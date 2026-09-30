"""Window for changing what Lora is doing on a timer."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import webbrowser
from threading import Lock as ThreadLock
import tkinter as tk
import tkinter.font as tkfont
from datetime import timedelta
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox

import ctypes
from ctypes import wintypes

import cast
import help_guide
import kindroid_extras
import narrator_commands
import simulation
import theme
import update_scene
import wand
import wipe

BANNER_PATH = Path(__file__).resolve().parent / "background.png"
SLIDES_DIR = Path(__file__).resolve().parent / "backgrounds"

# The colors. They come from the theme (Settings > Theme); use_palette changes them.
BG = PANEL = TEXT = MUTED = ACCENT = ACCENT_TEXT = ENTRY = WARN = ""
ACCENT_ACTIVE = PANEL_ACTIVE = DISABLED = TIP_BG = KIN = YOU = ""
PALETTE: dict = {}
KEY = "#010203"  # the see-through color for the picture behind the window (never a theme color)
WATERMARK = "mez.ink/ferisooo"
WATERMARK_URL = "https://mez.ink/ferisooo"
LEGAL_FILES = (("Terms of Service", "TERMS.txt"), ("Privacy Policy", "PRIVACY.txt"))


def use_palette(palette: dict) -> None:
    """Make these the app's colors. Everything made or recolored from now on uses them."""
    global BG, PANEL, TEXT, MUTED, ACCENT, ACCENT_TEXT, ENTRY, WARN, ACCENT_ACTIVE, PANEL_ACTIVE, DISABLED, TIP_BG, KIN, YOU
    global PALETTE
    BG, PANEL, TEXT, MUTED = palette["bg"], palette["panel"], palette["text"], palette["muted"]
    ACCENT, ACCENT_TEXT, ENTRY, WARN = palette["accent"], palette["accent_text"], palette["entry"], palette["warn"]
    ACCENT_ACTIVE, PANEL_ACTIVE, DISABLED = palette["accent_active"], palette["panel_active"], palette["disabled"]
    TIP_BG, KIN, YOU = palette["tip_bg"], palette["kin"], palette["you"]
    PALETTE = dict(palette)


use_palette(theme.full(theme.PRESETS[theme.DEFAULT]))
SCENE_LIMIT = 160
# "I will check back every so often": during a wait, the chat is read this often for commands.
MID_CHECK_SECONDS = 15 * 60  # Kindroid allows 600 chat reads a day
# If the heads-up can't be sent (no internet, Kindroid busy), the change waits. After this many tries it goes ahead.
HEADS_UP_TRIES = 10
# If she ignores the heads-up, the change waits this long for her answer, reading the chat every ANSWER_CHECK_SECONDS.
ANSWER_GRACE_SECONDS = 2 * 60
ANSWER_CHECK_SECONDS = 20
# Restart by itself when its code is updated. Only at a safe moment: never during a change or a heads-up.
APP_DIR = Path(__file__).resolve().parent
CODE_FILES = ("gui.py", "simulation.py", "update_scene.py", "wand.py", "narrator_commands.py")
UPDATE_CHECK_SECONDS = 5
DROP_COLUMNS = 4
# What the panel buttons say. (Inside, the panels keep their old names.)
DROP_LABELS = {"World": "Right now", "Environment": "Places", "Backstory": "About Lora"}
UPDATE_SETTLE_SECONDS = 10
RESTART_SAFE_SECONDS = 3 * 60
# Mochi and the Narrator take turns: Mochi waits near a change, and after the Narrator speaks.
MOCHI_QUIET_BEFORE_CHANGE = 3 * 60
MOCHI_AFTER_NARRATOR = 2 * 60
MOCHI_RETRY_SECONDS = 30
# Asleep: no changes or heads-ups, a dream every hour, and she wakes up at the wake time (or Wake up).
DREAM_SECONDS = 60 * 60
DREAM_RETRY_SECONDS = 5 * 60


def code_stamp() -> dict:
    """When each code file last changed, to notice an update."""
    stamp = {}
    for name in CODE_FILES:
        try:
            info = (APP_DIR / name).stat()
        except OSError:
            continue
        stamp[name] = (info.st_mtime_ns, info.st_size)
    return stamp


def code_problem() -> str:
    """A mistake that would stop the updated app from starting. Empty when every file is fine."""
    for name in CODE_FILES:
        path = APP_DIR / name
        if not path.exists():
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (SyntaxError, ValueError, UnicodeDecodeError, OSError) as caught:
            return f"{name}: {caught}"
    return ""

DEFAULT_ENVIRONMENT = ""
DEFAULT_BACKSTORY = ""


def let_clicks_through(hwnd: int) -> None:
    """Keep the picture window under the controls. An owned window is always forced above its owner, which is the dark-bar glitch."""
    GWL_EXSTYLE = -20
    GWLP_HWNDPARENT = -8
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    user32 = ctypes.windll.user32
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        get_long.restype = ctypes.c_size_t
        set_long.restype = ctypes.c_size_t
        get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
    else:
        get_long = user32.GetWindowLongW
        set_long = user32.SetWindowLongW
    set_long(hwnd, GWLP_HWNDPARENT, 0)
    style = get_long(hwnd, GWL_EXSTYLE) or 0
    set_long(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


def drag_window(hwnd: int) -> None:
    """Hand the current mouse press to Windows as if it were on the title bar."""
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    user32.SetForegroundWindow(wintypes.HWND(hwnd))
    user32.ReleaseCapture()
    send = user32.SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = ctypes.c_ssize_t
    send(hwnd, 0x00A1, 2, ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF))


def slide_files() -> list[Path]:
    files: list[Path] = []
    if SLIDES_DIR.is_dir():
        files = sorted(
            path
            for path in SLIDES_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        )
    if files:
        return files
    if BANNER_PATH.is_file():
        return [BANNER_PATH]
    return []


def add_slides(sources: tuple[str, ...]) -> int:
    SLIDES_DIR.mkdir(exist_ok=True)
    existing = [path for path in slide_files() if path.parent == SLIDES_DIR]
    if not existing and BANNER_PATH.is_file():
        save_banner(str(BANNER_PATH), SLIDES_DIR / "001.png")
    number = 1
    taken = {path.name for path in SLIDES_DIR.iterdir()}
    while f"{number:03d}.png" in taken:
        number += 1
    count = 0
    for source in sources:
        save_banner(source, SLIDES_DIR / f"{number:03d}.png")
        number += 1
        count += 1
    return count


def clear_slides() -> None:
    if SLIDES_DIR.is_dir():
        for path in SLIDES_DIR.iterdir():
            if path.is_file():
                path.unlink()
    BANNER_PATH.unlink(missing_ok=True)


def save_banner(source: str, dest: Path) -> None:
    from PIL import Image

    image = Image.open(source).convert("RGB")
    image.thumbnail((1600, 1600))
    image.save(dest, "PNG")


def fit_image(path: Path, width: int, height: int):
    """Show the whole picture inside the window, with a dimmed copy filling the edges."""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    backdrop = Image.blend(cover_image(path, width, height), Image.new("RGB", (width, height), (8, 6, 12)), 0.55)
    scale = min(width / image.width, height / image.height)
    fitted = image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    backdrop.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return backdrop


def cover_image(path: Path, width: int, height: int):
    from PIL import Image

    image = Image.open(path).convert("RGB")
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


MAX_TEXT_SCALE = 2.2  # at most this much bigger text in a big (or full-screen) window


class Tooltip:
    """A small note that shows when the mouse rests on an option for a moment."""

    DELAY_MS = 450

    def __init__(self, app: "App", widget: tk.Misc, text: str) -> None:
        self.app, self.widget, self.text = app, widget, text
        self.window: tk.Toplevel | None = None
        self.job = None
        widget.bind("<Enter>", self._soon, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _soon(self, _event=None) -> None:
        self._cancel()
        self.job = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self) -> None:
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def _show(self) -> None:
        self.job = None
        if self.window is not None or not self.widget.winfo_ismapped():
            return
        scale = self.app.text_scale(self.widget.winfo_toplevel())
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.attributes("-topmost", True)
        tk.Label(
            self.window, text=cast.localize(self.text), bg=TIP_BG, fg=TEXT, justify="left",
            font=("Segoe UI", round(10 * scale)), wraplength=round(340 * scale), padx=10, pady=6, bd=0,
        ).pack()
        self.window.update_idletasks()
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        x = min(x, self.window.winfo_screenwidth() - self.window.winfo_reqwidth() - 8)
        if y + self.window.winfo_reqheight() > self.window.winfo_screenheight() - 40:
            y = self.widget.winfo_rooty() - self.window.winfo_reqheight() - 6
        self.window.wm_geometry(f"+{max(0, x)}+{max(0, y)}")

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None


def tidy_answer(text: str) -> list[str]:
    """The helper's answer as plain lines: no markdown stars or headings, no empty lines."""
    text = re.sub(r"\*\*|__|`", "", str(text or ""))
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*#+\s*", "", line)
        line = re.sub(r"^\s*[*\u2022]\s+", "- ", line).strip()
        if line:
            lines.append(line)
    return lines or [""]


class HelpChat(tk.Frame):
    """The Help page: ask DeepSeek anything about the app. It answers from help_guide.GUIDE."""

    def __init__(self, app: "App") -> None:
        super().__init__(app, bg=BG)
        self.app = app
        self.history: list[dict] = []
        self.busy = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        tk.Label(self, text="Ask me anything about this app.", bg=BG, fg=TEXT, font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        self.chat = tk.Text(
            self, wrap="word", bg=ENTRY, fg=TEXT, relief="flat", font=("Segoe UI", 12), padx=16, pady=14,
            state="disabled", cursor="arrow", height=14,
        )
        self.chat.grid(row=1, column=0, sticky="nsew")
        bar = tk.Scrollbar(self, orient="vertical", command=self.chat.yview)
        bar.grid(row=1, column=1, sticky="ns")
        self.chat.configure(yscrollcommand=bar.set)
        name_font = app.scaled_font(11, "bold")
        self.chat.tag_configure("you_name", font=name_font, spacing1=4)
        self.chat.tag_configure("helper_name", font=name_font, spacing1=4)
        self.chat.tag_configure("you", spacing1=2, spacing3=2)
        self.chat.tag_configure("helper", spacing1=2, spacing3=2)
        self.chat.tag_configure("item", lmargin1=0, lmargin2=26)
        self.chat.tag_configure("gap", spacing3=16)
        self.chat.tag_configure("quiet", spacing3=16)
        self.recolor()
        row = tk.Frame(self, bg=BG)
        row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        row.columnconfigure(0, weight=1)
        self.entry = tk.Entry(row, bg=ENTRY, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 12))
        self.entry.grid(row=0, column=0, sticky="ew", ipady=8)
        self.entry.bind("<Return>", lambda _event: self.send())
        self.send_button = tk.Button(
            row, text="Ask", command=self.send, bg=ACCENT, fg=ACCENT_TEXT, activebackground=ACCENT_ACTIVE,
            activeforeground=ACCENT_TEXT, relief="flat", font=("Segoe UI", 12, "bold"), padx=20, cursor="hand2",
        )
        self.send_button.grid(row=0, column=1, padx=(8, 0), sticky="ns")
        example = "\"Why doesn't Mochi talk?\"" if cast.has_chatter() else "\"How do I change the places?\""
        self._add(
            "helper",
            cast.localize(f"Hi! Ask me about any part of this app. Try: \"How do I start?\", \"What does Warn Lora do?\", or {example}"),
        )

    def recolor(self) -> None:
        """The chat's colors, from the theme."""
        self.chat.tag_configure("you_name", foreground=YOU)
        self.chat.tag_configure("you", foreground=YOU)
        self.chat.tag_configure("helper_name", foreground=TEXT)
        self.chat.tag_configure("helper", foreground=TEXT)
        self.chat.tag_configure("quiet", foreground=MUTED)

    def _add(self, who: str, text: str) -> None:
        self.chat.configure(state="normal")
        if who == "quiet":
            self.chat.insert("end", text.strip() + "\n", ("quiet",))
        else:
            self.chat.insert("end", ("You" if who == "you" else "Helper") + "\n", (f"{who}_name",))
            lines = tidy_answer(text)
            for index, line in enumerate(lines):
                tags = [who]
                if re.match(r"^(\d+[.)]|-)\s", line):
                    tags.append("item")  # a wrapped step lines up under its own text
                if index == len(lines) - 1:
                    tags.append("gap")
                self.chat.insert("end", line + "\n", tuple(tags))
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def send(self) -> None:
        question = self.entry.get().strip()
        if not question or self.busy:
            return
        self.entry.delete(0, "end")
        self._add("you", question)
        self._add("quiet", "Thinking...")
        self.history.append({"role": "user", "content": question})
        self.busy = True
        self.send_button.configure(state="disabled")
        threading.Thread(target=self._work, args=(list(self.history),), daemon=True).start()

    def _work(self, history: list[dict]) -> None:
        try:
            answer, error = help_guide.ask(history), ""
        except Exception as caught:
            answer, error = "", str(caught)
        try:
            self.after(0, lambda: self._answered(answer, error))
        except (tk.TclError, RuntimeError):
            pass

    def _answered(self, answer: str, error: str) -> None:
        self.busy = False
        self.send_button.configure(state="normal")
        self.chat.configure(state="normal")
        start = self.chat.search("Thinking...", "end", backwards=True)
        if start:
            self.chat.delete(start, f"{start} lineend +1c")
        self.chat.configure(state="disabled")
        if error:
            self.history.pop()
            self._add("quiet", f"Could not ask right now. {error}")
            return
        self.history.append({"role": "assistant", "content": answer})
        self._add("helper", answer)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.theme_name, self.theme_colors = theme.load(update_scene.load_prefs())
        use_palette(theme.full(self.theme_colors))
        self.title(cast.cast()["kin_name"])
        self.configure(bg=BG)
        self.minsize(480, 280)
        self.geometry("560x320")
        self.phase = "idle"
        self.stop_requested = False
        self.clock_id = None
        self.deadline = 0.0
        self.stopped_on = ""
        prefs = update_scene.load_prefs()
        self.last_greet_date = str(prefs.get("last_greet_date", ""))
        try:
            self.opacity_value = int(str(prefs.get("opacity", "100")))
        except ValueError:
            self.opacity_value = 100
        self.opacity_value = max(0, min(100, self.opacity_value))
        self._opacity_save = None
        try:
            self.slide_seconds = int(str(prefs.get("slide_seconds", "15")))
        except ValueError:
            self.slide_seconds = 15
        self.slide_seconds = max(1, min(self.slide_seconds, 3600))
        self.slide_index = 0
        self.next_slide = time.monotonic() + self.slide_seconds
        self.plate = None
        self.plate_label = None
        self._plate_photo = None
        self._plate_spot = None
        self._glass = False
        self._solid: dict[tk.Misc, dict[str, str]] = {}
        self._bases: dict[tuple[str, int, int], object] = {}
        self._glass_sig = None
        self._glass_job = None

        self.columnconfigure(0, weight=1)
        self.note_until = 0.0
        self.narrator_peeked = False
        self.narrator_peeking = False
        self.narrator_request = ""
        self.narrator_speaker = ""
        self.narrator_extend_at = 0
        self.narrator_after = 0
        self.command_stamp = 0
        self.narrator_kind = ""
        self.next_mid_check = float("inf")
        self.next_world_refresh = 0.0
        self.wand_id = None
        self.wand_gate = ThreadLock()
        self._font_sets: dict[str, dict] = {}  # per window: its growing fonts and how much bigger they are now
        self.help_win: HelpChat | None = None

        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        header.columnconfigure(0, weight=1)
        self.kin_title = self._text(header, text="Lora", fg=KIN, font=("Segoe UI", 22, "bold"))
        self.kin_title.grid(row=0, column=0, sticky="w")
        self.clock_label = self._text(header, text="", fg=MUTED, font=("Segoe UI", 10))
        self.clock_label.grid(row=0, column=1, sticky="e")
        self.nav_button = self._small_button(header, "Settings", self._nav)
        self.nav_button.grid(row=0, column=2, sticky="e", padx=(12, 0))
        self.bind("<Configure>", self._on_configure)

        setting_header = tk.Frame(self, bg=BG)
        setting_header.grid(row=1, column=0, sticky="ew", padx=24)
        setting_header.columnconfigure(0, weight=1)
        self._text(setting_header, text="What Lora is doing now", fg=MUTED, font=("Segoe UI", 10)).grid(
            row=0, column=0, sticky="w"
        )
        self.count_label = self._text(setting_header, text=f"0/{SCENE_LIMIT}", fg=MUTED, font=("Segoe UI", 10))
        self.count_label.grid(row=0, column=1, sticky="e")

        self.prior = tk.Text(
            self,
            height=2,
            wrap="word",
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 11),
            padx=10,
            pady=8,
        )
        self.prior.grid(row=2, column=0, sticky="ew", padx=24, pady=(6, 8))
        self.prior.insert("1.0", update_scene.saved_prior())
        self.prior.bind("<KeyRelease>", lambda _event: self.refresh_count())
        self.refresh_count()

        actions = tk.Frame(self, bg=BG)
        actions.grid(row=3, column=0, sticky="ew", padx=24, pady=(8, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        self.ask_button = self._button(actions, "Start", self.start, ACCENT, ACCENT_TEXT, 0)
        self.stop_button = self._button(actions, "Stop", self.stop, PANEL, TEXT, 1, disabled=True)
        self.idle_background = self._button(
            actions, "Pictures", lambda: self.toggle_drop("Pictures"), PANEL, TEXT, 1
        )
        self.idle_background.grid_remove()
        actions.columnconfigure(2, weight=1)
        self.sleep_button = self._button(actions, "Sleep", self.toggle_asleep, PANEL, TEXT, 2)
        self.dreaming = False
        self.wake_morning = False

        self.countdown = self._text(self, text="Press Start to begin.", fg=MUTED, font=("Segoe UI", 11), justify="left")
        self.countdown.grid(row=4, column=0, sticky="w", padx=24, pady=(12, 0))
        # While she sleeps: skip the wait and send the next dream right now.
        self.dream_now_button = self._small_button(self, "Dream now", self.dream_now)
        self.dream_now_button.grid(row=4, column=0, sticky="e", padx=24, pady=(12, 0))
        self.dream_now_button.grid_remove()
        self.status = self._text(self, text="", fg=MUTED, font=("Segoe UI", 9), justify="left")
        self.status.grid(row=5, column=0, sticky="w", padx=24, pady=(2, 18))

        self._home_parts = [setting_header, self.prior, actions, self.countdown, self.status]
        # Everything else is on the Settings page of this same window: a button per part, and its panel
        # under them. The panels scroll (mouse wheel or the bar). Back returns to Start, Stop, and Sleep.
        self.page = "home"
        self._home_height = 0
        self.rowconfigure(6, weight=1)
        self.settings_page = tk.Frame(self, bg=BG)
        self.settings_page.grid(row=6, column=0, sticky="nsew")
        self.settings_page.columnconfigure(0, weight=1)
        self.settings_page.rowconfigure(1, weight=1)
        drops = tk.Frame(self.settings_page, bg=BG)
        drops.grid(row=0, column=0, columnspan=2, sticky="ew", padx=24, pady=(4, 14))
        self.settings_canvas = tk.Canvas(self.settings_page, bg=BG, highlightthickness=0, bd=0, height=1, yscrollincrement=24)
        self.settings_canvas.grid(row=1, column=0, sticky="nsew")
        self.settings_bar = tk.Scrollbar(self.settings_page, orient="vertical", command=self.settings_canvas.yview)
        self.settings_bar.grid(row=1, column=1, sticky="ns")
        self.settings_canvas.configure(yscrollcommand=self.settings_bar.set)
        self.settings_inner = tk.Frame(self.settings_canvas, bg=BG)
        self.settings_inner.columnconfigure(0, weight=1)
        inner_id = self.settings_canvas.create_window(0, 0, window=self.settings_inner, anchor="nw")
        self.settings_inner.bind("<Configure>", lambda _event: self._settings_scrolled())
        self.settings_canvas.bind(
            "<Configure>",
            lambda event: (self.settings_canvas.itemconfigure(inner_id, width=event.width), self._settings_scrolled()),
        )
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind("<Escape>", lambda _event: self.show_page("home"))
        self.settings_page.grid_remove()
        for column in range(DROP_COLUMNS):
            drops.columnconfigure(column, weight=1, uniform="drop")
        self.drop_open = ""
        self.drop_buttons: dict[str, tk.Button] = {}
        self.drop_panels: dict[str, tk.Frame] = {}
        for index, name in enumerate(
            ("Schedule", "Chat", "Mochi", "Narrator", "World", "Environment", "Backstory", "Pictures", "Theme", "Extras", "App", "Legal")
        ):
            self.drop_buttons[name] = self._drop_button(drops, name, index // DROP_COLUMNS, index % DROP_COLUMNS)

        schedule = self._panel(columns=(1,))
        self.minutes = self._labeled_entry(schedule, 0, 0, "Change every", str(prefs.get("minutes", "10")), 8)
        self._hint(schedule, "minutes (or 2h)", 0)
        self.times = self._labeled_entry(schedule, 1, 0, "Also at", str(prefs.get("times", "")), 22)
        self._hint(schedule, "optional: 9:00 AM, noon, night", 1)
        self.wake = self._labeled_entry(schedule, 2, 0, "Wake at", self._clock_text(prefs.get("wake"), "8:00 AM"), 10)
        self._hint(schedule, "the day starts", 2)
        self.sleep_at = self._labeled_entry(schedule, 3, 0, "Sleep at", self._clock_text(prefs.get("sleep_at"), ""), 10)
        self._hint(schedule, "Lora sleeps and dreams (empty: never)", 3)
        self.ai_minutes = tk.BooleanVar(value=str(prefs.get("ai_minutes", "1")) != "0")
        self._check(schedule, "Each moment lasts as long as it would in real life", self.ai_minutes, 4)
        self.heads_up = tk.BooleanVar(value=str(prefs.get("heads_up", "1")) != "0")
        self._check(
            schedule,
            "Warn Lora 1 minute before each change",
            self.heads_up,
            5,
            command=self._save_heads_up,
        )
        self.drop_panels["Schedule"] = schedule

        chat = self._panel(columns=(1,))
        self.use_group = tk.BooleanVar(value=str(prefs.get("use_group", "0")) == "1")
        self._check(chat, "Use the group chat instead of the 1-on-1 chat", self.use_group, 0, command=self._save_use_group)
        self.group_auto = tk.BooleanVar(value=str(prefs.get("group_auto", "0")) == "1")
        self._check(chat, "My group chat is set to Auto in Kindroid", self.group_auto, 1, command=self._save_group_auto)
        site_code = str(prefs.get("kindroid_site") or "auto")
        self.site_choice = tk.StringVar(
            value=next((label for label, code in wand.SITE_CHOICES.items() if code == site_code), next(iter(wand.SITE_CHOICES)))
        )
        self._text(chat, text="Kindroid site", fg=MUTED, font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=(12, 0))
        site_menu = tk.OptionMenu(chat, self.site_choice, *wand.SITE_CHOICES, command=lambda _label: self._save_site())
        site_menu.configure(
            bg=PANEL, fg=TEXT, activebackground=PANEL_ACTIVE, activeforeground=TEXT, relief="flat", bd=0,
            highlightthickness=0, font=("Segoe UI", 10), cursor="hand2", anchor="w",
        )
        site_menu["menu"].configure(bg=PANEL, fg=TEXT, activebackground=ACCENT, activeforeground=ACCENT_TEXT, font=("Segoe UI", 10))
        site_menu.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._text(
            chat, text="Look at the web address when a chat is open: /v2/ in it means v2.", fg=MUTED, font=("Segoe UI", 9),
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.drop_panels["Chat"] = chat

        app = self._panel(columns=(1,))
        self.setup_button = self._small_button(app, "Change setup (names, profiles, keys, places)", self.change_setup)
        self.setup_button.grid(row=0, column=0, columnspan=3, sticky="w")
        self._small_button(app, "Wipe everything and start over", self.wipe_all).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 10)
        )
        self.auto_restart = tk.BooleanVar(value=str(prefs.get("auto_restart", "1")) != "0")
        self._check(app, "Restart by itself after an update", self.auto_restart, 2, command=self._save_auto_restart)
        self.drop_panels["App"] = app

        themes = self._panel(columns=(2,))
        self._text(themes, text="Ready-made themes", fg=TEXT, font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        presets = tk.Frame(themes, bg=BG)
        presets.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 16))
        for index, name in enumerate(theme.PRESETS):
            self._small_button(presets, name, lambda name=name: self.set_theme(name, theme.PRESETS[name])).grid(
                row=0, column=index, padx=(0, 6), pady=2
            )
        self._text(themes, text="Your own colors", fg=TEXT, font=("Segoe UI", 11, "bold")).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        self.color_buttons: dict[str, tk.Button] = {}
        row = 3
        for row, (role, label) in enumerate(theme.ROLES, start=3):
            self._text(themes, text=label, fg=MUTED, font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=3)
            swatch = tk.Button(
                themes, width=12, relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 10),
                command=lambda role=role, label=label: self.pick_color(role, label),
            )
            swatch._swatch = True  # shows its own color, whatever the theme
            swatch.grid(row=row, column=1, sticky="w", pady=3)
            self.color_buttons[role] = swatch
        self.theme_note = self._text(themes, text="", fg=MUTED, font=("Segoe UI", 9), justify="left", wraplength=460)
        self.theme_note.grid(row=row + 1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.drop_panels["Theme"] = themes

        legal = self._panel(columns=(0,))
        picks = tk.Frame(legal, bg=BG)
        picks.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.legal_buttons: dict[str, tk.Button] = {}
        for index, (title, file_name) in enumerate(LEGAL_FILES):
            button = self._small_button(picks, title, lambda file_name=file_name: self._show_legal(file_name))
            button.grid(row=0, column=index, padx=(0, 6))
            self.legal_buttons[file_name] = button
        self.legal_text = tk.Text(
            legal, height=18, wrap="word", bg=ENTRY, fg=TEXT, relief="flat", font=("Segoe UI", 12), padx=20, pady=16,
            cursor="arrow", spacing1=2, spacing3=2,
        )
        self.legal_shown = ""
        self.legal_text.grid(row=1, column=0, sticky="ew")
        self._text(legal, text="Also in the app's folder: TERMS.txt and PRIVACY.txt.", fg=MUTED, font=("Segoe UI", 9)).grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        self.drop_panels["Legal"] = legal

        extras = self._panel(columns=(1,))
        self._text(
            extras, text="Extra things the app can do in Kindroid for you. Each one is off until you tick it. "
            "They use the app's own browser (Find logged it in), one at a time.",
            fg=MUTED, font=("Segoe UI", 9), justify="left", wraplength=520,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.extra_vars: dict[str, tk.BooleanVar] = {}
        for row, (name, label) in enumerate(kindroid_extras.SWITCHES.items(), start=1):
            if name == "x_react" and not cast.has_chatter():
                continue
            self.extra_vars[name] = tk.BooleanVar(value=str(prefs.get(name) or "0") == "1")
            self._check(extras, label, self.extra_vars[name], row, command=lambda name=name: self._save_extra(name))
        self.selfies_per_day = self._labeled_entry(
            extras, 10, 0, "Selfies a day", str(prefs.get("x_selfies_per_day") or kindroid_extras.SELFIES_PER_DAY), 4
        )
        self.selfies_per_day.bind("<FocusOut>", lambda _event: self._save_selfies_per_day())
        self.selfies_per_day.bind("<Return>", lambda _event: self._save_selfies_per_day())
        self._hint(extras, "at most (each selfie uses Kindroid credits)", 10)
        self.read_kin_button = self._small_button(extras, "Read Lora from Kindroid now", self.read_kin_now)
        self.read_kin_button.grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.extras_note = self._text(extras, text="", fg=MUTED, font=("Segoe UI", 9), justify="left", wraplength=520)
        self.extras_note.grid(row=12, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.drop_panels["Extras"] = extras
        self._show_legal(LEGAL_FILES[0][1])
        self._show_theme()
        self.code_seen = code_stamp()
        self.code_pending = None
        self.code_pending_since = 0.0
        self.code_bad = None
        self.update_waiting_noted = False
        self.next_update_check = time.monotonic() + UPDATE_CHECK_SECONDS

        mochi = self._panel(columns=(2,))
        self.wand_on = tk.BooleanVar(value=str(prefs.get("wand", "0")) == "1")
        self._check(mochi, "Mochi talks every few minutes", self.wand_on, 0, command=self._toggle_wand)
        self.wand_minutes = self._labeled_entry(mochi, 1, 0, "Talk every", str(prefs.get("wand_minutes", "10")), 8)
        self._hint(mochi, "minutes", 1)
        self.wand_setup_button = self._small_button(mochi, "Set up Mochi's browser", self.setup_wand)
        self.wand_setup_button.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._hint(mochi, "only if Mochi can't talk (setup did this)", 2)
        config = update_scene.load_json(update_scene.CONFIG_PATH, {})
        self.mochi_id = self._labeled_entry(mochi, 3, 0, "Mochi's AI ID", str(config.get("mochi_ai_id") or ""), 24)
        self._hint(mochi, "group chat only", 3)
        self.mochi_id.bind("<FocusOut>", lambda _event: self._save_mochi_id())
        self.mochi_id.bind("<Return>", lambda _event: self._save_mochi_id())
        self.drop_panels["Mochi"] = mochi
        self.narrator_done_at = -1e9
        self.mochi_waiting_noted = False

        environment = self._panel(columns=(0,))
        self.environment = self._text_box(environment, str(prefs.get("environment") or DEFAULT_ENVIRONMENT))
        suggest_row = tk.Frame(environment, bg=BG)
        suggest_row.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.suggest_button = self._small_button(suggest_row, "Suggest places", self.suggest_places)
        self.suggest_button.pack(side="left")
        self.suggest_note = self._text(suggest_row, text="One place per line. Suggest adds more.", fg=MUTED, font=("Segoe UI", 9))
        self.suggest_note.pack(side="left", padx=8)
        self.drop_panels["Environment"] = environment

        backstory = self._panel(columns=(0,))
        self.backstory = self._text_box(backstory, str(prefs.get("backstory") or DEFAULT_BACKSTORY))
        self._text(backstory, text="Who Lora is. The story uses it to pick what Lora does.", fg=MUTED,
                   font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.drop_panels["Backstory"] = backstory

        narrator = self._panel(columns=(0,))
        self._build_narrator_panel(narrator)
        self.drop_panels["Narrator"] = narrator

        pictures = self._panel()
        self.bg_button = self._small_button(pictures, "Add pictures", self.choose_background)
        self.bg_button.grid(row=0, column=0, sticky="w")
        self.clear_bg = self._small_button(pictures, "Remove all", self.clear_background)
        self.clear_bg.grid(row=0, column=1, sticky="w", padx=(6, 16))
        self._text(pictures, text="Visible", fg=MUTED, font=("Segoe UI", 9)).grid(row=0, column=2, sticky="e", padx=(12, 0))
        self.opacity = tk.Scale(
            pictures,
            from_=0,
            to=100,
            orient="horizontal",
            showvalue=False,
            bg=PANEL,
            fg=TEXT,
            troughcolor=ENTRY,
            activebackground=ACCENT,
            highlightthickness=0,
            bd=0,
            sliderrelief="flat",
            length=90,
            command=self._on_opacity,
        )
        self.opacity.grid(row=0, column=3, sticky="w", padx=(6, 4))
        self.opacity_label = self._text(pictures, text=f"{self.opacity_value}%", fg=TEXT, font=("Segoe UI", 9), width=4, anchor="w")
        self.opacity_label.grid(row=0, column=4, sticky="w", padx=(0, 12))
        self._text(pictures, text="Next every (sec)", fg=MUTED, font=("Segoe UI", 9)).grid(row=0, column=5, sticky="e")
        self.slide_every = tk.Entry(
            pictures,
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            width=4,
            justify="right",
        )
        self.slide_every.insert(0, str(self.slide_seconds))
        self.slide_every.grid(row=0, column=6, sticky="w", padx=(6, 0))
        self.slide_every.bind("<FocusOut>", self._save_slide_seconds)
        self.slide_every.bind("<Return>", self._save_slide_seconds)
        if not slide_files():
            self.clear_bg.grid_remove()
        self.drop_panels["Pictures"] = pictures

        world = self._panel(columns=(1,))
        self.world_rows: dict[str, tk.Label] = {}
        for index, label in enumerate(
            tuple(
                label for label in
                ("Place", "Doing", "Here for", "Extended", "Mochi", "Time", "Weather", "Goal", "Last event", "Memory", "Discovered")
                if label != "Mochi" or cast.has_chatter()
            )
        ):
            shown = {"Here for": "Here since", "Extended": "Stayed longer", "Last event": "Just now", "Memory": "Remembers",
                     "Discovered": "Found places"}.get(label, label)
            self._text(world, text=shown, fg=MUTED, font=("Segoe UI", 9)).grid(
                row=index, column=0, sticky="nw", padx=(0, 10), pady=1
            )
            value = self._text(world, text="", fg=TEXT, font=("Segoe UI", 9), wraplength=400, justify="left")
            value.grid(row=index, column=1, sticky="w", pady=1)
            self.world_rows[label] = value
        self.drop_panels["World"] = world
        self._wrap_text(560)
        for panel in self.drop_panels.values():
            panel.grid_remove()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Activate>", self._keep_plate_behind)
        self.bind("<Map>", self._on_map)
        self.bind("<Unmap>", self._on_unmap)
        self.opacity.set(self.opacity_value)
        self._chrome = [
            header,
            setting_header,
            self.prior,
            self.countdown,
            self.status,
            self.stop_button,
        ]
        self._compact = False
        self.help_bubble = tk.Button(
            self, text="?  Help", command=self.open_help, bg=ACCENT, fg=ACCENT_TEXT, activebackground=ACCENT_ACTIVE,
            activeforeground=ACCENT_TEXT, relief="flat", font=("Segoe UI", 10, "bold"), padx=14, pady=4, cursor="hand2",
        )
        self.help_bubble.place(relx=1.0, rely=1.0, x=-20, y=-14, anchor="se")
        self.watermark = self._text(self, text=WATERMARK, fg=MUTED, font=("Segoe UI", 9, "underline"), cursor="hand2")
        self.watermark.place(relx=0.0, rely=1.0, x=24, y=-10, anchor="sw")
        self.watermark.bind("<Button-1>", lambda _event: webbrowser.open(WATERMARK_URL), add="+")
        self.watermark.bind("<Enter>", lambda _event: self.watermark.configure(fg=TEXT), add="+")
        self.watermark.bind("<Leave>", lambda _event: self.watermark.configure(fg=MUTED), add="+")
        self._add_tips(self, everywhere=True)
        self._localize_widgets()
        self.update_idletasks()
        self.minsize(480, self.winfo_reqheight() + 30)
        self.geometry(f"560x{self.winfo_reqheight() + 30}")
        self.make_scalable(self)
        self.clock()
        self.after(100, self._sync_glass)
        self.after(400, self._resume_schedule)

    def _clock_text(self, value: str | None, default: str) -> str:
        if not value:
            return default
        try:
            return update_scene.format_clock(update_scene.parse_clock(value))
        except RuntimeError:
            return default

    def _text(self, parent: tk.Misc, **kwargs) -> tk.Label:
        kwargs.setdefault("bg", BG)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("padx", 0)
        kwargs.setdefault("pady", 0)
        kwargs.setdefault("highlightthickness", 0)
        return tk.Label(parent, **kwargs)

    def _labeled_entry(self, parent: tk.Frame, row: int, column: int, label: str, value: str, width: int) -> tk.Entry:
        self._text(parent, text=label, fg=MUTED, font=("Segoe UI", 10)).grid(
            row=row, column=column, sticky="w", padx=(0, 6), pady=4
        )
        entry = tk.Entry(
            parent,
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 11),
            width=width,
        )
        entry.insert(0, value)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=4)
        if label in help_guide.TIPS:
            Tooltip(self, entry, help_guide.TIPS[label])
        return entry

    def _drop_button(self, parent: tk.Frame, name: str, row: int, column: int) -> tk.Button:
        button = tk.Button(
            parent,
            text=DROP_LABELS.get(name, name),
            command=lambda: self.toggle_drop(name),
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL_ACTIVE,
            activeforeground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            padx=8,
            pady=6,
            cursor="hand2",
        )
        last = column == DROP_COLUMNS - 1
        button.grid(row=row, column=column, sticky="ew", padx=(0, 0 if last else 8), pady=(0 if row == 0 else 8, 0))
        return button

    def _panel(self, columns: tuple[int, ...] = ()) -> tk.Frame:
        """A panel that opens under the six buttons. All panels share one spot."""
        panel = tk.Frame(self.settings_inner, bg=BG)
        panel.grid(row=0, column=0, sticky="ew", padx=24, pady=(0, 64))
        for column in columns:
            panel.columnconfigure(column, weight=1)
        return panel

    def _hint(self, parent: tk.Frame, text: str, row: int) -> None:
        self._text(parent, text=text, fg=MUTED, font=("Segoe UI", 9)).grid(row=row, column=2, sticky="w")

    def _check(self, parent: tk.Frame, text: str, variable: tk.BooleanVar, row: int, command=None) -> None:
        tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=command,
            bg=BG,
            fg=TEXT,
            selectcolor=ENTRY,
            activebackground=BG,
            activeforeground=TEXT,
            relief="flat",
            highlightthickness=0,
            bd=0,
            font=("Segoe UI", 9),
            padx=0,
            pady=2,
            cursor="hand2",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _small_button(self, parent: tk.Frame, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL_ACTIVE,
            activeforeground=TEXT,
            relief="flat",
            font=("Segoe UI", 9),
            padx=8,
            pady=2,
            cursor="hand2",
        )

    def _text_box(self, parent: tk.Frame, value: str) -> tk.Text:
        box = tk.Text(
            parent,
            height=4,
            wrap="word",
            bg=ENTRY,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            padx=10,
            pady=8,
        )
        box.grid(row=0, column=0, sticky="ew")
        box.insert("1.0", value)
        return box

    def _button(
        self,
        parent: tk.Frame,
        text: str,
        command,
        bg: str,
        fg: str,
        column: int,
        disabled: bool = False,
    ) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=ACCENT_ACTIVE if bg == ACCENT else PANEL_ACTIVE,
            activeforeground=fg,
            disabledforeground=DISABLED,
            relief="flat",
            font=("Segoe UI", 12, "bold" if bg == ACCENT else "normal"),
            padx=12,
            pady=8,
            state="disabled" if disabled else "normal",
            cursor="hand2",
        )
        button.grid(row=0, column=column, sticky="ew", padx=(0, 8) if column < 2 else 0)
        return button

    def refresh_count(self) -> None:
        count = len(self.prior.get("1.0", "end-1c"))
        self.count_label.configure(text=f"{count}/{SCENE_LIMIT}", fg=WARN if count > SCENE_LIMIT else MUTED)

    def _show_compact(self, compact: bool) -> None:
        """Before the simulation starts, the picture fills the window. Change action and Pictures stay up."""
        if compact == self._compact:
            return
        self._compact = compact
        if compact and self.drop_open and self.drop_open != "Pictures":
            self.toggle_drop(self.drop_open)
        for widget in self._chrome:
            widget.grid_remove() if compact else widget.grid()
        if compact:
            self.idle_background.grid(row=0, column=1, sticky="ew")
        else:
            self.idle_background.grid_remove()
        self._glass_sig = None

    def _label_rects(self) -> list[tuple[int, int, int, int]]:
        origin_x = self.winfo_rootx()
        origin_y = self.winfo_rooty()
        rects = []
        for widget in self._each_widget():
            if widget.winfo_class() != "Label" or not widget.winfo_ismapped():
                continue
            if not str(widget.cget("text")).strip():
                continue
            x = widget.winfo_rootx() - origin_x
            y = widget.winfo_rooty() - origin_y
            rects.append((x - 3, y - 1, x + widget.winfo_width() + 3, y + widget.winfo_height() + 1))
        return rects

    def clock(self) -> None:
        try:
            now = update_scene.la_now()
            label = now.strftime("%I:%M %p").lstrip("0")
            place = str(update_scene.zone()).split("/")[-1].replace("_", " ")
            self.clock_label.configure(text=f"{label} {place}")
            if self.phase == "waiting" and self._bedtime_now(now):
                self._fall_asleep_on_time()
            elif self.phase == "waiting":
                self.follow_schedule(now)
            elif self.phase == "asleep":
                self._follow_sleep(now)
            elif self.phase == "idle":
                self.maybe_autostart(now)
            if time.monotonic() >= self.next_world_refresh:
                self.refresh_world()
            self._advance_slide()
            self._sync_glass()
            self._check_for_update()
            self._show_mode()
            self._show_dream_now()
        except Exception as caught:
            self.log(f"Could not update the window. {caught}")
        self.clock_id = self.after(1000, self.clock)

    def _on_opacity(self, value: str) -> None:
        opacity = max(0, min(100, int(float(value))))
        if opacity == self.opacity_value:
            return
        self.opacity_value = opacity
        self.opacity_label.configure(text=f"{opacity}%")
        self._queue_glass()
        if self._opacity_save is not None:
            self.after_cancel(self._opacity_save)
        self._opacity_save = self.after(300, self._save_opacity)

    def _save_opacity(self) -> None:
        self._opacity_save = None
        prefs = update_scene.load_prefs()
        prefs["opacity"] = str(self.opacity_value)
        update_scene.save_prefs(prefs)

    def _save_slide_seconds(self, _event=None) -> None:
        try:
            value = int(self.slide_every.get().strip())
        except ValueError:
            value = self.slide_seconds
        value = max(1, min(value, 3600))
        self.slide_seconds = value
        if self.slide_every.get().strip() != str(value):
            self.slide_every.delete(0, "end")
            self.slide_every.insert(0, str(value))
        self.next_slide = time.monotonic() + value
        prefs = update_scene.load_prefs()
        prefs["slide_seconds"] = str(value)
        update_scene.save_prefs(prefs)

    def _current_slide(self) -> Path | None:
        files = slide_files()
        if not files:
            return None
        self.slide_index %= len(files)
        return files[self.slide_index]

    def _advance_slide(self) -> None:
        if time.monotonic() < self.next_slide:
            return
        self.next_slide = time.monotonic() + self.slide_seconds
        files = slide_files()
        if len(files) < 2:
            return
        self.slide_index = (self.slide_index + 1) % len(files)

    def _queue_glass(self) -> None:
        if self._glass_job is None:
            self._glass_job = self.after(15, self._run_glass_job)

    def _run_glass_job(self) -> None:
        self._glass_job = None
        self._sync_glass()

    def _sync_glass(self) -> None:
        path = self._current_slide()
        if path is None:
            if self._glass:
                self._leave_glass()
            return
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 20 or height < 20 or self.state() == "iconic":
            return
        if not self._glass:
            self._enter_glass()
        self.update_idletasks()
        rects = tuple(self._label_rects())
        sig = (str(path), width, height, self.opacity_value, rects)
        if sig != self._glass_sig:
            self._glass_sig = sig
            try:
                self._draw_plate(path, width, height, rects)
            except Exception as caught:
                self.log(f"Could not show the background. {caught}")
        self._place_plate()

    def _enter_glass(self) -> None:
        self._ensure_plate()
        self._solid = {}
        for widget in self._each_widget():
            kind = widget.winfo_class()
            if kind in ("Tk", "Frame", "Label"):
                keys = ("bg",)
            else:
                continue
            self._solid[widget] = {key: str(widget.cget(key)) for key in keys}
            widget.configure(**{key: KEY for key in keys})
        self.attributes("-transparentcolor", KEY)
        self.plate.deiconify()
        self.plate.update_idletasks()
        self._glass = True
        self._glass_sig = None
        self._plate_spot = None

    def _leave_glass(self) -> None:
        self.attributes("-transparentcolor", "")
        for widget, colors in self._solid.items():
            if widget.winfo_exists():
                widget.configure(**colors)
        self._solid = {}
        self._bases = {}
        if self.plate is not None:
            self.plate.withdraw()
        self._glass = False
        self._glass_sig = None

    def _rgb(self, color: str) -> tuple[int, int, int]:
        red, green, blue = self.winfo_rgb(color)
        return (red >> 8, green >> 8, blue >> 8)

    def _draw_plate(self, path: Path, width: int, height: int, rects=()) -> None:
        from PIL import Image, ImageTk

        key = (str(path), width, height)
        base = self._bases.get(key)
        if base is None:
            if len(self._bases) > 20 or any(size[1:] != (width, height) for size in self._bases):
                self._bases = {}
            base = fit_image(path, width, height)
            self._bases[key] = base
        level = self.opacity_value / 100
        image = Image.blend(base, Image.new("RGB", (width, height), self._rgb(BG)), level)
        shade = Image.new("RGB", (1, 1), (8, 6, 14))
        for left, top, right, bottom in rects:
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)
            if right <= left or bottom <= top:
                continue
            patch = image.crop((left, top, right, bottom))
            tint = shade.resize(patch.size)
            image.paste(Image.blend(patch, tint, 0.62), (left, top))
        photo = self._plate_photo
        if photo is not None and (photo.width(), photo.height()) == (width, height):
            photo.paste(image)
        else:
            self._plate_photo = ImageTk.PhotoImage(image)
            self.plate_label.configure(image=self._plate_photo)

    def _keep_plate_behind(self, _event=None) -> None:
        if self._glass:
            self._place_plate(force=True)

    def _on_map(self, event) -> None:
        if event.widget is self and self._glass and self.plate is not None:
            self.plate.deiconify()
            self.plate.update_idletasks()
            self._place_plate(force=True)

    def _on_unmap(self, event) -> None:
        if event.widget is self and self.plate is not None:
            self.plate.withdraw()

    def _close(self) -> None:
        self.cancel_wand()
        threading.Thread(target=wand.shutdown, daemon=True).start()
        if self.plate is not None:
            self.plate.destroy()
            self.plate = None
        self.destroy()

    def _each_widget(self, everywhere: bool = False):
        """The window's widgets, on every page (the picture shows behind them). Other windows are left out."""
        stack = [self]
        while stack:
            widget = stack.pop()
            if widget is self.opacity or widget is self.plate:
                continue
            if isinstance(widget, tk.Toplevel):
                continue
            yield widget
            stack.extend(widget.winfo_children())

    def _ensure_plate(self) -> None:
        if self.plate is not None:
            return
        self.plate = tk.Toplevel(self)
        self.plate.withdraw()
        self.plate.overrideredirect(True)
        self.plate_label = tk.Label(self.plate, bd=0, highlightthickness=0, bg=KEY)
        self.plate_label.pack(fill="both", expand=True)
        self.plate_label.bind("<ButtonPress-1>", self._grab_from_picture)
        self.plate.update_idletasks()
        let_clicks_through(ctypes.windll.user32.GetAncestor(self.plate.winfo_id(), 2))

    def _grab_from_picture(self, _event=None) -> None:
        """The picture window never activates, so a click on it brings the window forward and drags it."""
        drag_window(ctypes.windll.user32.GetAncestor(self.winfo_id(), 2))

    def _place_plate(self, force: bool = False) -> None:
        if self.plate is None or not self._glass:
            return
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 20 or height < 20:
            return
        x = self.winfo_rootx()
        y = self.winfo_rooty()
        spot = (x, y, width, height)
        moved = spot != self._plate_spot or force
        self._plate_spot = spot
        if moved:
            self.plate.geometry(f"{width}x{height}+{x}+{y}")
        user32 = ctypes.windll.user32
        plate_hwnd = user32.GetAncestor(self.plate.winfo_id(), 2)
        main_hwnd = user32.GetAncestor(self.winfo_id(), 2)
        let_clicks_through(plate_hwnd)
        flags = 0x0010
        if not moved:
            flags |= 0x0001 | 0x0002
        user32.SetWindowPos(plate_hwnd, main_hwnd, x, y, width, height, flags)

    def maybe_autostart(self, now) -> None:
        if not simulation.schedule()["running"]:
            return
        try:
            options = self.read_options()
        except RuntimeError:
            return
        if not update_scene.should_autostart(
            now,
            options["wake"],
            options["quiet_from"],
            options["quiet_to"],
            self.last_greet_date,
            self.stopped_on,
        ):
            if (
                time.monotonic() >= self.note_until
                and self.stopped_on != now.date().isoformat()
                and self.last_greet_date != now.date().isoformat()
            ):
                wake = update_scene.format_clock(options["wake"])
                self.countdown.configure(text=f"Starts at {wake}.")
            return
        self.store_prefs(options)
        self.last_greet_date = now.date().isoformat()
        self._save_greet_date()
        wake = options["wake_text"]
        self.log(f"Morning. Setting what she is doing for {wake}.")
        self.shift_setting(options, morning=True)

    def log(self, text: str) -> None:
        message = " ".join(text.split())
        simulation.log_event(message)
        lowered = message.lower()
        if (
            lowered.startswith("could not")
            or lowered.startswith("minutes")
            or lowered.startswith("every")
            or lowered.startswith("use times")
            or lowered.startswith("set every")
            or "something went wrong" in lowered
        ):
            self.note(message)

    def _say(self, message: str) -> None:
        self.log(message)
        self.note(message)

    def note(self, message: str) -> None:
        self.note_until = time.monotonic() + 8
        self.countdown.configure(text=cast.localize(message))

    def _localize_widgets(self) -> None:
        """Show the names from setup on every button and label."""
        for widget in self._each_widget(everywhere=True):
            try:
                text = widget.cget("text")
            except (tk.TclError, AttributeError):
                continue
            if isinstance(text, str) and text:
                widget.configure(text=cast.localize(text))
        if not cast.has_chatter():
            self.drop_buttons["Mochi"].configure(text="Side character")
            self.wand_on.set(False)
            for child in self.drop_panels["Mochi"].winfo_children():
                child.grid_remove()
            self._text(
                self.drop_panels["Mochi"], text="No side character. Add one in Settings > App > Change setup.", fg=MUTED,
                font=("Segoe UI", 9),
            ).grid(row=0, column=0, sticky="w")

    def wipe_all(self) -> None:
        """Remove everything saved about you (the DeepSeek key can stay), then open setup like the first time."""
        if self.phase not in ("idle",) or getattr(self, "heads_up_busy", False) or self.wand_gate.locked():
            self._say("Press Stop first, and wait until the Narrator is done.")
            return
        if not messagebox.askokcancel(
            "Wipe everything",
            "This removes your keys, names and profile IDs, the world and memories, the log, the Kindroid login "
            "in the app's browser, and your pictures. Then setup opens like the first time.\n\nThis can't be undone.",
            icon="warning", parent=self,
        ):
            return
        keep = messagebox.askyesno(
            "Keep the DeepSeek key?",
            "Keep your DeepSeek key, so you don't have to paste it again?\n\nChoose No before you share the app with someone.",
            parent=self,
        )
        self.cancel_wand()
        wand.shutdown()
        left = wipe.wipe(keep_deepseek=keep)
        if left:
            messagebox.showwarning(
                "Wipe", "Some things could not be removed yet: " + ", ".join(left) + ".\n"
                "The app opens again now: wipe once more in Settings > App to finish.",
                parent=self,
            )
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen([sys.executable, str(APP_DIR / "gui.py")], cwd=str(APP_DIR), creationflags=flags, close_fds=True)
        except Exception:
            pass
        if self.plate is not None:
            self.plate.destroy()
            self.plate = None
        self.destroy()

    def change_setup(self) -> None:
        """Open setup again. The app restarts with the new names."""
        self.log("Opening setup. The app restarts when it is done.")
        self.restart_app(setup=True)

    def suggest_places(self) -> None:
        self.suggest_button.configure(state="disabled")
        self.suggest_note.configure(text="Asking DeepSeek for places...")
        about = self.backstory.get("1.0", "end").strip()
        existing = self.environment.get("1.0", "end").strip()

        def work() -> None:
            try:
                lines, error = simulation.suggest_places(about, update_scene.era(), existing), ""
            except Exception as caught:
                lines, error = [], str(caught)
            try:
                self.after(0, lambda: self._suggested(lines, error))
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=work, daemon=True).start()

    def _suggested(self, lines: list[str], error: str) -> None:
        self.suggest_button.configure(state="normal")
        if error:
            self.suggest_note.configure(text=f"DeepSeek could not suggest places. {error}"[:110])
            return
        current = self.environment.get("1.0", "end").strip()
        self.environment.delete("1.0", "end")
        self.environment.insert("1.0", (current + "\n" if current else "") + "\n".join(lines))
        self.environment.see("end")
        self.suggest_note.configure(text=f"Added {len(lines)} places at the end. Press again for more.")

    def refresh_world(self) -> None:
        self.next_world_refresh = time.monotonic() + 30
        try:
            info = simulation.summary(self.environment.get("1.0", "end").strip())
        except Exception as caught:
            self.status.configure(text=f"World could not be read. {caught}")
            return
        if self.phase == "asleep":
            state = "Asleep"
        elif self.phase in ("waiting", "busy"):
            state = "Running"
        elif not info["running"]:
            state = "Stopped"
        else:
            state = "Idle"
        parts = [state]
        if info.get("last_at"):
            parts.append(f"last {simulation.clock_text(info['last_at'])}")
        if self.phase == "waiting":
            upcoming = update_scene.la_now() + timedelta(seconds=max(0, self.deadline - time.monotonic()))
            parts.append(f"next {simulation.clock_text(upcoming)}")
        if info.get("pending"):
            parts.append("Kindroid update pending")
        self.status.configure(text=" · ".join(parts))
        if not info.get("ready"):
            for value in self.world_rows.values():
                value.configure(text="")
            self.world_rows["Place"].configure(text="Starts on the first change.")
            return
        try:
            here = simulation.here_summary()
        except Exception:
            here = {"how_long": "", "extends": 0}
        extends = int(here.get("extends") or 0)
        if extends:
            extended = f"{extends} time{'' if extends == 1 else 's'} in a row"
            if here.get("part") == "bedtime":
                extended += " · bedtime, no nudges"
            elif extends >= update_scene.EXTEND_LIMIT:
                extended += " · the Narrator moves her at the next change"
            elif extends >= update_scene.EXTEND_SUGGEST_AFTER:
                extended += " · the heads-up suggests a change"
        else:
            extended = "not yet"
        goal = info["goal"] + (f" · {info['progress']}" if info["progress"] else "") if info["goal"] else "none"
        rows = {
            "Place": info["place"],
            "Doing": info["activity"] + (f" · about {info['minutes']} min" if info.get("minutes") else ""),
            "Here for": here.get("how_long") or "just arrived",
            "Extended": extended,
            "Mochi": info["mochi"],
            "Time": info["time"],
            "Weather": f"{info['weather']} · {info['lighting']}".strip(" ·"),
            "Goal": goal,
            "Last event": info["event"],
            "Memory": "\n".join(f"- {text}" for text in info["memories"]) or "none yet",
            "Discovered": ", ".join(info["discovered"][:5]) or "none yet",
        }
        for label, text in rows.items():
            if label in self.world_rows:
                self.world_rows[label].configure(text=cast.localize(text))

    # The Narrator panel: two simple lists, her commands and the heads-up message ----------------------

    def _build_narrator_panel(self, panel: tk.Frame) -> None:
        panel.columnconfigure(0, weight=3, uniform="narrator")
        panel.columnconfigure(1, weight=2, uniform="narrator")
        self._narrator_loading = False

        left = tk.Frame(panel, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.columnconfigure(0, weight=1)
        self._text(left, text="What Lora can ask the Narrator", fg=TEXT, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.nar_rows_frame = tk.Frame(left, bg=BG)
        self.nar_rows_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.nar_rows_frame.columnconfigure(0, weight=1)
        self.nar_rows_frame.columnconfigure(2, weight=1)
        self._small_button(left, "+ Add command", self._narrator_add).grid(row=2, column=0, sticky="w", pady=(6, 0))

        right = tk.Frame(panel, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        self._text(right, text="Warning, 1 minute before a change", fg=TEXT, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.nar_message = tk.Text(right, height=11, width=30, wrap="word", bg=ENTRY, fg=TEXT, insertbackground=TEXT,
                                   relief="flat", font=("Segoe UI", 10), padx=8, pady=6)
        self.nar_message.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self.nar_message.bind("<KeyRelease>", lambda _event: self._narrator_unsaved())
        self._text(right, text="{where} and {wait} fill in by themselves.", fg=MUTED,
                   font=("Segoe UI", 8)).grid(row=2, column=0, sticky="w")
        self._text(right, text="Before each dream", fg=TEXT, font=("Segoe UI", 11, "bold")).grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.nar_dream = tk.Text(right, height=4, width=30, wrap="word", bg=ENTRY, fg=TEXT, insertbackground=TEXT,
                                 relief="flat", font=("Segoe UI", 10), padx=8, pady=6)
        self.nar_dream.grid(row=4, column=0, sticky="nsew", pady=(4, 0))
        self.nar_dream.bind("<KeyRelease>", lambda _event: self._narrator_unsaved())
        self._text(right, text="what the Narrator says first", fg=MUTED,
                   font=("Segoe UI", 8)).grid(row=5, column=0, sticky="w")

        bottom = tk.Frame(panel, bg=BG)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(3, weight=1)
        self._small_button(bottom, "Save", self._narrator_save).grid(row=0, column=0, sticky="w")
        self._small_button(bottom, "Preview", self._narrator_preview).grid(row=0, column=1, sticky="w", padx=(6, 0))
        self._small_button(bottom, "Reset to default", self._narrator_reset).grid(row=0, column=2, sticky="w", padx=(6, 0))
        self.nar_status = self._text(bottom, text="", fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=540)
        self.nar_status.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._narrator_show(narrator_commands.settings(update_scene.load_prefs()))

    def _narrator_show(self, found: dict) -> None:
        self._narrator_loading = True
        self.nar_rows = [
            {"say": tk.StringVar(value=c["say"]), "what": tk.StringVar(value=narrator_commands.what_text(c)),
             "orig": c["say"], "new": False}
            for c in found["commands"]
        ]
        self.nar_message.delete("1.0", "end")
        self.nar_message.insert("1.0", found["message"])
        self.nar_dream.delete("1.0", "end")
        self.nar_dream.insert("1.0", found.get("dream", ""))
        self._narrator_draw_rows()
        self._narrator_loading = False

    def _narrator_draw_rows(self) -> None:
        """One row per command:  what Lora says  ->  what happens  [pick]  [x]"""
        for child in self.nar_rows_frame.winfo_children():
            child.destroy()
        self._text(self.nar_rows_frame, text=narrator_commands._named("Lora says"), fg=MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        self._text(self.nar_rows_frame, text="What happens", fg=MUTED, font=("Segoe UI", 9)).grid(row=0, column=2, sticky="w")
        self.nar_say_boxes = []
        self.nar_what_boxes = []
        for index, row in enumerate(self.nar_rows, start=1):
            say = tk.Entry(self.nar_rows_frame, textvariable=row["say"], bg=ENTRY, fg=TEXT, insertbackground=TEXT,
                           relief="flat", font=("Segoe UI", 10), width=16)
            say.grid(row=index, column=0, sticky="ew", pady=2, ipady=2)
            self._text(self.nar_rows_frame, text="→", fg=MUTED, font=("Segoe UI", 10)).grid(row=index, column=1, padx=6)
            what = tk.Entry(self.nar_rows_frame, textvariable=row["what"], bg=ENTRY, fg=TEXT, insertbackground=TEXT,
                            relief="flat", font=("Segoe UI", 10), width=16)
            what.grid(row=index, column=2, sticky="ew", pady=2, ipady=2)
            pick = tk.Menubutton(self.nar_rows_frame, text="▾", bg=PANEL, fg=TEXT, activebackground=PANEL_ACTIVE,
                                 activeforeground=TEXT, relief="flat", font=("Segoe UI", 10), padx=6, cursor="hand2")
            menu = tk.Menu(pick, tearoff=0, bg=PANEL, fg=TEXT, activebackground=ACCENT, activeforeground=ACCENT_TEXT)
            for _key, label in narrator_commands.ACTIONS:
                menu.add_command(label=label, command=lambda row=row, label=label: self._narrator_pick(row, label))
            menu.add_separator()
            menu.add_command(label="Something else: type it in the box", command=lambda what=what: self._narrator_type_own(what))
            pick.configure(menu=menu)
            pick.grid(row=index, column=3, padx=(4, 0), pady=2)
            self._small_button(self.nar_rows_frame, "✕", lambda row=row: self._narrator_delete(row)).grid(
                row=index, column=4, padx=(4, 0), pady=2
            )
            for box in (say, what):
                box.bind("<KeyRelease>", lambda _event: self._narrator_unsaved())
            self.nar_say_boxes.append(say)
            self.nar_what_boxes.append(what)
        self._add_tips(self.nar_rows_frame)
        if str(self) in getattr(self, "_font_sets", {}):
            self.make_scalable(self, self.nar_rows_frame)
        self._queue_glass()

    def _narrator_pick(self, row: dict, label: str) -> None:
        row["what"].set(label)
        self._narrator_unsaved()

    def _narrator_type_own(self, box: tk.Entry) -> None:
        box.delete(0, "end")
        box.focus_set()
        self._narrator_unsaved(narrator_commands._named("Type what happens, like: Mochi brings Lora a flower"))

    def _narrator_add(self) -> None:
        self.nar_rows.append({"say": tk.StringVar(value="narrator "), "what": tk.StringVar(value=""), "orig": "", "new": True})
        self._narrator_draw_rows()
        box = self.nar_say_boxes[-1]
        box.focus_set()
        box.icursor("end")
        self._narrator_unsaved(narrator_commands._named("Type what Lora says, then what happens (or pick with ▾), then Save."))

    def _narrator_command(self, row: dict) -> dict:
        does, what = narrator_commands.does_from(row["what"].get())
        return {"say": " ".join(row["say"].get().split()), "does": does, "what": what}

    def _narrator_delete(self, row: dict) -> None:
        command = self._narrator_command(row)
        self.nar_rows.remove(row)
        message = self.nar_message.get("1.0", "end").strip()
        if command["say"] and not row["new"]:
            old = dict(command, say=row["orig"] or command["say"])
            message = narrator_commands.without_lines_of(message, old)
            self.nar_message.delete("1.0", "end")
            self.nar_message.insert("1.0", message)
        self._narrator_draw_rows()
        self._narrator_unsaved(f'Deleted "{command["say"]}" and its line in the message. Press Save to keep it.')

    def _narrator_current(self) -> dict:
        """What the boxes say now, saved or not."""
        return {
            "message": self.nar_message.get("1.0", "end").strip(),
            "dream": self.nar_dream.get("1.0", "end").strip(),
            "commands": [self._narrator_command(row) for row in self.nar_rows],
        }

    def _narrator_unsaved(self, text: str = "") -> None:
        if self._narrator_loading:
            return
        self.nar_status.configure(text=text or "Not saved yet.", fg=WARN)

    def _narrator_save(self) -> None:
        current = self._narrator_current()
        wrong = narrator_commands.problems(current)
        if wrong:
            self.nar_status.configure(text=wrong[0], fg=WARN)
            return
        message = current["message"]
        says = [command["say"] for command in current["commands"]]
        added = []
        for row, command in zip(self.nar_rows, current["commands"]):
            if row["new"]:
                before = message
                message = narrator_commands.with_line_for(message, command)
                if message != before:
                    added.append(command["say"])
            else:
                message = narrator_commands.renamed(message, row["orig"], command["say"], [s for s in says if s != command["say"]])
        current["message"] = message
        current["commands"] = [narrator_commands.clean_command(command) for command in current["commands"]]
        prefs = update_scene.load_prefs()
        prefs["narrator"] = current
        update_scene.save_prefs(prefs)
        self._narrator_show(narrator_commands.settings(prefs))
        note = "Saved. The next heads-up uses these."
        if added:
            note += " A line about " + ", ".join(f'"{say}"' for say in added) + " was added to the message."
        self.nar_status.configure(text=note, fg=MUTED)
        self.log(f"Narrator's heads-up and commands saved ({len(current['commands'])} commands).")

    def _narrator_reset(self) -> None:
        self._narrator_show(narrator_commands.defaults())
        self._narrator_unsaved("Back to the default messages and commands. Press Save to keep them.")

    def narrator_preview_text(self) -> str:
        current = self._narrator_current()
        current["commands"] = [c for c in (narrator_commands.clean_command(c) for c in current["commands"]) if c]
        return update_scene.heads_up_message("1 minute", "at the fireplace", "40 minutes", 0, narrator=current)

    def _narrator_preview(self) -> None:
        window = tk.Toplevel(self)
        window.title("Heads-up preview")
        window.configure(bg=BG)
        window.transient(self)
        self._text(window, text="What the narrator will send (with an example place and time):", fg=MUTED,
                   font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(10, 4))
        box = tk.Text(window, width=70, height=14, wrap="word", bg=ENTRY, fg=TEXT, relief="flat",
                      font=("Segoe UI", 10), padx=10, pady=8)
        box.pack(fill="both", expand=True, padx=12)
        box.insert("1.0", self.narrator_preview_text())
        box.configure(state="disabled")
        self._small_button(window, "Close", window.destroy).pack(anchor="e", padx=12, pady=10)

    def open_help(self) -> None:
        if self.help_win is None:
            self.help_win = HelpChat(self)
            self.help_win.grid(row=6, column=0, sticky="nsew", padx=24, pady=(4, 36))
            self.help_win.grid_remove()
            self.make_scalable(self, self.help_win)
        self.show_page("help")
        self.help_win.entry.focus_set()

    def scaled_font(self, size: int, weight: str = "normal") -> tkfont.Font:
        """A font that grows with the window, like every other text in it."""
        entry = self._font_sets.setdefault(str(self), {"window": self, "fonts": {}, "names": set(), "scale": 1.0})
        key = ("Segoe UI", size, weight, "roman", 0)
        if key not in entry["fonts"]:
            font = tkfont.Font(root=self, family="Segoe UI", size=max(1, round(size * entry["scale"])), weight=weight)
            entry["fonts"][key] = (font, size)
            entry["names"].add(str(font))
        return entry["fonts"][key][0]

    def _nav(self) -> None:
        if self.page == "home":
            self.open_settings()
        else:
            self.show_page("home")

    def show_page(self, page: str) -> None:
        """home: Start, Stop, and Sleep. settings: every setting. help: the Help chat. All in this one window."""
        if page == self.page:
            return
        if self.page == "home" and self.state() != "zoomed":
            self._home_height = self.winfo_height()
        for part in self._home_parts:
            part.grid() if page == "home" else part.grid_remove()
        self.settings_page.grid() if page == "settings" else self.settings_page.grid_remove()
        if self.help_win is not None:
            self.help_win.grid() if page == "help" else self.help_win.grid_remove()
        if page == "help":
            self.help_bubble.place_forget()
        else:
            self.help_bubble.place(relx=1.0, rely=1.0, x=-20, y=-14, anchor="se")
        self.nav_button.configure(text="Settings" if page == "home" else "\u2190 Back")
        self.page = page
        self._dream_now_shown = None
        self._show_dream_now()
        self._fit_page()
        self._queue_glass()

    def _fit_page(self) -> None:
        """Tall enough for the page, not taller than the screen. A window you made bigger stays as big."""
        self.update_idletasks()
        if self.state() == "zoomed":
            return
        room = self.winfo_screenheight() - 100
        if self.page == "home":
            need = self.winfo_reqheight() + 30
            self.minsize(480, need)
            height = max(need, self._home_height)
        else:
            self.minsize(480, 360)
            need = self.winfo_reqheight() + (self.settings_inner.winfo_reqheight() if self.page == "settings" else 0)
            height = max(self.winfo_height(), min(room, need))
        self.geometry(f"{self.winfo_width()}x{min(room, height)}")

    def _settings_scrolled(self) -> None:
        """The scroll area is as tall as the open part. The bar shows only when it doesn't all fit."""
        canvas = self.settings_canvas
        tall = self.settings_inner.winfo_reqheight()
        canvas.configure(scrollregion=(0, 0, 0, tall))
        if tall > canvas.winfo_height() + 2:
            self.settings_bar.grid()
        else:
            self.settings_bar.grid_remove()
            canvas.yview_moveto(0)

    def _on_wheel(self, event) -> None:
        """The mouse wheel scrolls Settings. A text box that can scroll by itself scrolls itself instead."""
        if self.page != "settings" or not self.settings_bar.winfo_ismapped():
            return
        widget = event.widget
        if not str(widget).startswith(str(self.settings_page)):
            return
        if isinstance(widget, tk.Text) and widget.yview() != (0.0, 1.0):
            return
        steps = -round(event.delta / 120) or (-1 if event.delta > 0 else 1)
        self.settings_canvas.yview_scroll(steps, "units")

    def _add_tips(self, root: tk.Misc, everywhere: bool = False) -> None:
        """A hover tip on every option that has one (help_guide.TIPS), with the names from setup or not."""
        named = {cast.localize(key): tip for key, tip in help_guide.TIPS.items()}
        extra = {"▾": "Pick what happens from a list.", "✕": "Deletes this line."}
        widgets = self._each_widget(everywhere=True) if everywhere else self._walk(root)
        for widget in widgets:
            try:
                text = str(widget.cget("text")).strip()
            except (tk.TclError, AttributeError):
                continue
            tip = help_guide.TIPS.get(text) or named.get(text) or extra.get(text)
            if tip and not getattr(widget, "_has_tip", False):
                widget._has_tip = True
                Tooltip(self, widget, tip)

    def _walk(self, root: tk.Misc):
        """root and everything in it, but not other windows opened from it."""
        stack = [root]
        while stack:
            widget = stack.pop()
            yield widget
            stack.extend(child for child in widget.winfo_children() if not isinstance(child, tk.Toplevel))

    def make_scalable(self, window: tk.Misc, root: tk.Misc | None = None) -> None:
        """The window's text grows when the window is made bigger (up to MAX_TEXT_SCALE), and shrinks back."""
        new = "base_width" not in self._font_sets.get(str(window), {})  # fonts may be asked for before it is set up
        entry = self._font_sets.setdefault(str(window), {"window": window, "fonts": {}, "names": set(), "scale": 1.0})
        for widget in self._walk(root or window):
            try:
                current = widget.cget("font")
            except (tk.TclError, AttributeError):
                continue
            if not current or str(current) in entry["names"]:
                continue
            try:
                actual = dict(zip(*[iter(widget.tk.splitlist(widget.tk.call("font", "actual", current)))] * 2))
            except tk.TclError:
                continue
            size = abs(int(actual.get("-size", 10))) or 10
            key = (
                actual.get("-family", "Segoe UI"), size, actual.get("-weight", "normal"), actual.get("-slant", "roman"),
                int(actual.get("-underline", 0) or 0),
            )
            if key not in entry["fonts"]:
                font = tkfont.Font(
                    root=window, family=key[0], size=max(1, round(size * entry["scale"])), weight=key[2], slant=key[3],
                    underline=key[4],
                )
                entry["fonts"][key] = (font, size)
                entry["names"].add(str(font))
            widget.configure(font=entry["fonts"][key][0])
        if new:
            window.update_idletasks()
            entry["base_width"] = max(window.winfo_width(), window.winfo_reqwidth(), 300)
            entry["natural_height"] = max(200, window.winfo_reqheight())
            if window is not self:
                window.bind("<Configure>", lambda event, window=window: event.widget is window and self._rescale(window), add="+")

    def text_scale(self, window: tk.Misc) -> float:
        return self._font_sets.get(str(window), {}).get("scale", 1.0)

    def _rescale(self, window: tk.Misc) -> None:
        entry = self._font_sets.get(str(window))
        if not entry or window.winfo_width() < 50:
            return
        scale = entry["scale"]
        wide = window.winfo_width() / entry["base_width"]
        tall = window.winfo_height() / entry.get("natural_height", max(1.0, window.winfo_reqheight() / scale))
        target = max(1.0, min(MAX_TEXT_SCALE, wide, tall))
        target = min(MAX_TEXT_SCALE, round(target * 4) / 4)  # quarter steps, so small drags don't redraw every letter
        if target == scale:
            return
        entry["scale"] = target
        for font, size in entry["fonts"].values():
            font.configure(size=max(1, round(size * target)))
        if window is self:
            self._wrap_room = None
            self._wrap_text(self.winfo_width())

    def _show_legal(self, file_name: str) -> None:
        """Settings > Legal: the Terms of Service or the Privacy Policy, from the files that come with the app.

        Laid out for reading: the first line is the title, lines in CAPITALS are headings, "- " lines are a list.
        """
        try:
            text = (APP_DIR / file_name).read_text(encoding="utf-8")
        except OSError:
            text = f"{file_name} is missing from the app's folder. Download the app again to get it."
        self.legal_shown = file_name
        box = self.legal_text
        box.tag_configure("title", font=self.scaled_font(17, "bold"), foreground=KIN, spacing3=2)
        box.tag_configure("date", foreground=MUTED, font=self.scaled_font(10), spacing3=10)
        box.tag_configure("heading", font=self.scaled_font(13, "bold"), foreground=TEXT, spacing1=18, spacing3=6)
        box.tag_configure("body", foreground=TEXT, spacing3=4)
        box.tag_configure("item", foreground=TEXT, lmargin1=10, lmargin2=28, spacing3=5)
        box.configure(state="normal")
        box.delete("1.0", "end")
        lines = [line.rstrip() for line in text.strip().splitlines()]
        for index, line in enumerate(lines):
            if not line.strip() or set(line.strip()) <= {"=", "-"}:
                continue
            if index == 0:
                tag = "title"
            elif line.lower().startswith(("updated", "last updated")):
                tag = "date"
            elif line.strip().startswith("- "):
                tag, line = "item", "\u2022  " + line.strip()[2:]
            elif re.fullmatch(r"[A-Z0-9 ,'&/()-]+", line.strip()) and re.search(r"[A-Z]", line):
                tag = "heading"
            else:
                tag = "body"
            box.insert("end", line.strip() + "\n", (tag,))
        box.configure(state="disabled")
        self.legal_text.yview_moveto(0)
        for name, button in self.legal_buttons.items():
            button.configure(bg=ACCENT if name == file_name else PANEL, fg=ACCENT_TEXT if name == file_name else TEXT)

    def _show_theme(self) -> None:
        """Each color square shows its color. The note says if anything is hard to read."""
        for role, button in self.color_buttons.items():
            color = self.theme_colors[role]
            ink = "#ffffff" if theme.contrast("#ffffff", color) >= theme.contrast("#141414", color) else "#141414"
            button.configure(text=color, bg=color, fg=ink, activebackground=color, activeforeground=ink)
        issues = theme.problems(self.theme_colors)
        if issues:
            text = f"Now: {self.theme_name}. Hard to read: " + "; ".join(issues).lower() + ". Try a lighter or darker color."
        else:
            text = f"Now: {self.theme_name}. Everything is easy to read."
        self.theme_note.configure(text=text, fg=WARN if issues else MUTED)

    def pick_color(self, role: str, label: str) -> None:
        chosen = colorchooser.askcolor(color=self.theme_colors[role], title=f"Pick a color: {label}", parent=self)
        if chosen and chosen[1]:
            self.set_theme("Custom", dict(self.theme_colors, **{role: str(chosen[1]).lower()}))

    def set_theme(self, name: str, colors: dict) -> None:
        """Change every color now, and keep it for next time."""
        old, new = dict(PALETTE), theme.full(colors)
        glass = self._glass
        if glass:
            self._leave_glass()
        use_palette(new)
        self._recolor(old, new)
        self.theme_name = name
        self.theme_colors = {role: new[role] for role, _label in theme.ROLES}
        prefs = update_scene.load_prefs()
        prefs["theme"] = theme.saved(name, self.theme_colors)
        update_scene.save_prefs(prefs)
        self._show_theme()
        self._mode_shown = None
        self._show_mode()
        if self.drop_open:
            self._mark_drop(self.drop_open, True)
        if self.help_win is not None:
            self.help_win.recolor()
        if getattr(self, "legal_shown", ""):
            self._show_legal(self.legal_shown)
        self._glass_sig = None
        if glass:
            self._sync_glass()

    # Which theme color each widget color was. Background colors and text colors are looked up apart,
    # so a background and a text that happen to share a color each get their own new color.
    BACK_ROLES = ("bg", "panel", "entry", "accent", "panel_active", "accent_active", "tip_bg")
    FORE_ROLES = ("text", "muted", "accent_text", "disabled", "warn", "you", "kin")
    BACK_OPTIONS = ("bg", "activebackground", "selectcolor", "troughcolor", "highlightbackground")
    FORE_OPTIONS = ("fg", "activeforeground", "disabledforeground", "insertbackground")

    def _recolor(self, old: dict, new: dict) -> None:
        back = {old[role].lower(): new[role] for role in reversed(self.BACK_ROLES)}
        fore = {old[role].lower(): new[role] for role in reversed(self.FORE_ROLES)}
        for widget in self._walk(self):
            if getattr(widget, "_swatch", False):
                continue
            for options, table in ((self.BACK_OPTIONS, back), (self.FORE_OPTIONS, fore)):
                for option in options:
                    try:
                        value = str(widget.cget(option)).lower()
                    except (tk.TclError, AttributeError):
                        continue
                    if value in table:
                        try:
                            widget.configure(**{option: table[value]})
                        except tk.TclError:
                            pass
        self.configure(bg=BG)
        self.kin_title.configure(fg=KIN)

    def open_settings(self) -> None:
        self.toggle_drop(self.drop_open or "Schedule")

    def toggle_drop(self, name: str) -> None:
        """Show the Settings page at one part of it."""
        self.show_page("settings")
        if self.drop_open != name:
            if self.drop_open:
                self.drop_panels[self.drop_open].grid_remove()
                self._mark_drop(self.drop_open, False)
            if name == "World":
                self.refresh_world()
            if name == "Mochi" and cast.has_chatter():
                # Show what is saved now (it may have been changed in setup).
                saved = update_scene.mochi_ai_id()
                if saved and self.mochi_id.get().strip() != saved:
                    self.mochi_id.delete(0, "end")
                    self.mochi_id.insert(0, saved)
            self.drop_panels[name].grid()
            self._mark_drop(name, True)
            self.drop_open = name
        self.update_idletasks()
        self.settings_canvas.yview_moveto(0)
        self._settings_scrolled()
        self._fit_page()

    def _mark_drop(self, name: str, open_: bool) -> None:
        buttons = [self.drop_buttons[name]]
        if name == "Pictures":
            buttons.append(self.idle_background)
        for button in buttons:
            if open_:
                button.configure(bg=ACCENT, fg=ACCENT_TEXT, activebackground=ACCENT_ACTIVE, activeforeground=ACCENT_TEXT)
            else:
                button.configure(bg=PANEL, fg=TEXT, activebackground=PANEL_ACTIVE, activeforeground=TEXT)

    def set_setting(self, scene: str) -> None:
        self.prior.configure(state="normal")
        self.prior.delete("1.0", "end")
        self.prior.insert("1.0", cast.localize(scene))  # the names from setup, never the example names
        self.refresh_count()

    def read_options(self) -> dict:
        minutes = simulation.parse_every(self.minutes.get())
        times = simulation.parse_times(self.times.get())
        if minutes < 1 and not times:
            raise RuntimeError("Set Every, or add a time under At.")
        saved = update_scene.load_prefs()
        quiet_from = update_scene.parse_clock(str(saved.get("quiet_from") or "10:00 PM"))
        quiet_to = update_scene.parse_clock(str(saved.get("quiet_to") or "8:00 AM"))
        wake = update_scene.parse_clock(self.wake.get())
        sleep_text = self.sleep_at.get().strip()
        sleep_at = update_scene.parse_clock(sleep_text) if sleep_text else None
        if sleep_at is not None and sleep_at == wake:
            raise RuntimeError("Sleep at and Wake at need to be different times.")
        return {
            "sleep_at": sleep_at,
            "sleep_text": sleep_text,
            "minutes": minutes,
            "times": times,
            "every_text": self.minutes.get().strip(),
            "times_text": self.times.get().strip(),
            "tone": str(saved.get("tone") or "robotic tone"),
            "quiet_from": quiet_from,
            "quiet_to": quiet_to,
            "wake": wake,
            "quiet_from_text": str(saved.get("quiet_from") or "10:00 PM"),
            "quiet_to_text": str(saved.get("quiet_to") or "8:00 AM"),
            "wake_text": self.wake.get().strip(),
            "ai_minutes": bool(self.ai_minutes.get()),
        }

    def store_prefs(self, options: dict) -> None:
        prefs = update_scene.load_prefs()
        prefs.update(
            {
                "minutes": options["every_text"] or str(options["minutes"]),
                "times": options["times_text"],
                "ai_minutes": "1" if options.get("ai_minutes") else "0",
                "heads_up": "1" if self.heads_up.get() else "0",
                "wand": "1" if self.wand_on.get() else "0",
                "wand_minutes": self.wand_minutes.get().strip(),
                "tone": options["tone"],
                "quiet_from": options["quiet_from_text"],
                "quiet_to": options["quiet_to_text"],
                "wake": options["wake_text"],
                "sleep_at": options.get("sleep_text", ""),
                "opacity": str(self.opacity_value),
                "slide_seconds": str(self.slide_seconds),
                "last_greet_date": self.last_greet_date,
                "environment": self.environment.get("1.0", "end").strip(),
                "backstory": self.backstory.get("1.0", "end").strip(),
                "asleep": "1" if self.phase == "asleep" else "0",
            }
        )
        update_scene.save_prefs(prefs)

    def _on_configure(self, event) -> None:
        if event.widget is not self:
            return
        self._wrap_text(event.width)
        self._rescale(self)
        if not self._glass:
            return
        self._place_plate()
        self._queue_glass()

    def _wrap_text(self, width: int) -> None:
        """Long messages wrap onto more lines instead of running off the edges."""
        room = max(200, width - 48)
        if room == getattr(self, "_wrap_room", None):
            return
        self._wrap_room = room
        self.countdown.configure(wraplength=room)
        self.status.configure(wraplength=room)
        for value in self.world_rows.values():
            value.configure(wraplength=max(150, room - 90))

    def choose_background(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Add pictures",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif"), ("All files", "*.*")],
        )
        if not paths:
            return
        try:
            added = add_slides(paths)
        except Exception as caught:
            self.log(f"Could not use those pictures. {caught}")
            return
        files = slide_files()
        self.slide_index = max(0, len(files) - added)
        self.next_slide = time.monotonic() + self.slide_seconds
        self.clear_bg.grid()
        self._sync_glass()
        word = "picture" if added == 1 else "pictures"
        self.log(f"Added {added} {word}. {len(files)} in the slideshow, changing every {self.slide_seconds} seconds.")

    def clear_background(self) -> None:
        clear_slides()
        self.slide_index = 0
        self._sync_glass()
        self.clear_bg.grid_remove()

    def start(self) -> None:
        if self.phase != "idle":
            return
        try:
            options = self.read_options()
        except RuntimeError as error:
            self._show_compact(False)
            self.log(str(error))
            return
        if not self.prior.get("1.0", "end").strip() and not update_scene.saved_prior():
            # The very first start: the Narrator needs to know where things begin.
            self._show_compact(False)
            self.prior.focus_set()
            self._say("First, type what Lora is doing now in the box at the top (like: reading by the window). Then press Start.")
            return
        self.stopped_on = ""
        self.store_prefs(options)
        self.stop_requested = False
        simulation.set_schedule(True)
        if self.heads_up.get():
            # Heads-up first, then the change a minute later, following whatever she asks for.
            self._show_compact(False)
            self.arm_timer(options, delay=update_scene.HEADS_UP_SECONDS)
            self.start_heads_up = True
            self.log("Started. Narrator gives her the heads-up now, and the change comes in 1 minute.")
            self.note("Heads-up now. The change comes in 1 minute.")
            self.arm_wand()
            return
        morning, self.wake_morning = self.wake_morning, False
        self.shift_setting(options, morning=morning)
        self.arm_wand()

    # ---------------------------------------------------------------- asleep and dreaming

    def _night_of(self, now) -> str:
        """The night a moment belongs to: the night starts at Sleep at and runs to the next Wake at."""
        return (now - timedelta(hours=12)).date().isoformat()

    def _bedtime_now(self, now) -> bool:
        """It is between Sleep at and Wake at, and she has not gone to sleep yet tonight."""
        options = getattr(self, "pending", None)
        if not options or options.get("sleep_at") is None:
            return False
        sleep_at, wake = options["sleep_at"], options["wake"]
        minutes = update_scene.clock_minutes(now)
        inside = sleep_at <= minutes < wake if sleep_at < wake else (minutes >= sleep_at or minutes < wake)
        return inside and getattr(self, "slept_night", "") != self._night_of(now)

    def _fall_asleep_on_time(self) -> None:
        """Sleep at: the scene changes stop and she falls asleep, once the Narrator is not in the middle of anything."""
        if getattr(self, "heads_up_busy", False) or getattr(self, "narrator_peeking", False) or getattr(self, "awaiting_answer", False):
            return
        self.log("It's her bedtime (Sleep at), so the scene changes stop and she falls asleep.")
        self._enter_sleep()

    def toggle_asleep(self) -> None:
        if self.phase == "asleep":
            self.wake_up()
            return
        if self.phase == "busy" or getattr(self, "heads_up_busy", False) or getattr(self, "awaiting_answer", False):
            self.note("The Narrator is busy. Press Sleep again in a moment.")
            return
        self._enter_sleep()

    def _mode(self) -> str:
        """Which mode is on: running, asleep, or stopped."""
        if self.phase == "asleep":
            return "asleep"
        if self.phase in ("waiting", "busy"):
            return "running"
        return "stopped"

    def _show_mode(self) -> None:
        """Light up the button of the mode that is on: Change action, Stop, or Asleep / Wake up."""
        mode = self._mode()
        if getattr(self, "_mode_shown", None) == mode:
            return
        self._mode_shown = mode
        lit = {"running": self.ask_button, "stopped": self.stop_button, "asleep": self.sleep_button}[mode]
        for button in (self.ask_button, self.stop_button, self.sleep_button):
            on = button is lit
            button.configure(
                bg=ACCENT if on else PANEL,
                fg=ACCENT_TEXT if on else TEXT,
                activebackground=ACCENT_ACTIVE if on else PANEL_ACTIVE,
                disabledforeground=ACCENT_TEXT if on else DISABLED,
                font=("Segoe UI", 12, "bold" if on else "normal"),
            )
            if str(self) in self._font_sets:
                self.make_scalable(self, button)  # keep growing with the window

    def _enter_sleep(self, resume: bool = False, next_dream_in: float = DREAM_SECONDS, wake_at=None) -> None:
        """She falls asleep: the timer and Mochi pause, and a dream comes every hour until she wakes up."""
        self.cancel_wand()
        self.phase = "asleep"
        self.awaiting_answer = False
        self.dreaming = False
        now = update_scene.la_now()
        # Once per night: after Wake up at night she stays awake.
        self.slept_night = self._night_of(now)
        try:
            wake = self.read_options()["wake"]
        except RuntimeError:
            wake = 8 * 60
        if wake_at is None:
            wake_at = now.replace(hour=wake // 60, minute=wake % 60, second=0, microsecond=0)
            if wake_at <= now:
                wake_at += timedelta(days=1)
        self.wake_at = wake_at
        self.next_dream = time.monotonic() + next_dream_in
        prefs = update_scene.load_prefs()
        prefs.update(
            asleep="1",
            wake_at=wake_at.isoformat(),
            next_dream_at=(now + timedelta(seconds=next_dream_in)).isoformat(),
        )
        update_scene.save_prefs(prefs)
        self.sleep_button.configure(text="Wake up")
        self.ask_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._show_compact(False)
        if resume:
            self.log("Lora is still asleep. Her dreams carry on.")
            return
        self.log(
            f"Lora is asleep. The Narrator sends her a dream every hour. "
            f"Wakes up at {simulation.clock_text(wake_at)}, or press Wake up."
        )
        environment = self.environment.get("1.0", "end").strip()
        threading.Thread(target=self._fall_asleep_work, args=(environment,), daemon=True).start()

    def _fall_asleep_work(self, environment: str) -> None:
        try:
            result, error = simulation.fall_asleep(environment), ""
        except Exception as caught:
            result, error = {}, str(caught)
        try:
            self.after(0, lambda: self._fall_asleep_done(result, error))
        except (tk.TclError, RuntimeError):
            pass

    def _fall_asleep_done(self, result: dict, error: str) -> None:
        if error:
            self.log(f"Could not tell Kindroid she fell asleep. {error}")
            return
        self.set_setting(result["setting"])
        if result.get("error"):
            self.log(result["error"])
        elif not result.get("pending"):
            self._extras(kindroid_extras.after_sleep)

    def _follow_sleep(self, now) -> None:
        if not self.dreaming and now >= self.wake_at:
            self.log("Good morning. Lora wakes up.")
            self.wake_up(morning=True)
            return
        if not self.dreaming and time.monotonic() >= self.next_dream:
            self.dreaming = True
            environment = self.environment.get("1.0", "end").strip()
            threading.Thread(target=self._dream_work, args=(environment,), daemon=True).start()
        if time.monotonic() >= self.note_until:
            if self.dreaming:
                self.countdown.configure(text=cast.localize("Asleep. The Narrator is sending Lora a dream."))
            else:
                left = max(0, int(self.next_dream - time.monotonic()))
                self.countdown.configure(
                    text=f"Asleep. Next dream in {left // 60}:{left % 60:02d}. Wakes at {simulation.clock_text(self.wake_at)}."
                )

    def dream_now(self) -> None:
        """Skip the wait: the next dream goes out now."""
        if self.phase != "asleep" or self.dreaming:
            return
        self.log("Skipping the wait. The Narrator sends her a dream now.")
        self.next_dream = time.monotonic()
        self._follow_sleep(update_scene.la_now())

    def _show_dream_now(self) -> None:
        """The Dream now button shows while she is asleep, and is greyed out while a dream is being sent."""
        asleep = self.phase == "asleep" and getattr(self, "page", "home") == "home"
        if getattr(self, "_dream_now_shown", False) != asleep:
            self._dream_now_shown = asleep
            if asleep:
                self.dream_now_button.grid()
            else:
                self.dream_now_button.grid_remove()
        self.dream_now_button.configure(state="disabled" if self.dreaming else "normal")

    def _dream_work(self, environment: str) -> None:
        dream = reply = problem = error = ""
        try:
            dream = simulation.write_dream(environment)
            reply, problem = update_scene.narrator_says(update_scene.dream_message(dream))
            simulation.remember_dream(dream)
        except Exception as caught:
            error = str(caught)
        try:
            self.after(0, lambda: self._dream_done(dream, reply, problem, error))
        except (tk.TclError, RuntimeError):
            pass

    def _dream_done(self, dream: str, reply: str, problem: str, error: str) -> None:
        self.dreaming = False
        if self.phase != "asleep":
            return
        if error:
            self.next_dream = time.monotonic() + DREAM_RETRY_SECONDS
            self.log(f"Could not send her a dream. Trying again in 5 minutes. {error}")
        else:
            self.next_dream = time.monotonic() + DREAM_SECONDS
            self.log(f"Narrator sent her a dream: {' '.join(dream.split())[:200]}...")
            self._extras(kindroid_extras.after_dream)
            if reply:
                self.log(f"Lora: {' '.join(reply.split())[:300]}")
            if problem:
                self.log(problem)
        prefs = update_scene.load_prefs()
        prefs["next_dream_at"] = (
            update_scene.la_now() + timedelta(seconds=max(0, self.next_dream - time.monotonic()))
        ).isoformat()
        update_scene.save_prefs(prefs)

    def wake_up(self, morning: bool = False) -> None:
        """She wakes up: the heads-up, then the day carries on like after Change action."""
        self.phase = "idle"
        self.sleep_button.configure(text="Sleep")
        prefs = update_scene.load_prefs()
        prefs["asleep"] = "0"
        update_scene.save_prefs(prefs)
        now = update_scene.la_now()
        if morning:
            self._extras(kindroid_extras.morning)
            self.last_greet_date = now.date().isoformat()
            self._save_greet_date()
        else:
            self.log("Lora wakes up.")
        # The day starts fresh only in the morning. Woken at night, the night carries on.
        self.wake_morning = morning or (update_scene.DAY_FROM <= now.hour * 60 + now.minute < 12 * 60)
        self.start()

    def _resume_schedule(self) -> None:
        """After a restart, pick the timer back up where the simulation left it."""
        if self.phase != "idle":
            return
        prefs = update_scene.load_prefs()
        if str(prefs.get("asleep") or "0") == "1":
            now = update_scene.la_now()
            wake_at = simulation.parse_time(prefs.get("wake_at"))
            next_at = simulation.parse_time(prefs.get("next_dream_at"))
            if wake_at is not None:
                left = (next_at - now).total_seconds() if next_at else DREAM_SECONDS
                self._enter_sleep(resume=True, next_dream_in=max(60, left), wake_at=wake_at)
                return
        plan = simulation.schedule()
        if not plan["running"] or plan["next_at"] is None:
            self.refresh_world()
            return
        try:
            options = self.read_options()
        except RuntimeError as error:
            self.log(str(error))
            return
        wait = (plan["next_at"] - update_scene.la_now()).total_seconds()
        if wait < 60:
            wait = 60
            self.log("The next change was due while the app was closed. It runs in a minute.")
        self._show_compact(False)
        self.arm_timer(options, delay=wait)
        if wait <= update_scene.HEADS_UP_SECONDS:
            # A change that is already due still gets its heads-up first, like pressing Change action.
            self.start_heads_up = True
        self.arm_wand()
        self.log(f"Resumed. Next change at {simulation.clock_text(update_scene.la_now() + timedelta(seconds=wait))}.")

    def _save_greet_date(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["last_greet_date"] = self.last_greet_date
        update_scene.save_prefs(prefs)

    def _arm_after_turn(self, options: dict, minutes: int | None = None, delay: float | None = None) -> None:
        self.arm_timer(options, minutes=minutes, delay=delay)

    def arm_timer(
        self,
        options: dict,
        hold_for_quiet: bool = False,
        delay: float | None = None,
        minutes: int | None = None,
    ) -> None:
        now = update_scene.la_now()
        if delay is None:
            delay = simulation.next_delay(
                now, options["minutes"], options.get("times") or [], bool(options.get("ai_minutes")), minutes
            )
        self.phase = "waiting"
        self.pending = options
        self.deadline = time.monotonic() + delay
        self.narrator_peeked = False
        self.narrator_peeking = False
        self.narrator_request = ""
        self.narrator_speaker = ""
        self.narrator_extend_at = 0
        # Read from the last change or extension, whichever is newer, so old commands are not read again.
        # Read from the last change or extension, whichever is newer. Nothing before it is read again,
        # so the request that caused the last change is never taken twice.
        start = max(simulation.last_step_ms() or int(time.time() * 1000), simulation.last_extend_ms())
        self.narrator_after = start
        self.heads_up_failures = 0
        self.command_stamp = 0
        self.narrator_kind = ""
        self.next_mid_check = time.monotonic() + MID_CHECK_SECONDS
        self.warned = False
        self.start_heads_up = False
        self.heads_up_busy = False
        self.awaiting_answer = False
        self.heads_up_ms = 0
        self.wait_total = delay
        simulation.set_schedule(True, now + timedelta(seconds=delay))
        if time.monotonic() >= self.note_until:
            self.countdown.configure(text=self._next_text(int(delay), now))
        self.ask_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.refresh_world()

    def _next_text(self, remaining: int, now) -> str:
        if remaining >= 3600:
            at = now + timedelta(seconds=remaining)
            return f"Next change at {simulation.clock_text(at)}"
        minutes, seconds = divmod(max(0, remaining), 60)
        return f"Next change in {minutes}:{seconds:02d}"

    def follow_schedule(self, now) -> None:
        options = getattr(self, "pending", None)
        if not options:
            return
        wake_now = update_scene.clock_minutes(now) == options["wake"]
        if wake_now and self.last_greet_date != now.date().isoformat():
            self.last_greet_date = now.date().isoformat()
            self._save_greet_date()
            self.log(f"Morning. Setting what she is doing for {options['wake_text']}.")
            self.shift_setting(options, morning=True)
            return
        if self.wand_gate.locked() and self.deadline - time.monotonic() < update_scene.HEADS_UP_SECONDS + 20:
            # Mochi is still finishing his line: hold the countdown until he is done.
            self.deadline += 1
        remaining = int(self.deadline - time.monotonic())
        self._maybe_mid_check(remaining)
        self._maybe_heads_up(remaining)
        # With the heads-up, the chat is read after her answer to it, 30 seconds before the change.
        peek_at = 30 if self._heads_up_on() else 60
        busy = getattr(self, "heads_up_busy", False)
        if remaining <= peek_at and not self.narrator_peeked and not self.narrator_peeking and not busy:
            self.narrator_peeked = True
            self.narrator_peeking = True
            threading.Thread(target=self._peek_narrator, daemon=True).start()
        if remaining <= 0:
            if self.narrator_peeking or busy or not self.narrator_peeked:
                return
            if self._waiting_for_answer():
                return
            morning, self.wake_morning = self.wake_morning, False
            self.shift_setting(
                options,
                morning=morning,
                request=self.narrator_request,
                requested_by=self.narrator_speaker,
                checked=self.narrator_peeked,
                extend_at=self.narrator_extend_at,
                kind=getattr(self, "narrator_kind", ""),
            )
            return
        if time.monotonic() >= self.note_until:
            self.countdown.configure(text=self._next_text(remaining, now))

    def _waiting_for_answer(self) -> bool:
        """She ignored the heads-up: hold the change a little, and read the chat for her answer."""
        if not getattr(self, "awaiting_answer", False):
            return False
        if getattr(self, "narrator_kind", ""):
            self.awaiting_answer = False
            return False
        now = time.monotonic()
        if now >= self.answer_until:
            self.awaiting_answer = False
            self.log("Lora didn't answer the heads-up, so the change goes ahead.")
            return False
        if now >= self.next_answer_check:
            self.next_answer_check = now + ANSWER_CHECK_SECONDS
            self.narrator_peeking = True
            threading.Thread(target=self._peek_narrator, args=(False, self.heads_up_ms), daemon=True).start()
        if time.monotonic() >= self.note_until:
            left = int(self.answer_until - now)
            self.countdown.configure(text=cast.localize(f"Waiting for Lora's answer to the heads-up ({left // 60}:{left % 60:02d})."))
        return True

    def _heads_up_on(self) -> bool:
        """The heads-up is sent for this wait: it is switched on and the wait is long enough to fit it."""
        if not self.heads_up.get():
            return False
        return getattr(self, "start_heads_up", False) or getattr(self, "wait_total", 0) >= 2 * update_scene.HEADS_UP_SECONDS

    def _maybe_heads_up(self, remaining: int) -> None:
        """One minute before a change, tell her it is coming and how to stay or pick something else."""
        if getattr(self, "warned", True) or not self._heads_up_on():
            return
        if remaining > update_scene.HEADS_UP_SECONDS or remaining <= 15:
            return
        self.warned = True
        self.heads_up_busy = True
        # Her answers are read from a little before the heads-up, so a long chat cannot hide them.
        self.heads_up_ms = int(time.time() * 1000) - 60_000
        environment = self.environment.get("1.0", "end").strip()
        wait = update_scene.duration_text(remaining)
        threading.Thread(target=self._heads_up_work, args=(wait, environment), daemon=True).start()

    def _heads_up_work(self, wait: str, environment: str = "") -> None:
        try:
            here = simulation.here_summary()
        except Exception as caught:
            simulation.log_event(f"could not say where she is in the heads-up: {caught}")
            here = {}
        try:
            # Once an hour during the day: a new place she just noticed, or one she has not been to lately.
            offer = simulation.discovery_offer(environment)
        except Exception as caught:
            simulation.log_event(f"could not offer her a place: {caught}")
            offer = ""
        message = update_scene.heads_up_message(wait, offer=offer, **here)
        try:
            reply, problem = update_scene.narrator_says(message)
            error = ""
        except Exception as caught:
            reply, problem, error = "", "", str(caught)
        try:
            self.after(0, lambda: self._heads_up_done(message, reply, problem, error))
        except tk.TclError:
            pass

    def _heads_up_done(self, message: str, reply: str, problem: str, error: str) -> None:
        self.heads_up_busy = False
        self.narrator_done_at = time.monotonic()
        if error:
            self._heads_up_failed(error)
            return
        where = " in the group chat" if update_scene.active_group() else " in Lora's 1-on-1 chat"
        self.log(f"Narrator gave the heads-up{where}: {message.splitlines()[0]}")
        if reply:
            self.log(f"Lora: {reply}")
            # Her answer comes straight back, so a command in it counts without reading the chat.
            answer = {"sender": "ai", "display_name": "", "message": reply, "timestamp": int(time.time() * 1000)}
            self._take_command(update_scene.narrator_command_from([answer]))
        if problem:
            self.log(problem)
        if not getattr(self, "narrator_kind", "") and self.phase == "waiting":
            # She ignored it, or answered without a command: give her time, so Master can remind her.
            self.awaiting_answer = True
            self.answer_until = max(self.deadline, time.monotonic()) + ANSWER_GRACE_SECONDS
            self.next_answer_check = self.deadline
            self.log("Lora didn't answer the heads-up yet. The change waits up to 2 more minutes for her answer.")
            self.note("Waiting for Lora's answer to the heads-up.")
            return
        self.note("Narrator told her a change is coming.")

    def _heads_up_failed(self, error: str) -> None:
        """No change without a heads-up: the change waits, and the heads-up tries again in 30 seconds."""
        self.heads_up_failures = getattr(self, "heads_up_failures", 0) + 1
        if self.phase != "waiting":
            return
        if self.heads_up_failures > HEADS_UP_TRIES:
            self.log(f"Could not give the heads-up after {HEADS_UP_TRIES} tries, so the change goes ahead. {error}")
            return
        wait = update_scene.HEADS_UP_SECONDS + 30
        self.warned = False
        self.narrator_peeked = False
        self.deadline = time.monotonic() + wait
        simulation.set_schedule(True, update_scene.la_now() + timedelta(seconds=wait))
        self.log(f"Could not give the heads-up, so the change waits. Trying again in 30 seconds. {error}")
        self.note("The heads-up didn't go through. The change waits, and it tries again in 30 seconds.")

    def _save_auto_restart(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["auto_restart"] = "1" if self.auto_restart.get() else "0"
        update_scene.save_prefs(prefs)

    def _check_for_update(self) -> None:
        """When the code files change, restart into the new version once they stop changing and it is safe."""
        if not self.auto_restart.get() or time.monotonic() < self.next_update_check:
            return
        self.next_update_check = time.monotonic() + UPDATE_CHECK_SECONDS
        stamp = code_stamp()
        if stamp == self.code_seen:
            return
        if stamp != self.code_pending:
            # Still being written, maybe several files: wait until nothing changes for a moment.
            self.code_pending = stamp
            self.code_pending_since = time.monotonic()
            return
        if time.monotonic() - self.code_pending_since < UPDATE_SETTLE_SECONDS:
            return
        problem = code_problem()
        if problem:
            if self.code_bad != stamp:
                self.code_bad = stamp
                self.log(f"The app was updated, but the update has a mistake, so it keeps running the old version. {problem}")
                self.note("Update has a mistake. Still running the old version.")
            return
        if not self._safe_to_restart():
            if not self.update_waiting_noted:
                self.update_waiting_noted = True
                self.log("The app was updated. It restarts as soon as the current change or heads-up is done.")
            return
        self.restart_app()

    def _safe_to_restart(self) -> bool:
        """Not in the middle of anything: no change, heads-up, chat read, or wand tap, and not close to the next change."""
        if self.phase == "busy":
            return False
        if getattr(self, "heads_up_busy", False) or getattr(self, "narrator_peeking", False):
            return False
        if getattr(self, "awaiting_answer", False) or self.wand_gate.locked() or getattr(self, "dreaming", False):
            return False
        if self.phase == "waiting" and self.deadline - time.monotonic() < RESTART_SAFE_SECONDS:
            return False
        return True

    def restart_app(self, setup: bool = False) -> None:
        """Start the new version, then close this one. The schedule is saved, so the new one picks it up."""
        if not setup:
            self.log("The app was updated, so it restarts now.")
        try:
            prefs = update_scene.load_prefs()
            prefs["environment"] = self.environment.get("1.0", "end").strip()
            prefs["backstory"] = self.backstory.get("1.0", "end").strip()
            update_scene.save_prefs(prefs)
        except Exception as caught:
            self.log(f"Could not save the Environment and Backstory before restarting. {caught}")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            args = [sys.executable, str(APP_DIR / "gui.py")] + (["--setup"] if setup else [])
            subprocess.Popen(args, cwd=str(APP_DIR), creationflags=flags, close_fds=True)
        except Exception as caught:
            self.log(f"Could not restart the app. Close it and open it again. {caught}")
            self.code_seen = code_stamp()
            return
        self._close()

    def _save_heads_up(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["heads_up"] = "1" if self.heads_up.get() else "0"
        update_scene.save_prefs(prefs)

    def _maybe_mid_check(self, remaining: int) -> None:
        """Every 10 minutes of a long wait, read the chat. A go, weather, or change command happens right away."""
        if self.narrator_peeking or getattr(self, "heads_up_busy", False):
            return
        if remaining <= 3 * 60 or time.monotonic() < getattr(self, "next_mid_check", float("inf")):
            return
        self.next_mid_check = time.monotonic() + MID_CHECK_SECONDS
        self.narrator_peeking = True
        threading.Thread(target=self._peek_narrator, args=(True,), daemon=True).start()

    def _peek_narrator(self, mid: bool = False, after: int = 0) -> None:
        try:
            messages = update_scene.recent_messages(max(after, self.narrator_after), pages=3)
            command = update_scene.narrator_command_from(messages)
            error = ""
        except Exception as caught:
            command = ("", "", "", 0)
            error = str(caught)
        self.after(0, lambda: self._peek_done(command, error, mid))

    def _peek_done(self, command: tuple[str, str, str, int], error: str, mid: bool = False) -> None:
        self.narrator_peeking = False
        if error:
            self.log(f"Could not read the chat. {error}")
            return
        taken = self._take_command(command)
        if mid and taken and self.narrator_kind == "go" and not simulation.request_is_new(self.narrator_request):
            self.log(f"Already did what she asked, so nothing changes now: {self.narrator_request}")
            self.narrator_request = ""
            self.narrator_kind = ""
            return
        if mid and taken and self.phase == "waiting" and self.narrator_kind in ("go", "weather", "change"):
            options = getattr(self, "pending", None)
            if options:
                self.shift_setting(
                    options,
                    request=self.narrator_request,
                    requested_by=self.narrator_speaker,
                    checked=True,
                    kind=self.narrator_kind,
                )

    def _take_command(self, command: tuple[str, str, str, int]) -> bool:
        """Keep the newest command. One that is older, or an extend that was already used, is ignored."""
        kind, speaker, request, stamp = command
        if not kind or stamp <= getattr(self, "command_stamp", 0):
            return False
        if kind == "extend" and stamp <= simulation.last_extend_ms():
            return False
        self.command_stamp = stamp
        self.narrator_kind = kind
        self.narrator_request = request if kind in ("go", "weather", "change") else ""
        self.narrator_speaker = speaker
        self.narrator_extend_at = stamp if kind == "extend" else 0
        if self.phase != "waiting":
            return True
        who = speaker or "Lora"
        if kind == "go":
            self.log(f"Narrator has a request from {speaker or 'the chat'}: {request}")
            self.note("Narrator has a request.")
        elif kind == "weather":
            self.log(f"{who} asked Narrator to change the weather to {request}.")
            self.note(f"Narrator will change the weather to {request}.")
        elif kind == "change":
            self.log(f"{who} asked Narrator to change something: {request}")
            self.note("Narrator will change something around her.")
        else:
            self.log(f"{who} asked Narrator to extend her time.")
            self.note("Narrator will extend her time.")
        return True

    def shift_setting(
        self,
        options: dict,
        morning: bool = False,
        request: str = "",
        requested_by: str = "",
        checked: bool = False,
        extend_at: int = 0,
        kind: str = "",
    ) -> None:
        self._show_compact(False)
        if self.phase == "busy":
            return
        self.phase = "busy"
        self.ask_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.countdown.configure(text=cast.localize("Changing what Lora is doing."))
        prior = self.prior.get("1.0", "end").strip()
        environment = self.environment.get("1.0", "end").strip()
        backstory = self.backstory.get("1.0", "end").strip()
        self.log("Simulation step started.")
        threading.Thread(
            target=self._shift_work,
            args=(prior, options, environment, backstory, morning, request, requested_by, checked, extend_at, kind),
            daemon=True,
        ).start()

    def _shift_work(
        self,
        prior: str,
        options: dict,
        environment: str,
        backstory: str,
        morning: bool,
        request: str,
        requested_by: str = "",
        checked: bool = False,
        extend_at: int = 0,
        kind: str = "",
    ) -> None:
        schedule = {"every": options["minutes"], "times": options.get("times") or [], "ai": bool(options.get("ai_minutes"))}
        try:
            result = simulation.advance_simulation(
                prior,
                environment,
                backstory,
                morning,
                request,
                requested_by,
                check_chat=not checked,
                schedule=schedule,
                extend_at=extend_at,
                stay_here=kind == "change",
                weather=request if kind == "weather" else "",
            )
            error = ""
        except Exception as caught:
            result = None
            error = str(caught)
        self.after(0, lambda: self._shift_done(result, error, options))

    def _shift_done(self, result: dict | None, error: str, options: dict) -> None:
        self.narrator_done_at = time.monotonic()
        if result and result.get("setting"):
            self.set_setting(result["setting"])
        if error or not result:
            self.log(f"Could not change what she is doing. {error}".strip())
        elif result.get("pending"):
            self.log(f"Saved here, Kindroid update pending: {result.get('error', '')}")
            self.note("Saved here. Kindroid update pending.")
        elif result.get("extended"):
            if result.get("error"):
                self.log(result["error"])
            self.log(f"She stays a little longer: {result['setting']}")
        else:
            if result.get("error"):
                self.log(result["error"])
            self.log(f"Changed what she is doing: {result['setting']}")
            self._extras(kindroid_extras.after_change, result)
        if self.stop_requested:
            self.become_idle("Stopped.")
            return
        self._arm_after_turn(options, (result or {}).get("minutes"), (result or {}).get("delay"))

    def arm_wand(self, announce: bool = True) -> None:
        """Tap the wand on its own timer while the simulation is running."""
        self.cancel_wand()
        if self.phase == "idle" or not self.wand_on.get():
            return
        try:
            minutes = wand.talk_minutes(self.wand_minutes.get())
        except RuntimeError as error:
            self._say(str(error))
            return
        self._save_wand_prefs()
        if announce:
            self._say(f"Mochi will tap the wand every {minutes} minutes.")
        self.wand_id = self.after(int(minutes * 60 * 1000), self._wand_tick)

    def cancel_wand(self) -> None:
        if self.wand_id is not None:
            try:
                self.after_cancel(self.wand_id)
            except tk.TclError:
                pass
            self.wand_id = None

    def _toggle_wand(self) -> None:
        self._save_wand_prefs()
        if self.phase == "idle":
            return
        if self.wand_on.get():
            self.arm_wand()
        else:
            self.cancel_wand()
            self.log("Mochi will stop tapping the wand.")

    def _save_use_group(self) -> None:
        config = update_scene.load_json(update_scene.CONFIG_PATH, {})
        if self.use_group.get() and not str(config.get("group_id") or "").strip():
            self.use_group.set(False)
            self._say("No group chat yet. Pick one in Settings > App > Change setup.")
            return
        prefs = update_scene.load_prefs()
        prefs["use_group"] = "1" if self.use_group.get() else "0"
        update_scene.save_prefs(prefs)
        if self.use_group.get():
            self._say("The Narrator now uses the group chat with Lora and Mochi.")
            if cast.has_chatter() and not update_scene.mochi_ai_id():
                self.log("Add Mochi's AI ID in the Mochi panel so Mochi can talk in the group on their own.")
        else:
            self._say("The Narrator now uses the 1-on-1 chat with Lora.")

    def _extras(self, hook, *args) -> None:
        """Start the Kindroid extras for something that happened. Never stops the story."""
        try:
            hook(*args)
        except Exception as caught:
            simulation.log_event(f"Kindroid extras: {caught}")

    def _save_extra(self, name: str) -> None:
        prefs = update_scene.load_prefs()
        on = self.extra_vars[name].get()
        prefs[name] = "1" if on else "0"
        update_scene.save_prefs(prefs)
        label = cast.localize(kindroid_extras.SWITCHES[name].split(":")[0])
        self.extras_note.configure(text=f"{label}: {'on' if on else 'off'}.")
        if name == "x_background":
            self._extras(kindroid_extras.set_background, on)
            self.extras_note.configure(
                text=cast.localize("Background: " + ("on. It shows Lora's latest selfie (tick Selfies too)." if on else "off.")),
            )
        if name == "x_read_kin" and on:
            self.read_kin_now()

    def _save_selfies_per_day(self) -> None:
        try:
            count = max(0, min(20, int(self.selfies_per_day.get().strip())))
        except ValueError:
            count = kindroid_extras.SELFIES_PER_DAY
        self.selfies_per_day.delete(0, "end")
        self.selfies_per_day.insert(0, str(count))
        prefs = update_scene.load_prefs()
        prefs["x_selfies_per_day"] = str(count)
        update_scene.save_prefs(prefs)

    def read_kin_now(self) -> None:
        """Read the kin's backstory and key memories from Kindroid. The About page shows the backstory."""
        self.read_kin_button.configure(state="disabled")
        self.extras_note.configure(text=cast.localize("Reading Lora from Kindroid..."))

        def done(details, error) -> None:
            try:
                self.after(0, lambda: self._read_kin_done(details, error))
            except (tk.TclError, RuntimeError):
                pass

        try:
            kindroid_extras.refresh_kin(done)
        except Exception as caught:
            self._read_kin_done(None, str(caught))

    def _read_kin_done(self, details, error: str) -> None:
        self.read_kin_button.configure(state="normal")
        if error or not details:
            self.extras_note.configure(text=f"Could not read from Kindroid. {error}"[:300])
            return
        story = str(details.get("backstory") or "").strip()
        if story:
            self.backstory.delete("1.0", "end")
            self.backstory.insert("1.0", story)
            prefs = update_scene.load_prefs()
            prefs["backstory"] = story
            update_scene.save_prefs(prefs)
        memory = "yes" if str(details.get("memory") or "").strip() else "none"
        self.extras_note.configure(
            text=cast.localize(f"Read Lora from Kindroid: backstory {'updated on the About page' if story else 'empty'}, key memories: {memory}."),
        )

    def _save_site(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["kindroid_site"] = wand.SITE_CHOICES.get(self.site_choice.get(), "auto")
        update_scene.save_prefs(prefs)
        self._say(f"Kindroid site: {self.site_choice.get()}.")

    def _save_group_auto(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["group_auto"] = "1" if self.group_auto.get() else "0"
        update_scene.save_prefs(prefs)
        if self.group_auto.get():
            self._say("Group on Auto: the website makes them answer, and the app only reads Lora's answer.")
        else:
            self._say("Group on Manual: the app gives Lora their turn right away, then sometimes Mochi.")

    def _save_mochi_id(self) -> None:
        value = "".join(self.mochi_id.get().split())
        config = update_scene.load_json(update_scene.CONFIG_PATH, {})
        if str(config.get("mochi_ai_id") or "") == value:
            return
        config["mochi_ai_id"] = value
        update_scene.CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        cast.reset_cache()
        self._say("Mochi's AI ID is saved." if value else "Mochi's AI ID is cleared.")

    def _save_wand_prefs(self) -> None:
        prefs = update_scene.load_prefs()
        prefs["wand"] = "1" if self.wand_on.get() else "0"
        prefs["wand_minutes"] = self.wand_minutes.get().strip()
        update_scene.save_prefs(prefs)

    def _wand_tick(self) -> None:
        self.wand_id = None
        if self.phase == "idle" or not self.wand_on.get():
            return
        why = self._mochi_must_wait()
        if why:
            # Try again soon. Mochi's next turn counts from when he actually talks.
            if not self.mochi_waiting_noted:
                self.mochi_waiting_noted = True
                self.log(f"Mochi waits: {why}.")
            self.wand_id = self.after(MOCHI_RETRY_SECONDS * 1000, self._wand_tick)
            return
        self.mochi_waiting_noted = False
        threading.Thread(target=self._wand_work, daemon=True).start()
        self.arm_wand(announce=False)

    def _mochi_must_wait(self) -> str:
        """Why Mochi should not talk right now, or "" when he can."""
        if self.phase == "asleep":
            return "Lora is asleep"
        if update_scene.day_part(update_scene.la_now()) == "bedtime":
            return "it is bedtime, Mochi is asleep"
        if self.phase == "busy" or getattr(self, "heads_up_busy", False):
            return "the Narrator is talking"
        if getattr(self, "awaiting_answer", False):
            return "Lora is answering the Narrator"
        if self.phase == "waiting" and self.deadline - time.monotonic() < MOCHI_QUIET_BEFORE_CHANGE:
            return "a change is coming soon"
        if time.monotonic() - getattr(self, "narrator_done_at", -1e9) < MOCHI_AFTER_NARRATOR:
            return "the Narrator just spoke, so Lora gets a moment"
        return ""

    def setup_wand(self) -> None:
        """Open Mochi's browser so you can log in and open Lora's chat. Closing it saves the chat."""
        self.wand_setup_button.configure(state="disabled")
        self._say("Mochi's browser is opening. Log in, open Lora's chat, then close that window.")
        threading.Thread(target=self._setup_wand_work, daemon=True).start()

    def _setup_wand_work(self) -> None:
        try:
            url = wand.setup()
            error = ""
        except Exception as caught:
            url, error = "", str(caught)
        try:
            self.after(0, lambda: self._setup_wand_done(url, error))
        except (tk.TclError, RuntimeError):
            pass

    def _setup_wand_done(self, url: str, error: str) -> None:
        self.wand_setup_button.configure(state="normal")
        if error:
            self._say(f"Mochi's browser is not set up. {error}")
            return
        self._say("Mochi's browser is set up. Mochi will use Lora's chat from now on.")

    def _wand_work(self) -> None:
        if not self.wand_gate.acquire(blocking=False):
            return
        try:
            if update_scene.active_group():
                # In the group Mochi is their own kin: they just talk, no wand and no browser.
                text = update_scene.mochi_talks()
            elif not update_scene.persona_lock.acquire(blocking=False):
                # The Narrator is switching "Chatting as" right now. Mochi skips this turn.
                raise RuntimeError("The Narrator is talking right now. Mochi tries again on the next turn.")
            else:
                try:
                    config = update_scene.load_json(update_scene.CONFIG_PATH, {})
                    mochi = str((config.get("mochi_profile") or {}).get("user_name") or "Mochi")
                    master = str((config.get("master_profile") or {}).get("user_name") or "Master")
                    site = str(update_scene.load_prefs().get("kindroid_site") or "auto")
                    react_to = kindroid_extras._names()[0] if kindroid_extras.enabled("x_react") else ""
                    text = wand.tap(mochi, master, site=site, react_to=react_to)
                finally:
                    update_scene.persona_lock.release()
                update_scene.remember_wand_line(text)
            error = ""
        except Exception as caught:
            text = ""
            error = str(caught)
        finally:
            self.wand_gate.release()
        try:
            self.after(0, lambda text=text, error=error: self._wand_done(text, error))
        except tk.TclError:
            pass

    def _wand_done(self, text: str, error: str) -> None:
        if not self.winfo_exists():
            return
        in_group = bool(update_scene.active_group())
        if error:
            self.log(f"Mochi could not talk in the group. {error}" if in_group else f"Could not tap the wand. {error}")
            return
        if in_group:
            self.note("Mochi said something in the group.")
            self.log(f"Mochi said: {text}")
            return
        self.note("Mochi sent a message with the wand.")
        self.log(f"Mochi sent a wand message: {text}")

    def stop(self) -> None:
        self.wake_morning = False
        if self.phase == "asleep":
            self.sleep_button.configure(text="Sleep")
            prefs = update_scene.load_prefs()
            prefs["asleep"] = "0"
            update_scene.save_prefs(prefs)
            self.stopped_on = update_scene.la_now().date().isoformat()
            simulation.set_schedule(False)
            self.become_idle("Stopped.")
            return
        self.stop_requested = True
        self.cancel_wand()
        self.queued_call = None
        self.stopped_on = update_scene.la_now().date().isoformat()
        simulation.set_schedule(False)
        if self.phase == "waiting":
            self.become_idle("Stopped.")
            return
        self.countdown.configure(text="Stopping after this change.")

    def become_idle(self, status: str, restore_master: bool = False) -> None:
        self.cancel_wand()
        self.phase = "idle"
        self.pending = None
        self.countdown.configure(text=status)
        self.ask_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        # Stay on the full window after Stop. (The small view with just the picture is only for when the app opens.)
        if restore_master:
            threading.Thread(target=self._restore_master, daemon=True).start()

    def _restore_master(self) -> None:
        try:
            name = update_scene.activate_named_profile("master_profile")
            error = ""
        except Exception as caught:
            name = ""
            error = str(caught)
        self.after(0, lambda: self._restore_master_done(name, error))

    def _restore_master_done(self, name: str, error: str) -> None:
        if error:
            self.log(error)
            return
        self.log(f"Chatting as {name}.")


def main() -> None:
    if "--setup" in sys.argv or cast.needs_setup():
        import setup_wizard

        if not setup_wizard.run() and cast.needs_setup():
            return
    App().mainloop()


if __name__ == "__main__":
    main()
