"""The install and uninstall windows must always show a visible button once they finish, and close by themselves."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _display_ok():
    try:
        import tkinter as tk

        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_display_ok(), "needs a display")
class FinishStateTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        base = tempfile.mkdtemp()
        os.environ["APPDATA"], os.environ["LOCALAPPDATA"], os.environ["USERPROFILE"] = base + "/R", base + "/L", base
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                self.root.update()
            except Exception:
                return
            time.sleep(0.01)

    def assert_button_visible(self, win):
        self.pump(0.3)
        w = win.window
        bottom = win.primary.winfo_rooty() + win.primary.winfo_height()
        self.assertTrue(win.primary.winfo_viewable())
        self.assertGreaterEqual(win.primary.winfo_height(), win.primary.winfo_reqheight(), "the button is squeezed or cut off")
        self.assertGreaterEqual(w.winfo_height(), w.winfo_reqheight(), "the window is smaller than its content")
        self.assertLessEqual(bottom, w.winfo_rooty() + w.winfo_height() + 1, "the button is cut off at the bottom of the window")

    def test_installer_finish_button_is_visible_and_window_closes_itself(self):
        import gl_windows as W

        win = W.InstallerWindow(self.root)
        win._show_progress(True)
        self.assert_button_visible(win)
        win._finished(None, False)
        self.assert_button_visible(win)
        self.assertIn("Finish", win.primary.text if hasattr(win.primary, "text") else "Finish")
        self.assertTrue(win._count_job, "a countdown should be running")
        win.start_countdown(win.status, 1, lambda: win._finish_and_launch(False))
        self.pump(2.5)
        self.assertFalse(win.exists(), "the window closes after the countdown")

    def test_installer_stays_open_when_there_is_something_to_read(self):
        import gl_windows as W

        win = W.InstallerWindow(self.root)
        win.shortcut_problem = "Could not create the desktop shortcut."
        win._finished(None, False)
        self.assertFalse(getattr(win, "_count_job", None))
        self.assertIn("desktop shortcut", win.status.cget("text"))
        self.assert_button_visible(win)

    def test_uninstaller_close_button_is_visible_and_closes_itself(self):
        import gl_windows as W

        win = W.UninstallerWindow(self.root)
        win.progress.pack(fill="x")
        win.progress_label.pack(fill="x")
        win.fit()
        self.assert_button_visible(win)
        win._finished(None)
        self.assert_button_visible(win)
        win.start_countdown(win.status, 1, win.cancel)
        self.pump(2.5)
        self.assertFalse(win.exists())

    def test_shortcuts_go_to_the_folders_windows_uses(self):
        import gl_core as C

        self.assertTrue(str(C.desktop_dir()).endswith("Desktop"))
        self.assertIn("Programs", str(C.start_menu_dir()))
