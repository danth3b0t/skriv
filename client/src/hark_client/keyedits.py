"""Terminal fallback for correction learning: replay key events on the pasted text.

Terminals expose no editable field (AT-SPI; macOS AX gives the whole scrollback), so the edits
after a paste can only be reconstructed from key events (ADR-002 "X"). The events come from the
daemon's hotkey listener, the same stream as the smart-continuation shadow buffer
(:func:`from_press`; macOS and X11); the Hyprland event source is designed in ADR-002, not
implemented. This module is the replay: given what was pasted (cursor at its end, as
after a paste at a shell/agent prompt) and the events of the watch window, it returns the
edited text, or None as soon as an event cannot be modelled exactly: history (Up/Down),
completion (Tab), kill-line, Escape (vi mode), modified keys other than the readline editing
chords, or any edit to the left of the pasted span. Never a guess.

Events are X keysym names (``a``, ``A``, ``space``, ``minus``, ``BackSpace``), i.e. after the
keyboard layout was applied, so no US layout is assumed. Events stay in memory; only the
derived correction pairs from :func:`hark_client.learn.corrections` are ever stored.

**Untested on real hardware**: unit-tested only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from hark_client.continuation import KeyPress

_PUNCT = {
    "space": " ", "minus": "-", "period": ".", "comma": ",", "slash": "/", "underscore": "_",
    "colon": ":", "semicolon": ";", "apostrophe": "'", "quotedbl": '"', "equal": "=",
    "plus": "+", "at": "@", "numbersign": "#", "dollar": "$", "percent": "%",
    "ampersand": "&", "asterisk": "*", "parenleft": "(", "parenright": ")",
    "bracketleft": "[", "bracketright": "]", "braceleft": "{", "braceright": "}",
    "less": "<", "greater": ">", "question": "?", "exclam": "!", "backslash": "\\",
    "bar": "|", "grave": "`", "asciitilde": "~", "asciicircum": "^",
}  # fmt: skip
_MODIFIER_KEYS = frozenset(
    f"{m}_{side}" for m in ("Shift", "Control", "Alt", "Super", "Meta", "Hyper") for side in "LR"
) | {"Caps_Lock", "ISO_Level3_Shift"}
SUBMIT = frozenset({"Return", "KP_Enter"})
_CTRL = frozenset({"ctrl"})
_ALT = frozenset({"alt"})


@dataclass(frozen=True, slots=True)
class Key:
    keysym: str
    mods: frozenset[str] = frozenset()  # "ctrl", "alt", "super"; shift is already in the keysym


# pynput Key names (``continuation.KeyPress.key``) -> X keysyms; other names pass through and
# make the replay give up (arrows up/down, Tab, Esc and the like are not modelled anyway).
_PYNPUT_KEYSYMS = {
    "backspace": "BackSpace", "delete": "Delete", "enter": "Return", "space": "space",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down", "home": "Home", "end": "End",
    "tab": "Tab", "esc": "Escape", "caps_lock": "Caps_Lock", "click": "Pointer_Button1",
}  # fmt: skip
_PYNPUT_MODS = {"cmd": "super", "ctrl": "ctrl", "alt": "alt", "shift": "shift"}


def from_press(press: KeyPress) -> Key:
    """A key press from the daemon's hotkey listener (the shadow buffer's stream) as a Key.

    pynput reports the character after the layout was applied, like a keysym; with Ctrl held
    it may be the control character (``\\x17`` for Ctrl+W), mapped back to its letter.
    """
    mods = frozenset(_PYNPUT_MODS[m] for m in press.mods if m in _PYNPUT_MODS)
    if press.key != "char":
        return Key(_PYNPUT_KEYSYMS.get(press.key, press.key), mods)
    char = press.char
    if "ctrl" in mods and len(char) == 1 and ord(char) < 0x20:  # C0 controls
        char = chr(ord(char) + 0x60)
    return Key(char.lower() if "ctrl" in mods else char, mods)


def _char(keysym: str) -> str | None:
    if len(keysym) == 1 and keysym.isprintable():
        return keysym
    return _PUNCT.get(keysym)


def _word_start(buf: str, cur: int, *, alnum: bool) -> int:
    """readline: ctrl+w deletes back to whitespace; alt/ctrl+BackSpace to a non-alphanumeric."""

    def boundary(c: str) -> bool:
        return not c.isalnum() if alnum else c.isspace()

    i = cur
    while i > 0 and boundary(buf[i - 1]):
        i -= 1
    while i > 0 and not boundary(buf[i - 1]):
        i -= 1
    return i


def reconstruct(pasted: str, events: Iterable[Key]) -> str | None:  # noqa: PLR0911, PLR0912 - one flat key table
    buf, cur = pasted, len(pasted)
    at_line_start = False  # after Home / ctrl+a: left of the pasted span, by an unknown amount
    for ev in events:
        k, mods = ev.keysym, ev.mods - {"shift"}
        if k in _MODIFIER_KEYS:
            continue
        if k in SUBMIT and not mods:
            return buf
        if (k == "End" and not mods) or (k == "e" and mods == _CTRL):
            at_line_start, cur = False, len(buf)
            continue
        if (k == "Home" and not mods) or (k == "a" and mods == _CTRL):
            at_line_start = True
            continue
        if at_line_start:
            return None  # an edit or move from an unknown position
        if (k == "BackSpace" and not mods) or (k == "h" and mods == _CTRL):
            if cur == 0:
                return None
            buf, cur = buf[: cur - 1] + buf[cur:], cur - 1
        elif k == "Delete" and not mods:
            buf = buf[:cur] + buf[cur + 1 :]
        elif (k == "Left" and not mods) or (k == "b" and mods == _CTRL):
            if cur == 0:
                return None
            cur -= 1
        elif (k == "Right" and not mods) or (k == "f" and mods == _CTRL):
            cur = min(len(buf), cur + 1)
        elif (k == "w" and mods == _CTRL) or (k == "BackSpace" and mods in (_ALT, _CTRL)):
            start = _word_start(buf, cur, alnum=k == "BackSpace")
            if start == 0:
                return None  # no word boundary inside the span: readline may delete further left
            buf, cur = buf[:start] + buf[cur:], start
        elif not mods and (c := _char(k)) is not None:
            buf, cur = buf[:cur] + c + buf[cur:], cur + 1
        else:
            return None  # Tab, Up/Down, Escape, ctrl+u/k/r, ...
    return buf
