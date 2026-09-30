"""Mochi's wand: presses Kindroid's own wand button in a browser this app owns. No API.

The browser is the app's own (never your Chrome). Set up opens a window once, so you can log in and
open Lora's chat. After that the browser runs fully hidden: no window, nothing on the taskbar, nothing
to click. It starts with Mochi's first tap and stays open until the app closes. Each tap: switch to
Mochi, press the wand, wait for the line, press send, switch back.
If something on the page is not where it should be, the page is saved to kindroid-browser/last-problem.html
(and a picture next to it) so it can be fixed.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from pathlib import Path

PROFILE = Path(__file__).resolve().parent / "kindroid-browser"
HOME = "https://kindroid.ai"
# Only the wand. (Never "Regenerate or suggest a change", which would redo Lora's message.)
# The new page (kindroid.ai/v2) first, then the old one.
WAND_LABELS = ("Suggest a message", "Suggest message")
WAND = ", ".join(f"button[aria-label='{label}']" for label in WAND_LABELS)
BOX = "textarea[aria-label='Message'], textarea[aria-label='Send message textarea']"
# The new page's "Chatting as" button: "Switch persona (currently Narrator)".
PERSONA_BUTTON = "button[aria-label^='Switch persona']"
# Which Kindroid site you use: the classic one (v1), the new one (v2), or let the wand check the page.
SITE_CHOICES = {
    "Not sure (the app checks by itself)": "auto",
    "v1: the classic site (kindroid.ai/chat/...)": "v1",
    "v2: the new site (kindroid.ai/v2/chat/...)": "v2",
}
SITE_HELP = "Is the Kindroid site right? Settings > Chat > Kindroid site."
SEND_NAMES = ("Send message", "Send")
CHATTING_AS = re.compile(r"chatting as\s+(.+)", re.IGNORECASE)
# Keep working when the window is not in front, covered, or off-screen.
KEEP_AWAKE = [
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
]
# The full browser, hidden: no window and no taskbar button. (The cut-down headless shell is not used.)
HIDDEN = ["--headless=new", "--window-size=1280,900"]
# Pages see a normal, focused browser.
LOOK_NORMAL = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});
Object.defineProperty(document, 'hidden', {get: () => false});
document.hasFocus = () => true;
"""
TAP_TIMEOUT = 5 * 60
_GATE = threading.Lock()


def talk_minutes(text: str) -> int:
    """How long to wait between wand taps. Kept between 1 and 180 minutes."""
    try:
        value = int(str(text).strip())
    except ValueError as error:
        raise RuntimeError("Talk every needs a number of minutes.") from error
    if value < 1 or value > 180:
        raise RuntimeError("Talk every must be between 1 and 180 minutes.")
    return value


# ---------------------------------------------------------------- the saved chat


def _url_path(profile: Path | None) -> Path:
    return (profile or PROFILE) / "chat-url.txt"


def saved_chat(profile: Path | None = None) -> str:
    path = _url_path(profile)
    if not path.is_file():
        return ""
    url = path.read_text(encoding="utf-8").strip()
    return url if url.startswith("https://kindroid.ai") else ""


def remember_chat(url: str, profile: Path | None = None) -> None:
    if not str(url).startswith("https://kindroid.ai"):
        raise RuntimeError("Log in and open Lora's chat before closing the window.")
    path = _url_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(url.strip(), encoding="utf-8")


# ---------------------------------------------------------------- the browser


def browser_options(folder: Path, visible: bool) -> dict:
    """The app's own browser. Never the Chrome installed on the computer."""
    options = {
        "user_data_dir": str(folder),
        "headless": False,
        "viewport": {"width": 1280, "height": 900},
        "locale": "en-US",
        "args": ["--disable-extensions", "--disable-blink-features=AutomationControlled", *KEEP_AWAKE],
        "ignore_default_args": ["--enable-automation"],
    }
    if not visible:
        options["args"] += HIDDEN
    executable = os.environ.get("KINDROID_BROWSER_PATH", "")
    if executable:
        options["executable_path"] = executable
    return options


def _browser(folder: Path, visible: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "Mochi's browser is not installed. Open a command window in the app folder and run: "
            "pip install playwright   then   python -m playwright install chromium"
        ) from error
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(**browser_options(folder, visible))
    except Exception as error:
        playwright.stop()
        raise RuntimeError(
            f"Mochi's browser could not start. Run: python -m playwright install chromium. ({error})"
        ) from error
    context.add_init_script(LOOK_NORMAL)
    return playwright, context


def _normal_page(context):
    """The first tab, telling websites it is plain Chrome (a hidden browser says HeadlessChrome)."""
    page = context.pages[0] if context.pages else context.new_page()
    try:
        agent = page.evaluate("navigator.userAgent")
        if "Headless" in agent:
            session = context.new_cdp_session(page)
            session.send("Emulation.setUserAgentOverride", {"userAgent": agent.replace("HeadlessChrome", "Chrome")})
    except Exception:
        pass
    return page


def _close_browser(playwright, context) -> None:
    try:
        context.close()
    except Exception:
        pass
    try:
        playwright.stop()
    except Exception:
        pass


def setup(profile: Path | None = None, cancel: threading.Event | None = None) -> str:
    """Open a visible window. You log in and open Lora's chat. Closing the window saves that chat."""
    folder = profile or PROFILE
    _browser_for(folder).close()
    if not _GATE.acquire(blocking=False):
        raise RuntimeError("Mochi's browser is busy. Try again in a minute.")
    folder.mkdir(parents=True, exist_ok=True)
    cancel = cancel or threading.Event()
    saved = ""
    try:
        playwright, context = _browser(folder, visible=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(saved_chat(folder) or HOME, wait_until="domcontentloaded", timeout=60000)
            while not cancel.is_set():
                try:
                    pages = list(context.pages)
                except Exception:
                    break
                if not pages:
                    break
                for item in pages:
                    try:
                        if item.url.startswith("https://kindroid.ai") and item.locator("textarea").count():
                            saved = item.url.split("#")[0]
                    except Exception:
                        continue
                try:
                    pages[0].wait_for_timeout(400)
                except Exception:
                    break
        finally:
            _close_browser(playwright, context)
    finally:
        _GATE.release()
    if not saved:
        raise RuntimeError("The window closed before Lora's chat was open. Press Set up again.")
    remember_chat(saved, folder)
    return saved


# ---------------------------------------------------------------- the hidden browser, kept open


class HiddenBrowser:
    """Mochi's hidden browser, open for as long as the app is. It lives on its own thread,
    because the browser has to be used from the thread that opened it."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.jobs: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

    def _ask(self, job: str, *args, timeout: float = TAP_TIMEOUT):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name="mochi-browser")
            self.thread.start()
        answer: queue.Queue = queue.Queue()
        self.jobs.put((job, args, answer))
        try:
            result, error = answer.get(timeout=timeout)
        except queue.Empty as caught:
            raise RuntimeError("Mochi's browser took too long.") from caught
        if error is not None:
            raise error
        return result

    def tap(self, mochi: str, master: str, url: str, site: str = "auto", react_to: str = "") -> str:
        return self._ask("tap", mochi, master, url, site, react_to)

    def call(self, work, *args, timeout: float = TAP_TIMEOUT):
        """Run work(page, *args) in this browser (the page is Kindroid's, logged in)."""
        return self._ask("call", work, *args, timeout=timeout)

    def close(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            self._ask("close", timeout=30)

    def stop(self) -> None:
        """Close the browser and end its thread."""
        if self.thread is not None and self.thread.is_alive():
            self._ask("stop", timeout=30)
            self.thread.join(timeout=10)

    def _run(self) -> None:
        playwright = context = page = None
        while True:
            job, args, answer = self.jobs.get()
            try:
                if job in ("close", "stop"):
                    if context is not None:
                        _close_browser(playwright, context)
                    playwright = context = page = None
                    answer.put((None, None))
                    if job == "stop":
                        return
                    continue
                if context is None:
                    self.folder.mkdir(parents=True, exist_ok=True)
                    playwright, context = _browser(self.folder, visible=False)
                    page = _normal_page(context)
                if job == "call":
                    work, *rest = args
                    answer.put((work(page, *rest), None))
                else:
                    answer.put((_tap_on(page, *args), None))
            except Exception as caught:
                if page is not None:
                    _save_problem(page, self.folder)
                # Start fresh next time, in case the browser itself is what went wrong.
                if context is not None:
                    _close_browser(playwright, context)
                playwright = context = page = None
                answer.put((None, caught))


_BROWSERS: dict[Path, HiddenBrowser] = {}
_BROWSERS_LOCK = threading.Lock()


def _browser_for(folder: Path) -> HiddenBrowser:
    with _BROWSERS_LOCK:
        key = Path(folder).resolve()
        if key not in _BROWSERS:
            _BROWSERS[key] = HiddenBrowser(key)
        return _BROWSERS[key]


def shutdown() -> None:
    """Close Mochi's hidden browser. The app calls this when it closes."""
    for browser in list(_BROWSERS.values()):
        try:
            browser.stop()
        except Exception:
            pass


def tap(
    mochi: str = "Mochi", master: str = "Master", profile: Path | None = None, url: str = "", site: str = "auto",
    react_to: str = "",
) -> str:
    """Switch to Mochi, press the wand, send its line, switch back. Returns what Mochi said.

    site: "v1" or "v2" for the Kindroid site you use, or "auto" to check the page.
    react_to: the kin's name. While chatting as Mochi, Mochi also reacts to the kin's last message (new site only).
    """
    folder = profile or PROFILE
    url = url or saved_chat(folder)
    if not url:
        raise RuntimeError("Mochi's browser is not set up yet. Press Set up Mochi's browser in the Mochi panel.")
    if not _GATE.acquire(blocking=False):
        raise RuntimeError("Mochi's browser is busy.")
    try:
        return _browser_for(folder).tap(mochi, master, url, site, react_to)
    finally:
        _GATE.release()


def run(work, *args, profile: Path | None = None, wait: float = 120, timeout: float = TAP_TIMEOUT):
    """Run work(page, *args) in the app's hidden browser, after the side character's turn if one is going on."""
    folder = profile or PROFILE
    if not saved_chat(folder):
        raise RuntimeError("The app's browser is not set up yet. Run setup's Find once, and log in.")
    if not _GATE.acquire(timeout=wait):
        raise RuntimeError("The app's browser is busy. It tries again later.")
    try:
        return _browser_for(folder).call(work, *args, timeout=timeout)
    finally:
        _GATE.release()


def _chat_id(url: str) -> str:
    found = re.search(r"/chat/([^/?#]+)", url or "")
    return (found.group(1) if found else "").strip()


def chat_url_for(url: str, site: str) -> str:
    """Lora's chat on the chosen site: kindroid.ai/chat/<id>/ (v1) or kindroid.ai/v2/chat/<id>/ (v2)."""
    kin = _chat_id(url)
    if not kin or site not in ("v1", "v2"):
        return url
    return f"{HOME}/{'v2/' if site == 'v2' else ''}chat/{kin}/"


def _open_chat(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        _chat_box(page).wait_for(state="visible", timeout=45000)
    except Exception as error:
        raise RuntimeError(
            "Lora's chat did not open in Mochi's browser. It may have been logged out: "
            "press Set up Mochi's browser and log in again."
        ) from error


def _stay_on_chat(page, url: str) -> None:
    """Persona switching must not leave Lora's 1-on-1 chat for another kin's."""
    want = _chat_id(url)
    if want and _chat_id(page.url) != want:
        _open_chat(page, url)


def _tap_on(page, mochi: str, master: str, url: str, site: str = "auto", react_to: str = "") -> str:
    url = chat_url_for(url, site)
    _open_chat(page, url)
    before = current_persona(page)
    back_to = master if not before or before.lower() == mochi.lower() else before
    switch_persona(page, mochi, site=site)
    _stay_on_chat(page, url)
    page.wait_for_timeout(800)
    try:
        if react_to and page.locator(PERSONA_BUTTON).count():
            # Mochi reacts to the kin's last message while chatting as Mochi. Only a nice touch: never fails the tap.
            try:
                import kindroid_extras

                kindroid_extras.react_to_last(page, react_to)
            except Exception:
                pass
        return send_suggestion(page)
    finally:
        _stay_on_chat(page, url)
        switch_persona(page, back_to, site=site)


def _chat_box(page):
    labeled = page.locator(BOX)
    if labeled.count():
        return labeled.first
    return page.locator("textarea").first


def current_persona(page) -> str:
    """Who the chat says you are chatting as, from the "Chatting as ..." part under the message box."""
    try:
        return page.evaluate(
            """() => {
                // The new page: the button says "Switch persona (currently Narrator)".
                const button = document.querySelector("button[aria-label^='Switch persona']");
                const label = button ? (button.getAttribute('aria-label') || '') : '';
                const current = label.match(/currently\\s+(.+?)\\)?\\s*$/i);
                if (current) return current[1].trim();
                // The old page: a line "Chatting as Narrator".
                for (const el of document.querySelectorAll('p, span, div')) {
                    if (el.children.length) continue;
                    const found = (el.textContent || '').trim().match(/^chatting as\\s+(.+)$/i);
                    if (found) return found[1].trim();
                }
                return '';
            }"""
        )
    except Exception:
        return ""


def _same_person(shown: str, name: str) -> bool:
    """ "Mochi (Male)" and "Mochi" are the same person."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", shown.strip()).lower() == name.strip().lower()


def switch_persona(page, name: str, timeout: float = 20, site: str = "auto") -> None:
    """Click "Chatting as ...", then the Use button next to the name in the Change personas list.

    site "v2" or "v1" uses that Kindroid site's way. "auto" looks at the page to tell.
    """
    if _same_person(current_persona(page), name):
        return
    if site == "v2" or (site == "auto" and page.locator(PERSONA_BUTTON).count()):
        _switch_persona_v2(page, name, timeout)
        return
    label = page.get_by_text(re.compile(r"^\s*chatting as", re.IGNORECASE)).first
    try:
        label.click(timeout=10000)
    except Exception as error:
        raise RuntimeError(f'Could not open "Chatting as" under the message box. {SITE_HELP}') from error
    deadline = time.time() + timeout
    clicked = False
    while time.time() < deadline and not clicked:
        buttons = page.get_by_role("button", name=re.compile(r"^\s*use\s*$", re.IGNORECASE))
        for index in range(buttons.count()):
            button = buttons.nth(index)
            try:
                if not button.is_visible():
                    continue
                card = button.locator("xpath=ancestor::*[.//p][1]")
                heading = card.locator("p").first.inner_text(timeout=1000)
            except Exception:
                continue
            if _same_person(heading, name):
                button.click(timeout=3000)
                clicked = True
                break
        if not clicked:
            page.wait_for_timeout(300)
    if not clicked:
        _close_dialog(page)
        raise RuntimeError(f"Could not find {name} in the Change personas list.")
    while time.time() < deadline:
        if _same_person(current_persona(page), name):
            _close_dialog(page)
            return
        page.wait_for_timeout(300)
    _close_dialog(page)
    raise RuntimeError(f"Pressed Use for {name}, but the chat did not switch to {name}.")


_PICK_PERSONA = """(name) => {
    // The new page's Personas list: a button "Use master", "Use Mochi"... for each profile.
    const plain = text => text.replace(/\\s*\\([^)]*\\)\\s*$/, '').trim().toLowerCase();
    for (const button of document.querySelectorAll("button[aria-label^='Use ']")) {
        const who = (button.getAttribute('aria-label') || '').slice(4);
        if (button.getClientRects().length && plain(who) === plain(name)) {
            button.click();
            return true;
        }
    }
    return false;
}"""


def _switch_persona_v2(page, name: str, timeout: float) -> None:
    """The new page: the "Chatting as" button opens the Personas list, where each profile has a Use button."""
    try:
        page.locator(PERSONA_BUTTON).first.click(timeout=10000)
    except Exception as error:
        raise RuntimeError(f'Could not open "Chatting as" under the message box. {SITE_HELP}') from error
    deadline = time.time() + timeout
    clicked = False
    while time.time() < deadline and not clicked:
        try:
            clicked = bool(page.evaluate(_PICK_PERSONA, name))
        except Exception:
            clicked = False
        if not clicked:
            page.wait_for_timeout(300)
    if not clicked:
        _close_dialog(page)
        raise RuntimeError(f"Could not find {name} in the Personas list.")
    while time.time() < deadline:
        if _same_person(current_persona(page), name):
            _close_dialog(page)
            return
        page.wait_for_timeout(300)
    _close_dialog(page)
    raise RuntimeError(f"Pressed Use for {name}, but the chat did not switch to {name}.")


def _close_dialog(page) -> None:
    """Close the personas list if it is still open (the old page's dialog, or the new page's sheet)."""
    try:
        for where in ("[role='dialog'] button[aria-label='Close']", "[class*='sheet'] button[aria-label='Close']"):
            close = page.locator(where)
            if close.count() and close.first.is_visible():
                close.first.click(timeout=2000)
                page.wait_for_timeout(300)
                return
    except Exception:
        pass


def _press_wand(page) -> None:
    """Click the wand next to the message box. Kindroid redraws it, so a normal locator often goes stale."""
    try:
        page.locator(WAND).last.wait_for(state="visible", timeout=20000)
    except Exception as error:
        raise RuntimeError("Could not find the wand on the chat page.") from error
    for _ in range(8):
        try:
            if page.evaluate(
                """() => {
                    const wands = "button[aria-label='Suggest a message'], button[aria-label='Suggest message']";
                    const box = document.querySelector("textarea[aria-label='Message'], textarea[aria-label='Send message textarea']")
                        || document.querySelector('textarea');
                    const root = box && (box.closest('form') || box.closest('[role="group"]') || box.parentElement);
                    const wand = (root && root.querySelector(wands))
                        || [...document.querySelectorAll(wands)].find(b => b.getClientRects().length);
                    if (!wand || !wand.getClientRects().length) return false;
                    wand.click();
                    return true;
                }"""
            ):
                return
        except Exception:
            pass
        try:
            page.locator(WAND).last.click(force=True, timeout=3000)
            return
        except Exception:
            page.wait_for_timeout(200)
    raise RuntimeError("Could not press the wand on the chat page.")


def _clear_box(box) -> None:
    try:
        box.click(timeout=3000)
        box.press("Control+A")
        box.press("Backspace")
    except Exception:
        try:
            box.fill("")
        except Exception:
            pass


def _read_box(box) -> str:
    try:
        return box.input_value(timeout=500).strip()
    except Exception:
        return ""


WAND_START_SECONDS = 30  # how long the wand may take to start writing
WAND_FINISH_SECONDS = 45  # how long it may take to write the whole line once it started
WAND_STEADY_SECONDS = 3  # the line counts as finished when it has not changed for this long


def _wait_for_suggestion(box, page, start_within: float) -> str:
    """The wand's whole line. Empty when it never started writing.

    It writes a bit at a time, so a line is only taken once it has stopped changing. (Taking it
    earlier sent half lines like "Ruff! Blushing is".)
    """
    start_by = time.time() + max(0.5, start_within)
    finish_by = None
    text, steady_since = "", time.time()
    while True:
        now = _read_box(box)
        if now != text:
            text, steady_since = now, time.time()
            if text and finish_by is None:
                finish_by = time.time() + WAND_FINISH_SECONDS
        elif text and time.time() - steady_since >= WAND_STEADY_SECONDS:
            return text
        if finish_by is None and time.time() >= start_by:
            return ""
        if finish_by is not None and time.time() >= finish_by:
            raise RuntimeError("The wand kept writing and never finished its line, so it was not sent.")
        page.wait_for_timeout(250)


def send_suggestion(page, tries: int = 2, start_within: float = WAND_START_SECONDS) -> str:
    """Press the wand, wait until its line has finished writing, then press send."""
    box = _chat_box(page)
    _clear_box(box)
    text = ""
    for _ in range(tries):
        _press_wand(page)
        text = _wait_for_suggestion(box, page, start_within)
        if text:
            break
        _clear_box(box)
        page.wait_for_timeout(600)
    if not text:
        raise RuntimeError("The wand did not write a message.")
    # The send arrow only shows up once there is text, and it has no name: try it, then Enter, then the arrow.
    if not _click_send(page):
        box.press("Enter")
    if _box_cleared(box, page, 5):
        return text
    if _click_arrow_by_the_box(page) and _box_cleared(box, page, 10):
        return text
    raise RuntimeError("Pressed send, but the message stayed in the box.")


def _box_cleared(box, page, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if not box.input_value(timeout=500).strip():
                page.wait_for_timeout(1000)
                return True
        except Exception:
            pass
        page.wait_for_timeout(300)
    return False


def _click_arrow_by_the_box(page) -> bool:
    """The last button next to the message box that is not the wand, the plus, or voice mode."""
    try:
        return bool(page.evaluate(
            """() => {
                const box = document.querySelector("textarea[aria-label='Message'], textarea[aria-label='Send message textarea']")
                    || document.querySelector('textarea');
                if (!box) return false;
                let root = box;
                for (let i = 0; i < 5 && root.parentElement; i++) root = root.parentElement;
                // Never these: the wand, attachments, the persona switch, or voice (that would start recording).
                const skip = /suggest|addons|attachment|voice|record|persona|chatting/i;
                const buttons = [...root.querySelectorAll('button')].filter(b =>
                    !skip.test(b.getAttribute('aria-label') || '') && b.getClientRects().length);
                if (!buttons.length) return false;
                buttons[buttons.length - 1].click();
                return true;
            }"""
        ))
    except Exception:
        return False


def _click_send(page) -> bool:
    for name in SEND_NAMES:
        buttons = page.get_by_role("button", name=name)
        for index in range(buttons.count()):
            button = buttons.nth(index)
            try:
                if button.is_visible() and button.is_enabled():
                    button.click()
                    return True
            except Exception:
                continue
    return False


def _save_problem(page, folder: Path) -> None:
    """Keep what the page looked like when something went wrong, so it can be fixed."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "last-problem.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(folder / "last-problem.png"))
    except Exception:
        pass
