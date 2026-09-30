"""First-launch setup: keys, finding your kins and profiles, who is who, the group chat, and the world.

It opens by itself when the app has not been set up, and again from Settings > App > Change setup.
Everything is saved to .env (keys), config.json (profiles and names), and state.json (world and backstory).
"""

from __future__ import annotations

import json
import os
import re
import threading
import webbrowser
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import cast
import kindroid_finder
import simulation
import theme
import update_scene as us
import wand

# The colors: the theme from Settings > Theme (use_theme sets them before the window is made).
BG = PANEL = TEXT = MUTED = ACCENT = ACCENT_TEXT = ACCENT_ACTIVE = ACCENT_INK = ENTRY = WARN = ""
WATERMARK = "mez.ink/ferisooo"
WATERMARK_URL = "https://mez.ink/ferisooo"


def use_theme() -> None:
    global BG, PANEL, TEXT, MUTED, ACCENT, ACCENT_TEXT, ACCENT_ACTIVE, ACCENT_INK, ENTRY, WARN
    try:
        colors = theme.load(us.load_prefs())[1]
    except Exception:
        colors = theme.PRESETS[theme.DEFAULT]
    palette = theme.full(colors)
    BG, PANEL, TEXT, MUTED = palette["bg"], palette["panel"], palette["text"], palette["muted"]
    ACCENT, ACCENT_TEXT, ACCENT_ACTIVE = palette["accent"], palette["accent_text"], palette["accent_active"]
    ENTRY, WARN, ACCENT_INK = palette["entry"], palette["warn"], palette["accent_ink"]


use_theme()
FONT = ("Segoe UI", -15)  # 15px (a negative size is pixels)
BOLD = ("Segoe UI", -15, "bold")

NOT_PICKED = "(pick one, or type below)"
TIMEZONES = [
    "America/Los_Angeles", "America/Phoenix", "America/Denver", "America/Chicago", "America/New_York",
    "America/Anchorage", "Pacific/Honolulu", "America/Toronto", "America/Mexico_City", "America/Sao_Paulo",
    "Europe/London", "Europe/Dublin", "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Rome",
    "Europe/Amsterdam", "Europe/Stockholm", "Europe/Warsaw", "Europe/Athens", "Europe/Moscow", "Africa/Johannesburg",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Manila", "Asia/Singapore", "Asia/Shanghai", "Asia/Tokyo",
    "Asia/Seoul", "Australia/Perth", "Australia/Sydney", "Pacific/Auckland",
]
CUSTOM_ERA = "Something else (type it below)"


def guess_timezone() -> str:
    """The zone from the list whose clock matches this computer's right now."""
    offset = datetime.now().astimezone().utcoffset()
    for name in TIMEZONES:
        try:
            if datetime.now(us.ZoneInfo(name)).utcoffset() == offset:
                return name
        except Exception:
            continue
    return us.DEFAULT_TIMEZONE


def read_env() -> dict:
    found = {}
    if us.ENV_PATH.exists():
        for raw in us.ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            if "=" in raw and not raw.strip().startswith("#"):
                key, value = raw.split("=", 1)
                found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def write_env(values: dict) -> None:
    """Keep other lines in .env, replace the three keys."""
    lines = []
    if us.ENV_PATH.exists():
        for raw in us.ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            key = raw.split("=", 1)[0].strip()
            if key not in values:
                lines.append(raw)
    lines += [f"{key}={value}" for key, value in values.items()]
    us.ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, value in values.items():
        us.os.environ[key] = value


def found_path():
    """What Find saw last time, kept with the browser's login (Wipe removes it)."""
    return wand.PROFILE / "found.json"


def load_found() -> dict:
    empty = {"kins": {}, "profiles": {}, "groups": {}}
    try:
        data = json.loads(found_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    return {key: data[key] if isinstance(data.get(key), dict) else {} for key in empty}


def save_found(found: dict) -> None:
    try:
        found_path().parent.mkdir(parents=True, exist_ok=True)
        found_path().write_text(json.dumps(found, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def short_id(value: str) -> str:
    return value if len(value) <= 10 else value[:8] + "..."


def kin_choices(found: dict) -> dict[str, tuple[str, str]]:
    """What the kin lists show, to (AI ID, name)."""
    choices = {}
    for ai_id, entry in found.get("kins", {}).items():
        name = entry.get("name") or ""
        choices[f"{name or 'A kin'}  (AI ID {short_id(ai_id)})"] = (ai_id, name)
    return choices


def profile_choices(found: dict) -> dict[str, tuple[str, str]]:
    """What the profile lists show, to (profile ID, name)."""
    choices = {}
    for persona_id, name in found.get("profiles", {}).items():
        choices[f"{name or 'A profile'}  (ID {short_id(persona_id)})"] = (persona_id, name)
    return choices


def group_choices(found: dict) -> dict[str, tuple[str, str]]:
    """What the group list shows, to (group ID, the kins in it)."""
    choices = {}
    for group_id, entry in found.get("groups", {}).items():
        if not entry.get("ok"):
            continue
        names = ", ".join(sorted(name for name in entry.get("kins", {}).values() if name)) or "no kin lines yet"
        choices[f"Group with {names}  (ID {short_id(group_id)})"] = (group_id, names)
    return choices


def renamed_texts(narrator: dict, before: dict, after: dict) -> dict:
    """The Narrator panel's saved texts keep the names they were saved with. A name changed in setup changes there too."""
    swaps = [
        (str(before.get(key) or ""), str(after.get(key) or ""))
        for key in ("kin_name", "user_name", "chatter_name", "narrator_name")
    ]
    swaps = [(old, new) for old, new in swaps if old and new and old.lower() != new.lower()]
    if not swaps:
        return narrator

    def swap(text):
        if not isinstance(text, str):
            return text
        for old, new in swaps:
            text = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", lambda found, new=new: cast._case(new, found.group(0)), text, flags=re.IGNORECASE)
        return text

    out = dict(narrator, message=swap(narrator.get("message")), dream=swap(narrator.get("dream")))
    if isinstance(narrator.get("commands"), list):
        out["commands"] = [
            {key: swap(value) for key, value in command.items()} if isinstance(command, dict) else command
            for command in narrator["commands"]
        ]
    return {key: value for key, value in out.items() if value is not None}


def starting_scene(text: str) -> str:
    """A kin's current setting from Kindroid, as one line that fits the app's box."""
    line = " ".join(str(text or "").split())
    if len(line) > us.SCENE_LIMIT:
        line = line[: us.SCENE_LIMIT].rsplit(" ", 1)[0].rstrip(" ,;")
    return line


def save_setup(answers: dict, found: dict | None = None) -> None:
    """Write everything setup collected. Kept apart from the window so it can be tested."""
    found = found or {}
    before = cast.cast() if isinstance(cast.load_config().get("cast"), dict) else {}
    kin_before = read_env().get("KINDROID_AI_ID", "")
    write_env(
        {
            "DEEPSEEK_API_KEY": answers["deepseek_key"],
            "KINDROID_API_KEY": answers["kindroid_key"],
            "KINDROID_AI_ID": answers["ai_id"],
        }
    )
    config = cast.load_config()

    def profile(prefix: str) -> dict:
        saved = {"id": answers[f"{prefix}_id"], "user_name": answers[f"{prefix}_name"]}
        if answers.get(f"{prefix}_gender"):
            saved["user_gender"] = answers[f"{prefix}_gender"]
        if answers.get(f"{prefix}_backstory", "").strip():
            saved["user_backstory"] = answers[f"{prefix}_backstory"].strip()
        return saved

    config["name"] = answers["kin_name"]
    config["master_profile"] = profile("you")
    config["narrator_profile"] = profile("narrator")
    if answers["has_chatter"]:
        config["mochi_profile"] = profile("chatter")
        config["companion"] = answers["chatter_name"]
        config["mochi_ai_id"] = answers.get("chatter_ai_id", "")
    else:
        for key in ("mochi_profile", "companion", "mochi_ai_id"):
            config.pop(key, None)
    config["group_id"] = answers.get("group_id", "")
    config["cast"] = {
        "kin_name": answers["kin_name"],
        "kin_pronoun": answers["kin_pronoun"],
        "user_name": answers["you_name"],
        "chatter_name": answers["chatter_name"] if answers["has_chatter"] else "",
        "chatter_what": answers["chatter_what"] if answers["has_chatter"] else "",
        "has_chatter": bool(answers["has_chatter"]),
        "narrator_name": answers["narrator_name"],
        "era": answers["era"],
        "timezone": answers["timezone"],
    }
    cast.CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    cast.reset_cache()
    prefs = us.load_prefs()
    prefs["environment"] = answers["environment"].strip()
    prefs["backstory"] = answers["about_kin"].strip()
    prefs["use_group"] = "1" if answers.get("use_group") and answers.get("group_id") else "0"
    prefs["kindroid_site"] = wand.SITE_CHOICES.get(answers.get("kindroid_site", ""), "auto")
    picked = found.get("kins", {}).get(answers["ai_id"]) or {}
    if any(picked.get(detail) for detail in ("backstory", "memory", "directive")):
        prefs["kin_profile"] = {
            "name": answers["kin_name"], "backstory": picked.get("backstory", ""), "memory": picked.get("memory", ""),
            "directive": picked.get("directive", ""), "read_at": datetime.now().isoformat(timespec="seconds"),
        }
    if not answers["has_chatter"]:
        prefs["wand"] = "0"
    prefs["agreed_terms"] = prefs.get("agreed_terms") or datetime.now().date().isoformat()
    # Nothing starts by itself right after setup: you press Change action. (From tomorrow, Wake at starts the day.)
    prefs["last_greet_date"] = us.la_now().date().isoformat()
    if isinstance(prefs.get("narrator"), dict) and before:
        prefs["narrator"] = renamed_texts(prefs["narrator"], before, cast.cast())
    us.save_prefs(prefs)
    # Where the story starts: the kin's current setting in Kindroid. None there: it stays empty.
    # A different kin than before starts a new story (the old kin's world and settings are not theirs).
    scene = starting_scene((found.get("kins", {}).get(answers["ai_id"]) or {}).get("scene", ""))
    new_kin = bool(kin_before) and kin_before != answers["ai_id"]

    def start_here(state: dict) -> None:
        if new_kin:
            state.pop("world", None)
            state["history"] = []
        if scene and not [item for item in state.get("history", []) if isinstance(item, str) and item.strip()]:
            us.append_history(state, scene)

    us.update_state(start_here)
    # Find opened the main kin's chat in the app's browser: the side chatter's wand uses that chat.
    url = (found.get("kins", {}).get(answers["ai_id"]) or {}).get("url", "")
    if url:
        wand.remember_chat(url)


def check_answers(answers: dict) -> str:
    """What is still missing, in plain words. Empty when everything needed is there."""
    if not answers.get("agree"):
        return "Please read and agree to the Terms of Service and the Privacy Policy (Welcome page)."
    needed = [
        ("kindroid_key", "your Kindroid API key (Welcome)"),
        ("deepseek_key", "your DeepSeek API key (Welcome)"),
        ("ai_id", "your kin (page 1)"),
        ("kin_name", "your kin's name (page 1)"),
        ("you_name", "your name (page 2)"),
        ("you_id", "your profile (page 2)"),
        ("narrator_name", "the Narrator's name (page 4)"),
        ("narrator_id", "the Narrator's profile (page 4)"),
    ]
    if answers.get("has_chatter"):
        needed += [("chatter_name", "the side character's name (page 3)"), ("chatter_id", "the side character's profile (page 3)")]
    if answers.get("use_group"):
        needed.append(("group_id", "a group chat (page 5), or untick Use a group chat"))
    missing = [label for key, label in needed if not str(answers.get(key) or "").strip()]
    if missing:
        return "Still needed: " + ", ".join(missing) + "."
    if len(simulation.build_catalog(answers.get("environment", ""))["places"]) < 3:
        return "Add at least 3 places (page 6), or press Suggest places."
    names = [answers["kin_name"], answers["you_name"], answers["narrator_name"]]
    ids = [answers["you_id"], answers["narrator_id"]]
    if answers.get("has_chatter"):
        names.append(answers["chatter_name"])
        ids.append(answers["chatter_id"])
    if len({name.strip().lower() for name in names}) < len(names):
        return "Everyone needs a different name."
    if len(set(ids)) < len(ids):
        return "You, the Narrator, and the side character each need their own profile."
    return ""


class SetupWizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        use_theme()
        self.title("Setup")
        self.configure(bg=BG)
        self.option_add("*TCombobox*Listbox.font", FONT)  # the open list of a pick box
        self.geometry("760x640")
        self.minsize(640, 360)
        self.finished = False
        self.page = 0
        self.finder: kindroid_finder.Finder | None = None
        self.found: dict = load_found()  # what Find saw, also last time
        self.pickers: list[tuple[ttk.Combobox, str, str]] = []  # (the list, what it lists, the ID it fills)
        env = read_env()
        config = cast.load_config()
        now = cast.cast() if isinstance(config.get("cast"), dict) else {}
        prefs = us.load_prefs()

        def from_profile(key: str, field: str) -> str:
            return str((config.get(key) or {}).get(field) or "")

        self.values = {
            "kindroid_key": tk.StringVar(value=env.get("KINDROID_API_KEY", "")),
            "deepseek_key": tk.StringVar(value=env.get("DEEPSEEK_API_KEY", "")),
            "ai_id": tk.StringVar(value=env.get("KINDROID_AI_ID", "")),
            "kin_name": tk.StringVar(value=now.get("kin_name", "") if config.get("cast") else ""),
            "kin_pronoun": tk.StringVar(value=now.get("kin_pronoun", "she")),
            "you_name": tk.StringVar(value=from_profile("master_profile", "user_name")),
            "you_id": tk.StringVar(value=from_profile("master_profile", "id")),
            "you_gender": tk.StringVar(value=from_profile("master_profile", "user_gender")),
            "has_chatter": tk.BooleanVar(value=bool(now.get("has_chatter", False)) if config.get("cast") else False),
            "chatter_name": tk.StringVar(value=from_profile("mochi_profile", "user_name")),
            "chatter_what": tk.StringVar(value=now.get("chatter_what", "") if now.get("has_chatter") else ""),
            "chatter_id": tk.StringVar(value=from_profile("mochi_profile", "id")),
            "chatter_gender": tk.StringVar(value=from_profile("mochi_profile", "user_gender")),
            "chatter_ai_id": tk.StringVar(value=str(config.get("mochi_ai_id") or "")),
            "narrator_name": tk.StringVar(value=from_profile("narrator_profile", "user_name") or "Narrator"),
            "narrator_id": tk.StringVar(value=from_profile("narrator_profile", "id")),
            "group_id": tk.StringVar(value=str(config.get("group_id") or "")),
            "use_group": tk.BooleanVar(value=str(prefs.get("use_group") or "0") == "1"),
            "agree": tk.BooleanVar(value=bool(prefs.get("agreed_terms"))),
            "kindroid_site": tk.StringVar(value=next(
                (label for label, code in wand.SITE_CHOICES.items() if code == str(prefs.get("kindroid_site") or "auto")),
                next(iter(wand.SITE_CHOICES)),
            )),
            "era_choice": tk.StringVar(),
            "era_custom": tk.StringVar(),
            "timezone": tk.StringVar(value=str((config.get("cast") or {}).get("timezone") or guess_timezone())),
        }
        era = str((config.get("cast") or {}).get("era") or "Modern day")
        if era in cast.ERAS:
            self.values["era_choice"].set(era)
        else:
            self.values["era_choice"].set(CUSTOM_ERA)
            self.values["era_custom"].set(era)
        self.texts: dict[str, tk.Text] = {}
        self.text_start = {
            "about_kin": str(prefs.get("backstory") or ""),
            "you_backstory": from_profile("master_profile", "user_backstory"),
            "chatter_backstory": from_profile("mochi_profile", "user_backstory"),
            "narrator_backstory": from_profile("narrator_profile", "user_backstory")
            or "I am the narrator. I continue the story. Do not interact with me. Do not talk to me.",
            "environment": str(prefs.get("environment") or ""),
        }

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=24, pady=(18, 6))
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=24, pady=(0, 16))
        self.message = tk.Label(bar, text="", bg=BG, fg=WARN, font=FONT, wraplength=440, justify="left")
        self.message.pack(side="left", fill="x", expand=True, anchor="w")
        self.next_button = self._button(bar, "Next", self.next, ACCENT)
        self.next_button.pack(side="right")
        self.back_button = self._button(bar, "Back", self.back, PANEL)
        self.back_button.pack(side="right", padx=(0, 8))
        link = tk.Label(self, text=WATERMARK, bg=BG, fg=MUTED, font=("Segoe UI", -13, "underline"), cursor="hand2")
        link.pack(anchor="w", padx=24, pady=(0, 12))
        link.bind("<Button-1>", lambda _event: webbrowser.open(WATERMARK_URL))
        link.bind("<Enter>", lambda _event: link.configure(fg=TEXT))
        link.bind("<Leave>", lambda _event: link.configure(fg=MUTED))

        self.pages = [
            self._page_welcome, self._page_find, self._page_kin, self._page_you, self._page_chatter,
            self._page_narrator, self._page_group, self._page_world,
        ]
        self.frames = []
        for build in self.pages:
            frame = tk.Frame(self.body, bg=BG)
            build(frame)
            self.frames.append(frame)
        self.show(0)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------ small builders

    def _button(self, parent, text, command, color):
        return tk.Button(
            parent, text=text, command=command, bg=color, fg=ACCENT_TEXT if color == ACCENT else TEXT,
            activebackground=ACCENT_ACTIVE if color == ACCENT else color,
            activeforeground=ACCENT_TEXT if color == ACCENT else TEXT, relief="flat", font=FONT, padx=16, pady=6, cursor="hand2",
        )

    def _title(self, parent, text, sub=""):
        tk.Label(parent, text=text, bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        if sub:
            tk.Label(parent, text=sub, bg=BG, fg=MUTED, font=FONT, wraplength=680, justify="left").pack(anchor="w", pady=(4, 12))

    def _hint(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED, font=FONT, wraplength=680, justify="left").pack(anchor="w", padx=(4, 0))

    def _field(self, parent, label, key, hint="", secret=False):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label, bg=BG, fg=TEXT, font=FONT, width=18, anchor="w").pack(side="left")
        entry = tk.Entry(row, textvariable=self.values[key], bg=ENTRY, fg=TEXT, insertbackground=TEXT, relief="flat", font=FONT, show="•" if secret else "")
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        if hint:
            self._hint(parent, hint)
        return entry

    def _box(self, parent, label, key, height=4, hint=""):
        tk.Label(parent, text=label, bg=BG, fg=TEXT, font=FONT).pack(anchor="w", pady=(8, 2))
        box = tk.Text(parent, height=height, wrap="word", bg=ENTRY, fg=TEXT, insertbackground=TEXT, relief="flat", font=FONT, padx=8, pady=6)
        box.pack(fill="both", expand=height > 6)
        box.insert("1.0", self.text_start.get(key, ""))
        self.texts[key] = box
        if hint:
            self._hint(parent, hint)
        return box

    def _choice(self, parent, label, key, options):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label, bg=BG, fg=TEXT, font=FONT, width=18, anchor="w").pack(side="left")
        box = ttk.Combobox(row, textvariable=self.values[key], values=options, state="readonly", font=FONT)
        box.pack(side="left", fill="x", expand=True)
        return box

    def _picker(self, parent, label, kind, id_key, name_key=""):
        """A list of what Find found. Picking one fills in the ID (and the name) below it."""
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(4, 8))
        tk.Label(row, text=label, bg=BG, fg=ACCENT_INK, font=BOLD, width=18, anchor="w").pack(side="left")
        box = ttk.Combobox(row, values=[NOT_PICKED], state="readonly", font=FONT)
        box.set(NOT_PICKED)
        box.pack(side="left", fill="x", expand=True)

        def picked(_event=None) -> None:
            chosen = self._choices(kind).get(box.get())
            if not chosen:
                return
            self.values[id_key].set(chosen[0])
            if name_key and chosen[1] and kind != "group":
                self.values[name_key].set(chosen[1])
            if id_key == "ai_id":
                self._kin_picked(chosen[0])

        box.bind("<<ComboboxSelected>>", picked)
        self.pickers.append((box, kind, id_key))
        return box

    def _kin_picked(self, ai_id: str) -> None:
        """Their backstory from Kindroid goes into About them, when that box is still empty."""
        story = str((self.found.get("kins", {}).get(ai_id) or {}).get("backstory") or "").strip()
        box = self.texts.get("about_kin")
        if story and box is not None and not box.get("1.0", "end").strip():
            box.insert("1.0", story[:1500])

    def _choices(self, kind: str) -> dict:
        return {"kin": kin_choices, "profile": profile_choices, "group": group_choices}[kind](self.found)

    def _refresh_pickers(self) -> None:
        for box, kind, id_key in self.pickers:
            choices = self._choices(kind)
            box.configure(values=[NOT_PICKED, *choices])
            current = self.values[id_key].get()
            match = next((label for label, (found_id, _name) in choices.items() if found_id == current), "")
            box.set(match or NOT_PICKED)

    # ------------------------------------------------------------ pages

    def _page_welcome(self, page):
        self._title(
            page, "Welcome to Live Kins",
            "This app gives your Kindroid character a day that moves on its own. A Narrator tells the story: "
            "where they are, what they do, the weather, bedtime, and dreams.",
        )
        tk.Label(
            page,
            text=(
                "Before you start (about 10 minutes):\n"
                "  1.  A Kindroid account with a kin (your AI character). Make one at kindroid.ai.\n"
                "  2.  Your Kindroid API key: in Kindroid, open Settings > General > API & advanced integrations, copy the key.\n"
                "  3.  A DeepSeek API key: sign up at platform.deepseek.com, open API keys, create one.\n"
                "       Add a little credit there. A few dollars lasts a long time.\n"
                "  4.  A profile named Narrator in Kindroid: in your kin's chat, tap \"Chatting as\" under the\n"
                "       message box and add a new profile called Narrator.\n\n"
                "Paste both keys below. Keep them private, like passwords."
            ),
            bg=BG, fg=TEXT, font=FONT, justify="left", wraplength=680,
        ).pack(anchor="w", pady=(0, 10))
        self._field(page, "Kindroid API key", "kindroid_key", secret=True)
        self._field(page, "DeepSeek API key", "deepseek_key", secret=True)
        tk.Checkbutton(
            page, text="I have read and agree to the Terms of Service and the Privacy Policy",
            variable=self.values["agree"], bg=BG, fg=TEXT, selectcolor=ENTRY, activebackground=BG,
            activeforeground=TEXT, font=FONT,
        ).pack(anchor="w", pady=(14, 2))
        links = tk.Frame(page, bg=BG)
        links.pack(anchor="w", padx=(24, 0))
        for title, file_name in (("Terms of Service", "TERMS.txt"), ("Privacy Policy", "PRIVACY.txt")):
            link = tk.Label(links, text=title, bg=BG, fg=ACCENT_INK, font=("Segoe UI", -15, "underline"), cursor="hand2")
            link.pack(side="left", padx=(0, 16))
            link.bind("<Button-1>", lambda _event, file_name=file_name: self._open_file(file_name))

    def _open_file(self, file_name: str) -> None:
        """Opens TERMS.txt or PRIVACY.txt (in Notepad, or whatever opens text files)."""
        path = us.ROOT / file_name
        try:
            os.startfile(str(path))  # noqa: S606 (Windows only, like the app)
        except (OSError, AttributeError):
            self.message.configure(text=f"Could not open {file_name}. It is in the app's folder.")

    def _page_find(self, page):
        self._title(page, "Find your kins and profiles", "The app looks them up for you.")
        tk.Label(
            page,
            text=(
                "  1.  Press Find. Kindroid opens in a new window.\n"
                "  2.  Log in.\n"
                "  3.  Open your kin's chat. Your profiles show up by themselves.\n"
                "  4.  Using a group chat? Open it and send one message.\n"
                "  5.  Close that window. The list below fills in."
            ),
            bg=BG, fg=TEXT, font=FONT, justify="left", wraplength=680,
        ).pack(anchor="w", pady=(0, 10))
        row = tk.Frame(page, bg=BG)
        row.pack(fill="x", pady=(0, 8))
        self.find_button = self._button(row, "Find", self.start_find, ACCENT)
        self.find_button.pack(side="left")
        self.find_note = tk.Label(row, text="", bg=BG, fg=MUTED, font=FONT, wraplength=520, justify="left")
        self.find_note.pack(side="left", padx=10)
        self.found_box = tk.Text(page, height=12, wrap="word", bg=ENTRY, fg=TEXT, relief="flat", font=FONT, padx=8, pady=6)
        self.found_box.pack(fill="both", expand=True)
        self._show_found()
        self._hint(page, "Something missing? Press Find again. Still missing? You can type it on the next pages.")
        self._choice(page, "Kindroid site", "kindroid_site", list(wand.SITE_CHOICES))
        self._hint(page, "Kindroid has a classic site (v1) and a new one (v2). With a chat open, look at the web address: "
                         "/v2/ in it means v2. Not sure? Leave it: the app checks by itself.")

    def _page_kin(self, page):
        self._title(page, "1. Your kin", "The character this is all about. Their current setting in Kindroid is where the story starts.")
        self._picker(page, "Pick your kin", "kin", "ai_id", "kin_name")
        self._field(page, "AI ID", "ai_id", "Filled in for you.")
        self._field(page, "Name", "kin_name", "What the story calls them.")
        self._choice(page, "Pronouns", "kin_pronoun", list(cast.PRONOUNS))
        self._box(page, "About them (optional)", "about_kin", height=4,
                  hint="Filled in from Kindroid when you pick your kin. It helps the story pick what they do.")

    def _page_you(self, page):
        self._title(page, "2. You", "Your profile: who you are in the chat. The story calls you by this name.")
        self._picker(page, "Pick your profile", "profile", "you_id", "you_name")
        self._field(page, "Your name", "you_name")
        self._field(page, "Profile ID", "you_id", "Filled in for you.")
        self._choice(page, "Gender (optional)", "you_gender", ["", "Male", "Female", "Nonbinary"])
        self._box(page, "Profile description (optional)", "you_backstory", height=2,
                  hint="Leave empty unless you want the app to put it back each time.")

    def _section(self, parent, title, text):
        tk.Label(parent, text=title, bg=BG, fg=TEXT, font=BOLD).pack(anchor="w", pady=(12, 0))
        self._hint(parent, text)

    def _page_chatter(self, page):
        self._title(
            page, "3. Side character (optional)",
            "A pet, friend, or sibling who is always with your kin and says something every few minutes.",
        )
        tk.Checkbutton(
            page, text="Add a side character", variable=self.values["has_chatter"], bg=BG, fg=TEXT, selectcolor=ENTRY,
            activebackground=BG, activeforeground=TEXT, font=FONT,
        ).pack(anchor="w", pady=(0, 6))
        self._field(page, "Name", "chatter_name")
        self._field(page, "What they are", "chatter_what", "Like: white dog, orange cat, little sister.")
        self._section(
            page, "Their profile",
            "Make a profile for them in Kindroid (the same way you made Narrator), then pick it. "
            "The app chats as it for a moment so they can talk.",
        )
        self._picker(page, "Pick their profile", "profile", "chatter_id", "chatter_name")
        self._field(page, "Profile ID", "chatter_id", "Filled in for you.")
        self._choice(page, "Gender (optional)", "chatter_gender", ["", "Male", "Female", "Nonbinary"])
        self._section(page, "Their kin (group chat only)", "Only if they are their own kin in your group chat. Otherwise skip this.")
        self._picker(page, "Pick their kin", "kin", "chatter_ai_id")
        self._field(page, "AI ID", "chatter_ai_id")
        self._box(page, "Profile description (optional)", "chatter_backstory", height=2)

    def _page_narrator(self, page):
        self._title(page, "4. Narrator", "The profile that tells the story. Pick the one you named Narrator.")
        self._picker(page, "Pick the Narrator", "profile", "narrator_id", "narrator_name")
        self._field(page, "Name", "narrator_name", "Your kin talks to it by this name, like: hey @Narrator take me home.")
        self._field(page, "Profile ID", "narrator_id", "Filled in for you.")
        self._box(page, "Profile description", "narrator_backstory", height=3,
                  hint="Tells your kin to leave the Narrator alone. Fine as it is.")

    def _page_group(self, page):
        self._title(
            page, "5. Group chat (optional)",
            "Don't use Kindroid group chats? Skip this page. You can turn it on later in Settings.",
        )
        tk.Checkbutton(
            page, text="Use a group chat", variable=self.values["use_group"], bg=BG, fg=TEXT, selectcolor=ENTRY,
            activebackground=BG, activeforeground=TEXT, font=FONT,
        ).pack(anchor="w", pady=(0, 6))
        self._picker(page, "Pick the group", "group", "group_id")
        self._field(page, "Group ID", "group_id", "Filled in for you. Not in the list? Open the group during Find.")

    def _page_world(self, page):
        self._title(page, "6. Their world", "Where they live. Easiest: press Suggest places.")
        self._choice(page, "Time and style", "era_choice", list(cast.ERAS) + [CUSTOM_ERA])
        self._field(page, "Or type your own", "era_custom", "Like: a small island in the 1950s.")
        self._choice(page, "Your time zone", "timezone", TIMEZONES)
        box = self._box(
            page, "Places", "environment", height=8,
            hint="One place per line, like: Kitchen with a stove, a table, and a teapot. "
            "For outdoor places add \"outside\": Garden outside with roses and a bench.",
        )
        box.configure(font=FONT)
        row = tk.Frame(page, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        self.suggest_button = self._button(row, "Suggest places", self.suggest, ACCENT)
        self.suggest_button.pack(side="left")
        self._button(row, "Use the medieval example", self.medieval_example, PANEL).pack(side="left", padx=8)
        self.suggest_note = tk.Label(row, text="", bg=BG, fg=MUTED, font=FONT)
        self.suggest_note.pack(side="left", padx=8)

    # ------------------------------------------------------------ finding

    def start_find(self) -> None:
        if self.finder is not None and self.finder.found()["running"]:
            return
        key = self.values["kindroid_key"].get().strip()
        self.finder = kindroid_finder.Finder(api_key=key)
        self.find_button.configure(state="disabled", text="Finding...")
        self.find_note.configure(
            text="Kindroid is open in the app's browser. Do the steps above, then close that window."
            + ("" if key else " (Add your Kindroid key on the first page, so kin names and groups can be checked.)"),
            fg=MUTED,
        )
        threading.Thread(target=self.finder.run, daemon=True).start()
        self.after(700, self._poll_find)

    def _poll_find(self) -> None:
        if self.finder is None:
            return
        now = self.finder.found()
        self._merge(now)
        self._show_found()
        if now["running"]:
            self.after(700, self._poll_find)
            return
        self.find_button.configure(state="normal", text="Find again")
        save_found(self.found)
        if now["problem"]:
            self.find_note.configure(text=f"The browser could not open. {now['problem']}"[:300], fg=WARN)
        else:
            self.find_note.configure(text="Done. Press Next and pick who is who.", fg=MUTED)

    def _merge(self, now: dict) -> None:
        """Keep what an earlier Find saw, and add the new."""
        for ai_id, entry in now.get("kins", {}).items():
            kept = self.found["kins"].setdefault(ai_id, {"name": "", "url": ""})
            kept["name"] = entry.get("name") or kept["name"]
            kept["url"] = entry.get("url") or kept["url"]
            for detail in ("scene", "backstory", "memory", "directive"):
                if entry.get(detail):
                    kept[detail] = entry[detail]
        self.found["profiles"].update({key: value for key, value in now.get("profiles", {}).items() if value or key not in self.found["profiles"]})
        self.found["groups"].update(now.get("groups", {}))

    def _show_found(self) -> None:
        lines = []
        kins = kin_choices(self.found)
        profiles = profile_choices(self.found)
        groups = group_choices(self.found)
        lines.append(f"Kins ({len(kins)}):")
        lines += [f"    {label}" for label in kins] or ["    none yet: open a kin's chat"]
        lines.append(f"Profiles ({len(profiles)}):")
        lines += [f"    {label}" for label in profiles] or [
            "    none yet: open any kin's chat (they load with it). Still none? Switch \"Chatting as\" to each one."
        ]
        lines.append(f"Group chats ({len(groups)}):")
        lines += [f"    {label}" for label in groups] or ["    none yet (optional): open your group chat"]
        for group_id, entry in self.found["groups"].items():
            if not entry.get("ok") and entry.get("problem"):
                lines.append(f"    {short_id(group_id)} could not be checked: {entry['problem']}")
        self.found_box.configure(state="normal")
        self.found_box.delete("1.0", "end")
        self.found_box.insert("1.0", "\n".join(lines))
        self.found_box.configure(state="disabled")

    # ------------------------------------------------------------ actions

    def answers(self) -> dict:
        result = {key: var.get() for key, var in self.values.items()}
        for key, box in self.texts.items():
            result[key] = box.get("1.0", "end").strip()
        for key in list(result):
            if isinstance(result[key], str):
                result[key] = result[key].strip()
        choice = result.pop("era_choice")
        custom = result.pop("era_custom")
        result["era"] = custom if choice == CUSTOM_ERA else choice
        if not result["has_chatter"]:
            result["chatter_name"] = result["chatter_what"] = result["chatter_id"] = result["chatter_ai_id"] = ""
        if result["has_chatter"] and not result["chatter_ai_id"] and result["group_id"]:
            # The side chatter's kin, from the group's lines, when it has their name.
            group = self.found["groups"].get(result["group_id"]) or {}
            result["chatter_ai_id"] = next(
                (ai_id for ai_id, name in group.get("kins", {}).items() if name.lower() == result["chatter_name"].lower()), ""
            )
        return result

    def show(self, index: int) -> None:
        for frame in self.frames:
            frame.pack_forget()
        self.page = index
        self._refresh_pickers()
        self.frames[index].pack(fill="both", expand=True)
        self.back_button.configure(state="normal" if index else "disabled")
        self.next_button.configure(text="Finish" if index == len(self.frames) - 1 else "Next")
        self.message.configure(text="")
        self._fit_page()

    def _fit_page(self) -> None:
        """The window is as tall as this page needs, so short pages have no empty space under them."""
        self.update_idletasks()
        width = self.winfo_width() if self.winfo_ismapped() and self.winfo_width() > 1 else 760
        height = min(self.winfo_reqheight(), self.winfo_screenheight() - 80)
        self.geometry(f"{width}x{height}")

    def back(self) -> None:
        if self.page:
            self.show(self.page - 1)

    def next(self) -> None:
        if self.page < len(self.frames) - 1:
            self.show(self.page + 1)
            return
        answers = self.answers()
        problem = check_answers(answers)
        if problem:
            self.message.configure(text=problem)
            return
        try:
            save_setup(answers, self.found)
        except Exception as caught:
            self.message.configure(text=f"Could not save the setup. {caught}")
            return
        self.finished = True
        self._close()

    def _close(self) -> None:
        if self.finder is not None:
            self.finder.stop()
        self.destroy()

    def medieval_example(self) -> None:
        path = us.ROOT / "environment_500ad.txt"
        if not path.exists():
            self.suggest_note.configure(text="The example file is missing.")
            return
        box = self.texts["environment"]
        box.delete("1.0", "end")
        box.insert("1.0", path.read_text(encoding="utf-8").strip())
        self.values["era_choice"].set("Medieval, around 500 AD")
        self.suggest_note.configure(text="Loaded 101 medieval places. Change anything you like.")

    def suggest(self) -> None:
        answers = self.answers()
        if not answers["deepseek_key"]:
            self.suggest_note.configure(text="Add your DeepSeek key on the first page first.")
            return
        self.suggest_button.configure(state="disabled")
        self.suggest_note.configure(text="Asking DeepSeek for places...")
        about = f"{answers['kin_name'] or 'They'}: {answers['about_kin']}"
        if answers["has_chatter"] and answers["chatter_name"]:
            about += f" Always with {answers['chatter_name']}, a {answers['chatter_what'] or 'companion'}."
        era = cast.ERAS.get(answers["era"], answers["era"])

        def work() -> None:
            try:
                lines, error = simulation.suggest_places(about, era, answers["environment"], key=answers["deepseek_key"]), ""
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
            self.suggest_note.configure(text=f"DeepSeek could not suggest places. {error}"[:120])
            return
        box = self.texts["environment"]
        current = box.get("1.0", "end").strip()
        box.delete("1.0", "end")
        box.insert("1.0", (current + "\n" if current else "") + "\n".join(lines))
        box.see("end")
        self.suggest_note.configure(text=f"Added {len(lines)} places. Press again for more.")


def run() -> bool:
    """Show setup. True when it was finished and saved."""
    wizard = SetupWizard()
    wizard.mainloop()
    return wizard.finished
