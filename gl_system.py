"""System helpers: global hotkey, idle detection / auto-lock, and a hang watchdog (no GUI).

Each piece takes its Windows access through a small backend object so the logic can be tested anywhere.
"""
import ctypes
import os
import queue
import threading
import time
from ctypes import wintypes

from gl_core import IS_WINDOWS, log_exception

MODIFIERS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_ID = 0x4C4B  # arbitrary, only needs to be unique inside this thread


# --------------------------------------------------------------------------- hotkey
def parse_hotkey(text):
    """'Ctrl+Alt+L' -> (modifier flags, virtual-key code). Raises ValueError for anything unusable."""
    parts = [part.strip().lower() for part in str(text).split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError("A hotkey needs at least one modifier and one key")
    *mods, key = parts
    flags = 0
    for mod in mods:
        if mod not in MODIFIERS:
            raise ValueError(f"Unknown modifier: {mod}")
        flags |= MODIFIERS[mod]
    if len(key) != 1 or not key.isalnum():
        raise ValueError(f"Unsupported key: {key}")
    return flags, ord(key.upper())  # virtual-key codes for A-Z and 0-9 equal their ASCII codes


class _WindowsHotkeyBackend:
    """Registers the hotkey on a dedicated thread and pumps its messages until stopped."""

    def __init__(self):
        self.thread_id = None

    def run(self, flags, vk, ready, pressed):
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()
        ok = bool(user32.RegisterHotKey(None, HOTKEY_ID, flags | MOD_NOREPEAT, vk))
        ready(ok)
        if not ok:
            return
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    pressed()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


class HotkeyListener:
    """``poll()`` returns True once per key press; call it from the UI thread on a timer."""

    def __init__(self, backend_factory=None):
        self._factory = backend_factory or _WindowsHotkeyBackend
        self._presses = queue.Queue()
        self._backend = None
        self._thread = None
        self.combo = None
        self.error = ""

    def start(self, combo):
        self.stop()
        try:
            flags, vk = parse_hotkey(combo)
        except ValueError as exc:
            self.error = str(exc)
            return False
        ready_event, result = threading.Event(), {}
        backend = self._factory()

        def ready(ok):
            result["ok"] = ok
            ready_event.set()

        def target():
            try:
                backend.run(flags, vk, ready, lambda: self._presses.put(1))
            except Exception as exc:
                log_exception("Hotkey thread failed", exc)
                ready(False)

        self._backend = backend
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        ready_event.wait(2.0)
        if result.get("ok"):
            self.combo, self.error = combo, ""
            return True
        self.error = f"{combo} is already used by another program."
        self.stop()
        return False

    def poll(self):
        pressed = False
        try:
            while True:
                self._presses.get_nowait()
                pressed = True
        except queue.Empty:
            return pressed

    def stop(self):
        backend, thread, self._backend, self._thread, self.combo = self._backend, self._thread, None, None, None
        if backend:
            try:
                backend.stop()
            except Exception:
                pass
        if thread:
            thread.join(1.0)


# --------------------------------------------------------------------------- idle auto-lock
class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def seconds_since_input():
    """Seconds since the last mouse or keyboard input anywhere in this Windows session."""
    if not IS_WINDOWS:
        return 0.0
    try:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(_LastInputInfo)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0  # masks handle the 49-day tick wrap
    except Exception:
        return 0.0


def fullscreen_app_running():
    """True while a full-screen app (video, game) or Windows presentation mode is active.

    Watching a film for twenty minutes has no mouse or keyboard input, but nobody wants the screen locked then.
    """
    if not IS_WINDOWS:
        return False
    try:
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
        return state.value in (2, 3, 4)  # QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE
    except Exception:
        return False


class AutoLock:
    COOLDOWN = 30.0  # never fire twice within this many seconds, even if the idle clock misbehaves

    def __init__(self, get_minutes, is_locked, lock, idle_seconds=seconds_since_input, clock=time.monotonic, busy=fullscreen_app_running):
        self.get_minutes, self.is_locked, self.lock = get_minutes, is_locked, lock
        self.busy = busy
        self.idle_seconds, self.clock = idle_seconds, clock
        self._last_fire = -1e9

    def check(self):
        """Call every few seconds. Returns True if it locked the screen."""
        minutes = self.get_minutes()
        if minutes <= 0 or self.is_locked():
            return False
        if self.busy():
            return False
        if self.clock() - self._last_fire < self.COOLDOWN:
            return False
        if self.idle_seconds() >= minutes * 60:
            self._last_fire = self.clock()
            self.lock()
            return True
        return False


# --------------------------------------------------------------------------- wake from sleep
class WakeDetector:
    """Notices that the PC slept: our timer stops while it sleeps, but the wall clock keeps going."""

    def __init__(self, gap=20.0, clock=time.time):
        self.gap, self.clock = gap, clock
        self._last = clock()

    def poll(self):
        """Call about once a second. True once after a gap longer than ``gap`` seconds."""
        now = self.clock()
        woke = now - self._last > self.gap
        self._last = now
        return woke


# --------------------------------------------------------------------------- watchdog
class Watchdog:
    """If the UI thread stops beating while armed (the lock screen is up), run ``on_stall``.

    The default releases the lock by ending the process: a frozen lock screen that nobody can type into is
    worse than no lock. GhostLock is a soft privacy screen, so failing open is the safe choice here.
    """

    def __init__(self, limit=90.0, on_stall=None, clock=time.monotonic, interval=1.0):
        self.limit, self.clock, self.interval = limit, clock, interval
        self.on_stall = on_stall or self._exit
        self._armed = False
        self._last = clock()
        self._last_step = clock()
        self._stop = threading.Event()
        self._thread = None

    @staticmethod
    def _exit():
        log_exception("UI stopped responding while locked; releasing the lock", RuntimeError("watchdog"))
        os._exit(3)

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def beat(self):
        self._last = self.clock()

    def arm(self):
        self.beat()
        self._armed = True

    def disarm(self):
        self._armed = False

    def check(self):
        if self._armed and self.clock() - self._last > self.limit:
            self._armed = False
            self.on_stall()
            return True
        return False

    def step(self):
        """One watchdog round. If this thread itself was suspended (the PC slept), that is not a UI stall."""
        now = self.clock()
        if now - self._last_step > self.interval * 10:
            self.beat()
        self._last_step = now
        return self.check()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.step()
            except Exception as exc:
                log_exception("Watchdog error", exc)

    def stop(self):
        self._stop.set()
