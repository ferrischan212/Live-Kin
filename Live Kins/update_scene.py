"""Write a new Kindroid current setting that is different from the last one.

DeepSeek invents the next scene. Narrator states the previous action and the new one,
the setting is saved, and the chat returns to Master or Mochi.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import random
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import cast
import narrator_commands
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
BACKUP_PATH = ROOT / "state.backup.json"
SCENE_LIMIT = 160
HISTORY_LIMIT = 8

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
KINDROID_URL = "https://api.kindroid.ai/v1/update-info"
SEND_URL = "https://api.kindroid.ai/v1/send-message"
MESSAGES_URL = "https://api.kindroid.ai/v1/get-chat-messages"
GROUP_MESSAGE_URL = "https://api.kindroid.ai/v1/groupchats-user-message"
GROUP_ANSWER_URL = "https://api.kindroid.ai/v1/groupchats-ai-response"
GROUP_UPDATE_URL = "https://api.kindroid.ai/v1/groupchats-update"
# In the group, after Lora answers the Narrator, Mochi adds a line about half the time.
MOCHI_FOLLOWS_CHANCE = 0.5
# While the group is open on the Kindroid website, the website makes the kins answer by itself.
# So the app first waits a little for Lora's answer, and only asks her itself if nobody answered.
GROUP_WAIT_SECONDS = 45
GROUP_CHECK_SECONDS = 10
# Kindroid allows 600 chat reads a day (get-chat-messages), and up to 100 messages a read.
MESSAGES_PER_READ = 100
CHATTER_REMEMBERED_SECONDS = 10 * 60  # who you chat as is read again after this

IGNORE_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "from",
    "her",
    "in",
    "lora",
    "mochi",
    "nearby",
    "of",
    "on",
    "she",
    "the",
    "to",
    "under",
    "with",
}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def require_keys() -> tuple[str, str, str]:
    deepseek = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    kindroid = os.environ.get("KINDROID_API_KEY", "").strip()
    ai_id = os.environ.get("KINDROID_AI_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("DEEPSEEK_API_KEY", deepseek),
            ("KINDROID_API_KEY", kindroid),
            ("KINDROID_AI_ID", ai_id),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Your keys are missing. Open Settings > App > Change setup and paste them on the first page.")
    return deepseek, kindroid, ai_id


_state_lock = threading.RLock()
# Only one thing switches "Chatting as" at a time: the Narrator, or Mochi's wand in the 1-on-1 chat.
persona_lock = threading.RLock()


def one_persona_at_a_time(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with persona_lock:
            return func(*args, **kwargs)

    return wrapper


def load_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{path.name} could not be read.") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name} could not be read.")
    return data


def _load_state_file() -> tuple[dict, bool]:
    """The saved state, and whether state.json itself was readable."""
    try:
        return load_json(STATE_PATH, {"history": []}), True
    except RuntimeError:
        if BACKUP_PATH.exists():
            return load_json(BACKUP_PATH, {"history": []}), False
        raise


def read_state() -> dict:
    with _state_lock:
        return _load_state_file()[0]


def update_state(mutator, validate=None) -> dict:
    """Change the state and save it. The backup only moves forward after the new state passes validate."""
    with _state_lock:
        state, main_ok = _load_state_file()
        mutator(state)
        if validate is not None:
            validate(state)
        text = json.dumps(state, indent=2) + "\n"
        if main_ok and STATE_PATH.exists():
            spare = BACKUP_PATH.parent / f"{BACKUP_PATH.name}.tmp"
            shutil.copyfile(STATE_PATH, spare)
            os.replace(spare, BACKUP_PATH)
        tmp = STATE_PATH.parent / f"{STATE_PATH.name}.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, STATE_PATH)
        return state


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", normalize(text))
    return {word for word in words if word not in IGNORE_WORDS and word not in cast.name_words()}


def clean_scene(text: str) -> str:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    line = re.sub(r"^(setting\s*:\s*)", "", line, flags=re.IGNORECASE)
    if len(line) >= 2 and line[0] == line[-1] and line[0] in {'"', "'"}:
        line = line[1:-1].strip()
    return re.sub(r"\s+", " ", line).strip()


def kindroid_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def request_body(url: str, payload: dict, headers: dict, timeout: int) -> str:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{url} returned {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach {url}: {error.reason}") from error
    except OSError as error:
        # A slow answer times out here (TimeoutError), not as a URLError.
        raise RuntimeError(f"Could not reach {url}: {error}") from error


def post_json(url: str, payload: dict, headers: dict, timeout: int, required: bool = False) -> dict:
    body = request_body(url, payload, headers, timeout).lstrip("\ufeff").strip()
    if not body:
        if required:
            raise RuntimeError(f"{url} sent back an empty response.")
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        if required:
            raise RuntimeError(f"{url} sent back text instead of data: {body[:180]}")
        return {}
    if not isinstance(parsed, dict):
        if required:
            raise RuntimeError(f"{url} sent back an unexpected response: {body[:180]}")
        return {}
    return parsed


def ask_deepseek(
    api_key: str,
    prompt: str,
    system: str | None = None,
    max_tokens: int = 120,
    temperature: float = 1.1,
    paragraph: bool = False,
    raw: bool = False,
) -> str:
    prompt = cast.localize(prompt)
    if system:
        system = cast.localize(system) + cast.pronoun_note()
    payload = {
        "model": "deepseek-flash",
        "messages": [
            {
                "role": "system",
                "content": system
                or (
                    "You write one Kindroid current-setting line. "
                    "Output only that line."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    result = post_json(
        DEEPSEEK_URL,
        payload,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
        required=True,
    )
    message = result["choices"][0]["message"]
    content = message.get("content") or ""
    if raw:
        return content.strip()
    if paragraph:
        return re.sub(r"\s+", " ", content).strip().strip('"').strip()
    return clean_scene(content)


def parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("DeepSeek did not send JSON.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise ValueError("DeepSeek sent broken JSON.") from error
    if not isinstance(data, dict):
        raise ValueError("DeepSeek sent JSON that is not an object.")
    return data


def ask_deepseek_json(
    api_key: str,
    prompt: str,
    system: str,
    max_tokens: int = 700,
    temperature: float = 0.9,
) -> dict:
    """One DeepSeek call that has to come back as a JSON object."""
    prompt = cast.localize(prompt)
    system = cast.localize(system) + cast.pronoun_note()
    payload = {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        result = post_json(DEEPSEEK_URL, payload, headers, timeout=90, required=True)
    except RuntimeError as error:
        if "response_format" not in str(error):
            raise
        payload.pop("response_format")
        result = post_json(DEEPSEEK_URL, payload, headers, timeout=90, required=True)
    try:
        content = str(result["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError("DeepSeek sent an unexpected response.") from error
    return parse_json_object(content)


# ---------------------------------------------------------------- the group chat


def active_group() -> str:
    """The group chat's ID when "Use the group chat" is on. Empty for the 1-on-1 chat."""
    try:
        if str(load_prefs().get("use_group") or "0") != "1":
            return ""
        return str(load_json(CONFIG_PATH, {}).get("group_id") or "").strip()
    except Exception:
        return ""


def mochi_ai_id() -> str:
    """The side chatter's own AI ID, when they are a kin in the group."""
    try:
        return str(load_json(CONFIG_PATH, {}).get("mochi_ai_id") or "").strip() if cast.has_chatter() else ""
    except Exception:
        return ""


def is_lora(display_name: str) -> bool:
    """A kin's line is the main kin's when it has their name (or no name, in the 1-on-1 chat)."""
    name = str(display_name or "").strip().lower()
    return name in {"", cast.cast()["kin_name"].lower()}


def reply_text(body: str) -> str:
    """A kin's answer from Kindroid, whether it comes back as plain text or as data."""
    text = str(body or "").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        for key in ("reply", "message", "response", "text", "content", "output"):
            if isinstance(data.get(key), str):
                return data[key].strip()
    return text


def group_post(api_key: str, group: str, message: str) -> None:
    """Post a message in the group, as whoever you are chatting as right now (the Narrator, while it talks)."""
    request_body(GROUP_MESSAGE_URL, {"group_id": group, "message": message}, kindroid_headers(api_key), timeout=60)


def group_answer(api_key: str, group: str, ai_id: str) -> str:
    """Ask one kin in the group to answer now. Kins in a group do not answer an app by themselves."""
    body = request_body(
        GROUP_ANSWER_URL, {"group_id": group, "ai_id": ai_id, "stream": False}, kindroid_headers(api_key), timeout=120
    )
    return reply_text(body)


def lora_answer_since(after_ms: int) -> str:
    """The main kin's newest line in the group since a time. Empty when they have not answered."""
    newest = ""
    for item in sorted(recent_messages(after_ms), key=lambda item: int(item.get("timestamp") or 0)):
        if str(item.get("sender") or "") == "ai" and is_lora(str(item.get("display_name") or "")):
            newest = str(item.get("message") or "").strip()
    return newest


def wait_for_lora(after_ms: int, seconds: float) -> str:
    deadline = time.monotonic() + seconds
    while True:
        try:
            text = lora_answer_since(after_ms)
        except Exception:
            text = ""
        if text or time.monotonic() >= deadline:
            return text
        time.sleep(GROUP_CHECK_SECONDS)


def mochi_is_up() -> bool:
    """The side chatter sleeps at bedtime and while the main kin is asleep."""
    try:
        return day_part(la_now()) != "bedtime" and str(load_prefs().get("asleep") or "0") != "1"
    except Exception:
        return False


def mochi_talks() -> str:
    """In the group, the side chatter says something by themselves (no wand, no browser)."""
    load_env(ENV_PATH)
    _, kindroid_key, _ = require_keys()
    group = active_group()
    mochi = mochi_ai_id()
    if not group or not mochi:
        raise RuntimeError(f"{cast.cast()['chatter_name']}'s AI ID is missing. Add it in Settings > App > Change setup.")
    return group_answer(kindroid_key, group, mochi)


def group_on_auto() -> bool:
    """You set the group to Auto on the Kindroid website: the website makes the kins answer by itself."""
    return str(load_prefs().get("group_auto") or "0") == "1"


def save_kindroid(api_key: str, ai_id: str, scene: str) -> None:
    scene = cast.localize(scene)
    group = active_group()
    if group:
        # In the group, the scene is the group's.
        post_json(GROUP_UPDATE_URL, {"group_id": group, "current_scene": scene}, kindroid_headers(api_key), timeout=30)
        return
    post_json(
        KINDROID_URL,
        {"ai_id": ai_id, "current_scene": scene},
        kindroid_headers(api_key),
        timeout=30,
    )


def switch_profile(api_key: str, profile: dict) -> None:
    headers = kindroid_headers(api_key)
    post_json(KINDROID_URL, {"active_persona_id": profile["id"]}, headers, timeout=30)
    payload: dict = {}
    for field in ("user_name", "user_gender"):
        value = str(profile.get(field, "")).strip()
        if value:
            payload[field] = value
    if "user_backstory" in profile:
        payload["user_backstory"] = str(profile.get("user_backstory", ""))
    if profile.get("delete_user_avatar"):
        payload["user_custom_avatar"] = {"delete_user_avatar": True}
    if payload:
        post_json(KINDROID_URL, payload, headers, timeout=30)


@one_persona_at_a_time
def activate_named_profile(key: str) -> str:
    load_env(ENV_PATH)
    _, kindroid_key, _ = require_keys()
    config = load_json(CONFIG_PATH, {})
    profile = config.get(key) or {}
    if not profile.get("id"):
        raise RuntimeError(
            "Master's profile id is not saved yet. Switch to Master in Kindroid, "
            "open the update-info request, and copy active_persona_id, user_name, "
            "user_gender, and user_backstory."
        )
    switch_profile(kindroid_key, profile)
    return str(profile.get("user_name") or "Master")


def switch_back_to_master() -> str:
    try:
        activate_named_profile("master_profile")
    except Exception as caught:
        return str(caught)
    return ""


def restore_profile(profile: dict) -> str:
    name = str(profile.get("user_name") or "Master")
    try:
        if not profile.get("id"):
            return switch_back_to_master()
        load_env(ENV_PATH)
        _, kindroid_key, _ = require_keys()
        switch_profile(kindroid_key, profile)
    except Exception as caught:
        return f"Could not switch back to {name}. {caught}"
    return ""


def recent_messages(after_timestamp: int, pages: int = 3) -> list[dict]:
    """Messages after a time, oldest first. Kindroid sends 20 at a time from the start, so page forward."""
    found: list[dict] = []
    after = after_timestamp
    for _ in range(pages):
        messages, _latest = fetch_messages(after)
        if not messages:
            break
        found.extend(messages)
        newest = max(int(item.get("timestamp") or 0) for item in messages)
        if len(messages) < MESSAGES_PER_READ or newest <= after:
            break
        after = newest
    return found


WAND_LINES_KEPT = 30


def _line_key(text) -> str:
    return " ".join(str(text or "").split()).lower()[:160]


def remember_wand_line(text: str) -> None:
    """Keep what Mochi's wand said, so it never counts as you chatting as Mochi."""
    key = _line_key(text)
    if not key:
        return
    prefs = load_prefs()
    lines = [line for line in prefs.get("wand_lines") or [] if isinstance(line, str)]
    prefs["wand_lines"] = (lines + [key])[-WAND_LINES_KEPT:]
    save_prefs(prefs)


def wand_lines() -> set[str]:
    try:
        return {line for line in load_prefs().get("wand_lines") or [] if isinstance(line, str)}
    except Exception:
        return set()


def chatter_profile(config: dict) -> dict:
    """The profile that last spoke in the chat, Master or Mochi.

    Look at the last 15 minutes first, which is one read while someone is chatting.
    Only if nobody spoke then, look back 3 hours.
    """
    master = config.get("master_profile") or {}
    named = {}
    for key in ("master_profile", "mochi_profile"):
        profile = config.get(key) or {}
        label = str(profile.get("user_name") or "").strip().lower()
        if label and profile.get("id"):
            named[label] = profile
    now_ms = int(datetime.now().timestamp() * 1000)
    # Kindroid allows only 600 chat reads a day: who you chat as is remembered for a few minutes.
    remembered = _CHATTER.get("label", "")
    if remembered in named and time.monotonic() - _CHATTER.get("at", -1e9) < CHATTER_REMEMBERED_SECONDS:
        return named[remembered]
    from_wand = wand_lines()
    try:
        for window in (3 * 60 * 60 * 1000,):
            latest_user: tuple[int, str] | None = None
            for item in recent_messages(now_ms - window):
                if str(item.get("sender") or "") != "user":
                    continue
                if _line_key(item.get("message")) in from_wand:
                    continue  # Mochi's wand talking, not you
                label = str(item.get("display_name") or "").strip().lower()
                stamp = int(item.get("timestamp") or 0)
                if label in named and (latest_user is None or stamp >= latest_user[0]):
                    latest_user = (stamp, label)
            if latest_user:
                _CHATTER.update(label=latest_user[1], at=time.monotonic())
                return named[latest_user[1]]
    except Exception:
        return master
    _CHATTER.update(label=str(master.get("user_name") or "").strip().lower(), at=time.monotonic())
    return master


_CHATTER: dict = {}  # who you chat as, and when that was read


def kin_profile_text() -> str:
    """What Kindroid says about the kin (Settings > Extras > Know Lora), for the story's prompts. Empty when off."""
    try:
        prefs = load_prefs()
        if str(prefs.get("x_read_kin") or "0") != "1":
            return ""
        profile = prefs.get("kin_profile") if isinstance(prefs.get("kin_profile"), dict) else {}
    except Exception:
        return ""
    parts = []
    if str(profile.get("memory") or "").strip():
        parts.append("Their key memories (from Kindroid): " + " ".join(str(profile["memory"]).split())[:700])
    if str(profile.get("directive") or "").strip():
        parts.append("How they act (from Kindroid): " + " ".join(str(profile["directive"]).split())[:300])
    return ("\n" + "\n".join(parts)) if parts else ""


def _narrator_words() -> str:
    """ "narrator", and the narrator's own name from setup."""
    names = {"narrator", cast.cast()["narrator_name"].lower()}
    return "(?:" + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + ")"


_PATTERNS: dict = {}


def _patterns() -> dict:
    """The command patterns, built with the narrator's name."""
    who = _narrator_words()
    if who in _PATTERNS:
        return _PATTERNS[who]
    ask = rf"\b{who}\b[\s,.:!-]*(?:please\s+|can you\s+|could you\s+|would you\s+)*"
    built = {
        "extend": re.compile(rf"\b{who}\b[\s,.:!-]*(?:please\s+)?extend\s+(?:my\s+|the\s+)?time", re.IGNORECASE),
        "weather": re.compile(ask + r"change\s+(?:the\s+)?weather\s+(?:to\s+|into\s+)?([^.!?\n\]\*\[]+)", re.IGNORECASE),
        # "narrator take me home", "narrator, I want to go to the kitchen": she wants to go somewhere.
        "go": re.compile(
            ask
            + r"((?:take|walk|move|carry|send|lead)\s+(?:me|us)\b"
            r"|bring\s+(?:me|us)\s+(?:to|home|back|inside|outside|over)\b"
            r"|(?:let'?s|let\s+us|i\s+want\s+to|i'?d\s+like\s+to|i\s+wanna|we\s+want\s+to|can\s+we|could\s+we)\s+(?:go|be|head|move)\b"
            r"|(?:go|head)\s+(?:to|back|home|inside|outside)\b)"
            r"([\s\S]*)",
            re.IGNORECASE,
        ),
        "hey": re.compile(rf"hey[\s,]+@?{who}\b[\s,:!-]*([\s\S]*)", re.IGNORECASE),
        # "narrator make it night", "narrator turn the lights down": a change around her, she stays where she is.
        "change": re.compile(
            ask + r"((?:change|make|turn|add|put|set|bring|give|switch|dim|brighten|open|close|light|let)\b[^.!?\n\]\*\[]*)",
            re.IGNORECASE,
        ),
    }
    _PATTERNS[who] = built
    return built


def _is_narrator(display_name: str) -> bool:
    name = display_name.strip().lower()
    return name in {"narrator", cast.cast()["narrator_name"].lower()}


def _default_speaker(sender: str) -> str:
    return cast.cast()["kin_name"] if sender == "ai" else cast.cast()["user_name"]


def request_words(text: str) -> str:
    """Her words without stage directions like [picks up Mochi] or *smiles*, on one line."""
    text = re.sub(r"\[[^\]]*\]|\*[^*]*\*", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n.,\"'")
    return text[:300]


def _command_in(text: str, commands: list[dict], rx: dict) -> tuple[str, str] | None:
    """The command in one message, as (kind, request). None when there is none.

    Your commands from the Narrator panel come first, then looser wording for the kinds you kept:
    "narrator extend my time", "narrator take me home", "narrator make it night".
    """
    kinds = {command["does"] for command in commands}
    for step in ("custom", "stay", "go", "weather", "go-loose", "change"):
        does = step.split("-")[0]
        if does not in kinds:
            continue
        if step != "go-loose":
            for command in narrator_commands.longest_first(commands, does):
                found = narrator_commands.request_in(command, text, request_words)
                if found is not None:
                    return found if found[0] else None
        if step == "stay" and rx["extend"].search(text):
            return "extend", ""
        if step == "go":
            match = rx["hey"].search(text)
            if match and request_words(match.group(1)):
                return "go", request_words(match.group(1))
        if step == "weather":
            weather = rx["weather"].search(text)
            if weather:
                body = narrator_commands.clean_weather(weather.group(1))
                return ("weather", body) if body else None
        if step == "go-loose":
            going = rx["go"].search(text)
            if going and request_words(going.group(1) + going.group(2)):
                return "go", request_words(going.group(1) + going.group(2))
        if step == "change":
            change = rx["change"].search(text)
            if change:
                body = narrator_commands.short_part(change.group(1), 200)
                if len(body.split()) >= 2:
                    return "change", body
    return None


def _not_a_command(item: dict, chatter: str, from_wand: set, echo: str) -> bool:
    """Lines that never count as a command: the side chatter's (Mochi's wand repeats things), and copies of the heads-up."""
    name = str(item.get("display_name") or "").strip().lower()
    text = str(item.get("message") or "")
    if chatter and name == chatter:
        return True
    if _line_key(text) in from_wand:
        return True
    low = " ".join(text.lower().split())
    if "your surroundings will change automatically" in low or "please dont conversate with me" in low:
        return True
    return bool(echo) and echo in low


def _heads_up_echo(commands_settings: dict) -> str:
    """The start of your heads-up, before {where}: a message that has it is a copy of the heads-up."""
    lines = [line for line in str(commands_settings.get("message") or "").splitlines() if line.strip()]
    start = " ".join(lines[0].lower().strip("* ").split("{")[0].split()) if lines else ""
    return start if len(start) >= 12 else ""


def narrator_command_from(messages: list[dict]) -> tuple[str, str, str, int]:
    """The latest narrator command in chat. Narrator's own lines are skipped.

    Kinds: "go" (hey @narrator ..., and "Do what I write" commands), "extend" (Narrator extend time please),
    "weather" (narrator change weather to ...), and "change" (narrator make/turn/add ... around her).
    The commands are the ones in the Narrator panel.
    """
    found = ("", "", "", 0)
    rx = _patterns()
    found_settings = narrator_commands.settings(load_prefs())
    commands = found_settings["commands"]
    try:
        chatter = (cast.cast()["chatter_name"] if cast.has_chatter() else "").strip().lower()
    except Exception:
        chatter = "mochi"
    from_wand = wand_lines()
    echo = _heads_up_echo(found_settings)
    for item in sorted(messages, key=lambda item: int(item.get("timestamp") or 0)):
        sender = str(item.get("sender") or "")
        if _is_narrator(str(item.get("display_name") or "")) or sender not in {"user", "ai"}:
            continue
        text = str(item.get("message") or "")
        who = str(item.get("display_name") or "").strip() or _default_speaker(sender)
        stamp = int(item.get("timestamp") or 0)
        if _not_a_command(item, chatter, from_wand, echo):
            continue
        command = _command_in(text, commands, rx)
        if command:
            found = (command[0], who, command[1], stamp)
    return found


def narrator_request_from(messages: list[dict]) -> tuple[str, str]:
    """Who asked, and what they asked, in the latest hey @narrator."""
    found = ""
    speaker = ""
    ordered = sorted(messages, key=lambda item: int(item.get("timestamp") or 0))
    for item in ordered:
        sender = str(item.get("sender") or "")
        if _is_narrator(str(item.get("display_name") or "")):
            continue
        if sender not in {"user", "ai"}:
            continue
        text = str(item.get("message") or "")
        match = _patterns()["hey"].search(text)
        if not match:
            continue
        body = re.sub(r"\s+", " ", match.group(1)).strip(" \t\r\n.,")
        if body:
            found = body
            speaker = str(item.get("display_name") or "").strip() or _default_speaker(sender)
    return speaker, found


def fetch_messages(
    after_timestamp: int, group_id: str | None = None, kin_id: str = "", api_key: str = ""
) -> tuple[list[dict], int]:
    """20 messages after a time. The main kin's chat (or the group's), or kin_id's own chat.

    api_key is for setup, before the keys are saved.
    """
    if api_key:
        kindroid_key, ai_id = api_key, kin_id
    else:
        load_env(ENV_PATH)
        _, kindroid_key, ai_id = require_keys()
        ai_id = kin_id or ai_id
    params = {"limit": MESSAGES_PER_READ, "start_after_timestamp": after_timestamp}
    group_id = group_id or ("" if kin_id else active_group())
    if group_id:
        params["group_id"] = group_id
    else:
        params["ai_id"] = ai_id
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{MESSAGES_URL}?{query}",
        headers={"Authorization": f"Bearer {kindroid_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Chat check returned {error.code}: {detail}") from error
    except OSError as error:
        raise RuntimeError(f"Could not check the chat: {getattr(error, 'reason', error)}") from error
    data = json.loads(body) if body.strip() else {}
    messages = data.get("messages") or []
    latest = after_timestamp
    for item in messages:
        latest = max(latest, int(item.get("timestamp") or 0))
    return messages, latest


def kin_name(ai_id: str, api_key: str = "") -> str:
    """A kin's name, from the lines it wrote in its own 1-on-1 chat. Empty when it has not written any yet."""
    messages, _latest = fetch_messages(0, kin_id=ai_id, api_key=api_key)
    for item in messages:
        name = str(item.get("display_name") or "").strip()
        if str(item.get("sender") or "") == "ai" and name:
            return name
    return ""


def tell_lora(api_key: str, ai_id: str, message: str, after_post=None) -> str:
    """Send the Narrator's message and return the main kin's answer.

    In the group: post it and wait a little. If the Kindroid website is open it makes the kin answer by itself,
    and the app just reads the answer. If nobody answers, the app asks the kin, and about half the time the
    side chatter adds a line after them.
    """
    message = cast.localize(message)
    group = active_group()
    if not group:
        return request_body(
            SEND_URL,
            {"ai_id": ai_id, "message": message, "stream": False},
            kindroid_headers(api_key),
            timeout=120,
        ).strip()
    posted = int(time.time() * 1000) - 2000
    group_post(api_key, group, message)
    # The message is out now. Nothing below may fail the send, or the app would post the same message again.
    if after_post:
        after_post()  # switch you back from the Narrator right away, not after everyone answered
    if group_on_auto():
        # On Auto the website makes the kins answer by themselves: just read the answer.
        reply = wait_for_lora(posted, GROUP_WAIT_SECONDS)
        if reply:
            return reply
    try:
        reply = group_answer(api_key, group, ai_id) or wait_for_lora(posted, GROUP_CHECK_SECONDS * 2)
    except Exception:
        # Kindroid is busy (often "too many requests" while the website is answering). The answer is read
        # from the chat afterwards, like when the kin answers late.
        return wait_for_lora(posted, GROUP_CHECK_SECONDS * 2)
    chatter = mochi_ai_id()
    if chatter and mochi_is_up() and random.random() < MOCHI_FOLLOWS_CHANCE:
        try:
            group_answer(api_key, group, chatter)
        except Exception:
            pass  # the extra line is only a nice touch
    return reply


DEFAULT_TIMEZONE = "America/Los_Angeles"


def zone() -> ZoneInfo:
    """The time zone from setup."""
    name = str(cast.load_config().get("cast", {}).get("timezone") or DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


LA = zone()


def la_now() -> datetime:
    return datetime.now(zone())


def clock_minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def parse_clock(text: str) -> int:
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?", text.strip().upper())
    if not match:
        raise RuntimeError("Use times like 10:00 PM.")
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    suffix = match.group(3)
    if minute > 59:
        raise RuntimeError("Use times like 10:00 PM.")
    if suffix:
        if not 1 <= hour <= 12:
            raise RuntimeError("Use times like 10:00 PM.")
        hour = hour % 12
        if suffix == "PM":
            hour += 12
    elif not 0 <= hour <= 23:
        raise RuntimeError("Use times like 10:00 PM.")
    return hour * 60 + minute


def format_clock(minutes: int) -> str:
    hour24, minute = divmod(minutes, 60)
    suffix = "AM" if hour24 < 12 else "PM"
    hour = hour24 % 12 or 12
    return f"{hour}:{minute:02d} {suffix}"


def in_quiet_hours(now: datetime, start: int, end: int) -> bool:
    if start == end:
        return False
    current = clock_minutes(now)
    if start < end:
        return start <= current < end
    return current >= start or current < end


def should_autostart(now: datetime, wake: int, quiet_from: int, quiet_to: int, greeted_on: str, stopped_on: str) -> bool:
    today = now.date().isoformat()
    if greeted_on == today or stopped_on == today:
        return False
    minutes = clock_minutes(now)
    if minutes == wake:
        return True
    return minutes > wake and not in_quiet_hours(now, quiet_from, quiet_to)


def is_late_night(now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    return minutes >= 22 * 60 or minutes < 8 * 60


def action_timing(now: datetime, waking: bool = False) -> str:
    """Light in the morning, medium from noon, cozy at night, cozier toward midnight."""
    when = now.strftime("%A %I:%M %p").replace(" 0", " ")
    minutes = now.hour * 60 + now.minute
    if waking or 8 * 60 <= minutes < 12 * 60:
        return (
            f"It is {when}. Morning. "
            "Light actions: easy, bright, just getting going. Daylight."
        )
    if 12 * 60 <= minutes < 18 * 60:
        return (
            f"It is {when}. Noon through afternoon. "
            "Medium actions: out and doing something, outside when the weather allows. Not bedtime."
        )
    if minutes >= 18 * 60:
        along = (minutes - 18 * 60) / (6 * 60 - 1)
        if along < 0.34:
            cozy = "Cozy, and she is still up."
        elif along < 0.67:
            cozy = "Cozier than earlier this evening. Settled and quieter."
        else:
            cozy = (
                "The coziest. She is winding down for sleep in the bedroom. "
                "No chores, no repairs, no projects, no new rooms."
            )
    else:
        cozy = (
            "Past midnight and still dark. She is asleep or nearly asleep in the bedroom until 8:00 AM. "
            "No chores, no repairs, no projects."
        )
    return (
        f"It is {when}. Night. {cozy} "
        "Cozy actions. The closer it is to 11:59 PM, the cozier and stiller she is."
    )


def load_prefs() -> dict:
    prefs = read_state().get("prefs")
    return prefs if isinstance(prefs, dict) else {}


def save_prefs(prefs: dict) -> None:
    def mutate(state: dict) -> None:
        state["prefs"] = prefs

    update_state(mutate)


def append_history(state: dict, scene: str) -> None:
    history = [item for item in state.get("history", []) if isinstance(item, str) and item.strip()]
    history.append(scene)
    state["history"] = history[-HISTORY_LIMIT:]
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")


def remember(scene: str) -> None:
    update_state(lambda state: append_history(state, scene))


STAPLES = {"furs", "candle", "candles", "moon", "moons", "moonlight", "fireplace", "hearth", "ember", "embers"}


def era() -> str:
    """The world's time and style from setup, for every prompt that writes her world."""
    return cast.era_text()


COMPANION = {"dog", "doggy", "bichon", "mochi"}
RESTING = {"lie", "lies", "lying", "stretch", "stretches", "sit", "sits", "sitting", "lean", "leans", "rest", "rests", "flop", "flops", "curled", "asleep"}


def same_picture(previous: str, nxt: str) -> bool:
    """True when the new line is the same moment with different wording."""
    old = content_words(previous)
    new = content_words(nxt)
    if len(new) < 3:
        return False
    pet = COMPANION | cast.companion_words()
    shared = (old - pet) & (new - pet)
    return len(shared) >= 4 and len(shared) / len(new) >= 0.45


def same_kit(previous: str, nxt: str) -> bool:
    """True when both lines lean on the same bow and lights."""
    shared = (content_words(previous) & STAPLES) & (content_words(nxt) & STAPLES)
    return len(shared) >= 3


def is_resting(text: str) -> bool:
    return bool(content_words(text) & RESTING)


def has_mochi(text: str) -> bool:
    """The side chatter is in the scene. Always true when there is no side chatter."""
    if not cast.has_chatter():
        return True
    words = set(re.findall(r"[a-z0-9]+", normalize(cast.localize(text))))
    return bool(words & cast.companion_words())


def variety_problem(previous: str, nxt: str, recent: list[str], late_night: bool = False) -> str:
    if not has_mochi(nxt):
        return "Mochi is not with her"
    if late_night and content_words(nxt) & {
        "radio", "jazz", "lamp", "panel", "stair", "stairwell", "rewire", "rewires", "rewiring",
        "closet", "ribbon", "repair", "repairs", "tune", "tunes", "tuning",
    }:
        return "it is a chore, not a wind-down"
    pool = [previous, *recent[-4:]]
    if any(same_picture(item, nxt) or same_kit(item, nxt) for item in pool if item.strip()):
        return "it repeats the same objects from a recent setting"
    if not late_night and is_resting(nxt) and any(is_resting(item) for item in pool[:2] if item.strip()):
        return "it is another resting pose"
    return ""


def transition_fits(api_key: str, previous: str, nxt: str, timing: str, elapsed: str = "", bridge: str = "") -> bool:
    """Yes only if she would actually move from this moment into the next one.

    A gap of hours is a new part of the day, not an instant jump from the old line.
    """
    if "hours passed" in elapsed or "days passed" in elapsed:
        return True
    gap = f"{elapsed}\n" if elapsed else ""
    between = f"\nWhat happens in between:\n{bridge}\n" if bridge else ""
    answer = ask_deepseek(
        api_key,
        f"""She is in this moment:
{previous}
{between}
The next moment would be:
{nxt}

{timing}
{gap}
The first moment may name an older time of day, like afternoon, when the clock above now says evening or night. Moving toward what the clock asks now, like heading inside, walking home, or settling somewhere cozier, is natural and counts as yes. A short walk to a nearby place counts as yes. If what happens in between gives a sensible reason and a way to get there, that counts as yes.
Answer no only if she would have to teleport, or do something that makes no sense after the first moment.
Would she actually move from the first moment into the second? Answer yes or no.
""",
        system="You answer yes or no. No other words.",
        max_tokens=5,
        temperature=0,
        paragraph=True,
    )
    return answer.strip().lower().startswith("yes")


def change_rules_for(late_night: bool, wish: str) -> str:
    if late_night and not wish:
        return """- Stay in the bedroom and the spots in it.
- A quieter version of settling down. Lying, resting, or sleeping is fine.
- No chores, no repairs, no projects, and no rooms that are not listed above.""" + (f"\n- {era()}" if era() else "")
    return """- A different place from the current setting and from the recent settings.
- A different kind of action. Do not swap lie, sit, stretch, lean, or rest for each other.
- Leave out most of the other objects from the recent settings. Do not put the furs, the candles, the moonlight, and the fireplace in the same line again.
- If the recent settings stayed in the bedroom, go somewhere outside, as long as the time of day and the weather allow it.
- Do not add people, objects, or places that are not in where she lives.""" + (f"\n- {era()}" if era() else "")


class PublishError(RuntimeError):
    """Kindroid did not take the whole change. parts says what did reach it."""

    def __init__(self, message: str, parts: dict):
        super().__init__(message)
        self.parts = dict(parts)


HEADS_UP_SECONDS = 60


EXTEND_SUGGEST_AFTER = 3
EXTEND_LIMIT = 8
EVENING_FROM = 19 * 60  # 7 PM: a move after the limit stays inside, at a cozy spot
BEDTIME_FROM = 21 * 60  # 9 PM: no nudges, no moving her on, extends do not count
DAY_FROM = 8 * 60  # 8 AM: the day starts and the extend count starts fresh


def day_part(now: datetime) -> str:
    """"day", "evening" (7 to 9 PM) or "bedtime" (9 PM to 8 AM), for the extend nudges."""
    minutes = clock_minutes(now)
    if minutes >= BEDTIME_FROM or minutes < DAY_FROM:
        return "bedtime"
    if minutes >= EVENING_FROM:
        return "evening"
    return "day"

# Small, gentle reasons for the warmer nudges. They never name a place: where she goes is her choice.
NUDGE_REASONS = (
    "Mochi has started nudging your hand and looking around, like he's ready for something new.",
    "Mochi is getting a little restless, sniffing around and wagging up at you.",
    "Mochi stretched, yawned, and is looking up at you hopefully.",
    "the light around you has started to shift a little.",
)


def nudge_text(extends: int, how_long: str = "", part: str = "day") -> str:
    """The more she extends in a row, the warmer and more direct the nudge. Always polite, never names a place.

    No nudge at bedtime: staying put at night is normal.
    """
    if part == "bedtime" or extends < EXTEND_SUGGEST_AFTER:
        return ""
    if extends == EXTEND_SUGGEST_AFTER:
        return (
            "you've stayed here a while now. if you'd like a change of scenery, tell me with "
            '"hey @narrator" and where you want to go, or ask me to change something around you.\n'
        )
    if extends <= 5:
        reason = NUDGE_REASONS[extends % len(NUDGE_REASONS)]
        return (
            f"{reason} maybe it's a good time for a change? wherever you'd like to go, just tell me. "
            "of course, you're welcome to stay.\n"
        )
    if extends < EXTEND_LIMIT:
        text = (
            f"you've been here {'about ' + how_long if how_long else 'a long while'} now. "
            "i'd really suggest stretching your legs somewhere new, anywhere you like. "
            'just say "hey @narrator" and where. if you truly want to stay, that\'s okay too.'
        )
        if extends == EXTEND_LIMIT - 1:
            text += " if you stay this time, next time i'll take you somewhere new."
        return text + "\n"
    return (
        f"lora, you've stayed here through {extends} extends, and i think a change would do you and Mochi some good. "
        "this time, when the minute is up, i'll take you somewhere new. "
        'if there\'s somewhere you\'d like to go, tell me with "hey @narrator" and where. '
        + ("if not, i'll pick a cozy spot inside.\n" if part == "evening" else "if not, i'll pick a spot you can walk to.\n")
    )


def heads_up_message(
    wait: str, where: str = "", how_long: str = "", extends: int = 0, part: str = "day", offer: str = "",
    narrator: dict | None = None,
) -> str:
    """The narrator's warning before a change: where she is, how long, and the commands she can use.

    The message and commands come from the Narrator panel (narrator= shows unsaved ones).
    From EXTEND_SUGGEST_AFTER extends in a row the narrator nudges her, more warmly each time. It is
    only a suggestion until EXTEND_LIMIT: then extending is not offered and she is moved at the change,
    to the place she names or, if she names none, a random place she can walk to.
    """
    found = narrator if narrator is not None else narrator_commands.settings(load_prefs())
    here = ""
    if where:
        here = f"you've been {where}"
        if how_long:
            here += f" for about {how_long}"
        if extends:
            here += f" (you've extended {extends} time{'' if extends == 1 else 's'})"
        here += ". "
    middle = nudge_text(extends, how_long, part) + (offer.strip() + "\n" if offer.strip() else "")
    middle = narrator_commands.with_go_phrase(middle, found)
    return cast.pronouns(
        narrator_commands.message_text(found, here, wait, middle, extends, part, EXTEND_SUGGEST_AFTER, EXTEND_LIMIT)
    )


KINDROID_MESSAGE_LIMIT = 4000
DREAM_INTRO = "*" + narrator_commands.DEFAULT_DREAM + "*"  # the default; yours is in the Narrator panel


def dream_intro() -> str:
    """What the narrator says before each dream, from the Narrator panel."""
    text = narrator_commands.settings(load_prefs()).get("dream", "").strip()
    return f"*{text}*" if text else ""


def dream_message(dream: str) -> str:
    """The narrator's dream post: the intro, then the dream in italics, inside Kindroid's 4000 characters."""
    intro = dream_intro()
    room = KINDROID_MESSAGE_LIMIT - len(intro) - 60
    text = dream.replace("*", "").strip()
    if len(text) > room:
        cut = text[:room]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
        text = (cut[: end + 1] if end > room // 2 else cut).rstrip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n", text) if part.strip()]
    body = "\n\n".join(f"*{part}*" for part in paragraphs)
    return f"{intro}\n\n{body}" if intro else body


@one_persona_at_a_time
def narrator_says(message: str) -> tuple[str, str]:
    """Post one line as Narrator, then switch back to whoever was chatting. Returns (her reply, problem)."""
    load_env(ENV_PATH)
    _, kindroid_key, ai_id = require_keys()
    config = load_json(CONFIG_PATH, {})
    narrator = config.get("narrator_profile") or {}
    if not narrator.get("id"):
        raise RuntimeError("The Narrator's profile is missing. Pick it in Settings > App > Change setup.")
    return_to = chatter_profile(config)
    back: dict = {}

    def switch_back() -> None:
        back["problem"] = restore_profile(return_to)

    extra = {"after_post": switch_back} if active_group() else {}
    try:
        switch_profile(kindroid_key, narrator)
        reply = tell_lora(kindroid_key, ai_id, message, **extra)
    except Exception as caught:
        restore_error = back["problem"] if "problem" in back else restore_profile(return_to)
        raise RuntimeError(f"{caught} {restore_error}".strip()) from caught
    return reply, back["problem"] if "problem" in back else restore_profile(return_to)


def duration_text(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60)))
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("" if hours == 1 else "s"))
    if rest:
        parts.append(f"{rest} minute" + ("" if rest == 1 else "s"))
    return " ".join(parts)


BRIDGE_LIMIT = 280


def clean_bridge(text) -> str:
    """The 'then' line: one or two plain sentences, no asterisks, lowercase start like the other lines."""
    if not isinstance(text, str):
        return ""
    line = " ".join(text.replace("*", " ").split()).strip().strip('"').strip()
    for label in ("then,", "then:", "then "):
        if line.lower().startswith(label):
            line = line[len(label):].strip()
    now = cast.cast()
    if line and not line.startswith(("Mochi", "Master", "I ", now["chatter_name"], now["user_name"])):
        line = line[0].lower() + line[1:]
    return line


def narrator_message(before: str, after: str, wait: str = "", bridge: str = "") -> str:
    bridge = clean_bridge(bridge)
    lines = [f"*before, {before}"]
    if bridge:
        lines.append(f"then, {bridge}")
    lines.append(f"now, {after}*")
    if wait:
        lines.append(f"time until next change: {wait}")
    return "\n".join(lines)


def extension_message(wait: str = "", now: str = "", bridge: str = "") -> str:
    """She stays. With a new moment: what happened in between, and what she is doing now."""
    if now:
        middle = f"then, {bridge}\n" if bridge else ""
        lines = [f"*lora stays where she is a little longer.\n{middle}now, {now}*"]
    else:
        lines = ["*lora stays where she is a little longer*"]
    if wait:
        lines.append(f"time until next change: {wait}")
    return cast.pronouns("\n".join(lines))


@one_persona_at_a_time
def publish_change(before: str, after: str, parts: dict | None = None, message: str | None = None) -> dict:
    """Save the setting, post before/now as Narrator, then return to whoever was chatting.

    parts records what already reached Kindroid, so a retry only sends what is missing.
    """
    load_env(ENV_PATH)
    _, kindroid_key, ai_id = require_keys()
    config = load_json(CONFIG_PATH, {})
    narrator = config.get("narrator_profile") or {}
    if not narrator.get("id"):
        raise RuntimeError("The Narrator's profile is missing. Pick it in Settings > App > Change setup.")
    done = {"setting": False, "message": False}
    done.update({key: bool(value) for key, value in (parts or {}).items() if key in done})
    message = message or narrator_message(before, after)
    return_to = chatter_profile(config) if not done["message"] else {}
    switched = False
    back: dict = {}

    def switch_back() -> None:
        # In the group: the message is out, so switch you back now, before everyone answers.
        done["message"] = True
        back["problem"] = restore_profile(return_to)

    extra = {"after_post": switch_back} if active_group() else {}
    try:
        if not done["message"]:
            switched = True
            switch_profile(kindroid_key, narrator)
        if not done["setting"]:
            save_kindroid(kindroid_key, ai_id, after)
            done["setting"] = True
        if not done["message"]:
            tell_lora(kindroid_key, ai_id, message, **extra)
            done["message"] = True
    except Exception as caught:
        restore_error = back["problem"] if "problem" in back else (restore_profile(return_to) if switched else "")
        detail = f"{caught} {restore_error}".strip()
        raise PublishError(detail, done) from caught
    if "problem" in back:
        restore_error = back["problem"]
    else:
        restore_error = restore_profile(return_to) if switched else ""
    if restore_error:
        raise PublishError(restore_error, done)
    return done


def write_next_line(
    scene: str,
    environment: str = "",
    backstory: str = "",
    morning: bool = False,
    request: str = "",
    deepseek_key: str = "",
    elapsed: str = "",
) -> str:
    """The plain setting writer. The simulation falls back to it when the planner fails."""
    scene = clean_scene(scene)
    if not scene:
        raise RuntimeError("Type her current setting before asking.")
    if not deepseek_key:
        load_env(ENV_PATH)
        deepseek_key = require_keys()[0]
    config = load_json(CONFIG_PATH, {})
    now = la_now()
    timing = action_timing(now, morning)
    home = environment.strip() or "not described"
    story = backstory.strip() or "not described"
    recent = [item for item in read_state().get("history", []) if isinstance(item, str) and item.strip()]
    recent_lines = "\n".join(f"- {item}" for item in recent[-4:]) or "- none"
    wish = request.strip()
    late_night = is_late_night(now)
    change_rules = change_rules_for(late_night, wish)
    if wish:
        asked = f"Request from the chat: {wish}\nFollow this request. Do not replace it with a random change.\n"
    else:
        asked = ""
    rejection = None
    last = ""
    # A line that passed every Python check and only lost the yes/no move check.
    backup = ""
    for _ in range(5):
        correction = f"\nYour last try was rejected because {rejection}. Try again.\n" if rejection else ""
        prompt = f"""Write the next current setting for {config.get("name", "Lora")}.
Who she is:
{story}

Where she lives:
{home}

Current setting: {scene}
Recent settings, which she must not repeat:
{recent_lines}
{timing}
{asked}{correction}
Rules:
- One line, no quotes, no label, no explanation.
- {SCENE_LIMIT} characters or fewer.
- Short, concrete, comma-separated. Plain words.
- Refer to her as "lora".
- Mochi, her white Bichon, is with her in every setting. Name him Mochi.
{change_rules}
- The clock above decides the light and the activity.
- Before you write the line, ask yourself if she would actually move from the current setting into it. If the answer is no, write a different line.
- This is a setting line, not a message to her.
"""
        last = ask_deepseek(deepseek_key, prompt)
        if not last:
            rejection = "it was empty"
            continue
        if len(last) > SCENE_LIMIT:
            rejection = f"it was {len(last)} characters"
            continue
        if normalize(last) == normalize(scene) and not wish:
            rejection = "it repeats the current setting"
            continue
        if wish:
            if not has_mochi(last):
                rejection = "Mochi is not with her"
                continue
            break
        rejection = variety_problem(scene, last, recent, late_night)
        if rejection:
            continue
        if not transition_fits(deepseek_key, scene, last, timing, elapsed):
            rejection = "the move does not follow from where she is"
            backup = backup or last
            continue
        break
    else:
        if backup:
            return backup
        raise RuntimeError(f"DeepSeek did not produce a new activity. Last try: {last}")
    return last


def saved_prior() -> str:
    load_env(ENV_PATH)
    state = read_state()
    history = [scene for scene in state.get("history", []) if isinstance(scene, str) and scene.strip()]
    if history:
        return history[-1]
    config = load_json(CONFIG_PATH, {})
    return clean_scene(config.get("seed_scene", ""))


def run_update(dry_run: bool = False, prior_override: str | None = None) -> list[str]:
    lines: list[str] = []

    def say(text: str) -> None:
        lines.append(text)

    load_env(ENV_PATH)
    deepseek_key, kindroid_key, ai_id = require_keys()
    config = load_json(CONFIG_PATH, {})
    state = read_state()
    history = [scene for scene in state.get("history", []) if isinstance(scene, str) and scene.strip()]

    if prior_override and prior_override.strip():
        prior = clean_scene(prior_override)
    elif history:
        prior = history[-1]
    else:
        prior = clean_scene(config.get("seed_scene", ""))
        say("No saved setting yet. Using the example in config.json as the prior line.")
    if not prior:
        raise RuntimeError("No prior setting. Type her current setting, or add seed_scene to config.json.")

    import simulation

    result = simulation.advance_simulation(prior, dry_run=dry_run)
    say(f"Prior: {result['before']}")
    if result.get("bridge"):
        say(f"Then:  {result['bridge']}")
    say(f"New:   {result['setting']}")
    if result.get("location"):
        say(f"Where: {result['location']}")
    if result.get("goal"):
        say(f"Goal:  {result['goal']}")
    if result.get("event"):
        say(f"Event: {result['event']}")
    if result.get("minutes"):
        say(f"Lasts: about {result['minutes']} min")
    if dry_run:
        say("Preview only. Nothing was saved, and Kindroid was not changed.")
    elif result.get("pending"):
        say(f"Saved here. Kindroid update pending. {result.get('error', '')}".strip())
    elif result.get("error"):
        say(result["error"])
    else:
        say("Narrator sent the change and switched back.")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Update Lora's Kindroid setting via DeepSeek.")
    parser.add_argument("--dry-run", action="store_true", help="Ask DeepSeek, but do not save to Kindroid.")
    parser.add_argument("--prior", help="Use this line as the previous setting for this run.")
    args = parser.parse_args()
    for line in run_update(dry_run=args.dry_run, prior_override=args.prior):
        print(line)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(error)
        sys.exit(1)
