"""Caret context providers against fake Accessibility / AT-SPI / tmux layers (any platform)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import pytest

from skriv_client.context import FocusInfo, caret
from skriv_client.context.caret import CaretContext, ax_caret, split_at_column, text_field_caret

OK, UNSUPPORTED, NO_VALUE, API_DISABLED = 0, -25205, -25212, -25211


@dataclass
class El:
    pid: int
    attrs: dict[str, object] = field(default_factory=dict[str, object])
    string_for_range: bool = True


def text_el(pid: int, text: str, loc: int, length: int = 0, **attrs: object) -> El:
    units = len(text.encode("utf-16-le")) // 2
    return El(
        pid,
        {
            "AXValue": text,
            "AXSelectedTextRange": (loc, length),
            "AXNumberOfCharacters": units,
            **attrs,
        },
    )


class FakeAx:
    """``AxApi``: (error, element) for the app-level and system-wide focused element."""

    def __init__(
        self, app: tuple[int, El | None], system: tuple[int, El | None] = (NO_VALUE, None)
    ) -> None:
        self.app, self.system = app, system

    def ax_focused_element(self, pid: int | None, timeout_s: float) -> tuple[int, object | None]:
        return self.app if pid is not None else self.system

    def ax_element_pid(self, element: object) -> int | None:
        assert isinstance(element, El)
        return element.pid

    def ax_attribute(
        self, element: object, name: str, timeout_s: float
    ) -> tuple[int, object | None]:
        assert isinstance(element, El)
        return (OK, element.attrs[name]) if name in element.attrs else (UNSUPPORTED, None)

    def ax_range_value(self, value: object) -> tuple[int, int] | None:
        assert isinstance(value, tuple)
        return value  # pyright: ignore[reportUnknownVariableType]

    def ax_string_for_range(
        self, element: object, location: int, length: int, timeout_s: float
    ) -> tuple[int, str | None]:
        assert isinstance(element, El)
        value = element.attrs["AXValue"]
        if not element.string_for_range or not isinstance(value, str):
            return UNSUPPORTED, None
        data = value.encode("utf-16-le")[2 * location : 2 * (location + length)]
        return OK, data.decode("utf-16-le")


SCROLLBACK = "Last login: Thu\n~/p $ ls\nREADME.md\n~/p $ git commit -m"
GHOSTTY = El(
    42, {"AXSelectedTextRange": (0, 0), "AXNumberOfCharacters": 3012, "AXValue": "x" * 3012}
)


@pytest.mark.parametrize(
    ("ax", "terminal", "expected"),
    [
        pytest.param(  # Terminal.app: real caret at the end of the prompt line
            FakeAx((OK, text_el(42, SCROLLBACK + "\n\n", len(SCROLLBACK)))),
            True,
            CaretContext(SCROLLBACK[-64:], "\n\n", "ax", trusted=True),
            id="terminal-app",
        ),
        pytest.param(  # Ghostty 1.4: text but the caret is always (0, 0)
            FakeAx((OK, GHOSTTY)),
            True,
            CaretContext(source="ax", detail="ax: terminal caret at (0, 0) with 3012 chars"),
            id="ghostty-untrusted",
        ),
        pytest.param(
            FakeAx((OK, text_el(42, "", 0))),
            False,
            CaretContext("", "", "ax", trusted=True),
            id="empty-field",
        ),
        pytest.param(  # caret at the start of a non-empty field is legitimate outside terminals
            FakeAx((OK, text_el(42, "Hello", 0))),
            False,
            CaretContext("", "He", "ax", trusted=True),
            id="field-start",
        ),
        pytest.param(  # a selection is replaced: context around it
            FakeAx((OK, text_el(42, "Hello world!", 6, 5))),
            False,
            CaretContext("Hello ", "!", "ax", trusted=True),
            id="selection",
        ),
        pytest.param(
            FakeAx((OK, text_el(42, "pw", 2, AXSubrole="AXSecureTextField"))),
            False,
            CaretContext(detail="ax: secure field"),
            id="secure",
        ),
        pytest.param(  # Chromium: no app-level answer, the system-wide one is the same app
            FakeAx((NO_VALUE, None), (OK, text_el(42, "Hi there", 2))),
            False,
            CaretContext("Hi", " t", "ax", trusted=True),
            id="system-wide-fallback",
        ),
        pytest.param(
            FakeAx((NO_VALUE, None), (OK, text_el(7, "Hi there", 2))),
            False,
            CaretContext(detail="ax: system-wide focused element belongs to pid 7, not 42"),
            id="system-wide-other-app",
        ),
        pytest.param(
            FakeAx((API_DISABLED, None)),
            False,
            CaretContext(detail="ax: no Accessibility permission"),
            id="no-permission",
        ),
        pytest.param(
            FakeAx((OK, El(42, {"AXRole": "AXButton"}))),
            False,
            CaretContext(detail=f"ax: focused element has no caret ({UNSUPPORTED})"),
            id="not-text",
        ),
    ],
)
def test_ax_caret(ax: FakeAx, terminal: bool, expected: CaretContext):
    assert ax_caret(ax, 42, terminal=terminal) == expected


def test_ax_caret_without_string_for_range_slices_the_value_in_utf16_units():
    el = text_el(42, "a\U0001f600b and more", 4)  # the emoji is two UTF-16 units
    el.string_for_range = False
    assert ax_caret(FakeAx((OK, el)), 42, terminal=False) == CaretContext(
        "a\U0001f600b", " a", "ax", trusted=True
    )


@dataclass
class Field:
    value: str
    caret_at: int
    secure: bool = False

    def caret(self) -> int:
        return self.caret_at

    def count(self) -> int:
        return len(self.value)

    def text(self, start: int, end: int) -> str:
        return self.value[start:end]


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        (Field("Hello world", 5), CaretContext("Hello", " w", "atspi", trusted=True)),
        (Field("Hello", 5), CaretContext("Hello", "", "atspi", trusted=True)),
        (Field("", 0), CaretContext("", "", "atspi", trusted=True)),
        (Field("secret", 6, secure=True), CaretContext(detail="atspi: secure field")),
        (Field("abc", 9), CaretContext(source="atspi", detail="atspi: caret 9 outside 0..3")),
        (None, CaretContext(detail="atspi: no focused text field")),
    ],
)
def test_atspi_text_field_caret(field: Field | None, expected: CaretContext):
    assert text_field_caret(field) == expected


@pytest.mark.parametrize(
    ("line", "column", "expected"),
    [
        ("$ git commit -m", 15, ("$ git commit -m", "")),
        ("$ ls", 5, ("$ ls ", "")),  # capture-pane dropped the trailing blank
        ("$ echo hi", 2, ("$ ", "ec")),
        ("\u65e5\u672c ok", 5, ("\u65e5\u672c ", "ok")),  # wide characters take two columns
    ],
)
def test_split_at_column(line: str, column: int, expected: tuple[str, str]):
    assert split_at_column(line, column) == expected


CLIENTS = "/dev/ttys001\t%3\t1\t15\t40\t0\n/dev/ttys007\t%9\t{flag}\t15\t46\t{mode}\n"


@pytest.mark.parametrize(
    ("flag", "mode", "tty", "expected"),
    [
        ("1", "0", "/dev/ttys007", CaretContext("$ git commit -m", "", "tmux", trusted=True)),
        ("0", "0", "/dev/ttys007", CaretContext(source="tmux", detail="tmux: cursor hidden (the app draws its own)")),
        ("1", "1", "/dev/ttys007", CaretContext(source="tmux", detail="tmux: pane in copy mode")),
        ("1", "0", "/dev/ttys099", CaretContext(detail="tmux: no client on /dev/ttys099")),
    ],
)  # fmt: skip
def test_tmux_pane_caret(flag: str, mode: str, tty: str, expected: CaretContext):
    calls: list[list[str]] = []

    def run(args: list[str]) -> str | None:
        calls.append(args)
        if "list-clients" in args:
            return CLIENTS.format(flag=flag, mode=mode)
        return "$ git commit -m\n"

    assert caret.tmux_pane_caret(["-L", "work"], tty, run) == expected
    if expected.trusted:
        assert calls[1] == ["-L", "work", "capture-pane", "-p", "-t", "%9", "-S", "46", "-E", "46"]


TMUX_OK = CaretContext("$ ls", "", "tmux", trusted=True)
TMUX_HIDDEN = CaretContext(source="tmux", detail="tmux: cursor hidden")


@pytest.mark.parametrize(
    ("ax", "tmux", "terminal", "expected"),
    [
        (FakeAx((OK, GHOSTTY)), TMUX_OK, True, TMUX_OK),
        (
            FakeAx((OK, GHOSTTY)), TMUX_HIDDEN, True,
            CaretContext(source="tmux", detail="ax: terminal caret at (0, 0) with 3012 chars; tmux: cursor hidden"),
        ),
        (FakeAx((OK, GHOSTTY)), None, True, CaretContext(source="ax", detail="ax: terminal caret at (0, 0) with 3012 chars")),
        (FakeAx((OK, text_el(42, "$ x", 3))), TMUX_OK, True, CaretContext("$ x", "", "ax", trusted=True)),
        (FakeAx((OK, text_el(42, "pw", 2, AXSubrole="AXSecureTextField"))), TMUX_OK, True, CaretContext(detail="ax: secure field")),
        (FakeAx((NO_VALUE, None)), TMUX_OK, False, CaretContext(detail=f"ax: no focused element (app {NO_VALUE}, system-wide {NO_VALUE})")),
    ],
)  # fmt: skip
def test_sources_in_order_on_macos(
    monkeypatch: pytest.MonkeyPatch,
    ax: FakeAx,
    tmux: CaretContext | None,
    terminal: bool,
    expected: CaretContext,
):
    monkeypatch.setattr(caret.sys, "platform", "darwin")
    monkeypatch.setattr(caret, "mac", ax)

    def fake_tmux(pid: int) -> CaretContext | None:
        return tmux

    monkeypatch.setattr(caret, "_tmux_caret", fake_tmux)
    monkeypatch.setattr(caret, "_WORKER", caret._Worker())  # pyright: ignore[reportPrivateUsage]
    assert caret.caret_context(FocusInfo(pid=42, is_terminal=terminal)) == expected


def test_a_hung_app_costs_at_most_the_deadline(monkeypatch: pytest.MonkeyPatch):
    release = threading.Event()

    def hang(focus: FocusInfo, pid: int) -> CaretContext:
        release.wait(5)
        return CaretContext("x", "", "ax", trusted=True)

    monkeypatch.setattr(caret, "_lookup", hang)
    monkeypatch.setattr(caret, "_WORKER", caret._Worker())  # pyright: ignore[reportPrivateUsage]
    try:
        started = time.monotonic()
        assert caret.caret_context(FocusInfo(pid=42), 0.05) == CaretContext(
            detail="caret: no answer within 50 ms"
        )
        assert time.monotonic() - started < 0.3
        # the stuck lookup is not stacked up behind
        assert caret.caret_context(FocusInfo(pid=42), 0.05).detail == (
            "caret: previous lookup still running (skipped)"
        )
    finally:
        release.set()
    assert caret.caret_context(FocusInfo()).detail == "caret: focused app unknown"
