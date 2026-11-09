"""Tests for settings, hotkey, auto-lock, watchdog, intruder photos, attempt log and update check."""
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gl_camera  # noqa: E402
import gl_core  # noqa: E402
import gl_security as sec  # noqa: E402
import gl_system as sysmod  # noqa: E402
import gl_update  # noqa: E402


def make_config(**overrides):
    """A ConfigManager backed by a temp folder, already set up with passwords."""
    tmp = Path(tempfile.mkdtemp())
    with mock.patch.object(gl_core, "app_data_dir", lambda: tmp):
        config = gl_core.ConfigManager()
        config.path = tmp / "config.json"
        config.configure("Sunrise#2026", "Moonlight!77", "Q of robots?", "Bolt")
        if overrides:
            config.update_settings(**overrides)
    return config, tmp


class SettingsTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        config, _ = make_config()
        self.assertTrue(config.setting("hotkey_enabled"))
        self.assertEqual(config.setting("hotkey"), "Ctrl+Alt+L")
        self.assertEqual(config.setting("auto_lock_minutes"), 0)
        self.assertFalse(config.setting("intruder_camera"))
        self.assertFalse(config.setting("check_updates"))
        config.update_settings(auto_lock_minutes=9999, hotkey="Alt+F4", lock_message="  Back   in 10   min  " + "x" * 80, intruder_after=7)
        self.assertEqual(config.setting("auto_lock_minutes"), gl_core.MAX_AUTO_LOCK_MINUTES)
        self.assertEqual(config.setting("hotkey"), "Ctrl+Alt+L")  # not an allowed preset
        self.assertTrue(config.setting("lock_message").startswith("Back in 10 min x"))
        self.assertLessEqual(len(config.setting("lock_message")), gl_core.MAX_LOCK_MESSAGE_LENGTH)
        self.assertEqual(config.setting("intruder_after"), gl_core.DEFAULT_INTRUDER_AFTER)

    def test_unknown_setting_is_rejected_and_nothing_changes(self):
        config, _ = make_config()
        before = json.dumps(config.data, sort_keys=True)
        with self.assertRaises(ValueError):
            config.update_settings(auto_lock_minutes=5, not_a_setting=1)
        self.assertEqual(json.dumps(config.data, sort_keys=True), before)

    def test_old_config_files_get_new_defaults(self):
        config, tmp = make_config()
        old = {k: v for k, v in config.data.items() if k in ("version", "password", "emergency_password", "security_question", "security_answer", "lock_opacity")}
        (tmp / "config.json").write_text(json.dumps(old))
        with mock.patch.object(gl_core, "app_data_dir", lambda: tmp):
            reloaded = gl_core.ConfigManager()
        self.assertTrue(reloaded.is_configured)
        self.assertTrue(reloaded.setting("show_away_report"))
        self.assertEqual(reloaded.setting("intruder_keep_days"), gl_core.DEFAULT_KEEP_DAYS)

    def test_preferences_survive_setup_being_run_again(self):
        config, _ = make_config(auto_lock_minutes=10, lock_message="Back soon", intruder_camera=True)
        config.configure("NewPassword#1", "Another!Pass2", "Another question?", "Answer")
        self.assertEqual(config.setting("auto_lock_minutes"), 10)
        self.assertEqual(config.setting("lock_message"), "Back soon")
        self.assertTrue(config.setting("intruder_camera"))


class HotkeyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(sysmod.parse_hotkey("Ctrl+Alt+L"), (0x2 | 0x1, ord("L")))
        self.assertEqual(sysmod.parse_hotkey("win+shift+l"), (0x8 | 0x4, ord("L")))
        for bad in ("L", "Ctrl", "Ctrl+Alt+Esc", "Foo+L", "", "Ctrl++"):
            with self.assertRaises(ValueError, msg=bad):
                sysmod.parse_hotkey(bad)

    def test_every_preset_parses(self):
        for preset in gl_core.HOTKEY_PRESETS:
            sysmod.parse_hotkey(preset)

    class FakeBackend:
        taken = False
        instances = []

        def __init__(self):
            self.stopped = False
            self.fire = None
            self.done = __import__("threading").Event()
            type(self).instances.append(self)

        def run(self, flags, vk, ready, pressed):
            ok = not type(self).taken
            ready(ok)
            if ok:
                self.fire = pressed
                self.done.wait(3)

        def stop(self):
            self.stopped = True
            self.done.set()

    def test_listener_reports_presses_once_and_stops(self):
        self.FakeBackend.taken = False
        listener = sysmod.HotkeyListener(self.FakeBackend)
        self.assertTrue(listener.start("Ctrl+Alt+L"))
        backend = self.FakeBackend.instances[-1]
        self.assertFalse(listener.poll())
        backend.fire(); backend.fire()
        self.assertTrue(listener.poll())
        self.assertFalse(listener.poll())  # presses are consumed
        listener.stop()
        self.assertTrue(backend.stopped)

    def test_listener_reports_a_taken_hotkey_and_bad_input(self):
        self.FakeBackend.taken = True
        listener = sysmod.HotkeyListener(self.FakeBackend)
        self.assertFalse(listener.start("Ctrl+Alt+L"))
        self.assertIn("already used", listener.error)
        self.FakeBackend.taken = False
        self.assertFalse(listener.start("nonsense"))
        self.assertTrue(listener.error)


class AutoLockTests(unittest.TestCase):
    def make(self, minutes, idle, locked=False):
        state = {"minutes": minutes, "idle": idle, "locked": locked, "now": 1000.0, "locks": 0}
        auto = sysmod.AutoLock(
            lambda: state["minutes"], lambda: state["locked"], lambda: state.__setitem__("locks", state["locks"] + 1),
            idle_seconds=lambda: state["idle"], clock=lambda: state["now"],
        )
        return auto, state

    def test_off_by_default(self):
        auto, state = self.make(0, 99999)
        self.assertFalse(auto.check())
        self.assertEqual(state["locks"], 0)

    def test_locks_only_after_the_idle_time(self):
        auto, state = self.make(5, 299)
        self.assertFalse(auto.check())
        state["idle"] = 300
        self.assertTrue(auto.check())
        self.assertEqual(state["locks"], 1)

    def test_does_not_lock_while_already_locked_or_repeat_quickly(self):
        auto, state = self.make(1, 120, locked=True)
        self.assertFalse(auto.check())
        state["locked"] = False
        self.assertTrue(auto.check())
        self.assertFalse(auto.check())  # cooldown
        state["now"] += sysmod.AutoLock.COOLDOWN + 1
        self.assertTrue(auto.check())

    def test_idle_clock_wraps_safely(self):
        self.assertGreaterEqual(sysmod.seconds_since_input(), 0.0)  # non-Windows returns 0, never negative


class WatchdogTests(unittest.TestCase):
    def test_fires_only_when_armed_and_stalled(self):
        now, fired = [0.0], []
        dog = sysmod.Watchdog(limit=10, on_stall=lambda: fired.append(1), clock=lambda: now[0])
        now[0] = 100
        self.assertFalse(dog.check())  # not armed: an idle desktop is not a hang
        dog.arm()
        now[0] = 109
        self.assertFalse(dog.check())
        dog.beat()
        now[0] = 118
        self.assertFalse(dog.check())  # a beat resets the timer
        now[0] = 130
        self.assertTrue(dog.check())
        self.assertEqual(fired, [1])
        self.assertFalse(dog.check())  # fires once

    def test_disarm(self):
        now, fired = [0.0], []
        dog = sysmod.Watchdog(limit=5, on_stall=lambda: fired.append(1), clock=lambda: now[0])
        dog.arm(); dog.disarm(); now[0] = 999
        self.assertFalse(dog.check())
        self.assertEqual(fired, [])


class FakeCamera:
    def __init__(self, ok=True, available=True):
        self.ok, self._available, self.calls = ok, available, 0

    def available(self):
        return self._available

    def capture(self, path):
        self.calls += 1
        if self.ok:
            Path(path).write_bytes(b"png")
        return self.ok


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log = sec.AttemptLog(self.tmp / "attempts.json")
        self.photos = self.tmp / "photos"

    def monitor(self, config, camera):
        return sec.SecurityMonitor(config, camera, self.log, self.photos)

    def test_camera_is_off_unless_the_owner_enabled_it(self):
        config, _ = make_config()
        camera = FakeCamera()
        mon = self.monitor(config, camera)
        for _ in range(4):
            mon.on_failed()
        mon.wait()
        self.assertEqual(camera.calls, 0)
        self.assertEqual(len(mon.session_entries()), 4)  # attempts are still logged

    def test_photo_starts_at_the_chosen_attempt(self):
        config, _ = make_config(intruder_camera=True, intruder_after=2)
        camera = FakeCamera()
        mon = self.monitor(config, camera)
        first = mon.on_failed()
        mon.wait()
        self.assertEqual(camera.calls, 0)
        second = mon.on_failed()
        mon.wait()
        self.assertEqual(camera.calls, 1)
        entries = {e["id"]: e for e in mon.session_entries()}
        self.assertIsNone(entries[first["id"]]["photo"])
        self.assertTrue((self.photos / entries[second["id"]]["photo"]).exists())

    def test_photos_per_lock_are_capped(self):
        config, _ = make_config(intruder_camera=True, intruder_after=1)
        camera = FakeCamera()
        mon = self.monitor(config, camera)
        for _ in range(12):
            mon.on_failed(); mon.wait()
        self.assertEqual(camera.calls, sec.MAX_PHOTOS_PER_SESSION)

    def test_missing_or_failing_camera_never_breaks_the_lock(self):
        config, _ = make_config(intruder_camera=True, intruder_after=1)
        for camera in (None, FakeCamera(available=False), FakeCamera(ok=False)):
            mon = self.monitor(config, camera)
            entry = mon.on_failed(); mon.wait()
            self.assertIsNone(self.log.since(0)[-1]["photo"])
            self.assertEqual(entry["kind"], "password")

    def test_a_new_session_starts_counting_again(self):
        config, _ = make_config(intruder_camera=True, intruder_after=2)
        camera = FakeCamera()
        mon = self.monitor(config, camera)
        mon.on_failed(); mon.begin_session(); mon.on_failed(); mon.wait()
        self.assertEqual(camera.calls, 0)  # 1 failure in each session: below the threshold
        self.assertEqual(len(mon.session_entries()), 1)


class LogAndRetentionTests(unittest.TestCase):
    def test_log_persists_survives_corruption_and_clears(self):
        tmp = Path(tempfile.mkdtemp()); path = tmp / "attempts.json"
        log = sec.AttemptLog(path); entry = log.record("recovery")
        log.attach_photo(entry["id"], "intruder-x.png")
        again = sec.AttemptLog(path)
        self.assertEqual(again.entries[0]["photo"], "intruder-x.png")
        path.write_text("{ not json")
        self.assertEqual(sec.AttemptLog(path).entries, [])
        log.clear()
        self.assertEqual(sec.AttemptLog(path).entries, [])

    def test_log_is_capped(self):
        log = sec.AttemptLog(Path(tempfile.mkdtemp()) / "a.json")
        for _ in range(sec.MAX_LOG_ENTRIES + 25):
            log.record("password")
        self.assertEqual(len(log.entries), sec.MAX_LOG_ENTRIES)

    def test_prune_by_age_and_count(self):
        folder = Path(tempfile.mkdtemp()); now = time.time()
        for i in range(8):
            path = folder / f"intruder-{i}.png"; path.write_bytes(b"x")
            age_days = 40 if i < 2 else 1
            os.utime(path, (now - age_days * 86400 - i, now - age_days * 86400 - i))
        (folder / "notes.txt").write_text("keep me")
        self.assertEqual(sec.prune_photos(30, folder, max_files=100, now=now), 2)
        self.assertEqual(sec.prune_photos(30, folder, max_files=4, now=now), 2)
        self.assertEqual(len(sec.photo_files(folder)), 4)
        self.assertTrue((folder / "notes.txt").exists())  # only our own photos are ever touched
        self.assertEqual(sec.delete_all_photos(folder), 4)
        self.assertTrue((folder / "notes.txt").exists())


class CameraBackendTests(unittest.TestCase):
    def fake_cv2(self, opens=True, frames=True):
        class Device:
            def isOpened(self):
                return opens

            def read(self):
                return (True, FakeFrame()) if frames else (False, None)

            def release(self):
                released.append(1)

        class FakeFrame:
            shape = (480, 1280, 3)

        class Encoded:
            def tofile(self, name):
                Path(name).write_bytes(b"\x89PNG")

        released = []
        module = types.SimpleNamespace(
            CAP_DSHOW=700, CAP_MSMF=1400, CAP_ANY=0,
            VideoCapture=lambda index, backend: Device(),
            resize=lambda frame, size: frame,
            imencode=lambda ext, frame: (True, Encoded()),
        )
        return module, released

    def run_capture(self, module):
        cam = gl_camera.OpenCvCamera()
        cam._tried, cam._cv2_module = True, module
        with mock.patch.object(gl_camera.time, "sleep", lambda *_: None):
            target = Path(tempfile.mkdtemp()) / "sub" / "p.png"
            return cam.capture(target), target

    def test_capture_saves_a_png_and_releases_the_camera(self):
        module, released = self.fake_cv2()
        ok, target = self.run_capture(module)
        self.assertTrue(ok and target.exists())
        self.assertEqual(len(released), 1)

    def test_camera_that_will_not_open_or_give_frames_returns_false(self):
        for kwargs in ({"opens": False}, {"frames": False}):
            module, released = self.fake_cv2(**kwargs)
            ok, target = self.run_capture(module)
            self.assertFalse(ok)
            self.assertFalse(target.exists())
            self.assertEqual(len(released), 3)  # every backend was tried and released

    def test_without_opencv_it_is_simply_unavailable(self):
        cam = gl_camera.OpenCvCamera()
        cam._tried, cam._cv2_module = True, None
        self.assertFalse(cam.available())
        self.assertFalse(cam.capture(Path(tempfile.mkdtemp()) / "x.png"))


class UpdateTests(unittest.TestCase):
    def test_version_compare(self):
        self.assertTrue(gl_update.is_newer("2.2.1", "2.2.0"))
        self.assertTrue(gl_update.is_newer("v2.10.0", "2.9.9"))
        self.assertFalse(gl_update.is_newer("2.2.0", "2.2.0"))
        self.assertFalse(gl_update.is_newer("2.1.9", "2.2.0"))
        self.assertTrue(gl_update.is_newer("2.2.0.1", "2.2.0"))

    def test_check_latest_with_a_fake_server(self):
        body = json.dumps({"tag_name": "v9.0.0", "html_url": "https://github.com/x/y/releases/tag/v9.0.0"}).encode()
        result = gl_update.check_latest("x/y", "2.2.0", fetch=lambda url: body)
        self.assertEqual(result, {"update": True, "version": "9.0.0", "url": "https://github.com/x/y/releases/tag/v9.0.0"})
        same = gl_update.check_latest("x/y", "9.0.0", fetch=lambda url: body)
        self.assertFalse(same["update"])

    def test_bad_answers_raise_instead_of_returning_nonsense(self):
        for fetch in (lambda u: b"{}", lambda u: b"not json"):
            with self.assertRaises(Exception):
                gl_update.check_latest("x/y", "2.2.0", fetch=fetch)
        with self.assertRaises(ValueError):
            gl_update.check_latest("", "2.2.0", fetch=lambda u: b"{}")


if __name__ == "__main__":
    unittest.main()
