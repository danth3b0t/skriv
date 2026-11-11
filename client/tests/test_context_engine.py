import json
import time
from collections.abc import Callable
from typing import cast

import pytest

from hark_client.context import TermSource, engine
from hark_client.context.__main__ import main
from hark_client.context.agents import AgentSession, Turn
from hark_client.context.focus import FocusResult
from hark_client.context.repo import RepoInfo
from hark_client.context.terminal import TerminalState
from hark_client.context.types import FocusInfo

TERMINAL = FocusInfo(app="kitty", app_id="kitty", title="nvim", pid=4242, is_terminal=True)


def session(confidence: float) -> AgentSession:
    turns = (Turn("assistant", "Changed `agentTerm`."),)
    return AgentSession("pi", "/s.jsonl", "/w", 0.0, turns, (), confidence)


def sessions(confidence: float) -> Callable[[str, str | None, float | None], AgentSession]:
    return found(session(confidence))


def found(s: AgentSession) -> Callable[[str, str | None, float | None], AgentSession]:
    return lambda cwd, agent, started: s


def fake_repo(cwd: str) -> RepoInfo:
    return RepoInfo(cwd, "main", "abc", ("src/repoTerm.py",), ("src/recentTerm.py",), ())


def fake_terminal(pid: int) -> TerminalState:
    return TerminalState(cwd="/w", pid=pid, agent="pi")


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "get_focus", lambda: FocusResult(TERMINAL, "see `selTerm`"))
    monkeypatch.setattr(engine, "resolve", fake_terminal)
    monkeypatch.setattr(engine, "find_session", sessions(0.95))
    monkeypatch.setattr(engine, "repo_info", fake_repo)


@pytest.mark.usefixtures("providers")
def test_terms_are_ranked_by_source():
    snap = engine.collect(1.0)
    order = [t.canonical for t in snap.terms if t.canonical.endswith("Term")]
    assert order == ["selTerm", "agentTerm", "recentTerm", "repoTerm"]
    assert (snap.cwd, snap.repo_root, snap.agent, snap.errors) == ("/w", "/w", "pi", ())


@pytest.mark.usefixtures("providers")
def test_older_agent_turns_contribute_with_lower_weight(monkeypatch: pytest.MonkeyPatch):
    turns = (
        Turn("assistant", "Use `newTerm`, key sk-abcdefghijklmnopqrstuv."),
        Turn("user", "what about `userTerm`?"),
        Turn("assistant", "Compare `oldTerm` and Parakeet."),
    )
    monkeypatch.setattr(
        engine, "find_session", found(AgentSession("pi", "/s", "/w", 0.0, turns, (), 0.95))
    )
    snap = engine.collect(1.0)
    weight = {t.canonical: t.weight for t in snap.terms if t.source is TermSource.AGENT_SESSION}
    assert weight["newTerm"] > weight["userTerm"] > weight["oldTerm"] == weight["Parakeet"]
    assert snap.agent_last_reply == "Use `newTerm`, key [redacted]."
    assert not any("sk-" in t.canonical for t in snap.terms)


@pytest.mark.usefixtures("providers")
def test_unconfident_session_is_not_used(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(engine, "find_session", sessions(0.5))
    snap = engine.collect(1.0)
    assert snap.agent is None
    assert snap.agent_last_reply is None
    assert all(t.source is not TermSource.AGENT_SESSION for t in snap.terms)


@pytest.mark.usefixtures("providers")
def test_max_terms_truncates():
    assert len(engine.collect(1.0, max_terms=2).terms) == 2


@pytest.mark.usefixtures("providers")
def test_slow_provider_is_cut_off_at_the_deadline(monkeypatch: pytest.MonkeyPatch):
    def slow_repo(cwd: str) -> RepoInfo | None:
        time.sleep(1.0)
        return None

    monkeypatch.setattr(engine, "repo_info", slow_repo)
    started = time.monotonic()
    snap = engine.collect(0.1)
    assert time.monotonic() - started < 0.4
    assert [e.split(":")[0] for e in snap.errors] == ["repo"]
    assert snap.agent == "pi"  # other providers still contribute


@pytest.mark.usefixtures("providers")
def test_failing_provider_is_recorded_not_raised(monkeypatch: pytest.MonkeyPatch):
    def boom() -> FocusResult:
        raise RuntimeError("no display")

    monkeypatch.setattr(engine, "get_focus", boom)
    snap = engine.collect(1.0)
    assert snap.errors == ("focus: RuntimeError: no display",)
    assert snap.terms == ()


def test_scrub_redacts_tokens():
    assert engine.scrub("key sk-abcdefghijklmnopqrstuv ok") == "key [redacted] ok"


@pytest.mark.usefixtures("providers")
def test_debug_cli_prints_json(capsys: pytest.CaptureFixture[str]):
    assert main(["--diagnostics", "--canonicalize", "fix sel term"]) == 0
    out = cast("dict[str, dict[str, dict[str, object]]]", json.loads(capsys.readouterr().out))
    assert out["canonicalized"]["text"] == "fix selTerm"
    assert out["diagnostics"]["terminal"]["agent"] == "pi"
