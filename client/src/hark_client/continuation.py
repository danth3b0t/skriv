"""Smart continuation: spacing and casing of a dictation from the text around the caret.

Right before pasting, the pipeline asks ``context.caret_context()`` for the text before and
after the caret. When that is unavailable or untrusted (Ghostty exposes no caret, TUIs hide
the cursor), it falls back to the :class:`ShadowBuffer`: hark's own last paste plus the keys
typed into the same window since, as seen by the hotkey listener. :func:`decide` then picks

- a leading space iff the character before the caret is known, is not whitespace, not an
  opening character, and the text does not start with closing punctuation;
- a lower-case first word iff the caret is mid-sentence (after a letter/digit or ``, ; :``,
  ignoring spaces) and that word is a common function word ("And", "The", ...; never "I",
  acronyms, identifiers or names);
- a trailing space iff the character after the caret is a letter or digit.

Unknown context counts as the start of a field: no leading space, nothing lower-cased.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Key events this soon after hark pasted can be its own synthetic paste chord.
PASTE_ECHO_S = 0.5
ECHO_PER_KEY_S = 0.01  # ...and a little longer per Backspace hark sends (undo)
SHADOW_CHARS = 64

OPENING = frozenset("([{\"'`/-\u201c\u2018\u00ab")  # no space after these
CLOSING = frozenset(".,;:!?)]}%\u2026\u201d\u2019\u00bb")  # text starting with these: no space
MID_SENTENCE = frozenset(",;:")
FUNCTION_WORDS = frozenset(
    {
        "About", "Also", "Although", "An", "And", "Are", "As", "At", "Because", "But", "By",
        "Can", "Could", "Did", "Do", "Does", "For", "From", "Had", "Has", "Have", "He", "Her",
        "His", "How", "If", "In", "Into", "Is", "It", "Its", "Just", "Maybe", "My", "Nor", "Not",
        "Now", "Of", "On", "Or", "Our", "Plus", "She", "Should", "Since", "So", "Than", "That",
        "The", "Their", "Them", "Then", "There", "These", "They", "This", "Those", "Though", "To",
        "Until", "Was", "We", "Were", "What", "When", "Where", "Which", "While", "Who", "Why",
        "With", "Would", "Yet", "You", "Your",
    }
)  # fmt: skip
_FIRST_WORD = re.compile(r"[A-Z][a-z]*(?!\w)")


@dataclass(frozen=True, slots=True)
class Spacing:
    text: str  # what to paste
    leading: bool = False
    trailing: bool = False
    lowercased: bool = False


def decide(before: str | None, after: str | None, text: str) -> Spacing:
    """Spacing/casing of ``text`` pasted between ``before`` and ``after`` (None: unknown)."""
    if not text:
        return Spacing(text)
    prev = before[-1:] if before else ""
    leading = bool(prev) and not (
        prev.isspace() or prev in OPENING or text[0].isspace() or text[0] in CLOSING
    )
    anchor = before.rstrip(" \t")[-1:] if before else ""
    lowercased = False
    if anchor and (anchor.isalnum() or anchor in MID_SENTENCE):
        m = _FIRST_WORD.match(text)
        if m is not None and m.group() in FUNCTION_WORDS:
            text, lowercased = text[0].lower() + text[1:], True
    nxt = after[:1] if after else ""
    trailing = nxt.isalnum() and not text[-1].isspace()
    return Spacing(
        (" " if leading else "") + text + (" " if trailing else ""), leading, trailing, lowercased
    )


# -- shadow buffer -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KeyPress:
    """One key press (or click) from the global listener, platform-neutral.

    key: "char" (``char`` holds it) or a pynput Key name ("backspace", "enter", "left",
    "space", ...), or "click" for a mouse click. mods: held modifiers among
    "cmd", "ctrl", "alt", "shift".
    """

    key: str
    char: str = ""
    mods: frozenset[str] = frozenset()


CLICK = KeyPress("click")


class Edit(Enum):
    APPEND = auto()
    BACKSPACE = auto()
    WORD_BACKSPACE = auto()
    LINE_START = auto()  # the caret is at a known line start
    PASTE_CHORD = auto()  # Cmd/Ctrl(+Shift)+V: hark's own within the echo window
    IGNORE = auto()  # does not move the caret or change text before it
    UNKNOWN = auto()


# forward Delete changes only the text after the caret
_IGNORED_KEYS = frozenset({"caps_lock", "num_lock", "scroll_lock", "delete"})


def classify(press: KeyPress) -> tuple[Edit, str]:  # noqa: PLR0911 - one rule per key class
    """What ``press`` does to the text before the caret: (edit, appended char)."""
    cmd, ctrl = "cmd" in press.mods, "ctrl" in press.mods
    key, char = press.key, press.char
    if key == "backspace":
        if cmd:
            return Edit.LINE_START, ""
        return (Edit.WORD_BACKSPACE if ctrl or "alt" in press.mods else Edit.BACKSPACE), ""
    if key == "enter":
        return (Edit.UNKNOWN if cmd or ctrl else Edit.LINE_START), ""
    if key == "char" and char.lower() in {"v", "\x16"} and (cmd or ctrl):
        return Edit.PASTE_CHORD, ""
    if key == "char" and ctrl and char.lower() in {"u", "\x15"}:
        return Edit.LINE_START, ""
    if cmd or ctrl:
        return Edit.UNKNOWN, ""
    if key == "space":
        return Edit.APPEND, " "
    if key == "char" and len(char) == 1 and char.isprintable():
        return Edit.APPEND, char
    if key in _IGNORED_KEYS or key.startswith("media_"):
        return Edit.IGNORE, ""
    return Edit.UNKNOWN, ""  # arrows, Home/End, PageUp/Down, Tab, Esc, F-keys, clicks, ...


def _joins(ch: str) -> bool:
    """``ch`` forms one user-perceived character with a neighbour."""
    cp = ord(ch)
    return (
        unicodedata.category(ch) in {"Mn", "Mc", "Me", "Cf"}  # marks; ZWJ, tags and other format
        or 0xFE00 <= cp <= 0xFE0F  # variation selectors
        or 0xE0100 <= cp <= 0xE01EF
        or 0x1F3FB <= cp <= 0x1F3FF  # emoji skin-tone modifiers
        or 0x1F1E6 <= cp <= 0x1F1FF  # regional indicators (flags are pairs)
        or 0x1160 <= cp <= 0x11FF  # Hangul medial/final jamo
        or 0xD7B0 <= cp <= 0xD7FF
    )


def backspaces(text: str) -> int | None:
    """Backspace presses that delete exactly ``text`` right before the caret: one per character.
    None if some characters combine with a neighbour (accents as separate marks, emoji sequences,
    flags, conjoining jamo, CR LF): apps delete those per cluster or per code point, so the count
    would be a guess."""
    if "\r\n" in text or any(_joins(ch) for ch in text):
        return None
    return len(text)


def _drop_word(text: str) -> str:
    """Option/Ctrl+Backspace: trailing non-word characters, then the word before them."""
    i = len(text)
    while i and not text[i - 1].isalnum():
        i -= 1
    while i and text[i - 1].isalnum():
        i -= 1
    return text[:i]


class ShadowBuffer:
    """The text before the caret as far as hark knows it: its last paste plus what the user
    typed into the same window since. Thread-safe (paste thread vs. listener threads).

    ``focus_token`` (``context.focus_token``) is checked on every tracked key: a key that went
    to another window means focus moved, so the caret is unknown from then on.
    """

    def __init__(
        self,
        window_s: float,
        focus_token: Callable[[], str],
        *,
        echo_s: float = PASTE_ECHO_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window_s = window_s
        self.echo_s = echo_s
        self._focus_token = focus_token
        self._clock = clock
        self._lock = threading.Lock()
        self._target = ""  # "" = nothing known
        self._text = ""
        self._anchored = False  # _text starts at a line start
        self._at = 0.0  # last change
        self._pasted_at = float("-inf")
        self._last = ""  # hark's last paste, while nothing has changed the text since
        self._echo_backspaces = 0  # hark's own Backspaces (undo) still to be seen
        self._echo_until = float("-inf")

    def forget(self) -> None:
        with self._lock:
            self._target = ""

    def pasted(self, target: str, text: str) -> None:
        """Call right before hark pastes ``text`` (spacing included) into window ``target``."""
        now = self._clock()
        with self._lock:
            if not target:
                self._target = ""
                return
            if self._target == target and now - self._at <= self.window_s:
                self._set(self._text + text, anchored=self._anchored)
            else:
                self._set(text, anchored=False)
            self._target, self._at, self._pasted_at = target, now, now
            self._last = text

    def last_insertion(self, target: str) -> str | None:
        """hark's last paste into ``target`` if the caret is known to be right after it: no key,
        click or focus change since (as far as the listener saw), else None."""
        with self._lock:
            if not target or target != self._target or self._clock() - self._at > self.window_s:
                return None
            return self._last or None

    def retract(self, target: str, text: str) -> bool:
        """Call right before hark deletes its last paste ``text`` with ``len(text)`` Backspaces;
        those are then not taken for the user's. False: ``text`` is not the last insertion."""
        now = self._clock()
        with self._lock:
            if not text or target != self._target or self._last != text:
                return False
            n = len(text)
            # _text ends with the paste; shorter (truncated) means what is left is unknown
            self._text = self._text[:-n] if n < len(self._text) else ""
            self._last = ""
            self._echo_backspaces = n
            self._echo_until = now + self.echo_s + n * ECHO_PER_KEY_S
            self._at = now
            return True

    def before(self, target: str) -> str | None:
        """Known text before the caret in ``target`` ("" = line start), else None."""
        with self._lock:
            if not target or target != self._target or self._clock() - self._at > self.window_s:
                return None
            return self._text if self._text or self._anchored else None

    def key(self, press: KeyPress) -> None:
        """A key press or click seen by the global listener (any thread)."""
        with self._lock:
            if not self._target:
                return
            now = self._clock()
            if now - self._at > self.window_s:
                self._target = ""
                return
            edit, char = classify(press)
            echo = now - self._pasted_at < self.echo_s
            # hark's Cmd+V is posted with flags only, so the listener may see a bare "v"
            if edit is Edit.IGNORE or (echo and press.char.lower() in {"v", "\x16"}):
                return
            if edit is Edit.BACKSPACE and self._echo_backspaces and now < self._echo_until:
                self._echo_backspaces -= 1  # one of hark's own (undo)
                return
            target = self._target
        if self._focus_token() != target:
            self.forget()
            return
        with self._lock:
            if self._target != target:
                return
            self._last = ""
            self._apply(edit, char)
            self._at = now

    def _apply(self, edit: Edit, char: str) -> None:
        if edit is Edit.APPEND:
            self._set(self._text + char, anchored=self._anchored)
        elif edit in {Edit.BACKSPACE, Edit.WORD_BACKSPACE}:
            if not self._text:  # into text hark never saw
                self._target = ""
                return
            rest = self._text[:-1] if edit is Edit.BACKSPACE else _drop_word(self._text)
            if not rest and edit is Edit.WORD_BACKSPACE and not self._anchored:
                self._target = ""  # the word may continue before what hark saw
                return
            self._text = rest
        elif edit is Edit.LINE_START:
            self._set("", anchored=True)
        else:  # UNKNOWN, PASTE_CHORD outside the echo window (the user's own paste)
            self._target = ""

    def _set(self, text: str, *, anchored: bool) -> None:
        self._text = text[-SHADOW_CHARS:]
        self._anchored = anchored and len(text) <= SHADOW_CHARS
