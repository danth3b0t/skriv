"""Merge term candidates from all sources into one ranked, deduplicated shortlist.

The recipe selected on development data in ``docs/research/context-ranking/REPORT.md``
(``tune_n8_d0.9``). For a term with evidence from origin ``s`` at age ``a`` (prompt/response
group, 0 = newest; 0 for sources without turns) and source-internal score ``r``
(proximity, file rank, persistence; 1 by default):

    v     = ORIGIN_WEIGHT[s] * DECAY**a * r
    F     = sum(DECAY**a over the distinct (origin, age) pairs supporting the term)
    score = max(v) * (1 + FREQUENCY * log1p(max(0, F - 1))) + SOURCE_BONUS * (origins - 1)

There is no recency floor (stale items fade) and no saturation before sorting (strong terms
stay ordered); ties go to the exact canonical string. Near-duplicates (equal letters and
digits ignoring case and separators: ``parse_config``/``parseConfig``/``parse-config``)
compete and only the best scoring one is kept. ``Term.weight = 1 - exp(-1.5 * score)`` maps
the unbounded score monotonically into [0, 1); acoustic calibration is still open.

Weights marked [U] are not from the experiment (it had no screen sources).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath
from typing import TYPE_CHECKING

from hark_client.context.aliases import is_interesting_identifier, spoken_forms
from hark_client.context.types import Evidence, Term, TermKind, TermSource, TermTrace

if TYPE_CHECKING:
    from collections.abc import Iterable

MAX_TERMS = 30
DECAY = 0.9
FREQUENCY = 0.15
SOURCE_BONUS = 0.12
MAX_EVIDENCE = 4  # per term in the trace


class Origin(StrEnum):
    """Where a candidate came from; finer than :class:`TermSource` (the wire provenance)."""

    SELECTION = "selection"
    USER = "user"  # the user's own earlier prompts in the agent session
    FOCUSED_TEXT = "focused_text"  # text around the caret of a text field
    TERMINAL_TEXT = "terminal_text"  # the focused terminal pane (tmux, Terminal.app)
    ASSISTANT = "assistant"  # the agent's responses
    TOOLS = "tools"  # paths of the agent's tool calls
    WINDOW_TITLE = "window_title"
    BUFFER = "buffer"  # terminal buffer without a trusted caret (Ghostty)
    PERSIST_REPO = "persist_repo"  # learned from earlier sessions in this repository
    REPO_RECENT = "repo_recent"
    CWD = "cwd"  # repository name and branch
    PERSIST_USER = "persist_user"  # learned from all earlier sessions
    REPO = "repo"  # repository-wide files


ORIGIN_WEIGHT: dict[Origin, float] = {
    Origin.SELECTION: 1.2,  # [U] explicitly chosen by the user
    Origin.USER: 1.1,
    Origin.FOCUSED_TEXT: 1.0,  # [U]
    Origin.TERMINAL_TEXT: 0.9,  # [U]
    Origin.ASSISTANT: 0.85,
    Origin.TOOLS: 0.75,
    Origin.WINDOW_TITLE: 0.7,  # [U]
    Origin.BUFFER: 0.6,  # [U]
    Origin.PERSIST_REPO: 0.55,
    Origin.REPO_RECENT: 0.45,
    Origin.CWD: 0.45,
    Origin.PERSIST_USER: 0.3,
    Origin.REPO: 0.25,
}
ORIGIN_SOURCE: dict[Origin, TermSource] = {
    Origin.SELECTION: TermSource.SELECTION,
    Origin.USER: TermSource.AGENT_SESSION,
    Origin.FOCUSED_TEXT: TermSource.FOCUSED_TEXT,
    Origin.TERMINAL_TEXT: TermSource.TERMINAL_TEXT,
    Origin.ASSISTANT: TermSource.AGENT_SESSION,
    Origin.TOOLS: TermSource.AGENT_SESSION,
    Origin.WINDOW_TITLE: TermSource.WINDOW_TITLE,
    Origin.BUFFER: TermSource.TERMINAL_TEXT,
    Origin.PERSIST_REPO: TermSource.VOCABULARY,
    Origin.REPO_RECENT: TermSource.REPO_RECENT,
    Origin.CWD: TermSource.REPO_RECENT,
    Origin.PERSIST_USER: TermSource.VOCABULARY,
    Origin.REPO: TermSource.REPO,
}


@dataclass(frozen=True, slots=True)
class Candidate:
    canonical: str
    kind: TermKind
    origin: Origin
    score: float = 1.0  # source-internal relevance r >= 0
    age: int = 0  # prompt/response group age; 0 for sources without turns


@dataclass(frozen=True, slots=True)
class Ranking:
    terms: tuple[Term, ...]  # best first
    trace: tuple[TermTrace, ...]  # one per term, same order


def file_candidates(path: str, origin: Origin, score: float, age: int = 0) -> list[Candidate]:
    """A file's name (``store.ts``) and, if it is an identifier, its stem (``useStore``)."""
    p = PurePath(path)
    if not p.name:
        return []
    out = [Candidate(p.name, TermKind.PATH, origin, score, age)]
    stem = p.name.split(".", 1)[0]  # store.test.ts -> store
    if is_interesting_identifier(stem):
        out.append(Candidate(stem, TermKind.IDENTIFIER, origin, score, age))
    return out


def competition_key(canonical: str) -> str:
    """Spelling-insensitive identity: ``parse_config``, ``parseConfig`` -> ``parseconfig``."""
    return re.sub(r"[^0-9a-z]", "", canonical.lower())


@dataclass(slots=True)
class _Merged:
    kind: TermKind
    origin: Origin  # of the strongest evidence
    best: float
    support: dict[tuple[Origin, int], float]  # strongest value per (origin, age)


def _score(m: _Merged) -> float:
    freq = sum(DECAY**age for _, age in m.support)
    origins = len({origin for origin, _ in m.support})
    return m.best * (1 + FREQUENCY * math.log1p(max(0.0, freq - 1))) + SOURCE_BONUS * (origins - 1)


def rank(candidates: Iterable[Candidate], max_terms: int = MAX_TERMS) -> Ranking:
    merged: dict[str, _Merged] = {}
    for c in candidates:
        if not c.canonical or c.score <= 0:
            continue
        v = ORIGIN_WEIGHT[c.origin] * DECAY**c.age * c.score
        m = merged.get(c.canonical)
        if m is None:
            merged[c.canonical] = _Merged(c.kind, c.origin, v, {(c.origin, c.age): v})
            continue
        key = (c.origin, c.age)
        m.support[key] = max(v, m.support.get(key, 0.0))
        if v > m.best:
            m.best, m.kind, m.origin = v, c.kind, c.origin
    scored = sorted(((_score(m), k) for k, m in merged.items()), key=lambda x: (-x[0], x[1]))
    winners: dict[str, tuple[float, str, list[str]]] = {}  # competition key -> best, losers
    for score, canonical in scored:
        key = competition_key(canonical)
        if not key:
            continue
        if key in winners:
            winners[key][2].append(canonical)
        else:
            winners[key] = (score, canonical, [])
    terms: list[Term] = []
    trace: list[TermTrace] = []
    for score, canonical, losers in winners.values():  # insertion order = score order
        if len(terms) >= max_terms:
            break
        m = merged[canonical]
        spoken = spoken_forms(canonical, m.kind)
        if not spoken:
            continue
        weight = round(1 - math.exp(-1.5 * score), 4)
        terms.append(Term(canonical, spoken, weight, m.kind, ORIGIN_SOURCE[m.origin]))
        evidence = sorted(m.support.items(), key=lambda x: (-x[1], x[0]))[:MAX_EVIDENCE]
        trace.append(
            TermTrace(
                canonical,
                round(score, 4),
                weight,
                tuple(Evidence(o.value, age, round(v, 4)) for (o, age), v in evidence),
                tuple(losers),
            )
        )
    return Ranking(tuple(terms), tuple(trace))
