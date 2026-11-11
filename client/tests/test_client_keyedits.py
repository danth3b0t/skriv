"""Terminal edit replay (keysyms -> edited text), then the same correction diff as GUI fields."""

from __future__ import annotations

import pytest

from hark_client.continuation import CLICK, KeyPress
from hark_client.keyedits import Key, from_press, reconstruct
from hark_client.learn import corrections

CTRL = frozenset({"ctrl"})
ALT = frozenset({"alt"})


def keys(*names: str | Key) -> list[Key]:
    out: list[Key] = []
    for n in names:
        if isinstance(n, Key):
            out.append(n)
        elif len(n) > 1 and n[0] == "'":  # 'text' = type each character
            out.extend(Key({" ": "space", "-": "minus", ".": "period"}.get(c, c)) for c in n[1:-1])
        else:
            out.append(Key(n))
    return out


def test_retyping_a_misheard_word_at_the_prompt_is_learned():
    pasted = "run cube control get pods"
    # move left over " get pods", delete "cube control" with alt+BackSpace twice, type kubectl
    events = [
        *keys(*["Left"] * len(" get pods")),
        Key("BackSpace", ALT),
        Key("BackSpace", ALT),
        *keys("'kubectl'", "Shift_L", "Return"),
    ]
    edited = reconstruct(pasted, events)
    assert edited == "run kubectl get pods"
    assert corrections(pasted, edited) == [("cube control", "kubectl")]


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        (keys("BackSpace", "BackSpace", "'ts'"), "open store.ts"),  # "store.js" -> "store.ts"
        (keys("Home", "End", "'!'"), "open store.js!"),  # Home then End is a no-op
        (keys("Left", "Left", "Delete", "'t'", "Right", "Right"), "open store.ts"),
        (keys("Left", Key("b", CTRL), Key("f", CTRL), "Right"), "open store.js"),
        ([Key("w", CTRL), *keys("'main.py'")], "open main.py"),
        ([Key("h", CTRL), Key("e", CTRL)], "open store.j"),
        (keys("'x'", "Escape"), None),  # vi mode: unknown from here on
        (keys("Tab"), None),  # completion inserts unknown text
        (keys("Up"), None),  # history
        ([Key("u", CTRL)], None),  # kill to line start: beyond the span
        (keys("Home", "'#'"), None),  # edit left of the span
        (keys(*["BackSpace"] * 15), None),  # past the start of the span
        ([Key("w", CTRL), Key("w", CTRL)], None),  # ctrl+w reaching the span start
        ([Key("v", CTRL)], None),  # pasting something else
    ],
)
def test_replay(events: list[Key], expected: str | None):
    assert reconstruct("open store.js", events) == expected


def test_events_after_submit_are_ignored():
    assert reconstruct("ls", keys("'a'", "Return", "'zzz'")) == "lsa"


@pytest.mark.parametrize(
    ("press", "key"),
    [
        (KeyPress("char", "\x17", frozenset({"ctrl"})), Key("w", CTRL)),  # Ctrl+W as a C0 char
        (KeyPress("char", "W", frozenset({"ctrl", "shift"})), Key("w", CTRL | {"shift"})),
        (KeyPress("char", "A", frozenset({"shift"})), Key("A", frozenset({"shift"}))),
        (KeyPress("backspace", "", frozenset({"alt"})), Key("BackSpace", ALT)),
        (KeyPress("enter"), Key("Return")),
        (KeyPress("char", "z", frozenset({"cmd"})), Key("z", frozenset({"super"}))),
        (CLICK, Key("Pointer_Button1")),
    ],
)
def test_listener_key_presses_map_to_keysyms(press: KeyPress, key: Key):
    assert from_press(press) == key
