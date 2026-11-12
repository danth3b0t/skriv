"""Smart continuation: the spacing decision and the shadow buffer's key state machine."""

from __future__ import annotations

import pytest

from skriv_client.continuation import CLICK, KeyPress, ShadowBuffer, decide

CMD = frozenset({"cmd"})
CTRL = frozenset({"ctrl"})
ALT = frozenset({"alt"})
SHIFT = frozenset({"shift"})


@pytest.mark.parametrize(
    ("before", "after", "text", "expected"),
    [
        # real failure: typed + deleted back to "point.", the next dictation must be spaced
        ("... up until this point.", None, "And then", " And then"),
        # real failure: everything deleted (field start or unknown) -> no space
        ("", "", "Hello there", "Hello there"),
        (None, None, "Hello there", "Hello there"),
        ("Done.", "", "Next", " Next"),
        ("we went to the", "", "When", " when"),  # mid-sentence function word
        ("first, ", "", "But not", "but not"),  # after ", " : no space, still mid-sentence
        ("so", "", "I think", " I think"),  # never "I"
        ("we met", "", "Alice", " Alice"),  # names are not function words
        ("call the", "", "API", " API"),  # acronyms
        ("set", "", "TheValue", " TheValue"),  # identifiers
        ("see", "", "The_thing", " The_thing"),
        ("Hello ", "world", "big", "big "),  # before a word: trailing space
        ("Hello", " world", "big", " big"),
        ("x", ".", "y", " y"),
        ("call(", ")", "foo", "foo"),  # opening paren
        ('say "', "", "hi", "hi"),
        ("path/", "", "src", "src"),
        ("line\n", "", "So", "So"),
        ("word", "", ", and", ", and"),  # closing punctuation starts the text
        ("word", "", ".", "."),
        ("$ git commit -m", "", "fix", " fix"),
        ("$ ", "", "ls", "ls"),
        ("x", "y", "", ""),
    ],
)
def test_decide(before: str | None, after: str | None, text: str, expected: str):
    assert decide(before, after, text).text == expected


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Focus:
    def __init__(self) -> None:
        self.token = "w1"  # noqa: S105 - a window id
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.token


@pytest.fixture
def shadow() -> tuple[ShadowBuffer, Clock, Focus]:
    clock, focus = Clock(), Focus()
    buf = ShadowBuffer(60.0, focus, echo_s=0.5, clock=clock)
    buf.pasted("w1", "up until this point.")
    clock.now += 1.0  # past the echo window
    return buf, clock, focus


def keys(buf: ShadowBuffer, *names: str, mods: frozenset[str] = frozenset()) -> None:
    for k in names:
        buf.key(KeyPress("char", k, mods) if len(k) == 1 else KeyPress(k, "", mods))


def test_typing_and_backspacing_back_to_the_paste(shadow: tuple[ShadowBuffer, Clock, Focus]):
    buf, _, _ = shadow
    keys(buf, "space", "A", "n", "d", "backspace", "backspace", "backspace")
    assert buf.before("w1") == "up until this point. "
    keys(buf, "backspace")
    assert buf.before("w1") == "up until this point."


def test_backspacing_past_the_known_start_is_unknown(shadow: tuple[ShadowBuffer, Clock, Focus]):
    buf, _, _ = shadow
    keys(buf, *["backspace"] * len("up until this point."))
    assert buf.before("w1") is None  # all of the paste gone: what precedes it is unknown
    keys(buf, "x")
    assert buf.before("w1") == "x"
    keys(buf, "backspace", "backspace")  # below the known start
    keys(buf, "y")  # stays unknown
    assert buf.before("w1") is None


def test_word_backspace(shadow: tuple[ShadowBuffer, Clock, Focus]):
    buf, _, _ = shadow
    keys(buf, "backspace", mods=ALT)  # Option+Backspace: "point." goes
    assert buf.before("w1") == "up until this "
    keys(buf, "backspace", mods=CTRL)
    assert buf.before("w1") == "up until "
    keys(buf, "backspace", "backspace", mods=ALT)  # the first word may continue before
    assert buf.before("w1") is None


@pytest.mark.parametrize(
    ("press", "expected"),
    [
        (KeyPress("char", "u", CTRL), ""),
        (KeyPress("backspace", "", CMD), ""),
        (KeyPress("enter"), ""),
        (KeyPress("enter", "", SHIFT), ""),
        (KeyPress("char", "!", SHIFT), "up until this point.!"),
        (KeyPress("delete"), "up until this point."),  # forward delete
        (KeyPress("caps_lock"), "up until this point."),
        (KeyPress("left"), None),
        (KeyPress("up"), None),
        (KeyPress("home"), None),
        (KeyPress("end"), None),
        (KeyPress("page_up"), None),
        (KeyPress("tab"), None),
        (KeyPress("esc"), None),
        (KeyPress("char", "z", CMD), None),  # undo
        (KeyPress("char", "a", CTRL), None),
        (KeyPress("enter", "", CMD), None),
        (KeyPress("char", ""), None),  # dead key / no character
        (CLICK, None),
    ],
)
def test_key_effects(
    shadow: tuple[ShadowBuffer, Clock, Focus], press: KeyPress, expected: str | None
):
    buf, _, _ = shadow
    buf.key(press)
    assert buf.before("w1") == expected


def test_line_start_is_known_until_backspaced_into_the_previous_line(
    shadow: tuple[ShadowBuffer, Clock, Focus],
):
    buf, _, _ = shadow
    keys(buf, "enter", "h", "i", "backspace", "backspace")
    assert buf.before("w1") == ""
    keys(buf, "backspace")
    assert buf.before("w1") is None


def test_own_paste_chord_is_ignored_only_right_after_pasting(
    shadow: tuple[ShadowBuffer, Clock, Focus],
):
    buf, clock, _ = shadow
    buf.pasted("w1", " Next")
    keys(buf, "v")  # macOS: skriv's Cmd+V carries its flag only, no Cmd key event
    keys(buf, "v", mods=CTRL)  # X11: xdotool ctrl+v
    assert buf.before("w1") == "up until this point. Next"
    clock.now += 1.0
    keys(buf, "v", mods=CMD)  # the user's own paste
    assert buf.before("w1") is None


def test_keys_in_another_window_make_it_unknown(shadow: tuple[ShadowBuffer, Clock, Focus]):
    buf, _, focus = shadow
    focus.token = "w2"  # noqa: S105
    keys(buf, "x")
    focus.token = "w1"  # noqa: S105
    assert buf.before("w1") is None


def test_other_target_and_expiry(shadow: tuple[ShadowBuffer, Clock, Focus]):
    buf, clock, _ = shadow
    assert buf.before("w2") is None
    assert buf.before("") is None
    clock.now += 59.0  # activity keeps it alive
    keys(buf, "x")
    clock.now += 59.0
    assert buf.before("w1") == "up until this point.x"
    clock.now += 2.0
    assert buf.before("w1") is None


def test_pastes_append_in_the_same_window_and_reset_in_another(
    shadow: tuple[ShadowBuffer, Clock, Focus],
):
    buf, clock, _ = shadow
    buf.pasted("w1", " More")
    keys(buf, *["backspace"] * 5)  # back into the first paste
    assert buf.before("w1") == "up until this point."
    clock.now += 1.0
    buf.pasted("w2", "Elsewhere")
    assert (buf.before("w1"), buf.before("w2")) == (None, "Elsewhere")


def test_idle_buffer_does_not_query_focus(shadow: tuple[ShadowBuffer, Clock, Focus]):
    buf, _, focus = shadow
    buf.forget()
    keys(buf, "a", "b")
    assert focus.calls == 0
