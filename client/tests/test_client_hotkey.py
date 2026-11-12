"""Push-to-talk listener callbacks (no listener is started; actions run synchronously)."""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportAny=false

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from skriv_client.continuation import CLICK, KeyPress

if TYPE_CHECKING:
    from collections.abc import Callable

    from skriv_client.hotkey import PushToTalk

keyboard = pytest.importorskip("pynput.keyboard")
hotkey = pytest.importorskip("skriv_client.hotkey")
K = keyboard.Key


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def run_now(action: Callable[[], object]) -> None:
    action()


def make(
    events: list[object],
    clock: Clock | None = None,
    *,
    tap_s: float = 0.25,
    swallow_esc: bool = False,
) -> PushToTalk:
    def note(name: str) -> Callable[[], None]:
        return lambda: events.append(name)

    return hotkey.PushToTalk(
        "alt_r", note("down"), note("up"), note("cancel"), events.append,
        on_tap=note("tap"), tap_s=tap_s, swallow_esc=swallow_esc, clock=clock or Clock(),
        dispatch=run_now,
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
    ptt.recording(True)  # `skriv ctl start`
    ptt._press(K.alt_r)  # stops it
    ptt._release(K.alt_r)
    ptt.recording(False)
    ptt._press(K.esc)  # idle: Esc is an ordinary key
    assert events == ["down", "tap", "down", "up", "up", KeyPress("esc")]


def test_esc_cancels_any_recording_once_and_a_dictation_until_it_is_pasted():
    events: list[object] = []
    ptt = make(events)
    ptt._press(K.alt_r)
    ptt._release(K.alt_r)
    ptt._press(K.esc)  # hands-free
    ptt._press(K.alt_r)
    ptt._press(K.esc)  # held
    ptt._release(K.alt_r)  # nothing left to stop
    ptt.recording(True)
    ptt._press(K.esc)  # started by `skriv ctl`
    ptt._press(K.esc)  # nothing left to cancel
    ptt.finishing(True)  # stopped, being transcribed
    ptt._press(K.esc)
    ptt.finishing(False)  # pasted
    ptt._press(K.esc)
    esc = KeyPress("esc")
    assert events == ["down", "tap", "cancel", "down", "cancel", "cancel", esc, "cancel", esc]


def test_without_tap_ms_every_press_is_push_to_talk():
    events: list[object] = []
    ptt = make(events, tap_s=0.0)
    ptt._press(K.alt_r)
    ptt._release(K.alt_r)
    assert events == ["down", "up"]


EVENT = object()  # stands in for the CGEventRef the tap filters


def delivered(ptt: PushToTalk, key: object, *, down: bool = True) -> bool:
    """Report ``key`` to the listener, then ask the tap filter whether the app gets it."""
    (ptt._press if down else ptt._release)(key)
    return ptt._intercept(0, EVENT) is EVENT


def test_only_the_esc_that_cancels_a_recording_is_swallowed():
    events: list[object] = []
    ptt = make(events, swallow_esc=True)
    assert delivered(ptt, K.esc)  # idle: never suppressed
    assert delivered(ptt, K.esc, down=False)
    assert delivered(ptt, K.alt_r)  # the hotkey itself is not suppressed
    assert delivered(ptt, K.space)  # nor what is typed while recording
    assert not delivered(ptt, K.esc)  # cancels
    assert not delivered(ptt, K.esc)  # its auto-repeat
    assert delivered(ptt, K.alt_r, down=False)
    assert not delivered(ptt, K.esc, down=False)  # its release
    assert delivered(ptt, K.esc)  # the next Esc, idle again
    assert delivered(ptt, K.esc, down=False)
    ptt.recording(True)  # `skriv ctl start`
    assert not delivered(ptt, K.esc)
    esc = KeyPress("esc")
    assert events == [esc, "down", KeyPress("space"), "cancel", esc, "cancel"]


def test_esc_reaches_the_app_when_swallowing_is_off():
    events: list[object] = []
    ptt = make(events)
    ptt._press(K.alt_r)
    assert delivered(ptt, K.esc)
    assert delivered(ptt, K.esc, down=False)
    assert events == ["down", "cancel"]
