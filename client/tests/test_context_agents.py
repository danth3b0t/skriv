"""Agent session readers against small fixtures shaped like real pi / Claude Code / Codex files."""

import json
import os
import time
from pathlib import Path

import pytest

from hark_client.context import agents
from hark_client.context.agents import Turn

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
    turns, files = agents.read_pi(pi_session(homes))
    assert turns == [
        Turn("assistant", "Renamed `useSessionStore`."),
        Turn("user", "rename the store"),
        Turn("assistant", "first reply"),
    ]
    assert files == ["src/useStore.ts"]


def pi_chat(homes: Path, replies: list[str]) -> Path:
    """Linear pi session: user "q<i>" then assistant replies[i], for each i."""
    entries: list[dict[str, object]] = [{"type": "session", "version": 3, "id": "s", "cwd": CWD}]
    parent: str | None = None
    for i, reply in enumerate(replies):
        entries.append(pi_msg(f"u{i}", parent, "user", f"q{i}"))
        entries.append(pi_msg(f"a{i}", f"u{i}", "assistant", [{"type": "text", "text": reply}]))
        parent = f"a{i}"
    return write_jsonl(agents.pi_session_dir(CWD) / "chat.jsonl", entries)


def test_reader_keeps_last_four_assistant_turns_and_last_user_turn(homes: Path):
    turns, _ = agents.read_pi(pi_chat(homes, [f"reply{i}" for i in range(6)]))
    assert turns == [
        Turn("assistant", "reply5"),
        Turn("user", "q5"),
        Turn("assistant", "reply4"),
        Turn("assistant", "reply3"),
        Turn("assistant", "reply2"),
    ]


def test_reader_clips_turns_to_their_tail_within_the_budget(homes: Path):
    turns, _ = agents.read_pi(pi_chat(homes, [f"{i}" * 5000 + f" end{i}" for i in range(4)]))
    assert [t.text[-4:] for t in turns] == ["end3", "q3", "end2", "end1"]
    assert len(turns[0].text) == agents.MAX_ASSISTANT_TURN_CHARS
    assert sum(len(t.text) for t in turns) == agents.MAX_CONTEXT_CHARS


def test_claude_reader_spans_tool_results_and_skips_sidechains_and_commands(homes: Path):
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
            entry("0a", None, "user", "start"),
            entry("0b", "0a", "assistant", [{"type": "text", "text": "Earlier reply."}]),
            entry("1", "0b", "user", "do it"),
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
            entry("7", "5", "user", "<command-name>/model</command-name>"),  # not the user's words
            {"type": "last-prompt", "lastPrompt": "do it", "leafUuid": "1"},
        ],
    )
    turns, files = agents.read_claude(path)
    assert turns == [
        Turn("assistant", "Editing `parse_args`.\n\nDone in cli.py."),
        Turn("user", "do it"),
        Turn("assistant", "Earlier reply."),
    ]
    assert files == [f"{CWD}/cli.py"]


def codex_msg(role: str, kind: str, text: str) -> dict[str, object]:
    payload = {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}
    return {"type": "response_item", "payload": payload}


def codex_rollout(homes: Path, name: str, cwd: str, mtime: float) -> Path:
    day = homes / "codex/sessions/2026/11/12"
    return write_jsonl(
        day / name,
        [
            {"type": "session_meta", "payload": {"id": name, "cwd": cwd}},
            codex_msg("user", "input_text", "# AGENTS.md instructions for /w"),
            codex_msg("assistant", "output_text", "earlier"),
            codex_msg("user", "input_text", "go"),
            codex_msg("user", "input_text", "<environment_context>cwd</environment_context>"),
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
    assert session.turns == (
        Turn("assistant", "patched rollout-mine.jsonl"),
        Turn("user", "go"),
        Turn("assistant", "earlier"),
    )
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
    assert agents.read_pi(path)[0][0] == Turn("assistant", "Renamed `useSessionStore`.")
