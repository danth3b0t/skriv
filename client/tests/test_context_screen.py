"""Screen text providers against fake tmux / Accessibility / AT-SPI layers (any platform)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from hark_client.context import FocusInfo, screen
from hark_client.context.rank import Origin
from hark_client.context.screen import ScreenText, ax_screen, field_screen, tmux_screen

from .test_context_caret import OK, El, FakeAx, text_el

CLIENT = "/dev/ttys004"


class FakeTmux:
    def __init__(self, command: str, grid: list[str], cursor_y: int) -> None:
        self.calls: list[list[str]] = []
        self.command, self.grid, self.cursor_y = command, grid, cursor_y

    def __call__(self, args: list[str]) -> str | None:
        self.calls.append(args)
        if "list-clients" in args:
            height = len(self.grid)
            return f"/dev/other\t%1\t0\t5\tzsh\n{CLIENT}\t%7\t{self.cursor_y}\t{height}\t{self.command}\n"
        return "\n".join(["old `scrollTerm` line", *[""] * 40, *self.grid]) + "\n"


def terms(text: ScreenText) -> dict[str, float]:
    return {c.canonical: c.score for c in screen.candidates(text)}


def test_tmux_reads_the_clients_pane_and_scores_rows_by_cursor_distance():
    tmux = FakeTmux("pi", ["see `nearTerm` here", "$ "], cursor_y=1)
    got = tmux_screen(["-L", "work"], CLIENT, tmux)
    assert tmux.calls[1] == ["-L", "work", "capture-pane", "-p", "-J", "-t", "%7", "-S", "-200"]
    assert (got.origin, got.source) == (Origin.TERMINAL_TEXT, "tmux")
    scores = terms(got)
    assert scores["nearTerm"] == 1.0
    assert scores["scrollTerm"] == round(math.exp(-35 / 40), 4)  # rows 0-7 vs cursor row 42


def test_tmux_skips_credential_programs_and_password_prompts():
    sudo = FakeTmux("sudo", ["`fooBar`"], cursor_y=0)
    assert tmux_screen([], CLIENT, sudo).chunks == ()
    assert len(sudo.calls) == 1  # never captured
    prompt = FakeTmux("zsh", ["`fooBar`", "Enter passphrase for key:"], cursor_y=1)
    assert tmux_screen([], CLIENT, prompt).detail == "tmux: password prompt"


def test_ax_terminal_with_a_caret_reads_bounded_text_around_it():
    text = "old `farTerm` " + "x " * 3000 + "$ git add `nearTerm`"
    got = ax_screen(FakeAx((OK, text_el(42, text, len(text)))), 42, terminal=True)
    assert got.origin is Origin.TERMINAL_TEXT
    scores = terms(got)
    assert scores["nearTerm"] == 1.0
    assert "farTerm" not in scores  # beyond MAX_CHARS before the caret


def test_ax_terminal_without_a_caret_is_discounted_buffer_tail():
    text = "`headTerm` " + "y " * 2000 + "`tailTerm`"
    units = len(text)
    ghostty = El(
        42, {"AXSelectedTextRange": (0, 0), "AXNumberOfCharacters": units, "AXValue": text}
    )
    got = ax_screen(FakeAx((OK, ghostty)), 42, terminal=True)
    assert got.origin is Origin.BUFFER
    assert set(terms(got)) == {"tailTerm"}


def test_ax_field_text_is_focused_text_and_secure_fields_are_never_read():
    field = FakeAx((OK, text_el(42, "Ping `fieldTerm` now", 5)))
    assert terms(ax_screen(field, 42, terminal=False)) == {"fieldTerm": 1.0}
    secure = FakeAx((OK, text_el(42, "`hunter2Pass`", 3, AXSubrole="AXSecureTextField")))
    assert ax_screen(secure, 42, terminal=False).chunks == ()


@dataclass
class Field:
    value: str
    at: int
    secure: bool = False

    def caret(self) -> int:
        return self.at

    def count(self) -> int:
        return len(self.value)

    def text(self, start: int, end: int) -> str:
        return self.value[start:end]


def test_atspi_field_text_and_password_fields():
    assert terms(field_screen(Field("call `fooBar` ok", 3))) == {"fooBar": 1.0}
    assert field_screen(Field("`fooBar`", 3, secure=True)).chunks == ()


def test_deny_listed_apps_are_not_read():
    vault = FocusInfo(app="1Password", app_id="com.1password.1password", pid=7)
    assert screen.screen_text(vault) is None
