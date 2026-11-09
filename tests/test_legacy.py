"""Tests for the legacy scanner/cleaner using a fake Windows (runs on any OS)."""
import ntpath
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gl_legacy as gl  # noqa: E402

U = r"C:\Users\Abid"
LOCAL = U + r"\AppData\Local"
ROAMING = U + r"\AppData\Roaming"
INSTALL = LOCAL + r"\GhostLock"
TARGET = INSTALL + r"\GhostLock.exe"
OWN = U + r"\Downloads\Setup.exe"
PF = r"C:\Program Files"
PD = r"C:\ProgramData"
COMMON_PROGRAMS = PD + r"\Microsoft\Windows\Start Menu\Programs"


def key(path):
    return path.replace("/", "\\").rstrip("\\").lower()


class FakeSystem:
    def __init__(self, admin=False):
        self.admin = admin
        self.procs = []  # (pid, name)
        self.run = {"HKCU": {}, "HKLM": {}, "HKLM32": {}}
        self.uninstall = {"HKCU": [], "HKLM": [], "HKLM32": []}
        self.tasks = []  # (name, command)
        self.fs = {}  # normalised path -> (real path, is_dir)
        self.denied_tasks = False
        self.killed = []

    # building the fake machine
    def add_dir(self, path):
        self.fs[key(path)] = (path, True)

    def add_file(self, path):
        self.fs[key(path)] = (path, False)
        self.add_dir(ntpath.dirname(path))

    # System API
    def processes(self):
        return list(self.procs)

    def kill(self, pid):
        self.killed.append(pid)
        self.procs = [p for p in self.procs if p[0] != pid]

    def run_values(self, hive):
        return dict(self.run[hive])

    def delete_run_value(self, hive, name):
        if hive != "HKCU" and not self.admin:
            raise PermissionError("denied")
        self.run[hive].pop(name, None)

    def uninstall_entries(self, hive):
        return list(self.uninstall[hive])

    def delete_uninstall_entry(self, hive, name):
        if hive != "HKCU" and not self.admin:
            raise PermissionError("denied")
        self.uninstall[hive] = [e for e in self.uninstall[hive] if e[0] != name]

    def scheduled_tasks(self):  # like the real wrapper: only tasks that mention GhostLock
        return [t for t in self.tasks if "ghostlock" in (t[0] + t[1]).lower()]

    def delete_task(self, name):
        if self.denied_tasks and not self.admin:
            raise PermissionError("denied")
        self.tasks = [t for t in self.tasks if t[0] != name]

    def exists(self, path):
        return key(path) in self.fs

    def is_dir(self, path):
        return self.fs.get(key(path), (None, False))[1]

    def list_dir(self, path):
        base = key(path)
        return sorted(real.rsplit("\\", 1)[-1] for k, (real, _d) in self.fs.items() if k != base and key(ntpath.dirname(real)) == base)

    def remove_path(self, path):
        base = key(path)
        for k in [k for k in self.fs if k == base or k.startswith(base + "\\")]:
            del self.fs[k]

    def is_admin(self):
        return self.admin


def make_env(**overrides):
    values = dict(
        own_pids=(500, 501),
        own_exe=OWN,
        app_version=gl.APP_VERSION,
        installed_version=None,
        user_install_dir=INSTALL,
        expected_target=TARGET,
        program_roots=(LOCAL + r"\Programs", PF, PD),
        user_startup_dir=ROAMING + r"\Microsoft\Windows\Start Menu\Programs\Startup",
        user_programs_dir=ROAMING + r"\Microsoft\Windows\Start Menu\Programs",
        common_startup_dir=COMMON_PROGRAMS + r"\Startup",
        common_programs_dir=COMMON_PROGRAMS,
        public_desktop_dir=r"C:\Users\Public\Desktop",
        app_data_dir=ROAMING + r"\GhostLock",
        user_profile_dir=U,
        protected_roots=(PF, PD, r"C:\Windows"),
    )
    values.update(overrides)
    return gl.Environment(**values)


def old_machine(admin=False):
    """A PC with a stale 2.0 install plus every kind of leftover."""
    fake = FakeSystem(admin)
    fake.add_file(TARGET)
    fake.add_file(ROAMING + r"\GhostLock\config.json")
    fake.add_file(PF + r"\GhostLock\GhostLock.exe")
    fake.add_file(OWN)
    fake.procs = [(500, "Setup.exe"), (501, "Setup.exe"), (777, "GhostLock.exe"), (778, "GhostLock.exe"), (900, "chrome.exe")]
    fake.run["HKCU"]["GhostLock"] = '"' + TARGET + '" --run'  # points at the stale install: refreshed by the installer
    fake.run["HKCU"]["GhostLockOld"] = r'"C:\Tools\GhostLock-old.exe" --run'
    fake.run["HKLM"]["GhostLock"] = '"' + PF + r'\GhostLock\GhostLock.exe"'
    fake.run["HKCU"]["Spotify"] = r'"C:\Spotify\spotify.exe"'
    fake.add_file(r"C:\Tools\GhostLock-old.exe")
    fake.add_file(fake_startup() + r"\GhostLock.lnk")
    fake.add_file(COMMON_PROGRAMS + r"\Startup\GhostLock.lnk")
    fake.add_file(r"C:\Users\Public\Desktop\GhostLock.lnk")
    fake.add_file(fake_startup() + r"\Other.lnk")
    fake.uninstall["HKCU"] = [("GhostLock", "GhostLock", INSTALL, '"' + TARGET + '" --uninstall'), ("Chrome", "Google Chrome", "", "")][:1]
    fake.uninstall["HKLM"] = [("GhostLock_is1", "GhostLock 1.0", PF + r"\GhostLock", "")]
    fake.tasks = [(r"\GhostLock autostart", '"' + PF + r'\GhostLock\GhostLock.exe"'), (r"\Backup", "backup.exe")]
    return fake


def fake_startup():
    return ROAMING + r"\Microsoft\Windows\Start Menu\Programs\Startup"


class ScannerTests(unittest.TestCase):
    def scan(self, fake, **env):
        return gl.LegacyScanner(fake, make_env(**env)).scan()

    def test_finds_every_kind_and_ignores_unrelated(self):
        result = self.scan(old_machine())
        found = {(item.kind, item.key) for item in result.items}
        self.assertIn((gl.PROCESS, "777"), found)
        self.assertIn((gl.PROCESS, "778"), found)
        self.assertNotIn((gl.PROCESS, "500"), found)  # own installer processes
        self.assertNotIn((gl.PROCESS, "900"), found)
        self.assertIn((gl.RUN_VALUE, "HKCU|GhostLockOld"), found)
        self.assertIn((gl.RUN_VALUE, "HKLM|GhostLock"), found)
        self.assertNotIn((gl.RUN_VALUE, "HKCU|GhostLock"), found)  # already points at the install target
        self.assertNotIn((gl.RUN_VALUE, "HKCU|Spotify"), found)
        self.assertIn((gl.STARTUP_LINK, fake_startup() + r"\GhostLock.lnk"), found)
        self.assertIn((gl.STARTUP_LINK, COMMON_PROGRAMS + r"\Startup\GhostLock.lnk"), found)
        self.assertNotIn((gl.STARTUP_LINK, fake_startup() + r"\Other.lnk"), found)
        self.assertIn((gl.SHORTCUT, r"C:\Users\Public\Desktop\GhostLock.lnk"), found)
        self.assertIn((gl.TASK, r"\GhostLock autostart"), found)
        self.assertNotIn((gl.TASK, r"\Backup"), found)
        self.assertIn((gl.PROGRAM_DIR, INSTALL), found)  # stale version
        self.assertIn((gl.PROGRAM_DIR, PF + r"\GhostLock"), found)
        self.assertIn((gl.PROGRAM_FILE, r"C:\Tools\GhostLock-old.exe"), found)
        self.assertIn((gl.UNINSTALL_ENTRY, "HKLM|GhostLock_is1"), found)
        self.assertTrue(result.has_legacy and result.needs_admin)
        self.assertEqual(result.data_item.key, ROAMING + r"\GhostLock")

    def test_admin_flags(self):
        items = {(i.kind, i.key): i for i in self.scan(old_machine()).items}
        self.assertTrue(items[(gl.RUN_VALUE, "HKLM|GhostLock")].admin)
        self.assertTrue(items[(gl.PROGRAM_DIR, PF + r"\GhostLock")].admin)
        self.assertTrue(items[(gl.SHORTCUT, r"C:\Users\Public\Desktop\GhostLock.lnk")].admin)
        self.assertFalse(items[(gl.RUN_VALUE, "HKCU|GhostLockOld")].admin)
        self.assertFalse(items[(gl.PROGRAM_DIR, INSTALL)].admin)

    def test_clean_current_install_is_not_legacy(self):
        fake = FakeSystem()
        fake.add_file(TARGET)
        fake.add_file(ROAMING + r"\GhostLock\config.json")
        fake.run["HKCU"]["GhostLock"] = '"' + TARGET + '" --run'
        fake.uninstall["HKCU"] = [("GhostLock", "GhostLock", INSTALL, "")]
        result = self.scan(fake, installed_version=gl.APP_VERSION)
        self.assertFalse(result)
        self.assertIsNone(result.data_item)  # settings alone are never "legacy"

    def test_running_current_copy_is_listed_but_not_legacy(self):
        fake = FakeSystem()
        fake.add_file(TARGET)
        fake.procs = [(321, "GhostLock.exe")]
        result = self.scan(fake, installed_version=gl.APP_VERSION)
        self.assertEqual([i.kind for i in result.items], [gl.PROCESS])
        self.assertFalse(result.has_legacy)

    def test_never_targets_the_running_program(self):
        fake = FakeSystem()
        fake.add_file(TARGET)  # old install, and the new exe is running from inside it
        result = self.scan(fake, own_exe=TARGET)
        self.assertEqual([i for i in result.items if i.kind == gl.PROGRAM_DIR], [])

    def test_fast_scan_skips_slow_probes_but_still_finds_the_rest(self):
        fake = old_machine()
        fake.scheduled_tasks = lambda: (_ for _ in ()).throw(AssertionError("schtasks must not run in a fast scan"))
        fake.processes = lambda: (_ for _ in ()).throw(AssertionError("tasklist must not run in a fast scan"))
        result = gl.LegacyScanner(fake, make_env()).scan(fast=True)
        kinds = {i.kind for i in result.items}
        self.assertNotIn(gl.PROCESS, kinds)
        self.assertNotIn(gl.TASK, kinds)
        self.assertIn(gl.RUN_VALUE, kinds)
        self.assertTrue(result.has_legacy)

    def test_scan_survives_a_failing_probe(self):
        fake = old_machine()
        fake.scheduled_tasks = lambda: (_ for _ in ()).throw(RuntimeError("schtasks missing"))
        result = self.scan(fake)
        self.assertIn(gl.RUN_VALUE, {i.kind for i in result.items})


class CleanerTests(unittest.TestCase):
    def run_cleanup(self, fake, remove_data=False, elevate=None, env=None):
        env = env or make_env()
        scan = gl.LegacyScanner(fake, env).scan()
        run = gl.CleanupRun(scan, remove_data=remove_data, system=fake, env=env, elevate=elevate or (lambda: False))
        return scan, run.run_all()

    def test_user_level_cleanup_defers_machine_items(self):
        fake = old_machine(admin=False)
        scan, report = self.run_cleanup(fake)
        self.assertEqual(sorted(fake.killed), [777, 778])
        self.assertNotIn("GhostLockOld", fake.run["HKCU"])
        self.assertIn("Spotify", fake.run["HKCU"])
        self.assertFalse(fake.exists(fake_startup() + r"\GhostLock.lnk"))
        self.assertTrue(fake.exists(fake_startup() + r"\Other.lnk"))
        self.assertFalse(fake.exists(INSTALL))
        self.assertFalse(fake.exists(r"C:\Tools\GhostLock-old.exe"))  # tried as the normal user, so no prompt needed
        self.assertTrue(fake.exists(OWN))
        # machine-wide leftovers wait for administrator rights
        self.assertIn("GhostLock", fake.run["HKLM"])
        self.assertTrue(fake.exists(PF + r"\GhostLock"))
        self.assertTrue(report.deferred)
        self.assertTrue(all(i.admin for i in report.deferred))
        self.assertFalse(report.ok)

    def test_elevation_finishes_the_job(self):
        fake = old_machine(admin=False)

        def elevate():  # what the elevated helper does: same scan, but with admin rights
            helper = FakeSystem.__new__(FakeSystem)
            helper.__dict__ = fake.__dict__
            fake.admin = True
            gl.CleanupRun(gl.LegacyScanner(fake, make_env()).scan(), system=fake, env=make_env(), elevate=lambda: False).run_all()
            fake.admin = False
            return True

        scan, report = self.run_cleanup(fake, elevate=elevate)
        self.assertEqual(fake.run["HKLM"], {})
        self.assertFalse(fake.exists(PF + r"\GhostLock"))
        self.assertFalse(fake.exists(r"C:\Users\Public\Desktop\GhostLock.lnk"))
        self.assertEqual(fake.tasks, [(r"\Backup", "backup.exe")])
        self.assertEqual(fake.uninstall["HKLM"], [])
        self.assertTrue(report.ok, report)
        self.assertEqual(report.deferred, [])

    def test_declined_elevation_keeps_machine_items_and_reports_them(self):
        fake = old_machine(admin=False)
        asked = []
        scan, report = self.run_cleanup(fake, elevate=lambda: asked.append(1) or False)
        self.assertEqual(len(asked), 1)  # exactly one Windows prompt
        self.assertTrue(report.deferred)
        self.assertIn("GhostLock", fake.run["HKLM"])

    def test_no_prompt_when_nothing_needs_admin(self):
        fake = FakeSystem()
        fake.add_file(TARGET)
        fake.run["HKCU"]["GhostLockOld"] = r'"C:\Tools\GhostLock-old.exe"'
        asked = []
        scan, report = self.run_cleanup(fake, elevate=lambda: asked.append(1) or True)
        self.assertEqual(asked, [])
        self.assertTrue(report.ok)

    def test_user_data_only_removed_when_asked(self):
        fake = old_machine(admin=True)
        self.run_cleanup(fake, remove_data=False)
        self.assertTrue(fake.exists(ROAMING + r"\GhostLock\config.json"))
        fake = old_machine(admin=True)
        self.run_cleanup(fake, remove_data=True)
        self.assertFalse(fake.exists(ROAMING + r"\GhostLock"))

    def test_task_denied_without_admin_is_deferred_not_failed(self):
        fake = FakeSystem()
        fake.add_file(TARGET)
        fake.tasks = [(r"\GhostLock logon", TARGET)]
        fake.denied_tasks = True
        scan, report = self.run_cleanup(fake)
        self.assertEqual(report.failed, [])
        self.assertEqual([i.kind for i in report.deferred], [gl.TASK])

    def test_guard_refuses_to_delete_the_running_program(self):
        fake = FakeSystem()
        fake.add_file(TARGET)
        env = make_env(own_exe=TARGET)
        run = gl.CleanupRun(gl.ScanResult(), system=fake, env=env, elevate=lambda: False)
        for item in (gl.LegacyItem(gl.PROGRAM_DIR, INSTALL, "x"), gl.LegacyItem(gl.PROGRAM_FILE, TARGET, "x")):
            run._clean(item)
        self.assertTrue(fake.exists(TARGET))
        self.assertEqual(len(run.report.deferred) + len(run.report.failed), 2)

    def test_guard_refuses_dangerous_paths(self):
        fake = FakeSystem()
        run = gl.CleanupRun(gl.ScanResult(), system=fake, env=make_env(), elevate=lambda: False)
        run.is_admin = True
        run._clean(gl.LegacyItem(gl.PROGRAM_DIR, "C:\\", "x"))
        run._clean(gl.LegacyItem(gl.PROGRAM_DIR, r"C:\Windows", "x"))
        self.assertEqual(len(run.report.failed), 2)

    def test_steps_have_progress_labels_and_final_check(self):
        fake = old_machine(admin=True)
        scan = gl.LegacyScanner(fake, make_env()).scan()
        steps = gl.CleanupRun(scan, remove_data=True, system=fake, env=make_env()).steps()
        self.assertEqual(len(steps), len(scan.items) + 2)  # items + data + final check
        self.assertTrue(all(label.endswith("\u2026") for label, _ in steps))


class HelperTests(unittest.TestCase):
    def test_keep_pids_parsing(self):
        old = sys.argv
        try:
            sys.argv = ["x", gl.ADMIN_ARG, gl.KEEP_PIDS_ARG, "12,34"]
            self.assertEqual(gl.admin_keep_pids(), (12, 34))
            self.assertTrue(gl.is_admin_cleanup_invocation())
            sys.argv = ["x", gl.ADMIN_ARG]
            self.assertEqual(gl.admin_keep_pids(), ())
            sys.argv = ["x"]
            self.assertFalse(gl.is_admin_cleanup_invocation())
        finally:
            sys.argv = old

    def test_helper_never_kills_the_installer_that_started_it(self):
        fake = FakeSystem(admin=True)
        fake.procs = [(4242, "GhostLock.exe"), (777, "GhostLock.exe")]
        env = make_env(own_pids=(1, 2, 4242))
        gl.CleanupRun(gl.LegacyScanner(fake, env).scan(), system=fake, env=env, elevate=lambda: False).run_all()
        self.assertEqual(fake.killed, [777])



# --------------------------------------------------------------------------- the original bug: startup pointed at the old exe
import json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

import gl_core  # noqa: E402


class FakeWinreg:
    """Just enough of winreg for the Run key."""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self, values=None):
        self.values = dict(values or {})

    def _key(self):
        outer = self

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Key()

    def OpenKey(self, hive, path, *args):  # noqa: N802
        return self._key()

    CreateKey = OpenKey

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, _r, _t, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        self.values.pop(name, None)


class StartupBugTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.install = self.tmp / "Local" / "GhostLock"
        self.install.mkdir(parents=True)
        self.installed = self.install / "GhostLock.exe"
        self.installed.write_bytes(b"x")
        self.fresh = self.tmp / "Downloads" / "GhostLock.exe"
        self.fresh.parent.mkdir()
        self.fresh.write_bytes(b"x")
        self.patches = [
            mock.patch.object(gl_core, "install_dir", lambda: self.install),
            mock.patch.object(gl_core, "startup_shortcut_path", lambda: self.tmp / "none.lnk"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def write_info(self, version):
        (self.install / "install_info.json").write_text(json.dumps({"app_version": version}))

    def test_stale_install_does_not_win_over_the_running_build(self):
        self.write_info("2.0.0")
        with mock.patch.object(gl_core, "current_executable_path", lambda: self.fresh):
            self.assertEqual(gl_core.app_launcher_path(), self.fresh)  # this was the bug: it returned the old install

    def test_same_version_install_is_used(self):
        self.write_info(gl_core.APP_VERSION)
        with mock.patch.object(gl_core, "current_executable_path", lambda: self.fresh):
            self.assertEqual(gl_core.app_launcher_path(), self.installed)

    def test_running_from_the_install_folder_uses_it_even_without_info(self):
        with mock.patch.object(gl_core, "current_executable_path", lambda: self.installed):
            self.assertEqual(gl_core.app_launcher_path(), self.installed)

    def run_repair(self, current, values):
        reg = FakeWinreg(values)
        with mock.patch.object(gl_core, "_winreg", lambda: reg), mock.patch.object(gl_core, "current_executable_path", lambda: current), mock.patch.object(sys, "frozen", True, create=True):
            changed = gl_core.repair_startup_entry()
        return changed, reg.values

    def test_repair_points_startup_at_the_running_installed_copy(self):
        changed, values = self.run_repair(self.installed, {"GhostLock": r'"C:\Old\GhostLock.exe" --run'})
        self.assertTrue(changed)
        self.assertEqual(values["GhostLock"], f'"{self.installed}" --run')

    def test_repair_leaves_a_correct_entry_alone(self):
        changed, values = self.run_repair(self.installed, {"GhostLock": f'"{self.installed}" --run'})
        self.assertFalse(changed)

    def test_repair_does_not_turn_startup_on(self):
        changed, values = self.run_repair(self.installed, {})
        self.assertFalse(changed)
        self.assertEqual(values, {})

    def test_repair_ignores_copies_outside_the_install_folder(self):
        changed, values = self.run_repair(self.fresh, {"GhostLock": r'"C:\Old\GhostLock.exe" --run'})
        self.assertFalse(changed)

    def test_stray_copy_detection(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            for argv, current, expected in (
                (["x"], self.fresh, True),
                (["x"], self.installed, False),
                (["x", "--run"], self.fresh, False),
                (["x", "--uninstall"], self.fresh, False),
            ):
                with mock.patch.object(sys, "argv", argv), mock.patch.object(gl_core, "current_executable_path", lambda c=current: c):
                    self.assertEqual(gl_core.is_stray_copy(), expected, (argv, current))
        with mock.patch.object(sys, "argv", ["x"]):  # running from source is never a stray copy
            self.assertFalse(gl_core.is_stray_copy())


if __name__ == "__main__":
    unittest.main()
