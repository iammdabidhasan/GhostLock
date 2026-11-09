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
APP_VERSION = "2.2.0"
APP_PUBLISHER = "MDexpLab"
APP_AUTHOR = "MD ABID HASAN"
APP_COPYRIGHT = "Copyright (c) 2026 MD ABID HASAN / MDexpLab. Open-source software."
CONFIG_VERSION = 3
APP_REPO = "MDexpLab/GhostLock"  # GitHub "owner/name" used by the optional update check
HOTKEY_PRESETS = ("Ctrl+Alt+L", "Ctrl+Shift+L", "Ctrl+Alt+G", "Win+Shift+L")
DEFAULT_HOTKEY = HOTKEY_PRESETS[0]
MAX_AUTO_LOCK_MINUTES = 120
MAX_LOCK_MESSAGE_LENGTH = 32
INTRUDER_AFTER_CHOICES = (1, 2, 3)
DEFAULT_INTRUDER_AFTER = 2
DEFAULT_KEEP_DAYS = 30
MAX_PASSWORD_LENGTH = 128
PBKDF2_ITERATIONS = 600_000  # new hashes; older ones keep the count stored beside them
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


def intruders_dir():
    return app_data_dir() / "intruders"


def attempts_log_path():
    return app_data_dir() / "attempts.json"


def install_dir():
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / INSTALL_DIR_NAME
    return Path.home() / "AppData" / "Local" / INSTALL_DIR_NAME


def _roaming():
    base = os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"


def _known_folder(folder_id):
    """The folder Windows really uses (follows OneDrive-redirected Desktop and moved profiles), or None."""
    if not IS_WINDOWS:
        return None
    try:
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

        raw = uuid.UUID(folder_id)
        guid = GUID(raw.time_low, raw.time_mid, raw.time_hi_version, (ctypes.c_ubyte * 8)(*raw.bytes[8:]))
        pointer = ctypes.c_wchar_p()
        shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
        shell32.SHGetKnownFolderPath.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p))
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pointer)) != 0:
            return None
        try:
            return Path(pointer.value)
        finally:
            ole32.CoTaskMemFree(pointer)
    except Exception:
        return None


def desktop_dir(fallback=False):
    plain = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    return plain if fallback else (_known_folder(FOLDERID_DESKTOP) or plain)


def start_menu_dir():
    return _known_folder(FOLDERID_PROGRAMS) or (_roaming() / "Microsoft" / "Windows" / "Start Menu" / "Programs")


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
MAX_LOG_BYTES = 512 * 1024


def log_exception(context, exc):
    try:
        path = app_data_dir() / "ghostlock.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > MAX_LOG_BYTES:  # keep one previous log, never grow without limit
                os.replace(path, path.with_suffix(".log.1"))
        except OSError:
            pass
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
def _clamp_int(low, high):
    def rule(value):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError("not a finite number")
        return max(low, min(high, int(round(number))))

    return rule


def _flag(value):
    """A real on/off value. bool("false") would be True, so only accept proper booleans, 0/1 and the words."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no", "on", "off", "1", "0"):
        return value.strip().lower() in ("true", "yes", "on", "1")
    raise ValueError("not a yes/no value")


def password_problem(value, minimum=None):
    """Why a password cannot be used, or "" if it is fine. The lock screen can only type printable text up to 128."""
    minimum = MIN_PASSWORD_LENGTH if minimum is None else minimum
    if len(value) < minimum:
        return f"Use at least {minimum} characters."
    if len(value) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    if not value.isprintable():
        return "Use only normal letters, digits and symbols (no tabs or line breaks)."
    return ""


def _choice(options, default):
    def rule(value):
        return value if value in options else default

    return rule


def _text(limit):
    def rule(value):
        return " ".join(str(value or "").split())[:limit]

    return rule


# name -> (default, normaliser). One table drives defaults, validation and update_settings().
SHIELD_PATTERNS = ("ripple", "sweep", "scan", "random")
SHIELD_TONES = ("teal", "ice", "amber")
SHIELD_SECONDS = (1, 2, 3, 5)

SETTING_RULES = {
    "lock_opacity": (DEFAULT_LOCK_OPACITY, lambda v: max(MIN_LOCK_OPACITY, min(MAX_LOCK_OPACITY, float(v)))),
    "idle_hide_seconds": (DEFAULT_IDLE_HIDE_SECONDS, _clamp_int(MIN_IDLE_HIDE_SECONDS, MAX_IDLE_HIDE_SECONDS)),
    "double_click_lock": (True, _flag),
    "hotkey_enabled": (True, _flag),
    "hotkey": (DEFAULT_HOTKEY, _choice(HOTKEY_PRESETS, DEFAULT_HOTKEY)),
    "auto_lock_minutes": (0, _clamp_int(0, MAX_AUTO_LOCK_MINUTES)),
    "lock_on_wake": (False, _flag),
    "lock_message": ("", _text(MAX_LOCK_MESSAGE_LENGTH)),
    "intruder_camera": (False, _flag),
    "intruder_after": (DEFAULT_INTRUDER_AFTER, _choice(INTRUDER_AFTER_CHOICES, DEFAULT_INTRUDER_AFTER)),
    "intruder_keep_days": (DEFAULT_KEEP_DAYS, _clamp_int(1, 365)),
    "show_away_report": (True, _flag),
    "shield_enabled": (True, _flag),
    "shield_pattern": ("ripple", _choice(SHIELD_PATTERNS, "ripple")),
    "shield_tone": ("teal", _choice(SHIELD_TONES, "teal")),
    "shield_seconds": (2, _choice(SHIELD_SECONDS, 2)),
    "check_updates": (False, _flag),
    "last_update_check": (0, _clamp_int(0, 2**40)),
}


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
        for name, (default, rule) in SETTING_RULES.items():
            try:
                self.data[name] = rule(self.data.get(name, default))
            except (TypeError, ValueError, OverflowError):
                self.data[name] = default
        self.data.setdefault("first_lock_tested", False)
        self.data.setdefault("start_with_windows", is_start_with_windows())
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
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.data, indent=2))
            handle.flush()
            try:
                os.fsync(handle.fileno())  # a power cut must not leave an empty config.json
            except OSError:
                pass
        os.replace(temp_path, self.path)

    def configure(self, password, emergency_password, question, answer):
        now = int(time.time())
        previous_widget = self.data.get("widget") if isinstance(self.data.get("widget"), dict) else {}
        previous = {name: self.data.get(name, default) for name, (default, _rule) in SETTING_RULES.items()}
        self.data = {
            "version": CONFIG_VERSION,
            "created_at": now,
            "updated_at": now,
            "password": hash_secret(password),
            "emergency_password": hash_secret(emergency_password),
            "security_question": question.strip(),
            "security_answer": hash_secret(answer.strip().lower()),
            **previous,
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

    def update_settings(self, **values):
        """Validate and apply several preferences with one all-or-nothing save."""
        unknown = [name for name in values if name not in SETTING_RULES]
        if unknown:
            raise ValueError(f"Unknown setting: {', '.join(unknown)}")
        previous = json.loads(json.dumps(self.data))
        try:
            for name, value in values.items():
                self.data[name] = SETTING_RULES[name][1](value)
            self.save()
        except Exception:
            self.data = previous
            raise

    def setting(self, name):
        return self.data.get(name, SETTING_RULES[name][0])

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
    stem = re.sub(r"\(\d+\)|\d+(\.\d+)+|v\d+", "", stem).strip()  # "Setup (1)", "GhostLock-Setup-2.2.0"
    return bool(getattr(sys, "frozen", False)) and stem.endswith(("setup", "installer")) and stem != "ghostlock"


def is_uninstall_invocation():
    return "--uninstall" in _args()


def is_forced_setup_invocation():
    return "--setup" in _args()


MODE_FLAGS = {"--run", "--setup", "--install", "--uninstall", "--legacy-admin-clean", "--selftest"}


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


def _powershell_path():
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(exe) if exe.exists() else "powershell"


def ps_quote(value):
    """Single-quoted PowerShell string. PowerShell also treats the typographic quotes as quote characters."""
    text = str(value)
    for quote in ("'", "\u2018", "\u2019", "\u201a", "\u201b"):
        text = text.replace(quote, quote * 2)
    return "'" + text + "'"


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
            [_powershell_path(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
            timeout=30,
        )
        return link_path.exists()
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
        desktop_dir(fallback=True) / f"{APP_NAME}.lnk",
        start_menu_dir() / f"{APP_NAME}.lnk",
        start_menu_dir() / f"{APP_NAME} Uninstall.lnk",
        startup_shortcut_path(),
    ):
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


SHOW_REQUEST_NAME = "show-request"


def request_running_instance():
    """Called by a second launch: ask the GhostLock that is already running to show its ghost and menu."""
    try:
        path = app_data_dir() / SHOW_REQUEST_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
        return True
    except OSError:
        return False


def consume_show_request(max_age=30.0, clear_only=False):
    """True once if another launch asked this instance to show itself (stale requests are dropped)."""
    path = app_data_dir() / SHOW_REQUEST_NAME
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return age <= max_age and not clear_only


def reopen_hint():
    """One sentence telling the person how to start GhostLock again after they exit it."""
    try:
        if (start_menu_dir() / f"{APP_NAME}.lnk").exists():
            return f"To start it again, open the Start menu and type {APP_NAME}"+(" (or use the shortcut on your desktop)." if (desktop_dir() / f"{APP_NAME}.lnk").exists() else ".")
        exe = current_executable_path()
        return f"To start it again, run {exe.name} from {exe.parent}."
    except Exception:
        return "To start it again, run GhostLock.exe."


def _mutex_api():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # so get_last_error() is not clobbered by other calls
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
        already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
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
        already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if mutex:
            kernel32.CloseHandle(mutex)
        return already_running
    except Exception:
        return False


def fresh_process_env():
    """Environment for starting another copy of this exe.

    A one-file PyInstaller exe unpacks itself into a temp folder and deletes it on exit. A child that inherits
    the parent's PyInstaller variables tries to use that folder and crashes once the parent is gone
    ("Failed to execute script pyi_rth_inspect ... _MEI..."). Telling the bootloader to reset gives the child
    its own folder.
    """
    env = dict(os.environ)
    for name in list(env):
        if name == "_MEIPASS2" or name.startswith("_PYI"):
            env.pop(name, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def launch_uninstaller():
    """Start the uninstaller as a separate process (the caller should exit right after)."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--uninstall"]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parent / "ghostlock.py"), "--uninstall"]
    try:
        subprocess.Popen(command, env=fresh_process_env(), creationflags=0x00000008 | 0x00000200 if IS_WINDOWS else 0)  # DETACHED | NEW_GROUP
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
            [_powershell_path(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
        return True
    except Exception as exc:
        log_exception("Could not schedule removal of the program folder", exc)
        return False
