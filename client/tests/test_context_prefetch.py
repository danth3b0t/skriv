"""Background prefetch daemon with fake focus/terminal/repo/agent providers."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from hark_client.context import agents, engine, prefetch, vocab
from hark_client.context.agents import AgentSession, Group
from hark_client.context.focus import FocusResult
from hark_client.context.repo import RepoInfo
from hark_client.context.terminal import TerminalState
from hark_client.context.types import FocusInfo

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.context.screen import ScreenText

CWD = "/w"


class World:
    """The focused window and what the slow providers return; counts slow calls."""

    def __init__(self, tmp: Path) -> None:
        self.window = "1:10"
        self.cwd = CWD
        self.session_file = tmp / "session.jsonl"
        self.session_file.write_text("{}\n")
        self.builds = 0

    def focus(self, *, accessibility: bool = True) -> FocusResult:
        return FocusResult(FocusInfo(app="kitty", app_id="kitty", pid=5, is_terminal=True,
                                     window_id=self.window))  # fmt: skip

    def terminal(self, pid: int) -> TerminalState:
        return TerminalState(cwd=self.cwd, pid=pid, agent="pi")

    def repo(self, cwd: str) -> RepoInfo:
        self.builds += 1
        return RepoInfo(cwd, "main", "abc", (), (), ())

    def session(self, cwd: str, agent: str | None, started: float | None) -> AgentSession:
        groups = (Group(0, "warm `warmTerm`", ""),)
        return AgentSession("pi", str(self.session_file), cwd, 0.0, groups, 0.95)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = World(tmp_path)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.delenv("HARK_CONTEXT_CWD", raising=False)
    monkeypatch.setattr(prefetch, "get_focus", w.focus)
    monkeypatch.setattr(prefetch, "focus_token", lambda: w.window)
    monkeypatch.setattr(prefetch, "resolve", w.terminal)
    monkeypatch.setattr(prefetch, "repo_info", w.repo)
    monkeypatch.setattr(prefetch, "find_session", w.session)
    return w


def test_warm_entry_is_reused_until_a_validator_changes(world: World, tmp_path: Path):
    p = prefetch.Prefetcher(str(tmp_path), learn=False)
    first = p.refresh()
    assert first is not None
    assert (first.cwd, world.builds) == (CWD, 1)
    assert p.refresh() is not None
    assert world.builds == 1  # validated, not rebuilt
    world.session_file.write_text("{}\n{}\n")  # the agent wrote a turn
    p.refresh()
    assert world.builds == 2
    world.cwd = "/other"  # tmux pane switch inside the same window
    p.refresh()
    assert world.builds == 3
    assert p.lookup(world.window) is not None
    assert p.lookup("9:99") is None


def test_stale_entry_is_not_used(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    p = prefetch.Prefetcher(str(tmp_path), learn=False)
    p.refresh()
    monkeypatch.setattr(prefetch, "MAX_WARM_AGE_S", 0.0)
    assert p.lookup(world.window) is None


DAEMONS = {"hark-context-prefetch", "hark-context-learn", "hark-context-hypr"}


def wait_for[T](fn: Callable[[], T | None], timeout_s: float = 3.0) -> T:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if (value := fn()) is not None:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_daemon_follows_focus_learns_vocabulary_and_stops(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(prefetch, "POLL_S", 0.01)
    ended = time.time() - 3600
    old = agents.pi_session_dir(CWD) / "old.jsonl"
    old.parent.mkdir(parents=True)
    prompt = {"type": "message", "id": "u", "message": {"role": "user", "content": "`oldTerm`"}}
    old.write_text(json.dumps(prompt) + "\n")
    os.utime(old, (ended, ended))

    p = prefetch.start_prefetch(str(tmp_path))
    daemons = [t for t in threading.enumerate() if t.name in DAEMONS]
    try:
        assert prefetch.start_prefetch(str(tmp_path)) is p  # idempotent
        wait_for(lambda: prefetch.lookup("1:10"))
        world.window = "2:20"
        wait_for(lambda: prefetch.lookup("2:20"))
        store = vocab.store(str(tmp_path))
        wait_for(lambda: store.vocab().user.get("oldTerm"))
    finally:
        prefetch.stop_prefetch()
    assert prefetch.lookup("2:20") is None
    assert len(daemons) == 2  # focus loop and learner (no Hyprland here)
    assert not any(t.is_alive() for t in daemons)


def test_hyprland_events_kick_on_focus_and_active_title_only():
    sock_dir = Path(tempfile.mkdtemp(dir="/tmp"))  # AF_UNIX paths are short
    path = sock_dir / ".socket2.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    kick, stop = threading.Event(), threading.Event()
    watcher = threading.Thread(target=prefetch.watch_hyprland, args=(path, kick, stop))
    watcher.start()
    try:
        conn = server.accept()[0]
        with conn:
            conn.sendall(b"activewindowv2>>abc\n")
            assert kick.wait(2)
            kick.clear()
            conn.sendall(b"windowtitlev2>>other,spinner\nworkspace>>2\n")
            assert not kick.wait(0.2)
            conn.sendall(b"windowtitlev2>>abc,vim main.py\n")
            assert kick.wait(2)
    finally:
        stop.set()
        watcher.join(2)
        server.close()
        path.unlink()
        sock_dir.rmdir()
    assert not watcher.is_alive()


def test_collect_uses_the_warm_entry_and_skips_slow_providers(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    p = prefetch.Prefetcher(str(tmp_path), learn=False)
    warm = p.refresh()

    def lookup(window_id: str) -> prefetch.Warm | None:
        return warm

    def no_screen(focus: FocusInfo) -> ScreenText | None:
        return None

    def boom(*args: object) -> None:
        raise AssertionError("slow provider called")

    monkeypatch.setattr(prefetch, "lookup", lookup)

    monkeypatch.setattr(engine, "get_focus", world.focus)
    monkeypatch.setattr(engine, "screen_text", no_screen)
    for name in ("resolve", "repo_info", "find_session", "vocab_candidates"):
        monkeypatch.setattr(engine, name, boom)
    snap = engine.collect(1.0)
    assert (snap.prefetched, snap.cwd, snap.agent, snap.errors) == (True, CWD, "pi", ())
    assert "warmTerm" in [t.canonical for t in snap.terms]
