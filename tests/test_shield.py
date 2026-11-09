import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gl_shield as S  # noqa: E402


class TimingTests(unittest.TestCase):
    def test_cells_appear_in_order_then_fade_in_order(self):
        build = 2.0
        near, far = 0.0, 1.0
        self.assertGreater(S.cell_state(0.3, near, build)[0], S.cell_state(0.3, far, build)[0])
        self.assertEqual(S.cell_state(0.0, far, build)[0], 0)
        t_hold = build + 0.3
        for delay in (0.0, 0.5, 1.0):
            grow, flash, life = S.cell_state(t_hold, delay, build)
            self.assertEqual((grow, life), (1.0, 1.0))
            self.assertEqual(flash, 0.0)
        end = S.total_seconds(build)
        for delay in (0.0, 0.5, 1.0):
            self.assertAlmostEqual(S.cell_state(end, delay, build)[2], 0.0)
        self.assertLess(S.cell_state(build + S.HOLD_SECONDS + 0.4, 0.0, build)[2], S.cell_state(build + S.HOLD_SECONDS + 0.4, 1.0, build)[2])

    def test_flash_peaks_mid_appearance(self):
        build = 2.0
        samples = [S.cell_state(i * 0.05, 0.0, build)[1] for i in range(0, 40)]
        self.assertGreater(max(samples), 0.95)

    def test_grid_covers_the_screen_and_patterns_set_the_order(self):
        cells = S.build_cells(1920, 1080, 56, (1800, 1000), "ripple")
        self.assertGreater(len(cells), 200)
        xs, ys = [c["x"] for c in cells], [c["y"] for c in cells]
        self.assertLessEqual(min(xs), 0)
        self.assertGreaterEqual(max(xs), 1920)
        self.assertLessEqual(min(ys), 0)
        self.assertGreaterEqual(max(ys), 1080)
        nearest = min(cells, key=lambda c: c["dist"])
        self.assertLess(nearest["delay"], 0.05)
        self.assertEqual(max(c["delay"] for c in cells), 1.0)
        sweep = S.build_cells(1920, 1080, 56, (0, 0), "sweep")
        self.assertLess(min(sweep, key=lambda c: c["x"])["delay"], 0.05)
        scan = S.build_cells(1920, 1080, 56, (0, 0), "scan")
        self.assertLess(min(scan, key=lambda c: c["y"])["delay"], 0.05)
        rnd = S.build_cells(1920, 1080, 56, (0, 0), "random")
        self.assertGreater(len({round(c["delay"], 3) for c in rnd}), 50)

    def test_hexagon_has_six_corners(self):
        self.assertEqual(len(S.hex_points(0, 0, 10)), 12)


def _display_ok():
    try:
        import tkinter as tk

        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_display_ok(), "needs a display")
class ShieldWindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def test_runs_to_the_end_and_cleans_up(self):
        done = []
        shield = S.HexShield(self.root, (0, 0, 800, 450), (700, 400), "ripple", "teal", 0.5, cell=30, on_done=lambda: done.append(1))
        self.pump(1.0)
        self.assertGreater(len(shield.canvas.find_all()), 20, "hexagons were drawn")
        self.pump(S.total_seconds(0.5))
        self.assertEqual(done, [1])
        self.assertFalse(shield.window.winfo_exists())

    def test_close_early_and_all_options_work(self):
        for pattern in ("ripple", "sweep", "scan", "random"):
            for tone in ("teal", "ice", "amber"):
                shield = S.HexShield(self.root, (0, 0, 640, 360), (600, 330), pattern, tone, 1, cell=40, backdrop=True)
                self.pump(0.4)
                shield.close()
                shield.close()  # closing twice is harmless
                self.assertTrue(shield.finished)

    def test_lock_screen_runs_the_shield_and_unlock_removes_it(self):
        base = tempfile.mkdtemp()
        os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = base + "/R", base + "/L"
        from gl_core import AttemptLimiter, ConfigManager
        from gl_lock import LockOverlay

        cfg = ConfigManager()
        cfg.configure("Sunrise#2026", "Moonlight!77", "Q? robots", "Bolt")
        self.assertTrue(cfg.setting("shield_enabled"))
        cfg.update_settings(shield_seconds=1)
        unlocked = []
        lock = LockOverlay(self.root, cfg, AttemptLimiter(), on_unlock=lambda: unlocked.append(1), shield_origin=(100, 100))
        self.pump(0.5)
        self.assertIsNotNone(lock.shield)
        lock._unlock()
        self.assertEqual(unlocked, [1])
        self.assertIsNone(lock.shield)
        cfg.update_settings(shield_enabled=False)
        lock = LockOverlay(self.root, cfg, AttemptLimiter(), on_unlock=lambda: None)
        self.assertIsNone(lock.shield)
        lock._unlock()

    def test_settings_save_and_preview(self):
        base = tempfile.mkdtemp()
        os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = base + "/R", base + "/L"
        from gl_core import AttemptLimiter, ConfigManager
        import gl_windows as W

        cfg = ConfigManager()
        cfg.configure("Sunrise#2026", "Moonlight!77", "Q? robots", "Bolt")
        win = W.SettingsWindow(self.root, cfg, AttemptLimiter())
        self.pump(0.3)
        win.shield_on.toggle.set(False)
        win.shield_pattern.set("scan")
        win.shield_tone.set("amber")
        win.shield_seconds.set(5)
        win.save()
        self.assertEqual((cfg.setting("shield_enabled"), cfg.setting("shield_pattern"), cfg.setting("shield_tone"), cfg.setting("shield_seconds")), (False, "scan", "amber", 5))
        win = W.SettingsWindow(self.root, cfg, AttemptLimiter())
        self.pump(0.3)
        win._preview_shield()
        self.pump(0.4)
        self.assertIsNotNone(win._shield_preview)
        win._shield_preview.close()
        self.assertIsNone(win._shield_preview)
        cfg.update_settings(shield_pattern="bogus") if False else None


if __name__ == "__main__":
    unittest.main()
