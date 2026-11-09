"""GhostLock legacy scanner and cleaner.

Finds leftovers of older GhostLock versions (running processes, startup entries, scheduled tasks,
old install folders, shortcuts and uninstall entries) and removes them, with the user's permission,
so the new version can install cleanly.

Design
------
* ``LegacyScanner`` only *reads* and returns ``LegacyItem`` objects.
* ``CleanupRun`` removes the chosen items one step at a time (so a UI can show progress).
* All operating-system access goes through a ``System`` object. ``WindowsSystem`` is the real one;
  tests use a fake, so the logic is verified without a Windows machine.
* Items that need administrator rights (machine-wide entries) are handled by a short elevated helper
  (``ghostlock --legacy-admin-clean``). The helper never trusts input from the normal process:
  it re-scans on its own and only removes what its own scan finds.
* Paths are handled as Windows-style text (``ntpath``) so the logic behaves the same everywhere.
"""
import csv
import ctypes
import io
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field

from gl_core import (
    APP_NAME,
    APP_VERSION,
    INSTALLED_EXE_NAME,
    IS_WINDOWS,
    RUN_KEY_PATH,
    UNINSTALL_KEY_PATH,
    app_data_dir,
    current_executable_path,
    install_dir,
    installed_app_version,
    log_exception,
    startup_command_target,
    startup_dir,
    start_menu_dir,
)

ADMIN_ARG = "--legacy-admin-clean"
NEEDLE = APP_NAME.lower()
UNINSTALL_PARENT = UNINSTALL_KEY_PATH.rsplit("\\", 1)[0]
STARTUP_EXTENSIONS = (".lnk", ".exe", ".bat", ".cmd", ".vbs", ".url")
HIVES = ("HKCU", "HKLM", "HKLM32")  # current user, machine (64-bit view), machine (32-bit view)
KEEP_PIDS_ARG = "--keep-pids"

# item kinds (also the order in which they are cleaned)
PROCESS = "process"
TASK = "task"
RUN_VALUE = "run"
STARTUP_LINK = "startup"
UNINSTALL_ENTRY = "uninstall"
SHORTCUT = "shortcut"
PROGRAM_DIR = "folder"
PROGRAM_FILE = "file"
USER_DATA = "data"
CLEAN_ORDER = (PROCESS, TASK, RUN_VALUE, STARTUP_LINK, UNINSTALL_ENTRY, SHORTCUT, PROGRAM_FILE, PROGRAM_DIR, USER_DATA)

KIND_TITLES = {
    PROCESS: "Running program",
    TASK: "Scheduled task",
    RUN_VALUE: "Start-with-Windows entry",
    STARTUP_LINK: "Startup shortcut",
    UNINSTALL_ENTRY: "Windows Apps entry",
    SHORTCUT: "Shortcut",
    PROGRAM_DIR: "Old program folder",
    PROGRAM_FILE: "Old program file",
    USER_DATA: "Old passwords and settings",
}


# --------------------------------------------------------------------------- data types
@dataclass(frozen=True)
class LegacyItem:
    kind: str
    key: str  # what identifies the thing: pid, "HKLM|name", a path or a task name
    title: str
    detail: str = ""
    admin: bool = False  # needs administrator rights to remove

    @property
    def label(self):
        return f"{KIND_TITLES.get(self.kind, self.kind)}: {self.title}"


@dataclass
class ScanResult:
    items: list = field(default_factory=list)
    data_item: LegacyItem = None  # user settings folder (optional to remove)

    @property
    def legacy_items(self):
        return [item for item in self.items if item.kind != PROCESS]

    @property
    def has_legacy(self):
        """True when older leftovers exist (a merely running copy does not count)."""
        return bool(self.legacy_items)

    @property
    def needs_admin(self):
        return any(item.admin for item in self.items)

    def __bool__(self):
        return bool(self.items)


@dataclass
class CleanupReport:
    done: list = field(default_factory=list)
    failed: list = field(default_factory=list)  # (item, reason)
    deferred: list = field(default_factory=list)  # need administrator rights
    remaining: list = field(default_factory=list)  # still present after cleanup

    @property
    def ok(self):
        return not self.failed and not self.remaining


# --------------------------------------------------------------------------- environment
@dataclass
class Environment:
    """Where to look. ``detect()`` fills it from the real Windows session."""

    own_pids: tuple = ()
    own_exe: str = ""
    app_version: str = APP_VERSION
    installed_version: str = None
    user_install_dir: str = ""
    expected_target: str = ""
    program_roots: tuple = ()  # folders that may hold GhostLock\ (machine-wide installs)
    user_startup_dir: str = ""
    user_programs_dir: str = ""
    common_startup_dir: str = ""
    common_programs_dir: str = ""
    public_desktop_dir: str = ""
    app_data_dir: str = ""
    user_profile_dir: str = ""
    protected_roots: tuple = ()  # Program Files, ProgramData, Windows: removing from here needs administrator rights

    @classmethod
    def detect(cls, extra_pids=()):
        def env(name):
            return os.environ.get(name, "")

        local, program_data = env("LOCALAPPDATA"), env("ProgramData")
        roots = []
        for base in (
            ntpath.join(local, "Programs") if local else "",
            env("ProgramFiles"),
            env("ProgramFiles(x86)"),
            env("ProgramW6432"),
            program_data,
        ):
            if base and base not in roots:
                roots.append(base)
        user_install = str(install_dir())
        common_programs = ntpath.join(program_data, "Microsoft", "Windows", "Start Menu", "Programs") if program_data else ""
        return cls(
            own_pids=(os.getpid(), os.getppid(), *extra_pids),  # onefile exes run as a parent + child pair
            own_exe=str(current_executable_path()),
            installed_version=installed_app_version(),
            user_install_dir=user_install,
            expected_target=ntpath.join(user_install, INSTALLED_EXE_NAME),
            program_roots=tuple(roots),
            user_startup_dir=str(startup_dir()),
            user_programs_dir=str(start_menu_dir()),
            common_startup_dir=ntpath.join(common_programs, "Startup") if common_programs else "",
            common_programs_dir=common_programs,
            public_desktop_dir=ntpath.join(env("PUBLIC"), "Desktop") if env("PUBLIC") else "",
            app_data_dir=str(app_data_dir()),
            user_profile_dir=env("USERPROFILE"),
            protected_roots=tuple(p for p in (env("ProgramFiles"), env("ProgramFiles(x86)"), env("ProgramW6432"), program_data, env("SystemRoot")) if p),
        )


def _norm(path):
    return str(path or "").replace("/", "\\").rstrip("\\").lower()


def _inside(path, folder):
    path, folder = _norm(path), _norm(folder)
    return bool(folder) and (path == folder or path.startswith(folder + "\\"))


def _mentions_app(text):
    return NEEDLE in str(text or "").lower()


# --------------------------------------------------------------------------- real system access
class WindowsSystem:
    """Thin, individually testable wrappers around Windows. Denied access raises PermissionError."""

    # -- processes
    def processes(self):
        out = self._run(["tasklist", "/FO", "CSV", "/NH"]).stdout
        result = []
        for row in csv.reader(io.StringIO(out)):
            if len(row) >= 2 and row[1].strip().isdigit():
                result.append((int(row[1]), row[0]))
        return result

    def kill(self, pid):
        done = self._run(["taskkill", "/PID", str(pid), "/T", "/F"])
        text = (done.stdout or "") + (done.stderr or "")
        if done.returncode != 0 and "access is denied" in text.lower():
            raise PermissionError(text.strip())

    # -- registry
    def _open(self, hive, path, write=False):
        import winreg

        access = winreg.KEY_READ | (winreg.KEY_SET_VALUE | winreg.KEY_WRITE if write else 0)
        if hive == "HKCU":
            return winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, access)
        view = winreg.KEY_WOW64_32KEY if hive == "HKLM32" else winreg.KEY_WOW64_64KEY
        return winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, access | view)

    def run_values(self, hive):
        import winreg

        values = {}
        try:
            with self._open(hive, RUN_KEY_PATH) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, data, _kind = winreg.EnumValue(key, index)
                    values[name] = str(data)
        except OSError:
            pass
        return values

    def delete_run_value(self, hive, name):
        import winreg

        try:
            with self._open(hive, RUN_KEY_PATH, write=True) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def uninstall_entries(self, hive):
        import winreg

        found = []
        try:
            with self._open(hive, UNINSTALL_PARENT) as parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    name = winreg.EnumKey(parent, index)
                    try:
                        with self._open(hive, UNINSTALL_PARENT + "\\" + name) as sub:
                            display = self._value(sub, "DisplayName")
                            location = self._value(sub, "InstallLocation")
                            command = self._value(sub, "UninstallString")
                    except OSError:
                        continue
                    if _mentions_app(name) or _mentions_app(display):
                        found.append((name, display or name, location, command))
        except OSError:
            pass
        return found

    @staticmethod
    def _value(key, name):
        import winreg

        try:
            return str(winreg.QueryValueEx(key, name)[0])
        except OSError:
            return ""

    def delete_uninstall_entry(self, hive, name):
        import winreg

        path = UNINSTALL_PARENT + "\\" + name
        try:
            if hive == "HKCU":
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            else:
                view = winreg.KEY_WOW64_32KEY if hive == "HKLM32" else winreg.KEY_WOW64_64KEY
                winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, path, view, 0)
        except FileNotFoundError:
            pass

    # -- scheduled tasks
    def scheduled_tasks(self):
        try:
            out = self._run(["schtasks", "/Query", "/FO", "CSV", "/V", "/NH"], timeout=45).stdout
        except Exception:
            return []
        seen, tasks = set(), []
        for row in csv.reader(io.StringIO(out)):
            if len(row) < 9:
                continue
            name, command = row[1], row[8]
            if (_mentions_app(name) or _mentions_app(command)) and name not in seen:
                seen.add(name)
                tasks.append((name, command))
        return tasks

    def delete_task(self, name):
        done = self._run(["schtasks", "/Delete", "/TN", name, "/F"])
        text = (done.stdout or "") + (done.stderr or "")
        if done.returncode != 0:
            if "access is denied" in text.lower():
                raise PermissionError(text.strip())
            if "cannot find" not in text.lower() and "does not exist" not in text.lower():
                raise OSError(text.strip() or "schtasks failed")

    # -- files
    def exists(self, path):
        return os.path.exists(path)

    def is_dir(self, path):
        return os.path.isdir(path)

    def list_dir(self, path):
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def remove_path(self, path):
        """Delete a file or folder tree, retrying briefly (antivirus or a closing process may hold it)."""
        last = None
        self._clear_readonly(path)
        for _attempt in range(6):
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                elif os.path.lexists(path):
                    os.unlink(path)
                return
            except FileNotFoundError:
                return
            except PermissionError as exc:
                if getattr(exc, "winerror", None) == 5:
                    raise  # access denied will not fix itself; a sharing violation (32) might
                last = exc
            except OSError as exc:
                last = exc
            time.sleep(0.5)
        raise last

    @staticmethod
    def _clear_readonly(path):
        """Windows refuses to delete read-only files; clear the flag first."""
        try:
            targets = [path]
            if os.path.isdir(path):
                for folder, dirs, files in os.walk(path):
                    targets += [os.path.join(folder, name) for name in dirs + files]
            for target in targets:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass

    # -- identity
    def is_admin(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    @staticmethod
    def _run(command, timeout=20):
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="oem" if sys.platform == "win32" else None,  # taskkill/schtasks print in the OEM code page
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


# --------------------------------------------------------------------------- scanner
class LegacyScanner:
    def __init__(self, system=None, env=None):
        self.system = system or WindowsSystem()
        self.env = env or Environment.detect()
        self._tasks_cache = self._runs_cache = None
        self.fast = False  # fast scans skip the slow probes (scheduled tasks, process list)

    def _tasks_cached(self):
        if self.fast:
            return []
        if self._tasks_cache is None:
            self._tasks_cache = self.system.scheduled_tasks()
        return self._tasks_cache

    def _runs_cached(self):
        if self._runs_cache is None:
            self._runs_cache = {hive: self.system.run_values(hive) for hive in HIVES}
        return self._runs_cache

    def _is_machine_wide(self, path):
        """Only the protected system folders are assumed to need administrator rights.
        Anything else is tried as the normal user first and deferred if Windows says access denied."""
        return any(_inside(path, root) for root in self.env.protected_roots)

    def scan(self, fast=False):
        self.fast = fast
        self._tasks_cache = self._runs_cache = None
        items = []
        for step in (
            self._processes,
            self._tasks,
            self._run_values,
            self._startup_links,
            self._uninstall_entries,
            self._machine_shortcuts,
            self._program_folders,
            self._referenced_programs,
        ):
            try:
                items.extend(step())
            except Exception as exc:  # one failing probe must never block the others
                log_exception(f"Legacy scan step {step.__name__} failed", exc)
        items = self._unique(items)
        return ScanResult(items=items, data_item=self._user_data(items))

    # -- probes
    def _processes(self):
        if self.fast:
            return []
        pattern = re.compile(rf"^{re.escape(NEEDLE)}.*\.exe$", re.IGNORECASE)
        found = []
        for pid, name in self.system.processes():
            if pid in self.env.own_pids or not pattern.match(name):
                continue
            found.append(LegacyItem(PROCESS, str(pid), f"{name} (process {pid})"))
        return found

    def _tasks(self):
        return [LegacyItem(TASK, name, name, command) for name, command in self._tasks_cached()]

    def _is_expected_target(self, command):
        return _norm(startup_command_target(command)) == _norm(self.env.expected_target)

    def _run_values(self):
        found = []
        for hive in HIVES:
            for name, command in self._runs_cached()[hive].items():
                if not (_mentions_app(name) or _mentions_app(command)):
                    continue
                if hive == "HKCU" and self._is_expected_target(command):
                    continue  # already points at the right place; the installer refreshes it
                found.append(LegacyItem(RUN_VALUE, f"{hive}|{name}", f"{name}: {command}", command, admin=hive != "HKCU"))
        return found

    def _startup_links(self):
        found = []
        for folder, admin in ((self.env.user_startup_dir, False), (self.env.common_startup_dir, True)):
            for name in self.system.list_dir(folder) if folder else []:
                if _mentions_app(name) and name.lower().endswith(STARTUP_EXTENSIONS):
                    found.append(LegacyItem(STARTUP_LINK, ntpath.join(folder, name), ntpath.join(folder, name), folder, admin=admin))
        return found

    def _uninstall_entries(self):
        found = []
        for hive in HIVES:
            for name, display, location, command in self.system.uninstall_entries(hive):
                if hive == "HKCU" and _norm(location) == _norm(self.env.user_install_dir) and self._current_install():
                    continue  # the up-to-date entry of the current install
                found.append(LegacyItem(UNINSTALL_ENTRY, f"{hive}|{name}", display, command or location, admin=hive != "HKCU"))
        return found

    def _machine_shortcuts(self):
        found = []
        for folder, only_dirs in (
            (self.env.public_desktop_dir, False),
            (self.env.common_programs_dir, False),
            (self.env.user_programs_dir, True),  # user's own .lnk files are recreated by the installer
        ):
            for name in self.system.list_dir(folder) if folder else []:
                path = ntpath.join(folder, name)
                if not _mentions_app(name):
                    continue
                is_dir = self.system.is_dir(path)
                if only_dirs and not is_dir:
                    continue
                found.append(LegacyItem(PROGRAM_DIR if is_dir else SHORTCUT, path, path, folder, admin=folder != self.env.user_programs_dir))
        return found

    def _current_install(self):
        return self.env.installed_version == self.env.app_version

    def _program_folders(self):
        found = []
        user_dir = self.env.user_install_dir
        if user_dir and self.system.is_dir(user_dir) and not self._current_install() and self._has_app_exe(user_dir):
            if not _inside(self.env.own_exe, user_dir):
                found.append(LegacyItem(PROGRAM_DIR, user_dir, user_dir, "Older installed copy"))
        for root in self.env.program_roots:
            for sub in (APP_NAME, ntpath.join("MDexpLab", APP_NAME)):
                path = ntpath.join(root, sub)
                if _norm(path) == _norm(user_dir) or _inside(self.env.own_exe, path):
                    continue
                if self.system.is_dir(path) and self._has_app_exe(path):
                    found.append(LegacyItem(PROGRAM_DIR, path, path, "Older installed copy", admin=self._is_machine_wide(path)))
        return found

    def _has_app_exe(self, folder):
        return any(_mentions_app(name) and name.lower().endswith(".exe") for name in self.system.list_dir(folder))

    def _referenced_programs(self):
        """Old exe files that startup entries or tasks point at (only files that look like GhostLock)."""
        found, folders = [], {_norm(item.key) for item in self._program_folders()}
        commands = [command for _name, command in self._tasks_cached()]
        for values in self._runs_cached().values():
            commands += list(values.values())
        for command in commands:
            target = startup_command_target(command)
            stem = ntpath.basename(target).lower()
            if not (stem.endswith(".exe") and _mentions_app(stem)):
                continue
            if _norm(target) in (_norm(self.env.own_exe), _norm(self.env.expected_target)):
                continue
            if any(_inside(target, folder) for folder in folders) or not self.system.exists(target):
                continue
            found.append(LegacyItem(PROGRAM_FILE, target, target, "Referenced by a startup entry", admin=self._is_machine_wide(target)))
        return found

    def _user_data(self, items):
        folder = self.env.app_data_dir
        if folder and self.system.is_dir(folder) and any(item.kind != PROCESS for item in items):
            return LegacyItem(USER_DATA, folder, folder, "Saved passwords, recovery answer and settings")
        return None

    @staticmethod
    def _unique(items):
        seen, result = set(), []
        for item in items:
            marker = (item.kind, _norm(item.key))
            if marker not in seen:
                seen.add(marker)
                result.append(item)
        order = {kind: index for index, kind in enumerate(CLEAN_ORDER)}
        return sorted(result, key=lambda item: order.get(item.kind, 99))


# --------------------------------------------------------------------------- cleaner
class CleanupRun:
    """Removes items one step at a time. ``steps()`` returns (label, callable) pairs for a progress UI."""

    def __init__(self, scan, remove_data=False, system=None, env=None, elevate=None):
        self.scan = scan
        self.remove_data = bool(remove_data and scan.data_item)
        self.system = system or WindowsSystem()
        self.env = env or Environment.detect()
        self.elevate = elevate if elevate is not None else request_admin_cleanup
        self.is_admin = self.system.is_admin()
        self.report = CleanupReport()

    def targets(self):
        items = list(self.scan.items)
        if self.remove_data:
            items.append(self.scan.data_item)
        order = {kind: index for index, kind in enumerate(CLEAN_ORDER)}
        return sorted(items, key=lambda item: order.get(item.kind, 99))

    def steps(self):
        steps = [
            (f"Removing {KIND_TITLES[item.kind].lower()}…", lambda item=item: self._clean(item))
            for item in self.targets()
        ]
        steps.append(("Checking the result…", self._finish))
        return steps

    def run_all(self):
        for _label, action in self.steps():
            action()
        return self.report

    # -- internals
    def _clean(self, item):
        if item.admin and not self.is_admin:
            self.report.deferred.append(item)
            return
        try:
            self._remove(item)
            self.report.done.append(item)
        except PermissionError as exc:
            if self.is_admin:
                self.report.failed.append((item, str(exc)))
            else:
                self.report.deferred.append(item)
        except Exception as exc:
            log_exception(f"Could not remove {item.kind} {item.key}", exc)
            self.report.failed.append((item, str(exc)))

    def _remove(self, item):
        system = self.system
        if item.kind == PROCESS:
            system.kill(int(item.key))
        elif item.kind == TASK:
            system.delete_task(item.key)
        elif item.kind == RUN_VALUE:
            hive, name = item.key.split("|", 1)
            system.delete_run_value(hive, name)
        elif item.kind == UNINSTALL_ENTRY:
            hive, name = item.key.split("|", 1)
            system.delete_uninstall_entry(hive, name)
        elif item.kind in (STARTUP_LINK, SHORTCUT, PROGRAM_FILE, PROGRAM_DIR, USER_DATA):
            self._guard_path(item.key)
            system.remove_path(item.key)
        else:
            raise ValueError(f"Unknown item kind: {item.kind}")

    def _guard_path(self, path):
        """Never delete the running program, or any folder that contains it."""
        own = self.env.own_exe
        if _norm(path) == _norm(own) or _inside(own, path):
            raise PermissionError("This is the copy of GhostLock that is running now")
        if len(_norm(path)) <= 3 or ntpath.basename(_norm(path)) in ("", "windows", "system32", "users"):
            raise ValueError(f"Refusing to remove {path}")

    def _finish(self):
        deferred_keys = {(item.kind, _norm(item.key)) for item in self.report.deferred}
        if self.report.deferred and not self.is_admin:
            self.elevate()  # one Windows permission prompt; declining simply leaves those items in place
        before = {(item.kind, _norm(item.key)) for item in self.scan.items}
        remaining = []
        for attempt in range(3):  # a just-closed program can take a moment to disappear
            again = LegacyScanner(self.system, self.env).scan()
            remaining = [item for item in again.items if (item.kind, _norm(item.key)) in before]
            if not [item for item in remaining if (item.kind, _norm(item.key)) not in deferred_keys] or attempt == 2:
                break
            time.sleep(0.7)
        self.report.remaining = remaining
        self.report.deferred = [item for item in remaining if (item.kind, _norm(item.key)) in deferred_keys and not self.is_admin]


# --------------------------------------------------------------------------- administrator helper
class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def request_admin_cleanup(timeout_seconds=180):
    """Ask Windows (UAC) to run the cleanup helper as administrator and wait for it.

    Returns True when the helper ran, False when the person declined or it could not start.
    """
    if not IS_WINDOWS:
        return False
    try:
        keep = f"{KEEP_PIDS_ARG} {os.getpid()},{os.getppid()}"
        program = sys.executable
        if getattr(sys, "frozen", False):
            params = f"{ADMIN_ARG} {keep}"
        else:
            params = f'"{os.path.abspath(sys.argv[0])}" {ADMIN_ARG} {keep}'
        info = _ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(_ShellExecuteInfo)
        info.fMask = 0x00000040 | 0x00000100  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
        info.lpVerb = "runas"
        info.lpFile = program
        info.lpParameters = params
        info.nShow = 0  # hidden
        ole32 = ctypes.windll.ole32
        com_ready = ole32.CoInitialize(None) in (0, 1)  # we may be on a worker thread
        try:
            os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # the helper must unpack its own copy
            shell_execute = ctypes.windll.shell32.ShellExecuteExW
            shell_execute.argtypes = (ctypes.POINTER(_ShellExecuteInfo),)
            shell_execute.restype = wintypes.BOOL
            if not shell_execute(ctypes.byref(info)):
                return False  # declined or blocked
        finally:
            if com_ready:
                ole32.CoUninitialize()
        kernel32 = ctypes.windll.kernel32
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        if info.hProcess:
            kernel32.WaitForSingleObject(info.hProcess, int(timeout_seconds * 1000))
            kernel32.CloseHandle(info.hProcess)
        return True
    except Exception as exc:
        log_exception("Administrator cleanup could not start", exc)
        return False


def is_admin_cleanup_invocation():
    return ADMIN_ARG in {arg.lower() for arg in sys.argv[1:]}


def admin_keep_pids():
    """Process ids the helper must never close (the installer that launched it). Only ever spares, never targets."""
    args = [arg.lower() for arg in sys.argv[1:]]
    if KEEP_PIDS_ARG in args and args.index(KEEP_PIDS_ARG) + 1 < len(args):
        return tuple(int(p) for p in args[args.index(KEEP_PIDS_ARG) + 1].split(",") if p.strip().isdigit())
    return ()


def run_admin_cleanup():
    """Entry point of the elevated helper. It trusts nothing from the caller: it scans on its own."""
    try:
        system, env = WindowsSystem(), Environment.detect(extra_pids=admin_keep_pids())
        scan = LegacyScanner(system, env).scan()
        run = CleanupRun(scan, remove_data=False, system=system, env=env, elevate=lambda: False)
        report = run.run_all()
        return 0 if report.ok else 1
    except Exception as exc:
        log_exception("Administrator cleanup failed", exc)
        return 2


def scan_legacy(fast=False):
    """Convenience wrapper used by the app."""
    return LegacyScanner().scan(fast=fast)
