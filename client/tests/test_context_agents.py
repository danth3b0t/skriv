"""Agent session readers against small fixtures shaped like real pi / Claude Code / Codex files."""

import json
import os
import time
from pathlib import Path

import pytest

from hark_client.context import agents

CWD = "/home/u/projects/app"


def write_jsonl(path: Path, entries: list[dict[str, object]], mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture(autouse=True)
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    return tmp_path


def pi_msg(eid: str, parent: str | None, role: str, content: object) -> dict[str, object]:
    return {
        "type": "message",
        "id": eid,
        "parentId": parent,
        "timestamp": "2026-11-12T10:00:00.000Z",
        "message": {"role": role, "content": content},
    }


def pi_session(homes: Path, name: str = "s1.jsonl", mtime: float | None = None) -> Path:
    # a1 <- u2 <- a2 (abandoned branch);  a1 <- u3 <- a3 (tool) <- r3 <- a4 (leaf)
    entries: list[dict[str, object]] = [
        {"type": "session", "version": 3, "id": "sess", "cwd": CWD},
        pi_msg("u1", None, "user", [{"type": "text", "text": "hi"}]),
        pi_msg("a1", "u1", "assistant", [{"type": "text", "text": "first reply"}]),
        pi_msg("u2", "a1", "user", [{"type": "text", "text": "try x"}]),
        pi_msg(
            "a2",
            "u2",
            "assistant",
            [
                {"type": "toolCall", "name": "edit", "arguments": {"path": "src/old.ts"}},
                {"type": "text", "text": "abandoned reply"},
            ],
        ),
        pi_msg("u3", "a1", "user", [{"type": "text", "text": "rename the store"}]),
        pi_msg(
            "a3",
            "u3",
            "assistant",
            [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "toolCall", "name": "read", "arguments": {"path": "src/useStore.ts"}},
            ],
        ),
        pi_msg("r3", "a3", "toolResult", [{"type": "text", "text": "file body"}]),
        pi_msg("a4", "r3", "assistant", [{"type": "text", "text": "Renamed `useSessionStore`."}]),
    ]
    return write_jsonl(agents.pi_session_dir(CWD) / name, entries, mtime)


def test_pi_session_dir_encoding(homes: Path):
    assert agents.pi_session_dir("/Users/me/.pi") == homes / "pi/sessions/--Users-me-.pi--"


def test_claude_project_dir_encoding(homes: Path):
    assert agents.claude_project_dir("/Users/me/.pi") == homes / "claude/projects/-Users-me--pi"


def test_pi_reader_follows_active_branch(homes: Path):
    reply, files = agents.read_pi(pi_session(homes))
    assert (reply, files) == ("Renamed `useSessionStore`.", ["src/useStore.ts"])


def test_claude_reader_spans_tool_results_and_skips_sidechains(homes: Path):
    def entry(uuid: str, parent: str | None, kind: str, content: object, **extra: object):
        return {
            "type": kind,
            "uuid": uuid,
            "parentUuid": parent,
            "cwd": CWD,
            "message": {"role": kind, "content": content},
            **extra,
        }

    path = write_jsonl(
        agents.claude_project_dir(CWD) / "abc.jsonl",
        [
            entry("1", None, "user", "do it"),
            entry("2", "1", "assistant", [{"type": "text", "text": "Editing `parse_args`."}]),
            entry(
                "3",
                "2",
                "assistant",
                [{"type": "tool_use", "name": "Edit", "input": {"file_path": f"{CWD}/cli.py"}}],
            ),
            entry("4", "3", "user", [{"type": "tool_result", "content": "ok"}]),
            entry("5", "4", "assistant", [{"type": "text", "text": "Done in cli.py."}]),
            entry("6", "5", "assistant", [{"type": "text", "text": "side"}], isSidechain=True),
            {"type": "last-prompt", "lastPrompt": "do it", "leafUuid": "1"},
        ],
    )
    reply, files = agents.read_claude(path)
    assert (reply, files) == ("Editing `parse_args`.\n\nDone in cli.py.", [f"{CWD}/cli.py"])


def codex_rollout(homes: Path, name: str, cwd: str, mtime: float) -> Path:
    day = homes / "codex/sessions/2026/11/12"
    return write_jsonl(
        day / name,
        [
            {"type": "session_meta", "payload": {"id": name, "cwd": cwd}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "go"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "input": "*** Begin Patch\n*** Update File: src/a.py\n@@\n*** End Patch",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": f"patched {name}"}],
                },
            },
        ],
        mtime,
    )


def test_codex_session_is_matched_by_cwd_in_session_meta(homes: Path):
    now = time.time()
    codex_rollout(homes, "rollout-mine.jsonl", CWD, now - 10)
    codex_rollout(homes, "rollout-other.jsonl", "/elsewhere", now)
    session = agents.find_session(CWD, "codex", agent_started=now - 60, now=now)
    assert session is not None
    assert (session.last_reply, session.files) == ("patched rollout-mine.jsonl", ("src/a.py",))


def test_agent_process_fresh_session_is_confident(homes: Path):
    now = time.time()
    pi_session(homes, mtime=now - 5)
    session = agents.find_session(CWD, "pi", agent_started=now - 100, now=now)
    assert session is not None
    assert session.confidence >= agents.CONFIDENT


def test_without_agent_process_session_is_not_confident(homes: Path):
    now = time.time()
    pi_session(homes, mtime=now - 5)
    session = agents.find_session(CWD, None, now=now)
    assert session is not None
    assert session.confidence < agents.CONFIDENT


def test_two_recent_sessions_for_same_cwd_are_not_confident(homes: Path):
    now = time.time()
    pi_session(homes, "a.jsonl", mtime=now - 5)
    pi_session(homes, "b.jsonl", mtime=now - 20)
    session = agents.find_session(CWD, "pi", agent_started=now - 100, now=now)
    assert session is not None
    assert session.confidence < agents.CONFIDENT


def test_no_session_for_cwd(homes: Path):
    pi_session(homes)
    assert agents.find_session("/other", "pi") is None


def test_truncated_and_garbage_lines_are_tolerated(homes: Path):
    path = pi_session(homes)
    with path.open("a") as fh:
        fh.write('{"type": "message", "id": "x9", "parentId": "a4", "mess\n')
    assert agents.read_pi(path)[0] == "Renamed `useSessionStore`."
