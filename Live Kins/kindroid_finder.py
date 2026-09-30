"""Finds your kins, profiles, and group chats in Kindroid, for setup. No copying IDs by hand.

It opens the app's own browser (the one the side chatter's wand uses, so its login is kept). While you
click around Kindroid in it, it notes:
  - each kin whose chat you open: its AI ID and name
  - your profiles ("Chatting as"): their IDs and names. Kindroid's page loads the whole list from its
    database when it opens, and Find reads that list as it arrives. (The older page: each profile you switch to.)
  - each group chat you open: its ID, and which kins talk in it
Close the window when you are done. Setup then lets you pick who is who from what was found.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

import update_scene
import wand

ID = r"([A-Za-z0-9_-]{8,})"
# Where a group's ID can show up: the page address, a web address the page calls, or the data it sends.
GROUP_PLACES = (
    re.compile(r"kindroid\.ai/[^?#\s]*group[^/?#\s]*/" + ID, re.IGNORECASE),
    re.compile(r"group_?id=" + ID, re.IGNORECASE),
    re.compile(r'"group_?id"\s*:\s*"' + ID + '"', re.IGNORECASE),
)
# kindroid.ai/chat/<AI ID>, and the newer kindroid.ai/v2/chat/<AI ID>.
KIN_CHAT = re.compile(r"kindroid\.ai/(?:v\d+/)?chat/" + ID, re.IGNORECASE)
PERSONA = re.compile(r'"(?:active_)?persona_?id"\s*:\s*"' + ID + '"', re.IGNORECASE)
ID_FIELDS = ("ai_id", "aiId", "sender_id", "senderId", "kin_id", "kinId")
NAME_TRIES = 20  # the page may still be loading the first few times a kin's name is looked for
# The open kin's name: their row in the Kins list is marked as the current page ("Open Mochi").
CURRENT_KIN = """() => {
    const row = document.querySelector('[aria-current="page"][aria-label^="Open "]');
    return row ? row.getAttribute('aria-label').slice(5).trim() : '';
}"""
# Or the kin's name next to their picture, when the picture's address has the kin's AI ID in it.
PAGE_NAME = """(id) => {
    const skip = new Set(['profile', 'chat', 'social', 'kins', 'groups', 'rooms', 'search kins']);
    for (const img of document.querySelectorAll('img[src*="' + id + '"]')) {
        let el = img;
        for (let i = 0; i < 4 && el.parentElement; i++) {
            el = el.parentElement;
            const line = (el.innerText || '').split('\\n').map(s => s.trim())
                .find(s => s.length <= 40 && /\\p{L}/u.test(s) && !skip.has(s.toLowerCase()));
            if (line) return line;
        }
    }
    return '';
}"""
# Kindroid's page reads your profiles from its database (Firestore, "UserPersonas") as a stream. This listens
# along, before the page's own code runs, and keeps the parts that mention profiles for Python to read.
LISTEN_ALONG = """(() => {
    if (window.__kindroidFinder) return;
    window.__kindroidFinder = [];
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
        this.__finderUrl = String(url || '');
        return open.apply(this, arguments);
    };
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function () {
        if (this.__finderUrl.includes('Firestore/Listen/channel')) {
            let read = 0;
            let kept = '';  // the end of what came before: a profile or a kin may span two parts
            const grab = () => {
                try {
                    const text = this.responseText || '';
                    if (text.length <= read) return;
                    const part = kept + text.slice(read);
                    read = text.length;
                    kept = part.slice(-40000);
                    if (part.includes('UserPersonas/') || /[/]AIs[/][A-Za-z0-9_-]{8,}"/.test(part)) {
                        window.__kindroidFinder.push(part);
                    }
                } catch (error) {}
            };
            this.addEventListener('progress', grab);
            this.addEventListener('load', grab);
        }
        return send.apply(this, arguments);
    };
})();"""
TAKE_HEARD = "() => (window.__kindroidFinder || []).splice(0)"
PERSONA_DOC = re.compile(r'"name"\s*:\s*"projects/[^"]*/UserPersonas/([A-Za-z0-9_-]{8,})"')
PERSONA_NAME_FIELDS = ("user_name", "userName", "name", "display_name", "displayName", "persona_name", "personaName")


def personas_in(text: str) -> dict[str, str]:
    """Profile ID to name, from what Kindroid's database sent the page."""
    text = str(text or "").replace('\\"', '"')
    found: dict[str, str] = {}
    for doc in PERSONA_DOC.finditer(text):
        fields = text[doc.end() : doc.end() + 4000]
        following = fields.find('"name":"projects/')
        fields = fields[:following] if following > 0 else fields
        name = ""
        for field in PERSONA_NAME_FIELDS:
            value = re.search(r'"%s"\s*:\s*\{\s*"stringValue"\s*:\s*"((?:[^"\\]|\\.){1,80})"' % field, fields)
            if value:
                try:
                    name = json.loads('"' + value.group(1) + '"').strip()
                except ValueError:
                    name = value.group(1).strip()
                break
        found[doc.group(1)] = name or found.get(doc.group(1), "")
    return found


KIN_DOC = re.compile(r'"name"\s*:\s*"projects/[^"]*/AIs/([A-Za-z0-9_-]{8,})"')
TEXT_FIELD = re.compile(r'"([A-Za-z0-9_]+)"\s*:\s*\{\s*"stringValue"\s*:\s*"((?:[^"\\]|\\.)*)"')
# The kin's current setting and backstory. Kindroid may call them either way.
SCENE_FIELDS = ("current_scene", "currentScene", "current_setting", "currentSetting", "scene", "setting")
BACKSTORY_FIELDS = ("ai_backstory", "backstory", "backStory", "back_story")
KIN_NAME_FIELDS = ("ai_name", "name", "display_name", "displayName", "aiName")


def _json_text(value: str) -> str:
    try:
        return json.loads('"' + value + '"')
    except ValueError:
        return value


def _pick(fields: dict, names: tuple, like: str) -> str:
    for name in names:
        if str(fields.get(name) or "").strip():
            return str(fields[name]).strip()
    for name, value in fields.items():
        if like in name.lower() and str(value).strip():
            return str(value).strip()
    return ""


def kins_in(text: str) -> dict[str, dict]:
    """Kin AI ID to its text details (name, current setting, backstory...), from what Kindroid's database sent."""
    text = str(text or "")
    found: dict[str, dict] = {}
    for doc in KIN_DOC.finditer(text):
        fields = text[doc.end() : doc.end() + 40000]
        following = fields.find('"name":"projects/')
        fields = fields[:following] if following > 0 else fields
        values = {name: _json_text(value) for name, value in TEXT_FIELD.findall(fields)}
        found.setdefault(doc.group(1), {}).update(values)
    return found


def kin_details(fields: dict) -> dict:
    """The name, current setting, and backstory in a kin's details. Empty when Kindroid has none."""
    return {
        "name": _pick(fields, KIN_NAME_FIELDS, "name")[:60],
        "scene": _pick(fields, SCENE_FIELDS, "scene"),
        "backstory": _pick(fields, BACKSTORY_FIELDS, "backstory"),
        "memory": _pick(fields, ("ai_memory",), "ai_memory"),
        "directive": _pick(fields, ("ai_directive",), "directive"),
    }


# What Find saw, kept next to the browser's login so a changed Kindroid page can be looked at and fixed.
DEBUG_PAGE = "find-page.html"
DEBUG_LOG = "find-log.txt"


def kin_id_in(text: str) -> str:
    found = KIN_CHAT.search(str(text or ""))
    return found.group(1) if found else ""


def group_id_in(text: str) -> str:
    for place in GROUP_PLACES:
        found = place.search(str(text or ""))
        if found:
            return found.group(1)
    return ""


def kins_in_group(api_key: str, group_id: str) -> dict[str, str]:
    """The kins who talked in the group: AI ID to name, when Kindroid says which kin wrote each line."""
    found: dict[str, str] = {}
    after = 0
    for _ in range(5):
        messages, latest = update_scene.fetch_messages(after, group_id=group_id, api_key=api_key)
        for item in messages:
            if str(item.get("sender") or "") != "ai":
                continue
            name = str(item.get("display_name") or "").strip()
            kin = next((str(item[key]) for key in ID_FIELDS if isinstance(item.get(key), str) and item.get(key)), "")
            if kin:
                found[kin] = name or found.get(kin, "")
        if len(messages) < update_scene.MESSAGES_PER_READ or latest <= after:
            break
        after = latest
    return found


def copy_login(source: Path) -> Path:
    """A copy of the app's browser login, for when the real one is still in use."""
    target = Path(tempfile.mkdtemp(prefix="kindroid-finder-"))
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any("cache" in part.lower() for part in relative.parts):
            continue
        if relative.name.startswith("Singleton") or relative.name in {"lockfile", "LOCK"}:
            continue
        destination = target / relative
        try:
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        except OSError:
            continue
    return target


class Finder:
    """Run it on its own thread. found() can be read from any thread while it runs."""

    def __init__(self, api_key: str = "", profile: Path | None = None) -> None:
        self.api_key = api_key.strip()
        self.profile = profile or wand.PROFILE
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.kins: dict[str, dict] = {}  # AI ID -> {"name", "url"}
        self.profiles: dict[str, str] = {}  # profile ID -> name
        self.groups: dict[str, dict] = {}  # group ID -> {"ok", "problem", "kins": {AI ID: name}}
        self.problem = ""
        self.running = False
        self.kin_fields: dict[str, set] = {}  # the names of the details Kindroid sent for each kin, for the debug log

    # ------------------------------------------------------------ what was found

    def found(self) -> dict:
        with self.lock:
            return {
                "kins": {key: dict(value) for key, value in self.kins.items()},
                "profiles": dict(self.profiles),
                "groups": {key: dict(value, kins=dict(value["kins"])) for key, value in self.groups.items()},
                "problem": self.problem,
                "running": self.running,
            }

    def _kin(self, ai_id: str, name: str = "", url: str = "", scene: str = "", backstory: str = "", **more: str) -> None:
        with self.lock:
            entry = self.kins.setdefault(ai_id, {"name": "", "url": ""})
            entry["name"] = entry["name"] or name
            entry["url"] = entry["url"] or url
            for detail, value in (("scene", scene), ("backstory", backstory), *more.items()):
                if value:
                    entry[detail] = value

    def _ask_name(self, ai_id: str) -> str:
        """The kin's name from Kindroid, from the lines they wrote. Empty for a chat with no messages yet."""
        if not self.api_key:
            return ""
        try:
            return update_scene.kin_name(ai_id, api_key=self.api_key)
        except Exception:
            return ""

    def _page_name(self, pages, ai_id: str) -> str:
        """The kin's name as the page shows it: the open chat's row in the Kins list, or next to their picture."""
        for page in pages:
            try:
                if kin_id_in(page.url) == ai_id:
                    name = str(page.evaluate(CURRENT_KIN) or "").strip()
                    if name:
                        return name
                name = str(page.evaluate(PAGE_NAME, ai_id) or "").strip()
            except Exception:
                continue
            if name:
                return name
        return ""

    def _read_profiles(self, pages) -> None:
        """The profiles and kins Kindroid's database sent the pages since the last look."""
        for page in pages:
            try:
                heard = page.evaluate(TAKE_HEARD) or []
            except Exception:
                continue
            for part in heard:
                for persona_id, name in personas_in(part).items():
                    with self.lock:
                        if name or persona_id not in self.profiles:
                            self.profiles[persona_id] = name
                for ai_id, fields in kins_in(part).items():
                    details = kin_details(fields)
                    self._kin(ai_id, details["name"], "", details["scene"], details["backstory"],
                              memory=details["memory"], directive=details["directive"])
                    with self.lock:
                        self.kin_fields.setdefault(ai_id, set()).update(fields)

    def _check_group(self, group_id: str) -> None:
        entry = {"ok": False, "problem": "", "kins": {}}
        if not self.api_key:
            entry["problem"] = "add your Kindroid API key to check it"
        else:
            try:
                entry["kins"] = kins_in_group(self.api_key, group_id)
                entry["ok"] = True
            except Exception as error:
                entry["problem"] = str(error)[:160]
        with self.lock:
            self.groups[group_id] = entry
        for ai_id, name in entry["kins"].items():
            self._kin(ai_id, name)

    # ------------------------------------------------------------ the browser

    def _open(self):
        """The app's browser with its login, or a copy of the login if the real one is still in use."""
        try:
            return (*wand._browser(self.profile, visible=True), None)
        except RuntimeError as error:
            if not self.profile.exists():
                raise
            first = error
        copy = copy_login(self.profile)
        try:
            return (*wand._browser(copy, visible=True), copy)
        except RuntimeError:
            shutil.rmtree(copy, ignore_errors=True)
            raise first

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        with self.lock:
            self.running, self.problem = True, ""
        try:
            self._run()
        except Exception as error:
            with self.lock:
                self.problem = str(error)
        finally:
            with self.lock:
                self.running = False

    def _run(self) -> None:
        wand.shutdown()  # the side chatter's hidden browser, if this app has one open
        playwright, context, copy = self._open()
        groups_seen: list[str] = []
        personas_seen: list[str] = []
        name_tries: dict[str, int] = {}  # kin AI ID -> times its name was looked for
        log_lines: list[str] = []

        def on_request(request) -> None:
            try:
                body = request.post_data or ""
            except Exception:
                body = ""
            if "kindroid" in request.url and request.method != "GET":
                log_lines.append(f"{request.method} {request.url.split('?')[0]}  {body[:300]}")
            group_id = group_id_in(request.url) or group_id_in(body)
            if group_id and group_id not in groups_seen:
                groups_seen.append(group_id)
            found = PERSONA.search(body) if "kindroid" in request.url else None
            if found and found.group(1) not in personas_seen:
                personas_seen.append(found.group(1))

        persona_waiting, persona_since = "", 0.0
        last_url = ""
        try:
            context.add_init_script(LISTEN_ALONG)
            context.on("request", on_request)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(wand.HOME, wait_until="domcontentloaded", timeout=60000)
            while not self.stop_event.is_set():
                try:
                    pages = list(context.pages)
                except Exception:
                    break
                if not pages:
                    break
                for item in pages:
                    try:
                        url = item.url
                    except Exception:
                        continue
                    group_id = group_id_in(url)
                    if group_id and group_id not in groups_seen:
                        groups_seen.append(group_id)
                    kin = kin_id_in(url)
                    if kin and kin not in name_tries:
                        name_tries[kin] = 0
                        self._kin(kin, "", url.split("#")[0])
                    if url != last_url:
                        last_url = url
                        self._keep_debug(item, url)
                self._read_profiles(pages)
                # A kin's name: from the page, or else from their messages.
                for kin, tries in list(name_tries.items()):
                    if tries >= NAME_TRIES or self.kins[kin]["name"]:
                        continue
                    name_tries[kin] = tries + 1
                    name = self._page_name(pages, kin) or (self._ask_name(kin) if tries in (0, NAME_TRIES - 1) else "")
                    if name:
                        self._kin(kin, name)
                for group_id in [g for g in groups_seen if g not in self.groups]:
                    self._check_group(group_id)
                # A profile switch: its name shows under the message box a moment later.
                if personas_seen and not persona_waiting:
                    persona_waiting, persona_since = personas_seen.pop(0), time.monotonic()
                if persona_waiting and time.monotonic() - persona_since > 2:
                    name = ""
                    for item in pages:
                        name = wand.current_persona(item) or name
                    with self.lock:
                        self.profiles[persona_waiting] = re.sub(r"\s*\([^)]*\)\s*$", "", name) or self.profiles.get(persona_waiting, "")
                    persona_waiting = ""
                try:
                    pages[0].wait_for_timeout(500)
                except Exception:
                    break
        finally:
            with self.lock:
                log_lines += [f"kin {ai_id} details: {', '.join(sorted(names))}" for ai_id, names in self.kin_fields.items()]
            try:
                (self.profile / DEBUG_LOG).write_text("\n".join(log_lines[-300:]) + "\n", encoding="utf-8")
            except OSError:
                pass
            wand._close_browser(playwright, context)
            if copy:
                shutil.rmtree(copy, ignore_errors=True)

    def _keep_debug(self, page, url: str) -> None:
        """Save the Kindroid page (a chat page, once it has loaded), so a changed page can be fixed later."""
        if "kindroid.ai" not in url:
            return
        try:
            page.wait_for_timeout(1500)
            (self.profile / DEBUG_PAGE).write_text(f"<!-- {url} -->\n" + page.content(), encoding="utf-8")
        except Exception:
            pass
