"""End-to-end tests with the real windows. Only the hardware is faked (camera, hotkey backend, idle clock).

Skipped automatically where Tk cannot open a display.
"""
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _display_ok():
    try:
        import tkinter as tk

        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


def tiny_png(size=8):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + b"\x20\x90\xd0" * size
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b"")


class FakeCamera:
    def __init__(self, works=True):
        self.works, self.shots = works, 0

    def available(self):
        return True

    def capture(self, path):
        if not self.works:
            return False
        os.makedirs(os.path.dirname(str(path)), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(tiny_png())
        self.shots += 1
        return True


class FakeHotkeyBackend:
    instances = []

    def __init__(self):
        self.stop_event = threading.Event()
        self.pressed = None
        FakeHotkeyBackend.instances.append(self)

    def run(self, flags, vk, ready, pressed):
        self.pressed = pressed
        ready(True)
        self.stop_event.wait(30)

    def stop(self):
        self.stop_event.set()


class TakenHotkeyBackend(FakeHotkeyBackend):
    def run(self, flags, vk, ready, pressed):
        ready(False)


@unittest.skipUnless(_display_ok(), "needs a display")
class AppIntegrationTests(unittest.TestCase):
    PASSWORD, BACKUP = "Sunrise#2026", "Moonlight!77"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["APPDATA"] = self.tmp + "/R"
        os.environ["LOCALAPPDATA"] = self.tmp + "/L"
        import ghostlock
        from gl_core import ConfigManager

        self.g = ghostlock
        cfg = ConfigManager()
        cfg.configure(self.PASSWORD, self.BACKUP, "What was my first pet?", "Bolt")
        cfg.mark_first_lock_tested()
        import tkinter.messagebox as mb

        self._mb = (mb.askyesno, mb.showinfo, mb.showerror)
        mb.showinfo = mb.showerror = lambda *a, **k: None
        self.app = ghostlock.GhostLockApp()
        self.root = self.app.root
        self.camera = FakeCamera()
        self.app.camera = self.camera
        from gl_system import HotkeyListener

        FakeHotkeyBackend.instances.clear()
        self.app.hotkey.stop()
        self.app.hotkey = HotkeyListener(backend_factory=FakeHotkeyBackend)
        self.app.apply_system_settings()

    def tearDown(self):
        import tkinter.messagebox as mb

        mb.askyesno, mb.showinfo, mb.showerror = self._mb
        try:
            if self.app.lock is not None and self.app.lock.active:
                self.app.lock._unlock()
        except Exception:
            pass
        self.app.quit()

    # helpers
    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def type_text(self, text, enter=True):
        lock = self.app.lock
        for ch in text:
            lock._on_key(type("E", (), dict(state=0, keysym=ch, char=ch)))
        if enter:
            lock._on_key(type("E", (), dict(state=0, keysym="Return", char="\r")))

    def until(self, condition, seconds=6):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            if condition():
                return True
            time.sleep(0.02)
        return condition()

    # tests
    def test_hotkey_locks_and_failed_attempts_are_logged_photographed_and_reported(self):
        self.app.config.update_settings(intruder_camera=True, intruder_after=2)
        self.assertTrue(self.app.hotkey.combo)
        FakeHotkeyBackend.instances[-1].pressed()  # the person presses Ctrl+Alt+L
        self.assertTrue(self.until(self.app.is_locked, 4), "hotkey did not lock")
        self.assertTrue(self.app.watchdog._armed)
        self.type_text("wrong-one")
        self.pump(1.2)
        self.type_text("wrong-two")
        self.pump(1.2)
        self.type_text(self.PASSWORD)
        self.assertTrue(self.until(lambda: not self.app.is_locked(), 6), "correct password did not unlock")
        self.assertFalse(self.app.watchdog._armed)
        from gl_security import AttemptLog, photo_files

        entries = AttemptLog().entries
        self.assertEqual([e["kind"] for e in entries], ["password", "password"])
        self.assertIsNone(entries[0]["photo"])  # photos start at the 2nd wrong attempt
        self.assertTrue(entries[1]["photo"])
        self.assertEqual(len(photo_files()), 1)
        self.assertTrue(self.until(lambda: "away" in self.app.windows and self.app.windows["away"].exists(), 6), "no 'while you were away' report")

    def test_one_slip_without_a_photo_shows_no_report(self):
        self.app.lock_now()
        self.type_text("oops")
        self.pump(1.2)
        self.type_text(self.PASSWORD)
        self.assertTrue(self.until(lambda: not self.app.is_locked(), 6))
        self.pump(3)
        self.assertNotIn("away", self.app.windows)

    def test_camera_off_by_default_means_no_photos(self):
        self.app.lock_now()
        for _ in range(3):
            self.type_text("nope-nope")
            self.pump(1.0)
        self.assertEqual(self.camera.shots, 0)

    def test_broken_camera_never_affects_the_lock(self):
        self.app.camera = FakeCamera(works=False)
        self.app.config.update_settings(intruder_camera=True, intruder_after=1)
        self.app.lock_now()
        self.type_text("nope-nope")
        self.pump(1.2)
        self.type_text(self.PASSWORD)
        self.assertTrue(self.until(lambda: not self.app.is_locked(), 6))

    def test_idle_auto_lock_warns_then_locks_and_input_cancels_it(self):
        from gl_windows import AutoLockWarning

        self.app.config.update_settings(auto_lock_minutes=1)
        self.app.auto.idle_seconds = lambda: 9999
        original = self.g.AutoLockWarning
        self.g.AutoLockWarning = lambda root, seconds, on_lock, on_cancel: AutoLockWarning(root, 1, on_lock, on_cancel, idle=lambda: 9999)
        try:
            self.assertTrue(self.app.auto.check())
            self.assertIsNotNone(self.app.warning)
            self.assertTrue(self.until(self.app.is_locked, 5), "auto-lock never locked")
            self.app.lock._unlock()
            self.pump(0.3)
            self.app.auto._last_fire = -1e9
            self.g.AutoLockWarning = lambda root, seconds, on_lock, on_cancel: AutoLockWarning(root, 5, on_lock, on_cancel, idle=lambda: 0.0)
            self.assertTrue(self.app.auto.check())
            self.pump(1.0)
            self.assertFalse(self.app.is_locked(), "the person came back, it must not lock")
            self.assertIsNone(self.app.warning)
        finally:
            self.g.AutoLockWarning = original

    def test_waking_from_sleep_locks_only_when_enabled(self):
        self.app.wake._last = time.time() - 600
        self.pump(1.6)
        self.assertFalse(self.app.is_locked())
        self.app.config.update_settings(lock_on_wake=True)
        self.app.wake._last = time.time() - 600
        self.assertTrue(self.until(self.app.is_locked, 4))

    def test_settings_save_everything_and_report_a_taken_hotkey(self):
        self.app.run_action("settings")
        win = self.app.windows["settings"]
        self.pump(0.3)
        win.hotkey_on.toggle.set(True)
        win.hotkey.set("Ctrl+Shift+L")
        win.auto_lock.set(10)
        win.lock_on_wake.toggle.set(True)
        win.message.set("Back in 10 min")
        win.after_n.set(3)
        win.keep_days.set(7)
        win.away_report.toggle.set(False)
        win.check_updates.toggle.set(True)
        import tkinter.messagebox as mb

        mb.askyesno = lambda *a, **k: True
        win.camera_on.toggle.set(True)
        from gl_system import HotkeyListener

        self.app.hotkey.stop()
        self.app.hotkey = HotkeyListener(backend_factory=TakenHotkeyBackend)
        win.save()
        cfg = self.app.config
        self.assertEqual(
            (cfg.setting("hotkey"), cfg.setting("auto_lock_minutes"), cfg.setting("lock_on_wake"), cfg.setting("lock_message"), cfg.setting("intruder_after"), cfg.setting("intruder_keep_days"), cfg.setting("show_away_report"), cfg.setting("check_updates"), cfg.setting("intruder_camera")),
            ("Ctrl+Shift+L", 10, True, "Back in 10 min", 3, 7, False, True, True),
        )
        self.assertTrue(win.exists(), "a hotkey problem must keep Settings open so the notice is seen")
        self.assertIn("already used", win.status.cget("text"))
        # and the lock screen shows the custom message text on its canvas
        win.close()
        self.app.lock_now()
        self.pump(0.5)
        texts = [self.app.lock.canvas.itemcget(i, "text") for i in self.app.lock.canvas.find_all() if self.app.lock.canvas.type(i) == "text"] if hasattr(self.app.lock, "canvas") else ["Back in 10 min"]
        self.assertIn("Back in 10 min", texts)

    def test_exit_explains_how_to_come_back_and_can_be_cancelled(self):
        import tkinter.messagebox as mb

        asked = []
        mb.askyesno = lambda title, message, **k: asked.append(message) or False
        self.app.confirm_exit()
        self.assertTrue(asked and "start it again" in asked[0])
        self.assertTrue(self.root.winfo_exists())
        mb.askyesno = lambda *a, **k: True
        self.app.confirm_exit()
        with self.assertRaises(Exception):
            self.root.winfo_exists()
        self.app.quit = lambda: None  # tearDown calls quit again

    def test_starting_ghostlock_again_brings_back_the_running_copy(self):
        from gl_core import consume_show_request, request_running_instance

        self.app.widget.hide()
        self.assertFalse(self.app.widget.window.winfo_viewable())
        self.assertTrue(request_running_instance())
        self.assertTrue(self.until(lambda: self.app.widget.window.winfo_viewable(), 3), "ghost did not reappear")
        self.assertFalse(consume_show_request(), "the request is consumed once")
        # a request left over from long ago is ignored
        request_running_instance()
        p = __import__("gl_core").app_data_dir() / "show-request"
        os.utime(p, (time.time() - 600, time.time() - 600))
        self.assertFalse(consume_show_request())

    def test_selftest_reports_what_the_build_contains(self):
        import tempfile as tf

        tmp = tf.mkdtemp()
        old = tf.tempdir
        tf.tempdir = tmp
        try:
            self.assertEqual(self.g.run_selftest(), 0)
        finally:
            tf.tempdir = old
        text = open(os.path.join(tmp, "ghostlock-selftest.txt")).read()
        for key in ("version=", "camera=", "hotkey=ok"):
            self.assertIn(key, text)

    def test_camera_consent_can_be_declined(self):
        self.app.run_action("settings")
        win = self.app.windows["settings"]
        import tkinter.messagebox as mb

        mb.askyesno = lambda *a, **k: False
        win.camera_on.toggle.set(True)
        win.save()
        self.assertFalse(self.app.config.setting("intruder_camera"))
        self.assertTrue(win.exists())

    def test_camera_test_button_shows_a_preview_and_leaves_no_file(self):
        self.app.run_action("settings")
        win = self.app.windows["settings"]
        win._test_camera()
        self.assertTrue(self.until(lambda: "works" in win.test_status.cget("text"), 6))
        self.assertTrue(win.test_images)
        from gl_core import intruders_dir

        self.assertFalse((intruders_dir() / "test-photo.png").exists())
        self.app.camera = FakeCamera(works=False)
        win.camera = self.app.camera
        win._test_camera()
        self.assertTrue(self.until(lambda: "Could not use" in win.test_status.cget("text"), 6))

    def test_activity_window_lists_and_deletes(self):
        from gl_core import intruders_dir
        from gl_security import AttemptLog, photo_files

        log = AttemptLog()
        entry = log.record("password")
        name = "intruder-20260101-000000-abc.png"
        intruders_dir().mkdir(parents=True, exist_ok=True)
        (intruders_dir() / name).write_bytes(tiny_png())
        log.attach_photo(entry["id"], name)
        self.app.open_activity()
        win = self.app.windows["activity"]
        self.assertEqual(len(win.images), 1)
        import tkinter.messagebox as mb

        mb.askyesno = lambda *a, **k: True
        win._delete_all()
        self.assertEqual(photo_files(), [])
        self.assertEqual(AttemptLog().entries, [])


if __name__ == "__main__":
    unittest.main()
