"""Push-to-talk listener callbacks (no listener is started; actions run synchronously)."""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportAny=false

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from hark_client.continuation import CLICK, KeyPress

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.hotkey import PushToTalk

keyboard = pytest.importorskip("pynput.keyboard")
hotkey = pytest.importorskip("hark_client.hotkey")
K = keyboard.Key


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def run_now(action: Callable[[], object]) -> None:
    action()


def make(events: list[object], clock: Clock | None = None, *, tap_s: float = 0.25) -> PushToTalk:
    def note(name: str) -> Callable[[], None]:
        return lambda: events.append(name)

    return hotkey.PushToTalk(
        "alt_r", note("down"), note("up"), note("cancel"), events.append,
        on_tap=note("tap"), tap_s=tap_s, clock=clock or Clock(), dispatch=run_now,
    )  # fmt: skip


def test_other_keys_carry_held_modifiers_and_the_hotkey_never_leaks():
    events: list[object] = []
    clock = Clock()
    ptt = make(events, clock)
    kc = keyboard.KeyCode.from_char
    ptt._press(K.alt_l)
    ptt._press(K.backspace)
    ptt._release(K.alt_l)
    ptt._press(kc("a"))
    ptt._press(K.ctrl_l)
    ptt._press(kc("u"))
    ptt._release(K.ctrl_l)
    ptt._press(K.alt_r)  # the hotkey, not a modifier
    ptt._press(K.alt_r)  # auto-repeat
    ptt._press(K.space)  # typed while dictating still reaches the field
    clock.t += 1
    ptt._release(K.alt_r)
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


def test_a_tap_records_hands_free_until_the_next_press_and_a_hold_is_push_to_talk():
    events: list[object] = []
    clock = Clock()
    ptt = make(events, clock)
    ptt._press(K.alt_r)
    clock.t += 0.249
    ptt._release(K.alt_r)  # a tap: keeps recording
    assert ptt.mode == "hands_free"
    clock.t += 5
    ptt._press(K.alt_r)  # the next press stops at once
    clock.t += 1
    ptt._release(K.alt_r)  # ...and its release does nothing
    ptt._press(K.alt_r)
    clock.t += 0.25  # held exactly tap_ms: push-to-talk
    ptt._release(K.alt_r)
    assert events == ["down", "tap", "up", "down", "up"]
    assert ptt.mode == "idle"


def test_recordings_ended_or_started_elsewhere_reset_the_tap_state():
    events: list[object] = []
    clock = Clock()
    ptt = make(events, clock)
    ptt._press(K.alt_r)
    ptt._release(K.alt_r)  # hands-free...
    ptt.recording(False)  # ...until max duration stopped it
    ptt._press(K.alt_r)  # a fresh start, not a stop
    clock.t += 1
    ptt._release(K.alt_r)
    ptt.recording(True)  # `hark ctl start`
    ptt._press(K.alt_r)  # stops it
    ptt._release(K.alt_r)
    ptt.recording(False)
    ptt._press(K.esc)  # idle: Esc is an ordinary key
    assert events == ["down", "tap", "down", "up", "up", KeyPress("esc")]


def test_esc_cancels_any_recording_once():
    events: list[object] = []
    ptt = make(events)
    ptt._press(K.alt_r)
    ptt._release(K.alt_r)
    ptt._press(K.esc)  # hands-free
    ptt._press(K.alt_r)
    ptt._press(K.esc)  # held
    ptt._release(K.alt_r)  # nothing left to stop
    ptt.recording(True)
    ptt._press(K.esc)  # started by `hark ctl`
    ptt._press(K.esc)  # nothing left to cancel
    assert events == ["down", "tap", "cancel", "down", "cancel", "cancel", KeyPress("esc")]


def test_without_tap_ms_every_press_is_push_to_talk():
    events: list[object] = []
    ptt = make(events, tap_s=0.0)
    ptt._press(K.alt_r)
    ptt._release(K.alt_r)
    assert events == ["down", "up"]
