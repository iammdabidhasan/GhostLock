"""Webcam snapshot for the optional intruder photo (no GUI).

Uses OpenCV when it is bundled in the build (``build.ps1`` includes it unless you pass ``-NoCamera``).
Without it, ``available()`` is False and the app simply skips photos; nothing else changes.
Photos are saved locally as PNG. Nothing is ever sent anywhere.
"""
import threading
import time
from pathlib import Path

from gl_core import log_exception

WARMUP_FRAMES = 8  # webcams return dark frames until auto-exposure settles
MAX_WIDTH = 960


class OpenCvCamera:
    def __init__(self, index=0):
        self.index = index
        self._cv2_module = None
        self._tried = False
        self.import_error = ""
        self._busy = threading.Lock()
        self._import_lock = threading.Lock()

    def _cv2(self):
        with self._import_lock:  # a second caller waits for the first import instead of seeing "not available"
            if not self._tried:
                try:
                    import cv2  # noqa: PLC0415 (optional dependency)

                    self._cv2_module = cv2
                except Exception as exc:
                    self._cv2_module = None
                    self.import_error = f"{type(exc).__name__}: {exc}"
                    log_exception("OpenCV could not be loaded", exc)
                self._tried = True
        return self._cv2_module

    def available(self):
        return self._cv2() is not None

    def capture(self, path):
        """Save one photo to ``path``. Returns True on success. Safe to call from any thread."""
        cv2 = self._cv2()
        if cv2 is None or not self._busy.acquire(blocking=False):
            return False  # no OpenCV, or a photo is already being taken
        try:
            backends = [getattr(cv2, name) for name in ("CAP_DSHOW", "CAP_MSMF", "CAP_ANY") if hasattr(cv2, name)]
            for backend in backends:
                device = None
                try:
                    device = cv2.VideoCapture(self.index, backend)
                    if not device.isOpened():
                        continue
                    frame = None
                    for _ in range(WARMUP_FRAMES):
                        ok, candidate = device.read()
                        if ok:
                            frame = candidate
                        time.sleep(0.08)
                    if frame is None:
                        continue
                    height, width = frame.shape[:2]
                    if width > MAX_WIDTH:
                        frame = cv2.resize(frame, (MAX_WIDTH, int(height * MAX_WIDTH / width)))
                    ok, encoded = cv2.imencode(".png", frame)
                    if not ok:
                        continue
                    target = Path(path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    encoded.tofile(str(target))  # unlike cv2.imwrite this handles non-ASCII user names
                    return True
                except Exception as exc:
                    log_exception("Camera capture failed", exc)
                finally:
                    if device is not None:
                        try:
                            device.release()
                        except Exception:
                            pass
            return False
        finally:
            self._busy.release()


def default_camera():
    return OpenCvCamera()
