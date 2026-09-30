"""Kindroid extras: things the app can also do in Kindroid, each one off until you turn it on.

1. Know your kin: read the kin's backstory, key memories, and directive from Kindroid (each morning, or on Read now).
2. Journal: add journal entries (keyword memories) for the places the kin finds, and a short one for each day.
3. Key memories: add the story's big moments to the kin's key memories (read first, then added, never overwritten).
4. Favorites: pin the Narrator's dreams and discoveries.
5. Background: the chat background shows the kin's latest selfie (so it follows where they are).
6. Reactions: the side character reacts to the kin's last message while it is their turn (see wand.py).
7. Selfies: the kin sends a selfie at a new place (uses Kindroid credits, a few a day at most).

Everything that clicks around Kindroid's website runs in the app's own hidden browser (the one Find logged in),
one thing at a time, after the side character's turn. If Kindroid changes its website and something can't be
done, it is written in simulation.log and the story carries on.
Key memories use Kindroid's API (update-info, ai_memory).
"""

from __future__ import annotations

import json
import queue
import random
import re
import threading
import time
from datetime import datetime

import update_scene as us

HOME = "https://kindroid.ai"
V2_CHAT = HOME + "/v2/chat/{kin}/"
V1_CHAT = HOME + "/chat/{kin}/"
JOURNAL = HOME + "/v2/kin-settings/{kin}/?tab=journal"
OVERLAY = "[class*='message-actions-overlay-v2_root']"
TRIGGERS = "button[aria-label^='Open actions for message from']"
REACTIONS = ("❤️", "\U0001f979", "\U0001f389", "\U0001f525")  # the ones Kindroid offers: heart, moved, party, fire
KEY_MEMORY_LIMIT = 1000  # stay within what Kindroid keeps for key memories
MEMORY_HEADER = "From our story:"
MEMORIES_KEPT = 10
SELFIES_PER_DAY = 3

# The switches (prefs), and what Settings > Extras calls them.
SWITCHES = {
    "x_read_kin": "Know Lora: read Lora's backstory and key memories from Kindroid each morning",
    "x_journal": "Journal: write the places Lora finds, and each day, into Lora's Kindroid journal",
    "x_memories": "Key memories: add the story's big moments to Lora's key memories",
    "x_pins": "Favorites: pin the Narrator's dreams and discoveries",
    "x_react": "Mochi reacts to Lora's messages (on Mochi's turn, new Kindroid site)",
    "x_selfies": "Selfies: Lora sends a selfie at new places (uses Kindroid credits)",
    "x_background": "Background: the chat background shows Lora's latest selfie",
}


def enabled(name: str) -> bool:
    try:
        return str(us.load_prefs().get(name) or "0") == "1"
    except Exception:
        return False


def _log(text: str) -> None:
    try:
        import simulation

        simulation.log_event(text)
    except Exception:
        pass


def _kin_id() -> str:
    us.load_env(us.ENV_PATH)
    return us.require_keys()[2]


# ---------------------------------------------------------------- one thing at a time, in the background


class _Worker:
    def __init__(self) -> None:
        self.jobs: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()

    def add(self, name: str, work, done=None) -> None:
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True, name="kindroid-extras")
                self.thread.start()
        self.jobs.put((name, work, done))

    def _run(self) -> None:
        while True:
            name, work, done = self.jobs.get()
            try:
                result, error = work(), ""
                _log(f"Kindroid extras: {name}: done")
            except Exception as caught:
                result, error = None, str(caught)
                _log(f"Kindroid extras: could not {name}. {error}")
            if done is not None:
                try:
                    done(result, error)
                except Exception:
                    pass


_WORKER = _Worker()


def later(name: str, work, done=None) -> None:
    """Do it in the background. done(result, error) is called after, on the worker's thread."""
    _WORKER.add(name, work, done)


def _in_browser(work, *args):
    import wand

    return wand.run(work, *args)


# ---------------------------------------------------------------- the website: small steps


def _goto(page, url: str, ready: str, seconds: float = 45) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.locator(ready).first.wait_for(state="visible", timeout=seconds * 1000)
    except Exception as error:
        raise RuntimeError(
            "Kindroid's page did not open in the app's browser. It may have been logged out: run setup's Find and log in."
        ) from error


_LAST_TRIGGER = """(name) => {
    // The last "Open actions for message from <name>" button (names compared without case).
    const all = [...document.querySelectorAll("button[aria-label^='Open actions for message from']")];
    const want = 'open actions for message from ' + String(name).trim().toLowerCase();
    let found = -1;
    all.forEach((button, index) => {
        if ((button.getAttribute('aria-label') || '').trim().toLowerCase() === want) found = index;
    });
    return found;
}"""
_ALREADY_REACTED = """(index) => {
    // Does that message already have a reaction from you? (Picking the same emoji again would remove it.)
    const trigger = document.querySelectorAll("button[aria-label^='Open actions for message from']")[index];
    let box = trigger;
    for (let i = 0; i < 8 && box.parentElement; i++) {
        const up = box.parentElement;
        if (up.querySelectorAll("button[aria-label^='Open actions for message from']").length > 1) break;
        box = up;
    }
    return !!box.querySelector("button[aria-label^='You reacted with']");
}"""
_ROW_INDEX = """(first) => {
    // The menu row whose first line is exactly this ("Favorite", "Autoselfie"...), in the normal menu.
    const buttons = [...document.querySelectorAll("[class*='message-actions-overlay-v2_root'] button")];
    return buttons.findIndex(b => !(b.getAttribute('aria-label') || '').startsWith('React with')
        && (b.innerText || '').trim().split('\\n')[0].trim().toLowerCase() === String(first).toLowerCase());
}"""


def _open_menu(page, sender: str) -> bool:
    """Open the actions menu of sender's last message. False when there is no such message."""
    index = page.evaluate(_LAST_TRIGGER, sender)
    if index < 0:
        return False
    trigger = page.locator(TRIGGERS).nth(index)
    trigger.scroll_into_view_if_needed(timeout=5000)
    trigger.click(timeout=5000)
    page.locator(OVERLAY).first.wait_for(state="visible", timeout=8000)
    return True


def _close_menu(page) -> None:
    try:
        if page.locator(OVERLAY).count():
            page.locator(f"{OVERLAY} button[aria-label='Dismiss']").first.click(force=True, timeout=3000)
    except Exception:
        pass


def _click_row(page, first_line: str) -> bool:
    index = page.evaluate(_ROW_INDEX, first_line)
    if index < 0:
        return False
    page.locator(f"{OVERLAY} button").nth(index).click(timeout=5000)
    return True


# ---------------------------------------------------------------- 1. know your kin

_READ_KIN = """async ([kin, since]) => {
    // Kindroid's website keeps the kins it loaded in its own storage (IndexedDB "kindroid-v2").
    const db = await new Promise((ok, bad) => { const r = indexedDB.open('kindroid-v2'); r.onsuccess = () => ok(r.result); r.onerror = () => bad(r.error); });
    if (!db.objectStoreNames.contains('react-query')) return null;
    const rows = await new Promise(ok => { const q = db.transaction('react-query').objectStore('react-query').getAll(); q.onsuccess = () => ok(q.result); q.onerror = () => ok([]); });
    let best = null;
    for (const row of rows) {
        let client = row.client;
        if (typeof client === 'string') { try { client = JSON.parse(client); } catch (e) { continue; } }
        for (const query of ((client || {}).clientState || {}).queries || []) {
            const data = query.state && query.state.data;
            if (!data || typeof data !== 'object') continue;
            const docs = [];
            for (const page of data.pages || []) docs.push(...((page && page.docs) || (Array.isArray(page) ? page : [])));
            if (data.ai_id) docs.push(data);
            for (const doc of docs) {
                if (!doc || doc.ai_id !== kin) continue;
                const at = query.state.dataUpdatedAt || 0;
                if (!best || at > best.at) best = {at, doc};
            }
        }
    }
    if (!best) return null;
    const d = best.doc;
    return {fresh: best.at >= since, name: d.ai_name || '', backstory: d.ai_backstory || '', memory: d.ai_memory || '',
            directive: d.ai_directive || '', example: d.ai_example_message || '', scene: d.current_scene || ''};
}"""


def read_kin(page, kin: str, need_fresh: bool = False) -> dict:
    """The kin's details as Kindroid has them now: name, backstory, memory (key memories), directive, example, scene."""
    since = int(time.time() * 1000) - 5000
    _goto(page, V2_CHAT.format(kin=kin), "textarea")
    found = None
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            found = page.evaluate(_READ_KIN, [kin, since])
        except Exception:
            found = None
        if found and found.get("fresh"):
            return found
        page.wait_for_timeout(1000)
    if found and not need_fresh:
        return found
    raise RuntimeError("Could not read the kin's details from Kindroid's page.")


def save_kin_profile(details: dict) -> None:
    """Keep what Kindroid says about the kin. The story reads it (update_scene.kin_profile_text)."""
    prefs = us.load_prefs()
    prefs["kin_profile"] = {
        "name": details.get("name", ""), "backstory": details.get("backstory", ""), "memory": details.get("memory", ""),
        "directive": details.get("directive", ""), "example": details.get("example", ""),
        "read_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not str(prefs.get("backstory") or "").strip() and details.get("backstory"):
        prefs["backstory"] = details["backstory"]
    us.save_prefs(prefs)


def refresh_kin(done=None) -> None:
    """Read the kin's details now (Settings > Extras > Read now, and each morning)."""
    kin = _kin_id()

    def work():
        details = _in_browser(read_kin, kin)
        save_kin_profile(details)
        return details

    later("read the kin's details", work, done)


# ---------------------------------------------------------------- 2. journal


def add_journal_entry(page, kin: str, keywords: list[str], description: str) -> None:
    """A journal entry: keywords (2-50 letters, up to 8) that bring the description (up to 500) back up."""
    words = []
    for word in keywords:
        word = " ".join(str(word).split())[:50]
        if len(word) >= 2 and word.lower() not in {w.lower() for w in words}:
            words.append(word)
    words = words[:8]
    if not words or not description.strip():
        return
    _goto(page, JOURNAL.format(kin=kin), "button[aria-label='Add entry']")
    page.locator("button[aria-label='Add entry']").first.click(timeout=5000)
    box = page.locator("input[placeholder^='e.g. Coffee']").first
    box.wait_for(state="visible", timeout=8000)
    for word in words:
        box.fill(word)
        box.press("Enter")
        page.wait_for_timeout(250)
    text = page.locator("textarea[placeholder^='Anything your kins should know']").first
    text.fill(description.strip()[:500])
    page.get_by_role("button", name="Add entry").last.click(timeout=5000)
    try:
        text.wait_for(state="hidden", timeout=15000)
    except Exception as error:
        raise RuntimeError("Kindroid did not take the journal entry.") from error


def _day_summary(entries: list[dict], kin_name: str) -> str:
    """A few sentences about the day, from the story's journal."""
    lines = "\n".join(f"- {entry.get('now') or entry.get('event')}" for entry in entries if entry.get("now") or entry.get("event"))
    if not lines:
        return ""
    us.load_env(us.ENV_PATH)
    key = us.require_keys()[0]
    prompt = (
        f"These are moments from Lora's day, oldest first:\n{lines}\n\n"
        "Write two or three plain sentences about this day, past tense, as a memory Lora keeps: where she went and "
        "what she did. No more than 400 characters. No quotes, no headings."
    )
    return us.ask_deepseek(key, prompt, "You write short, plain memories.", max_tokens=200, temperature=0.6, paragraph=True)[:480]


# ---------------------------------------------------------------- 3. key memories


def merged_memory(current: str, lines: list[str]) -> str | None:
    """The key memories with the story's lines kept at the end (only the newest that fit). None when nothing fits."""
    current = str(current or "")
    base = current.split(MEMORY_HEADER)[0].rstrip() if MEMORY_HEADER in current else current.rstrip()
    room = KEY_MEMORY_LIMIT - len(base) - len(MEMORY_HEADER) - 4
    kept: list[str] = []
    for line in reversed(lines):
        entry = "- " + " ".join(str(line).split())[:140]
        if sum(len(item) + 1 for item in kept) + len(entry) + 1 > room:
            break
        kept.insert(0, entry)
    if not kept:
        return None
    return (base + "\n\n" if base else "") + MEMORY_HEADER + "\n" + "\n".join(kept)


def remember(line: str) -> None:
    """Add a big moment to the kin's key memories. Read first: the rest of the key memories stays as it is."""
    line = " ".join(str(line or "").split())
    if not line:
        return
    prefs = us.load_prefs()
    lines = [item for item in prefs.get("x_story_memories") or [] if isinstance(item, str)]
    lines = (lines + [line])[-MEMORIES_KEPT:]
    prefs["x_story_memories"] = lines
    us.save_prefs(prefs)
    kin = _kin_id()

    def work():
        details = _in_browser(read_kin, kin)
        current = details.get("memory", "")
        # Kindroid's website keeps its copy of the kin a few minutes behind. If that copy does not have what the app
        # wrote last time yet, it is older than that write: wait, so a newer edit of yours is never written over.
        written = str(us.load_prefs().get("x_memory_written") or "")
        if written and written not in current:
            raise RuntimeError("Kindroid's copy of the key memories is not up to date yet. It tries again next time.")
        text = merged_memory(current, lines)
        if text is None:
            raise RuntimeError("Lora's key memories are full, so nothing was added.")
        if text == current:
            return "no change"
        _, kindroid_key, _ = us.require_keys()
        us.post_json(us.KINDROID_URL, {"ai_id": kin, "ai_memory": text}, us.kindroid_headers(kindroid_key), timeout=30)
        saved = us.load_prefs()
        saved["x_memory_written"] = text[text.index(MEMORY_HEADER):]
        us.save_prefs(saved)
        return "added"

    later("add to key memories", work)


# ---------------------------------------------------------------- 4. favorites


def favorite_last(page, kin: str, sender: str) -> str:
    """Pin sender's last message (the Narrator's dream or discovery)."""
    _goto(page, V2_CHAT.format(kin=kin), "textarea")
    page.wait_for_timeout(1500)
    if not _open_menu(page, sender):
        raise RuntimeError(f"No message from {sender} to pin.")
    try:
        if page.evaluate(_ROW_INDEX, "Unfavorite") >= 0:
            return "already pinned"
        if not _click_row(page, "Favorite"):
            raise RuntimeError("Kindroid's message menu has no Favorite.")
        return "pinned"
    finally:
        page.wait_for_timeout(500)
        _close_menu(page)


# ---------------------------------------------------------------- 5. background


def use_selfie_background(page, kin: str, on: bool = True) -> str:
    """Kindroid's Chat Background: "Always use latest selfie in gallery" on (or off)."""
    _goto(page, V1_CHAT.format(kin=kin), "button[aria-label='Background settings']")
    page.locator("button[aria-label='Background settings']").first.click(timeout=5000)
    dialog = page.locator("[role='dialog']").filter(has_text="Chat Background").first
    dialog.wait_for(state="visible", timeout=8000)
    state = page.evaluate(
        """() => {
            const dialog = [...document.querySelectorAll('[role="dialog"]')].find(d => /Chat Background/.test(d.innerText || ''));
            const label = [...dialog.querySelectorAll('p')].find(p => /Always use latest selfie/i.test(p.textContent || ''));
            let row = label;
            for (let i = 0; i < 4 && row && !row.querySelector('input[type="checkbox"]'); i++) row = row.parentElement;
            const box = row && row.querySelector('input[type="checkbox"]');
            if (!box) return null;
            box.setAttribute('data-live-kins', 'selfie-background');
            return box.checked;
        }"""
    )
    if state is None:
        raise RuntimeError("Kindroid's Chat Background has no 'Always use latest selfie' switch.")
    if bool(state) != on:
        page.locator("input[data-live-kins='selfie-background']").first.click(force=True, timeout=5000)
        page.wait_for_timeout(300)
    dialog.get_by_role("button", name="Save").click(timeout=5000)
    page.wait_for_timeout(1000)
    return "on" if on else "off"


def set_background(on: bool) -> None:
    kin = _kin_id()
    later(f"turn the selfie background {'on' if on else 'off'}", lambda: _in_browser(use_selfie_background, kin, on))


# ---------------------------------------------------------------- 6. reactions (from wand.py, on the side character's turn)


def react_to_last(page, kin_name: str) -> str:
    """React to the kin's last message, as whoever the chat is on now (the side character). Never twice."""
    index = page.evaluate(_LAST_TRIGGER, kin_name)
    if index < 0 or page.evaluate(_ALREADY_REACTED, index):
        return "nothing to react to"
    if not enabled("x_react") or random.random() > 0.5:
        return "not this time"
    if not _open_menu(page, kin_name):
        return "nothing to react to"
    try:
        page.locator(f"{OVERLAY} button[aria-label='React to message']").first.click(timeout=5000)
        emoji = random.choice(REACTIONS)
        page.locator(f"{OVERLAY} button[aria-label='React with {emoji}']").first.click(timeout=5000)
        return f"reacted {emoji}"
    finally:
        page.wait_for_timeout(500)
        _close_menu(page)


# ---------------------------------------------------------------- 7. selfies


def autoselfie(page, kin: str, kin_name: str) -> str:
    """Ask the kin for a selfie of the current moment (Kindroid's Autoselfie, on their last message)."""
    _goto(page, V2_CHAT.format(kin=kin), "textarea")
    page.wait_for_timeout(1500)
    if not _open_menu(page, kin_name):
        raise RuntimeError(f"No message from {kin_name} to ask a selfie from.")
    if not _click_row(page, "Autoselfie"):
        _close_menu(page)
        raise RuntimeError("Kindroid's message menu has no Autoselfie.")
    create = page.get_by_role("button", name="Create Selfie")
    create.wait_for(state="visible", timeout=10000)
    create.click(timeout=5000)
    try:
        page.get_by_text("Autoselfie requested").first.wait_for(state="visible", timeout=90000)
    except Exception as error:
        raise RuntimeError("Kindroid did not start the selfie (maybe no selfie credits left).") from error
    return "requested"


def selfies_left_today() -> int:
    prefs = us.load_prefs()
    today = datetime.now().date().isoformat()
    used = int(prefs.get("x_selfies_used") or 0) if prefs.get("x_selfies_day") == today else 0
    try:
        most = max(0, int(str(prefs.get("x_selfies_per_day") or SELFIES_PER_DAY)))
    except ValueError:
        most = SELFIES_PER_DAY
    return max(0, most - used)


def _count_selfie() -> None:
    prefs = us.load_prefs()
    today = datetime.now().date().isoformat()
    used = int(prefs.get("x_selfies_used") or 0) if prefs.get("x_selfies_day") == today else 0
    prefs.update(x_selfies_day=today, x_selfies_used=used + 1)
    us.save_prefs(prefs)


# ---------------------------------------------------------------- when things happen in the story (from gui.py)


def _names() -> tuple[str, str]:
    """The kin's and the Narrator's names as Kindroid's chat shows them (the kin's own Kindroid name when known)."""
    import cast

    now = cast.cast()
    profile = us.load_prefs().get("kin_profile")
    kindroid_name = str((profile or {}).get("name") or "").strip() if isinstance(profile, dict) else ""
    narrator = str((us.load_json(us.CONFIG_PATH, {}).get("narrator_profile") or {}).get("user_name") or now["narrator_name"])
    return kindroid_name or now["kin_name"], narrator.strip() or now["narrator_name"]


def after_change(result: dict) -> None:
    """After a scene change reached Kindroid: a discovery, a new place."""
    if not result or result.get("pending") or result.get("extended"):
        return
    kin_name, narrator = _names()
    kin = _kin_id()
    place = str(result.get("location") or "")
    found = str(result.get("discovered") or "")
    prefs = us.load_prefs()
    new_place = bool(place) and place != prefs.get("x_last_place")
    if new_place:
        prefs["x_last_place"] = place
        us.save_prefs(prefs)
    if found:
        about = _discovery_text(found)
        if enabled("x_journal"):
            keywords = [found] + [word for word in re.findall(r"[A-Za-z][A-Za-z']{3,}", found)]
            later(f"add {found} to the journal", lambda: _in_browser(add_journal_entry, kin, keywords, about))
        if enabled("x_memories"):
            remember(f"{kin_name} found the {found.lower()} ({datetime.now():%b %d}).")
        if enabled("x_pins"):
            later("pin the discovery", lambda: _in_browser(favorite_last, kin, narrator))
    if new_place and enabled("x_selfies") and selfies_left_today() > 0:
        _count_selfie()
        later(f"ask {kin_name} for a selfie at the {place.lower()}", lambda: _in_browser(autoselfie, kin, kin_name))


def _discovery_text(name: str) -> str:
    """What the story knows about a place the kin found."""
    import cast

    try:
        world = us.read_state().get("world") or {}
        for entry in (world.get("discovered") or {}).values():
            if isinstance(entry, dict) and str(entry.get("name") or "").lower() == name.lower():
                text = f"{entry.get('description') or ''} {cast.cast()['kin_name']} found it on {datetime.now():%B %d}."
                return cast.localize(" ".join(text.split()))[:500]
    except Exception:
        pass
    return f"{name}: a place {cast.cast()['kin_name']} found on {datetime.now():%B %d}."


def after_dream() -> None:
    if enabled("x_pins"):
        kin_name, narrator = _names()
        kin = _kin_id()
        later("pin the dream", lambda: _in_browser(favorite_last, kin, narrator))


def after_sleep() -> None:
    """At bedtime: the day goes into the journal and the key memories."""
    if not (enabled("x_journal") or enabled("x_memories")):
        return
    kin_name, _narrator = _names()
    kin = _kin_id()
    try:
        world = us.read_state().get("world") or {}
    except Exception:
        return
    since = time.time() - 18 * 3600
    entries = []
    for entry in world.get("journal") or []:
        try:
            if datetime.fromisoformat(str(entry.get("at"))).timestamp() >= since:
                entries.append(entry)
        except ValueError:
            continue
    if not entries:
        return
    import cast

    places = []
    for entry in entries:
        place = str(entry.get("to") or "").replace("-", " ").strip()
        if place and place not in places:
            places.append(place)

    def work():
        summary = cast.localize(_day_summary(entries, kin_name))
        if not summary:
            return "nothing to write"
        if enabled("x_journal"):
            day = datetime.now().strftime("%A %B %d")
            _in_browser(add_journal_entry, kin, [day, *places][:8], f"{day}: {summary}")
        if enabled("x_memories"):
            remember(f"{datetime.now():%b %d}: {summary[:130]}")
        return "written"

    later("write the day into the journal", work)


def morning() -> None:
    if enabled("x_read_kin"):
        refresh_kin()


def status_json() -> str:
    """For the log: which extras are on."""
    return json.dumps({name: enabled(name) for name in SWITCHES})
