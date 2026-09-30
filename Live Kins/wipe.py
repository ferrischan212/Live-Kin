"""Start over: remove everything this copy has saved about you, so it can be set up again or shared.

Removes your keys (the DeepSeek key can stay), names and profile IDs, the world and memories, the log,
the Kindroid login in the app's browser, and your pictures. The app's own files stay.
In the app: Schedule > Wipe everything and start over.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIVATE_FILES = (
    ".env", "config.json", "state.json", "state.json.tmp", "state.backup.json", "state.backup.json.tmp", "background.png",
)
PRIVATE_FOLDERS = ("kindroid-browser", "backgrounds", "__pycache__")
KEY_NAMES = ("DEEPSEEK_API_KEY", "KINDROID_API_KEY", "KINDROID_AI_ID")


def deepseek_key(root: Path = ROOT) -> str:
    path = root / ".env"
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        key, _, value = raw.partition("=")
        if key.strip() == "DEEPSEEK_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def _remove(path: Path) -> bool:
    """True when it is gone. A file the browser is still closing gets a few more tries."""
    for _ in range(10):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return True
        except OSError:
            time.sleep(0.5)
    return not path.exists()


def wipe(keep_deepseek: bool = True, root: Path = ROOT) -> list[str]:
    """Remove it all. Returns what could not be removed (still open somewhere), empty when everything went."""
    key = deepseek_key(root) if keep_deepseek else ""
    # The app's diary is kept open while it runs: let go of it first.
    for handler in list(logging.getLogger("lora.simulation").handlers):
        handler.close()
        logging.getLogger("lora.simulation").removeHandler(handler)
    targets = [root / name for name in PRIVATE_FILES + PRIVATE_FOLDERS] + sorted(root.glob("simulation.log*"))
    left = [path.name for path in targets if not _remove(path)]
    for name in KEY_NAMES:
        os.environ.pop(name, None)
    if key:
        (root / ".env").write_text(f"DEEPSEEK_API_KEY={key}\n", encoding="utf-8")
        os.environ["DEEPSEEK_API_KEY"] = key
    try:
        import cast

        cast.reset_cache()
    except Exception:
        pass
    return left
