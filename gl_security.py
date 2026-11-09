"""Attempt log and intruder photos (no GUI).

* ``AttemptLog``       - small JSON file with the most recent failed unlock attempts.
* ``SecurityMonitor``  - one lock session: records each failed attempt and, when the owner has turned the
                         feature on, asks the camera for a photo. Photos stay on this PC (see README).
* ``prune_photos``     - deletes old photos so the folder never grows without limit.
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path

from gl_core import attempts_log_path, intruders_dir, log_exception

MAX_LOG_ENTRIES = 200
MAX_PHOTOS_PER_SESSION = 5
MAX_PHOTO_FILES = 60
PHOTO_PREFIX = "intruder-"


class AttemptLog:
    _shared = None
    _shared_path = None

    @classmethod
    def shared(cls):
        """One log object per process, so photo threads of an earlier lock never overwrite a later lock's entries."""
        path = attempts_log_path()
        if cls._shared is None or cls._shared_path != path:
            cls._shared, cls._shared_path = cls(path), path
        return cls._shared

    def __init__(self, path=None):
        self.path = Path(path) if path else attempts_log_path()
        self._lock = threading.Lock()
        self.entries = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("entries") if isinstance(data, dict) else None
            return [e for e in (items or []) if isinstance(e, dict) and "t" in e and "id" in e][-MAX_LOG_ENTRIES:]
        except FileNotFoundError:
            return []
        except Exception as exc:  # unreadable log: start empty rather than blocking the lock screen
            log_exception("attempts.json unreadable", exc)
            return []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"entries": self.entries[-MAX_LOG_ENTRIES:]}, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    def record(self, kind, when=None):
        with self._lock:
            entry = {"id": uuid.uuid4().hex[:10], "t": float(when if when is not None else time.time()), "kind": kind, "photo": None}
            self.entries.append(entry)
            self.entries = self.entries[-MAX_LOG_ENTRIES:]
            self._save()
            return entry

    def attach_photo(self, entry_id, filename):
        with self._lock:
            for entry in self.entries:
                if entry["id"] == entry_id:
                    entry["photo"] = filename
            self._save()

    def since(self, timestamp):
        with self._lock:
            return [dict(e) for e in self.entries if e["t"] >= timestamp]

    def clear(self):
        with self._lock:
            self.entries = []
            self._save()


def photo_files(folder=None):
    folder = Path(folder) if folder else intruders_dir()
    try:
        return sorted((p for p in folder.iterdir() if p.is_file() and p.name.startswith(PHOTO_PREFIX)), key=lambda p: p.stat().st_mtime)
    except OSError:
        return []


def prune_photos(keep_days, folder=None, max_files=MAX_PHOTO_FILES, now=None):
    """Remove photos older than ``keep_days`` and any beyond the newest ``max_files``. Returns how many went."""
    now = now if now is not None else time.time()
    files = photo_files(folder)
    doomed = [p for p in files if now - p.stat().st_mtime > keep_days * 86400]
    survivors = [p for p in files if p not in doomed]
    doomed += survivors[: max(0, len(survivors) - max_files)]
    removed = 0
    for path in doomed:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def delete_all_photos(folder=None):
    removed = 0
    for path in photo_files(folder):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


class SecurityMonitor:
    """Tracks one lock session. ``config`` supplies the owner's choices at the moment of each failure."""

    def __init__(self, config, camera=None, log=None, photos_dir=None, clock=time.time):
        self.config = config
        self.camera = camera
        self.log = log or AttemptLog.shared()
        self.photos_dir = Path(photos_dir) if photos_dir else intruders_dir()
        self.clock = clock
        self._threads = []
        self.begin_session()

    def begin_session(self):
        self.started = self.clock()
        self.failures = 0
        self.photos_taken = 0
        self.session_ids = []

    def on_failed(self, kind="password"):
        entry = self.log.record(kind, when=self.clock())
        self.session_ids.append(entry["id"])
        self.failures += 1
        if self._wants_photo():
            self.photos_taken += 1  # counted when requested, so a slow camera cannot cause a burst
            thread = threading.Thread(target=self._capture, args=(entry["id"],), daemon=True)
            self._threads.append(thread)
            thread.start()
        return entry

    def _wants_photo(self):
        cfg = self.config
        return bool(
            self.camera is not None
            and cfg.setting("intruder_camera")
            and self.failures >= int(cfg.setting("intruder_after"))
            and self.photos_taken < MAX_PHOTOS_PER_SESSION
            and self.camera.available()
        )

    def _capture(self, entry_id):
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
            name = f"{PHOTO_PREFIX}{stamp}-{entry_id}.png"
            self.photos_dir.mkdir(parents=True, exist_ok=True)
            if self.camera.capture(self.photos_dir / name):
                self.log.attach_photo(entry_id, name)
                prune_photos(int(self.config.setting("intruder_keep_days")), self.photos_dir)
        except Exception as exc:
            log_exception("Intruder photo failed", exc)

    def session_entries(self):
        ids = set(self.session_ids)
        return [e for e in self.log.since(self.started - 1) if e["id"] in ids]

    def wait(self, timeout=5.0):
        """Wait for pending photos, at most ``timeout`` seconds in total."""
        deadline = time.monotonic() + timeout
        for thread in list(self._threads):
            thread.join(max(0.0, deadline - time.monotonic()))
