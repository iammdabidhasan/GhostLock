"""GhostLock visual system: palette, DPI scaling, Windows chrome helpers and widgets."""
import ctypes
import os
import re
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

from gl_core import IS_WINDOWS, asset_path, log_exception

# Palette taken from the GhostLock 2.0 promo video: deep navy with periwinkle accent.
COLORS = {
    "bg": "#0b0f1d",
    "surface": "#121830",
    "surface_2": "#192142",
    "surface_3": "#222c57",
    "surface_hover": "#2b3769",
    "border": "#2b3669",
    "text": "#eef1ff",
    "muted": "#9aa5cf",
    "faint": "#6b76a3",
    "accent": "#7b8dff",
    "accent_hover": "#95a4ff",
    "accent_press": "#6677f0",
    "accent_dim": "#3b4690",
    "on_accent": "#090d26",
    "danger": "#ff6b7d",
    "danger_hover": "#ff8796",
    "danger_press": "#e5566a",
    "on_danger": "#2a0710",
    "warning": "#f5c26b",
    "success": "#5ee0a8",
    # Colour that Windows treats as fully see-through on the lock screen panel.
    "key": "#010203",
}

FONT_FAMILY = "Segoe UI" if IS_WINDOWS else "Inter"
MONO_FAMILY = "Consolas" if IS_WINDOWS else "DejaVu Sans Mono"

_STATE = {"scale": 1.0}


# --------------------------------------------------------------------------- scaling & fonts
def init_scaling(root):
    try:
        _STATE["scale"] = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
    except Exception:
        _STATE["scale"] = 1.0
    return _STATE["scale"]


def ui_scale():
    return _STATE["scale"]


def px(value):
    """Scale a 96-dpi pixel measurement to the current display."""
    return int(round(value * _STATE["scale"]))


def font(size=10, bold=False, mono=False):
    return (MONO_FAMILY if mono else FONT_FAMILY, size, "bold" if bold else "normal")


def enable_dpi_awareness():
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# --------------------------------------------------------------------------- screen geometry
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def geometry_string(width, height, x, y):
    return f"{int(width)}x{int(height)}{int(x):+d}{int(y):+d}"


def virtual_screen_geometry(root):
    if IS_WINDOWS:
        try:
            user32 = ctypes.windll.user32
            x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
            w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
            if w > 0 and h > 0:
                return x, y, w, h
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def primary_screen_geometry(root):
    if IS_WINDOWS:
        try:
            user32 = ctypes.windll.user32
            w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
            if w > 0 and h > 0:
                return 0, 0, w, h
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def work_area(root, x=None, y=None):
    """(left, top, right, bottom) usable area of the monitor containing (x, y)."""
    if IS_WINDOWS:
        try:
            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user32.MonitorFromPoint.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
            if x is None or y is None:
                x, y = 0, 0
            monitor = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def center_window(window, width, height):
    window.update_idletasks()
    left, top, right, bottom = work_area(window)
    margin = px(24)
    width = min(width, right - left - margin)
    height = min(height, bottom - top - margin)
    x = left + (right - left - width) // 2
    y = top + (bottom - top - height) // 2
    window.geometry(geometry_string(width, height, x, y))
    return width, height


# --------------------------------------------------------------------------- Windows chrome
def _hwnd(window):
    if not IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        handle = window.winfo_id()
        return user32.GetAncestor(handle, 2) or handle  # GA_ROOT
    except Exception:
        return None


def _dwm_set(hwnd, attribute, value):
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        data = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))
    except Exception:
        return -1


def _colorref(hex_color):
    return int(hex_color[5:7] + hex_color[3:5] + hex_color[1:3], 16)


def style_titlebar(window):
    """Dark, theme-coloured title bar (Windows 10 1809+ / Windows 11)."""
    if not IS_WINDOWS:
        return
    try:
        window.update_idletasks()
        hwnd = _hwnd(window)
        if not hwnd:
            return
        if _dwm_set(hwnd, 20, 1) != 0:
            _dwm_set(hwnd, 19, 1)
        _dwm_set(hwnd, 34, _colorref(COLORS["border"]))  # border
        _dwm_set(hwnd, 35, _colorref(COLORS["bg"]))  # caption
        _dwm_set(hwnd, 36, _colorref(COLORS["text"]))  # caption text
    except Exception:
        pass


def apply_round_corners(window, radius=16):
    """Smooth rounded corners on Windows 11; GDI region fallback on Windows 10."""
    if not IS_WINDOWS:
        return
    try:
        window.update_idletasks()
        hwnd = _hwnd(window)
        if not hwnd:
            return
        if _dwm_set(hwnd, 33, 2) == 0:  # DWMWCP_ROUND
            return
        gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
        width, height = max(1, window.winfo_width()), max(1, window.winfo_height())
        region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius)
        if user32.SetWindowRgn(hwnd, region, True) == 0:
            gdi32.DeleteObject(region)
    except Exception:
        pass


def keep_rounded(window, radius=16):
    state = {"size": None}

    def update(_event=None):
        size = (window.winfo_width(), window.winfo_height())
        if size == state["size"]:
            return
        state["size"] = size
        window.after_idle(lambda: apply_round_corners(window, radius))

    window.bind("<Configure>", update, add="+")
    window.after(40, update)


# --------------------------------------------------------------------------- icons
_ICON_CACHE = {}


def _available_sizes(kind):
    pattern = re.compile(r"^ghostlock_(\d+)\.png$" if kind == "tile" else r"^ghostlock_mark_(\d+)\.png$")
    try:
        names = os.listdir(asset_path(""))
    except OSError:
        return []
    return sorted(int(m.group(1)) for m in (pattern.match(n) for n in names) if m)


def load_icon(master, kind="tile", target=48):
    """Return a PhotoImage whose pixel size is the closest pre-rendered size to `target`."""
    sizes = _available_sizes(kind)
    if not sizes:
        return None
    pick = min(sizes, key=lambda s: abs(s - target))
    key = (id(master.winfo_toplevel()), kind, pick)
    if key not in _ICON_CACHE:
        name = f"ghostlock_{pick}.png" if kind == "tile" else f"ghostlock_mark_{pick}.png"
        try:
            _ICON_CACHE[key] = tk.PhotoImage(master=master, file=str(asset_path(name)))
        except Exception as exc:
            log_exception("icon load failed", exc)
            return None
    return _ICON_CACHE[key]


def set_window_icon(window):
    try:
        icon = asset_path("ghostlock.ico")
        if IS_WINDOWS and icon.exists():
            window.iconbitmap(default=str(icon))
            return
    except Exception:
        pass
    try:
        image = load_icon(window, "tile", 64)
        if image:
            window.iconphoto(True, image)
    except Exception:
        pass


# --------------------------------------------------------------------------- primitives
def rounded_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]  # fmt: skip


def make_label(parent, text="", size=10, color="muted", bold=False, **kw):
    kw.setdefault("bg", parent.cget("bg"))
    kw.setdefault("anchor", "w")
    kw.setdefault("justify", "left")
    return tk.Label(parent, text=text, fg=COLORS.get(color, color), font=font(size, bold), **kw)


def divider(parent, pady=0):
    frame = tk.Frame(parent, bg=COLORS["border"], height=1)
    frame.pack(fill="x", pady=pady)
    return frame


class Card(tk.Frame):
    """Surface with a 1px border; put content in `.body`."""

    def __init__(self, parent, padx=22, pady=20, bg=None):
        super().__init__(parent, bg=COLORS["border"], padx=1, pady=1)
        self.body = tk.Frame(self, bg=bg or COLORS["surface"], padx=px(padx), pady=px(pady))
        self.body.pack(fill="both", expand=True)


class Button(tk.Canvas):
    STYLES = {
        "primary": ("accent", "accent_hover", "accent_press", "on_accent"),
        "secondary": ("surface_3", "surface_hover", "surface_2", "text"),
        "danger": ("danger", "danger_hover", "danger_press", "on_danger"),
        "ghost": (None, "surface_2", "surface_3", "text"),
    }

    def __init__(self, parent, text, command=None, kind="secondary", width=None, height=38, size=10, padx=22):
        self.kind = kind
        self.command = command
        self.text = text
        self._enabled = True
        self._hover = False
        self._pressed = False
        weight = kind in ("primary", "danger")
        self._font = tkfont.Font(family=FONT_FAMILY, size=size, weight="bold" if weight else "normal")
        w = width if width else self._font.measure(text) + 2 * px(padx)
        h = px(height)
        super().__init__(
            parent, width=w, height=h, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2", takefocus=1
        )
        self._bw, self._bh = w, h
        self._shape = self.create_polygon(
            rounded_points(1, 1, w - 1, h - 1, px(9)), smooth=True, splinesteps=12, fill="", outline=""
        )
        self._label = self.create_text(w // 2, h // 2, text=text, font=self._font)
        self._paint()
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda e: self._paint())
        self.bind("<FocusOut>", lambda e: self._paint())
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)

    def _colors(self):
        base, hover, press, fg = self.STYLES[self.kind]
        base_color = COLORS[base] if base else self.cget("bg")
        if not self._enabled:
            return COLORS["surface_2"], COLORS["faint"]
        if self._pressed:
            return COLORS[press], COLORS[fg]
        if self._hover:
            return COLORS[hover], COLORS[fg]
        return base_color, COLORS[fg]

    def _paint(self):
        fill, fg = self._colors()
        focused = self.focus_get() is self and self._enabled
        self.itemconfigure(self._shape, fill=fill, outline=COLORS["text"] if focused else "", width=2 if focused else 1)
        self.itemconfigure(self._label, fill=fg)

    def _set(self, hover=None, pressed=None):
        if hover is not None:
            self._hover = hover
        if pressed is not None:
            self._pressed = pressed
        self._paint()

    def _release(self, event):
        was_pressed = self._pressed
        self._set(pressed=False)
        if was_pressed and self._enabled and 0 <= event.x <= self._bw and 0 <= event.y <= self._bh:
            self._invoke()

    def _invoke(self, _event=None):
        if self._enabled and self.command:
            self.command()
        return "break"

    def configure_text(self, text):
        self.text = text
        self.itemconfigure(self._label, text=text)

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()


class Field(tk.Frame):
    """Labelled text input with focus ring, optional show/hide and hint/error line."""

    def __init__(self, parent, label, secret=False, hint="", on_return=None, wrap=420, bg=None):
        bg = bg or parent.cget("bg")
        super().__init__(parent, bg=bg)
        self.var = tk.StringVar()
        self._secret = secret
        self._revealed = False
        self._hint = hint
        make_label(self, label, 9, "muted", True, bg=bg).pack(fill="x", pady=(0, px(5)))
        self._border = tk.Frame(self, bg=COLORS["border"], padx=1, pady=1)
        self._border.pack(fill="x")
        inner = tk.Frame(self._border, bg=COLORS["surface_2"])
        inner.pack(fill="x")
        self.entry = tk.Entry(
            inner,
            textvariable=self.var,
            show="•" if secret else "",
            bg=COLORS["surface_2"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent_dim"],
            selectforeground=COLORS["text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=font(11),
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(px(12), px(6)), pady=px(9))
        self._toggle = None
        if secret:
            self._toggle = tk.Label(
                inner, text="Show", bg=COLORS["surface_2"], fg=COLORS["muted"], font=font(9), cursor="hand2"
            )
            self._toggle.pack(side="right", padx=(0, px(12)))
            self._toggle.bind("<Button-1>", self._toggle_reveal)
        self._message = tk.Label(
            self, text=hint, bg=bg, fg=COLORS["faint"], font=font(9), anchor="w", justify="left", wraplength=px(wrap)
        )
        if hint:
            self._message.pack(fill="x", pady=(px(5), 0))
        self.entry.bind("<FocusIn>", lambda e: self._focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._focus(False))
        if on_return:
            self.entry.bind("<Return>", lambda e: on_return())

    def _focus(self, focused):
        if getattr(self, "_has_error", False):
            return
        self._border.configure(bg=COLORS["accent"] if focused else COLORS["border"])

    def _refit(self):
        """Let the window grow/shrink so an inline message never clips the footer."""
        try:
            top = self.winfo_toplevel()
            if top.resizable()[0] is False or True:
                top.geometry("")
        except tk.TclError:
            pass

    def _toggle_reveal(self, _event=None):
        self._revealed = not self._revealed
        self.entry.configure(show="" if self._revealed else "•")
        self._toggle.configure(text="Hide" if self._revealed else "Show")

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(value)

    def clear(self):
        self.var.set("")

    def focus(self):
        self.entry.focus_set()

    def set_error(self, text=""):
        if bool(text) == getattr(self, "_has_error", False) and not text:
            return
        self._has_error = bool(text)
        self.after_idle(self._refit)
        if text:
            self._border.configure(bg=COLORS["danger"])
            self._message.configure(text=text, fg=COLORS["danger"])
            self._message.pack(fill="x", pady=(px(5), 0))
        else:
            self._border.configure(bg=COLORS["border"])
            self._message.configure(text=self._hint, fg=COLORS["faint"])
            if not self._hint:
                self._message.pack_forget()


class Toggle(tk.Canvas):
    def __init__(self, parent, value=False, command=None):
        self.value = bool(value)
        self.command = command
        w, h = px(42), px(24)
        super().__init__(parent, width=w, height=h, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self._bw, self._bh = w, h
        self.bind("<Button-1>", self.toggle)
        self.bind("<space>", self.toggle)
        self.bind("<Return>", self.toggle)
        self.bind("<FocusIn>", lambda e: self._draw())
        self.bind("<FocusOut>", lambda e: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self._bw, self._bh
        track = COLORS["accent"] if self.value else COLORS["surface_3"]
        focused = self.focus_get() is self
        self.create_polygon(
            rounded_points(1, 1, w - 1, h - 1, h // 2), smooth=True, splinesteps=12,
            fill=track, outline=COLORS["text"] if focused else "", width=2,
        )  # fmt: skip
        pad = px(3)
        d = h - 2 * pad
        x = w - pad - d if self.value else pad
        self.create_oval(x, pad, x + d, pad + d, fill=COLORS["on_accent"] if self.value else COLORS["muted"], outline="")

    def toggle(self, _event=None):
        self.set(not self.value)
        if self.command:
            self.command(self.value)
        return "break"

    def get(self):
        return self.value

    def set(self, value):
        self.value = bool(value)
        self._draw()


class ToggleRow(tk.Frame):
    """Title + description on the left, switch on the right."""

    def __init__(self, parent, title, description="", value=False, command=None, wrap=330):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        text = tk.Frame(self, bg=bg)
        text.pack(side="left", fill="x", expand=True)
        make_label(text, title, 10, "text", True, bg=bg).pack(fill="x")
        if description:
            make_label(text, description, 9, "muted", bg=bg, wraplength=px(wrap)).pack(fill="x", pady=(px(2), 0))
        self.toggle = Toggle(self, value, command)
        self.toggle.pack(side="right", padx=(px(14), 0))
        for widget in (text, *text.winfo_children()):
            widget.bind("<Button-1>", lambda e: self.toggle.toggle(), add="+")

    def get(self):
        return self.toggle.get()


class Slider(tk.Canvas):
    def __init__(self, parent, minimum, maximum, value, command=None, formatter=None):
        self.minimum, self.maximum = minimum, maximum
        self.value = max(minimum, min(maximum, value))
        self.command = command
        self.formatter = formatter or (lambda v: str(int(round(v))))
        super().__init__(parent, height=px(34), bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Left>", lambda e: self._nudge(-1))
        self.bind("<Right>", lambda e: self._nudge(1))
        self.bind("<FocusIn>", lambda e: self._draw())
        self.bind("<FocusOut>", lambda e: self._draw())

    def _bounds(self):
        pad = px(12)
        return pad, max(pad + 1, self.winfo_width() - pad - px(54))

    def _draw(self):
        self.delete("all")
        left, right = self._bounds()
        cy = self.winfo_height() // 2
        t = (self.value - self.minimum) / (self.maximum - self.minimum)
        x = left + (right - left) * t
        track_h = px(5)
        self.create_line(left, cy, right, cy, fill=COLORS["surface_3"], width=track_h, capstyle="round")
        self.create_line(left, cy, x, cy, fill=COLORS["accent"], width=track_h, capstyle="round")
        r = px(9)
        focused = self.focus_get() is self
        self.create_oval(
            x - r, cy - r, x + r, cy + r, fill=COLORS["text"], outline=COLORS["accent"] if focused else "", width=2
        )
        self.create_text(right + px(18), cy, text=self.formatter(self.value), anchor="w", fill=COLORS["text"], font=font(10, True))

    def _drag(self, event):
        self.focus_set()
        left, right = self._bounds()
        t = max(0.0, min(1.0, (event.x - left) / max(1, right - left)))
        self.set(self.minimum + t * (self.maximum - self.minimum))

    def _nudge(self, direction):
        self.set(self.value + direction * (self.maximum - self.minimum) / 20)
        return "break"

    def set(self, value):
        self.value = max(self.minimum, min(self.maximum, value))
        self._draw()
        if self.command:
            self.command(self.value)

    def get(self):
        return self.value


class StrengthMeter(tk.Canvas):
    COLORS_BY_SCORE = {0: "danger", 1: "danger", 2: "warning", 3: "success", 4: "success"}

    def __init__(self, parent):
        super().__init__(parent, height=px(18), bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.score, self.text = 0, ""
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, score, text):
        self.score, self.text = score, text
        self._draw()

    def _draw(self):
        self.delete("all")
        width = self.winfo_width()
        gap, seg_h = px(5), px(4)
        label_w = px(70)
        seg_w = max(10, (width - label_w - 3 * gap) // 4)
        color = COLORS[self.COLORS_BY_SCORE[self.score]]
        for i in range(4):
            x = i * (seg_w + gap)
            filled = self.text and i < max(1, self.score)
            self.create_rectangle(x, px(6), x + seg_w, px(6) + seg_h, width=0, fill=color if filled else COLORS["surface_3"])
        if self.text:
            self.create_text(width, px(8), text=self.text, anchor="e", fill=color, font=font(9, True))


class StepDots(tk.Canvas):
    """Compact 'Step n of N' progress pills."""

    def __init__(self, parent, total):
        super().__init__(parent, height=px(8), width=total * px(34), bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.total = total
        self.set(0)

    def set(self, index):
        self.delete("all")
        for i in range(self.total):
            x = i * px(34)
            color = COLORS["accent"] if i <= index else COLORS["surface_3"]
            self.create_polygon(
                rounded_points(x, 0, x + px(28), px(6), px(3)), smooth=True, fill=color, outline=""
            )


class ProgressBar(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, height=px(8), bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.fraction = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, fraction):
        self.fraction = max(0.0, min(1.0, fraction))
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), px(6)
        self.create_polygon(rounded_points(0, 1, w, 1 + h, h // 2), smooth=True, fill=COLORS["surface_3"], outline="")
        if self.fraction > 0:
            fw = max(h, int(w * self.fraction))
            self.create_polygon(rounded_points(0, 1, fw, 1 + h, h // 2), smooth=True, fill=COLORS["accent"], outline="")


class ScrollFrame(tk.Frame):
    """Vertically scrollable container with a slim themed scrollbar (mouse wheel aware)."""

    def __init__(self, parent, bg=None):
        bg = bg or COLORS["bg"]
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Canvas(self, width=px(8), bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.inner.bind("<Configure>", lambda e: self._sync(), add="+")
        self.canvas.bind("<Configure>", self._resize, add="+")
        self.bar.bind("<B1-Motion>", self._drag_bar)
        self.bar.bind("<Button-1>", self._drag_bar)
        self._bar_visible = False

    def _resize(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _sync(self):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_scroll(self, first, last):
        first, last = float(first), float(last)
        need = not (first <= 0.0 and last >= 1.0)
        if need and not self._bar_visible:
            self.bar.pack(side="right", fill="y", padx=(0, px(2)))
            self._bar_visible = True
        elif not need and self._bar_visible:
            self.bar.pack_forget()
            self._bar_visible = False
        self.bar.delete("all")
        if need:
            h = max(1, self.bar.winfo_height())
            self.bar.create_polygon(
                rounded_points(1, h * first, px(7), max(h * first + px(24), h * last), px(3)),
                smooth=True, fill=COLORS["surface_hover"], outline="",
            )  # fmt: skip

    def _drag_bar(self, event):
        h = max(1, self.bar.winfo_height())
        self.canvas.yview_moveto(max(0.0, min(1.0, event.y / h - 0.1)))

    def _wheel(self, event):
        if self._bar_visible:
            delta = -1 if (getattr(event, "delta", 0) > 0 or event.num == 4) else 1
            self.canvas.yview_scroll(delta * 3, "units")

    def finalize(self):
        """Call after building children so wheel scrolling works over every widget."""

        def bind_tree(widget):
            widget.bind("<MouseWheel>", self._wheel, add="+")
            widget.bind("<Button-4>", self._wheel, add="+")
            widget.bind("<Button-5>", self._wheel, add="+")
            for child in widget.winfo_children():
                bind_tree(child)

        bind_tree(self)


class ToolTip:
    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _event=None):
        self.hide()
        self._job = self.widget.after(self.delay, self.show)

    def show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + px(8)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(8)
            self.tip = tk.Toplevel(self.widget)
            self.tip.overrideredirect(True)
            self.tip.attributes("-topmost", True)
            self.tip.configure(bg=COLORS["border"])
            inner = tk.Label(
                self.tip, text=self.text, bg=COLORS["surface_2"], fg=COLORS["text"], padx=px(10), pady=px(6), font=font(9)
            )
            inner.pack(padx=1, pady=1)
            self.tip.geometry(f"+{x}+{y}")
        except tk.TclError:
            self.tip = None

    def hide(self, _event=None):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


def header_block(parent, title, subtitle, icon_px=52, bg=None, title_size=20):
    """Icon + title + subtitle row used at the top of every window."""
    bg = bg or parent.cget("bg")
    row = tk.Frame(parent, bg=bg)
    icon = load_icon(parent, "tile", px(icon_px))
    if icon:
        holder = tk.Label(row, image=icon, bg=bg)
        holder.image = icon
        holder.pack(side="left", padx=(0, px(16)))
    text = tk.Frame(row, bg=bg)
    text.pack(side="left", fill="x", expand=True)
    make_label(text, title, title_size, "text", True, bg=bg).pack(fill="x")
    if subtitle:
        make_label(text, subtitle, 10, "muted", bg=bg, wraplength=px(380)).pack(fill="x", pady=(px(3), 0))
    return row


def key_chip(parent, text, bg=None):
    """Small keyboard-key style label."""
    bg = bg or parent.cget("bg")
    frame = tk.Frame(parent, bg=COLORS["border"], padx=1, pady=1)
    tk.Label(
        frame, text=text, bg=COLORS["surface_2"], fg=COLORS["text"], font=font(9, True), padx=px(8), pady=px(2)
    ).pack()
    return frame


def time_ms():
    return int(time.monotonic() * 1000)


class TabBar(tk.Frame):
    """Simple underlined tabs. `command(key)` is called when the selection changes."""

    def __init__(self, parent, tabs, command):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        self.command = command
        self.items = {}
        for key, text in tabs:
            holder = tk.Frame(self, bg=bg, cursor="hand2")
            holder.pack(side="left", padx=(0, px(6)))
            label = tk.Label(holder, text=text, bg=bg, fg=COLORS["muted"], font=font(10, True), padx=px(14), pady=px(9), cursor="hand2")
            label.pack()
            underline = tk.Frame(holder, bg=bg, height=px(3))
            underline.pack(fill="x")
            for widget in (holder, label, underline):
                widget.bind("<Button-1>", lambda _e, k=key: self.select(k))
            label.bind("<Enter>", lambda _e, k=key: self._hover(k, True))
            label.bind("<Leave>", lambda _e, k=key: self._hover(k, False))
            self.items[key] = (label, underline)
        self.current = None

    def _hover(self, key, inside):
        if key != self.current:
            self.items[key][0].configure(fg=COLORS["text"] if inside else COLORS["muted"])

    def select(self, key):
        self.current = key
        bg = self.cget("bg")
        for k, (label, underline) in self.items.items():
            active = k == key
            label.configure(fg=COLORS["text"] if active else COLORS["muted"])
            underline.configure(bg=COLORS["accent"] if active else bg)
        if self.command:
            self.command(key)


def chip(parent, text, command):
    """Small clickable suggestion pill."""
    bg = parent.cget("bg")
    frame = tk.Frame(parent, bg=COLORS["border"], padx=1, pady=1, cursor="hand2")
    label = tk.Label(frame, text=text, bg=COLORS["surface_2"], fg=COLORS["muted"], font=font(9), padx=px(10), pady=px(4), cursor="hand2")
    label.pack()

    def hover(inside):
        label.configure(bg=COLORS["surface_3"] if inside else COLORS["surface_2"], fg=COLORS["text"] if inside else COLORS["muted"])

    for widget in (frame, label):
        widget.bind("<Enter>", lambda _e: hover(True))
        widget.bind("<Leave>", lambda _e: hover(False))
        widget.bind("<Button-1>", lambda _e: command())
    return frame
