"""GhostLock - transparent desktop soft-lock for Windows.

Created by MD ABID HASAN, published under MDexpLab. MIT licensed.

Modules
-------
ghostlock.py   entry point and application controller (this file)
gl_core.py     constants, hashing, configuration, Windows integration (no GUI)
gl_theme.py    palette, DPI scaling, window chrome and custom widgets
gl_windows.py  setup wizard, installer, uninstaller, settings, about, floating widget
gl_legacy.py   finds and removes older GhostLock installs (with permission)
gl_lock.py     the transparent lock screen
"""
import platform
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

# Re-exported so tests and other tools can keep using ``import ghostlock``.
from gl_core import (  # noqa: F401
    APP_AUTHOR,
    APP_REPO,
    APP_COPYRIGHT,
    APP_NAME,
    APP_PUBLISHER,
    APP_VERSION,
    AttemptLimiter,
    ConfigManager,
    acquire_single_instance,
    consume_show_request,
    reopen_hint,
    request_running_instance,
    hash_secret,
    is_forced_setup_invocation,
    is_stray_copy,
    is_setup_invocation,
    is_uninstall_invocation,
    launch_uninstaller,
    log_exception,
    password_strength,
    repair_startup_entry,
    verify_secret,
)
from gl_camera import default_camera
from gl_legacy import is_admin_cleanup_invocation, run_admin_cleanup, scan_legacy
from gl_lock import LockOverlay
from gl_security import SecurityMonitor, prune_photos
from gl_system import AutoLock, HotkeyListener, WakeDetector, Watchdog
from gl_update import check_latest
from gl_theme import enable_dpi_awareness, init_scaling, set_window_icon
from gl_windows import (
    AboutWindow,
    ActivityWindow,
    AuthGateWindow,
    AutoLockWarning,
    AwayReportWindow,
    ChangePasswordWindow,
    FloatingWidget,
    InstallerWindow,
    SettingsWindow,
    SetupWindow,
    UninstallerWindow,
)


def make_root():
    root = tk.Tk()
    root.withdraw()
    root.title(APP_NAME)
    init_scaling(root)
    set_window_icon(root)

    def report(exc_type, exc, tb):
        exc.__traceback__ = tb
        log_exception("Unhandled UI error", exc)

    root.report_callback_exception = report
    return root


AWAY_REPORT_MIN_FAILURES = 2  # one slip of your own finger is not worth a pop-up (a photo always is)
UPDATE_CHECK_DAYS = 7


class GhostLockApp:
    def __init__(self, force_setup=False):
        self.root = make_root()
        self.config = ConfigManager()
        self.guard = AttemptLimiter()
        self.widget = None
        self.lock = None
        self.monitor = None
        self.camera = None
        self.hotkey = None
        self.auto = None
        self.wake = None
        self.watchdog = None
        self.warning = None
        self._camera_warm = False
        self._ticks = 0
        self.windows = {}
        consume_show_request(clear_only=True)  # drop a request left over from before this start

        if not self.config.is_configured:
            self.start_setup()
        elif force_setup:
            # Re-running setup would overwrite the passwords, so prove identity first.
            self.windows["gate"] = AuthGateWindow(
                self.root,
                self.config,
                self.guard,
                on_success=self.start_setup,
                on_cancel=self.quit,
                title="Change GhostLock setup",
                message="Enter your current password to replace your setup.",
            )
        else:
            self.show_widget()
            self.start_services()

    # ------------------------------------------------------------------ background services
    def start_services(self):
        """Hotkey, auto-lock, wake detection, the lock-screen watchdog, photo housekeeping and update check."""
        if self.watchdog is not None or not self.config.is_configured:
            return
        self.camera = default_camera()
        self.hotkey = HotkeyListener()
        self.auto = AutoLock(lambda: self.config.setting("auto_lock_minutes"), self.is_locked, self.auto_lock)
        self.wake = WakeDetector()
        self.watchdog = Watchdog()
        self.watchdog.start()
        notice = self.apply_system_settings()
        if notice:
            log_exception("Shortcut not active", RuntimeError(notice))
        self.root.after(1000, self._tick)
        self.root.after(150, self._hotkey_tick)
        try:
            prune_photos(int(self.config.setting("intruder_keep_days")))
        except Exception as exc:
            log_exception("Photo cleanup failed", exc)
        self.root.after(8000, self.check_for_updates)

    def apply_system_settings(self):
        """Apply the shortcut setting. Returns a short notice for Settings when it could not be applied."""
        if self.hotkey is None:
            return ""
        self._warm_up_camera()
        if not self.config.setting("hotkey_enabled"):
            self.hotkey.stop()
            return ""
        combo = self.config.setting("hotkey")
        if self.hotkey.combo != combo and not self.hotkey.start(combo):
            return self.hotkey.error
        return ""

    def _warm_up_camera(self):
        """Loading OpenCV takes a second or two. Do it in the background so the first photo is not delayed."""
        if self.camera is None or self._camera_warm or not self.config.setting("intruder_camera"):
            return
        self._camera_warm = True
        threading.Thread(target=self.camera.available, daemon=True).start()

    def _hotkey_tick(self):
        """Checked several times a second so the shortcut feels instant."""
        try:
            if self.hotkey and self.hotkey.poll():
                self.lock_now()
            if consume_show_request():
                self.show_again()
        except Exception as exc:
            log_exception("Shortcut handling failed", exc)
        try:
            self.root.after(150, self._hotkey_tick)
        except tk.TclError:
            pass

    def is_locked(self):
        return self.lock is not None and self.lock.active

    def _tick(self):
        """Once a second: heartbeat for the watchdog, hotkey, wake-from-sleep, and (every 5 s) idle auto-lock."""
        try:
            self.watchdog.beat()
            if self.wake.poll() and self.config.setting("lock_on_wake") and not self.is_locked():
                self.lock_now()
            self._ticks += 1
            if self._ticks % 5 == 0:
                self.auto.check()
        except Exception as exc:
            log_exception("Background check failed", exc)
        try:
            self.root.after(1000, self._tick)
        except tk.TclError:
            pass  # the app is closing

    def auto_lock(self):
        """Idle timeout reached: warn for a few seconds (any input cancels), then lock."""
        if self.warning is not None or self.is_locked():
            return

        def clear():
            self.warning = None

        def lock():
            clear()
            self.lock_now()

        warning = AutoLockWarning(self.root, 8, on_lock=lock, on_cancel=clear)
        # it may already have finished (the person is back) before the constructor returned
        self.warning = None if warning.done else warning

    def check_for_updates(self):
        """Weekly, opt-in. The only network request GhostLock makes."""
        if not self.config.setting("check_updates") or not APP_REPO:
            return
        if time.time() - self.config.setting("last_update_check") < UPDATE_CHECK_DAYS * 86400:
            return
        result = {}

        def work():
            try:
                result["info"] = check_latest()
            except Exception as exc:
                log_exception("Update check failed", exc)
            result["done"] = True

        def poll(attempt=0):
            if "done" not in result and attempt < 120:
                self.root.after(500, lambda: poll(attempt + 1))
                return
            info = result.get("info")
            if not info:
                return
            if self.is_locked():  # never put a dialog behind the lock screen; ask again a bit later
                self.root.after(30000, poll)
                return
            try:
                self.config.update_settings(last_update_check=int(time.time()))
            except Exception:
                pass
            if info["update"] and messagebox.askyesno(APP_NAME, f"GhostLock {info['version']} is available.\n\nOpen the download page?"):
                webbrowser.open(info["url"])

        threading.Thread(target=work, daemon=True).start()
        poll()

    # ------------------------------------------------------------------ flow
    def start_setup(self):
        self.windows["setup"] = SetupWindow(self.root, self.config, self._setup_done, self.quit)

    def _setup_done(self, test_lock=False):
        self.show_widget()
        self.start_services()
        if test_lock:
            self.root.after(400, self.lock_now)

    def open_activity(self):
        self.show_window("activity", lambda: ActivityWindow(self.root))

    def open_change_password(self):
        self.show_window("password", lambda: ChangePasswordWindow(self.root, self.config, self.guard))

    def show_widget(self):
        if not self.config.is_configured:
            return
        if not self.widget:
            self.widget = FloatingWidget(self.root, self.config, self)

    def show_window(self, key, factory):
        existing = self.windows.get(key)
        if existing is not None and existing.exists():
            existing.focus()
            return existing
        window = factory()
        self.windows[key] = window
        return window

    def run_action(self, key):
        actions = {
            "lock": self.lock_now,
            "password": self.open_change_password,
            "settings": lambda: self.show_window(
                "settings",
                lambda: SettingsWindow(
                    self.root,
                    self.config,
                    self.guard,
                    self.open_change_password,
                    on_saved=self.apply_system_settings,
                    camera=self.camera,
                    on_open_activity=self.open_activity,
                ),
            ),
            "about": lambda: self.show_window("about", lambda: AboutWindow(self.root, self.config)),
            "uninstall": self.uninstall,
            "exit": self.confirm_exit,
        }
        action = actions.get(key)
        if action:
            action()

    def uninstall(self):
        """Ask for the password, then hand over to the uninstaller and exit."""
        self.show_window(
            "uninstall-gate",
            lambda: AuthGateWindow(
                self.root,
                self.config,
                self.guard,
                on_success=self._start_uninstaller,
                on_cancel=lambda: None,
                title="Uninstall GhostLock",
                message="Enter your password to continue with uninstalling.",
            ),
        )

    def _start_uninstaller(self):
        if launch_uninstaller():
            self.quit()
        else:
            messagebox.showerror(APP_NAME, "GhostLock could not start the uninstaller. Use Settings > Apps instead.")

    def _ghost_center(self):
        """Screen position of the floating ghost (where the shield animation starts), or None."""
        try:
            window = self.widget.window if self.widget else None
            if window and window.winfo_exists() and window.winfo_viewable():
                return (window.winfo_rootx() + window.winfo_width() // 2, window.winfo_rooty() + window.winfo_height() // 2)
        except Exception:
            pass
        return None

    def lock_now(self):
        if self.lock is not None and self.lock.active:
            self.lock.focus()
            return
        origin = self._ghost_center()
        if self.widget:
            self.widget.hide()
        try:  # the attempt log and photos are extras: if they cannot start, the lock itself still must
            self.monitor = SecurityMonitor(self.config, camera=self.camera)
        except Exception as exc:
            self.monitor = None
            log_exception("Attempt monitor could not start", exc)
        try:
            self.lock = LockOverlay(
                self.root,
                self.config,
                self.guard,
                on_unlock=self._after_unlock,
                on_recovered=self._open_recovery_password,
                on_failed=self.monitor.on_failed if self.monitor else None,
                shield_origin=origin,
            )
            if self.watchdog:
                self.watchdog.arm()
        except Exception as exc:
            self.lock = None
            if self.watchdog:
                self.watchdog.disarm()
            if self.widget:
                self.widget.show()
            log_path = log_exception("Lock Now failed", exc)
            detail = f"\n\nLog: {log_path}" if log_path else ""
            messagebox.showerror(APP_NAME, f"GhostLock could not start the lock screen.\n\n{exc}{detail}")

    def _after_unlock(self):
        self.lock = None
        if self.watchdog:
            self.watchdog.disarm()
        if self.widget:
            self.widget.show()
        self._show_away_report()

    def _show_away_report(self):
        """Tell the owner about wrong attempts made while they were away (after photos have finished saving)."""
        monitor, self.monitor = self.monitor, None
        if monitor is None or monitor.failures < 1 or not self.config.setting("show_away_report"):
            return

        def show():
            monitor.wait(0.5)
            entries = monitor.session_entries()
            if entries and (len(entries) >= AWAY_REPORT_MIN_FAILURES or any(e.get("photo") for e in entries)):
                self.show_window("away", lambda: AwayReportWindow(self.root, entries, self.open_activity))

        self.root.after(2000, show)

    def _open_recovery_password(self):
        self.show_window(
            "password", lambda: ChangePasswordWindow(self.root, self.config, self.guard, recovery=True)
        )

    def confirm_exit(self):
        """Exiting removes the ghost from the desktop, so say how to bring it back before doing it."""
        if self.is_locked():
            return
        if messagebox.askyesno(
            f"Exit {APP_NAME}",
            f"Exit {APP_NAME}?\n\nThe ghost disappears, and the lock shortcut and automatic lock stop working until you start it again.\n\n{reopen_hint()}",
            parent=self.root,
        ):
            self.quit()

    def show_again(self):
        """Another launch asked this copy to appear: bring the ghost back and open its menu."""
        if self.is_locked() or not self.config.is_configured:
            return
        self.show_widget()
        try:
            self.widget.show()
            self.root.after(150, self.widget.show_menu)
        except Exception as exc:
            log_exception("Could not show the ghost", exc)

    def quit(self):
        for service in (self.hotkey, self.watchdog):
            try:
                if service:
                    service.stop()
            except Exception:
                pass
        try:
            if self.widget:
                self.widget.destroy()
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def needs_cleanup_install():
    """True when this exe was started plainly (not from the install folder) while older GhostLock
    leftovers exist. Starting the new exe then offers to clean up and install, instead of running
    next to the old version."""
    if not is_stray_copy():
        return False
    try:
        return scan_legacy(fast=True).has_legacy  # quick check; the installer does the full scan
    except Exception as exc:
        log_exception("Legacy check failed", exc)
        return False


def run_installer():
    root = make_root()
    InstallerWindow(root)
    root.mainloop()


def run_selftest():
    """``GhostLock.exe --selftest``: checks that this build has what it needs, without opening any window.

    The report goes to ``%TEMP%\\ghostlock-selftest.txt`` because a windowed exe has no console.
    build.ps1 runs it after building so a camera-less exe is caught immediately.
    """
    import tempfile
    from pathlib import Path

    camera = default_camera()
    lines = [
        f"version={APP_VERSION}",
        f"frozen={bool(getattr(sys, 'frozen', False))}",
        f"tk={tk.TkVersion}",
        f"camera={'yes' if camera.available() else 'no'}",
    ]
    if not camera.available() and getattr(camera, "import_error", ""):
        lines.append(f"camera_error={camera.import_error}")
    try:
        from gl_system import parse_hotkey

        parse_hotkey("Ctrl+Alt+L")
        lines.append("hotkey=ok")
    except Exception as exc:
        lines.append(f"hotkey=error {exc}")
    try:
        (Path(tempfile.gettempdir()) / "ghostlock-selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return 1
    return 0


def main():
    if "--selftest" in sys.argv[1:]:
        sys.exit(run_selftest())
    if is_admin_cleanup_invocation():  # elevated helper started by the installer: no UI
        sys.exit(run_admin_cleanup())
    if platform.system() != "Windows":
        print("GhostLock is designed for Windows 10 and newer.")
    enable_dpi_awareness()

    if is_uninstall_invocation():
        root = make_root()
        UninstallerWindow(root)
        root.mainloop()
        return
    if is_setup_invocation() or needs_cleanup_install():
        run_installer()
        return

    if not acquire_single_instance():
        # Already running: ask that copy to show its ghost and menu instead of opening a second one.
        if not request_running_instance():
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(APP_NAME, "GhostLock is already running. Click the ghost button on your desktop.")
            root.destroy()
        return
    try:
        repair_startup_entry()  # keeps "Start with Windows" pointing at this copy, never an older one
    except Exception as exc:
        log_exception("Startup repair failed", exc)
    GhostLockApp(force_setup=is_forced_setup_invocation()).run()


if __name__ == "__main__":
    main()
