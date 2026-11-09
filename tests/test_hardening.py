"""Regression tests for issues found in the code review."""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_B = tempfile.mkdtemp()
os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = _B + "/R", _B + "/L"

import gl_core as C  # noqa: E402
import gl_theme as T  # noqa: E402


class GeometryTests(unittest.TestCase):
    def test_negative_positions_use_plus_minus(self):
        self.assertEqual(T.geometry_string(3840, 1080, -1920, 0), "3840x1080+-1920+0")
        self.assertEqual(T.geometry_string(100, 100, 5, -7), "100x100+5+-7")
        self.assertEqual(T.geometry_string(100, 100, 0, 0), "100x100+0+0")


class PasswordRuleTests(unittest.TestCase):
    def test_only_passwords_the_lock_screen_can_type_are_accepted(self):
        self.assertTrue(C.password_problem("abc"))
        self.assertEqual(C.password_problem("Sunrise#2026"), "")
        self.assertTrue(C.password_problem("x" * 129))
        self.assertEqual(C.password_problem("x" * 128), "")
        self.assertTrue(C.password_problem("tab\there"))
        self.assertTrue(C.password_problem("line\nbreak!"))


class ConfigRobustnessTests(unittest.TestCase):
    def test_flags_and_numbers_from_a_hand_edited_config(self):
        flag = C.SETTING_RULES["lock_on_wake"][1]
        self.assertIs(flag("false"), False)
        self.assertIs(flag("true"), True)
        self.assertIs(flag(1), True)
        with self.assertRaises(ValueError):
            flag("maybe")
        base = tempfile.mkdtemp()
        os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = base + "/R", base + "/L"
        cfg = C.ConfigManager()
        cfg.configure("Sunrise#2026", "Moonlight!77", "Q? robots", "Bolt")
        cfg.data.update(auto_lock_minutes=float("inf"), lock_on_wake="false", shield_seconds=float("nan"))
        cfg.save()
        again = C.ConfigManager()
        self.assertTrue(again.is_configured, "a bad number must not wipe the config")
        self.assertEqual((again.setting("auto_lock_minutes"), again.setting("lock_on_wake"), again.setting("shield_seconds")), (0, False, 2))

    def test_new_hashes_are_strong_and_old_ones_still_verify(self):
        record = C.hash_secret("Sunrise#2026")
        self.assertGreaterEqual(int(record["iterations"]), 600_000)
        old = C.hash_secret("Sunrise#2026", iterations=260_000)
        self.assertTrue(C.verify_secret("Sunrise#2026", old))
        self.assertFalse(C.verify_secret("wrong-guess", old))


class WindowsNamingTests(unittest.TestCase):
    def test_powershell_quoting_covers_typographic_quotes(self):
        self.assertEqual(C.ps_quote("O\u2019Neil"), "'O\u2019\u2019Neil'")
        self.assertEqual(C.ps_quote("it's"), "'it''s'")

    def test_renamed_setup_files_still_count_as_the_installer(self):
        old_exe, old_frozen, old_argv = sys.executable, getattr(sys, "frozen", None), sys.argv
        sys.frozen = True
        sys.argv = ["x"]
        try:
            for name, expected in (("Setup.exe", True), ("Setup (1).exe", True), ("GhostLock-Setup-2.2.0.exe", True), ("GhostLock_Setup.exe", True),
                                   ("GhostLock.exe", False), ("ghostlock (1).exe", False)):
                sys.executable = "C:\\Users\\me\\Downloads\\" + name
                self.assertEqual(C.is_setup_invocation(), expected, name)
        finally:
            sys.executable, sys.argv = old_exe, old_argv
            if old_frozen is None:
                del sys.frozen
            else:
                sys.frozen = old_frozen


class SecurityLogTests(unittest.TestCase):
    def test_one_shared_log_per_process_and_bounded_waiting(self):
        import gl_security as S

        base = tempfile.mkdtemp()
        os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = base + "/R", base + "/L"
        self.assertIs(S.AttemptLog.shared(), S.AttemptLog.shared())

        class Cfg:
            def setting(self, name):
                return {"intruder_camera": True, "intruder_after": 1, "intruder_keep_days": 30}[name]

        class Slow:
            def available(self):
                return True

            def capture(self, path):
                time.sleep(3)
                return False

        monitor = S.SecurityMonitor(Cfg(), Slow(), photos_dir=base + "/p")
        for _ in range(4):
            monitor.on_failed()
        started = time.monotonic()
        monitor.wait(0.5)
        self.assertLess(time.monotonic() - started, 1.2, "waiting for photos must be bounded in total")

    def test_camera_import_is_not_reported_missing_while_loading(self):
        import gl_camera

        cam = gl_camera.OpenCvCamera()
        results = []
        threads = [threading.Thread(target=lambda: results.append(cam.available())) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(set(results)), 1, "all callers must agree")


def _display_ok():
    try:
        import tkinter as tk

        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_display_ok(), "needs a display")
class LockOverlayFailureTests(unittest.TestCase):
    def test_a_failed_build_leaves_no_cover_behind(self):
        import tkinter as tk

        import gl_lock

        base = tempfile.mkdtemp()
        os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = base + "/R", base + "/L"
        root = tk.Tk()
        root.withdraw()
        try:
            cfg = C.ConfigManager()
            cfg.configure("Sunrise#2026", "Moonlight!77", "Q? robots", "Bolt")
            original = gl_lock.LockOverlay._build

            def broken(self):
                self.overlay = tk.Toplevel(self.root)
                self.panel = tk.Toplevel(self.root)
                raise tk.TclError("driver problem")

            gl_lock.LockOverlay._build = broken
            before = len(root.winfo_children())
            with self.assertRaises(tk.TclError):
                gl_lock.LockOverlay(root, cfg, C.AttemptLimiter(), on_unlock=lambda: None)
            self.assertEqual(len(root.winfo_children()), before, "overlay windows must be destroyed")
            gl_lock.LockOverlay._build = original
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
