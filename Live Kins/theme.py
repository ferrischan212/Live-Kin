"""The app's colors: four ready-made themes, or your own. Saved in state.json (prefs "theme").

You pick seven colors. The rest (the text on a lit button, hover shades, tips) is worked out from them,
so it stays readable.
"""

from __future__ import annotations

# The colors you can change, in the order the Theme tab shows them.
ROLES = (
    ("bg", "Background"),
    ("panel", "Buttons and panels"),
    ("text", "Text"),
    ("muted", "Soft text and hints"),
    ("entry", "Boxes you type in"),
    ("accent", "Highlight (the lit button)"),
    ("kin", "Your kin's name"),
)

PRESETS = {
    "Default": {
        "bg": "#1c1424", "panel": "#2a2033", "text": "#f4eef8", "muted": "#b7a8c4",
        "entry": "#120d18", "accent": "#7c4dff", "kin": "#f4eef8",
    },
    "Midnight Ocean": {
        "bg": "#0b1726", "panel": "#15293f", "text": "#e6f1ff", "muted": "#9db6d3",
        "entry": "#06101c", "accent": "#1f6fc5", "kin": "#7fd4ff",
    },
    "Pink Sakura": {
        "bg": "#fff1f6", "panel": "#ffd6e5", "text": "#4a1f33", "muted": "#7f4a60",
        "entry": "#ffffff", "accent": "#c2185b", "kin": "#b0164f",
    },
    "Fall Leaves": {
        "bg": "#2b1a10", "panel": "#402818", "text": "#f7e8d6", "muted": "#d0ae8c",
        "entry": "#1d110a", "accent": "#b24a12", "kin": "#f2a54a",
    },
}
DEFAULT = "Default"


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _hex(red: float, green: float, blue: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(value))) for value in (red, green, blue)))


def mix(first: str, second: str, amount: float) -> str:
    """first, moved amount (0 to 1) of the way toward second."""
    a, b = _rgb(first), _rgb(second)
    return _hex(*(a[i] + (b[i] - a[i]) * amount for i in range(3)))


def luminance(color: str) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in _rgb(color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: str, second: str) -> float:
    """How easy one color is to read on the other: 1 (not at all) to 21 (black on white). 4.5 reads well."""
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def is_color(value) -> bool:
    text = str(value or "")
    return len(text) == 7 and text.startswith("#") and all(char in "0123456789abcdefABCDEF" for char in text[1:])


def full(colors: dict) -> dict:
    """The seven colors, plus the ones worked out from them."""
    base = {role: str(colors.get(role) or PRESETS[DEFAULT][role]).lower() for role, _label in ROLES}
    base = {role: value if is_color(value) else PRESETS[DEFAULT][role] for role, value in base.items()}
    dark = luminance(base["bg"]) < 0.4
    out = dict(base)
    # The lit button's text: white or near-black, whichever reads better on it.
    out["accent_text"] = "#ffffff" if contrast("#ffffff", base["accent"]) >= contrast("#141414", base["accent"]) else "#141414"
    # Hovered, the lit button moves away from its text color, so it reads even better.
    out["accent_active"] = mix(base["accent"], "#000000" if out["accent_text"] == "#ffffff" else "#ffffff", 0.15)
    out["panel_active"] = mix(base["panel"], base["text"], 0.12)
    out["disabled"] = mix(base["text"], base["panel"], 0.4)
    out["tip_bg"] = mix(base["panel"], base["accent"], 0.18)
    out["warn"] = "#ff8b7b" if dark else "#b3261e"
    out["you"] = mix(base["accent"], base["text"], 0.45)  # your lines in the Help chat
    # The highlight color as text (like the "Pick ..." labels in setup): moved toward the text color until it reads well.
    out["accent_ink"] = base["text"]
    for step in range(11):
        ink = mix(base["accent"], base["text"], step / 10)
        if contrast(ink, base["bg"]) >= 4.5:
            out["accent_ink"] = ink
            break
    return out


def problems(colors: dict) -> list[str]:
    """What would be hard to read with these colors. Empty when everything reads well."""
    palette = full(colors)
    checks = (
        ("text", "bg", 4.5, "Text on the background"),
        ("text", "panel", 4.5, "Text on buttons and panels"),
        ("text", "entry", 4.5, "Text in the boxes you type in"),
        ("muted", "bg", 3.0, "Soft text on the background"),
        ("kin", "bg", 3.0, "Your kin's name on the background"),
        ("accent_text", "accent", 4.5, "Text on the lit button"),
    )
    return [label for fore, back, need, label in checks if contrast(palette[fore], palette[back]) < need]


def load(prefs: dict | None) -> tuple[str, dict]:
    """The saved theme: its name ("Custom" for your own colors) and its seven colors."""
    saved = (prefs or {}).get("theme")
    if not isinstance(saved, dict):
        return DEFAULT, dict(PRESETS[DEFAULT])
    name = str(saved.get("name") or DEFAULT)
    colors = saved.get("colors") if isinstance(saved.get("colors"), dict) else {}
    if name in PRESETS and not colors:
        return name, dict(PRESETS[name])
    return name, {role: full(colors)[role] for role, _label in ROLES}


def saved(name: str, colors: dict) -> dict:
    """What goes into prefs["theme"]."""
    return {"name": name, "colors": {role: full(colors)[role] for role, _label in ROLES}}
