"""Vocabulary learned from the user's earlier agent sessions, per repository and per user.

The largest gain of the ranking experiment (``docs/research/context-ranking/REPORT.md``):
names and identifiers the user wrote (or dictated) in earlier sessions come back.

- Only the user's own prompts in main sessions teach: no assistant text, tool output,
  subagent sessions or prompts longer than ``MAX_PROMPT_CHARS`` (pasted logs/code). Only terms are kept, never messages; text is scrubbed first.
- Time-respecting: a session file teaches once it has been idle for ``IDLE_S`` (the session
  ended), and the session matched for the focused terminal is excluded by the caller.
- Scopes: the session's cwd. Repository vocabulary merges the scopes inside the repository
  root; user vocabulary spans all sessions.
- Score (REPORT): ``r = (0.5 + 0.5 * min(1, log1p(sessions) / log(6))) * 2**(-days / 30)``,
  ``days`` since the newest session end that used the term. The best ``SERVE_TERMS`` of each
  scope become candidates.
- Store: ``<data dir>/context-vocab.json``, owner-only, rewritten atomically. Bounded: terms
  per scope, user terms, scopes and file marks are capped; files are read incrementally from
  the last offset, at most ``MAX_READ_BYTES`` per file and ``MAX_FILES_PER_PASS`` per pass.

Inspect or reset with ``python -m hark_client.context vocab list|update|clear``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from hark_client.config import default_data_dir
from hark_client.context import agents
from hark_client.context.aliases import extract_terms, scrub
from hark_client.context.rank import Candidate, Origin
from hark_client.context.types import TermKind

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

STORE_NAME = "context-vocab.json"
STORE_VERSION = 1
IDLE_S = 15 * 60  # a session file untouched this long has ended
SERVE_TERMS = 300  # candidates per scope
HALF_LIFE_DAYS = 30.0
SATURATION_SESSIONS = 5
MAX_SCOPE_TERMS = 600
MAX_USER_TERMS = 2000
MAX_SCOPES = 100
MAX_MARKS = 5000
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_FILES_PER_PASS = 40
MAX_PROMPT_CHARS = 6000
BACKGROUND_PAUSE_S = 0.02


@dataclass(slots=True)
class Entry:
    kind: TermKind
    sessions: int  # distinct sessions that used the term
    last_end: float  # epoch seconds: end of the newest such session
    last_session: str  # id of that session (a growing file must not count twice)


def persistence(sessions: int, days: float) -> float:
    """Source-internal score of a learned term (see the module docstring)."""
    saturation = min(1.0, math.log1p(sessions) / math.log(SATURATION_SESSIONS + 1))
    return (0.5 + 0.5 * saturation) * 2 ** (-max(0.0, days) / HALF_LIFE_DAYS)


def _score(e: Entry, now: float) -> float:
    return persistence(e.sessions, (now - e.last_end) / 86400)


def _top(terms: dict[str, Entry], now: float, n: int) -> list[tuple[str, Entry, float]]:
    scored = [(t, e, _score(e, now)) for t, e in terms.items()]
    return sorted(scored, key=lambda x: (-x[2], x[0]))[:n]


def _within(scope: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    return scope == root or scope.startswith(root if root.endswith("/") else root + "/")


@dataclass(slots=True)
class Vocab:
    """The learned terms (pure data; :class:`VocabStore` persists it)."""

    scopes: dict[str, dict[str, Entry]] = field(default_factory=dict[str, dict[str, Entry]])
    user: dict[str, Entry] = field(default_factory=dict[str, Entry])
    marks: dict[str, tuple[int, float]] = field(default_factory=dict[str, tuple[int, float]])

    def add_session(
        self, scope: str, terms: Iterable[tuple[str, TermKind]], end: float, session: str
    ) -> None:
        """Count one session's terms in ``scope`` (cwd; "" = unknown, user-wide only)."""
        targets = [self.user] if not scope else [self.user, self.scopes.setdefault(scope, {})]
        for term, kind in terms:
            for table in targets:
                e = table.get(term)
                if e is None:
                    table[term] = Entry(kind, 1, end, session)
                elif e.last_session != session:
                    e.sessions, e.last_end, e.last_session = (
                        e.sessions + 1,
                        max(e.last_end, end),
                        session,
                    )
                else:
                    e.last_end = max(e.last_end, end)

    def candidates(self, repo: str | None, now: float) -> list[Candidate]:
        """Best terms of the repository containing ``repo`` (a root or cwd) and user-wide."""
        out: list[Candidate] = []
        if repo:
            merged: dict[str, Entry] = {}
            for scope, terms in self.scopes.items():
                if not _within(scope, repo):
                    continue
                for t, e in terms.items():
                    m = merged.get(t)
                    merged[t] = (
                        Entry(e.kind, e.sessions, e.last_end, e.last_session)
                        if m is None
                        else Entry(m.kind, m.sessions + e.sessions, max(m.last_end, e.last_end), "")
                    )
            out.extend(
                Candidate(t, e.kind, Origin.PERSIST_REPO, r)
                for t, e, r in _top(merged, now, SERVE_TERMS)
            )
        out.extend(
            Candidate(t, e.kind, Origin.PERSIST_USER, r)
            for t, e, r in _top(self.user, now, SERVE_TERMS)
        )
        return out

    def prune(self, now: float) -> None:
        """Apply the size caps, dropping the weakest terms, stalest scopes and oldest marks."""
        self.user = {t: e for t, e, _ in _top(self.user, now, MAX_USER_TERMS)}
        for scope, terms in self.scopes.items():
            self.scopes[scope] = {t: e for t, e, _ in _top(terms, now, MAX_SCOPE_TERMS)}
        if len(self.scopes) > MAX_SCOPES:
            newest = sorted(
                self.scopes,
                key=lambda s: max((e.last_end for e in self.scopes[s].values()), default=0.0),
                reverse=True,
            )
            self.scopes = {s: self.scopes[s] for s in newest[:MAX_SCOPES]}
        if len(self.marks) > MAX_MARKS:
            keep = sorted(self.marks, key=lambda p: self.marks[p][1], reverse=True)[:MAX_MARKS]
            self.marks = {p: self.marks[p] for p in keep}


def _session_id(path: Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class _Read:
    """What one session file adds: terms per scope, and how far it has been read."""

    terms: dict[str, dict[str, TermKind]]
    offset: int
    mtime: float


def read_session(agent: str, path: Path, start: int) -> _Read | None:
    """Terms of the user's prompts in ``path`` from byte ``start`` (bounded); None if the
    file is unreadable."""
    try:
        st = path.stat()
        with path.open("rb") as fh:
            fh.seek(start)
            data = fh.read(MAX_READ_BYTES)
    except OSError:
        return None
    if start + len(data) < st.st_size:
        data = data[: data.rfind(b"\n") + 1]  # continue at a line boundary next time
    header_cwd, subagent = agents.session_header(agent, path)
    found: dict[str, dict[str, TermKind]] = {}
    if not subagent:
        lines = data.decode(errors="replace").splitlines()
        for text, cwd in agents.user_prompts(agent, lines):
            if len(text) > MAX_PROMPT_CHARS:
                continue  # pasted logs, code or generated task text, not the user's words
            table = found.setdefault(cwd or header_cwd or "", {})
            for term, kind in extract_terms(scrub(text), prose=True):
                table.setdefault(term, kind)
    return _Read(found, start + len(data), st.st_mtime)


def _entry_json(e: Entry) -> list[object]:
    return [e.kind.value, e.sessions, round(e.last_end, 1), e.last_session]


def _entry(raw: object) -> Entry | None:
    if not isinstance(raw, list):
        return None
    row = cast("list[object]", raw)
    if len(row) != 4:
        return None
    kind, sessions, last_end, session = row
    if not (
        isinstance(kind, str)
        and kind in TermKind.__members__.values()
        and isinstance(sessions, int)
        and isinstance(last_end, (int, float))
        and isinstance(session, str)
    ):
        return None
    return Entry(TermKind(kind), sessions, float(last_end), session)


def _table(raw: object) -> dict[str, Entry]:
    if not isinstance(raw, dict):
        return {}
    items = cast("dict[str, object]", raw).items()
    return {t: e for t, v in items if (e := _entry(v)) is not None}


def to_json(v: Vocab) -> dict[str, object]:
    return {
        "version": STORE_VERSION,
        "marks": {p: [n, round(m, 1)] for p, (n, m) in v.marks.items()},
        "user": {t: _entry_json(e) for t, e in v.user.items()},
        "scopes": {s: {t: _entry_json(e) for t, e in ts.items()} for s, ts in v.scopes.items()},
    }


def from_json(raw: object) -> Vocab:
    """Tolerant: unknown versions and malformed parts are dropped, not fatal."""
    if not isinstance(raw, dict):
        return Vocab()
    d = cast("dict[str, object]", raw)
    if d.get("version") != STORE_VERSION:
        return Vocab()
    marks: dict[str, tuple[int, float]] = {}
    raw_marks = d.get("marks")
    mark_items = cast("dict[str, object]", raw_marks) if isinstance(raw_marks, dict) else {}
    for p, m in mark_items.items():
        row = cast("list[object]", m) if isinstance(m, list) else []
        if len(row) == 2 and isinstance(row[0], int) and isinstance(row[1], (int, float)):
            marks[p] = (row[0], float(row[1]))
    raw_scopes = d.get("scopes")
    scopes = (
        {s: _table(t) for s, t in cast("dict[str, object]", raw_scopes).items()}
        if isinstance(raw_scopes, dict)
        else {}
    )
    return Vocab(scopes, _table(d.get("user")), marks)


class VocabStore:
    """:class:`Vocab` in a file; thread-safe. Reads are cached by file mtime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._vocab = Vocab()
        self._mtime: float | None = None
        self._cache: dict[tuple[float | None, str | None, int], list[Candidate]] = {}

    def _load(self) -> Vocab:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if mtime != self._mtime:
            raw: object = {}
            if mtime is not None:
                try:
                    raw = cast("object", json.loads(self.path.read_text(encoding="utf-8")))
                except (OSError, ValueError) as e:
                    log.warning("ignoring unreadable %s: %s", self.path, e)
            self._vocab, self._mtime, self._cache = from_json(raw), mtime, {}
        return self._vocab

    def vocab(self) -> Vocab:
        with self._lock:
            return self._load()

    def candidates(self, repo: str | None, now: float | None = None) -> list[Candidate]:
        """Persistent candidates for the repository ``repo`` (cached per hour)."""
        now = time.time() if now is None else now
        with self._lock:
            vocab = self._load()
            key = (self._mtime, repo, int(now // 3600))
            if key not in self._cache:
                self._cache = {key: vocab.candidates(repo, now)}
            return self._cache[key]

    def learn(
        self,
        *,
        now: float | None = None,
        exclude: Iterable[str] = (),
        priority_cwd: str | None = None,
        max_files: int = MAX_FILES_PER_PASS,
        stop: threading.Event | None = None,
    ) -> int:
        """Read ended, unread (parts of) session files, ``priority_cwd``'s first. With
        ``stop`` (a background pass) it pauses ``BACKGROUND_PAUSE_S`` after each file and ends
        early once ``stop`` is set. Returns the number of files read; saves if any."""
        now = time.time() if now is None else now
        skip = set(exclude)
        with self._lock:
            marks = dict(self._load().marks)
        done = 0
        for agent, path in _ordered_files(priority_cwd):
            if done >= max_files or (stop is not None and stop.is_set()):
                break
            key = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            start = marks.get(key, (0, 0.0))[0]
            start = 0 if start > st.st_size else start
            if key in skip or start == st.st_size or now - st.st_mtime < IDLE_S:
                continue
            read = read_session(agent, path, start)
            if read is None:
                continue
            done += 1
            marks[key] = (read.offset, read.mtime)
            with self._lock:
                vocab = self._load()
                for scope, terms in read.terms.items():
                    vocab.add_session(scope, terms.items(), read.mtime, _session_id(path))
                vocab.marks[key] = marks[key]
            if stop is not None:
                stop.wait(BACKGROUND_PAUSE_S)  # leave the GIL to the dictation threads
        if done:
            self._save(now)
        return done

    def _save(self, now: float) -> None:
        with self._lock:
            vocab = self._load()
            vocab.prune(now)
            payload = json.dumps(to_json(vocab), ensure_ascii=False, separators=(",", ":"))
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            tmp.replace(self.path)
            self._mtime, self._cache = self.path.stat().st_mtime, {}

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)
            self._vocab, self._mtime, self._cache = Vocab(), None, {}


def _ordered_files(priority_cwd: str | None) -> list[tuple[str, Path]]:
    """Session files of ``priority_cwd`` first, then everything else newest first."""
    first: set[Path] = set()
    if priority_cwd:
        dirs = (agents.pi_session_dir(priority_cwd), agents.claude_project_dir(priority_cwd))
        first = {p for d in dirs for p in d.glob("*.jsonl")}

    def mtime(item: tuple[str, Path]) -> float:
        try:
            return item[1].stat().st_mtime
        except OSError:
            return 0.0

    files = sorted(agents.session_files(), key=mtime, reverse=True)
    return sorted(files, key=lambda item: item[1] not in first)  # stable: keeps mtime order


def repo_root(cwd: str) -> str:
    """The nearest ancestor of ``cwd`` with a ``.git`` entry (worktrees too), else ``cwd``."""
    for d in (Path(cwd), *Path(cwd).parents):
        if (d / ".git").exists():
            return str(d)
    return cwd


_store: VocabStore | None = None
_store_lock = threading.Lock()


def store(data_dir: str | None = None) -> VocabStore:
    """The process-wide store in ``data_dir`` (default: hark's data dir)."""
    global _store  # noqa: PLW0603 - one store per process
    path = Path(data_dir or default_data_dir()).expanduser() / STORE_NAME
    with _store_lock:
        if _store is None or _store.path != path:
            _store = VocabStore(path)
        return _store
