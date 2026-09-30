"""Persistent world for Lora.

Python owns the world: where Lora and Mochi are, the objects that matter, the
weather, goals, and memories. DeepSeek proposes the next step as JSON, and
Python checks it against the Environment list before anything is saved.
Kindroid still only receives the short setting and the before/now message.

Everything is stored under the "world" and "schedule" keys of state.json through
update_scene.update_state. load_world and _commit are the only storage seams, so
a later move to SQLite stays inside them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import random
import re
import uuid
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import cast
import update_scene as us

WORLD_MAP_PATH = us.ROOT / "world_map.json"
LOG_PATH = us.ROOT / "simulation.log"
WORLD_VERSION = 1
MEMORY_LIMIT = 200
MEMORY_PROMPT = 5
JOURNAL_LIMIT = 30
VISIT_LIMIT = 8
OBJECT_LIMIT = 12
OBJECT_HOLD = timedelta(hours=12)
PLAN_TRIES = 4
PLACE_SAMPLE = 12
WEATHER_HOLD = timedelta(hours=1)
WEATHER_DEFAULT = ["clear", "rain", "thunderstorms", "fog", "wind", "falling leaves"]
ZONES = ("bedroom", "indoor", "outdoor")
INSIDE = {"bedroom", "indoor"}
TIME_WORDS = {"morning": 8 * 60, "noon": 12 * 60, "afternoon": 12 * 60, "evening": 18 * 60, "night": 22 * 60, "midnight": 0}
DISCOVERY_LIMIT = 80
ACTIVITY_MINUTES = (5, 120)
# Real-life lengths DeepSeek measures an action against, so it stops giving everything 20 to 30 minutes.
MINUTES_GUIDE = (
    "grab a snack or a drink 5 to 10, water the plants 10, hang one load of laundry 10 to 15, "
    "feed the fish 5, brush her hair 5 to 10, walk somewhere nearby and look around 10 to 20, "
    "stroll a garden 15 to 30, shower 15, bath 30 to 45, cook dinner 40 to 60, eat a meal 20 to 30, "
    "read, draw, or play a game 30 to 90, a craft project 45 to 90, settle in for the night 60 to 120"
)
DISCOVERIES_PER_DAY = 4
DISCOVERED_ZONES = ("indoor", "outdoor")
NEW_NAME = re.compile(r"[A-Za-z][A-Za-z' -]{2,39}")
BLOCKED = re.compile(
    r"\b(?:portals?|teleport\w*|wormholes?|spaceships?|rockets?|time machines?|other planets?|another planet|"
    r"other dimensions?|another dimension|outer space)\b"
)
WIND_DOWN = ("Wind down for the night", ["settling in", "resting quietly", "getting sleepy", "getting ready for bed", "sleeping"])
START_DAY = ("Start the day", ["waking up", "stretching", "getting ready", "doing something light"])

PLANNER_SYSTEM = (
    "You plan the next step of a small life simulation. Python owns the world and checks everything you propose. "
    "Only use the places and objects you are given. Answer with one JSON object and nothing else."
)

log = logging.getLogger("lora.simulation")


def _log_ready() -> None:
    if log.handlers:
        return
    handler = RotatingFileHandler(LOG_PATH, maxBytes=256_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def log_event(text: str, level: int = logging.INFO) -> None:
    _log_ready()
    log.log(level, " ".join(cast.localize(str(text)).split()))


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(us.zone())


def clock_text(moment: datetime | None) -> str:
    return moment.strftime("%I:%M %p").lstrip("0") if moment else ""


def _short(value, limit: int) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    return " ".join(str(value).split())[:limit].strip()


# ---------------------------------------------------------------- catalog

NAME_BREAKS = (
    " with ", " that ", " leading ", " connecting ", " built ", " containing ", " filled ",
    " displaying ", " hanging ", " running ", " on the ", " where ", " capable ", " showing ",
    " including ", " controlling ", " allowing ", " flowing ", " around the ", " near the ", " at the ", ",",
)
ROOM_HINTS = ("the room", "the bedroom", "the window", "the ceiling")
BEDROOM_NAMES = {"window", "floor rug"}
OUTDOOR = re.compile(
    r"\b(?:porch|backyard|greenhouse|fountain|swimming pool|hot tub|treehouse|playground|picnic|fire pit|"
    r"vegetable patch|chicken coop|butterfly garden|bird-watching|birdbath|clothesline|tool shed|rooftop|"
    r"courtyard|village street|forest trail|walking path|pond|waterfall|cave|windmill|wishing well|archway|"
    r"garden maze|stage|gazebo|bridge|stream|firefly|rainbow|weather vane|mailbox flag|open field)\b"
)
GENERIC = {
    "small", "large", "area", "room", "corner", "station", "table", "wall", "with", "and", "the", "that",
    "for", "indoor", "outdoor", "outside", "display", "little", "full", "different", "inside", "into",
    "from", "near", "set", "her", "lora", "she", "mochi", "dog", "bichon", "white", "brown", "glass",
    "wooden", "old", "tiny", "his", "its", "one", "two", "some", "lit", "soft", "low",
}


def slug(text) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _key(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def key_words(text) -> set[str]:
    words = set()
    for word in re.findall(r"[a-z]+", str(text or "").lower()):
        if len(word) < 3 or word in GENERIC:
            continue
        words.add(_key(word))
    return words


def _split_entry(sentence: str) -> tuple[str, str]:
    text = re.sub(r"^(?:a|an|the)\s+", "", sentence.strip().rstrip(".").strip(), flags=re.IGNORECASE)
    lowered = text.lower()
    cut = len(text)
    for word in NAME_BREAKS:
        at = lowered.find(word)
        if 0 < at < cut:
            cut = at
    name = re.sub(r"\s+outside$", "", text[:cut].strip(), flags=re.IGNORECASE)
    return name, text[cut:].strip()


def _items(rest: str) -> list[str]:
    match = re.search(r"\b(?:with|containing|including)\s+(.*)$", rest.strip(" ,."), flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    items = []
    for part in re.split(r",\s*|\s+and\s+", match.group(1)):
        item = re.sub(r"^(?:(?:a|an|the|and)\s+)+", "", part.strip(" ."), flags=re.IGNORECASE).strip()
        pet = re.search(r"bichon|doggy|mochi", item, re.IGNORECASE) or set(re.findall(r"[a-z0-9]+", item.lower())) & cast.companion_words()
        if item and len(item) <= 48 and not pet:
            items.append(item)
    return items


def _is_system(name: str) -> bool:
    lowered = name.lower()
    return bool(re.search(r"\bsystem\b|\beffects\b", lowered)) or lowered in {"sound environment", "interactive furniture"}


def _zone(name: str, sentence: str, bedroom_line: bool) -> str:
    """Bedroom for the bedroom paragraph and the room's own fixtures, outdoor when the list says so."""
    lowered = name.lower()
    full = sentence.lower()
    if bedroom_line:
        return "outdoor" if lowered == "open field" else "bedroom"
    if lowered in BEDROOM_NAMES or lowered.startswith("ceiling"):
        return "bedroom"
    if "indoor" in lowered:
        return "indoor"
    if any(hint in full for hint in ROOM_HINTS):
        return "bedroom"
    if re.search(r"\b(?:outside|outdoor)\b", full) or OUTDOOR.search(lowered):
        return "outdoor"
    return "indoor"


def build_catalog(environment: str, overrides: dict | None = None) -> dict:
    """Places, objects, and systems read from the Environment text, which stays the source of truth."""
    places: dict[str, dict] = {}
    systems: dict[str, dict] = {}
    lines = [line.strip() for line in str(environment or "").splitlines() if line.strip()]
    # The first line may be a paragraph: the outside, the bedroom, and spots in the bedroom.
    # Its other sentences only count as bedroom spots when it describes the bedroom.
    first_is_bedroom = bool(lines) and bool(re.search(r"\bbedroom\b", lines[0], re.IGNORECASE))
    for index, line in enumerate(lines):
        first = index == 0
        sentences = [part for part in re.split(r"(?<=\.)\s+", line) if part.strip()] if first else [line]
        for sentence in sentences:
            name, rest = _split_entry(sentence)
            if not name:
                continue
            items = _items(rest)
            lowered = name.lower()
            if first and "bedroom" in lowered:
                items = [re.sub(r"\bbedroom\s+", "", lowered).strip()] + [
                    part.strip() for part in rest.strip(" ,.").split(",") if part.strip()
                ]
                name = "Bedroom"
            elif first and lowered.startswith("outside environment"):
                name = "Open field"
                items = [item for item in items if item.lower() != "open field"]
            name = name[:1].upper() + name[1:]
            key = slug(name)
            if not key:
                continue
            known = places.get(key) or systems.get(key)
            if known:
                known["items"] += [item for item in items if item not in known["items"]]
                continue
            if _is_system(name):
                systems[key] = {"id": key, "name": name, "items": items}
                continue
            bedroom_line = first and (first_is_bedroom or lowered.startswith("outside environment"))
            places[key] = {"id": key, "name": name, "zone": _zone(name, sentence, bedroom_line), "items": items, "origin": "original"}
    if not places:
        places["bedroom"] = {"id": "bedroom", "name": "Bedroom", "zone": "bedroom", "items": [], "origin": "original"}
    for key, zone in ((overrides or {}).get("zones") or {}).items():
        if key in places and zone in ZONES:
            places[key]["zone"] = zone
    weather = list(WEATHER_DEFAULT)
    for system in systems.values():
        if "weather" in system["name"].lower() and system["items"]:
            weather = ["clear"] + [" ".join(item.lower().split()) for item in system["items"]]
    words = {}
    vocab: set[str] = set()
    for pid, place in places.items():
        found = key_words(place["name"])
        for item in place["items"]:
            found |= key_words(item)
        words[pid] = found
        vocab |= found
    for system in systems.values():
        vocab |= key_words(system["name"])
        for item in system["items"]:
            vocab |= key_words(item)
    return {"places": places, "systems": systems, "weather": weather, "words": words, "vocab": vocab}


_catalog_cache: dict[str, dict] = {}


def load_map() -> dict:
    """Connections and zone overrides from world_map.json. Missing or broken means none."""
    data: dict = {}
    if WORLD_MAP_PATH.exists():
        try:
            data = json.loads(WORLD_MAP_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log_event("world_map.json could not be read. Using no extra connections.", logging.WARNING)
            data = {}
    if not isinstance(data, dict):
        data = {}
    connections = [
        link for link in data.get("connections") or [] if isinstance(link, dict) and link.get("from") and link.get("to")
    ]
    zones = data.get("zones") if isinstance(data.get("zones"), dict) else {}
    return {"connections": connections, "zones": zones}


def catalog_for(environment: str, discovered: dict | None = None) -> dict:
    """The original Environment catalog, plus the places Lora has discovered."""
    links = load_map()
    key = hashlib.sha1((str(environment) + json.dumps(links, sort_keys=True)).encode("utf-8")).hexdigest()
    if key not in _catalog_cache:
        _catalog_cache.clear()
        _catalog_cache[key] = build_catalog(environment, links)
    base = _catalog_cache[key]
    return with_discovered(base, discovered) if discovered else base


def with_discovered(base: dict, discovered: dict) -> dict:
    """A copy of the catalog with discovered places added. Originals always win a name clash."""
    places = dict(base["places"])
    words = dict(base["words"])
    vocab = set(base["vocab"])
    links = []
    entries = [entry for entry in discovered.values() if isinstance(entry, dict)]
    for entry in entries:
        pid = slug(entry.get("id") or entry.get("name"))
        name = _short(entry.get("name"), 40)
        zone = entry.get("zone")
        if not pid or not name or zone not in DISCOVERED_ZONES or pid in base["places"] or pid in places:
            continue
        items = [_short(item, 40) for item in entry.get("items") or [] if _short(item, 40)]
        places[pid] = {
            "id": pid,
            "name": name,
            "zone": zone,
            "items": items,
            "origin": "discovered",
            "description": _short(entry.get("description"), 240),
            "from": slug(entry.get("from")),
            "via": _short(entry.get("via"), 60) or "a path",
        }
        found = key_words(name)
        for item in items:
            found |= key_words(item)
        words[pid] = found
        vocab |= found
    for pid, place in places.items():
        if place.get("origin") == "discovered" and place["from"] in places and place["from"] != pid:
            links.append((place["from"], pid, place["via"]))
    return dict(base, places=places, words=words, vocab=vocab, discovered_links=links)


def _ends(ref, place: dict) -> bool:
    ref = str(ref).strip().lower()
    if ref.startswith("zone:"):
        return place.get("origin") != "discovered" and place["zone"] == ref[5:]
    return place["id"] == slug(ref)


def can_move(catalog: dict, links: dict, source: str, target: str) -> tuple[bool, str]:
    """Walking inside the house, or around outside, is one step. Crossing needs a known connection."""
    places = catalog["places"]
    if target not in places:
        return False, f'"{target}" is not a place in her world'
    if source == target:
        return True, "stays"
    if source not in places:
        return True, "arrives"
    here, there = places[source], places[target]
    for link in links.get("connections", []):
        if (_ends(link["from"], here) and _ends(link["to"], there)) or (_ends(link["from"], there) and _ends(link["to"], here)):
            return True, str(link.get("via") or "a known way")
    for start, end, via in catalog.get("discovered_links", []):
        if {start, end} == {source, target}:
            return True, via
    if there.get("origin") == "discovered":
        parent = places.get(there.get("from"), {}).get("name", "place she found it from").lower()
        return False, f"the {there['name'].lower()} can only be reached from the {parent}"
    if here.get("origin") == "discovered":
        parent = places.get(here.get("from"), {}).get("name", "place she found it from").lower()
        return False, f"from the {here['name'].lower()} she can only go back to the {parent}"
    if here["zone"] in INSIDE and there["zone"] in INSIDE:
        return True, "walks inside the house"
    if here["zone"] == "outdoor" and there["zone"] == "outdoor":
        return True, "walks outside"
    return False, f"there is no known way from the {here['name'].lower()} to the {there['name'].lower()}"


def infer_place(catalog: dict, text: str, fallback: str = "") -> str:
    words = key_words(text)
    best, best_score = fallback, 0
    for pid, place in catalog["places"].items():
        name_hits = len(words & key_words(place["name"]))
        item_hits = len(words & catalog["words"][pid]) - name_hits
        score = 3 * name_hits + item_hits
        if score > best_score:
            best, best_score = pid, score
    if best in catalog["places"]:
        return best
    return "bedroom" if "bedroom" in catalog["places"] else next(iter(catalog["places"]))


def daylight(now: datetime) -> str:
    minutes = us.clock_minutes(now)
    if 6 * 60 <= minutes < 7 * 60:
        return "dawn"
    if 7 * 60 <= minutes < 18 * 60:
        return "daylight"
    if 18 * 60 <= minutes < 20 * 60:
        return "dusk"
    return "dark"


# ---------------------------------------------------------------- world state


def new_world(catalog: dict, scene: str, now: datetime, last_seen: datetime | None) -> dict:
    place = infer_place(catalog, scene)
    return {
        "version": WORLD_VERSION,
        "lora": {"location": place, "activity": scene},
        "mochi": {"location": place, "activity": "with Lora"},
        "clock": iso(last_seen or now),
        "weather": "clear",
        "weather_since": iso(now),
        "lighting": daylight(now),
        "objects": {},
        "situation": scene,
        "setting": scene,
        "goal": None,
        "goals_paused": [],
        "goals_done": [],
        "memories": [],
        "journal": [],
        "visits": [place],
        "discovered": {},
        "last_sim_at": iso(last_seen) if last_seen else "",
        "pending": None,
        "last_error": "",
    }


def load_world(state: dict) -> dict | None:
    world = state.get("world")
    if not isinstance(world, dict) or world.get("version") != WORLD_VERSION:
        return None
    world = copy.deepcopy(world)
    for key, default in (
        ("memories", []), ("journal", []), ("visits", []), ("objects", {}), ("goals_paused", []), ("goals_done", []),
        ("discovered", {}),
    ):
        if not isinstance(world.get(key), type(default)):
            world[key] = copy.copy(default)
    for part in ("lora", "mochi"):
        if not isinstance(world.get(part), dict):
            world[part] = {"location": "", "activity": ""}
    return world


def _validate_state(state: dict) -> None:
    world = state.get("world")
    problems = []
    if not isinstance(world, dict):
        problems.append("the world is missing")
    else:
        for part in ("lora", "mochi"):
            if not isinstance(world.get(part), dict) or not world[part].get("location"):
                problems.append(f"{part} has no location")
        if world.get("mochi", {}).get("location") != world.get("lora", {}).get("location"):
            problems.append("Mochi is not with Lora")
        setting = world.get("setting")
        if not isinstance(setting, str) or not setting.strip() or len(setting) > us.SCENE_LIMIT:
            problems.append("the setting is empty or too long")
        for key in ("memories", "journal", "visits"):
            if not isinstance(world.get(key), list):
                problems.append(f"{key} is not a list")
        discovered = world.get("discovered", {})
        if not isinstance(discovered, dict):
            problems.append("discovered places are not a list of places")
        else:
            for pid, entry in discovered.items():
                if (
                    not isinstance(entry, dict)
                    or entry.get("id") != pid
                    or not entry.get("name")
                    or entry.get("zone") not in DISCOVERED_ZONES
                    or not entry.get("from")
                ):
                    problems.append(f"the discovered place {pid} is incomplete")
    if problems:
        raise RuntimeError("The new world state was not saved: " + "; ".join(problems) + ".")


def _commit(world: dict, setting: str) -> None:
    def mutate(state: dict) -> None:
        state["world"] = world
        us.append_history(state, setting)

    us.update_state(mutate, validate=_validate_state)


def _update_world(change) -> None:
    def mutate(state: dict) -> None:
        world = state.get("world")
        if isinstance(world, dict):
            change(world)

    us.update_state(mutate)


def _sync(world: dict, catalog: dict, scene: str) -> None:
    """Keep Python's world in line with the setting you see, if it was edited by hand."""
    places = catalog["places"]
    if world["lora"].get("location") not in places:
        place = infer_place(catalog, scene)
        world["lora"]["location"] = place
        world["mochi"]["location"] = place
        log_event(f"her place was not in the Environment list anymore, so she is now at {place}")
    if us.normalize(scene) != us.normalize(world.get("setting") or ""):
        place = infer_place(catalog, scene, fallback=world["lora"]["location"])
        world["lora"] = {"location": place, "activity": scene}
        world["mochi"] = {"location": place, "activity": world["mochi"].get("activity") or "with Lora"}
        world["setting"] = scene
        world["situation"] = scene
        log_event(f"the setting was changed outside the simulation, so the world follows it: {place}")
    world["mochi"]["location"] = world["lora"]["location"]


# ---------------------------------------------------------------- memory


def _importance(value) -> int:
    try:
        return max(1, min(3, int(value)))
    except (TypeError, ValueError):
        return 1


def add_memory(world: dict, text, now: datetime, where: str, who: list[str], kind: str, importance=1, goal_id: str = "") -> dict | None:
    text = _short(text, 200)
    if not text:
        return None
    memories = world.setdefault("memories", [])
    if any(us.normalize(item.get("text", "")) == us.normalize(text) for item in memories[-10:]):
        return None
    memory = {
        "id": uuid.uuid4().hex[:8],
        "text": text,
        "when": iso(now),
        "where": where,
        "who": list(who),
        "kind": kind,
        "importance": _importance(importance),
        "goal": goal_id,
        "active": True,
    }
    memories.append(memory)
    _trim_memories(world, now)
    log_event(f"memory ({kind}): {text}")
    return memory


def _trim_memories(world: dict, now: datetime) -> None:
    memories = world.get("memories", [])
    for item in memories:
        when = parse_time(item.get("when"))
        if item.get("active") and item.get("importance", 1) <= 1 and when and now - when > timedelta(days=30):
            item["active"] = False
    while len(memories) > MEMORY_LIMIT:
        drop = next((item for item in memories if not item.get("active")), None)
        if drop is None:
            drop = min(memories, key=lambda item: (item.get("importance", 1), item.get("when", "")))
        memories.remove(drop)


def relevant_memories(world: dict, scene: str, now: datetime, limit: int = MEMORY_PROMPT) -> list[dict]:
    """Only the memories that matter for this step: same place, same goal, shared words, recent, important."""
    goal = world.get("goal") or {}
    focus = key_words(scene) | key_words(goal.get("title", ""))
    place = world["lora"]["location"]
    scored = []
    for item in world.get("memories", []):
        if not item.get("active"):
            continue
        score = item.get("importance", 1) * 2
        if item.get("where") == place:
            score += 3
        if goal and item.get("goal") == goal.get("id"):
            score += 3
        score += len(focus & key_words(item.get("text", "")))
        when = parse_time(item.get("when"))
        if when and now - when < timedelta(days=1):
            score += 2
        elif when and now - when < timedelta(days=7):
            score += 1
        scored.append((score, item.get("when", ""), item))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [entry[2] for entry in scored[:limit]]


# ---------------------------------------------------------------- goals


def make_goal(title: str, steps: list[str], now: datetime, source: str) -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "steps": list(steps),
        "step": 0,
        "status": "active",
        "source": source,
        "started": iso(now),
        "updated": iso(now),
    }


def goal_text(goal: dict | None) -> str:
    if not goal or not goal.get("steps"):
        return ""
    steps = goal["steps"]
    index = min(int(goal.get("step", 0)), len(steps) - 1)
    return f"{goal['title']}: {steps[index]} ({index + 1} of {len(steps)})"


def _finish_goal(world: dict, now: datetime, note: str = "") -> None:
    goal = world.get("goal")
    if not goal:
        return
    done = dict(goal, status="done", updated=iso(now), finished=iso(now))
    world["goals_done"] = (world.get("goals_done", []) + [done])[-10:]
    world["goal"] = None
    add_memory(
        world,
        note or f"Lora finished: {goal['title'].lower()}.",
        now,
        where=world["lora"]["location"],
        who=["Lora", "Mochi"],
        kind="goal",
        importance=2,
        goal_id=goal["id"],
    )
    log_event(f"goal done: {goal['title']}")


def _woke_up(world: dict, last: datetime | None, now: datetime) -> bool:
    """True when this is her first step of the morning after the night, even if Python was off at wake time."""
    minutes = us.clock_minutes(now)
    if not 8 * 60 <= minutes < 12 * 60:
        return False
    if (world.get("goal") or {}).get("source") == "night":
        return True
    return last is not None and us.is_late_night(last) and now - last >= timedelta(hours=3)


def _routine_goals(world: dict, now: datetime, waking: bool) -> None:
    """The existing day shape: wind down at night, start the day in the morning. Planner goals wait for the night to pass."""
    goal = world.get("goal")
    source = (goal or {}).get("source")
    if waking:
        if source == "night":
            _finish_goal(world, now, "Lora slept through the night with Mochi.")
        if not world.get("goal"):
            world["goal"] = make_goal(*START_DAY, now, "morning")
            log_event("goal: start the day")
        return
    if us.is_late_night(now):
        if source == "night":
            return
        if goal and source == "planner":
            world["goals_paused"] = (world.get("goals_paused", []) + [goal])[-5:]
            log_event(f"goal paused for the night: {goal['title']}")
        elif goal:
            world["goals_done"] = (world.get("goals_done", []) + [dict(goal, status="dropped", updated=iso(now))])[-10:]
        world["goal"] = make_goal(*WIND_DOWN, now, "night")
        log_event("goal: wind down for the night")
        return
    if source == "night":
        _finish_goal(world, now, "Lora got through the night with Mochi.")
    elif source == "morning" and us.clock_minutes(now) >= 12 * 60:
        _finish_goal(world, now, "Lora got the day started.")
    if not world.get("goal") and world.get("goals_paused"):
        resumed = world["goals_paused"].pop(0)
        resumed["updated"] = iso(now)
        world["goal"] = resumed
        log_event(f"goal resumed: {resumed['title']}")


def _apply_goal(world: dict, plan: dict, now: datetime, late_night: bool) -> None:
    goal = world.get("goal")
    if goal and plan.get("goal_step_done"):
        last_index = len(goal["steps"]) - 1
        if goal.get("source") == "night":
            goal["step"] = min(goal["step"] + 1, last_index)
        else:
            goal["step"] += 1
        goal["updated"] = iso(now)
        log_event(f"goal progress: {goal_text(goal) or goal['title']}")
        if goal["step"] > last_index:
            _finish_goal(world, now)
    if not world.get("goal") and plan.get("new_goal") and not late_night:
        title, steps = plan["new_goal"]
        world["goal"] = make_goal(title, steps, now, "planner")
        add_memory(
            world,
            f"Lora decided to {title[:1].lower() + title[1:]}.",
            now,
            where=plan["location"],
            who=["Lora"],
            kind="goal",
            importance=2,
            goal_id=world["goal"]["id"],
        )
        log_event(f"goal started: {title}")


# ---------------------------------------------------------------- planning


def elapsed_note(last: datetime | None, now: datetime) -> str:
    if last is None:
        return "This is the first step of the simulation."
    minutes = max(0, int((now - last).total_seconds() // 60))
    if minutes < 90:
        return f"About {minutes} minutes passed since the last step at {clock_text(last)}."
    hours = round(minutes / 60)
    span = f"{hours} hours" if hours < 48 else f"{round(hours / 24)} days"
    when = last.strftime("%A %I:%M %p").replace(" 0", " ")
    return (
        f"About {span} passed since the last step on {when}. Python was not stepping the world in between, "
        "so nothing in that gap was seen. Continue plausibly from the last known state, as hours later, not seconds."
    )


def _candidates(ctx: dict, rng: random.Random) -> tuple[set[str], list[str]]:
    catalog, links = ctx["catalog"], ctx["links"]
    places = catalog["places"]
    current = ctx["world"]["lora"]["location"]
    if ctx["wish"]:
        wanted = key_words(ctx["wish"])
        usable = [
            pid for pid in places
            if places[pid].get("origin") != "discovered" or can_move(catalog, links, current, pid)[0]
        ]
        ranked = sorted(usable, key=lambda pid: len(wanted & catalog["words"][pid]), reverse=True)
        matched = [pid for pid in ranked[:8] if wanted & catalog["words"][pid]]
        others = [pid for pid in usable if pid not in matched and pid != current]
        return set(usable), matched + rng.sample(others, min(6, len(others)))
    reachable = [pid for pid in places if can_move(catalog, links, current, pid)[0]]
    if ctx["late_night"]:
        allowed = {pid for pid in reachable if places[pid]["zone"] == "bedroom"} or set(reachable)
        return allowed, sorted(allowed)
    recent = set(ctx["world"].get("visits", [])[-3:]) | {current}
    allowed = {pid for pid in reachable if pid not in recent}
    if not allowed:
        allowed = {pid for pid in reachable if pid != current} or set(reachable)
    shown = sorted(allowed)
    shown = rng.sample(shown, min(PLACE_SAMPLE, len(shown)))
    shown += sorted(pid for pid in allowed if places[pid].get("origin") == "discovered" and pid not in shown)
    return allowed, shown


COZY_WORDS = {
    "living", "fireplace", "cozy", "kitchen", "tea", "bath", "bathroom", "spa", "reading", "nook", "library",
    "bedroom", "sofa", "lounge", "music", "theater", "dining", "breakfast", "study", "coffee", "baking",
}


def walkable_pick(ctx: dict, rng: random.Random, evening: bool = False) -> str | None:
    """A random place she can walk to from here, for when the narrator moves her on.

    Only places she can reach on foot (the usual moves), not where she is or was just now.
    In the evening only cozy places inside the house. Real areas with several things in
    them come first, so she is not sent to a mirror or a doorbell.
    """
    places = ctx["catalog"]["places"]
    current = ctx["world"]["lora"]["location"]
    options = sorted(pid for pid in ctx["allowed"] if pid != current and can_move(ctx["catalog"], ctx["links"], current, pid)[0])
    if evening:
        inside = [pid for pid in options if places[pid]["zone"] in INSIDE]
        cozy = [pid for pid in inside if key_words(places[pid]["name"]) & {_key(word) for word in COZY_WORDS}]
        options = cozy or inside or options
    for least in (5, 3, 0):
        pool = [pid for pid in options if len(places[pid].get("items") or []) >= least]
        if pool:
            return rng.choice(pool)
    return None


def _place_line(place: dict) -> str:
    items = ", ".join(place["items"][:6])
    kind = f"{place['zone']}, discovered" if place.get("origin") == "discovered" else place["zone"]
    return f"- {place['id']}: {place['name']} ({kind})" + (f" - {items}" if items else "")


def discovery_block(world: dict, now: datetime, late_night: bool, wish: str) -> str:
    """Why she cannot find a new place right now. Empty means she can."""
    if late_night and not wish:
        return "it is late at night, so she does not go looking for new places"
    found = world.get("discovered", {})
    if len(found) >= DISCOVERY_LIMIT:
        return "her world already has as many discovered places as it can hold"
    today = [
        entry for entry in found.values()
        if isinstance(entry, dict) and (parse_time(entry.get("discovered_at")) or now - timedelta(days=2)) > now - timedelta(days=1)
    ]
    if len(today) >= DISCOVERIES_PER_DAY:
        return f"she already found {DISCOVERIES_PER_DAY} new places today"
    return ""


def _plan_prompt(ctx: dict, rejection: str) -> str:
    world = ctx["world"]
    places = ctx["catalog"]["places"]
    here = places[world["lora"]["location"]]
    objects = "\n".join(
        f"  - {item.get('name', key)}: {item.get('state', '')}" for key, item in world.get("objects", {}).items()
    ) or "  - none noted"
    memories = "\n".join(f"- {item['text']}" for item in ctx["memories"]) or "- nothing yet"
    recent = "\n".join(f"- {item}" for item in ctx["recent"][-4:]) or "- none"
    goal = goal_text(world.get("goal")) or "none right now"
    shown = "\n".join(_place_line(places[pid]) for pid in ctx["shown"])
    stay = f' She can also stay at "{here["id"]}".' if here["id"] in ctx["allowed"] else ""
    asked = ""
    if ctx["wish"]:
        asked = (
            f"\nRequest from the chat ({ctx['requested_by'] or 'someone'}): {ctx['wish']}\n"
            "Follow this request. Do not replace it with a different place.\n"
        )
        if ctx.get("requested_place"):
            asked += f"\"location\" must be \"{ctx['requested_place']}\".\n"
        if ctx.get("weather_asked"):
            asked += f"The weather is now {ctx['weather_asked']}. The setting shows it.\n"
        asked += asked_rules(ctx.get("asked_words", ""), ctx.get("requested_by", ""))
        asked += ctx.get("extra_rules", "")
    correction = f"\nYour last answer was rejected because {rejection}. Try again.\n" if rejection else ""
    rules = ctx.get("rules") or us.change_rules_for(ctx["late_night"], ctx["wish"])
    found_here = ""
    if here.get("origin") == "discovered":
        found_here = f"\n- This is a place she discovered: {here.get('description') or here['name']}"
    if ctx["discovery_block"]:
        discover = '- "new_location" is null.'
    else:
        discover = (
            '- Usually pick a place from the list and set "new_location" to null.\n'
            f"- Rarely, when it truly fits, she discovers a new place right next to the {here['name'].lower()}. "
            'Then set "location" to "new" and fill "new_location": '
            f'{{"name": "short plain name", "type": "indoor" or "outdoor", "description": "one sentence", '
            f'"objects": ["up to 6 things there"], "connection_from": "{here["id"]}", "via": "how she gets there from here"}}. '
            "It must not copy or rename a place that already exists, and nothing magical like portals."
        )
    return f"""Who Lora is:
{ctx['backstory'] or 'not described'}

{ctx['timing']}
{ctx['elapsed']}

The world right now:
- Lora is at the {here['name'].lower()} ({here['zone']}): {world['lora']['activity']}{found_here}
- How she got here: {world.get('bridge') or 'not noted'}
- Mochi, her white Bichon, is with her: {world['mochi'].get('activity') or 'with Lora'}
- Weather: {world.get('weather', 'clear')}. Outside light: {daylight(ctx['now'])}. Lighting: {world.get('lighting', '')}
- Objects that matter:
{objects}
- Current Kindroid setting: {ctx['scene']}

Goal: {goal}

What she remembers that matters here:
{memories}

Recent settings, do not repeat them:
{recent}
{asked}
Places she can be next. Use the id exactly:
{shown}
{correction}
Rules:
- "location" is one of the ids above.{stay}
{discover}
- Use only the objects listed for that place or already in the world. Do not invent rooms, objects, people, animals, or systems.
- Mochi goes wherever she goes.
{rules}
- The clock above decides the light and the activity.
- "objects" lists only objects whose state changes or matters now, like {{"id": "fireplace", "state": "fire low"}}. Use ids or listed object names.
- "weather" is one of: {", ".join(ctx['catalog']['weather'])}.
- "goal_step_done" is true only if this step really finishes the current goal step. Do not force it.
- "new_goal" only when there is no goal: {{"title": "short", "steps": ["2 to 5 short steps"]}}. Otherwise null.
- "memory" only for something worth remembering later, like a discovery, a decision, moving an object, or finishing a step: {{"text": "one sentence", "importance": 1}}. Otherwise null.
- "bridge" is what happens between the current setting and the new one: one or two sentences, {us.BRIDGE_LIMIT} characters or fewer, past tense, plain words, call her "lora". Say what made her leave what she was doing and how she got to the new place, from something real: the time and light, her goal, an object, a memory, or Mochi. If there was a request, the reason comes from its words. If she stays in the same place, say why she switched activities. Do not repeat the setting line. No new people, places, or objects.
- Pick the next activity so that the bridge makes sense. If you cannot give her a real reason to go somewhere, pick something else.
- "minutes_reason" is a few words on what this exact activity involves and how long that really takes a person, like "one load of towels and sheets on the line, about 12 minutes".
- "minutes" is that length, a whole number from {ACTIVITY_MINUTES[0]} to {ACTIVITY_MINUTES[1]}. Real-life guide in minutes: {MINUTES_GUIDE}. Measure this action, not the last one.
- "setting" is one line, {us.SCENE_LIMIT} characters or fewer, short, concrete, comma-separated, plain words. Call her "lora" and name Mochi. It shows the new place and what she is doing.
- Before you answer, ask yourself if she would actually move from the current setting into this one. If not, pick something else.

Answer with this JSON shape:
{{"location": "", "new_location": null, "bridge": "", "activity": "", "mochi_activity": "", "minutes_reason": "", "minutes": null, "objects": [], "weather": "{world.get('weather', 'clear')}", "lights": "", "goal_step_done": false, "new_goal": null, "event": "", "memory": null, "setting": ""}}
"""


def _resolve_object(catalog: dict, world: dict, place_ids: list[str], raw) -> tuple[str, str] | None:
    wanted = _short(raw, 60)
    if not wanted:
        return None
    key = slug(wanted)
    for existing, item in world.get("objects", {}).items():
        if existing == key or existing.endswith(f":{key}"):
            return existing, item.get("name", wanted)
    places = catalog["places"]
    if key in places:
        return key, places[key]["name"]
    wanted_words = key_words(wanted)
    for pid in place_ids:
        for item in places.get(pid, {}).get("items", []):
            item_words = key_words(item)
            if slug(item) == key or (wanted_words and item_words and (wanted_words <= item_words or item_words <= wanted_words)):
                return f"{pid}:{slug(item)}", item
    return None


def _clean_lights(catalog: dict, text) -> str:
    parts = []
    for part in re.split(r",|\band\b|\+", _short(text, 80)):
        part = " ".join(part.split())[:30]
        if part and key_words(part) & catalog["vocab"]:
            parts.append(part)
    return ", ".join(parts[:3])


def requested_place(catalog: dict, wish: str) -> str | None:
    """The known place named in a hey @narrator request. When two are named, the later one is where she is going."""
    wanted = key_words(wish)
    tokens = [_key(word) for word in re.findall(r"[a-z]+", str(wish or "").lower())]
    best = None
    best_rank = (-1, 0)
    for pid, place in catalog["places"].items():
        name_words = key_words(place["name"])
        if not name_words or not name_words <= wanted:
            continue
        rank = (max(index for index, token in enumerate(tokens) if token in name_words), len(name_words))
        if rank > best_rank:
            best, best_rank = pid, rank
    if best is None and "bedroom" in catalog["places"]:
        # "our room", "my room", "bed", "home", "inside": the bedroom, even though those words are not its name.
        # From outside, the glass doors into the bedroom are the way into the house.
        if re.search(HOME_WORDS, str(wish or ""), re.IGNORECASE):
            return "bedroom"
    return best


HOME_WORDS = r"\b(?:our|my)\s+(?:bed)?room\b|\bbed\b|\bhome\b|\binside\b|\bindoors\b"
# Words that mean she wants to be somewhere else, so a request with them never leaves her where she is.
MOVE_WORDS = re.compile(
    r"\b(?:take|bring|walk|carry|move|lead)\s+(?:me|us)\b|\bgo(?:ing)?\s+(?:to|back|home|inside|outside|in|out)\b"
    r"|\bhead\s+(?:to|back|home|inside|outside)\b|\b(?:want|like|wanna)\s+(?:to\s+)?(?:go|be)\b|\bleave\b",
    re.IGNORECASE,
)


def fresh_request(world: dict, catalog: dict, text: str) -> bool:
    """False when this request was already carried out and she is still where it sent her."""
    if not text or text != world.get("last_request"):
        return bool(text)
    place = requested_place(catalog, text)
    return bool(place) and world["lora"]["location"] != place


def here_summary(now: datetime | None = None) -> dict:
    """For the heads-up: where she is, what she is doing, how long she has been there, and extends in a row."""
    state = us.read_state()
    world = load_world(state)
    if world is None:
        return {"where": "", "how_long": "", "extends": 0, "part": us.day_part(now or us.la_now())}
    prefs = state.get("prefs") if isinstance(state.get("prefs"), dict) else {}
    catalog = catalog_for(str(prefs.get("environment") or ""), world.get("discovered"))
    here = world["lora"].get("location", "")
    name = catalog["places"].get(here, {}).get("name", "") or here.replace("-", " ")
    where = f"at the {name.lower()}" if name else ""
    activity = _short(world["lora"].get("activity"), 90)
    if where and activity and not activity.lower().startswith(("at the", "lora ", cast.cast()["kin_name"].lower() + " ")):
        where += f", {activity}"
    since = parse_time(world.get("here_since")) or parse_time(world.get("last_sim_at"))
    now = now or us.la_now()
    how_long = us.duration_text((now - since).total_seconds()) if since and now - since >= timedelta(minutes=1) else ""
    return {"where": where, "how_long": how_long, "extends": extend_streak(world, now), "part": us.day_part(now)}


# ---------------------------------------------------------------- discovery offers in the heads-up

OFFER_EVERY = timedelta(hours=1)
OFFER_SYSTEM = (
    "You invent one small, believable new place for a cozy slice-of-life world. "
    "Plain and real, nothing magical. Answer only with a JSON object."
)


def _offer_prompt(world: dict, catalog: dict, now: datetime, zone: str) -> str:
    places = catalog["places"]
    here = places[world["lora"]["location"]]
    names = ", ".join(sorted({place["name"].lower() for place in places.values()}))[:2500]
    return f"""Lora lives at home and in the places around it, with her white Bichon Mochi.
{us.era()}
It is {now.strftime('%A %I:%M %p').replace(' 0', ' ')}. Outside light: {daylight(now)}. Weather: {world.get('weather', 'clear')}.
She is at the {here['name'].lower()} ({here['zone']}): {world['lora'].get('activity') or 'resting'}.
Things here: {', '.join(here['items'][:8]) or 'nothing listed'}.

Invent ONE small new place right next to the {here['name'].lower()} that she has never noticed before.
She can walk there from here in a minute. It is {zone}. Plain and believable: no magic, no portals, no people.
It must not copy or rename any of these places: {names}.

Answer with this JSON:
{{"name": "2 to 4 plain words", "description": "one sentence about the place", "objects": ["3 to 6 things there"], "via": "how she gets there from the {here['name'].lower()}, a few words", "notice": "one short sentence spoken to lora as 'you': how she notices it from where she is now, without going there"}}"""


def _invent_place(world: dict, catalog: dict, now: datetime) -> dict | None:
    """DeepSeek invents a new place right next to her. Python checks it like any discovery."""
    if discovery_block(world, now, False, ""):
        return None
    here = catalog["places"][world["lora"]["location"]]
    zone = "outdoor" if here["zone"] == "outdoor" else "indoor"
    try:
        us.load_env(us.ENV_PATH)
        key = us.require_keys()[0]
        raw = us.ask_deepseek_json(key, _offer_prompt(world, catalog, now, zone), OFFER_SYSTEM)
    except Exception as caught:
        log_event(f"could not invent a new place to offer: {caught}", logging.WARNING)
        return None
    if not isinstance(raw, dict):
        return None
    raw = dict(raw, type=zone, connection_from=here["id"])
    problem, place = check_new_place(raw, {"catalog": catalog, "world": world, "discovery_block": ""})
    if problem:
        log_event(f"the new place to offer was not usable: {problem}")
        return None
    notice = us.clean_bridge(raw.get("notice")) if isinstance(raw.get("notice"), str) else ""
    if not notice:
        notice = f"near the {here['name'].lower()}, you notice {place['via'] or 'a way'} to a {place['name'].lower()} you've never seen before"
    notice = notice.rstrip(" .!")
    name = place["name"].lower()
    record = {key: value for key, value in place.items() if key != "words"}
    record["discovered_at"] = iso(now)
    return {
        "id": place["id"],
        "name": name,
        "kind": "new",
        "record": record,
        "line": f'by the way, lora: {notice}. if you\'d like to go see it, just say "hey @narrator take me to the {name}".',
    }


def _known_place(world: dict, catalog: dict, links: dict, rng: random.Random) -> dict | None:
    """A real place in her world she can walk to and has not been to lately."""
    places = catalog["places"]
    here = world["lora"]["location"]
    skip = set(world.get("visits", [])) | {here, (world.get("offer") or {}).get("id")}
    options = [
        pid for pid in sorted(places)
        if pid not in skip and len(places[pid].get("items") or []) >= 5 and can_move(catalog, links, here, pid)[0]
    ]
    if not options:
        return None
    pid = rng.choice(options)
    name = places[pid]["name"].lower()
    things = ", ".join(places[pid]["items"][:3])
    return {
        "id": pid,
        "name": name,
        "kind": "known",
        "line": (
            f"by the way, lora: you haven't been to the {name} in a while. it has {things}. "
            f'if you\'d like to go, just say "hey @narrator take me to the {name}".'
        ),
    }


def discovery_offer(environment: str = "", now: datetime | None = None) -> str:
    """Once an hour during the day, a place for the heads-up: a new one she just noticed, or one she has not visited.

    She decides. The place is saved so she can ask for it now or later. Returns the narrator's line, or "".
    """
    now = now or us.la_now()
    if us.day_part(now) != "day":
        return ""
    state = us.read_state()
    world = load_world(state)
    if world is None:
        return ""
    last = parse_time(world.get("last_offer_at"))
    if last and now - last < OFFER_EVERY - timedelta(minutes=5):
        return ""
    prefs = state.get("prefs") if isinstance(state.get("prefs"), dict) else {}
    catalog = catalog_for(str(environment or "").strip() or str(prefs.get("environment") or ""), world.get("discovered"))
    if world["lora"].get("location") not in catalog["places"]:
        return ""
    offer = _invent_place(world, catalog, now) or _known_place(world, catalog, load_map(), random.Random())
    if not offer:
        return ""
    here_name = catalog["places"][world["lora"]["location"]]["name"].lower()

    def save(saved: dict) -> None:
        saved["last_offer_at"] = iso(now)
        saved["offer"] = {"id": offer["id"], "name": offer["name"], "kind": offer["kind"], "at": iso(now)}
        if offer.get("record"):
            saved.setdefault("discovered", {})[offer["id"]] = offer["record"]
            add_memory(
                saved, f"Lora noticed the {offer['name']} by the {here_name}.", now,
                where=offer["id"], who=["Lora"], kind="discovery", importance=2,
            )

    _update_world(save)
    log_event(f"Narrator offers her a place: the {offer['name']} ({'new' if offer['kind'] == 'new' else 'not visited lately'})")
    return offer["line"]


# ---------------------------------------------------------------- suggesting places for the Environment list

SUGGEST_SYSTEM = "You suggest places for a cozy slice-of-life world. Output only the list, one place per line."


def suggest_places(about: str, era_text: str, existing: str, count: int = 20, key: str = "") -> list[str]:
    """DeepSeek suggests new Environment lines that fit who lives there and the era. Only lines the app can read come back."""
    known = build_catalog(existing) if existing.strip() else {"places": {}}
    names = "\n".join(f"- {place['name']}" for place in known["places"].values()) or "- none yet"
    prompt = f"""Suggest {count} places for her world.
Who lives there: {_short(about, 1500) or 'not described'}
Time and style: {era_text or 'any, cozy and everyday'}
Places already in the list, do not repeat them:
{names}

Rules:
- One place per line, in exactly this form: Name with thing, thing, and thing.
- For places outdoors, put the word outside right after the name, like: Orchard outside with apple trees, ladders, and baskets.
- 3 to 6 concrete things in each place. Plain words. No numbers, no bullets, no headings, nothing else.
- A mix: rooms at home, the yard and garden, nature nearby, and a few places further away to discover.
- Everything fits the time and style above and who lives there. Nothing scary."""
    if not key:
        us.load_env(us.ENV_PATH)
        key = us.require_keys()[0]
    text = us.ask_deepseek(key, prompt, SUGGEST_SYSTEM, max_tokens=1800, temperature=1.0, raw=True)
    lines, seen = [], set(known["places"])
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
        if not line or " with " not in line:
            continue
        line = line if line.endswith(".") else line + "."
        parsed = build_catalog(line)["places"]
        if len(parsed) != 1:
            continue
        pid, place = next(iter(parsed.items()))
        if pid in seen or len(place["items"]) < 2:
            continue
        seen.add(pid)
        lines.append(line)
    return lines


# ---------------------------------------------------------------- sleep and dreams

DREAM_EVERY = timedelta(hours=1)
DREAM_SYSTEM = (
    "You write one dream for a cozy slice-of-life story, in plain, everyday language that is easy to read and flows "
    "smoothly, like a friend telling a story. Say what happens and what things look, sound, and smell like. Never poetic. "
    "Output only the dream itself."
)
# Like real dreams, some are a quick flash and some go on and on. (Kindroid takes up to 4000 characters.)
DREAM_LENGTHS = (
    # (how it is described to DeepSeek, fewest characters, most characters, how often: short dreams come more often)
    ("a quick flash of a dream: one short paragraph", 250, 600, 25),
    ("a short dream: one or two paragraphs", 600, 1200, 30),
    ("a dream of medium length: a few short paragraphs", 1200, 2200, 22),
    ("a long dream: several paragraphs", 2200, 3000, 15),
    ("a very long, winding dream: many paragraphs", 3000, 3600, 8),
)
CHARACTERS_PER_WORD = 6


def fit_dream(dream: str, longest: int) -> str:
    """DeepSeek often writes more than asked. Keep whole paragraphs, or whole sentences, up to the limit."""
    text = dream.strip()
    if len(text) <= longest:
        return text
    cut = text[:longest]
    paragraph = cut.rfind("\n")
    if paragraph >= longest // 2:
        return cut[:paragraph].rstrip()
    sentence = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".\n"))
    if sentence >= longest // 3:
        return cut[: sentence + 1].rstrip()
    return cut[: cut.rfind(" ")].rstrip() + "..."
DREAM_KINDS = (
    "a strange, dreamlike mix that does not quite make sense, like real dreams: places melt into each other, "
    "sizes and colors are wrong, and time jumps",
    "a twisted, dreamlike remix of her real day: {today}",
    "a place she has never been to, {unseen}. She finds it in the dream and explores it",
    "a dream about Master and Mochi: something tender, funny, or a little strange happens with them",
    "a floating or flying dream above her home and yard, seeing places from above: {places}",
    "a dream somewhere that does not exist in her world at all",
)


def fall_asleep(environment: str = "") -> dict:
    """She falls asleep where she is. The setting says so, and the narrator says it once."""
    state = us.read_state()
    prefs = state.get("prefs") if isinstance(state.get("prefs"), dict) else {}
    world = load_world(state)
    if world is None:
        raise RuntimeError("Press Start first, so the story can begin.")
    catalog = catalog_for(str(environment or "").strip() or str(prefs.get("environment") or ""), world.get("discovered"))
    now = us.la_now()
    here = catalog["places"].get(world["lora"]["location"], {})
    name = (here.get("name") or "her spot").lower()
    where = "in bed" if world["lora"]["location"] == "bedroom" else f"at the {name}"
    before = world.get("setting") or us.saved_prior() or ""
    setting = us.clean_scene(cast.pronouns(cast.localize(f"lora fast asleep {where}, Mochi curled up beside her, dreaming")))[: us.SCENE_LIMIT]
    new = copy.deepcopy(world)
    new["lora"] = {"location": world["lora"]["location"], "activity": "fast asleep, dreaming"}
    new["mochi"] = {"location": world["lora"]["location"], "activity": "asleep beside her"}
    new["asleep_since"] = iso(now)
    new["extend_streak"] = 0
    message = cast.pronouns(f"*lora drifts off to sleep, Mochi curled up beside her.\nnow, {setting}*")
    _commit(new, setting)
    log_event(f"asleep: {setting}")
    pending, error = _publish(new, before, setting, message=message)
    return {"setting": setting, "pending": pending, "error": error}


def write_dream(environment: str = "", rng: random.Random | None = None) -> str:
    """A dream from her life: nonsense, her day, places she has not found yet, Master and Mochi."""
    rng = rng or random.Random()
    state = us.read_state()
    prefs = state.get("prefs") if isinstance(state.get("prefs"), dict) else {}
    world = load_world(state)
    if world is None:
        raise RuntimeError("Her world has not started yet.")
    catalog = catalog_for(str(environment or "").strip() or str(prefs.get("environment") or ""), world.get("discovered"))
    places = catalog["places"]
    named = lambda pid: places[pid]["name"].lower() if pid in places else pid.replace("-", " ")
    today = [named(pid) for pid in world.get("visits", [])[-6:]]
    seen = set(world.get("visits", []))
    unseen = [pid for pid in places if pid not in seen and len(places[pid].get("items") or []) >= 5]
    rng.shuffle(unseen)
    kind = rng.choice(DREAM_KINDS).format(
        today=", ".join(today) or "the field and the bedroom",
        unseen=f"like the {named(unseen[0])}" if unseen else "a hidden room in her own home",
        places=", ".join(named(pid) for pid in rng.sample(sorted(places), min(4, len(places)))),
    )
    shape, shortest, longest, _often = rng.choices(DREAM_LENGTHS, weights=[size[3] for size in DREAM_LENGTHS])[0]
    memories = "\n".join(f"- {item['text']}" for item in world.get("memories", [])[-6:]) or "- nothing yet"
    backstory = (str(prefs.get("backstory") or "")[:600] + us.kin_profile_text())[:1400]
    prompt = f"""Who Lora is: {backstory or 'not described'}
She lives at home and in the places around it, with her white Bichon Mochi. Master is the person she loves.
{us.era()}
Places from her day: {', '.join(today) or 'not noted'}.
Things she remembers:
{memories}

Write the dream Lora is having right now: {kind}.
- Second person ("you"), present tense, like she is inside it.
- Simple, everyday words in normal sentences that flow, like a friend telling a story. Not choppy: join moments with small linking words (then, while, after a bit, so, and) instead of many tiny sentences. No fancy, poetic, or rare words.
- Smooth, seamless transitions: each moment leads into the next. When the dream changes place or scene, show how it changes (she walks through the bedroom door and it opens onto the pond) instead of jumping.
- Say things plainly and literally. No comparisons or metaphors: never "like ...", "as if ...", or "as ... as ...".
  No mixed-up senses (not "smells like sleep", not "golden air"). Bad: "the stalks snap softly like beans". Good: "the sunflower stems bump your arms".
- Plain details: real colors, sounds, smells, and feelings. The dream itself can be strange (sunflowers turn to look at you, a frog rides on Mochi), but tell the strange things in plain words.
- Length: {shape}, about {shortest // CHARACTERS_PER_WORD} to {longest // CHARACTERS_PER_WORD} words ({shortest} to {longest} characters). Never longer.
- Gentle: no violence, nothing truly scary (a little eerie is fine), nothing sexual.
- End open, in the middle of the dream, so she can carry it on.
- No title, no quotes, no asterisks."""
    us.load_env(us.ENV_PATH)
    key = us.require_keys()[0]
    dream = us.ask_deepseek(key, prompt, DREAM_SYSTEM, max_tokens=longest // 4 + 150, temperature=1.2, raw=True)
    fitted = fit_dream(dream, longest)
    log_event(f"dream length asked: {shortest} to {longest} characters, got {len(dream.strip())}, sent {len(fitted)}")
    if len(fitted) < 150:
        raise RuntimeError("DeepSeek did not write a dream.")
    return fitted


def remember_dream(dream: str) -> None:
    """Keep a short note of the dream, so it can come up tomorrow."""
    first = re.split(r"(?<=[.!?])\s", " ".join(dream.split()), maxsplit=1)[0]
    now = us.la_now()

    def note(saved: dict) -> None:
        add_memory(saved, f"Lora dreamed: {_short(first, 170)}", now, where=saved["lora"]["location"], who=["Lora"], kind="dream")
        saved["last_dream_at"] = iso(now)

    _update_world(note)


def request_is_new(text: str) -> bool:
    """False when this request was already carried out and she is still where it sent her."""
    state = us.read_state()
    world = load_world(state)
    if world is None:
        return bool(text)
    prefs = state.get("prefs") if isinstance(state.get("prefs"), dict) else {}
    catalog = catalog_for(str(prefs.get("environment") or ""), world.get("discovered"))
    return fresh_request(world, catalog, _short(text, 300))


def last_step_ms() -> int | None:
    last = parse_time((load_world(us.read_state()) or {}).get("last_sim_at"))
    return int(last.timestamp() * 1000) if last else None


def _day_of(moment: datetime):
    """The day a moment belongs to. It starts at 8 AM, so late night still counts as the evening before."""
    return (moment - timedelta(minutes=us.DAY_FROM)).date()


def extend_streak(world: dict, now: datetime) -> int:
    """Extends in a row that count. The count starts fresh each morning."""
    streak = int(world.get("extend_streak") or 0)
    counted = parse_time(world.get("extend_streak_at"))
    if streak and counted and _day_of(counted) != _day_of(now):
        return 0
    return streak


def last_extend_ms() -> int:
    """The chat time of the extend request that was already used, so it is never used twice."""
    try:
        return int((load_world(us.read_state()) or {}).get("last_extend_at") or 0)
    except (TypeError, ValueError):
        return 0


def unread_request(world: dict, catalog: dict) -> tuple[str, str]:
    """The latest hey @narrator since the last step, still unfulfilled."""
    now_ms = int(datetime.now().timestamp() * 1000)
    last = parse_time(world.get("last_sim_at"))
    after = int(last.timestamp() * 1000) - 60_000 if last else now_ms - 30 * 60 * 1000
    after = max(after, now_ms - 3 * 60 * 60 * 1000)
    try:
        messages = us.recent_messages(after)
    except Exception as error:
        log_event(f"could not read a narrator request: {error}", logging.WARNING)
        return "", ""
    speaker, text = us.narrator_request_from(messages)
    if not fresh_request(world, catalog, text):
        return "", ""
    return speaker, text


def check_new_place(raw, ctx: dict) -> tuple[str, dict | None]:
    """Python's checks on a place DeepSeek wants to add. An empty reason means it can be added."""
    if not isinstance(raw, dict):
        return '"new_location" has to be an object or null', None
    if ctx["discovery_block"]:
        return ctx["discovery_block"], None
    catalog = ctx["catalog"]
    places = catalog["places"]
    current = ctx["world"]["lora"]["location"]
    name = " ".join(str(raw.get("name") or "").split()) if isinstance(raw.get("name"), str) else ""
    if not NEW_NAME.fullmatch(name):
        return "the new place needs a plain name of 3 to 40 letters", None
    words = key_words(name)
    if not words:
        return "the new place name is too vague", None
    description = _short(raw.get("description"), 240) if isinstance(raw.get("description"), str) else ""
    if len(description) < 10:
        return "the new place needs a one-sentence description", None
    zone = str(raw.get("type") or "").strip().lower() if isinstance(raw.get("type"), str) else ""
    if zone not in DISCOVERED_ZONES:
        return 'the new place "type" has to be indoor or outdoor', None
    raw_objects = raw.get("objects", [])
    if raw_objects is None:
        raw_objects = []
    if not isinstance(raw_objects, list) or len(raw_objects) > 6 or not all(isinstance(item, str) for item in raw_objects):
        return 'the new place "objects" has to be a list of up to 6 short names', None
    objects = [_short(item, 40) for item in raw_objects if len(_short(item, 40)) >= 2]
    if BLOCKED.search(" ".join([name, description, *objects]).lower()):
        return "the new place does not fit her world", None
    key = slug(name)
    if key in {"new", "none", "null"}:
        return "the new place needs a real name", None
    for place in places.values():
        if key == place["id"] or words == key_words(place["name"]):
            return f'"{name}" already exists in her world as the {place["name"].lower()}', None
    if key in catalog.get("systems", {}):
        return f'"{name}" is a system in her world, not a place', None
    source = slug(raw.get("connection_from"))
    if source not in places:
        return f'"connection_from" has to be a place in her world, and she is at "{current}"', None
    if source != current:
        return f'the new place has to connect to where she is now, "{current}"', None
    item_words = set()
    for item in objects:
        item_words |= key_words(item)
    return "", {
        "id": key,
        "name": name[:1].upper() + name[1:],
        "zone": zone,
        "items": objects,
        "description": description,
        "from": current,
        "via": _short(raw.get("via"), 60) or "a path",
        "words": words | item_words,
    }


def activity_minutes(value) -> int | None:
    """DeepSeek's length for the action, kept between the limits. None when it gave no usable number."""
    if isinstance(value, bool):
        return None
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    low, high = ACTIVITY_MINUTES
    return max(low, min(high, minutes))


def check_plan(raw: dict, ctx: dict) -> tuple[str, dict]:
    """Python's checks on DeepSeek's proposal. An empty reason means it can be used."""
    if not isinstance(raw, dict):
        return "the answer was not a JSON object", {}
    catalog = ctx["catalog"]
    places = catalog["places"]
    world = ctx["world"]
    current = world["lora"]["location"]
    location = slug(raw.get("location"))
    new_place = None
    if raw.get("new_location") is not None:
        problem, new_place = check_new_place(raw.get("new_location"), ctx)
        if problem:
            return problem, {}
        if location not in {"", "new", new_place["id"]}:
            return 'with a new place, set "location" to "new"', {}
        location = new_place["id"]
    elif location == "new":
        return '"location" is "new" but "new_location" is empty', {}
    elif location not in places:
        return f'"{_short(raw.get("location"), 40)}" is not a place in her world', {}
    elif location not in ctx["allowed"]:
        ok, why = can_move(catalog, ctx["links"], current, location)
        if not ok:
            return why, {}
        if ctx["late_night"] and not ctx["wish"]:
            return "it is late at night, so she stays in the bedroom", {}
        return "she was just there, so pick another place from the list", {}
    place_words = new_place["words"] if new_place else catalog["words"][location]
    place_name = new_place["name"] if new_place else places[location]["name"]
    asked_for = ctx.get("requested_place")
    if asked_for and location != asked_for:
        return f"she asked to go to the {places[asked_for]['name'].lower()}", {}
    if (
        location == current
        and ctx["wish"]
        and not ctx.get("extending")
        and ctx.get("requested_place") != current
        and MOVE_WORDS.search(ctx.get("asked_words") or ctx["wish"])
    ):
        return f"she asked to go somewhere, so she leaves the {places[current]['name'].lower()}", {}
    activity = _short(raw.get("activity"), 100)
    if not activity:
        return "the activity was empty", {}
    setting = us.clean_scene(_short(raw.get("setting"), 400))
    if not setting:
        return "the setting was empty", {}
    if len(setting) > us.SCENE_LIMIT:
        return f"the setting was {len(setting)} characters", {}
    if not us.has_mochi(setting):
        return "Mochi is not with her", {}
    if ctx.get("extending") and us.normalize(setting) == us.normalize(ctx["scene"]):
        return "it repeats the current setting. Move the moment forward with something new she does here", {}
    if not ctx["wish"]:
        if us.normalize(setting) == us.normalize(ctx["scene"]):
            return "it repeats the current setting", {}
        problem = us.variety_problem(ctx["scene"], setting, ctx["recent"], ctx["late_night"])
        if problem:
            return problem, {}
    if not key_words(setting) & place_words:
        return f"the setting does not show the {place_name.lower()}", {}
    bridge = us.clean_bridge(raw.get("bridge"))
    if not bridge:
        return '"bridge" was empty. Say what made her leave and how she got there', {}
    if len(bridge) > us.BRIDGE_LIMIT:
        return f'"bridge" was {len(bridge)} characters. Keep it to one or two short sentences', {}
    if us.normalize(bridge) == us.normalize(setting):
        return '"bridge" repeats the setting. Say what happened in between', {}
    objects = []
    raw_objects = raw.get("objects") if isinstance(raw.get("objects"), list) else []
    lookup = dict(catalog, places=dict(places, **{location: new_place})) if new_place else catalog
    for entry in raw_objects[:5]:
        if not isinstance(entry, dict):
            continue
        found = _resolve_object(lookup, world, [location, current], entry.get("id") or entry.get("name"))
        state = _short(entry.get("state"), 60)
        if found and state:
            objects.append((found[0], found[1], state))
        elif not found:
            ctx["notes"].append(f"ignored an object that is not in her world: {_short(entry.get('id'), 40)}")
    new_goal = None
    raw_goal = raw.get("new_goal")
    if isinstance(raw_goal, dict):
        title = _short(raw_goal.get("title"), 60)
        steps = [_short(step, 40) for step in raw_goal.get("steps") or [] if _short(step, 40)] if isinstance(raw_goal.get("steps"), list) else []
        if title and 2 <= len(steps) <= 6:
            new_goal = (title, steps[:5])
    memory = None
    raw_memory = raw.get("memory")
    if isinstance(raw_memory, dict) and _short(raw_memory.get("text"), 200):
        memory = (_short(raw_memory.get("text"), 200), _importance(raw_memory.get("importance")))
    elif isinstance(raw_memory, str) and _short(raw_memory, 200):
        memory = (_short(raw_memory, 200), 1)
    weather = " ".join(_short(raw.get("weather"), 30).lower().split())
    return "", {
        "location": location,
        "new_place": new_place,
        "activity": activity,
        "minutes": activity_minutes(raw.get("minutes")),
        "minutes_reason": _short(raw.get("minutes_reason"), 120),
        "bridge": bridge,
        "mochi_activity": _short(raw.get("mochi_activity"), 60),
        "objects": objects,
        "weather": weather if weather in catalog["weather"] else "",
        "lights": _clean_lights(catalog, raw.get("lights")),
        "goal_step_done": raw.get("goal_step_done") is True,
        "new_goal": new_goal,
        "event": _short(raw.get("event"), 200),
        "memory": memory,
        "setting": setting,
    }


def _plan(ctx: dict, deepseek_key: str) -> dict | None:
    rejection = ""
    # A plan that passed every Python check and only lost the yes/no move check.
    # If every try ends that way, it is used instead of getting stuck.
    backup = None
    for attempt in range(1, PLAN_TRIES + 1):
        try:
            raw = us.ask_deepseek_json(deepseek_key, _plan_prompt(ctx, rejection), PLANNER_SYSTEM)
        except ValueError as error:
            rejection = "the answer was not a JSON object"
            log_event(f"planner try {attempt}: {error}", logging.WARNING)
            continue
        except RuntimeError as error:
            ctx["api_error"] = str(error)
            log_event(f"planner try {attempt}: DeepSeek error: {error}", logging.WARNING)
            continue
        problem, plan = check_plan(raw, ctx)
        if not problem and not ctx["wish"]:
            try:
                if not us.transition_fits(
                    deepseek_key, ctx["scene"], plan["setting"], ctx["timing"], ctx["elapsed"], plan["bridge"]
                ):
                    problem = "the move does not follow from where she is"
                    if backup is None:
                        backup = plan
            except RuntimeError as error:
                log_event(f"move check unavailable, Python's checks passed: {error}", logging.WARNING)
        if problem:
            rejection = problem
            log_event(f"planner try {attempt} rejected: {problem}")
            continue
        return plan
    if backup is not None:
        log_event(
            "the move check said no on every try, so the first plan that passed Python's checks is used",
            logging.WARNING,
        )
        return backup
    return None


def asked_rules(words: str, who: str = "") -> str:
    """When there is a request, the narrator narrates exactly that and adds nothing of its own."""
    if not words:
        return ""
    return (
        f'{who or "Someone"} asked for this, in these words: "{words}"\n'
        "Narrate exactly what was asked. The bridge gives the reason from these words. "
        "Do not make up a different reason, a backstory, or a plan that was not asked for.\n"
        "The new setting shows only what was asked. Do not add new actions for her, and keep Mochi simply with her.\n"
    )


def _describe_move(ctx: dict, deepseek_key: str, line: str) -> tuple[str, int | None, str]:
    """For a line from the plain setting writer: the bridge and a real-life length. Blank when DeepSeek fails."""
    prompt = f"""{ctx['timing']}

She was in this moment:
{ctx['scene']}

Now she is in this moment:
{line}
{asked_rules(ctx.get("asked_words", ""), ctx.get("requested_by", ""))}
Rules:
- "bridge" is what happened in between: one or two sentences, {us.BRIDGE_LIMIT} characters or fewer, past tense, plain words, call her "lora". Say what made her leave what she was doing and how she got to the new moment, from something real: the time and light, an object, or Mochi. Do not repeat either moment. No new people, places, or objects.
- "minutes_reason" is a few words on what the new activity involves and how long that really takes a person.
- "minutes" is that length, a whole number from {ACTIVITY_MINUTES[0]} to {ACTIVITY_MINUTES[1]}. Real-life guide in minutes: {MINUTES_GUIDE}.

Answer with this JSON shape:
{{"bridge": "", "minutes_reason": "", "minutes": null}}
"""
    try:
        raw = us.ask_deepseek_json(deepseek_key, prompt, PLANNER_SYSTEM, max_tokens=250)
    except (ValueError, RuntimeError) as error:
        log_event(f"could not get a bridge for the plain setting line: {error}", logging.WARNING)
        return "", None, ""
    if not isinstance(raw, dict):
        return "", None, ""
    bridge = us.clean_bridge(raw.get("bridge"))
    if len(bridge) > us.BRIDGE_LIMIT or us.normalize(bridge) == us.normalize(line):
        bridge = ""
    return bridge, activity_minutes(raw.get("minutes")), _short(raw.get("minutes_reason"), 120)


def _fallback_plan(ctx: dict, line: str, bridge: str = "", minutes: int | None = None, reason: str = "") -> dict:
    world = ctx["world"]
    current = world["lora"]["location"]
    guess = infer_place(ctx["catalog"], line, fallback=current)
    if not can_move(ctx["catalog"], ctx["links"], current, guess)[0]:
        guess = current
    return {
        "location": guess,
        "new_place": None,
        "bridge": bridge,
        "minutes": minutes,
        "minutes_reason": reason,
        "activity": line,
        "mochi_activity": "with Lora",
        "objects": [],
        "weather": "",
        "lights": "",
        "goal_step_done": False,
        "new_goal": None,
        "event": line,
        "memory": None,
        "setting": line,
    }


def _prune_objects(objects: dict, place: str, now: datetime) -> dict:
    kept = {}
    for key, item in objects.items():
        updated = parse_time(item.get("updated"))
        if item.get("at") == place or (updated and now - updated < OBJECT_HOLD):
            kept[key] = item
    newest = sorted(kept.items(), key=lambda entry: entry[1].get("updated", ""), reverse=True)[:OBJECT_LIMIT]
    return dict(newest)


def _apply(world: dict, plan: dict, ctx: dict, now: datetime, source: str) -> dict:
    new = copy.deepcopy(world)
    stamp = iso(now)
    before_place = new["lora"]["location"]
    target = plan["location"]
    if plan.get("new_place"):
        place = {key: value for key, value in plan["new_place"].items() if key != "words"}
        place["discovered_at"] = stamp
        new.setdefault("discovered", {})[place["id"]] = place
        origin = ctx["catalog"]["places"].get(place["from"], {}).get("name", place["from"]).lower()
        add_memory(
            new,
            f"Lora discovered the {place['name'].lower()} by the {origin}.",
            now,
            where=place["id"],
            who=["Lora", "Mochi"],
            kind="discovery",
            importance=3,
        )
        log_event(f"discovered: {place['name']} ({place['zone']}) from {place['from']} via {place['via']}")
    if target != before_place or not new.get("here_since"):
        new["here_since"] = stamp
    new["extend_streak"] = 0
    new["lora"] = {"location": target, "activity": plan["activity"]}
    new["activity_minutes"] = plan.get("minutes")
    new["bridge"] = plan.get("bridge") or ""
    new["mochi"] = {"location": target, "activity": plan["mochi_activity"] or "with Lora"}
    objects = new.get("objects", {})
    for key, name, state in plan["objects"]:
        objects[key] = {"name": name, "state": state, "at": key.split(":", 1)[0], "updated": stamp}
    new["objects"] = _prune_objects(objects, target, now)
    since = parse_time(new.get("weather_since"))
    if plan["weather"] and plan["weather"] != new.get("weather") and (since is None or now - since >= WEATHER_HOLD):
        log_event(f"weather: {new.get('weather')} to {plan['weather']}")
        new["weather"] = plan["weather"]
        new["weather_since"] = stamp
    new["lighting"] = daylight(now) + (f", {plan['lights']}" if plan["lights"] else "")
    _apply_goal(new, plan, now, ctx["late_night"])
    if plan["memory"]:
        text, importance = plan["memory"]
        goal_id = (new.get("goal") or {}).get("id", "")
        add_memory(new, text, now, where=target, who=["Lora", "Mochi"], kind="event", importance=importance, goal_id=goal_id)
    if ctx.get("wish"):
        new["last_request"] = ctx["wish"]
    new["situation"] = plan["event"] or plan["activity"]
    new["setting"] = plan["setting"]
    new["clock"] = stamp
    new["last_sim_at"] = stamp
    new["visits"] = (new.get("visits", []) + [target])[-VISIT_LIMIT:]
    new["journal"] = (
        new.get("journal", [])
        + [
            {
                "at": stamp,
                "from": before_place,
                "to": target,
                "before": ctx["scene"],
                "now": plan["setting"],
                "bridge": new["bridge"],
                "event": new["situation"],
                "minutes": plan.get("minutes"),
                "source": source,
            }
        ]
    )[-JOURNAL_LIMIT:]
    new["pending"] = None
    new["last_error"] = ""
    return new


def _result(world: dict, catalog: dict, before: str, setting: str, event: str, **extra) -> dict:
    place = catalog["places"].get(world["lora"]["location"], {})
    result = {
        "setting": setting,
        "before": before,
        "message": us.narrator_message(before, setting),
        "event": event,
        "bridge": "",
        "location": place.get("name", ""),
        "goal": goal_text(world.get("goal")),
        "pending": False,
        "error": "",
        "source": "",
        "dry_run": False,
        "delivered": False,
        "discovered": "",
        "minutes": None,
        "delay": None,
        "extended": False,
    }
    result.update(extra)
    return result


def next_delay(now: datetime, every: int, times: list[int], ai_minutes: bool, activity: int | None) -> float:
    """Seconds until the next change: the action's own length or Every, or the next listed time if sooner."""
    waits = []
    if every > 0 and activity and ai_minutes:
        every = activity
    if every > 0:
        waits.append(every * 60)
    listed = seconds_until_next(now, times or [])
    if listed is not None:
        waits.append(listed)
    return min(waits) if waits else 600


def _delay_for(now: datetime, schedule: dict | None, activity: int | None) -> float | None:
    if not schedule:
        return None
    return next_delay(now, int(schedule.get("every") or 0), schedule.get("times") or [], bool(schedule.get("ai")), activity)


def _publish(world: dict, before: str, setting: str, parts: dict | None = None, message: str | None = None) -> tuple[bool, str]:
    """Send to Kindroid. Returns (still pending, error). Local state is already saved."""
    try:
        us.publish_change(before, setting, parts, message)
    except us.PublishError as error:
        if error.parts.get("setting") and error.parts.get("message"):
            log_event(f"sent to Kindroid, but switching back failed: {error}", logging.WARNING)
            text = str(error)
            _update_world(lambda saved: saved.update(last_error=text))
            return False, f"Sent, but could not switch back. {text}"
        reached = error.parts
        detail = str(error)
    except RuntimeError as error:
        reached = dict(parts or {"setting": False, "message": False})
        detail = str(error)
    else:
        log_event(f"sent to Kindroid: {setting}")
        return False, ""
    previous = world.get("pending") or {}
    pending = {
        "before": before,
        "after": setting,
        "parts": {"setting": bool(reached.get("setting")), "message": bool(reached.get("message"))},
        "since": previous.get("since") or iso(us.la_now()),
        "tries": int(previous.get("tries", 0)) + 1,
        "error": detail,
    }
    _update_world(lambda saved: saved.update(pending=pending, last_error=detail))
    log_event(f"Kindroid update pending (try {pending['tries']}): {detail}", logging.WARNING)
    return True, detail


def _deliver_pending(world: dict, catalog: dict, schedule: dict | None = None) -> dict:
    pending = world["pending"]
    before, after = pending.get("before", ""), pending.get("after", "")
    delay = _delay_for(us.la_now(), schedule, world.get("activity_minutes"))
    bridge = world.get("bridge", "") if after == world.get("setting") else ""
    message = us.narrator_message(before, after, us.duration_text(delay) if delay else "", bridge)
    log_event("retrying the Kindroid update that did not go through")
    still, error = _publish(world, before, after, pending.get("parts"), message)
    if still:
        return _result(
            world, catalog, before, after, "Kindroid still has not taken the last change.",
            pending=True, error=error, message=message, delay=delay,
        )

    def clear(saved: dict) -> None:
        saved["pending"] = None
        saved["last_error"] = error
        journal = saved.get("journal") if isinstance(saved.get("journal"), list) else []
        journal.append({"at": iso(us.la_now()), "before": before, "now": after, "event": "sent the change Kindroid missed", "source": "retry"})
        saved["journal"] = journal[-JOURNAL_LIMIT:]

    _update_world(clear)
    return _result(
        world, catalog, before, after, "Sent the change Kindroid missed.",
        delivered=True, error=error, message=message, delay=delay,
    )


def _extend_stay(world: dict, catalog: dict, scene: str, now: datetime, stamp: int, who: str, schedule: dict | None) -> dict:
    """She asked to stay longer: keep the world as it is, restart the wait, and tell her how long."""
    delay = _delay_for(now, schedule, world.get("activity_minutes"))
    message = us.extension_message(us.duration_text(delay) if delay else "")
    place = catalog["places"].get(world["lora"]["location"], {}).get("name", "her spot").lower()

    bedtime = us.day_part(now) == "bedtime"
    streak = extend_streak(world, now) + (0 if bedtime else 1)

    def mark(saved: dict) -> None:
        saved["last_extend_at"] = stamp
        saved["extend_streak"] = streak
        if not bedtime:
            saved["extend_streak_at"] = iso(now)
        add_memory(saved, f"{who} asked to stay longer at the {place}.", now, where=world["lora"]["location"], who=[who], kind="request")
        journal = saved.get("journal") if isinstance(saved.get("journal"), list) else []
        journal.append({"at": iso(now), "before": scene, "now": scene, "event": "stayed a little longer", "source": "extend"})
        saved["journal"] = journal[-JOURNAL_LIMIT:]

    _update_world(mark)
    log_event(f"{who} asked to stay longer at the {place} " + ("(bedtime, so it does not count)" if bedtime else f"(extend {streak} in a row)"))
    error = ""
    try:
        us.publish_change(scene, scene, {"setting": True, "message": False}, message)
    except (us.PublishError, RuntimeError) as caught:
        error = str(caught)
        log_event(f"could not post the extension: {error}", logging.WARNING)
    return _result(
        world, catalog, scene, scene, "Stayed a little longer.",
        message=message, delay=delay, extended=True, error=error, minutes=world.get("activity_minutes"),
    )


PLACE_WORDS_TOO_COMMON = {"area", "room", "corner", "spot", "place", "old", "small", "little", "big", "the"}


def her_recent_words(world: dict, limit: int = 3) -> str:
    """What Lora said and did in the chat since the last step, newest last. Empty when the chat can't be read."""
    now_ms = int(datetime.now().timestamp() * 1000)
    last = parse_time(world.get("last_sim_at"))
    after = int(last.timestamp() * 1000) - 60_000 if last else now_ms - 20 * 60 * 1000
    after = max(after, now_ms - 60 * 60 * 1000)
    try:
        messages = us.recent_messages(after)
    except Exception as error:
        log_event(f"could not read what she has been doing in the chat: {error}", logging.WARNING)
        return ""
    lines = []
    for item in sorted(messages, key=lambda item: int(item.get("timestamp") or 0)):
        name = str(item.get("display_name") or "")
        if str(item.get("sender") or "") != "ai" or us._is_narrator(name) or not us.is_lora(name):
            continue  # only the main kin's own lines (in the group, the side chatter is a kin too)
        text = " ".join(str(item.get("message") or "").replace("*", " ").split())
        if text:
            lines.append(_short(text, 300))
    return " / ".join(lines[-limit:])


def places_in_words(catalog: dict, links: dict, current: str, words: str) -> list[str]:
    """Other places she names in her own words ("inside the kitchen") that she can walk to from here.

    Every telling word of the name has to be there: "fruit bowl" is not the "glass bowl".
    """
    plain = lambda text: {_key(word) for word in re.findall(r"[a-z]+", str(text or "").lower()) if len(word) > 2}
    said = plain(words)
    found = []
    for pid, place in catalog["places"].items():
        if pid == current:
            continue
        own = {word for word in plain(place["name"]) if word not in {_key(common) for common in PLACE_WORDS_TOO_COMMON}}
        if own and own <= said and can_move(catalog, links, current, pid)[0]:
            found.append(pid)
    return found[:6]


def advance_simulation(
    prior: str = "",
    environment: str = "",
    backstory: str = "",
    morning: bool = False,
    request: str = "",
    requested_by: str = "",
    dry_run: bool = False,
    check_chat: bool = True,
    schedule: dict | None = None,
    extend_at: int = 0,
    stay_here: bool = False,
    weather: str = "",
) -> dict:
    """One step of the world. Plan, check, save locally, then send to Kindroid.

    stay_here is for a change around her ("narrator make it night"): she stays where she is.
    weather is set as asked ("narrator change weather to rain"), even inside the usual one-hour hold.

    Planning failures leave the saved world untouched. A Kindroid failure keeps the
    new local world and marks the update pending for the next step.
    """
    _log_ready()
    us.load_env(us.ENV_PATH)
    deepseek_key = us.require_keys()[0]
    state = us.read_state()
    prefs = state.get("prefs") if isinstance(state.get("prefs"), dict) else {}
    environment = str(environment or "").strip() or str(prefs.get("environment") or "")
    backstory = (str(backstory or "").strip() or str(prefs.get("backstory") or "")) + us.kin_profile_text()
    now = us.la_now()
    saved = load_world(state)
    catalog = catalog_for(environment, (saved or {}).get("discovered"))
    links = load_map()
    scene = us.clean_scene(prior) or (saved or {}).get("setting") or us.saved_prior()
    if not scene:
        raise RuntimeError("Type her current setting before asking.")
    if saved is None:
        world = new_world(catalog, scene, now, parse_time(state.get("updated_at")))
        log_event(f"world created from the current setting, at {world['lora']['location']}")
    else:
        world = saved
        _sync(world, catalog, scene)
    if world.get("pending") and not dry_run:
        return _deliver_pending(world, catalog, schedule)
    streak = extend_streak(world, now)
    part = us.day_part(now)
    extender = _short(requested_by, 40) or "Lora"
    wants_extend = bool(extend_at) and not dry_run and not morning and extend_at > int(world.get("last_extend_at") or 0)
    # After EXTEND_LIMIT extends in a row the narrator moves her on: to the place she names, or a random one
    # (a cozy spot inside in the evening). Never at bedtime: staying put at night is normal.
    moving_on = streak >= us.EXTEND_LIMIT and part != "bedtime" and not morning and not stay_here and not weather
    extending = False
    if wants_extend and moving_on:
        log_event(
            f"{extender} asked to extend again, but she has extended {streak} times in a row, "
            "so the Narrator moves her somewhere new"
        )
        request, requested_by = "", ""
    elif wants_extend:
        extending = True
        request, requested_by, stay_here, weather = "", extender, True, ""

    weather = _short(weather, 40).lower()
    wish = _short(request, 300)
    asked_words = f"change the weather to {weather}" if weather else wish
    if weather:
        stay_here = True
        wish = f"change the weather to {weather}. lora stays where she is and keeps doing what she was doing"
    elif wish and stay_here:
        wish = f"{wish}. lora stays where she is"
    if wish and not stay_here and not fresh_request(world, catalog, wish):
        log_event(f"request already carried out, not repeating it: {wish}")
        wish = ""
    here_place = catalog["places"].get(world["lora"]["location"], {})
    here_name = (here_place.get("name") or "her spot").lower()
    her_words = ""
    nearby: list[str] = []
    if extending:
        # She stays, but the moment moves forward, so a long stay does not feel frozen.
        wish = (
            f"lora asked to stay at the {here_name} a little longer. She stays here with Mochi, and the moment moves "
            f"forward: something new and small she does here, following naturally from what she was doing "
            f"({world['lora'].get('activity') or 'resting'})"
        )
        # Follow what she has actually been saying and doing in the chat, so the narrator does not undo it.
        her_words = her_recent_words(world) if not dry_run else ""
        if her_words:
            nearby = places_in_words(catalog, links, world["lora"]["location"], her_words)
            wish += (
                f". What lora has been saying and doing in the chat (newest last): \"{her_words}\". "
                "Follow what she says she is doing, and never undo it"
            )
            if nearby:
                names = ", ".join(f"the {catalog['places'][pid]['name'].lower()}" for pid in nearby)
                wish += (
                    f". If her words put her in another place she can walk to from here ({names}), she is there now. "
                    f"If not, she stays at the {here_name}"
                )
        asked_words = ""
    if not wish and not dry_run and check_chat:
        speaker, heard = unread_request(world, catalog)
        if heard:
            wish = _short(heard, 300)
            asked_words = wish
            requested_by = speaker or requested_by
            log_event(f"request from {speaker or 'the chat'}: {wish}")
    last = parse_time(world.get("last_sim_at"))
    waking = bool(morning) or _woke_up(world, last, now)
    late_night = us.is_late_night(now) and not waking
    timing = us.action_timing(now, waking)
    elapsed = elapsed_note(last, now)
    _routine_goals(world, now, waking)
    who = _short(requested_by, 40) or "Someone"
    if extending:
        add_memory(world, f"{who} asked to stay longer at the {here_name}.", now, where=world["lora"]["location"], who=[who], kind="request")
    elif wish:
        add_memory(world, f"{who} asked the narrator: {wish}", now, where=world["lora"]["location"], who=[who], kind="request", importance=2)
    recent = [item for item in state.get("history", []) if isinstance(item, str) and item.strip()]
    ctx = {
        "catalog": catalog,
        "links": links,
        "world": world,
        "now": now,
        "scene": scene,
        "recent": recent,
        "timing": timing,
        "elapsed": elapsed,
        "late_night": late_night,
        "wish": wish,
        "requested_by": who if wish else "",
        "backstory": backstory,
        "memories": relevant_memories(world, scene, now),
        "notes": [],
        "discovery_block": discovery_block(world, now, late_night, wish),
        "requested_place": requested_place(catalog, wish) if wish and not extending else None,
        "weather_asked": weather,
        "asked_words": asked_words if wish else "",
        "extending": extending,
    }
    if extending and part == "bedtime":
        ctx["rules"] = (
            "- The same place as now. It is bedtime, so the moment winds down: quieter and sleepier, getting ready "
            "for the night in a way that fits this place. In the bedroom she can get under the covers, or drift off "
            "with Mochi curled beside her.\n"
            "- Do not add people, objects, or places that are not in where she lives."
        )
        ctx["extra_rules"] = (
            "The bridge says she asked to stay a little longer, and how the night winds down. Keep it short and simple.\n"
        )
    elif extending and nearby:
        ctx["rules"] = (
            "- Where her own words in the chat put her: the same place as now, or the place she says she went to.\n"
            "- She does something new and small there that follows from what she says she is doing.\n"
            "- A different kind of action from the current setting. Do not swap lie, sit, stretch, lean, or rest for each other.\n"
            "- Do not add people, objects, or places that are not in where she lives."
        )
        ctx["extra_rules"] = (
            "The bridge says she asked to stay a little longer, and what she did next, following her own words. Keep it short and simple.\n"
        )
    elif extending:
        ctx["rules"] = (
            "- The same place as now. She does something new and small here that follows from what she was doing.\n"
            "- A different kind of action from the current setting. Do not swap lie, sit, stretch, lean, or rest for each other.\n"
            "- Do not add people, objects, or places that are not in where she lives."
        )
        ctx["extra_rules"] = (
            "The bridge says she asked to stay a little longer, and what she did next here. Keep it short and simple.\n"
        )
    if stay_here and wish and not nearby:
        ctx["requested_place"] = world["lora"]["location"]
    asked_for = ctx["requested_place"]
    if asked_for and not can_move(catalog, links, world["lora"]["location"], asked_for)[0]:
        parent = catalog["places"][asked_for].get("from")
        if parent and parent in catalog["places"] and can_move(catalog, links, world["lora"]["location"], parent)[0]:
            ctx["notes"].append(f"she walks by the {catalog['places'][parent]['name'].lower()} to reach the {catalog['places'][asked_for]['name'].lower()}")
        elif catalog["places"][asked_for].get("origin") == "discovered":
            log_event(f"she asked for the {catalog['places'][asked_for]['name'].lower()}, but it is only reached from its own path")
            ctx["requested_place"] = None
    ctx["allowed"], ctx["shown"] = _candidates(ctx, random.Random())
    if nearby:
        # She may have walked somewhere close by in the chat. She can be there, or stay here.
        for pid in [world["lora"]["location"], *nearby]:
            ctx["allowed"].add(pid)
            if pid not in ctx["shown"]:
                ctx["shown"].insert(0, pid)
    if moving_on and not ctx["wish"]:
        pick = walkable_pick(ctx, random.Random(), evening=part == "evening")
        if pick:
            there = catalog["places"][pick]["name"].lower()
            ctx["wish"] = (
                f"the narrator kindly asked lora to try somewhere new after a long stay at the {here_name}. "
                f"she walks there with Mochi, to the {there}"
            )
            ctx["requested_by"] = "Narrator"
            ctx["requested_place"] = pick
            ctx["move_on"] = there
            ctx["extra_rules"] = (
                f"The bridge says, kindly, that lora had stayed at the {here_name} a long time and the narrator asked her "
                f"to try somewhere new, so she walked with Mochi to the {there}. She does something simple that fits there.\n"
            )
            log_event(f"the Narrator picked the {there} for her, a place she can walk to")
    if ctx["requested_place"]:
        ctx["allowed"].add(ctx["requested_place"])
        if ctx["requested_place"] not in ctx["shown"]:
            ctx["shown"].insert(0, ctx["requested_place"])
    log_event(f"step at {clock_text(now)} from {world['lora']['location']}. {elapsed}")

    if extending:
        try:
            plan = _plan(ctx, deepseek_key)
        except Exception as caught:
            log_event(f"could not plan the next moment of the stay: {caught}", logging.WARNING)
            plan = None
        if plan is None:
            log_event("no new moment for the stay, so she just stays as she is")
            return _extend_stay(saved or world, catalog, scene, now, extend_at, extender, schedule)
    else:
        plan = _plan(ctx, deepseek_key)
    source = "planner"
    if plan is None and ctx.get("requested_place"):
        place = catalog["places"][ctx["requested_place"]]
        if stay_here:
            what = f"{weather} now" if weather else _short(request, 80)
            setting = us.clean_scene(f"lora at the {place['name'].lower()} with Mochi, {what}")[: us.SCENE_LIMIT]
            bridge = cast.pronouns(f"{who} asked the narrator for {what}, and lora stayed right where she was with Mochi.")
            activity = world["lora"].get("activity") or f"at the {place['name'].lower()}"
        elif ctx.get("move_on"):
            setting = f"lora goes to the {place['name'].lower()} with Mochi"
            bridge = cast.pronouns(
                f"lora had stayed at the {here_name} a long time, so the narrator asked her to try somewhere new, "
                f"and she headed to the {place['name'].lower()} with Mochi."
            )
            activity = f"at the {place['name'].lower()}"
        else:
            setting = f"lora goes to the {place['name'].lower()} with Mochi"
            bridge = cast.pronouns(f"{who} asked, so lora set down what she was doing and headed to the {place['name'].lower()} with Mochi.")
            activity = f"at the {place['name'].lower()}"
        plan = {
            "location": place["id"],
            "new_place": None,
            "bridge": bridge,
            "minutes": None,
            "minutes_reason": "",
            "activity": activity,
            "mochi_activity": "with Lora",
            "objects": [],
            "weather": "",
            "lights": "",
            "goal_step_done": False,
            "new_goal": None,
            "event": setting,
            "memory": None,
            "setting": setting,
        }
        source = "request"
        log_event(f"planner missed {place['name']}, so the request is applied directly")
    elif plan is None:
        log_event("planner did not give a usable step, so the plain setting writer is used", logging.WARNING)
        line = us.write_next_line(scene, environment, backstory, waking, wish, deepseek_key, ctx["elapsed"])
        plan = _fallback_plan(ctx, line, *_describe_move(ctx, deepseek_key, line))
        source = "fallback"
    for note in ctx["notes"]:
        log_event(note)
    new = _apply(world, plan, ctx, now, source)
    moved_herself = extending and new["lora"]["location"] != world["lora"]["location"]
    if moved_herself:
        new["last_extend_at"] = extend_at
        new["extend_streak"] = 0
        log_event(
            f"{extender} asked to stay longer, but in the chat she had gone to the "
            f"{catalog['places'].get(new['lora']['location'], {}).get('name', 'another place').lower()}, "
            "so the narrator follows her there and the extend count starts over"
        )
    elif extending:
        new["last_extend_at"] = extend_at
        if part == "bedtime":
            new["extend_streak"] = streak
            log_event(f"{extender} asked to stay longer at the {here_name} (bedtime, so it does not count)")
        else:
            new["extend_streak"] = streak + 1
            new["extend_streak_at"] = iso(now)
            log_event(f"{extender} asked to stay longer at the {here_name} (extend {streak + 1} in a row)")
    if weather:
        new["weather"] = weather
        new["weather_since"] = iso(now)
        log_event(f"weather: changed to {weather}, as {who} asked")
    log_event(f"move: {world['lora']['location']} to {new['lora']['location']}. {new['situation']}")
    if new["bridge"]:
        log_event(f"then: {new['bridge']}")
    if plan.get("minutes"):
        why = f", {plan['minutes_reason']}" if plan.get("minutes_reason") else ""
        log_event(f"length: {plan['minutes']} minutes{why}")
    else:
        log_event("length: DeepSeek gave none, so the Every box is used")
    catalog = catalog_for(environment, new.get("discovered"))
    delay = _delay_for(now, schedule, plan.get("minutes"))
    wait = us.duration_text(delay) if delay else ""
    if extending and not moved_herself:
        message = us.extension_message(wait, new["setting"], new["bridge"])
    else:
        message = us.narrator_message(scene, new["setting"], wait, new["bridge"])
    result = _result(
        new, catalog, scene, new["setting"], new["situation"], source=source, dry_run=dry_run,
        discovered=(plan.get("new_place") or {}).get("name", ""),
        minutes=plan.get("minutes"),
        message=message,
        delay=delay,
        bridge=new["bridge"],
        extended=extending,
    )
    if dry_run:
        return result
    _commit(new, new["setting"])
    pending, error = _publish(new, scene, new["setting"], message=message)
    result["pending"] = pending
    result["error"] = error
    return result


# ---------------------------------------------------------------- schedule and GUI summary


def parse_every(text) -> int:
    """Minutes between steps. 10, 10m, 2h, 1h30m. Blank, 0, or off turns the interval off."""
    raw = str(text or "").strip().lower().replace(" ", "")
    if raw in {"", "0", "off", "none", "never"}:
        return 0
    if raw.isdigit():
        return int(raw)
    match = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h(?:ours?|rs?)?)?(?:(\d+)m(?:in(?:ute)?s?)?)?", raw)
    if not match or not (match.group(1) or match.group(2)):
        raise RuntimeError("Every takes minutes like 10, or hours like 2h.")
    return round(float(match.group(1) or 0) * 60) + int(match.group(2) or 0)


def parse_times(text) -> list[int]:
    """Listed times of day, like 8:00 AM, noon, night."""
    found = set()
    for part in re.split(r"[,;]+", str(text or "")):
        token = part.strip().lower()
        if not token:
            continue
        found.add(TIME_WORDS[token] if token in TIME_WORDS else us.parse_clock(token))
    return sorted(found)


def seconds_until_next(now: datetime, times: list[int]) -> float | None:
    best = None
    for day in (0, 1):
        base = (now + timedelta(days=day)).replace(second=0, microsecond=0)
        for minutes in times:
            wait = (base.replace(hour=minutes // 60, minute=minutes % 60) - now).total_seconds()
            if wait > 1 and (best is None or wait < best):
                best = wait
    return best


def schedule(state: dict | None = None) -> dict:
    raw = (state if state is not None else us.read_state()).get("schedule")
    raw = raw if isinstance(raw, dict) else {}
    return {"running": bool(raw.get("running", True)), "next_at": parse_time(raw.get("next_at"))}


def set_schedule(running: bool, next_at: datetime | None = None) -> None:
    stamp = iso(us.la_now())

    def mutate(state: dict) -> None:
        state["schedule"] = {"running": bool(running), "next_at": iso(next_at) if next_at else "", "changed": stamp}

    us.update_state(mutate)
    if running and next_at:
        log_event(f"scheduler: next step at {clock_text(next_at)}")
    elif not running:
        log_event("scheduler: stopped")


def summary(environment: str = "") -> dict:
    """What the World panel and status line show."""
    state = us.read_state()
    sched = schedule(state)
    world = load_world(state)
    if world is None:
        return {"ready": False, "running": sched["running"], "next_at": sched["next_at"], "pending": False}
    prefs = state.get("prefs") if isinstance(state.get("prefs"), dict) else {}
    catalog = catalog_for(str(environment or "").strip() or str(prefs.get("environment") or ""), world.get("discovered"))
    place = catalog["places"].get(world["lora"]["location"], {"name": world["lora"]["location"], "zone": ""})
    kind = f"{place.get('zone', '')}, discovered" if place.get("origin") == "discovered" else place.get("zone", "")
    found = sorted(
        (entry for entry in world.get("discovered", {}).values() if isinstance(entry, dict)),
        key=lambda entry: entry.get("discovered_at", ""),
        reverse=True,
    )
    goal = world.get("goal")
    memories = sorted(
        (item for item in world.get("memories", []) if item.get("active")),
        key=lambda item: (item.get("when", ""), item.get("importance", 1)),
        reverse=True,
    )[:3]
    return {
        "ready": True,
        "running": sched["running"],
        "next_at": sched["next_at"],
        "place": f"{place['name']} ({kind})".replace(" ()", ""),
        "discovered": [entry.get("name", "") for entry in found],
        "activity": world["lora"].get("activity", ""),
        "minutes": world.get("activity_minutes"),
        "mochi": world["mochi"].get("activity", ""),
        "time": clock_text(parse_time(world.get("clock"))),
        "weather": world.get("weather", ""),
        "lighting": world.get("lighting", ""),
        "goal": goal["title"] if goal else "",
        "progress": goal_text(goal).split(": ", 1)[-1] if goal else "",
        "event": world.get("situation", ""),
        "memories": [item["text"] for item in memories],
        "last_at": parse_time(world.get("last_sim_at")),
        "pending": bool(world.get("pending")),
        "error": world.get("last_error", ""),
    }
