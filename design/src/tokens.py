"""Derive skriv UI tokens from Omarchy theme colors.toml files and check WCAG contrast.

Every derived token is a formula over Omarchy theme variables (colors.toml + the shell.toml.tpl
defaults), so the QML implementation can compute the same values at runtime.
"""
import colorsys
import json
import sys
import tomllib
from pathlib import Path

SRC = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/skriv-design-research/omarchy/themes")  # omacom/omarchy@quattro themes/
THEMES = ["tokyo-night", "catppuccin-latte", "catppuccin", "gruvbox", "rose-pine",
          "flexoki-light", "matte-black", "nord", "everforest", "kanagawa", "ristretto", "white",
          "solitude", "lumon", "vantablack", "retro-82", "ethereal", "miasma"]


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb2hex(c):
    return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02x}" for v in c)


def mix(a, b, t):
    """a over b with alpha t (t=1 -> a)."""
    a, b = hex2rgb(a), hex2rgb(b)
    return rgb2hex(tuple(x * t + y * (1 - t) for x, y in zip(a, b)))


def lum(h):
    def ch(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in hex2rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def qt_darker(h, f):
    r, g, b = hex2rgb(h)
    hh, s, v = colorsys.rgb_to_hsv(r, g, b)
    return rgb2hex(colorsys.hsv_to_rgb(hh, s, v / f))


def secondary_text(fg, bg, target=4.5):
    """Strongest blend toward the background that still meets `target` (text.secondary)."""
    best = fg
    for i in range(0, 61):
        t = 1 - i / 100
        c = mix(fg, bg, t)
        if contrast(c, bg) >= target:
            best = c
        else:
            break
    return best


def ensure(color, bg, fg, target=3.0):
    """Blend `color` toward fg (away from bg) until it reaches `target` against bg."""
    for i in range(0, 101):
        c = mix(fg, color, i / 100)
        if contrast(c, bg) >= target:
            return c
    return fg


def derive(name):
    c = tomllib.loads((SRC / name / "colors.toml").read_text())
    bg, fg = c["background"], c["foreground"]
    accent = c.get("accent", c.get("blue"))
    red = c.get("red", "#a55555")
    tok = {
        "theme": name,
        "mode": c.get("mode", "dark"),
        # --- straight from colors.toml / Commons.Color
        "bg": bg,
        "fg": fg,
        "accent": accent,
        "urgent": red,
        "muted": c.get("muted", fg),
        "green": c.get("green", accent),
        "yellow": c.get("yellow", fg),
        "dark_bg": c.get("dark_background", bg),
        "lighter_bg": c.get("lighter_background", bg),
        "selection": c.get("selection", mix(fg, bg, 0.18)),
        # --- [controls] defaults composited over the card background
        "fill_normal": mix(fg, bg, 0.04),
        "fill_hover": mix(fg, bg, 0.08),
        "fill_selected": mix(fg, bg, 0.18),
        "fill_pressed": mix(fg, bg, 0.22),
        "border_normal": mix(fg, bg, 0.40),
        "border_hover": mix(fg, bg, 0.25),
        # --- surfaces
        "osd_bg": bg,  # alpha .97 in QML; opaque in mocks
        "scrim": bg,  # alpha .5
        # --- text
        "text_secondary": secondary_text(fg, bg, 4.5),
        "text_dim_omarchy": qt_darker(fg, 1.55),  # Omarchy panels' `dim` (Qt.darker(fg, 1.55))
        "on_accent": bg,
        "accent_fill": mix(accent, bg, 0.18),  # Style.selectedAccentFill
        "urgent_fill": mix(red, bg, 0.16),
        "green_fill": mix(c.get("green", accent), bg, 0.16),
        "yellow_fill": mix(c.get("yellow", fg), bg, 0.16),
        "glow_mid": mix(accent, bg, 0.45),
        "glow_soft": mix(accent, bg, 0.18),
        "live_glow": mix(red, bg, 0.35),
    }
    # Contrast-solved inks (icons, 2px strokes, focus ring: 1.4.11 needs 3:1) and borders.
    tok["accent_ink"] = ensure(accent, bg, fg, 3.0)
    tok["live_ink"] = ensure(red, bg, fg, 3.0)
    tok["ok_ink"] = ensure(tok["green"], bg, fg, 3.0)
    tok["warn_ink"] = ensure(tok["yellow"], bg, fg, 3.0)
    tok["border_control"] = ensure(tok["border_normal"], bg, fg, 3.0)
    tok["accent_text"] = ensure(accent, bg, fg, 4.5)
    checks = {
        "fg/bg": contrast(fg, bg),
        "text_secondary/bg": contrast(tok["text_secondary"], bg),
        "text_dim_omarchy/bg": contrast(tok["text_dim_omarchy"], bg),
        "accent/bg": contrast(accent, bg),
        "urgent/bg": contrast(red, bg),
        "green/bg": contrast(tok["green"], bg),
        "yellow/bg": contrast(tok["yellow"], bg),
        "on_accent/accent": contrast(bg, accent),
        "border_normal/bg": contrast(tok["border_normal"], bg),
        "fg/fill_selected": contrast(fg, tok["fill_selected"]),
        "warn_ink/bg": contrast(tok["warn_ink"], bg),
        "ok_ink/bg": contrast(tok["ok_ink"], bg),
        "live_ink/bg": contrast(tok["live_ink"], bg),
        "border_control/bg": contrast(tok["border_control"], bg),
    }
    tok["contrast"] = checks
    return tok


def main():
    out = {t: derive(t) for t in THEMES}
    Path(sys.argv[1]).write_text(json.dumps(out, indent=2))
    cols = list(next(iter(out.values()))["contrast"].keys())
    print("| theme | mode | " + " | ".join(cols) + " |")
    print("|" + "---|" * (len(cols) + 2))
    for t, v in out.items():
        print(f"| {t} | {v['mode']} | " + " | ".join(str(v['contrast'][k]) for k in cols) + " |")


main()
