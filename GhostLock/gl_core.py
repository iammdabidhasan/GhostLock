"""GhostLock core: constants, paths, hashing, configuration and Windows integration.

This module has no GUI code so it can be unit-tested anywhere.
"""
import base64
import ctypes
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import time
import traceback
from ctypes import wintypes
from pathlib import Path

APP_NAME = "GhostLock"
APP_VERSION = "2.1.1"
APP_PUBLISHER = "MDexpLab"
APP_AUTHOR = "MD ABID HASAN"
APP_COPYRIGHT = "Copyright (c) 2026 MD ABID HASAN / MDexpLab. Open-source software."
CONFIG_VERSION = 3
PBKDF2_ITERATIONS = 260_000
MIN_PASSWORD_LENGTH = 6
DEFAULT_LOCK_OPACITY = 0.60
MIN_LOCK_OPACITY = 0.30
MAX_LOCK_OPACITY = 0.90
DEFAULT_IDLE_HIDE_SECONDS = 15
MIN_IDLE_HIDE_SECONDS = 5
MAX_IDLE_HIDE_SECONDS = 60
INSTALL_DIR_NAME = APP_NAME
INSTALLED_EXE_NAME = f"{APP_NAME}.exe"
STARTUP_SHORTCUT_NAME = f"{APP_NAME}.lnk"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY_PATH = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
SINGLE_INSTANCE_MUTEX_NAME = "Local\\GhostLockSingleInstance"
ERROR_ALREADY_EXISTS = 183
_SINGLE_INSTANCE_MUTEX = None

IS_WINDOWS = platform.system() == "Windows"


# --------------------------------------------------------------------------- paths
def app_data_dir():
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def install_dir():
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / INSTALL_DIR_NAME
    return Path.home() / "AppData" / "Local" / INSTALL_DIR_NAME


def _roaming():
    base = os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


def desktop_dir():
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def start_menu_dir():
    return _roaming() / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def startup_dir():
    return start_menu_dir() / "Startup"


def startup_shortcut_path():
    return startup_dir() / STARTUP_SHORTCUT_NAME


def current_executable_path():
    source = sys.executable if getattr(sys, "frozen", False) else __file__
    return Path(source).resolve()


def resource_path(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def asset_path(name):
    return resource_path("assets", name)


def installed_exe_path():
    return install_dir() / INSTALLED_EXE_NAME


def installed_app_version():
    """Version recorded by the installer for the installed copy, or None if unknown."""
    try:
        info = json.loads((install_dir() / "install_info.json").read_text(encoding="utf-8"))
        return str(info.get("app_version") or "") or None
    except Exception:
        return None


def app_launcher_path():
    """The exe that Windows startup should launch.

    An installed copy is only trusted when it is the one running or when it is the same version.
    A stale install from an older release must never win over the build the user is running now.
    """
    installed = installed_exe_path()
    current = current_executable_path()
    if installed.exists() and (_same_path(installed, current) or installed_app_version() == APP_VERSION):
        return installed
    return current


def _same_path(a, b):
    def norm(value):
        return str(value).replace("/", "\\").rstrip("\\").lower()

    return norm(a) == norm(b)


# --------------------------------------------------------------------------- logging
def log_exception(context, exc):
    try:
        path = app_data_dir() / "ghostlock.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n")
            handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        return path
    except Exception:
        return None


# --------------------------------------------------------------------------- hashing
def b64encode(raw):
    return base64.b64encode(raw).decode("ascii")


def b64decode(value):
    return base64.b64decode(value.encode("ascii"))


def hash_secret(secret, salt=None, iterations=PBKDF2_ITERATIONS):
    if not isinstance(secret, str) or not secret:
        raise ValueError("Secret must be a non-empty string.")
    raw_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), raw_salt, iterations)
    return {
        "algorithm": "pbkdf2_sha256",
        "iterations": iterations,
        "salt": b64encode(raw_salt),
        "hash": b64encode(digest),
    }


def verify_secret(secret, record):
    if not secret or not isinstance(record, dict):
        return False
    try:
        iterations = int(record.get("iterations", PBKDF2_ITERATIONS))
        # Refuse absurd values from a tampered config (would hang the UI).
        if not 1_000 <= iterations <= 5_000_000:
            return False
        salt = b64decode(record["salt"])
        expected = b64decode(record["hash"])
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def password_strength(password):
    """Return (score 0-4, label) for a simple, dependency-free strength hint."""
    if not password:
        return 0, ""
    classes = sum(
        (
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        )
    )
    length = len(password)
    score = 0
    if length >= MIN_PASSWORD_LENGTH:
        score = 1
    if length >= 8 and classes >= 2:
        score = 2
    if length >= 10 and classes >= 3:
        score = 3
    if length >= 14 and classes >= 3:
        score = 4
    if len(set(password)) <= 2:
        score = min(score, 1)
    if length < MIN_PASSWORD_LENGTH:
        return 0, "Too short"
    return score, ("Weak", "Fair", "Good", "Strong")[max(0, score - 1)]


# --------------------------------------------------------------------------- brute-force guard
class AttemptLimiter:
    """Escalating cool-down after repeated wrong passwords or recovery answers."""

    FREE_ATTEMPTS = 5
    BASE_DELAY = 15
    MAX_DELAY = 300

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.failures = 0
        self._locked_until = 0.0

    def remaining(self):
        return max(0, int(self._locked_until - self._clock() + 0.999))

    @property
    def blocked(self):
        return self.remaining() > 0

    def record_failure(self):
        self.failures += 1
        over = self.failures - self.FREE_ATTEMPTS
        if over >= 0:
            delay = min(self.MAX_DELAY, self.BASE_DELAY * (2 ** over))
            self._locked_until = self._clock() + delay
        return self.remaining()

    def reset(self):
        self.failures = 0
        self._locked_until = 0.0


# --------------------------------------------------------------------------- configuration
class ConfigManager:
    def __init__(self):
        self.path = app_data_dir() / "config.json"
        self.data = {}
        self.load()

    def load(self):
        if not self.path.exists():
            self.data = {}
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("config root must be an object")
            self.data = loaded
            self._apply_defaults()
        except Exception as exc:
            self.data = {}
            self._quarantine_corrupt_file(exc)

    def _quarantine_corrupt_file(self, exc):
        """Keep an unreadable config instead of silently overwriting it later."""
        try:
            backup = self.path.with_name(f"config.corrupt-{int(time.time())}.json")
            os.replace(self.path, backup)
            log_exception(f"config.json unreadable; moved to {backup.name}", exc)
        except Exception:
            pass

    def _apply_defaults(self):
        if not self.data:
            return
        self.data.setdefault("version", CONFIG_VERSION)
        self.data.setdefault("lock_opacity", DEFAULT_LOCK_OPACITY)
        try:
            opacity = float(self.data["lock_opacity"])
        except (TypeError, ValueError):
            opacity = DEFAULT_LOCK_OPACITY
        self.data["lock_opacity"] = max(MIN_LOCK_OPACITY, min(MAX_LOCK_OPACITY, opacity))
        self.data.setdefault("first_lock_tested", False)
        self.data.setdefault("start_with_windows", is_start_with_windows())
        self.data.setdefault("double_click_lock", True)
        try:
            idle = int(self.data.get("idle_hide_seconds", DEFAULT_IDLE_HIDE_SECONDS))
        except (TypeError, ValueError):
            idle = DEFAULT_IDLE_HIDE_SECONDS
        self.data["idle_hide_seconds"] = max(MIN_IDLE_HIDE_SECONDS, min(MAX_IDLE_HIDE_SECONDS, idle))
        if not isinstance(self.data.get("widget"), dict):
            self.data["widget"] = {}

    @property
    def is_configured(self):
        return all(
            key in self.data
            for key in ("password", "emergency_password", "security_question", "security_answer")
        )

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = int(time.time())
        temp_path = self.path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(temp_path, self.path)

    def configure(self, password, emergency_password, question, answer):
        now = int(time.time())
        previous_widget = self.data.get("widget") if isinstance(self.data.get("widget"), dict) else {}
        previous_opacity = self.data.get("lock_opacity", DEFAULT_LOCK_OPACITY)
        previous_idle = self.data.get("idle_hide_seconds", DEFAULT_IDLE_HIDE_SECONDS)
        previous_double = self.data.get("double_click_lock", True)
        self.data = {
            "version": CONFIG_VERSION,
            "created_at": now,
            "updated_at": now,
            "password": hash_secret(password),
            "emergency_password": hash_secret(emergency_password),
            "security_question": question.strip(),
            "security_answer": hash_secret(answer.strip().lower()),
            "lock_opacity": previous_opacity,
            "idle_hide_seconds": previous_idle,
            "double_click_lock": previous_double,
            "first_lock_tested": False,
            "start_with_windows": is_start_with_windows(),
            "widget": previous_widget,
        }
        self.save()

    def verify_password(self, value):
        return verify_secret(value, self.data.get("password"))

    def verify_emergency_password(self, value):
        return verify_secret(value, self.data.get("emergency_password"))

    def verify_any_unlock_secret(self, value):
        return self.verify_password(value) or self.verify_emergency_password(value)

    def verify_security_answer(self, value):
        return verify_secret(value.strip().lower(), self.data.get("security_answer"))

    def set_password(self, password):
        self.data["password"] = hash_secret(password)
        self.save()

    def set_emergency_password(self, password):
        self.data["emergency_password"] = hash_secret(password)
        self.save()

    def set_security_question(self, question, answer):
        self.data["security_question"] = question.strip()
        self.data["security_answer"] = hash_secret(answer.strip().lower())
        self.save()

    def set_lock_opacity(self, opacity):
        self.data["lock_opacity"] = max(MIN_LOCK_OPACITY, min(MAX_LOCK_OPACITY, float(opacity)))
        self.save()

    def set_idle_hide_seconds(self, seconds):
        self.data["idle_hide_seconds"] = max(MIN_IDLE_HIDE_SECONDS, min(MAX_IDLE_HIDE_SECONDS, int(round(seconds))))
        self.save()

    def set_double_click_lock(self, enabled):
        self.data["double_click_lock"] = bool(enabled)
        self.save()

    def mark_first_lock_tested(self):
        self.data["first_lock_tested"] = True
        self.save()

    def set_start_with_windows(self, enabled):
        self.data["start_with_windows"] = bool(enabled)
        self.save()

    def set_widget_position(self, x, y):
        self.data.setdefault("widget", {})
        self.data["widget"]["x"] = int(x)
        self.data["widget"]["y"] = int(y)
        self.save()

    def update_credentials(self, password=None, emergency=None, question=None, answer=None):
        """Apply several credential changes with a single, all-or-nothing save."""
        previous = json.loads(json.dumps(self.data))
        try:
            if password:
                self.data["password"] = hash_secret(password)
            if emergency:
                self.data["emergency_password"] = hash_secret(emergency)
            if question is not None and answer:
                self.data["security_question"] = question.strip()
                self.data["security_answer"] = hash_secret(answer.strip().lower())
            self.save()
        except Exception:
            self.data = previous
            raise


# --------------------------------------------------------------------------- invocation modes
def _args():
    return {arg.lower() for arg in sys.argv[1:]}


def is_setup_invocation():
    args = _args()
    if "--uninstall" in args or "--run" in args or "--setup" in args:
        return False
    if "--install" in args:
        return True
    executable = str(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
    filename = executable.replace("\\", "/").rsplit("/", 1)[-1]  # works for Windows paths on any OS
    stem = filename.rsplit(".", 1)[0].lower().replace("_", "").replace("-", "")
    return bool(getattr(sys, "frozen", False)) and stem in {"setup", "ghostlocksetup"}


def is_uninstall_invocation():
    return "--uninstall" in _args()


def is_forced_setup_invocation():
    return "--setup" in _args()


MODE_FLAGS = {"--run", "--setup", "--install", "--uninstall", "--legacy-admin-clean"}


def is_stray_copy():
    """A packaged exe started plainly from somewhere other than the install folder.

    That is what happens when someone builds or downloads a new exe and double-clicks it while an
    older GhostLock is still installed.
    """
    if not getattr(sys, "frozen", False) or _args() & MODE_FLAGS:
        return False
    return not _same_path(current_executable_path(), installed_exe_path())


# --------------------------------------------------------------------------- Windows integration
def _creation_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(link_path, target_path, description=APP_NAME, arguments=""):
    if not IS_WINDOWS:
        return False
    try:
        link_path = Path(link_path)
        target_path = Path(target_path)
        link_path.parent.mkdir(parents=True, exist_ok=True)
        script = (
            "$shell = New-Object -ComObject WScript.Shell; "
            f"$shortcut = $shell.CreateShortcut({ps_quote(link_path)}); "
            f"$shortcut.TargetPath = {ps_quote(target_path)}; "
            f"$shortcut.WorkingDirectory = {ps_quote(target_path.parent)}; "
            f"$shortcut.IconLocation = {ps_quote(target_path)}; "
            f"$shortcut.Description = {ps_quote(description)}; "
            f"$shortcut.Arguments = {ps_quote(arguments)}; "
            "$shortcut.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
            timeout=30,
        )
        return True
    except Exception:
        return False


def _winreg():
    if not IS_WINDOWS:
        return None
    try:
        import winreg

        return winreg
    except ImportError:
        return None


def is_start_with_windows():
    winreg = _winreg()
    if winreg:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
                winreg.QueryValueEx(key, APP_NAME)
                return True
        except OSError:
            pass
    return startup_shortcut_path().exists()


def set_start_with_windows(enabled, target_path=None):
    """Enable/disable launching with Windows using the per-user Run registry key."""
    winreg = _winreg()
    legacy = startup_shortcut_path()
    if not winreg:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            if enabled:
                target = Path(target_path or app_launcher_path())
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{target}" --run')
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        try:  # migrate away from the 2.0 startup shortcut
            if legacy.exists():
                legacy.unlink()
        except Exception:
            pass
        return True
    except Exception:
        return False


def startup_command_target(command):
    """Extract the program path from a Run-key command line."""
    text = os.path.expandvars(str(command or "").strip())
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 0 else text[1:]
    match = re.match(r"(.+?\.(?:exe|bat|cmd|lnk|py|pyw))(?:\s|$)", text, re.IGNORECASE)
    return match.group(1) if match else (text.split(" ")[0] if text else "")


def repair_startup_entry():
    """If Start-with-Windows points at some other GhostLock, point it at the running installed copy."""
    winreg = _winreg()
    if not winreg or not getattr(sys, "frozen", False):
        return False
    current = current_executable_path()
    if not _same_path(current, installed_exe_path()):
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            command, _kind = winreg.QueryValueEx(key, APP_NAME)
    except OSError:
        return False  # startup is off (or unreadable): nothing to repair
    if _same_path(startup_command_target(command), current):
        return False
    return set_start_with_windows(True, current)


def register_uninstall_entry(target_path):
    """Show GhostLock in Windows Settings > Apps (per-user, no admin needed)."""
    winreg = _winreg()
    if not winreg:
        return False
    try:
        target = Path(target_path)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY_PATH) as key:
            values = {
                "DisplayName": APP_NAME,
                "DisplayVersion": APP_VERSION,
                "Publisher": APP_PUBLISHER,
                "DisplayIcon": str(target),
                "InstallLocation": str(target.parent),
                "UninstallString": f'"{target}" --uninstall',
            }
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
            try:
                size_kb = max(1, target.stat().st_size // 1024)
                winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))
            except OSError:
                pass
        return True
    except Exception:
        return False


def unregister_uninstall_entry():
    winreg = _winreg()
    if not winreg:
        return False
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY_PATH)
        return True
    except OSError:
        return False


def remove_known_shortcuts():
    for path in (
        desktop_dir() / f"{APP_NAME}.lnk",
        start_menu_dir() / f"{APP_NAME}.lnk",
        start_menu_dir() / f"{APP_NAME} Uninstall.lnk",
        startup_shortcut_path(),
    ):
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _mutex_api():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.GetLastError.argtypes = ()
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def acquire_single_instance():
    global _SINGLE_INSTANCE_MUTEX
    if not IS_WINDOWS:
        return True
    try:
        kernel32 = _mutex_api()
        mutex = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
        already_running = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        _SINGLE_INSTANCE_MUTEX = mutex
        return not already_running
    except Exception:
        return True


def is_another_instance_running():
    if not IS_WINDOWS:
        return False
    try:
        kernel32 = _mutex_api()
        mutex = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
        already_running = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        if mutex:
            kernel32.CloseHandle(mutex)
        return already_running
    except Exception:
        return False


def launch_uninstaller():
    """Start the uninstaller as a separate process (the caller should exit right after)."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--uninstall"]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parent / "ghostlock.py"), "--uninstall"]
    try:
        subprocess.Popen(command, creationflags=0x00000008 | 0x00000200 if IS_WINDOWS else 0)  # DETACHED | NEW_GROUP
        return True
    except Exception as exc:
        log_exception("Could not start uninstaller", exc)
        return False


def schedule_install_dir_removal():
    """Remove the install folder shortly after this process exits (retries while locked)."""
    target = install_dir().resolve()
    if target.name != INSTALL_DIR_NAME or not IS_WINDOWS:
        return False
    script = (
        "for ($i = 0; $i -lt 10; $i++) { "
        "Start-Sleep -Seconds 2; "
        f"Remove-Item -LiteralPath {ps_quote(target)} -Recurse -Force -ErrorAction SilentlyContinue; "
        f"if (-not (Test-Path -LiteralPath {ps_quote(target)})) {{ break }} }}"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
        return True
    except Exception:
        return False
