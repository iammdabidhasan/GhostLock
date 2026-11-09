"""The hexagon "shield" animation shown when the screen locks.

A grid of hexagons forms outward from the ghost, covers the screen, shimmers, then shrinks away.
It lives in its own click-through-colour window (like the password capsule), so only the hexagon
outlines are drawn and the desktop stays visible behind them.

``cell_state`` holds the timing maths (no GUI) so it can be tested anywhere.
"""
import math
import time
import tkinter as tk

from gl_core import log_exception
from gl_theme import geometry_string

HOLD_SECONDS = 0.7
FADE_SECONDS = 1.3
FRAME_MS = 33
KEY_COLOR = "#010203"  # same colour key the password capsule window uses
BACKDROP_COLOR = "#02060c"
DARK = (6, 10, 18)  # lines fade towards this (the dimmed desktop) because Tk cannot draw alpha
TONES = {"teal": ((20, 184, 166), (37, 99, 235)), "ice": ((150, 230, 255), (90, 140, 255)), "amber": ((255, 196, 80), (255, 110, 60))}


def clamp(value):
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def mix(a, b, amount):
    return tuple(a[i] + (b[i] - a[i]) * amount for i in range(3))


def total_seconds(build):
    return build + HOLD_SECONDS + FADE_SECONDS


def cell_state(t, delay, build):
    """(grow, flash, life) for a cell whose normalised start order is ``delay`` (0..1) at time ``t`` seconds.

    grow  0..1  how far the cell has appeared (0 = not yet)
    flash 0..1  bright pulse while it appears
    life  0..1  1 = fully shown, 0 = gone (the fade-out phase)
    """
    grow = clamp((t - delay * 0.65 * build) / (0.35 * build + 1e-6))
    flash = math.sin(math.pi * grow) if 0 < grow < 1 else 0.0
    fade_t = max(0.0, t - (build + HOLD_SECONDS))
    life = 1.0 - clamp((fade_t - delay * 0.5 * FADE_SECONDS) / (0.5 * FADE_SECONDS))
    return grow, flash, life


def hex_points(x, y, r):
    pts = []
    for k in range(6):
        angle = math.radians(60 * k - 30)
        pts += [x + r * math.cos(angle), y + r * math.sin(angle)]
    return pts


def build_cells(width, height, radius, origin, pattern):
    """List of dicts: x, y, delay (0..1 start order), ripple (0..1 distance from origin, used for colour)."""
    step_x, step_y = math.sqrt(3) * radius, 1.5 * radius
    cells, farthest = [], 1.0
    row = -1
    while row * step_y < height + radius * 2:
        col = -1
        while col * step_x < width + step_x:
            x = col * step_x + (step_x / 2 if row & 1 else 0)
            y = row * step_y
            dist = math.hypot(x - origin[0], y - origin[1])
            farthest = max(farthest, dist)
            cells.append({"x": x, "y": y, "dist": dist, "seed": ((row * 73856093) ^ (col * 19349663)) % 1000 / 1000})
            col += 1
        row += 1
    for cell in cells:
        ripple = cell["dist"] / farthest
        cell["ripple"] = ripple
        cell["delay"] = {
            "ripple": ripple,
            "sweep": clamp(cell["x"] / max(1, width)),
            "scan": clamp(cell["y"] / max(1, height)),
            "random": cell["seed"],
        }.get(pattern, ripple)
    return cells


def _hex(color):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in color)


class HexShield:
    """Fullscreen hexagon animation. Calls ``on_done`` when it finishes or is closed."""

    def __init__(self, root, bounds, origin, pattern="ripple", tone="teal", seconds=2.0, cell=None,
                 backdrop=False, on_done=None, on_click=None, on_motion=None, on_key=None):  # fmt: skip
        self.root, self.on_done = root, on_done
        self.build = max(0.5, float(seconds))
        self.tone = TONES.get(tone, TONES["teal"])
        x, y, w, h = bounds
        self.size = (w, h)
        self.finished = False
        self.windows = []
        geometry = geometry_string(w, h, x, y)
        if backdrop:
            dim = tk.Toplevel(root)
            dim.overrideredirect(True)
            dim.configure(bg=BACKDROP_COLOR)
            dim.attributes("-topmost", True)
            try:
                dim.attributes("-alpha", 0.55)
            except tk.TclError:
                pass
            dim.geometry(geometry)
            self.windows.append(dim)
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.configure(bg=KEY_COLOR, cursor="arrow")
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            pass
        self.window.geometry(geometry)
        self.windows.append(self.window)
        self.canvas = tk.Canvas(self.window, width=w, height=h, bg=KEY_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack()
        for widget in (self.window, self.canvas):
            if on_click:
                widget.bind("<ButtonPress-1>", on_click, add="+")
            if on_motion:
                widget.bind("<Motion>", on_motion, add="+")
            if on_key:
                widget.bind("<Key>", on_key, add="+")
        radius = cell or max(14, (w if w < 2600 else w / 2) / 34)
        local_origin = (origin[0] - x, origin[1] - y)
        self.cells = build_cells(w, h, radius, local_origin, pattern)
        for c in self.cells:
            c["item"] = None
            c["sig"] = None
            c["color"] = mix(self.tone[0], self.tone[1], c["ripple"])
        self.scan = self.canvas.create_line(0, -10, w, -10, fill=_hex(self.tone[0]), width=2, state="hidden")
        self.radius = radius
        self.started = time.monotonic()
        self._job = self.root.after(FRAME_MS, self._frame)

    # -------------------------------------------------------------- animation
    def _frame(self):
        self._job = None
        if self.finished:
            return
        try:
            t = time.monotonic() - self.started
            if t >= total_seconds(self.build):
                self.close()
                return
            self._draw(t)
            self._job = self.root.after(FRAME_MS, self._frame)
        except tk.TclError:
            self.close()
        except Exception as exc:  # decoration must never break the lock
            log_exception("Shield animation failed", exc)
            self.close()

    def _draw(self, t):
        canvas = self.canvas
        for c in self.cells:
            grow, flash, life = cell_state(t, c["delay"], self.build)
            if grow <= 0 or life <= 0:
                if c["item"] is not None and c["sig"] != "off":
                    canvas.itemconfigure(c["item"], state="hidden")
                    c["sig"] = "off"
                continue
            shimmer = 0.72 + 0.28 * math.sin(t * 3 + c["seed"] * 20)
            scale = (0.55 + 0.45 * min(1.0, grow * 1.4)) * life
            brightness = min(1.0, grow * shimmer) * (0.35 + 0.65 * life)
            line = mix(DARK, mix(c["color"], (255, 255, 255), flash * 0.7), min(1.0, brightness + 0.45 * flash))
            fill = mix(DARK, c["color"], 0.28 + 0.4 * flash) if flash > 0.12 else None
            sig = (round(scale * 8), _hex(line), round(flash * 5) if fill else -1)
            if sig == c["sig"]:
                continue
            c["sig"] = sig
            radius = self.radius * 0.94 * (sig[0] / 8 or 0.05)
            pts = hex_points(c["x"], c["y"], radius)
            width = 1 + (1.4 if flash > 0.5 else 0)
            if c["item"] is None:
                c["item"] = canvas.create_polygon(pts, outline=sig[1], fill="", width=width)
            canvas.coords(c["item"], *pts)
            canvas.itemconfigure(c["item"], state="normal", outline=sig[1], fill=_hex(fill) if fill else "", stipple="gray25" if fill else "", width=width)
        build_end = self.build * 0.8
        end = self.build + HOLD_SECONDS + FADE_SECONDS * 0.4
        if build_end < t < end:
            canvas.itemconfigure(self.scan, state="normal")
            y = self.size[1] * (t - build_end) / (end - build_end)
            canvas.coords(self.scan, 0, y, self.size[0], y)
        else:
            canvas.itemconfigure(self.scan, state="hidden")

    # -------------------------------------------------------------- lifecycle
    def lift(self):
        for window in self.windows:
            try:
                window.attributes("-topmost", True)
                window.lift()
            except tk.TclError:
                pass

    def close(self):
        if self.finished:
            return
        self.finished = True
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
        for window in self.windows:
            try:
                window.destroy()
            except Exception:
                pass
        if self.on_done:
            try:
                self.on_done()
            except Exception as exc:
                log_exception("Shield done callback failed", exc)
