"""GhostLock lock screen.

Layout
------
* ``overlay``  - one borderless window covering every monitor. Its opacity dims the desktop.
* ``panel``    - a second window whose background colour is a Windows colour key, so everything
                 except the capsule outline and dots/text is genuinely see-through.
                 This is what gives the password field its transparent look (no blur, no filled box).
* ``recovery`` - a small opaque card shown only while answering the recovery question.
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

from gl_core import APP_NAME, log_exception
from gl_theme import (
    COLORS,
    Button,
    Card,
    Field,
    font,
    geometry_string,
    keep_rounded,
    make_label,
    primary_screen_geometry,
    px,
    virtual_screen_geometry,
)

FIRST_TEST_SECONDS = 25
MAX_PASSWORD_LENGTH = 128
OUTLINE_IDLE = "#d6dbfa"
OUTLINE_BUSY = COLORS["accent"]
OUTLINE_ERROR = COLORS["danger"]
DIM_COLOR = "#03050b"


class LockOverlay:
    def __init__(self, root, config, guard, on_unlock, on_recovered=None):
        self.root, self.config, self.guard = root, config, guard
        self.on_unlock, self.on_recovered = on_unlock, on_recovered
        self.active = True
        self.buffer = ""
        self.busy = False
        self.first_test = not bool(config.data.get("first_lock_tested", False))
        self.test_remaining = FIRST_TEST_SECONDS
        self.status_text = ""
        self.status_color = COLORS["muted"]
        self.outline_color = OUTLINE_IDLE
        self.last_activity = time.monotonic()
        self.faded = False
        self.panel_alpha = 1.0
        self._alpha_target = 1.0
        self._fading = False
        self.idle_seconds = int(config.data.get("idle_hide_seconds", 15))
        self.recovery = None
        self.recovery_field = None
        self._jobs = set()
        self._results = queue.Queue()
        self._shake_origin = (0, 0)
        self._build()

    # ------------------------------------------------------------------ construction
    def _build(self):
        vx, vy, vw, vh = virtual_screen_geometry(self.root)
        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        self.overlay.configure(bg=DIM_COLOR, cursor="arrow")
        self.overlay.attributes("-topmost", True)
        self.overlay.attributes("-alpha", float(self.config.data.get("lock_opacity", 0.6)))
        self.overlay.geometry(geometry_string(vw, vh, vx, vy))
        self._harden(self.overlay)
        self.overlay.bind("<ButtonPress-1>", self._focus_input, add="+")
        self.overlay.bind("<ButtonPress-3>", self.show_recovery, add="+")
        self.overlay.bind("<Motion>", self._wake, add="+")
        self.overlay.bind("<Key>", self._on_key, add="+")

        sx, sy, sw, sh = primary_screen_geometry(self.root)
        # The panel is just the capsule (plus a little room for the shake animation).
        self.cap_w, self.cap_h = px(400), px(60)
        self.panel_w, self.panel_h = self.cap_w + px(60), self.cap_h + px(12)
        self.cap_cy = self.panel_h // 2
        panel_x = sx + (sw - self.panel_w) // 2
        panel_y = sy + int(sh * 0.52) - self.cap_cy
        self._shake_origin = (panel_x, panel_y)

        self.panel = tk.Toplevel(self.root)
        self.panel.overrideredirect(True)
        self.panel.configure(bg=COLORS["key"])
        self.panel.attributes("-topmost", True)
        try:
            self.panel.attributes("-transparentcolor", COLORS["key"])
        except tk.TclError:
            pass
        self.panel.geometry(geometry_string(self.panel_w, self.panel_h, panel_x, panel_y))
        self._harden(self.panel)
        self.canvas = tk.Canvas(
            self.panel, width=self.panel_w, height=self.panel_h, bg=COLORS["key"], highlightthickness=0, bd=0, cursor="arrow"
        )
        self.canvas.pack()
        self.panel.bind("<Key>", self._on_key, add="+")
        self.panel.bind("<Motion>", self._wake, add="+")
        self.canvas.bind("<ButtonPress-1>", self._focus_input, add="+")
        self.canvas.bind("<ButtonPress-3>", self.show_recovery, add="+")

        self._draw()
        self._focus_input()
        self._after(400, self._keep_front)
        self._after(500, self._idle_check)
        self._after(40, self._poll_results)
        if self.first_test:
            self._tick_first_test()

    def _harden(self, window):
        """Alt+F4 / window-manager close must never remove the lock screen."""
        window.protocol("WM_DELETE_WINDOW", lambda: None)
        window.bind("<Alt-F4>", lambda _e: "break", add="+")

    def _after(self, ms, callback):
        job = None

        def run():
            self._jobs.discard(job)
            if self.active:
                callback()

        job = self.root.after(ms, run)
        self._jobs.add(job)
        return job

    # ------------------------------------------------------------------ drawing
    def _draw(self):
        if not self.active:
            return
        c = self.canvas
        c.delete("all")
        cx = self.panel_w // 2

        # Hollow capsule: outline only, so the dimmed desktop shows through the input.
        x1, x2 = cx - self.cap_w // 2, cx + self.cap_w // 2
        y1, y2 = self.cap_cy - self.cap_h // 2, self.cap_cy + self.cap_h // 2
        r, lw = self.cap_h // 2, px(2) + 1
        kw = dict(outline=self.outline_color, width=lw)
        c.create_arc(x1, y1, x1 + 2 * r, y2, start=90, extent=180, style="arc", **kw)
        c.create_arc(x2 - 2 * r, y1, x2, y2, start=-90, extent=180, style="arc", **kw)
        c.create_line(x1 + r, y1, x2 - r, y1, fill=self.outline_color, width=lw)
        c.create_line(x1 + r, y2, x2 - r, y2, fill=self.outline_color, width=lw)

        if self.buffer:
            spacing, radius = px(20), px(5)
            capacity = max(1, (self.cap_w - px(70)) // spacing)
            shown = min(len(self.buffer), capacity)
            start = cx - (shown - 1) * spacing / 2
            for i in range(shown):
                x = start + i * spacing
                c.create_oval(x - radius, self.cap_cy - radius, x + radius, self.cap_cy + radius, fill="#ffffff", outline="")
            if len(self.buffer) > capacity:
                c.create_text(x1 + px(26), self.cap_cy, text="\u2039", fill=COLORS["muted"], font=font(14, True))
        elif self.status_text:
            # Feedback lives inside the capsule so nothing else is ever drawn on the lock screen.
            c.create_text(cx, self.cap_cy, text=self.status_text, fill=self.status_color, font=font(11, True))
        else:
            c.create_text(cx, self.cap_cy, text="Enter password", fill="#9aa5cf", font=font(12))

    def _set_status(self, text, color=None):
        self.status_text = text
        self.status_color = color or COLORS["warning"]
        self._draw()

    # ------------------------------------------------------------------ focus & keep-alive
    def focus(self):
        self._focus_input()

    def _focus_input(self, _event=None):
        if not self.active:
            return "break"
        self._wake()
        try:
            if self.recovery_field and self.recovery and self.recovery.winfo_exists():
                self.recovery_field.entry.focus_force()
            else:
                self.panel.focus_force()
                self.canvas.focus_set()
        except tk.TclError:
            pass
        return "break"

    def _keep_front(self):
        for window in (self.overlay, self.panel, self.recovery):
            try:
                if window and window.winfo_exists():
                    window.attributes("-topmost", True)
                    window.lift()
            except tk.TclError:
                pass
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        target = self.recovery if self.recovery_field else self.panel
        if focused is None or focused.winfo_toplevel() is not target:
            self._focus_input()
        self._after(400, self._keep_front)

    def _wake(self, _event=None):
        """Any mouse movement, click or key brings the password field back instantly."""
        self.last_activity = time.monotonic()
        if self.active and (self.faded or self.panel_alpha < 1.0):
            self.faded = False
            self._fade_to(1.0)

    def _idle_check(self):
        idle_for = time.monotonic() - self.last_activity
        if not self.recovery_field and not self.faded and idle_for >= self.idle_seconds:
            self.faded = True
            self._fade_to(0.0)  # logo + password field disappear completely
        self._after(400, self._idle_check)

    def _fade_to(self, target):
        self._alpha_target = target
        if not self._fading:
            self._fading = True
            self._fade_step()

    def _fade_step(self):
        current, target = self.panel_alpha, self._alpha_target
        if target > current:
            current = min(target, current + 0.34)  # reappear quickly
        else:
            current = max(target, current - 0.05)  # vanish gently
        self._set_panel_alpha(current)
        if current != target:
            self._after(35, self._fade_step)
        else:
            self._fading = False

    def _set_panel_alpha(self, value):
        self.panel_alpha = value
        try:
            self.panel.attributes("-alpha", value)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ keyboard
    def _on_key(self, event):
        if not self.active:
            return "break"
        self._wake()
        if self.recovery_field:
            return None  # the recovery card owns the keyboard
        ctrl = bool(event.state & 0x0004) and not bool(event.state & 0x20000)  # AltGr reports Ctrl+Alt
        keysym = event.keysym
        lower = keysym.lower()
        if keysym in ("Return", "KP_Enter"):
            self.submit()
        elif keysym == "BackSpace":
            if not self.busy and not self.guard.blocked:
                self.buffer = self.buffer[:-1]
                self._set_idle_outline()
                self._draw()
        elif keysym == "Escape":
            self.buffer = ""
            self._draw()
        elif keysym == "F1" or (ctrl and lower == "r"):
            self.show_recovery()
        elif ctrl and lower == "v":
            self._paste()
        elif event.char and event.char.isprintable() and not ctrl:
            self._append(event.char)
        return "break"

    def _append(self, text):
        if self.busy or self.guard.blocked:
            return
        self.buffer = (self.buffer + text)[:MAX_PASSWORD_LENGTH]
        if self.status_text.startswith(("That", "Still")):
            self._set_status("")
        self._set_idle_outline()
        self._draw()

    def _paste(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        self._append("".join(ch for ch in text if ch.isprintable()))

    def _set_idle_outline(self):
        self.outline_color = OUTLINE_IDLE

    # ------------------------------------------------------------------ unlocking
    def submit(self):
        if self.busy or not self.buffer:
            return
        if self.guard.blocked:
            self._start_countdown()
            return
        self.busy = True
        self.outline_color = OUTLINE_BUSY
        value = self.buffer
        self._draw()

        def work():
            try:
                self._results.put(("verified", self.config.verify_any_unlock_secret(value)))
            except Exception as exc:  # never leave the lock stuck in "busy"
                log_exception("Password check failed", exc)
                self._results.put(("verified", False))

        threading.Thread(target=work, daemon=True).start()

    def _poll_results(self):
        try:
            while True:
                kind, payload = self._results.get_nowait()
                if kind == "verified":
                    self._handle_verified(payload)
        except queue.Empty:
            pass
        self._after(40, self._poll_results)

    def _handle_verified(self, ok):
        self.busy = False
        if ok:
            self.guard.reset()
            if self.first_test:
                try:
                    self.config.mark_first_lock_tested()
                except Exception as exc:
                    log_exception("Could not record first lock test", exc)
                self.first_test = False
            self._unlock()
            return
        self.guard.record_failure()
        self.buffer = ""
        self._flash_error()
        if self.guard.blocked:
            self._start_countdown()
        elif self.guard.failures >= 3:
            self._set_status("Still locked. Press F1 for recovery.")
        else:
            self._set_status("That password did not match.", COLORS["danger"])
        self._after(4500, self._clear_error_status)

    def _clear_error_status(self):
        if self.status_text.startswith("That") or self.status_text.startswith("Still"):
            self._set_status("")

    def _start_countdown(self):
        remaining = self.guard.remaining()
        if remaining <= 0:
            self._set_status("")
            return
        self._set_status(f"Too many attempts. Try again in {remaining}s.", COLORS["danger"])
        self._after(1000, self._start_countdown)

    def _flash_error(self):
        self.outline_color = OUTLINE_ERROR
        ox, oy = self._shake_origin
        offsets = [-16, 14, -10, 8, -5, 3, 0]

        def step(index=0):
            if index >= len(offsets):
                self._after(500, self._set_idle_and_draw)
                return
            try:
                self.panel.geometry(f"+{ox + px(offsets[index])}+{oy}")
            except tk.TclError:
                return
            self._after(32, lambda: step(index + 1))

        step()
        self._draw()

    def _set_idle_and_draw(self):
        if not self.busy:
            self._set_idle_outline()
            self._draw()

    def _tick_first_test(self):
        if not self.first_test:
            return
        if self.test_remaining <= 0:
            self._unlock(auto_release=True)
            return
        if not self.status_text.startswith(("That", "Too many", "Still")):
            self._set_status(f"Safety test: unlocks itself in {self.test_remaining}s", COLORS["warning"])
        self.test_remaining -= 1
        self._after(1000, self._tick_first_test)

    # ------------------------------------------------------------------ recovery
    def show_recovery(self, _event=None):
        if not self.active:
            return "break"
        self._wake()
        if self.recovery_field and self.recovery and self.recovery.winfo_exists():
            self._focus_input()
            return "break"
        question = self.config.data.get("security_question", "")
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=COLORS["border"])
        self._harden(win)
        card = tk.Frame(win, bg=COLORS["surface"], padx=px(26), pady=px(22))
        card.pack(fill="both", expand=True, padx=1, pady=1)
        make_label(card, "Recover access", 16, "text", True, bg=COLORS["surface"]).pack(fill="x")
        make_label(card, question, 10, "muted", bg=COLORS["surface"], wraplength=px(400)).pack(fill="x", pady=(px(8), px(14)))
        field = Field(card, "Your answer", True, on_return=lambda: check(), bg=COLORS["surface"])
        field.pack(fill="x")
        row = tk.Frame(card, bg=COLORS["surface"])
        row.pack(fill="x", pady=(px(18), 0))

        def check():
            if not self.active:
                return
            ok, message = False, ""
            if self.guard.blocked:
                message = f"Too many attempts. Try again in {self.guard.remaining()}s."
            else:
                ok = self.config.verify_security_answer(field.get())
                if ok:
                    self.guard.reset()
                else:
                    self.guard.record_failure()
                    message = (
                        f"Too many attempts. Try again in {self.guard.remaining()}s."
                        if self.guard.blocked
                        else "That answer did not match."
                    )
            if ok:
                self._close_recovery()
                self._unlock(recovered=True)
            else:
                field.set_error(message)
                field.clear()
                field.focus()

        Button(row, "Recover", check, "primary", width=px(120)).pack(side="right")
        Button(row, "Cancel", self._close_recovery, "secondary", width=px(100)).pack(side="right", padx=(0, px(10)))
        field.var.trace_add("write", lambda *_a: field.set_error(""))
        win.bind("<Escape>", lambda _e: self._close_recovery())

        win.update_idletasks()
        width, height = max(px(480), win.winfo_reqwidth()), win.winfo_reqheight()
        origin_x, origin_y = self._shake_origin
        x = origin_x + (self.panel_w - width) // 2
        y = origin_y + self.cap_cy - self.cap_h // 2 - height - px(26)
        if y < px(16):
            y = origin_y + self.cap_cy + self.cap_h // 2 + px(26)
        win.geometry(geometry_string(width, height, x, y))
        win.deiconify()
        keep_rounded(win, px(20))
        self.recovery, self.recovery_field = win, field
        self.faded = False
        self._fade_to(1.0)
        field.entry.focus_force()
        return "break"

    def _close_recovery(self):
        win, self.recovery, self.recovery_field = self.recovery, None, None
        try:
            if win and win.winfo_exists():
                win.destroy()
        except tk.TclError:
            pass
        self._focus_input()

    # ------------------------------------------------------------------ teardown
    def _unlock(self, recovered=False, auto_release=False):
        if not self.active:
            return
        self.active = False
        for job in list(self._jobs):
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._jobs.clear()
        for window in (self.recovery, self.panel, self.overlay):
            try:
                if window and window.winfo_exists():
                    window.destroy()
            except Exception:
                pass
        self.on_unlock()
        if recovered and self.on_recovered:
            self.root.after(250, self.on_recovered)
        if auto_release:
            self.root.after(
                300,
                lambda: messagebox.showinfo(
                    APP_NAME,
                    "Your first test lock released itself after 25 seconds.\n\n"
                    "Lock again and type your password to confirm the unlock works. "
                    "After one successful unlock, GhostLock stays locked until you enter a password.",
                ),
            )
