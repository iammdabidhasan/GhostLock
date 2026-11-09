"""GhostLock windows: setup wizard, installer, uninstaller, settings, about and floating widget."""
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

from gl_core import (
    APP_AUTHOR,
    APP_COPYRIGHT,
    APP_NAME,
    APP_PUBLISHER,
    APP_VERSION,
    CONFIG_VERSION,
    INSTALLED_EXE_NAME,
    DEFAULT_IDLE_HIDE_SECONDS,
    DEFAULT_LOCK_OPACITY,
    HOTKEY_PRESETS,
    SHIELD_SECONDS,
    fresh_process_env,
    INTRUDER_AFTER_CHOICES,
    MAX_IDLE_HIDE_SECONDS,
    MAX_LOCK_MESSAGE_LENGTH,
    MAX_LOCK_OPACITY,
    MIN_IDLE_HIDE_SECONDS,
    MIN_LOCK_OPACITY,
    MIN_PASSWORD_LENGTH,
    password_problem,
    ConfigManager,
    app_data_dir,
    create_shortcut,
    current_executable_path,
    desktop_dir,
    install_dir,
    intruders_dir,
    is_another_instance_running,
    is_start_with_windows,
    log_exception,
    password_strength,
    register_uninstall_entry,
    remove_known_shortcuts,
    schedule_install_dir_removal,
    set_start_with_windows,
    start_menu_dir,
    unregister_uninstall_entry,
)
from gl_legacy import KIND_TITLES, CleanupRun, PROCESS, scan_legacy
from gl_shield import HexShield
from gl_security import AttemptLog, delete_all_photos
from gl_system import seconds_since_input
from gl_update import check_latest
from gl_theme import (
    COLORS,
    Button,
    Card,
    Field,
    ProgressBar,
    ScrollFrame,
    Segmented,
    Slider,
    StepDots,
    StrengthMeter,
    TabBar,
    ToggleRow,
    ToolTip,
    center_window,
    chip,
    divider,
    font,
    geometry_string,
    header_block,
    key_chip,
    keep_rounded,
    load_icon,
    primary_screen_geometry,
    make_label,
    px,
    set_window_icon,
    style_titlebar,
    work_area,
)

MENU_ITEMS = (
    ("lock", "Lock Now"),
    ("password", "Change Password"),
    ("settings", "Settings"),
    ("about", "About & Safety"),
    (None, None),
    ("uninstall", "Uninstall GhostLock\u2026"),
    ("exit", "Exit GhostLock"),
)


def verify_with_guard(guard, verifier, value):
    """Run a verifier through the brute-force guard. Returns (ok, error message)."""
    if guard is not None and guard.blocked:
        return False, f"Too many attempts. Try again in {guard.remaining()}s."
    ok = bool(verifier(value))
    if guard is not None:
        if ok:
            guard.reset()
        else:
            guard.record_failure()
            if guard.blocked:
                return False, f"Too many attempts. Try again in {guard.remaining()}s."
    return ok, ""


def open_folder(path):
    try:
        os.makedirs(path, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # noqa: S606 (Windows only)
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


# --------------------------------------------------------------------------- base window
class AppWindow:
    """Themed top-level window that sizes itself to its content."""

    def __init__(self, root, title, min_width=480, min_height=200, resizable=False):
        self.root = root
        self.min_width, self.min_height = min_width, min_height
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.title(title)
        self.window.configure(bg=COLORS["bg"])
        set_window_icon(self.window)
        self.window.resizable(resizable, resizable)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _e: self.close(), add="+")

    def exists(self):
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def show(self, extra_height=0):
        w = self.window
        w.update_idletasks()
        width = max(px(self.min_width), w.winfo_reqwidth())
        height = max(px(self.min_height), w.winfo_reqheight() + extra_height)
        width, height = center_window(w, width, height)
        w.deiconify()
        style_titlebar(w)
        w.lift()
        try:
            w.attributes("-topmost", True)
            w.after(400, lambda: w.winfo_exists() and w.attributes("-topmost", False))
        except tk.TclError:
            pass
        w.focus_force()

    def fit(self):
        """Grow or shrink to the current content, keeping the window centred on screen.

        Needed after content is added once the window is on screen (a progress bar, a "finished" state):
        the window is not resizable, so without this the buttons at the bottom end up cut off.
        """
        if not self.exists():
            return
        w = self.window
        w.update_idletasks()
        width = max(w.winfo_width(), w.winfo_reqwidth(), px(self.min_width))
        height = max(w.winfo_reqheight(), px(self.min_height))
        center_window(w, width, height)

    def start_countdown(self, label, seconds, action, text="This window closes in {n} s."):
        """Show a countdown in ``label`` and run ``action`` when it reaches zero (a button can run it sooner)."""
        self.stop_countdown()

        def tick(n):
            self._count_job = None
            if not self.exists():
                return
            if n <= 0:
                action()
                return
            label.configure(text=text.format(n=n), fg=COLORS["muted"])
            self._count_job = self.window.after(1000, lambda: tick(n - 1))

        tick(int(seconds))

    def stop_countdown(self):
        job = getattr(self, "_count_job", None)
        self._count_job = None
        if job and self.exists():
            try:
                self.window.after_cancel(job)
            except Exception:
                pass

    def focus(self):
        if self.exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()

    def close(self):
        if self.exists():
            self.window.destroy()


def page_frame(window, padx=32, pady=28):
    frame = tk.Frame(window, bg=COLORS["bg"])
    frame.pack(fill="both", expand=True, padx=px(padx), pady=px(pady))
    return frame


def _bullet(parent):
    """Small accent dot (drawn, so it never depends on a font glyph)."""
    size = px(10)
    canvas = tk.Canvas(parent, width=size, height=size, bg=parent.cget("bg"), highlightthickness=0, bd=0)
    canvas.create_oval(1, 1, size - 1, size - 1, fill=COLORS["accent"], outline="")
    return canvas


def button_row(parent, *buttons):
    row = tk.Frame(parent, bg=parent.cget("bg"))
    for index, button_spec in enumerate(buttons):
        text, command, kind = button_spec
        Button(row, text, command, kind).pack(side="left", padx=(0 if index == 0 else px(10), 0))
    return row


# --------------------------------------------------------------------------- first-run setup
SUGGESTED_QUESTIONS = (
    ("First pet's name", "What was the name of my first pet?"),
    ("Birth city", "In which city was I born?"),
    ("Favourite teacher", "What was my favourite teacher's name?"),
    ("First school", "What was the name of my first school?"),
)


class SetupWindow(AppWindow):
    """Friendly wizard: welcome, then one simple question per step."""

    STEP_TITLES = ("YOUR PASSWORD", "BACKUP PASSWORD", "RECOVERY QUESTION")

    def __init__(self, root, config, on_complete, on_cancel):
        super().__init__(root, "GhostLock Setup", min_width=600)
        self.config = config
        self.on_complete = on_complete  # called with test_lock: bool
        self.on_cancel = on_cancel
        self.done = False
        self._finished = False
        self.index = -1  # -1 = welcome page
        self.outer = page_frame(self.window)
        self._build_welcome()
        self._build_wizard()
        self._show_welcome()
        self.show()

    # ------------------------------------------------------------------ welcome
    def _build_welcome(self):
        self.welcome = tk.Frame(self.outer, bg=COLORS["bg"])
        mark = load_icon(self.welcome, "mark", px(104))
        if mark:
            holder = tk.Label(self.welcome, image=mark, bg=COLORS["bg"])
            holder.image = mark
            holder.pack(pady=(px(6), px(14)))
        make_label(self.welcome, "Welcome to GhostLock", 22, "text", True, anchor="center", justify="center").pack(fill="x")
        make_label(
            self.welcome,
            "Keep your screen private when you step away. Setup takes about a minute.",
            10, "muted", anchor="center", justify="center", wraplength=px(420),
        ).pack(fill="x", pady=(px(8), px(22)))  # fmt: skip
        card = Card(self.welcome, padx=24, pady=16)
        card.pack(fill="x")
        for title, text in (
            ("Lock in one move", "Double-click the ghost on your desktop."),
            ("Only you can unlock", "Type your password and press Enter."),
            ("Stays out of the way", "The password box hides itself when you are idle."),
        ):
            row = tk.Frame(card.body, bg=COLORS["surface"])
            row.pack(fill="x", pady=px(7))
            _bullet(row).pack(side="left", anchor="n", padx=(0, px(10)), pady=(px(4), 0))
            col = tk.Frame(row, bg=COLORS["surface"])
            col.pack(side="left", fill="x", expand=True)
            make_label(col, title, 10, "text", True, bg=COLORS["surface"]).pack(fill="x")
            make_label(col, text, 9, "muted", bg=COLORS["surface"], wraplength=px(380)).pack(fill="x")
        footer = tk.Frame(self.welcome, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(px(24), 0))
        Button(footer, "Get started", self._start, "primary", width=px(160)).pack(side="right")

    def _start(self):
        self.index = 0
        self._show_wizard()
        self.pw.focus()

    # ------------------------------------------------------------------ wizard
    def _build_wizard(self):
        self.wizard = tk.Frame(self.outer, bg=COLORS["bg"])
        top = tk.Frame(self.wizard, bg=COLORS["bg"])
        top.pack(fill="x", pady=(0, px(14)))
        self.step_label = make_label(top, "", 9, "accent", True, bg=COLORS["bg"])
        self.step_label.pack(side="left")
        self.dots = StepDots(top, 3)
        self.dots.pack(side="right")

        self.card = Card(self.wizard, padx=26, pady=24)
        self.card.pack(fill="x")
        body = self.card.body

        # page 1: main password
        self.page_password = tk.Frame(body, bg=COLORS["surface"])
        self._heading(self.page_password, "Create your password", "You will type this each time you unlock your screen.")
        self.pw = Field(self.page_password, "Password", True, f"At least {MIN_PASSWORD_LENGTH} characters. A short phrase works well.", self.next)
        self.pw.pack(fill="x")
        self.meter = StrengthMeter(self.page_password)
        self.meter.pack(fill="x", pady=(px(6), px(12)))
        self.pw2 = Field(self.page_password, "Type it again", True, on_return=self.next)
        self.pw2.pack(fill="x")

        # page 2: emergency password
        self.page_backup = tk.Frame(body, bg=COLORS["surface"])
        self._heading(
            self.page_backup,
            "Add a backup password",
            "A second password that also unlocks GhostLock, in case you mistype or forget the first. Write it down somewhere safe.",
        )
        self.em = Field(self.page_backup, "Backup password", True, "Must be different from your main password.", self.next)
        self.em.pack(fill="x")
        self.em2 = Field(self.page_backup, "Type it again", True, on_return=self.next)
        self.em2.pack(fill="x", pady=(px(14), 0))

        # page 3: recovery question
        self.page_recovery = tk.Frame(body, bg=COLORS["surface"])
        self._heading(
            self.page_recovery,
            "Add a recovery question",
            "If you forget both passwords, answer this on the lock screen (press F1) to get back in and choose a new password.",
        )
        make_label(self.page_recovery, "Pick a suggestion or write your own", 9, "muted", True, bg=COLORS["surface"]).pack(fill="x", pady=(0, px(6)))
        chips = tk.Frame(self.page_recovery, bg=COLORS["surface"])
        chips.pack(fill="x", pady=(0, px(14)))
        for index, (short, full) in enumerate(SUGGESTED_QUESTIONS):
            chip(chips, short, lambda f=full: self._use_question(f)).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, px(8)), pady=(0, px(6)))
        self.q = Field(self.page_recovery, "Your question", False, on_return=self.next)
        self.q.pack(fill="x")
        self.a = Field(self.page_recovery, "Your answer", True, "Not case-sensitive. Stored securely, never as plain text.", self.next)
        self.a.pack(fill="x", pady=(px(14), 0))

        for field in (self.pw, self.pw2, self.em, self.em2, self.q, self.a):
            field.var.trace_add("write", lambda *_a, f=field: f.set_error(""))
        self.pw.var.trace_add("write", lambda *_a: self.meter.set(*password_strength(self.pw.get())))

        self.footer = tk.Frame(self.wizard, bg=COLORS["bg"])
        self.footer.pack(fill="x", pady=(px(20), 0))
        self.status = make_label(self.footer, "", 9, "danger", bg=COLORS["bg"], wraplength=px(240))
        self.status.pack(side="left", fill="x", expand=True)
        self.next_button = Button(self.footer, "Continue", self.next, "primary", width=px(140))
        self.next_button.pack(side="right")
        self.back_button = Button(self.footer, "Back", self.back, "secondary", width=px(90))

    def _heading(self, parent, title, text):
        make_label(parent, title, 15, "text", True, bg=COLORS["surface"]).pack(fill="x")
        make_label(parent, text, 10, "muted", bg=COLORS["surface"], wraplength=px(440)).pack(fill="x", pady=(px(4), px(18)))

    def _use_question(self, text):
        self.q.set(text)
        self.a.focus()

    def _show_welcome(self):
        self.wizard.pack_forget()
        self.welcome.pack(fill="both", expand=True)
        self._refit()

    def _show_wizard(self):
        self.welcome.pack_forget()
        self.wizard.pack(fill="both", expand=True)
        self._render()

    def _refit(self):
        """Keep a constant width across steps; only the height follows the content."""
        self.window.update_idletasks()
        center_window(self.window, px(600), self.window.winfo_reqheight())

    def _render(self):
        pages = (self.page_password, self.page_backup, self.page_recovery)
        for page in pages:
            page.pack_forget()
        pages[self.index].pack(fill="x")
        self.step_label.configure(text=f"STEP {self.index + 1} OF 3  ·  {self.STEP_TITLES[self.index]}")
        self.dots.set(self.index)
        self.status.configure(text="")
        self.back_button.pack_forget()
        self.back_button.pack(side="right", padx=(0, px(10)))
        self.next_button.configure_text("Finish setup" if self.index == 2 else "Continue")
        self._refit()

    def back(self):
        if self.index > 0:
            self.index -= 1
            self._render()
            (self.pw, self.em, self.q)[self.index].focus()
        elif self.index == 0:
            self.index = -1
            self._show_welcome()

    def next(self):
        checks = (self._check_password, self._check_backup, self._check_recovery)
        problem = checks[self.index]()
        if problem:
            field, message = problem
            field.set_error(message)
            field.focus()
            return
        if self.index < 2:
            self.index += 1
            self._render()
            (self.pw, self.em, self.q)[self.index].focus()
            return
        try:
            self.config.configure(self.pw.get(), self.em.get(), self.q.get(), self.a.get())
        except Exception as exc:
            log_exception("Setup save failed", exc)
            self.status.configure(text=f"Could not save your setup: {exc}")
            return
        self._show_done()

    def _check_password(self):
        problem = password_problem(self.pw.get())
        if problem:
            return self.pw, problem
        if self.pw.get() != self.pw2.get():
            return self.pw2, "The two passwords are not the same."
        return None

    def _check_backup(self):
        problem = password_problem(self.em.get())
        if problem:
            return self.em, problem
        if self.em.get() == self.pw.get():
            return self.em, "Choose something different from your main password."
        if self.em.get() != self.em2.get():
            return self.em2, "The two passwords are not the same."
        return None

    def _check_recovery(self):
        if len(self.q.get().strip()) < 6:
            return self.q, "Write a question you will recognise later."
        if len(self.a.get().strip()) < 2:
            return self.a, "Please enter an answer."
        return None

    # ------------------------------------------------------------------ done
    def _show_done(self):
        self.done = True
        for child in self.outer.winfo_children():
            child.destroy()
        mark = load_icon(self.outer, "mark", px(104))
        if mark:
            holder = tk.Label(self.outer, image=mark, bg=COLORS["bg"])
            holder.image = mark
            holder.pack(pady=(px(6), px(14)))
        make_label(self.outer, "You are all set", 22, "text", True, anchor="center", justify="center").pack(fill="x")
        make_label(
            self.outer,
            "GhostLock is ready. The ghost now floats on your desktop.",
            10, "muted", anchor="center", justify="center", wraplength=px(420),
        ).pack(fill="x", pady=(px(8), px(20)))  # fmt: skip
        card = Card(self.outer, padx=24, pady=14)
        card.pack(fill="x")
        for title, text in (
            ("Click the ghost", "Opens the menu (settings, change password, uninstall)."),
            ("Double-click the ghost", "Locks your screen right away."),
            ("Type your password + Enter", "Unlocks. Forgot it? Press F1 on the lock screen."),
        ):
            row = tk.Frame(card.body, bg=COLORS["surface"])
            row.pack(fill="x", pady=px(6))
            _bullet(row).pack(side="left", anchor="n", padx=(0, px(10)), pady=(px(4), 0))
            col = tk.Frame(row, bg=COLORS["surface"])
            col.pack(side="left", fill="x", expand=True)
            make_label(col, title, 10, "text", True, bg=COLORS["surface"]).pack(fill="x")
            make_label(col, text, 9, "muted", bg=COLORS["surface"], wraplength=px(380)).pack(fill="x")
        make_label(
            self.outer,
            "Your first lock is a safe test: it unlocks itself after 25 seconds.",
            9, "warning", anchor="center", justify="center", wraplength=px(420),
        ).pack(fill="x", pady=(px(16), 0))  # fmt: skip
        footer = tk.Frame(self.outer, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(px(18), 0))
        Button(footer, "Start using GhostLock", lambda: self._finish(False), "primary", width=px(210)).pack(side="right")
        Button(footer, "Try a test lock", lambda: self._finish(True), "secondary", width=px(150)).pack(side="right", padx=(0, px(10)))
        self._refit()

    def _finish(self, test_lock):
        if self._finished:
            return
        self._finished = True
        if self.exists():
            self.window.destroy()
        self.on_complete(test_lock)

    def close(self):
        if self._finished or not self.exists():
            return
        if self.done:
            self._finish(False)
            return
        self._finished = True
        self.window.destroy()
        self.on_cancel()


# --------------------------------------------------------------------------- identity gate
class AuthGateWindow(AppWindow):
    """Asks for a password (or the recovery answer) before sensitive actions."""

    def __init__(self, root, config, guard, on_success, on_cancel, title="Confirm it is you", message=None):
        super().__init__(root, "GhostLock", min_width=480)
        self.config, self.guard = config, guard
        self.on_success, self.on_cancel = on_success, on_cancel
        self.finished = False
        self.recovery_mode = False

        frame = page_frame(self.window)
        header_block(
            frame,
            title,
            message or "Enter your main or emergency password to continue.",
            icon_px=48,
            title_size=18,
        ).pack(fill="x")
        card = Card(frame, padx=22, pady=20)
        card.pack(fill="x", pady=(px(22), 0))
        self.password = Field(card.body, "Password", True, on_return=self.submit)
        self.password.pack(fill="x")
        self.question_label = make_label(
            card.body, self.config.data.get("security_question", ""), 10, "text", bg=COLORS["surface"], wraplength=px(400)
        )
        self.answer = Field(card.body, "Recovery answer", True, on_return=self.submit)
        self.switch = tk.Label(
            card.body, text="Forgot your passwords? Use the recovery answer", bg=COLORS["surface"],
            fg=COLORS["accent"], font=font(9), cursor="hand2",
        )  # fmt: skip
        self.switch.pack(anchor="w", pady=(px(12), 0))
        self.switch.bind("<Button-1>", lambda _e: self._toggle_mode())
        for field in (self.password, self.answer):
            field.var.trace_add("write", lambda *_a, f=field: f.set_error(""))

        footer = tk.Frame(frame, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(px(20), 0))
        self.continue_button = Button(footer, "Continue", self.submit, "primary", width=px(130))
        self.continue_button.pack(side="right")
        Button(footer, "Cancel", self.close, "secondary", width=px(100)).pack(side="right", padx=(0, px(10)))
        self.show()
        self.password.focus()

    def _toggle_mode(self):
        self.recovery_mode = not self.recovery_mode
        if self.recovery_mode:
            self.password.pack_forget()
            self.question_label.pack(fill="x", pady=(0, px(10)), before=self.switch)
            self.answer.pack(fill="x", before=self.switch)
            self.switch.configure(text="Use a password instead")
            self.answer.focus()
        else:
            self.question_label.pack_forget()
            self.answer.pack_forget()
            self.password.pack(fill="x", before=self.switch)
            self.switch.configure(text="Forgot your passwords? Use the recovery answer")
            self.password.focus()

    def submit(self):
        field = self.answer if self.recovery_mode else self.password
        verifier = self.config.verify_security_answer if self.recovery_mode else self.config.verify_any_unlock_secret
        field.set_error("")
        ok, message = verify_with_guard(self.guard, verifier, field.get())
        if ok:
            self.finished = True
            self.window.destroy()
            self.on_success()
            return
        field.set_error(message or ("That answer did not match." if self.recovery_mode else "That password did not match."))
        field.clear()
        field.focus()

    def close(self):
        if not self.exists():
            return
        self.window.destroy()
        if not self.finished:
            self.on_cancel()


# --------------------------------------------------------------------------- change password
class ChangePasswordWindow(AppWindow):
    def __init__(self, root, config, guard, recovery=False):
        super().__init__(root, "Change Password", min_width=480)
        self.config, self.guard, self.recovery = config, guard, recovery
        frame = page_frame(self.window)
        header_block(
            frame,
            "Set a new password" if recovery else "Change password",
            "Recovery confirmed. Choose a new main password." if recovery else "Update the password you use to unlock.",
            icon_px=48,
            title_size=18,
        ).pack(fill="x")
        card = Card(frame, padx=22, pady=20)
        card.pack(fill="x", pady=(px(22), 0))
        self.current = Field(card.body, "Current or emergency password", True, on_return=self.save)
        if not recovery:
            self.current.pack(fill="x", pady=(0, px(14)))
        self.new = Field(card.body, "New password", True, f"At least {MIN_PASSWORD_LENGTH} characters.", self.save)
        self.new.pack(fill="x")
        self.meter = StrengthMeter(card.body)
        self.meter.pack(fill="x", pady=(px(6), px(12)))
        self.confirm = Field(card.body, "Confirm new password", True, on_return=self.save)
        self.confirm.pack(fill="x")
        for field in (self.current, self.new, self.confirm):
            field.var.trace_add("write", lambda *_a, f=field: f.set_error(""))
        self.new.var.trace_add("write", lambda *_a: self.meter.set(*password_strength(self.new.get())))

        footer = tk.Frame(frame, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(px(20), 0))
        self.status = make_label(footer, "", 10, "success", bg=COLORS["bg"])
        self.status.pack(side="left")
        self.save_button = Button(footer, "Save password", self.save, "primary", width=px(150))
        self.save_button.pack(side="right")
        Button(footer, "Cancel", self.close, "secondary", width=px(100)).pack(side="right", padx=(0, px(10)))
        self.show()
        (self.new if recovery else self.current).focus()

    def save(self):
        if not self.recovery:
            ok, message = verify_with_guard(self.guard, self.config.verify_any_unlock_secret, self.current.get())
            if not ok:
                self.current.set_error(message or "That password did not match.")
                self.current.focus()
                return
        new = self.new.get()
        problem = password_problem(new)
        if problem:
            self.new.set_error(problem)
            self.new.focus()
            return
        if self.config.verify_emergency_password(new):
            self.new.set_error("This matches your emergency password. Choose a different one.")
            self.new.focus()
            return
        if new != self.confirm.get():
            self.confirm.set_error("The passwords do not match.")
            self.confirm.focus()
            return
        try:
            self.config.set_password(new)
        except Exception as exc:
            log_exception("Password save failed", exc)
            self.current.set_error(f"Could not save: {exc}")
            return
        self.save_button.set_enabled(False)
        self.status.configure(text="✓  Password updated")
        self.window.after(900, self.close)


# --------------------------------------------------------------------------- activity and photos
def _wrong_label(kind):
    return "Wrong recovery answer" if kind == "recovery" else "Wrong password"


def _stamp(timestamp):
    return time.strftime("%d %b  %H:%M", time.localtime(timestamp))


def _load_thumbnail(path, target=150):
    """A small Tk image of a saved photo, or None when it cannot be read."""
    try:
        image = tk.PhotoImage(file=str(path))
        step = max(1, image.width() // target)
        return image.subsample(step, step) if step > 1 else image
    except Exception:
        return None


def _open_file(path):
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # noqa: S606 (Windows only)
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def _thumbnail_label(parent, path, images, bg):
    """A clickable photo preview (click opens the full picture)."""
    image = _load_thumbnail(path)
    if image is None:
        return None
    images.append(image)  # Tk forgets images that Python no longer references
    label = tk.Label(parent, image=image, bg=bg, cursor="hand2", bd=0)
    label.bind("<Button-1>", lambda _e, f=path: _open_file(f))
    return label


class ActivityWindow(AppWindow):
    """Every wrong password or recovery answer, with the photo if one was taken."""

    MAX_ROWS = 40

    def __init__(self, root):
        super().__init__(root, "GhostLock activity", min_width=580, resizable=True)
        self.log = AttemptLog()
        self.images = []
        frame = page_frame(self.window, pady=24)
        header_block(frame, "Activity", "Wrong passwords and recovery answers, newest first.", icon_px=44, title_size=18).pack(fill="x")
        footer = tk.Frame(frame, bg=COLORS["bg"])
        footer.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.scroll = ScrollFrame(frame)
        self.scroll.pack(side="top", fill="both", expand=True, pady=(px(16), 0))
        self.list = self.scroll.inner
        self.scroll.finalize()
        self.scroll.canvas.configure(height=px(380), width=px(520))
        Button(footer, "Close", self.close, "primary", width=px(110)).pack(side="right")
        Button(footer, "Open photos folder", lambda: open_folder(intruders_dir()), "secondary").pack(side="right", padx=(0, px(10)))
        Button(footer, "Delete everything\u2026", self._delete_all, "secondary").pack(side="left")
        self._fill()
        self.show()

    def _fill(self):
        for child in self.list.winfo_children():
            child.destroy()
        self.images.clear()
        entries = list(reversed(self.log.entries[-self.MAX_ROWS :]))
        if not entries:
            make_label(self.list, "Nothing to show. No wrong attempts have been recorded.", 10, "muted", bg=COLORS["bg"]).pack(fill="x", pady=px(8))
            return
        for entry in entries:
            card = Card(self.list, padx=18, pady=12)
            card.pack(fill="x", pady=(0, px(10)))
            row = tk.Frame(card.body, bg=COLORS["surface"])
            row.pack(fill="x")
            text = tk.Frame(row, bg=COLORS["surface"])
            text.pack(side="left", fill="x", expand=True)
            make_label(text, _wrong_label(entry.get("kind")), 10, "text", True, bg=COLORS["surface"]).pack(fill="x")
            make_label(text, _stamp(entry["t"]), 9, "muted", bg=COLORS["surface"]).pack(fill="x")
            photo = entry.get("photo")
            if photo and (intruders_dir() / photo).exists():
                label = _thumbnail_label(row, intruders_dir() / photo, self.images, COLORS["surface"])
                if label is not None:
                    label.pack(side="right")

    def _delete_all(self):
        if messagebox.askyesno(APP_NAME, "Delete the activity log and every intruder photo?\nThis cannot be undone.", parent=self.window):
            delete_all_photos()
            self.log.clear()
            self._fill()


class AwayReportWindow(AppWindow):
    """Shown after unlocking when someone got the password wrong while you were away."""

    def __init__(self, root, entries, open_activity):
        super().__init__(root, "While you were away", min_width=540)
        self.images = []
        count = len(entries)
        frame = page_frame(self.window)
        header_block(
            frame,
            "While you were away",
            f"{count} wrong attempt{'s' if count != 1 else ''} to unlock \u00b7 last at {time.strftime('%H:%M', time.localtime(entries[-1]['t']))}",
            icon_px=52,
            title_size=20,
        ).pack(fill="x")
        photos = [e for e in entries if e.get("photo") and (intruders_dir() / e["photo"]).exists()][-3:]
        if photos:
            card = Card(frame, padx=18, pady=14)
            card.pack(fill="x", pady=(px(18), 0))
            row = tk.Frame(card.body, bg=COLORS["surface"])
            row.pack(fill="x")
            for entry in photos:
                cell = tk.Frame(row, bg=COLORS["surface"])
                cell.pack(side="left", padx=(0, px(12)))
                label = _thumbnail_label(cell, intruders_dir() / entry["photo"], self.images, COLORS["surface"])
                if label is not None:
                    label.pack()
                    make_label(cell, _stamp(entry["t"]), 8, "muted", bg=COLORS["surface"]).pack(pady=(px(4), 0))
        else:
            make_label(frame, "No photo was taken. You can turn intruder photos on in Settings > Activity.", 9, "muted", bg=COLORS["bg"], wraplength=px(460)).pack(fill="x", pady=(px(16), 0))
        footer = tk.Frame(frame, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(px(20), 0))
        Button(footer, "Dismiss", self.close, "primary", width=px(120)).pack(side="right")
        Button(footer, "Open activity", lambda: (self.close(), open_activity()), "secondary", width=px(150)).pack(side="right", padx=(0, px(10)))
        self.show()


class AutoLockWarning:
    """A small notice before an automatic lock. Moving the mouse or pressing a key cancels it."""

    def __init__(self, root, seconds, on_lock, on_cancel=None, idle=seconds_since_input):
        self.root, self.on_lock, self.on_cancel, self.idle = root, on_lock, on_cancel, idle
        self.deadline = time.monotonic() + seconds
        self.done = False
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.configure(bg=COLORS["border"])
        try:
            self.window.attributes("-topmost", True)
        except tk.TclError:
            pass
        inner = tk.Frame(self.window, bg=COLORS["surface"], padx=px(22), pady=px(14))
        inner.pack(padx=1, pady=1)
        self.title = make_label(inner, "", 11, "text", True, bg=COLORS["surface"])
        self.title.pack()
        make_label(inner, "Move the mouse or press a key to stay unlocked.", 9, "muted", bg=COLORS["surface"]).pack(pady=(px(3), 0))
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.window.geometry(geometry_string(width, height, (screen_w - width) // 2, int(screen_h * 0.82)))
        self._tick()

    def _tick(self):
        if self.done:
            return
        if self.idle() < 1.5:  # the person is back
            self._finish(cancelled=True)
            return
        left = self.deadline - time.monotonic()
        if left <= 0:
            self._finish(cancelled=False)
            return
        self.title.configure(text=f"Locking in {int(left) + 1}\u2026")
        self.window.after(250, self._tick)

    def _finish(self, cancelled):
        self.done = True
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        callback = self.on_cancel if cancelled else self.on_lock
        if callback:
            callback()


# --------------------------------------------------------------------------- settings
class SettingsWindow(AppWindow):
    TABS = (("general", "General"), ("lock", "Lock screen"), ("security", "Security"), ("activity", "Activity"))
    CONTENT_HEIGHT = 430
    AUTO_LOCK_CHOICES = ((0, "Off"), (1, "1 min"), (5, "5 min"), (10, "10 min"), (15, "15 min"), (30, "30 min"))
    SHIELD_PATTERN_LABELS = (("ripple", "Ripple"), ("sweep", "Sweep"), ("scan", "Scan down"), ("random", "Random"))
    SHIELD_TONE_LABELS = (("teal", "Teal-blue"), ("ice", "Ice"), ("amber", "Amber"))
    KEEP_CHOICES = ((7, "7 days"), (30, "30 days"), (90, "90 days"))

    def __init__(self, root, config, guard, on_change_password=None, on_saved=None, camera=None, on_open_activity=None):
        super().__init__(root, "GhostLock Settings", min_width=580, resizable=True)
        self.config, self.guard = config, guard
        self.on_change_password = on_change_password
        self.on_saved, self.camera, self.on_open_activity = on_saved, camera, on_open_activity
        self.panes = {}
        self.test_images = []

        outer = tk.Frame(self.window, bg=COLORS["bg"])
        outer.pack(fill="both", expand=True)
        head = tk.Frame(outer, bg=COLORS["bg"])
        head.pack(fill="x", padx=px(32), pady=(px(24), 0))
        header_block(head, "Settings", "Make GhostLock work the way you like.", icon_px=44, title_size=18).pack(fill="x")
        tabs_holder = tk.Frame(outer, bg=COLORS["bg"])
        tabs_holder.pack(fill="x", padx=px(32), pady=(px(14), 0))
        self.tabbar = TabBar(tabs_holder, self.TABS, self._select)
        self.tabbar.pack(anchor="w")
        divider(outer).pack_configure(padx=px(32))

        footer = tk.Frame(outer, bg=COLORS["bg"])
        footer.pack(side="bottom", fill="x", padx=px(32), pady=(px(10), px(22)))
        self.scroll = ScrollFrame(outer)
        self.scroll.pack(side="top", fill="both", expand=True, padx=(px(32), px(24)), pady=(px(16), 0))
        page = self.scroll.inner

        self._build_general(self._pane(page, "general"))
        self._build_lock(self._pane(page, "lock"))
        self._build_security(self._pane(page, "security"))
        self._build_activity(self._pane(page, "activity"))
        self.scroll.finalize()

        self.status = make_label(footer, "", 9, "danger", bg=COLORS["bg"], wraplength=px(260))
        self.status.pack(side="left", fill="x", expand=True)
        Button(footer, "Save", self.save, "primary", width=px(120)).pack(side="right")
        Button(footer, "Cancel", self.close, "secondary", width=px(100)).pack(side="right", padx=(0, px(10)))

        self.scroll.canvas.configure(height=px(self.CONTENT_HEIGHT), width=px(520))
        self.tabbar.select("general")
        self.window.minsize(px(580), px(440))
        self.show()

    def _pane(self, parent, key):
        frame = tk.Frame(parent, bg=COLORS["bg"])
        self.panes[key] = frame
        return frame

    def _select(self, key):
        for name, frame in self.panes.items():
            frame.pack_forget()
        self.panes[key].pack(fill="x")
        self.scroll.canvas.yview_moveto(0)

    def _card(self, parent, title=None, padx=22, pady=18, last=False):
        card = Card(parent, padx=padx, pady=pady)
        card.pack(fill="x", pady=(0, 0 if last else px(14)))
        if title:
            make_label(card.body, title, 9, "accent", True, bg=COLORS["surface"]).pack(fill="x", pady=(0, px(10)))
        return card.body

    # ------------------------------------------------------------------ tabs
    @staticmethod
    def _nearest(choices, value):
        """The choice closest to a stored number (older or hand-edited configs may hold other values)."""
        return min((key for key, _label in choices), key=lambda key: abs(key - int(value)))

    def _build_general(self, pane):
        cfg = self.config
        body = self._card(pane, "STARTUP")
        self.startup = ToggleRow(body, "Start GhostLock with Windows", "The ghost appears on your desktop as soon as you sign in.", is_start_with_windows())
        self.startup.pack(fill="x")

        body = self._card(pane, "SHORTCUTS")
        self.double_click = ToggleRow(
            body, "Double-click the ghost to lock", "Locks right away. A single click still opens the menu.",
            bool(cfg.setting("double_click_lock")),
        )  # fmt: skip
        self.double_click.pack(fill="x")
        divider(body, px(14))
        self.hotkey_on = ToggleRow(body, "Lock with a keyboard shortcut", "Works from any program, even when GhostLock is in the background.", bool(cfg.setting("hotkey_enabled")))
        self.hotkey_on.pack(fill="x")
        self.hotkey = Segmented(body, [(k, k) for k in HOTKEY_PRESETS], cfg.setting("hotkey"))
        self.hotkey.pack(anchor="w", pady=(px(10), 0))

        body = self._card(pane, "AUTOMATIC LOCKING")
        make_label(body, "Lock automatically when I am away", 10, "text", True, bg=COLORS["surface"]).pack(fill="x")
        make_label(body, "After this long without mouse or keyboard use. You get a few seconds' warning first.", 9, "muted", bg=COLORS["surface"], wraplength=px(440)).pack(fill="x", pady=(px(2), px(10)))
        self.auto_lock = Segmented(body, self.AUTO_LOCK_CHOICES, self._nearest(self.AUTO_LOCK_CHOICES, cfg.setting("auto_lock_minutes")))
        self.auto_lock.pack(anchor="w")
        divider(body, px(14))
        self.lock_on_wake = ToggleRow(body, "Lock when the PC wakes up", "After sleep or hibernate, the screen is covered again at once.", bool(cfg.setting("lock_on_wake")))
        self.lock_on_wake.pack(fill="x")

        body = self._card(pane, "UPDATES", last=True)
        self.check_updates = ToggleRow(
            body, "Check for new versions", "Once a week GhostLock asks GitHub for the latest version. Nothing about you is sent. Off by default.",
            bool(cfg.setting("check_updates")),
        )  # fmt: skip
        self.check_updates.pack(fill="x")

    def _build_lock(self, pane):
        body = self._card(pane, "SCREEN DIMMING")
        make_label(body, "How dark the screen gets while locked", 10, "text", True, bg=COLORS["surface"]).pack(fill="x")
        make_label(body, "Lower values let you keep an eye on downloads or renders.", 9, "muted", bg=COLORS["surface"], wraplength=px(440)).pack(fill="x", pady=(px(2), 0))
        self.opacity = Slider(
            body, round(MIN_LOCK_OPACITY * 100), round(MAX_LOCK_OPACITY * 100),
            float(self.config.data.get("lock_opacity", 0.6)) * 100, formatter=lambda v: f"{int(round(v))}%",
        )  # fmt: skip
        self.opacity.pack(fill="x", pady=(px(8), 0))

        body = self._card(pane, "PASSWORD BOX", last=False)
        make_label(body, "Hide it when you are not using the computer", 10, "text", True, bg=COLORS["surface"]).pack(fill="x")
        make_label(
            body,
            "After this many seconds without mouse or keyboard activity, the password box disappears completely. Move the mouse or press a key and it returns.",
            9, "muted", bg=COLORS["surface"], wraplength=px(440),
        ).pack(fill="x", pady=(px(2), 0))  # fmt: skip
        self.idle = Slider(
            body, MIN_IDLE_HIDE_SECONDS, MAX_IDLE_HIDE_SECONDS,
            float(self.config.data.get("idle_hide_seconds", DEFAULT_IDLE_HIDE_SECONDS)), formatter=lambda v: f"{int(round(v))} s",
        )  # fmt: skip
        self.idle.pack(fill="x", pady=(px(8), 0))

        body = self._card(pane, "SHIELD ANIMATION", last=False)
        cfg = self.config
        self.shield_on = ToggleRow(body, "Hexagon shield when locking", "A grid of hexagons forms from the ghost, covers the screen and fades away.", bool(cfg.setting("shield_enabled")))
        self.shield_on.pack(fill="x")
        make_label(body, "Pattern", 10, "text", True, bg=COLORS["surface"]).pack(fill="x", pady=(px(10), px(4)))
        self.shield_pattern = Segmented(body, self.SHIELD_PATTERN_LABELS, cfg.setting("shield_pattern"))
        self.shield_pattern.pack(anchor="w")
        make_label(body, "Color", 10, "text", True, bg=COLORS["surface"]).pack(fill="x", pady=(px(10), px(4)))
        self.shield_tone = Segmented(body, self.SHIELD_TONE_LABELS, cfg.setting("shield_tone"))
        self.shield_tone.pack(anchor="w")
        make_label(body, "Time to cover the screen", 10, "text", True, bg=COLORS["surface"]).pack(fill="x", pady=(px(10), px(4)))
        self.shield_seconds = Segmented(body, [(n, f"{n} s") for n in SHIELD_SECONDS], self._nearest([(n, "") for n in SHIELD_SECONDS], cfg.setting("shield_seconds")))
        self.shield_seconds.pack(anchor="w")
        Button(body, "Preview", self._preview_shield, "secondary", size=9, height=32).pack(anchor="w", pady=(px(12), 0))

        body = self._card(pane, "MESSAGE", last=False)
        self.message = Field(
            body, "Text inside the password box", False,
            f"Shown instead of \u201cEnter password\u201d, for example \u201cBack in 10 min\u201d. Up to {MAX_LOCK_MESSAGE_LENGTH} characters.",
        )  # fmt: skip
        self.message.set(self.config.setting("lock_message"))
        self.message.pack(fill="x")
        row = tk.Frame(pane, bg=COLORS["bg"])
        row.pack(fill="x", pady=(px(12), 0))
        Button(row, "Reset to defaults", self._reset_lock, "secondary", size=9, height=32).pack(side="left")

    def _preview_shield(self):
        """Plays the animation with the options as they are now (not yet saved). Click to stop it."""
        if getattr(self, "_shield_preview", None) is not None:
            return
        try:
            sx, sy, sw, sh = primary_screen_geometry(self.root)
            self._shield_preview = HexShield(
                self.root, (sx, sy, sw, sh), (sx + sw // 2, sy + sh // 2), self.shield_pattern.get(), self.shield_tone.get(),
                self.shield_seconds.get(), cell=max(14, sw / 34), backdrop=True,
                on_done=lambda: setattr(self, "_shield_preview", None),
                on_click=lambda _e: self._shield_preview and self._shield_preview.close(),
            )  # fmt: skip
        except Exception as exc:
            self._shield_preview = None
            log_exception("Shield preview failed", exc)

    def _reset_lock(self):
        self.shield_on.toggle.set(True)
        self.shield_pattern.set("ripple")
        self.shield_tone.set("teal")
        self.shield_seconds.set(2)
        self.opacity.set(round(DEFAULT_LOCK_OPACITY * 100))
        self.idle.set(DEFAULT_IDLE_HIDE_SECONDS)
        self.message.set("")

    def _build_security(self, pane):
        body = self._card(pane, "MAIN PASSWORD")
        make_label(body, "The password you type to unlock your screen.", 10, "text", bg=COLORS["surface"]).pack(fill="x", pady=(0, px(10)))
        Button(
            body, "Change password…", lambda: self.on_change_password and self.on_change_password(), "secondary", width=px(180)
        ).pack(anchor="w")

        body = self._card(pane, "BACKUP AND RECOVERY", last=True)
        make_label(
            body, "Leave a box empty to keep what you have now.", 9, "muted", bg=COLORS["surface"]
        ).pack(fill="x", pady=(0, px(12)))
        self.em = Field(body, "New backup password", True)
        self.em.pack(fill="x")
        self.em2 = Field(body, "Type the backup password again", True)
        self.em2.pack(fill="x", pady=(px(12), 0))
        divider(body, px(16))
        self.q = Field(body, "Recovery question", False)
        self.q.set(self.config.data.get("security_question", ""))
        self.q.pack(fill="x")
        self.a = Field(body, "New recovery answer", True, "Needed whenever you change the question.")
        self.a.pack(fill="x", pady=(px(12), 0))
        divider(body, px(16))
        self.auth = Field(body, "Your current password", True, "Needed only when you change the backup password or recovery question.", self.save)
        self.auth.pack(fill="x")
        for field in (self.em, self.em2, self.q, self.a, self.auth):
            field.var.trace_add("write", lambda *_a, f=field: f.set_error(""))

    def _build_activity(self, pane):
        cfg = self.config
        body = self._card(pane, "INTRUDER PHOTO")
        self.camera_on = ToggleRow(
            body, "Take a photo after wrong passwords",
            "Uses your webcam. Photos stay on this PC, are never uploaded, and old ones are deleted automatically. Use it only on your own computer.",
            bool(cfg.setting("intruder_camera")),
        )  # fmt: skip
        self.camera_on.pack(fill="x")
        ready = bool(self.camera and self.camera.available())
        self.camera_note = make_label(
            body,
            "Camera support is ready." if ready else "This build has no camera support yet. Build with OpenCV (build.ps1 does this by default) to use it.",
            9, "success" if ready else "warning", bg=COLORS["surface"], wraplength=px(440),
        )  # fmt: skip
        self.camera_note.pack(fill="x", pady=(px(8), 0))
        divider(body, px(14))
        make_label(body, "Start taking photos at", 10, "text", True, bg=COLORS["surface"]).pack(fill="x")
        self.after_n = Segmented(
            body, [(n, ("1st", "2nd", "3rd")[n - 1] + " wrong attempt") for n in INTRUDER_AFTER_CHOICES], cfg.setting("intruder_after")
        )
        self.after_n.pack(anchor="w", pady=(px(8), 0))
        make_label(body, "Keep photos for", 10, "text", True, bg=COLORS["surface"]).pack(fill="x", pady=(px(14), 0))
        self.keep_days = Segmented(body, self.KEEP_CHOICES, self._nearest(self.KEEP_CHOICES, cfg.setting("intruder_keep_days")))
        self.keep_days.pack(anchor="w", pady=(px(8), 0))
        row = tk.Frame(body, bg=COLORS["surface"])
        row.pack(fill="x", pady=(px(16), 0))
        Button(row, "Test camera", self._test_camera, "secondary", size=9, height=32).pack(side="left")
        self.test_status = make_label(row, "", 9, "muted", bg=COLORS["surface"], wraplength=px(300))
        self.test_status.pack(side="left", padx=(px(12), 0))
        self.test_preview = tk.Label(body, bg=COLORS["surface"], bd=0)

        body = self._card(pane, "ACTIVITY LOG", last=True)
        self.away_report = ToggleRow(
            body, "Show a summary when I unlock", "If someone got the password wrong while you were away, you see when, and the photo.",
            bool(cfg.setting("show_away_report")),
        )  # fmt: skip
        self.away_report.pack(fill="x")
        row = tk.Frame(body, bg=COLORS["surface"])
        row.pack(fill="x", pady=(px(14), 0))
        Button(row, "View activity", lambda: self.on_open_activity and self.on_open_activity(), "secondary", size=9, height=32).pack(side="left")
        Button(row, "Open photos folder", lambda: open_folder(intruders_dir()), "secondary", size=9, height=32).pack(side="left", padx=(px(10), 0))

    def _test_camera(self):
        if not (self.camera and self.camera.available()):
            self.test_status.configure(text="No camera support in this build.", fg=COLORS["warning"])
            return
        self.test_status.configure(text="Taking a test photo\u2026", fg=COLORS["muted"])
        path = intruders_dir() / "test-photo.png"
        result = {}

        def work():
            try:
                result["ok"] = bool(self.camera.capture(path))
            except Exception as exc:
                log_exception("Camera test failed", exc)
                result["ok"] = False

        threading.Thread(target=work, daemon=True).start()
        self._await(result, lambda: self._test_done(result["ok"], path))

    def _await(self, result, done):
        """Poll a worker thread from the UI thread (Tk must not be touched from other threads)."""
        if not self.exists():
            return
        if "ok" in result:
            done()
        else:
            self.window.after(150, lambda: self._await(result, done))

    def _test_done(self, ok, path):
        if not ok:
            self.test_status.configure(
                text="Could not use the camera. Check Windows Settings > Privacy > Camera > \u201cLet desktop apps access your camera\u201d.",
                fg=COLORS["danger"],
            )
            return
        self.test_status.configure(text="The camera works. This is what GhostLock sees.", fg=COLORS["success"])
        image = _load_thumbnail(path, 220)
        try:
            path.unlink()
        except OSError:
            pass
        if image is not None:
            self.test_images = [image]
            self.test_preview.configure(image=image)
            self.test_preview.pack(anchor="w", pady=(px(10), 0))

    # ------------------------------------------------------------------ saving
    def save(self):
        emergency, confirm = self.em.get(), self.em2.get()
        question, answer = self.q.get().strip(), self.a.get().strip()
        old_question = self.config.data.get("security_question", "")
        change_emergency = bool(emergency or confirm)
        change_recovery = question != old_question or bool(answer)

        if change_emergency or change_recovery:
            if change_emergency:
                if len(emergency) < MIN_PASSWORD_LENGTH:
                    return self._error(self.em, f"Please use at least {MIN_PASSWORD_LENGTH} characters.")
                if emergency != confirm:
                    return self._error(self.em2, "The two backup passwords are not the same.")
                if self.config.verify_password(emergency):
                    return self._error(self.em, "Choose something different from your main password.")
            if change_recovery:
                if len(question) < 6:
                    return self._error(self.q, "Write a question you will recognise later.")
                if len(answer) < 2:
                    return self._error(self.a, "Enter a new answer to change the recovery question.")
            ok, message = verify_with_guard(self.guard, self.config.verify_any_unlock_secret, self.auth.get())
            if not ok:
                return self._error(self.auth, message or "That password is not correct.")
            try:
                self.config.update_credentials(
                    emergency=emergency if change_emergency else None,
                    question=question if change_recovery else None,
                    answer=answer if change_recovery else None,
                )
            except Exception as exc:
                log_exception("Settings save failed", exc)
                self.status.configure(text=f"Could not save: {exc}")
                return

        if self.camera_on.get() and not self.config.setting("intruder_camera"):
            if not messagebox.askyesno(
                "Turn on intruder photos?",
                "GhostLock will use your webcam to take a photo after wrong passwords.\n\n"
                "Photos are saved only on this PC and are never uploaded. The camera light will switch on briefly. "
                "Only use this on your own computer.\n\nTurn it on?",
                parent=self.window,
            ):
                self.camera_on.toggle.set(False)
                self.tabbar.select("activity")
                return
        try:
            self.config.update_settings(
                lock_opacity=self.opacity.get() / 100,
                idle_hide_seconds=self.idle.get(),
                double_click_lock=self.double_click.get(),
                hotkey_enabled=self.hotkey_on.get(),
                hotkey=self.hotkey.get(),
                auto_lock_minutes=self.auto_lock.get(),
                shield_enabled=self.shield_on.get(),
                shield_pattern=self.shield_pattern.get(),
                shield_tone=self.shield_tone.get(),
                shield_seconds=self.shield_seconds.get(),
                lock_on_wake=self.lock_on_wake.get(),
                check_updates=self.check_updates.get(),
                lock_message=self.message.get(),
                intruder_camera=self.camera_on.get(),
                intruder_after=self.after_n.get(),
                intruder_keep_days=self.keep_days.get(),
                show_away_report=self.away_report.get(),
            )
        except Exception as exc:
            log_exception("Preference save failed", exc)
            self.status.configure(text=f"Could not save: {exc}")
            return
        wanted = self.startup.get()
        if wanted != is_start_with_windows():
            if not set_start_with_windows(wanted):
                self.status.configure(text="Saved, but Windows would not change the startup setting.")
                self._select("general")
                self.tabbar.select("general")
                return
        try:
            self.config.set_start_with_windows(wanted)
        except Exception:
            pass
        notice = self.on_saved() if self.on_saved else ""
        if notice:  # e.g. the shortcut is taken by another program: say so instead of closing silently
            self.status.configure(text=f"Saved. {notice}", fg=COLORS["warning"])
            self._select("general")
            self.tabbar.select("general")
            return
        self.close()

    def _error(self, field, message):
        self.tabbar.select("security")
        field.set_error(message)
        field.focus()
        return None


# --------------------------------------------------------------------------- about
class AboutWindow(AppWindow):
    def __init__(self, root, config=None):
        super().__init__(root, "About GhostLock", min_width=520)
        self.update_url = None
        frame = page_frame(self.window)
        top = tk.Frame(frame, bg=COLORS["bg"])
        top.pack(fill="x")
        header_block(top, APP_NAME, f"Version {APP_VERSION}  ·  by {APP_PUBLISHER}", icon_px=64, title_size=22).pack(fill="x")
        make_label(
            frame, "A transparent privacy layer for moments when you step away from a running desktop.",
            10, "muted", bg=COLORS["bg"], wraplength=px(450),
        ).pack(fill="x", pady=(px(16), px(16)))  # fmt: skip

        card = Card(frame, padx=22, pady=14)
        card.pack(fill="x")
        shortcuts = (
            ("Click", "Open the options panel from the floating ghost"),
            ("2\u00d7 Click", "Double-click the ghost to lock instantly"),
            *((("Hotkey", f"{config.setting('hotkey')} locks from any program"),) if config and config.setting("hotkey_enabled") else ()),
            ("Enter", "Unlock with your main or emergency password"),
            ("F1", "Recovery question (also Ctrl+R or right-click the field)"),
            ("Esc", "Clear what you have typed on the lock screen"),
        )
        for chip, text in shortcuts:
            row = tk.Frame(card.body, bg=COLORS["surface"])
            row.pack(fill="x", pady=px(6))
            holder = tk.Frame(row, bg=COLORS["surface"], width=px(78), height=px(26))
            holder.pack(side="left")
            holder.pack_propagate(False)
            key_chip(holder, chip).pack(side="left")
            make_label(row, text, 10, "text", bg=COLORS["surface"], wraplength=px(370)).pack(side="left", fill="x")

        safety = tk.Frame(frame, bg=COLORS["warning"], padx=1, pady=1)
        safety.pack(fill="x", pady=(px(14), 0))
        inner = tk.Frame(safety, bg=COLORS["surface"], padx=px(18), pady=px(14))
        inner.pack(fill="x")
        make_label(inner, "Good to know", 10, "warning", True, bg=COLORS["surface"]).pack(fill="x")
        make_label(
            inner,
            "GhostLock is a soft privacy guard, not a replacement for Windows Lock or BitLocker. "
            "Ctrl+Alt+Del, UAC, restart and sign-out stay available by design, and they are your emergency exit.",
            9, "muted", bg=COLORS["surface"], wraplength=px(440),
        ).pack(fill="x", pady=(px(4), 0))  # fmt: skip

        make_label(
            frame, f"{APP_COPYRIGHT}\nCreated by {APP_AUTHOR}. Released under the MIT License.",
            8, "faint", bg=COLORS["bg"], wraplength=px(450),
        ).pack(fill="x", pady=(px(16), px(14)))  # fmt: skip
        self.update_status = make_label(frame, "", 9, "muted", bg=COLORS["bg"], wraplength=px(450))
        self.update_status.pack(fill="x", pady=(0, px(10)))
        footer = tk.Frame(frame, bg=COLORS["bg"])
        footer.pack(fill="x")
        Button(footer, "Close", self.close, "primary", width=px(110)).pack(side="right")
        Button(footer, "Open data folder", lambda: open_folder(app_data_dir()), "secondary").pack(side="right", padx=(0, px(10)))
        self.update_button = Button(footer, "Check for updates", self._check_updates, "secondary")
        self.update_button.pack(side="left")
        self.show()

    def _check_updates(self):
        if self.update_url:  # the button turned into "Open download page"
            webbrowser.open(self.update_url)
            return
        self.update_status.configure(text="Checking GitHub\u2026 (this is the only network request GhostLock makes)", fg=COLORS["muted"])
        result = {}

        def work():
            try:
                result["info"] = check_latest()
            except Exception as exc:
                result["error"] = str(exc) or exc.__class__.__name__
            result["ok"] = True

        threading.Thread(target=work, daemon=True).start()
        self._await_update(result)

    def _await_update(self, result):
        if not self.exists():
            return
        if "ok" not in result:
            self.window.after(150, lambda: self._await_update(result))
            return
        if "error" in result:
            self.update_status.configure(text=f"Could not check: {result['error']}", fg=COLORS["warning"])
        elif result["info"]["update"]:
            self.update_url = result["info"]["url"]
            self.update_status.configure(text=f"Version {result['info']['version']} is available.", fg=COLORS["success"])
            self.update_button.configure_text("Open download page")
        else:
            self.update_status.configure(text="You have the latest version.", fg=COLORS["success"])


# --------------------------------------------------------------------------- installer / uninstaller
class _Worker:
    """Run a list of (label, callable) steps on a thread and report progress to the UI thread."""

    def __init__(self, window, steps, on_progress, on_done):
        self.window, self.steps = window, steps
        self.on_progress, self.on_done = on_progress, on_done
        self.queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()
        self._poll()

    def _run(self):
        total = len(self.steps)
        try:
            for index, (label, action) in enumerate(self.steps):
                self.queue.put(("progress", index / total, label))
                action()
            self.queue.put(("progress", 1.0, "Done"))
            self.queue.put(("done", None, None))
        except Exception as exc:
            log_exception("Installer step failed", exc)
            self.queue.put(("error", exc, None))

    def _poll(self):
        try:
            while True:
                kind, a, b = self.queue.get_nowait()
                if kind == "progress":
                    self.on_progress(a, b)
                else:
                    self.on_done(a if kind == "error" else None)
                    return
        except queue.Empty:
            pass
        try:
            self.window.after(60, self._poll)
        except tk.TclError:
            pass


class LegacyNoticeWindow(AppWindow):
    """Asks permission before older GhostLock files are removed. Lists exactly what will go."""

    MAX_ROWS = 7

    def __init__(self, root, scan, on_confirm, on_skip, on_cancel, parent=None):
        super().__init__(root, "Older GhostLock found", min_width=600)
        self.scan, self.on_confirm, self.on_skip, self.on_cancel = scan, on_confirm, on_skip, on_cancel
        self._answered = False
        if parent is not None:
            try:
                self.window.transient(parent)
            except tk.TclError:
                pass
        self.window.protocol("WM_DELETE_WINDOW", lambda: self._answer(self.on_cancel))
        self.window.bind("<Escape>", lambda _e: self._answer(self.on_cancel), add="+")

        frame = page_frame(self.window)
        if scan.has_legacy:
            title, subtitle = "Older GhostLock found", "Remove it so the new version starts cleanly. Nothing else on your PC is touched."
        else:
            title, subtitle = "GhostLock is running", "It has to close so the new version can be installed."
        header_block(frame, title, subtitle, title_size=22).pack(fill="x")

        card = Card(frame, padx=24, pady=18)
        card.pack(fill="x", pady=(px(20), 0))
        body = card.body
        make_label(body, "THESE WILL BE REMOVED", 9, "accent", True, bg=COLORS["surface"]).pack(fill="x", pady=(0, px(8)))
        for item in scan.items[: self.MAX_ROWS]:
            self._row(body, item)
        extra = len(scan.items) - self.MAX_ROWS
        if extra > 0:
            make_label(body, f"and {extra} more old item{'s' if extra != 1 else ''}", 9, "muted", bg=COLORS["surface"]).pack(fill="x", pady=(px(6), 0))

        self.fresh = None
        if scan.data_item is not None:
            divider(body)
            self.fresh = ToggleRow(
                body,
                "Start fresh",
                "Also remove my old passwords and settings, so setup starts from zero.",
                True,
            )
            self.fresh.pack(fill="x", pady=(px(14), 0))

        notes = []
        if scan.needs_admin:
            notes.append("Some items are shared with all users. Windows will ask for administrator permission once.")
        if any(item.kind == PROCESS for item in scan.items):
            notes.append("Running GhostLock programs will be closed (they hold no documents).")
        if notes:
            make_label(frame, "  ".join(notes), 9, "muted", bg=COLORS["bg"], wraplength=px(520)).pack(fill="x", pady=(px(14), 0))

        footer = tk.Frame(frame, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(px(20), 0))
        Button(footer, "Remove old version & continue", lambda: self._answer(self._confirm), "primary", width=px(250)).pack(side="right")
        Button(footer, "Skip cleanup", lambda: self._answer(self.on_skip), "secondary", width=px(120)).pack(side="right", padx=(0, px(10)))
        Button(footer, "Cancel", lambda: self._answer(self.on_cancel), "secondary", width=px(90)).pack(side="left")
        self.show()

    def _row(self, parent, item):
        row = tk.Frame(parent, bg=COLORS["surface"])
        row.pack(fill="x", pady=(0, px(7)))
        make_label(row, KIND_TITLES.get(item.kind, item.kind), 9, "text", True, bg=COLORS["surface"]).pack(fill="x")
        make_label(row, _shorten(item.title, 74), 8, "muted", bg=COLORS["surface"], wraplength=px(500)).pack(fill="x")

    def _confirm(self):
        self.on_confirm(bool(self.fresh.get()) if self.fresh is not None else False)

    def _answer(self, action):
        if self._answered:
            return
        self._answered = True
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        action()

    def close(self):
        self._answer(self.on_cancel)


def _shorten(text, limit):
    text = str(text)
    if len(text) <= limit:
        return text
    head = (limit - 1) // 2
    return text[:head] + "…" + text[-(limit - head - 1):]


class InstallerWindow(AppWindow):
    def __init__(self, root):
        super().__init__(root, "GhostLock Setup", min_width=560)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.launch = tk.BooleanVar(value=True)
        self.installing = False
        self.cleanup_note = ""
        self.shortcut_problem = ""
        self.target = install_dir() / INSTALLED_EXE_NAME

        self.frame = page_frame(self.window)
        header_block(
            self.frame, f"Install {APP_NAME}", f"A transparent desktop guard by {APP_PUBLISHER}. Installs for this Windows user only.", title_size=22
        ).pack(fill="x")

        self.card = Card(self.frame, padx=24, pady=20)
        self.card.pack(fill="x", pady=(px(24), 0))
        body = self.card.body
        make_label(body, "INSTALL LOCATION", 9, "accent", True, bg=COLORS["surface"]).pack(fill="x")
        path_box = tk.Frame(body, bg=COLORS["surface_2"], padx=px(12), pady=px(9))
        path_box.pack(fill="x", pady=(px(8), px(16)))
        make_label(path_box, str(self.target), 9, "text", bg=COLORS["surface_2"], wraplength=px(430)).pack(fill="x")
        divider(body)
        self.autostart = ToggleRow(body, "Start with Windows", "Launch the ghost button when you sign in.", bool(is_start_with_windows()))
        self.autostart.pack(fill="x", pady=(px(14), px(14)))
        divider(body)
        self.launch_row = ToggleRow(body, "Launch GhostLock when finished", "Opens password setup on first install.", True)
        self.launch_row.pack(fill="x", pady=(px(14), 0))

        self.progress = ProgressBar(body)
        self.progress_label = make_label(body, "", 9, "muted", bg=COLORS["surface"])

        self.status = make_label(self.frame, "", 9, "warning", bg=COLORS["bg"], wraplength=px(300))
        footer = tk.Frame(self.frame, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(px(20), 0))
        self.status.pack(in_=footer, side="left", fill="x", expand=True)
        self.primary = Button(footer, "Install", self.install, "primary", width=px(130))
        self.primary.pack(side="right")
        self.secondary = Button(footer, "Cancel", self.cancel, "secondary", width=px(100))
        self.secondary.pack(side="right", padx=(0, px(10)))
        self.show()

    def cancel(self):
        if not self.installing:
            self.window.destroy()
            self.root.destroy()

    def _busy(self, busy):
        self.installing = busy
        self.primary.set_enabled(not busy)
        self.secondary.set_enabled(not busy)

    def _show_progress(self, visible=True):
        if visible:
            self.progress.pack(fill="x", pady=(px(20), px(4)))
            self.progress_label.pack(fill="x")
        else:
            self.progress.pack_forget()
            self.progress_label.pack_forget()
        self.fit()

    def _on_progress(self, fraction, label):
        self.progress.set(fraction)
        self.progress_label.configure(text=label)

    def install(self):
        """Step 1: look for older GhostLock leftovers. Steps 2-3 (ask, clean) only happen if any are found."""
        if not getattr(sys, "frozen", False):
            self.status.configure(text="Installing needs the packaged Setup.exe (build it with build.ps1).")
            return
        self.cleanup_note = ""
        self.status.configure(text="")
        self._busy(True)
        self._show_progress(True)
        found = {}

        def look():
            found["scan"] = scan_legacy()

        _Worker(self.window, [("Checking for older versions\u2026", look)], self._on_progress, lambda error: self._after_scan(found.get("scan"), error))

    def _after_scan(self, scan, error):
        if error:
            log_exception("Legacy scan failed", error)
            scan = None
        if not scan:
            self._run_install()
            return
        self._show_progress(False)
        LegacyNoticeWindow(
            self.root,
            scan,
            on_confirm=lambda remove_data: self._clean_then_install(scan, remove_data),
            on_skip=self._run_install,
            on_cancel=self._declined,
            parent=self.window,
        )

    def _declined(self):
        self._busy(False)
        self.status.configure(text="Nothing was changed.")

    def _clean_then_install(self, scan, remove_data):
        self._show_progress(True)
        run = CleanupRun(scan, remove_data=remove_data)
        _Worker(self.window, run.steps(), self._on_progress, lambda error: self._after_cleanup(run, error))

    def _after_cleanup(self, run, error):
        if error:
            self._show_progress(False)
            self._busy(False)
            self.status.configure(text=f"Cleanup stopped: {error}. Nothing else was changed.")
            return
        report = run.report
        if any(item.kind == PROCESS for item in report.remaining):
            self._show_progress(False)
            self._busy(False)
            self.status.configure(text="The old GhostLock could not be closed. End it in Task Manager, then try again.")
            return
        if report.remaining:
            count = len(report.remaining)
            self.cleanup_note = (
                f"{count} old item{'s' if count != 1 else ''} could not be removed"
                + (" (administrator permission was not given)." if report.deferred else ". See the log in the data folder.")
            )
        self._run_install()

    def _run_install(self):
        """Step 4: the actual install."""
        if is_another_instance_running():
            self._show_progress(False)
            self._busy(False)
            self.status.configure(text="GhostLock is running. Exit it from the ghost menu, then try again.")
            return
        self._busy(True)
        self._show_progress(True)
        want_autostart = self.autostart.get()
        want_launch = self.launch_row.get()
        source, target = current_executable_path(), self.target

        def copy_files():
            target.parent.mkdir(parents=True, exist_ok=True)
            if source != target:
                shutil.copy2(source, target)

        def shortcuts():
            description = f"Launch {APP_NAME} by {APP_PUBLISHER}"
            failed = []
            for name, folder, arg, text in (
                ("the desktop shortcut", desktop_dir(), "--run", description),
                ("the Start menu entry", start_menu_dir(), "--run", description),
            ):
                if not create_shortcut(folder / f"{APP_NAME}.lnk", target, text, arg):
                    failed.append(name)
            if not create_shortcut(start_menu_dir() / f"{APP_NAME} Uninstall.lnk", target, f"Uninstall {APP_NAME}", "--uninstall"):
                failed.append("the Start menu uninstall entry")
            if failed:
                self.shortcut_problem = "Could not create " + " and ".join(failed) + ". GhostLock works; start it from " + str(target) + "."

        def register():
            register_uninstall_entry(target)
            set_start_with_windows(want_autostart, target)

        def finish():
            import json

            info = {
                "installed_at": int(time.time()),
                "source": str(source),
                "target": str(target),
                "version": CONFIG_VERSION,
                "app_version": APP_VERSION,
                "publisher": APP_PUBLISHER,
                "author": APP_AUTHOR,
                "start_with_windows": bool(want_autostart),
            }
            (target.parent / "install_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

        steps = [
            ("Copying GhostLock…", copy_files),
            ("Creating shortcuts…", shortcuts),
            ("Registering with Windows…", register),
            ("Finishing up…", finish),
        ]
        _Worker(
            self.window,
            steps,
            lambda fraction, label: (self.progress.set(fraction), self.progress_label.configure(text=label)),
            lambda error: self._finished(error, want_launch),
        )

    def _finished(self, error, want_launch):
        self.installing = False
        if error:
            self.progress.pack_forget()
            self.progress_label.pack_forget()
            self.status.configure(text=f"Install failed: {error}. Close GhostLock if it is running and try again.")
            self.primary.set_enabled(True)
            self.secondary.set_enabled(True)
            return
        self.progress.set(1.0)
        self.progress_label.configure(text="GhostLock is installed.", fg=COLORS["success"])
        self.status.configure(text=(self.cleanup_note + " " + self.shortcut_problem).strip())
        self.secondary.pack_forget()
        self.primary.configure_text("Finish" if not want_launch else "Finish and open")
        self.primary.command = lambda: self._finish_and_launch(want_launch)
        self.primary.set_enabled(True)
        self.window.protocol("WM_DELETE_WINDOW", lambda: self._finish_and_launch(want_launch))
        self.fit()
        if not (self.cleanup_note or self.shortcut_problem):
            self.start_countdown(self.status, 10, lambda: self._finish_and_launch(want_launch),
                                 "Opening GhostLock in {n} s." if want_launch else "This window closes in {n} s.")

    def _finish_and_launch(self, want_launch):
        self.stop_countdown()
        if want_launch:
            try:
                configured = ConfigManager().is_configured
                subprocess.Popen(
                    [str(self.target), "--run" if configured else "--setup"],
                    cwd=str(self.target.parent),
                    env=fresh_process_env(),
                    creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                )
            except Exception as exc:
                log_exception("Launch after install failed", exc)
        self.window.destroy()
        self.root.destroy()


def remove_program_files():
    """Schedule removal of the install folder. If it exists but removal cannot start, say so instead of claiming success."""
    if install_dir().exists() and not schedule_install_dir_removal():
        raise RuntimeError("the program folder could not be scheduled for removal; delete it manually: " + str(install_dir()))


class UninstallerWindow(AppWindow):
    def __init__(self, root):
        super().__init__(root, "Uninstall GhostLock", min_width=560)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.working = False
        frame = page_frame(self.window)
        self.frame = frame
        header_block(frame, f"Uninstall {APP_NAME}", f"Remove {APP_NAME} by {APP_PUBLISHER} from this Windows account.", title_size=22).pack(fill="x")
        self.card = Card(frame, padx=24, pady=20)
        self.card.pack(fill="x", pady=(px(24), 0))
        body = self.card.body
        make_label(body, "INSTALLED IN", 9, "accent", True, bg=COLORS["surface"]).pack(fill="x")
        path_box = tk.Frame(body, bg=COLORS["surface_2"], padx=px(12), pady=px(9))
        path_box.pack(fill="x", pady=(px(8), px(16)))
        make_label(path_box, str(install_dir()), 9, "text", bg=COLORS["surface_2"], wraplength=px(430)).pack(fill="x")
        divider(body)
        self.delete_data = ToggleRow(
            body, "Also delete my passwords, settings and activity", "Removes the saved password hashes, preferences, the failed-attempt log and any intruder photos. You cannot undo this.", False
        )
        self.delete_data.pack(fill="x", pady=(px(14), 0))
        self.progress = ProgressBar(body)
        self.progress_label = make_label(body, "", 9, "muted", bg=COLORS["surface"])

        self.status = make_label(frame, "", 9, "warning", bg=COLORS["bg"], wraplength=px(300))
        footer = tk.Frame(frame, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(px(20), 0))
        self.status.pack(in_=footer, side="left", fill="x", expand=True)
        self.primary = Button(footer, "Uninstall", self.uninstall, "danger", width=px(130))
        self.primary.pack(side="right")
        self.secondary = Button(footer, "Cancel", self.cancel, "secondary", width=px(100))
        self.secondary.pack(side="right", padx=(0, px(10)))
        self.show()

    def cancel(self):
        self.stop_countdown()
        if not self.working:
            self.window.destroy()
            self.root.destroy()

    def _wait_for_exit(self, tries):
        if not is_another_instance_running():
            self.uninstall()
        elif tries <= 0:
            self.status.configure(text="GhostLock is still running. Exit it from the ghost menu, then try again.")
            self.primary.set_enabled(True)
        else:
            self.window.after(300, lambda: self._wait_for_exit(tries - 1))

    def uninstall(self):
        if is_another_instance_running():
            self.status.configure(text="Waiting for GhostLock to close\u2026")
            self.primary.set_enabled(False)
            self._wait_for_exit(20)
            return
        self.working = True
        self.status.configure(text="")
        self.primary.set_enabled(False)
        self.secondary.set_enabled(False)
        self.progress.pack(fill="x", pady=(px(20), px(4)))
        self.progress_label.pack(fill="x")
        self.fit()
        delete_data = self.delete_data.get()

        def remove_user_data():
            if delete_data:
                shutil.rmtree(app_data_dir(), ignore_errors=True)

        steps = [
            ("Removing shortcuts…", remove_known_shortcuts),
            ("Removing startup entry…", lambda: (set_start_with_windows(False), unregister_uninstall_entry())),
            ("Removing saved settings…" if delete_data else "Keeping your settings…", remove_user_data),
            ("Removing program files…", remove_program_files),
        ]
        _Worker(
            self.window,
            steps,
            lambda fraction, label: (self.progress.set(fraction), self.progress_label.configure(text=label)),
            self._finished,
        )

    def _finished(self, error):
        self.working = False
        if error:
            self.status.configure(text=f"Uninstall failed: {error}")
            self.primary.set_enabled(True)
            self.secondary.set_enabled(True)
            return
        self.progress_label.configure(text="GhostLock has been removed.", fg=COLORS["success"])
        self.secondary.pack_forget()
        self.primary.kind = "primary"
        self.primary.configure_text("Close")
        self.primary.command = self.cancel
        self.primary.set_enabled(True)
        self.fit()
        self.start_countdown(self.status, 8, self.cancel)


# --------------------------------------------------------------------------- floating widget
class FloatingWidget:
    """Always-on-top ghost button. Click opens the options panel; drag to move."""

    DRAG_THRESHOLD = 6
    DOUBLE_CLICK_SECONDS = 0.30

    def __init__(self, root, config, app):
        self.root, self.config, self.app = root, config, app
        self.drag_start = None
        self.dragged = False
        self.menu = None
        self.menu_items = []
        self.menu_selected = -1
        self.menu_open_at_press = False
        self._last_click = 0.0
        self._pending_menu = None

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=COLORS["key"])
        try:
            self.window.attributes("-transparentcolor", COLORS["key"])
        except tk.TclError:
            pass
        self.icon = load_icon(self.window, "tile", px(64))
        self.pad = px(2)
        inner = self.icon.width() if self.icon else px(64)
        self.size = inner + 2 * self.pad
        self.canvas = tk.Canvas(
            self.window, width=self.size, height=self.size, bg=COLORS["key"], highlightthickness=0, bd=0, cursor="hand2"
        )
        self.canvas.pack()
        if self.icon:
            self.canvas.create_image(self.size // 2, self.size // 2, image=self.icon)
        else:
            self.canvas.create_text(self.size // 2, self.size // 2, text="GL", fill=COLORS["text"], font=font(14, True))
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", lambda _e: self.toggle_menu())
        ToolTip(self.canvas, "GhostLock — click for options")
        self._place()
        self.window.deiconify()

    # placement ---------------------------------------------------------------
    def _place(self):
        saved = self.config.data.get("widget", {})
        x, y = saved.get("x"), saved.get("y")
        if x is None or y is None:
            left, top, right, bottom = work_area(self.root)
            x, y = right - self.size - px(28), bottom - self.size - px(72)
        self._move_to(x, y)

    def _move_to(self, x, y):
        left, top, right, bottom = work_area(self.root, x + self.size // 2, y + self.size // 2)
        x = max(left + px(4), min(x, right - self.size - px(4)))
        y = max(top + px(4), min(y, bottom - self.size - px(4)))
        self.window.geometry(geometry_string(self.size, self.size, x, y))

    # interaction -------------------------------------------------------------
    def _press(self, event):
        self._cancel_pending_menu()
        self.drag_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())
        self.dragged = False
        self.menu_open_at_press = self.menu is not None
        return "break"

    def _drag(self, event):
        if not self.drag_start:
            return "break"
        sx, sy, wx, wy = self.drag_start
        dx, dy = event.x_root - sx, event.y_root - sy
        if not self.dragged and dx * dx + dy * dy >= px(self.DRAG_THRESHOLD) ** 2:
            self.dragged = True
            self.hide_menu()
        if self.dragged:
            self._move_to(wx + dx, wy + dy)
        return "break"

    def _release(self, _event):
        if self.drag_start and self.dragged:
            try:
                self.config.set_widget_position(self.window.winfo_x(), self.window.winfo_y())
            except Exception as exc:
                log_exception("Saving widget position failed", exc)
        elif self.drag_start:
            self._clicked()
        self.drag_start = None
        self.dragged = False
        return "break"

    def _clicked(self):
        """Single click opens the panel; a quick second click locks immediately."""
        now = time.monotonic()
        double_enabled = bool(self.config.data.get("double_click_lock", True))
        if double_enabled and now - self._last_click <= self.DOUBLE_CLICK_SECONDS:
            self._last_click = 0.0
            self._cancel_pending_menu()
            self.hide_menu()
            self.root.after(10, lambda: self.app.run_action("lock"))
            return
        self._last_click = now
        if self.menu_open_at_press:
            self.hide_menu()
        elif double_enabled:
            # Wait one double-click interval so a double-click never flashes the menu.
            self._pending_menu = self.root.after(int(self.DOUBLE_CLICK_SECONDS * 1000), self._open_pending_menu)
        else:
            self.show_menu()

    def _open_pending_menu(self):
        self._pending_menu = None
        self.show_menu()

    def _cancel_pending_menu(self):
        if self._pending_menu is not None:
            try:
                self.root.after_cancel(self._pending_menu)
            except Exception:
                pass
            self._pending_menu = None

    # visibility while locked ---------------------------------------------------
    def hide(self):
        self._cancel_pending_menu()
        self.hide_menu()
        self.window.withdraw()

    def show(self):
        self.window.deiconify()
        self.window.attributes("-topmost", True)

    def destroy(self):
        self._cancel_pending_menu()
        self.hide_menu()
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # menu ----------------------------------------------------------------------
    def toggle_menu(self):
        if self.menu:
            self.hide_menu()
        else:
            self.show_menu()

    def show_menu(self):
        self.hide_menu()
        menu = tk.Toplevel(self.window)
        menu.withdraw()
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        menu.configure(bg=COLORS["border"])
        shell = tk.Frame(menu, bg=COLORS["surface"])
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(shell, bg=COLORS["surface"])
        head.pack(fill="x", padx=px(16), pady=(px(14), px(10)))
        mark = load_icon(self.window, "mark", px(26))
        if mark:
            holder = tk.Label(head, image=mark, bg=COLORS["surface"])
            holder.image = mark
            holder.pack(side="left", padx=(0, px(10)))
        make_label(head, APP_NAME, 11, "text", True, bg=COLORS["surface"]).pack(side="left")
        status = tk.Frame(head, bg=COLORS["surface"])
        status.pack(side="right")
        dot = tk.Canvas(status, width=px(8), height=px(8), bg=COLORS["surface"], highlightthickness=0)
        dot.create_oval(0, 0, px(8) - 1, px(8) - 1, fill=COLORS["success"], outline="")
        dot.pack(side="left", padx=(0, px(6)))
        make_label(status, "Ready", 9, "success", bg=COLORS["surface"]).pack(side="left")
        divider(shell)

        self.menu_items = []
        list_frame = tk.Frame(shell, bg=COLORS["surface"])
        list_frame.pack(fill="x", padx=px(8), pady=px(8))
        for key, text in MENU_ITEMS:
            if key is None:
                divider(list_frame, px(6))
                continue
            row = tk.Frame(list_frame, bg=COLORS["surface"], cursor="hand2")
            row.pack(fill="x", pady=px(1))
            accent = tk.Frame(row, bg=COLORS["surface"], width=px(3))
            accent.pack(side="left", fill="y")
            is_lock = key == "lock"
            color = {"lock": COLORS["accent"], "exit": COLORS["danger"], "uninstall": COLORS["muted"]}.get(key, COLORS["text"])
            label = tk.Label(
                row, text=text, anchor="w", bg=COLORS["surface"], fg=color, font=font(10, is_lock),
                padx=px(12), pady=px(9), cursor="hand2",
            )  # fmt: skip
            label.pack(side="left", fill="x", expand=True)
            item = {"key": key, "row": row, "accent": accent, "label": label}
            self.menu_items.append(item)
            for widget in (row, label, accent):
                widget.bind("<Enter>", lambda _e, i=len(self.menu_items) - 1: self._select(i))
                widget.bind("<Button-1>", lambda _e, k=key: self._activate(k))

        menu.bind("<Escape>", lambda _e: self.hide_menu())
        menu.bind("<Up>", lambda _e: self._select((self.menu_selected - 1) % len(self.menu_items)))
        menu.bind("<Down>", lambda _e: self._select((self.menu_selected + 1) % len(self.menu_items)))
        menu.bind("<Return>", lambda _e: self._activate(self.menu_items[self.menu_selected]["key"]) if self.menu_selected >= 0 else None)
        menu.bind("<FocusOut>", lambda _e: menu.after(80, self._close_if_unfocused))
        self.menu = menu
        self.menu_selected = -1
        menu.update_idletasks()
        self._position_menu()
        menu.deiconify()
        keep_rounded(menu, px(18))
        menu.focus_force()

    def _select(self, index):
        self.menu_selected = index
        for i, item in enumerate(self.menu_items):
            active = i == index
            bg = COLORS["surface_2"] if active else COLORS["surface"]
            item["row"].configure(bg=bg)
            item["label"].configure(bg=bg)
            item["accent"].configure(bg=COLORS["accent"] if active else bg)

    def _position_menu(self):
        menu = self.menu
        if not menu:
            return
        width, height = menu.winfo_reqwidth(), menu.winfo_reqheight()
        width = max(width, px(250))
        wx, wy = self.window.winfo_x(), self.window.winfo_y()
        left, top, right, bottom = work_area(self.root, wx + self.size // 2, wy + self.size // 2)
        gap = px(10)
        x = wx - width - gap
        if x < left + px(8):
            x = wx + self.size + gap
        y = wy + self.size - height if wy + self.size // 2 > (top + bottom) // 2 else wy
        x = max(left + px(8), min(x, right - width - px(8)))
        y = max(top + px(8), min(y, bottom - height - px(8)))
        menu.geometry(geometry_string(width, height, x, y))

    def _close_if_unfocused(self):
        if not self.menu:
            return
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        if focused is None or focused.winfo_toplevel() is not self.menu:
            self.hide_menu()

    def hide_menu(self):
        menu, self.menu = self.menu, None
        if menu is not None:
            try:
                menu.destroy()
            except tk.TclError:
                pass

    def _activate(self, key):
        self.hide_menu()
        self.root.after(10, lambda: self.app.run_action(key))
