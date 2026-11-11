"""Push-to-talk listener callbacks (no listener is started)."""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportAny=false

from __future__ import annotations

import pytest

from hark_client.continuation import CLICK, KeyPress

keyboard = pytest.importorskip("pynput.keyboard")
hotkey = pytest.importorskip("hark_client.hotkey")


def test_other_keys_carry_held_modifiers_and_the_hotkey_never_leaks():
    events: list[object] = []
    ptt = hotkey.PushToTalk(
        "alt_r", lambda: events.append("down"), lambda: events.append("up"),
        lambda: events.append("cancel"), events.append,
    )  # fmt: skip
    k, kc = keyboard.Key, keyboard.KeyCode.from_char
    ptt._press(k.alt_l)
    ptt._press(k.backspace)
    ptt._release(k.alt_l)
    ptt._press(kc("a"))
    ptt._press(k.ctrl_l)
    ptt._press(kc("u"))
    ptt._release(k.ctrl_l)
    ptt._press(k.alt_r)  # the hotkey, not a modifier
    ptt._press(k.space)  # typed while dictating still reaches the field
    ptt._release(k.alt_r)
    ptt._click(0, 0, None, True)
    assert events == [
        KeyPress("backspace", "", frozenset({"alt"})),
        KeyPress("char", "a"),
        KeyPress("char", "u", frozenset({"ctrl"})),
        "down",
        KeyPress("space"),
        "up",
        CLICK,
    ]
