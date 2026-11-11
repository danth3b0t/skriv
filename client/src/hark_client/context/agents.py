"""Read the coding agent's recent conversation straight from its session files (no hooks needed).

On the active branch the readers keep the last ``MAX_ASSISTANT_TURNS`` assistant turns (all
assistant text between two human prompts) plus the latest human prompt, newest first, within
``MAX_CONTEXT_CHARS`` (each turn clipped to its tail).

Formats (verified against real files from pi 1.5 (format v3), Claude Code 2.1, Codex 0.166):

- pi: ``$PI_CODING_AGENT_DIR/sessions/--<cwd, leading / dropped, / \\ : -> ->--/*.jsonl``
  (``$PI_CODING_AGENT_SESSION_DIR`` overrides the whole directory, then without the per-cwd
  subfolder). Entries form a tree via ``id``/``parentId``; the active branch is the chain
  from the last entry. ``message.role == "assistant"`` content blocks: ``text`` and
  ``toolCall`` (``arguments.path``).
- Claude Code: ``$CLAUDE_CONFIG_DIR|~/.claude/projects/<cwd, non-alnum -> ->/*.jsonl``.
  Entries ``type: user|assistant`` linked by ``uuid``/``parentUuid``, one content block per
  line; ``tool_use.input.file_path|path``; sidechain (subagent) entries are skipped.
- Codex: ``$CODEX_HOME|~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``; first line is
  ``session_meta`` with ``payload.cwd``; ``response_item`` payloads of type ``message`` (role
  assistant, ``output_text``) and ``function_call``/``custom_tool_call`` (paths from JSON
  arguments or ``*** Update File: <path>`` patch headers).

Matching: the newest session file for the focused terminal's cwd. Confidence:

- 0.95 agent process of the same kind in the foreground and the file was written after it
  started; 0.8 when the file is older than the process (idle agent, resumed session).
- without an agent process: 0.5 if written in the last 2 minutes, else 0.2.
- x0.7 if another session for the same cwd was written within a minute of it (ambiguous).

Callers should only use sessions with ``confidence >= CONFIDENT``.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

CONFIDENT = 0.7
TAIL_BYTES = 4 * 1024 * 1024  # only the end of a session file is read
MAX_CHAIN = 2000  # entries walked back from the leaf
MAX_REPLY_CHARS = 1500  # AgentSession.last_reply
MAX_ASSISTANT_TURNS = 4
MAX_CONTEXT_CHARS = 8000  # all turns together
MAX_ASSISTANT_TURN_CHARS = 3000
MAX_USER_TURN_CHARS = 1500
MAX_FILES = 30
AGENTS = ("pi", "claude", "codex")

type Json = dict[str, object]


@dataclass(frozen=True, slots=True)
class Turn:
    role: str  # "assistant" | "user"
    text: str


@dataclass(frozen=True, slots=True)
class AgentSession:
    agent: str
    path: str
    cwd: str
    mtime: float
    turns: tuple[Turn, ...]  # newest first
    files: tuple[str, ...] = ()  # paths touched by tool calls, most recent first
    confidence: float = 0.0
    notes: tuple[str, ...] = field(default=())

    @property
    def last_reply(self) -> str:
        """Tail of the newest assistant turn."""
        text = next((t.text for t in self.turns if t.role == "assistant"), "")
        return text[-MAX_REPLY_CHARS:]


# --------------------------------------------------------------------------- helpers


def _obj(value: object) -> Json | None:
    return cast("Json", value) if isinstance(value, dict) else None


def _list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _tail_lines(path: Path) -> list[str]:
    with path.open("rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        start = max(0, size - TAIL_BYTES)
        fh.seek(start)
        data = fh.read()
    lines = data.decode(errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # first line is probably cut
    return lines


def _parse(line: str) -> Json | None:
    try:
        return _obj(cast("object", json.loads(line)))
    except ValueError:
        return None


def _first_line(path: Path) -> Json | None:
    with path.open("rb") as fh:
        return _parse(fh.readline(1024 * 1024).decode(errors="replace"))


def _chain(lines: list[str], id_key: str, parent_key: str, leaf: str | None) -> Iterator[Json]:
    """Entries from ``leaf`` (default: last entry with an id) back to the root, newest first."""
    by_id: dict[str, Json] = {}
    last: str | None = None
    for line in lines:
        entry = _parse(line)
        if entry is None:
            continue
        eid = _str(entry.get(id_key))
        if eid is not None:
            by_id[eid] = entry
            last = eid
    cur = leaf if leaf in by_id else last
    for _ in range(MAX_CHAIN):
        if cur is None:
            return
        entry = by_id.get(cur)
        if entry is None:
            return
        yield entry
        cur = _str(entry.get(parent_key))


def _path_args(args: Json | None) -> list[str]:
    if args is None:
        return []
    return [p for k in ("path", "file_path", "notebook_path") if (p := _str(args.get(k)))]


_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


class _Turns:
    """Groups messages, fed newest first, into turns; see the module docstring."""

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self._texts: list[str] = []  # current assistant turn, newest first
        self._assistant_turns = 0
        self._have_user = False

    def assistant(self, text: str) -> None:
        if text.strip():
            self._texts.append(text)

    def user(self, text: str) -> bool:
        """A human prompt (turn boundary). Returns True once enough turns are collected."""
        self._flush()
        if text.strip() and not self._have_user:
            self._have_user = True
            self.turns.append(Turn("user", text))
        return self._assistant_turns >= MAX_ASSISTANT_TURNS

    def _flush(self) -> None:
        if self._texts:
            self.turns.append(Turn("assistant", "\n\n".join(reversed(self._texts))))
            self._texts = []
            self._assistant_turns += 1

    def result(self) -> list[Turn]:
        if self._assistant_turns < MAX_ASSISTANT_TURNS:
            self._flush()
        out: list[Turn] = []
        budget = MAX_CONTEXT_CHARS
        for turn in self.turns:
            if budget <= 0:
                break
            cap = MAX_USER_TURN_CHARS if turn.role == "user" else MAX_ASSISTANT_TURN_CHARS
            text = turn.text.strip()[-min(cap, budget) :]
            budget -= len(text)
            out.append(Turn(turn.role, text))
        return out


def _texts(blocks: list[Json], kind: str) -> str:
    return "\n".join(t for b in blocks if b.get("type") == kind and (t := _str(b.get("text"))))


def _blocks(content: object) -> list[Json]:
    return [b for b in map(_obj, _list(content)) if b is not None]


# --------------------------------------------------------------------------- readers


def read_pi(path: Path) -> tuple[list[Turn], list[str]]:
    """(recent turns newest first, tool-call paths newest first) on the active branch."""
    turns = _Turns()
    files: list[str] = []
    for entry in _chain(_tail_lines(path), "id", "parentId", None):
        msg = _obj(entry.get("message"))
        if entry.get("type") != "message" or msg is None:
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            text = content if isinstance(content, str) else _texts(_blocks(content), "text")
            if turns.user(text):
                break
        elif role == "assistant":
            blocks = _blocks(content)
            for block in reversed(blocks):
                if block.get("type") == "toolCall":
                    files.extend(_path_args(_obj(block.get("arguments"))))
            turns.assistant(_texts(blocks, "text"))
    return turns.result(), files


def read_claude(path: Path) -> tuple[list[Turn], list[str]]:
    lines = _tail_lines(path)
    leaf: str | None = None
    for line in reversed(lines):
        entry = _parse(line) if '"uuid"' in line else None
        if entry is not None and entry.get("isSidechain") is not True:
            leaf = _str(entry.get("uuid"))
            if leaf is not None:
                break
    turns = _Turns()
    files: list[str] = []
    for entry in _chain(lines, "uuid", "parentUuid", leaf):
        if entry.get("isSidechain") is True:
            continue
        msg = _obj(entry.get("message"))
        if msg is None:
            continue
        kind = entry.get("type")
        content = msg.get("content")
        if kind == "user" and _is_human_turn(msg):
            text = content if isinstance(content, str) else _texts(_blocks(content), "text")
            # meta entries and slash-command/system wrappers are not the user's words
            injected = entry.get("isMeta") is True or text.lstrip().startswith("<")
            if turns.user("" if injected else text):
                break
        elif kind == "assistant":
            for block in reversed(_blocks(content)):
                if block.get("type") == "tool_use":
                    files.extend(_path_args(_obj(block.get("input"))))
                elif block.get("type") == "text" and (t := _str(block.get("text"))):
                    turns.assistant(t)
    return turns.result(), files


def _is_human_turn(msg: Json) -> bool:
    """Claude stores tool results as ``user`` messages; only real prompts end a turn."""
    content = msg.get("content")
    if isinstance(content, str):
        return True
    return not any(
        (b := _obj(x)) is not None and b.get("type") == "tool_result" for x in _list(content)
    )


# Codex sends AGENTS.md and environment context as user messages
_CODEX_INJECTED = ("<", "# AGENTS.md instructions")


def read_codex(path: Path) -> tuple[list[Turn], list[str]]:
    turns = _Turns()
    files: list[str] = []
    for line in reversed(_tail_lines(path)):
        if '"response_item"' not in line:
            continue
        entry = _parse(line)
        payload = _obj(entry.get("payload")) if entry is not None else None
        if payload is None:
            continue
        ptype = payload.get("type")
        if ptype == "message":
            role = payload.get("role")
            if role == "user":
                text = _texts(_blocks(payload.get("content")), "input_text")
                if not text.lstrip().startswith(_CODEX_INJECTED) and turns.user(text):
                    break
            elif role == "assistant":
                turns.assistant(_texts(_blocks(payload.get("content")), "output_text"))
        elif ptype in {"function_call", "custom_tool_call"}:
            raw = _str(payload.get("arguments")) or _str(payload.get("input")) or ""
            files.extend(reversed([m.group(1) for m in _PATCH_FILE_RE.finditer(raw)]))
            args = _parse(raw) if raw.startswith("{") else None
            files.extend(_path_args(args))
    return turns.result(), files


# --------------------------------------------------------------------------- locating


def _home() -> Path:
    return Path.home()


def pi_session_dir(cwd: str) -> Path:
    override = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    if override:
        return Path(override).expanduser()
    agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR") or _home() / ".pi" / "agent")
    safe = re.sub(r"[/\\:]", "-", re.sub(r"^[/\\]", "", cwd))
    return agent_dir.expanduser() / "sessions" / f"--{safe}--"


def claude_project_dir(cwd: str) -> Path:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or _home() / ".claude").expanduser()
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)


def codex_sessions_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or _home() / ".codex").expanduser() / "sessions"


def _newest(paths: list[Path]) -> list[tuple[float, Path]]:
    out: list[tuple[float, Path]] = []
    for p in paths:
        try:
            out.append((p.stat().st_mtime, p))
        except OSError:
            continue
    out.sort(reverse=True)
    return out


def _candidates(agent: str, cwd: str) -> list[tuple[float, Path]]:
    """Session files for ``cwd``, newest first."""
    if agent == "pi":
        d = pi_session_dir(cwd)
        if "PI_CODING_AGENT_SESSION_DIR" in os.environ:
            files = _newest(list(d.glob("*.jsonl")))[:MAX_FILES]
            return [(m, p) for m, p in files if _session_cwd(agent, p) == cwd]
        return _newest(list(d.glob("*.jsonl")))
    if agent == "claude":
        return _newest(list(claude_project_dir(cwd).glob("*.jsonl")))
    # codex: newest day folders only, filtered by session_meta.cwd
    root = codex_sessions_dir()
    days = sorted(root.glob("*/*/*"), reverse=True)[:3]
    files = _newest([p for d in days for p in d.glob("rollout-*.jsonl")])[:MAX_FILES]
    return [(m, p) for m, p in files if _session_cwd(agent, p) == cwd]


def _session_cwd(agent: str, path: Path) -> str | None:
    try:
        head = _first_line(path)
    except OSError:
        return None
    if head is None:
        return None
    if agent == "codex":
        payload = _obj(head.get("payload"))
        return _str(payload.get("cwd")) if payload is not None else None
    return _str(head.get("cwd"))


_READERS = {"pi": read_pi, "claude": read_claude, "codex": read_codex}


def find_session(
    cwd: str,
    agent_hint: str | None = None,
    agent_started: float | None = None,
    now: float | None = None,
) -> AgentSession | None:
    """Best matching session for ``cwd`` (see module docstring for confidence)."""
    now = time.time() if now is None else now
    agents = (agent_hint,) if agent_hint is not None and agent_hint in AGENTS else AGENTS
    best: AgentSession | None = None
    for agent in agents:
        found = _candidates(agent, cwd)
        if not found:
            continue
        mtime, path = found[0]
        if agent_hint == agent:
            fresh = agent_started is None or mtime >= agent_started - 5
            conf = 0.95 if fresh else 0.8
        else:
            conf = 0.5 if now - mtime <= 120 else 0.2
        notes: list[str] = []
        if len(found) > 1 and mtime - found[1][0] < 60:
            conf *= 0.7
            notes.append(
                f"ambiguous: {found[1][1].name} written {mtime - found[1][0]:.0f}s earlier"
            )
        if best is not None and best.confidence >= conf:
            continue
        turns, files = _READERS[agent](path)
        best = AgentSession(
            agent=agent,
            path=str(path),
            cwd=cwd,
            mtime=mtime,
            turns=tuple(turns),
            files=tuple(dict.fromkeys(files)),
            confidence=round(conf, 3),
            notes=tuple(notes),
        )
    return best
