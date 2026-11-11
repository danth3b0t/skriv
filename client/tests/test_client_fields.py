"""Paste read-back: verdicts, the hard-deadline reader, and the AT-SPI search (fake nodes)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import pytest

from hark_client.atspi import read_focused
from hark_client.context import FocusInfo
from hark_client.fields import DeadlineReader, FieldText, verdict_for, verify

FOCUS = FocusInfo(app="Notes", app_id="com.apple.Notes", pid=42, window_id="42:7")


@pytest.mark.parametrize(
    ("field", "pasted", "verdict"),
    [
        (FieldText("text", "Dear Ann,\nI\u2019ll be there \u2014 at 5."), "I'll be there - at 5.", "landed"),
        (FieldText("text", "hello  world"), " hello world", "landed"),  # join space, spacing
        (FieldText("text", "something else"), "hello", "missing"),
        (FieldText("not_text"), "hello", "not_text"),
        (FieldText("secure"), "hello", "unknown"),
        (FieldText("too_long"), "hello", "unknown"),
        (FieldText("unavailable"), "hello", "unknown"),
        (FieldText("text", "anything"), "  ", "unknown"),
    ],
)  # fmt: skip
def test_verdict(field: FieldText, pasted: str, verdict: str):
    assert verdict_for(field, pasted) == verdict


class ScriptedReader:
    def __init__(self, *reads: FieldText) -> None:
        self.reads = list(reads)
        self.calls = 0

    def read(self, focus: FocusInfo) -> FieldText:
        self.calls += 1
        return self.reads.pop(0)


def test_missing_needs_every_read_to_miss():
    slept: list[float] = []
    late = ScriptedReader(FieldText("text", ""), FieldText("text", "hello"))
    assert verify(late, FOCUS, "hello", sleep=slept.append)[0] == "landed"
    assert slept == [0.15, 0.35]
    never = ScriptedReader(FieldText("text", "x"), FieldText("text", "x"))
    assert verify(never, FOCUS, "hello", sleep=lambda _: None)[0] == "missing"
    first = ScriptedReader(FieldText("text", "hello"))
    assert verify(first, FOCUS, "hello", sleep=lambda _: None)[0] == "landed"
    assert first.calls == 1


def test_deadline_reader_gives_up_on_a_hung_app_and_skips_while_it_hangs():
    release = threading.Event()
    calls: list[int] = []

    def hung(pid: int) -> FieldText:
        calls.append(pid)
        release.wait(5)
        return FieldText("text", "late")

    reader = DeadlineReader(hung, timeout_s=0.05)
    assert reader.read(FOCUS) == FieldText("unavailable")
    assert reader.read(FOCUS) == FieldText("unavailable")  # still hung: not queued
    assert calls == [42]
    release.set()
    assert reader.read(FocusInfo()) == FieldText("unavailable")  # no pid: no read


def test_deadline_reader_survives_exceptions():
    def broken(pid: int) -> FieldText:
        raise RuntimeError("AT-SPI bus gone")

    reader = DeadlineReader(broken)
    assert reader.read(FOCUS) == FieldText("unavailable")
    assert reader.read(FOCUS) == FieldText("unavailable")


@dataclass
class FakeNode:
    role_: str = "panel"
    states_: frozenset[str] = frozenset({"showing"})
    kids: list[FakeNode] = field(default_factory=list["FakeNode"])
    value: str | None = None
    pid_: int = 42
    reads: list[int] = field(default_factory=list[int])

    def pid(self) -> int:
        return self.pid_

    def role(self) -> str:
        return self.role_

    def states(self) -> frozenset[str]:
        return self.states_

    def children(self) -> list[FakeNode]:
        return self.kids

    def char_count(self) -> int | None:
        return None if self.value is None else len(self.value)

    def text(self, end: int) -> str:
        self.reads.append(end)
        return (self.value or "")[:end]


FOCUSED = frozenset({"showing", "focused", "editable"})


def app(*kids: FakeNode, pid: int = 42) -> FakeNode:
    return FakeNode("application", frozenset(), list(kids), pid_=pid)


def test_atspi_finds_the_focused_text_of_the_target_pid_only():
    entry = FakeNode("text", FOCUSED, value="hello world")
    other = app(FakeNode("text", FOCUSED, value="other app"), pid=7)
    target = app(FakeNode("frame", kids=[FakeNode("panel", kids=[entry])]))
    assert read_focused([other, target], 42, max_chars=100) == FieldText("text", "hello world")
    assert read_focused([other], 42, max_chars=100) == FieldText("unavailable")


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        (FakeNode("password text", FOCUSED, value="hunter2"), FieldText("secure")),
        (FakeNode("push button", FOCUSED), FieldText("not_text")),
        (FakeNode("text", FOCUSED, value="x" * 101), FieldText("too_long")),
    ],
)
def test_atspi_never_reads_secrets_or_huge_fields(node: FakeNode, expected: FieldText):
    assert read_focused([app(node)], 42, max_chars=100) == expected
    assert node.reads == []


def test_atspi_search_is_bounded_and_skips_hidden_subtrees():
    hidden = FakeNode("panel", frozenset(), [FakeNode("text", FOCUSED, value="stale")])
    assert read_focused([app(hidden)], 42, max_chars=100) == FieldText("unavailable")
    deep = FakeNode("text", FOCUSED, value="deep")
    chain = deep
    for _ in range(10):
        chain = FakeNode(kids=[chain])
    assert read_focused([app(chain)], 42, max_chars=100, max_nodes=5) == FieldText("unavailable")
    assert read_focused([app(chain)], 42, max_chars=100, max_nodes=50) == FieldText("text", "deep")
