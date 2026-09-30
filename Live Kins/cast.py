"""Who is in the story: the main kin, you, the side chatter (optional), and the narrator.

The app is written with example names (Lora, Master, Mochi, Narrator). Everything that goes out,
to DeepSeek and to Kindroid, and everything shown in the log, passes through localize(), which
swaps in the names from setup. Commands are read with the narrator's real name.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

EXAMPLE = {
    "kin_name": "Lora",
    "kin_pronoun": "she",
    "user_name": "Master",
    "chatter_name": "Mochi",
    "chatter_what": "white Bichon dog",
    "narrator_name": "Narrator",
    "era": "",
}
PRONOUNS = ("she", "he", "they")
ERAS = {
    "Modern day": "Their world is the present day.",
    "Medieval, around 500 AD": (
        "Their world is around 500 AD: firelight and candles, wood, stone, wool, linen, and clay, old Roman ruins, "
        "villages, abbeys, and forests. Nothing modern: no electricity, screens, glass doors, plastic, cars, or machines."
    ),
    "Cozy fantasy": (
        "Their world is a gentle fantasy realm: cottages, enchanted forests, friendly creatures, soft glowing magic. "
        "Nothing modern: no electricity, screens, cars, or machines."
    ),
    "Victorian, around 1880": (
        "Their world is around 1880: gas lamps, steam trains, horse carriages, lace and wool. "
        "Nothing more modern: no electricity at home, screens, cars, or plastic."
    ),
    "Sci-fi future": "Their world is a calm, hopeful future: clean cities, gardens on rooftops, gentle robots, and starships.",
}

_cache: dict = {"mtime": None, "cast": None}


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cast() -> dict:
    """The names from setup, with the example names for anything left empty."""
    try:
        mtime = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _cache["cast"] is not None and _cache["mtime"] == mtime:
        return _cache["cast"]
    config = load_config()
    saved = config.get("cast") if isinstance(config.get("cast"), dict) else {}
    result = dict(EXAMPLE)
    for key in EXAMPLE:
        value = str(saved.get(key) or "").strip()
        if value:
            result[key] = value
    for key, field in (("master_profile", "user_name"), ("mochi_profile", "chatter_name"), ("narrator_profile", "narrator_name")):
        name = str((config.get(key) or {}).get("user_name") or "").strip()
        if name and not str(saved.get(field) or "").strip():
            result[field] = name
    result["has_chatter"] = saved.get("has_chatter", True) is not False
    if result["kin_pronoun"] not in PRONOUNS:
        result["kin_pronoun"] = "she"
    _cache.update(mtime=mtime, cast=result)
    return result


def reset_cache() -> None:
    _cache.update(mtime=None, cast=None)


def needs_setup() -> bool:
    """A new copy: no cast, or a required profile id still missing."""
    config = load_config()
    if not isinstance(config.get("cast"), dict):
        return True
    for key in ("master_profile", "narrator_profile"):
        if not str((config.get(key) or {}).get("id") or "").strip():
            return True
    return False


def has_chatter() -> bool:
    return bool(cast()["has_chatter"])


def is_example() -> bool:
    now = cast()
    return all(now[key] == value for key, value in EXAMPLE.items() if key != "era") and now["has_chatter"]


def era_text() -> str:
    era = cast().get("era") or ""
    return ERAS.get(era, era)


def _case(word: str, like: str) -> str:
    """Write word the way the example was written: lora, Lora, or LORA."""
    if like.isupper() and len(like) > 1:
        return word.upper()
    if like[:1].islower():
        return word.lower()
    return word[:1].upper() + word[1:]


_NO_CHATTER = [
    # Clauses about the side chatter, when there is none: ", Mochi curled up beside her" and "with Mochi".
    re.compile(r"^\s*-[^\n]*\bmochi\b[^\n]*\n?", re.IGNORECASE | re.MULTILINE),
    re.compile(r",?\s*\b(?:and|with)\s+mochi\b(?:'s)?", re.IGNORECASE),
    re.compile(r",\s*mochi\b[^.,;*\n]*", re.IGNORECASE),
    re.compile(r"\bmochi\b[^.;*\n]*[.;]?\s*", re.IGNORECASE),
]


def localize(text: str) -> str:
    """Swap the example names for the ones from setup. Unchanged when the names are the examples."""
    if not text or is_example():
        return text
    now = cast()
    out = str(text)
    # Through placeholders, so a kin named like another example name is not swapped twice.
    marks = {}
    swaps = [
        ("white bichon dog", now["chatter_what"]),
        ("white bichon", now["chatter_what"]),
        ("lora", now["kin_name"]),
        ("master", now["user_name"]),
        ("narrator", now["narrator_name"]),
    ]
    if now["has_chatter"]:
        swaps.insert(2, ("mochi", now["chatter_name"]))
    else:
        for pattern in _NO_CHATTER:
            out = pattern.sub("", out)
        out = re.sub(r"\bwhite bichon(?: dog)?\b", "", out, flags=re.IGNORECASE)
    for index, (name, replacement) in enumerate(swaps):
        mark = f"\x00{index}\x00"
        marks[mark] = (name, replacement)
        out = re.sub(
            rf"(?<![\w]){re.escape(name)}(?!\w)",
            lambda found, mark=mark: mark + ("U" if found.group(0).isupper() else "c" if found.group(0)[0].isupper() else "l"),
            out,
            flags=re.IGNORECASE,
        )
    for mark, (name, replacement) in marks.items():
        out = out.replace(mark + "U", replacement.upper()).replace(mark + "c", replacement[:1].upper() + replacement[1:])
        out = out.replace(mark + "l", replacement.lower())
    return re.sub(r"[ \t]{2,}", " ", out)


_PRONOUN_SWAPS = {
    "he": [("herself", "himself"), ("she's", "he's"), ("she", "he")],
    "they": [("herself", "themselves"), ("she's", "they're"), ("she is", "they are"), ("she was", "they were"),
             ("she has", "they have"), ("she", "they")],
}
_OBJECT_AFTER = {
    "to", "and", "a", "an", "the", "with", "that", "in", "on", "at", "for", "up", "down", "back", "when", "so",
    "if", "out", "off", "over", "about", "again", "now", "more", "some", "time", "go", "know", "feel", "be",
    "rest", "sleep", "stay", "see", "come", "walk", "sit", "stand", "look", "try", "have", "get", "think",
}


def pronouns(text: str) -> str:
    """For the narrator's own set lines: she and her become he/him/his or they/them/their."""
    kind = cast()["kin_pronoun"]
    if not text or kind == "she":
        return text
    out = text
    for old, new in _PRONOUN_SWAPS[kind]:
        out = re.sub(rf"\b{old}\b", lambda found, new=new: _case(new, found.group(0)), out, flags=re.IGNORECASE)
    object_word, own_word = ("him", "his") if kind == "he" else ("them", "their")
    return re.sub(r"\b(her)\b(\W+(\w+))?", lambda found: her_fix(found, object_word, own_word), out, flags=re.IGNORECASE)


def her_fix(found: re.Match, object_word: str, own_word: str) -> str:
    following = found.group(3) or ""
    between = (found.group(2) or "")[: len(found.group(2) or "") - len(following)]
    # "beside her." or "her, and": punctuation right after means "him"/"them"; "her book" means "his"/"their".
    ends = bool(between.strip())
    word = object_word if (ends or not following or following.lower() in _OBJECT_AFTER) else own_word
    return _case(word, found.group(1)) + (found.group(2) or "")


def pronoun_note() -> str:
    """One line for DeepSeek when the kin is not she/her."""
    now = cast()
    if now["kin_pronoun"] == "she":
        return ""
    words = "he/him/his" if now["kin_pronoun"] == "he" else "they/them/their"
    return f"\n{now['kin_name']} uses {words} pronouns. Use them for {now['kin_name']} everywhere."


def companion_words() -> set[str]:
    """Words that show the side chatter is in a scene."""
    now = cast()
    words = {word for word in re.findall(r"[a-z0-9]+", now["chatter_name"].lower()) if len(word) > 1}
    if is_example():
        words |= {"dog", "doggy", "bichon"}
    return words


def name_words() -> set[str]:
    """Names that are never part of a place's name or items."""
    now = cast()
    found = set()
    for key in ("kin_name", "chatter_name", "user_name", "narrator_name"):
        found |= set(re.findall(r"[a-z0-9]+", now[key].lower()))
    return found | {"lora", "mochi"}
