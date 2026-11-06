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
import tkinter as tk
from tkinter import messagebox

# Re-exported so tests and other tools can keep using ``import ghostlock``.
from gl_core import (  # noqa: F401
    APP_AUTHOR,
    APP_COPYRIGHT,
    APP_NAME,
    APP_PUBLISHER,
    APP_VERSION,
    AttemptLimiter,
    ConfigManager,
    acquire_single_instance,
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
from gl_legacy import is_admin_cleanup_invocation, run_admin_cleanup, scan_legacy
from gl_lock import LockOverlay
from gl_theme import enable_dpi_awareness, init_scaling, set_window_icon
from gl_windows import (
    AboutWindow,
    AuthGateWindow,
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


class GhostLockApp:
    def __init__(self, force_setup=False):
        self.root = make_root()
        self.config = ConfigManager()
        self.guard = AttemptLimiter()
        self.widget = None
        self.lock = None
        self.windows = {}

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

    # ------------------------------------------------------------------ flow
    def start_setup(self):
        self.windows["setup"] = SetupWindow(self.root, self.config, self._setup_done, self.quit)

    def _setup_done(self, test_lock=False):
        self.show_widget()
        if test_lock:
            self.root.after(400, self.lock_now)

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
                lambda: SettingsWindow(self.root, self.config, self.guard, self.open_change_password),
            ),
            "about": lambda: self.show_window("about", lambda: AboutWindow(self.root)),
            "uninstall": self.uninstall,
            "exit": self.quit,
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

    def lock_now(self):
        if self.lock is not None and self.lock.active:
            self.lock.focus()
            return
        if self.widget:
            self.widget.hide()
        try:
            self.lock = LockOverlay(
                self.root,
                self.config,
                self.guard,
                on_unlock=self._after_unlock,
                on_recovered=self._open_recovery_password,
            )
        except Exception as exc:
            self.lock = None
            if self.widget:
                self.widget.show()
            log_path = log_exception("Lock Now failed", exc)
            detail = f"\n\nLog: {log_path}" if log_path else ""
            messagebox.showerror(APP_NAME, f"GhostLock could not start the lock screen.\n\n{exc}{detail}")

    def _after_unlock(self):
        self.lock = None
        if self.widget:
            self.widget.show()

    def _open_recovery_password(self):
        self.show_window(
            "password", lambda: ChangePasswordWindow(self.root, self.config, self.guard, recovery=True)
        )

    def quit(self):
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


def main():
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
