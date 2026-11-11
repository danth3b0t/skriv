"""Read the coding agent's recent conversation straight from its session files (no hooks needed).

On the active branch the readers keep the last ``MAX_GROUPS`` prompt/response groups, newest
first: a human prompt (tail ``MAX_USER_CHARS``), all assistant text up to the next prompt
(tail ``MAX_ASSISTANT_CHARS``) and its tool-call paths. A group's ``age`` (0 = newest) drives
recency decay in ranking; an empty response still takes an age. Injected prompts (Claude
meta/command wrappers, Codex AGENTS.md/environment) and tool results are no boundary.

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
    from collections.abc import Iterable, Iterator

CONFIDENT = 0.7
TAIL_BYTES = 4 * 1024 * 1024  # only the end of a session file is read
MAX_CHAIN = 2000  # entries walked back from the leaf
MAX_REPLY_CHARS = 1500  # AgentSession.last_reply
MAX_GROUPS = 8  # prompt/response groups kept, newest first
MAX_ASSISTANT_CHARS = 1500  # tail of each response
MAX_USER_CHARS = 6000  # tail of each prompt
MAX_GROUP_FILES = 30  # tool-call paths per group
MAX_FILES = 30  # session files considered per agent and cwd
AGENTS = ("pi", "claude", "codex")

type Json = dict[str, object]


@dataclass(frozen=True, slots=True)
class Group:
    """A human prompt and the agent's response to it. ``age`` 0 is the newest group; a
    prompt still waiting for its response is a group with an empty ``assistant``."""

    age: int
    assistant: str  # tail of the response text (all text blocks between two prompts)
    user: str  # tail of the prompt; "" when it was not the user's words
    files: tuple[str, ...] = ()  # tool-call paths of the response, newest first


@dataclass(frozen=True, slots=True)
class AgentSession:
    agent: str
    path: str
    cwd: str
    mtime: float
    groups: tuple[Group, ...]  # newest first, at most MAX_GROUPS
    confidence: float = 0.0
    notes: tuple[str, ...] = field(default=())

    @property
    def last_reply(self) -> str:
        """Tail of the newest non-empty response."""
        text = next((g.assistant for g in self.groups if g.assistant), "")
        return text[-MAX_REPLY_CHARS:]

    @property
    def files(self) -> tuple[str, ...]:
        """Tool-call paths of all groups, most recent first."""
        return tuple(dict.fromkeys(f for g in self.groups for f in g.files))


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


class _Groups:
    """Groups messages, fed newest first, into prompt/response groups (module docstring)."""

    def __init__(self) -> None:
        self.groups: list[Group] = []
        self._texts: list[str] = []  # current response, newest first
        self._files: list[str] = []

    def assistant(self, text: str, files: list[str]) -> None:
        if text.strip():
            self._texts.append(text)
        self._files.extend(files)

    def user(self, text: str) -> bool:
        """A human prompt (group boundary). Returns True once enough groups are collected."""
        self._flush(text)
        return len(self.groups) >= MAX_GROUPS

    def _flush(self, user: str) -> None:
        self.groups.append(
            Group(
                age=len(self.groups),
                assistant="\n\n".join(reversed(self._texts)).strip()[-MAX_ASSISTANT_CHARS:],
                user=user.strip()[-MAX_USER_CHARS:],
                files=tuple(dict.fromkeys(self._files))[:MAX_GROUP_FILES],
            )
        )
        self._texts, self._files = [], []

    def result(self) -> list[Group]:
        if (self._texts or self._files) and len(self.groups) < MAX_GROUPS:
            self._flush("")  # the oldest response, its prompt outside the tail read
        return self.groups


def _texts(blocks: list[Json], kind: str) -> str:
    return "\n".join(t for b in blocks if b.get("type") == kind and (t := _str(b.get("text"))))


def _blocks(content: object) -> list[Json]:
    return [b for b in map(_obj, _list(content)) if b is not None]


# --------------------------------------------------------------------------- readers


def read_pi(path: Path) -> list[Group]:
    """Recent prompt/response groups, newest first, on the active branch."""
    groups = _Groups()
    for entry in _chain(_tail_lines(path), "id", "parentId", None):
        msg = _obj(entry.get("message"))
        if entry.get("type") != "message" or msg is None:
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            text = content if isinstance(content, str) else _texts(_blocks(content), "text")
            if groups.user(text):
                break
        elif role == "assistant":
            blocks = _blocks(content)
            files = [
                p
                for block in reversed(blocks)
                if block.get("type") == "toolCall"
                for p in _path_args(_obj(block.get("arguments")))
            ]
            groups.assistant(_texts(blocks, "text"), files)
    return groups.result()


def _claude_injected(entry: Json, text: str) -> bool:
    """Meta entries and slash-command/system wrappers are not the user's words."""
    return entry.get("isMeta") is True or text.lstrip().startswith("<")


def read_claude(path: Path) -> list[Group]:
    lines = _tail_lines(path)
    leaf: str | None = None
    for line in reversed(lines):
        entry = _parse(line) if '"uuid"' in line else None
        if entry is not None and entry.get("isSidechain") is not True:
            leaf = _str(entry.get("uuid"))
            if leaf is not None:
                break
    groups = _Groups()
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
            if not _claude_injected(entry, text) and groups.user(text):
                break
        elif kind == "assistant":
            for block in reversed(_blocks(content)):
                if block.get("type") == "tool_use":
                    groups.assistant("", _path_args(_obj(block.get("input"))))
                elif block.get("type") == "text" and (t := _str(block.get("text"))):
                    groups.assistant(t, [])
    return groups.result()


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


def read_codex(path: Path) -> list[Group]:
    groups = _Groups()
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
                if not text.lstrip().startswith(_CODEX_INJECTED) and groups.user(text):
                    break
            elif role == "assistant":
                groups.assistant(_texts(_blocks(payload.get("content")), "output_text"), [])
        elif ptype in {"function_call", "custom_tool_call"}:
            raw = _str(payload.get("arguments")) or _str(payload.get("input")) or ""
            files = list(reversed([m.group(1) for m in _PATCH_FILE_RE.finditer(raw)]))
            args = _parse(raw) if raw.startswith("{") else None
            groups.assistant("", [*files, *_path_args(args)])
    return groups.result()


# --------------------------------------------------------------------------- all prompts

_USER_LINE_RE = re.compile(r'"(?:role|type)":\s?"user"')


def user_prompts(agent: str, lines: Iterable[str]) -> Iterator[tuple[str, str | None]]:
    """(prompt text, cwd if the entry records one) of every human prompt in ``lines`` of a
    session file, any branch; injected, tool-result and sidechain entries are skipped."""
    for line in lines:
        if not _USER_LINE_RE.search(line) or '"tool_result"' in line:
            continue
        entry = _parse(line)
        if entry is None:
            continue
        text = _prompt_text(agent, entry)
        if text and text.strip():
            yield text, _str(entry.get("cwd"))


def _prompt_text(agent: str, entry: Json) -> str | None:
    if agent == "codex":
        payload = _obj(entry.get("payload"))
        if payload is None or payload.get("type") != "message" or payload.get("role") != "user":
            return None
        text = _texts(_blocks(payload.get("content")), "input_text")
        return None if text.lstrip().startswith(_CODEX_INJECTED) else text
    msg = _obj(entry.get("message"))
    if msg is None or msg.get("role") != "user":
        return None
    content = msg.get("content")
    text = content if isinstance(content, str) else _texts(_blocks(content), "text")
    if agent == "claude" and (
        entry.get("type") != "user"
        or entry.get("isSidechain") is True
        or not _is_human_turn(msg)
        or _claude_injected(entry, text)
    ):
        return None
    if agent == "pi" and entry.get("type") != "message":
        return None
    return text


def session_header(agent: str, path: Path) -> tuple[str | None, bool]:
    """(cwd from the first line, whether it is a Codex subagent session)."""
    try:
        head = _first_line(path)
    except OSError:
        return None, False
    if head is None:
        return None, False
    if agent != "codex":
        return _str(head.get("cwd")), False
    payload = _obj(head.get("payload")) or {}
    sub = bool(payload.get("parent_thread_id")) or isinstance(payload.get("source"), dict)
    return _str(payload.get("cwd")), sub


def session_files() -> Iterator[tuple[str, Path]]:
    """(agent, path) of every main session file (pi, Claude Code, Codex); subagent
    directories are not searched."""
    override = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    if override:
        pi = Path(override).expanduser().glob("*.jsonl")
    else:
        agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR") or _home() / ".pi" / "agent")
        pi = (agent_dir.expanduser() / "sessions").glob("*/*.jsonl")
    yield from (("pi", p) for p in pi)
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or _home() / ".claude").expanduser()
    yield from (("claude", p) for p in (claude / "projects").glob("*/*.jsonl"))
    yield from (("codex", p) for p in codex_sessions_dir().glob("*/*/*/rollout-*.jsonl"))


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
    return session_header(agent, path)[0]


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
        groups = _READERS[agent](path)
        best = AgentSession(
            agent=agent,
            path=str(path),
            cwd=cwd,
            mtime=mtime,
            groups=tuple(groups),
            confidence=round(conf, 3),
            notes=tuple(notes),
        )
    return best
