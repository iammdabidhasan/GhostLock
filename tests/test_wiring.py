"""Logic tests for the pieces the app controller relies on (no display needed)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_APPDATA = tempfile.mkdtemp()
os.environ["APPDATA"] = _APPDATA + "/R"
os.environ["LOCALAPPDATA"] = _APPDATA + "/L"

import gl_core  # noqa: E402
import gl_system  # noqa: E402


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class WakeDetectorTests(unittest.TestCase):
    def test_only_a_long_gap_counts_as_waking_up(self):
        clock = Clock()
        wake = gl_system.WakeDetector(gap=20, clock=clock)
        for _ in range(5):
            clock.now += 1
            self.assertFalse(wake.poll())
        clock.now += 3600  # the PC slept for an hour
        self.assertTrue(wake.poll())
        clock.now += 1
        self.assertFalse(wake.poll())  # reported once


class WatchdogSleepTests(unittest.TestCase):
    def make(self, fired):
        self.clock = Clock(0.0)
        return gl_system.Watchdog(limit=90, on_stall=lambda: fired.append(1), clock=self.clock, interval=1.0)

    def test_a_real_stall_is_still_caught(self):
        fired = []
        dog = self.make(fired)
        dog.arm()
        for _ in range(100):  # the UI stops beating for 100 one-second rounds
            self.clock.now += 1
            dog.step()
        self.assertEqual(fired, [1])

    def test_sleeping_while_locked_is_not_a_stall(self):
        fired = []
        dog = self.make(fired)
        dog.arm()
        self.clock.now += 5 * 3600  # sleep: the whole process was suspended, so no rounds ran
        dog.step()
        self.assertEqual(fired, [])
        dog.beat()
        for _ in range(10):
            self.clock.now += 1
            dog.beat()
            dog.step()
        self.assertEqual(fired, [])


class BusyAndLogTests(unittest.TestCase):
    def test_auto_lock_never_fires_during_fullscreen_video(self):
        locks = []
        auto = gl_system.AutoLock(lambda: 5, lambda: False, lambda: locks.append(1), idle_seconds=lambda: 9999, clock=Clock(), busy=lambda: True)
        self.assertFalse(auto.check())
        auto.busy = lambda: False
        self.assertTrue(auto.check())
        self.assertEqual(locks, [1])

    def test_error_log_is_rotated_instead_of_growing_forever(self):
        base = tempfile.mkdtemp()
        os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = base + "/R", base + "/L"
        path = gl_core.app_data_dir() / "ghostlock.log"
        path.parent.mkdir(parents=True)
        path.write_text("x" * (gl_core.MAX_LOG_BYTES + 10))
        gl_core.log_exception("test", RuntimeError("boom"))
        self.assertLess(path.stat().st_size, 5000)
        self.assertTrue(path.with_suffix(".log.1").exists())


class SettingsRuleTests(unittest.TestCase):
    def test_lock_on_wake_defaults_off_and_is_validated(self):
        base = tempfile.mkdtemp()
        os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = base + "/R", base + "/L"
        self.assertEqual(gl_core.SETTING_RULES["lock_on_wake"][0], False)
        cfg = gl_core.ConfigManager()
        cfg.configure("Sunrise#2026", "Moonlight!77", "What was my first pet?", "Bolt")
        self.assertFalse(cfg.setting("lock_on_wake"))
        cfg.update_settings(lock_on_wake=1)
        self.assertIs(cfg.setting("lock_on_wake"), True)


if __name__ == "__main__":
    unittest.main()
