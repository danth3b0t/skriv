"""The pill's colours and sizes from the running Omarchy theme (design/SPEC.md §3).

skriv defines no colours: everything comes from ``colors.toml`` of the current theme, and the
inks are solved per theme to WCAG contrast (text 4.5:1, glyphs 3:1) because some theme colours
fall short (yellow and green on light themes). Same formulas as ``design/src/tokens.py`` and the
shell plugin's ``Model.js``. Without an Omarchy theme the shell's own fallback colours apply.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

# Omarchy's Commons.Color defaults (shell/Commons/Color.qml) for a missing theme
FALLBACK = {"background": "#101315", "foreground": "#cacccc", "accent": "#cacccc", "red": "#a55555"}
BASE_SIZE = 12  # Omarchy's [font] base-size default: the type scale's rem


@dataclass(frozen=True, slots=True)
class Palette:
    bg: str
    fg: str
    text2: str  # secondary text: as dim as 4.5:1 allows
    border: str  # the pill hairline: fg @ 0.25 ([controls] hover-cursor border)
    accent: str  # accent-ink
    live: str
    ok: str
    warn: str


def _rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    if len(h) not in (6, 8):
        raise ValueError(f"not a colour: #{h}")
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def _hex(c: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02x}" for v in c)


def mix(a: str, b: str, t: float) -> str:
    """``a`` over ``b`` at alpha ``t`` (1 = a)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(
        (ra[0] * t + rb[0] * (1 - t), ra[1] * t + rb[1] * (1 - t), ra[2] * t + rb[2] * (1 - t))
    )


def _luminance(h: str) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(v) for v in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def secondary(fg: str, bg: str, target: float = 4.5) -> str:
    """The strongest blend of fg toward bg (at most 60 %) that still meets ``target``."""
    best = fg
    for i in range(61):
        c = mix(fg, bg, 1 - i / 100)
        if contrast(c, bg) < target:
            break
        best = c
    return best


def ink(color: str, bg: str, fg: str, target: float = 3.0) -> str:
    """``color`` blended toward fg (away from bg) until it reaches ``target`` against bg."""
    for i in range(101):
        c = mix(fg, color, i / 100)
        if contrast(c, bg) >= target:
            return c
    return fg


def palette(colors: dict[str, str]) -> Palette:
    """From a colors.toml table (missing keys fall back like Omarchy's shell does)."""
    bg = _hex(_rgb(colors.get("background", FALLBACK["background"])))  # #rrggbb from here on
    fg = _hex(_rgb(colors.get("foreground", FALLBACK["foreground"])))
    accent = colors.get("accent", colors.get("blue", FALLBACK["accent"]))
    return Palette(
        bg=bg,
        fg=fg,
        text2=secondary(fg, bg),
        border=mix(fg, bg, 0.25),
        accent=ink(accent, bg, fg),
        live=ink(colors.get("red", FALLBACK["red"]), bg, fg),
        ok=ink(colors.get("green", accent), bg, fg),
        warn=ink(colors.get("yellow", fg), bg, fg),
    )


def _table(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def load(theme_dir: Path, user_shell: Path) -> tuple[Palette, int]:
    """The palette of ``theme_dir/colors.toml`` and the font base size (``[font] base-size``
    of the user's shell.toml, else the theme's, else 12: `omarchy display text size`)."""
    colors = {k: v for k, v in _table(theme_dir / "colors.toml").items() if isinstance(v, str)}
    try:
        pal = palette(colors)
    except ValueError:  # a malformed colour: the whole theme is suspect
        pal = palette({})
    size = BASE_SIZE
    for shell in (theme_dir / "shell.toml", user_shell):
        font = _table(shell).get("font")
        if isinstance(font, dict):
            value = cast("dict[str, object]", font).get("base-size")
            if isinstance(value, int | float) and not isinstance(value, bool) and value >= 1:
                size = round(value)
    return pal, size
