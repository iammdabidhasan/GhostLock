import os
import sys
import tempfile
import unittest
from pathlib import Path

import ghostlock


class CoreBehaviorTests(unittest.TestCase):
    def test_password_hash_verification(self):
        record = ghostlock.hash_secret("correct-password")
        self.assertTrue(ghostlock.verify_secret("correct-password", record))
        self.assertFalse(ghostlock.verify_secret("wrong-password", record))

    def test_config_round_trip(self):
        old_appdata = os.environ.get("APPDATA")
        old_localappdata = os.environ.get("LOCALAPPDATA")
        try:
            with tempfile.TemporaryDirectory() as temp:
                os.environ["APPDATA"] = str(Path(temp) / "Roaming")
                os.environ["LOCALAPPDATA"] = str(Path(temp) / "Local")
                config = ghostlock.ConfigManager()
                config.configure("main-password", "backup-password", "Favorite release name?", "GhostLock")

                loaded = ghostlock.ConfigManager()
                self.assertTrue(loaded.is_configured)
                self.assertTrue(loaded.verify_password("main-password"))
                self.assertTrue(loaded.verify_emergency_password("backup-password"))
                self.assertTrue(loaded.verify_security_answer("ghostlock"))
                self.assertFalse(loaded.verify_password("bad-password"))
        finally:
            self._restore_env("APPDATA", old_appdata)
            self._restore_env("LOCALAPPDATA", old_localappdata)

    def test_invocation_modes(self):
        old_argv = sys.argv[:]
        old_executable = sys.executable
        had_frozen = hasattr(sys, "frozen")
        old_frozen = getattr(sys, "frozen", None)
        try:
            sys.frozen = True
            sys.argv = ["Setup.exe"]
            sys.executable = r"C:\Package\Setup.exe"
            self.assertTrue(ghostlock.is_setup_invocation())
            self.assertFalse(ghostlock.is_uninstall_invocation())

            sys.argv = ["GhostLock.exe", "--run"]
            sys.executable = r"C:\Users\Person\AppData\Local\GhostLock\GhostLock.exe"
            self.assertFalse(ghostlock.is_setup_invocation())
            self.assertFalse(ghostlock.is_uninstall_invocation())
            self.assertFalse(ghostlock.is_forced_setup_invocation())

            sys.argv = ["GhostLock.exe", "--setup"]
            self.assertFalse(ghostlock.is_setup_invocation())
            self.assertFalse(ghostlock.is_uninstall_invocation())
            self.assertTrue(ghostlock.is_forced_setup_invocation())

            sys.argv = ["GhostLock.exe", "--uninstall"]
            self.assertFalse(ghostlock.is_setup_invocation())
            self.assertTrue(ghostlock.is_uninstall_invocation())
        finally:
            sys.argv = old_argv
            sys.executable = old_executable
            if had_frozen:
                sys.frozen = old_frozen
            elif hasattr(sys, "frozen"):
                delattr(sys, "frozen")

    @staticmethod
    def _restore_env(name, old_value):
        if old_value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old_value


class HardeningTests(unittest.TestCase):
    def test_attempt_limiter_escalates_and_resets(self):
        now = [0.0]
        limiter = ghostlock.AttemptLimiter(clock=lambda: now[0])
        for _ in range(4):
            limiter.record_failure()
        self.assertFalse(limiter.blocked)
        limiter.record_failure()  # 5th wrong attempt -> first cool-down
        self.assertTrue(limiter.blocked)
        self.assertGreaterEqual(limiter.remaining(), 15)
        now[0] += 16
        self.assertFalse(limiter.blocked)
        limiter.record_failure()
        self.assertGreaterEqual(limiter.remaining(), 30)  # delay doubles
        limiter.reset()
        self.assertFalse(limiter.blocked)

    def test_password_strength_ordering(self):
        self.assertEqual(ghostlock.password_strength("")[0], 0)
        self.assertEqual(ghostlock.password_strength("abc")[1], "Too short")
        weak = ghostlock.password_strength("aaaaaa")[0]
        strong = ghostlock.password_strength("Correct-Horse-Battery9")[0]
        self.assertLess(weak, strong)

    def test_verify_secret_rejects_tampered_iterations(self):
        record = ghostlock.hash_secret("secret-1")
        record["iterations"] = 10**9
        self.assertFalse(ghostlock.verify_secret("secret-1", record))

    def test_corrupt_config_is_quarantined_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            old = os.environ.get("APPDATA")
            os.environ["APPDATA"] = temp
            try:
                folder = Path(temp) / "GhostLock"
                folder.mkdir()
                (folder / "config.json").write_text("{not json", encoding="utf-8")
                config = ghostlock.ConfigManager()
                self.assertFalse(config.is_configured)
                self.assertFalse((folder / "config.json").exists())
                self.assertTrue(list(folder.glob("config.corrupt-*.json")))
            finally:
                CoreBehaviorTests._restore_env("APPDATA", old)

    def test_update_credentials_is_atomic_and_keeps_widget_on_reconfigure(self):
        with tempfile.TemporaryDirectory() as temp:
            old = os.environ.get("APPDATA")
            os.environ["APPDATA"] = temp
            try:
                config = ghostlock.ConfigManager()
                config.configure("main-pass", "emergency-pass", "A question?", "answer")
                config.set_widget_position(40, 50)
                config.update_credentials(emergency="new-emergency", question="New question?", answer="fresh")
                self.assertTrue(config.verify_emergency_password("new-emergency"))
                self.assertTrue(config.verify_security_answer("FRESH"))
                self.assertTrue(config.verify_password("main-pass"))
                config.configure("other-main", "other-emerg", "Q one two?", "a1")
                self.assertEqual(config.data["widget"]["x"], 40)
            finally:
                CoreBehaviorTests._restore_env("APPDATA", old)


if __name__ == "__main__":
    unittest.main()
