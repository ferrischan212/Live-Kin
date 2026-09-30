"""Start.bat runs this before the app. It installs what the app needs, only when something is missing.

  - the Python parts in requirements.txt (Pillow, tzdata, playwright)
  - the app's own browser (Chromium), for setup's Find and the side character
Exit code 0: everything is there. 1: the app cannot run (the reason is printed).
2: the app can run, but the browser could not be installed (the reason is printed).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements.txt"
# What each part in requirements.txt is called when Python imports it.
MODULES = {"Pillow": "PIL", "tzdata": "tzdata", "playwright": "playwright"}


def missing_parts() -> list[str]:
    return [part for part, module in MODULES.items() if importlib.util.find_spec(module) is None]


def browser_folder() -> Path:
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    return Path(custom) if custom and custom != "0" else Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"


def have_browser() -> bool:
    """Playwright's Chromium is downloaded. (The app uses the full browser, not the smaller headless shell.)"""
    return any(path.is_dir() for path in browser_folder().glob("chromium-*"))


def run(what: str, command: list[str]) -> bool:
    print(f"\n{what}...")
    return subprocess.run(command).returncode == 0


def main() -> int:
    if importlib.util.find_spec("tkinter") is None:
        print("Your Python is missing a part the app needs (tkinter, for windows).")
        print("Reinstall Python from python.org with every box ticked, then double-click Start.bat again.")
        return 1
    missing = missing_parts()
    if missing:
        print("First start: getting the app ready. This takes a few minutes, please wait...")
        if not run("Installing the app's parts", [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)]):
            print("\nCouldn't finish. Check your internet, then double-click Start.bat again.")
            return 1
        importlib.invalidate_caches()
        still = missing_parts()
        if still:
            print("\nStill missing: " + ", ".join(still) + ". Double-click Start.bat again.")
            return 1
    if not have_browser():
        print("Getting the app's browser ready (for Find and the side character). This takes a minute...")
        if not run("Downloading the browser", [sys.executable, "-m", "playwright", "install", "chromium"]) or not have_browser():
            print("\nCouldn't get the browser. The app still opens, but Find won't work yet.")
            print("Double-click Start.bat again later to try once more.")
            return 2  # not a reason to stop: setup can be typed by hand
    return 0


if __name__ == "__main__":
    sys.exit(main())
