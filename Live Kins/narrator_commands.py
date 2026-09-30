"""The narrator's heads-up message and the commands she can use. You edit them in the Narrator panel.

The panel has two simple lists:
  Commands: what she says, and what happens then. "What happens" is one of the ready choices
            (Stay longer, Go somewhere / do something, Change the weather, Change something nearby)
            or anything you type, like "Mochi brings Lora a flower".
  Heads-up message: the whole message, one line per idea. {where} and {wait} fill in by themselves.
  Dream message: what the narrator says before each dream.
"""

from __future__ import annotations

import re

try:  # The shareable copy has names from setup. This app is always "narrator".
    import cast as _cast
except ImportError:  # pragma: no cover
    _cast = None

ACTIONS = (
    ("stay", "Stay longer"),
    ("go", "Go somewhere / do something"),
    ("weather", "Change the weather"),
    ("change", "Change something nearby"),
)
ACTION_LABELS = dict(ACTIONS)
KINDS = set(ACTION_LABELS) | {"custom"}

DEFAULT_LINES = (
    "heads up, lora: {where}your surroundings will change automatically in {wait}.",
    'if you want to stay where you are a little longer, say "Narrator extend time please".',
    'if you would like to do something else, say "hey @narrator" and what you want to do or where you want to go.',
    'if you want to change the weather, please say "narrator change weather to per your request"',
    "basically, if you want me to change anything around you or your world. please tell me.",
    "if you dont like where you are at please tell me at the heads up when i ask you and ill move you to your "
    "specified area. I will check back every so often and ask you the last minute. please dont conversate with me.",
)
DEFAULT_DREAM = (
    "lora, you are dreaming right now. please don't talk to the narrator. live inside this dream. use simple words, "
    "don't be poetic. use 1st person. keep it smooth and seamless transition"
)
DEFAULT_COMMANDS = (
    {"say": "Narrator extend time please", "does": "stay", "what": ""},
    {"say": "hey @narrator", "does": "go", "what": ""},
    {"say": "narrator change weather to", "does": "weather", "what": ""},
    {"say": "narrator change", "does": "change", "what": ""},
)
# Which default line is about which default command, so deleting a command also takes its line out.
DEFAULT_LINE_OF = {
    "Narrator extend time please": DEFAULT_LINES[1],
    "hey @narrator": DEFAULT_LINES[2],
    "narrator change weather to": DEFAULT_LINES[3],
    "narrator change": DEFAULT_LINES[4],
}
SAY_LIMIT = 120
WHAT_LIMIT = 300
MESSAGE_LIMIT = 3000


def _named(text: str) -> str:
    """In the copy, the default texts use the names from setup."""
    if _cast is None or not text:
        return text
    try:
        return _cast.localize(text)
    except Exception:
        return text


def default_message() -> str:
    return "\n".join(_named(line) for line in DEFAULT_LINES)


def defaults() -> dict:
    return {
        "message": default_message(),
        "dream": _named(DEFAULT_DREAM),
        "commands": [{"say": _named(c["say"]), "does": c["does"], "what": ""} for c in DEFAULT_COMMANDS],
    }


def does_from(text: str) -> tuple[str, str]:
    """(does, what) from the "What happens" box: one of the ready choices, or your own words."""
    text = " ".join(str(text or "").split())
    for key, label in ACTIONS:
        if text.lower() == label.lower():
            return key, ""
    return "custom", text[:WHAT_LIMIT]


def what_text(command: dict) -> str:
    """What the "What happens" box shows for a command."""
    return ACTION_LABELS.get(command["does"]) or command.get("what", "")


def clean_command(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    say = " ".join(str(raw.get("say") or "").split())[:SAY_LIMIT]
    if not words(say):
        return None
    does = raw.get("does") if raw.get("does") in KINDS else "custom"
    return {"say": say, "does": does, "what": " ".join(str(raw.get("what") or "").split())[:WHAT_LIMIT]}


def settings(prefs: dict | None) -> dict:
    """The saved message and commands, or the defaults for anything never saved."""
    saved = (prefs or {}).get("narrator")
    out = defaults()
    if not isinstance(saved, dict):
        return out
    if isinstance(saved.get("commands"), list):
        out["commands"] = [command for command in (clean_command(item) for item in saved["commands"]) if command]
    if isinstance(saved.get("dream"), str):
        out["dream"] = saved["dream"].strip().strip("*").strip()[:MESSAGE_LIMIT]
    if isinstance(saved.get("message"), str):
        out["message"] = saved["message"].strip()[:MESSAGE_LIMIT]
    elif isinstance(saved.get("start"), str) or isinstance(saved.get("end"), str):
        # Saved by the first version of the panel: a start, a line per command, and an end.
        lines = [str(saved.get("start") or "")]
        for item in saved.get("commands") or []:
            command = clean_command(item)
            if command:
                lines.append(str(item.get("tell") or "") or tell_line(command))
        lines.append(str(saved.get("end") or ""))
        out["message"] = "\n".join(line.strip() for line in lines if line.strip())[:MESSAGE_LIMIT]
    return out


def tell_line(command: dict) -> str:
    """A plain line about a new command, added to the message when you save it."""
    say = command["say"]
    return {
        "stay": f'if you want to stay where you are a little longer, say "{say}".',
        "go": f'if you want to go somewhere or do something, say "{say}" and what you want.',
        "weather": f'if you want different weather, say "{say}" and the weather you want.',
        "change": f'if you want something around you changed, say "{say}" and what to change.',
        "custom": f'you can also say "{say}".',
    }[command["does"]]


def belongs(line: str, command: dict) -> bool:
    """The message line is about this command: it quotes what she says, or it is the command's default line."""
    low = line.lower()
    say = command["say"].lower()
    if f'"{say}"' in low or tell_line(command).lower() in low:
        return True
    for default_say, default_line in DEFAULT_LINE_OF.items():
        if _named(default_say).lower() == say and _named(default_line).lower() in low:
            return True
    return False


def without_lines_of(message: str, command: dict) -> str:
    return "\n".join(line for line in message.splitlines() if not belongs(line, command))


def with_line_for(message: str, command: dict) -> str:
    """Add a line about a new command, before the last line (the goodbye), unless one is already there."""
    lines = [line for line in message.splitlines() if line.strip()]
    if any(belongs(line, command) for line in lines):
        return message
    spot = len(lines) - 1 if len(lines) >= 3 else len(lines)
    lines.insert(spot, tell_line(command))
    return "\n".join(lines)


def renamed(message: str, old: str, new: str, others: list[str]) -> str:
    """She says something new: the message quotes the new words. Left alone if the old words start another command."""
    if not old or old.lower() == new.lower():
        return message
    if any(other.lower().startswith(old.lower() + " ") for other in others):
        return message
    return re.sub(r'"' + re.escape(old) + r'(?=["\s.,!?])', lambda _m: '"' + new, message, flags=re.IGNORECASE)


def problems(found: dict) -> list[str]:
    """What is wrong with commands before they are saved. Empty when they are fine."""
    out = []
    seen = set()
    for number, command in enumerate(found.get("commands") or [], start=1):
        say = " ".join(str(command.get("say") or "").split())
        label = f'"{say}"' if say else f"Command {number}"
        tokens = words(say)
        if not tokens:
            out.append(f"Command {number}: type what Lora says.")
            continue
        only_name = all(token in names() | {"please"} for token in tokens)
        only_hey = all(token in names() | {"hey", "please"} for token in tokens)
        if only_name or (only_hey and command.get("does") != "go"):
            out.append(f"{label}: add a word or two, or the narrator would answer every time she says its name.")
        key = " ".join(tokens)
        if key in seen:
            out.append(f"{label}: another command already uses these words.")
        seen.add(key)
        if command.get("does") == "custom" and not str(command.get("what") or "").strip():
            out.append(f"{label}: type what happens, or pick one from the list.")
    return out


# Reading her messages ----------------------------------------------------------------------------


def names() -> set[str]:
    """The words that mean the narrator: "narrator", and in the copy its name from setup."""
    found = {"narrator"}
    if _cast is not None:
        try:
            found |= set(words(_cast.cast()["narrator_name"]))
        except Exception:
            pass
    return found


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower().replace("’", "'"))


def pattern(say: str) -> re.Pattern | None:
    """What she says, loosely: any punctuation between the words, and "please" may slip in."""
    tokens = words(say)
    if not tokens:
        return None
    who = "(?:" + "|".join(re.escape(name) for name in sorted(names(), key=len, reverse=True)) + ")"
    parts = [who if token in names() else re.escape(token) for token in tokens]
    between = r"[\W_]+(?:please[\W_]+)?"
    return re.compile(r"(?<![\w'])" + between.join(parts) + r"(?![\w'])", re.IGNORECASE)


def lead_words(say: str) -> str:
    """The words of a command without the narrator's name, "hey", or "please": "narrator take me" is "take me"."""
    return " ".join(token for token in words(say) if token not in names() | {"hey", "please"})


def short_part(text: str, limit: int) -> str:
    """The words up to the end of the sentence, without a trailing please."""
    part = re.split(r"[.!?\n\]\*\[]", text, maxsplit=1)[0]
    part = re.sub(r"\s+", " ", part).strip(" \t\r\n.,\"'")
    part = re.sub(r"[\s,]+please$", "", part, flags=re.IGNORECASE).strip()
    return part[:limit]


def clean_weather(text: str) -> str:
    body = short_part(text, 40)
    if re.search(r"\bper your request\b|\byour request\b", body, re.IGNORECASE):
        return ""
    return body


def longest_first(commands: list[dict], does: str) -> list[dict]:
    """Commands of one kind, the ones with more words first, so "narrator change weather to" beats "narrator change"."""
    return sorted((command for command in commands if command["does"] == does), key=lambda command: -len(words(command["say"])))


def request_in(command: dict, text: str, request_words) -> tuple[str, str] | None:
    """(kind, request) when she used this command. None when she did not.

    ("", "") means she used it but left out the needed part ("narrator change weather to per your request"),
    so nothing else in that message counts either.
    """
    found = pattern(command["say"])
    if found is None:
        return None
    match = found.search(str(text or "").replace("’", "'"))
    if not match:
        return None
    after = re.sub(r"^[\s,:;!@.\-]+", "", match.string[match.end():])
    does = command["does"]
    if does == "stay":
        return "extend", ""
    if does == "weather":
        body = clean_weather(after)
        return ("weather", body) if body else ("", "")
    if does == "custom":
        what = command.get("what") or lead_words(command["say"])
        extra = re.sub(r"^(?:please\b\W*)+|\W*\bplease\W*$", "", request_words(after), flags=re.IGNORECASE)
        extra = extra.strip(" !?.,;:")[:200]
        return "go", f"{what}. {extra}" if extra else what
    lead = lead_words(command["say"])
    if does == "change":
        request = f"{lead} {short_part(after, 200)}".strip()[:200]
        return ("change", request) if len(request.split()) >= 2 else None
    request = f"{lead} {request_words(after)}".strip()[:300]
    return ("go", request) if request else None


# Writing the heads-up -----------------------------------------------------------------------------


def message_text(found: dict, where: str, wait: str, middle: str, extends: int, part: str,
                 suggest_after: int, limit: int) -> str:
    """The heads-up: the first line in italics, then the nudge or offer (middle), then the other lines.

    After several extends the line about going comes before the line about staying; after the limit
    the line about staying is left out. At bedtime staying put is normal, so the message stays as you wrote it.
    """
    lines = [line.strip() for line in str(found.get("message") or "").splitlines() if line.strip()]
    if not lines:
        return middle
    first = lines[0].strip("*").strip().replace("{where}", where).replace("{wait}", wait)
    rest = [line.replace("{where}", where).replace("{wait}", wait) for line in lines[1:]]
    stays = [c for c in found["commands"] if c["does"] == "stay"]
    goes = [c for c in found["commands"] if c["does"] == "go"]
    is_stay = lambda line: any(belongs(line, c) for c in stays)
    is_go = lambda line: any(belongs(line, c) for c in goes)
    if part != "bedtime" and extends >= limit:
        rest = [line for line in rest if not is_stay(line)]
    elif part != "bedtime" and extends > suggest_after:
        first_stay = next((index for index, line in enumerate(rest) if is_stay(line)), None)
        going = [line for line in rest if is_go(line) and not is_stay(line)]
        if first_stay is not None and going:
            others = [line for line in rest if line not in going]
            spot = others.index(rest[first_stay])
            rest = others[:spot] + going + others[spot:]
    return f"*{first}*\n" + middle + " ".join(rest)


def go_phrase(found: dict) -> str:
    """What she says to go somewhere: your first "Go somewhere" command."""
    for command in found["commands"]:
        if command["does"] == "go":
            return command["say"]
    return ""


def with_go_phrase(text: str, found: dict) -> str:
    """The nudges and offers say "hey @narrator". If you use other words to go somewhere, they say yours."""
    say = go_phrase(found)
    if not say or say.lower() == "hey @narrator":
        return text
    return re.sub(r'"hey @narrator', lambda _match: '"' + say, text, flags=re.IGNORECASE)
