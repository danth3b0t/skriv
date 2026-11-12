"""Context engine contract shared by the client core and the context providers.

The client core calls :func:`hark_client.context.collect` at hotkey-down (in a worker
thread, with a deadline) and later :func:`hark_client.context.canonicalize` on the ASR
result. Everything here is plain data so it can be logged as JSON for the dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TermKind(StrEnum):
    IDENTIFIER = "identifier"  # useSessionStore, parse_args, HTTPServer
    PATH = "path"  # src/lib/store.ts, store.ts
    FLAG = "flag"  # --dry-run
    COMMAND = "command"  # git, cargo, uv
    NAME = "name"  # people, products: Superwhisper, Hyprland
    WORD = "word"  # other vocabulary


class TermSource(StrEnum):
    DICTIONARY = "dictionary"  # hand-written [[words]] in the config
    VOCABULARY = "vocabulary"  # user's vocabulary (learned from earlier agent sessions)
    SELECTION = "selection"
    FOCUSED_TEXT = "focused_text"
    WINDOW_TITLE = "window_title"
    TERMINAL_TEXT = "terminal_text"
    AGENT_SESSION = "agent_session"
    REPO_RECENT = "repo_recent"  # changed / recently committed files and their symbols
    REPO = "repo"  # repository-wide files


@dataclass(frozen=True, slots=True)
class Term:
    """One shortlist entry: exact written form plus how it may be spoken."""

    canonical: str
    spoken: tuple[str, ...]  # lower-case spoken aliases, e.g. ("use session store",)
    weight: float  # relevance in [0, 1]; the server maps it to its own boost scale
    kind: TermKind
    source: TermSource


@dataclass(frozen=True, slots=True)
class Evidence:
    """One source supporting a term (provenance for history and evaluation)."""

    origin: str  # rank.Origin value: "assistant", "user", "persist_repo", "terminal_text", ...
    age: int  # prompt/response group age (0 = newest) for agent-session origins, else 0
    value: float  # origin weight x recency decay x source-internal score


@dataclass(frozen=True, slots=True)
class TermTrace:
    """Why a term is on the list: its ranking score and strongest evidence."""

    canonical: str
    score: float  # ranking score (unbounded); Term.weight is derived from it
    weight: float
    evidence: tuple[Evidence, ...]  # strongest first
    competitors: tuple[str, ...] = ()  # near-duplicate spellings dropped for this one


@dataclass(frozen=True, slots=True)
class FocusInfo:
    app: str = ""  # human name: "Ghostty", "kitty", "Code"
    app_id: str = ""  # bundle id (macOS) or window class (Linux)
    title: str = ""
    pid: int | None = None
    is_terminal: bool = False
    window_id: str = ""  # opaque, used to re-check focus before paste


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    focus: FocusInfo = field(default_factory=FocusInfo)
    cwd: str | None = None  # working directory of the focused terminal's foreground shell/agent
    repo_root: str | None = None
    branch: str | None = None
    agent: str | None = None  # "pi" | "claude" | "codex" when a session was matched
    agent_last_reply: str | None = None  # excerpt, scrubbed
    selection: str | None = None
    terms: tuple[Term, ...] = ()  # ranked, best first, already truncated
    collected_ms: float = 0.0
    errors: tuple[str, ...] = ()  # provider failures/timeouts (non-fatal)
    provenance: tuple[TermTrace, ...] = ()  # one per term, same order
    prefetched: bool = False  # repo/agent/vocabulary came from the background prefetch


@dataclass(frozen=True, slots=True)
class Replacement:
    start: int  # byte offsets in the *input* text
    end: int
    before: str
    after: str
    term: str  # canonical
    reason: str  # e.g. "alias match conf=0.91"


@dataclass(frozen=True, slots=True)
class WordInfo:
    """Subset of hark.v1 Word the canonicalizer needs (keeps it proto-independent)."""

    text: str
    start_ms: int
    end_ms: int
    confidence: float | None
