"""Merge term candidates from all providers into one ranked, deduplicated shortlist.

weight = SOURCE_WEIGHT[source] * (0.6 + 0.4 * score) + 0.05 per additional source, capped at 1,
where ``score`` in [0, 1] is the provider's own recency/frequency rank (1 = most recent).
The written form is the dedupe key; the strongest occurrence decides source and kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING

from hark_client.context.aliases import is_interesting_identifier, spoken_forms
from hark_client.context.types import Term, TermKind, TermSource

if TYPE_CHECKING:
    from collections.abc import Iterable

SOURCE_WEIGHT: dict[TermSource, float] = {
    TermSource.LEARNED: 1.0,
    TermSource.SELECTION: 1.0,
    TermSource.VOCABULARY: 0.9,
    TermSource.AGENT_SESSION: 0.85,
    TermSource.FOCUSED_TEXT: 0.8,
    TermSource.WINDOW_TITLE: 0.7,
    TermSource.TERMINAL_TEXT: 0.7,
    TermSource.REPO_RECENT: 0.55,
    TermSource.REPO: 0.3,
}
MULTI_SOURCE_BONUS = 0.05


@dataclass(frozen=True, slots=True)
class Candidate:
    canonical: str
    kind: TermKind
    source: TermSource
    score: float = 1.0  # within-source relevance in [0, 1]


def file_candidates(path: str, source: TermSource, score: float) -> list[Candidate]:
    """A file's name (``store.ts``) and, if it is an identifier, its stem (``useStore``)."""
    p = PurePath(path)
    if not p.name:
        return []
    out = [Candidate(p.name, TermKind.PATH, source, score)]
    stem = p.name.split(".", 1)[0]  # store.test.ts -> store
    if is_interesting_identifier(stem):
        out.append(Candidate(stem, TermKind.IDENTIFIER, source, score))
    return out


def ranked(candidates: Iterable[Candidate], max_terms: int) -> tuple[Term, ...]:
    best: dict[str, tuple[float, Candidate]] = {}
    sources: dict[str, set[TermSource]] = {}
    for c in candidates:
        if not c.canonical:
            continue
        w = SOURCE_WEIGHT[c.source] * (0.6 + 0.4 * min(1.0, max(0.0, c.score)))
        sources.setdefault(c.canonical, set()).add(c.source)
        prev = best.get(c.canonical)
        if prev is None or w > prev[0]:
            best[c.canonical] = (w, c)
    scored = sorted(
        (
            (round(min(1.0, w + MULTI_SOURCE_BONUS * (len(sources[k]) - 1)), 4), k, c)
            for k, (w, c) in best.items()
        ),
        key=lambda x: (-x[0], x[1]),
    )
    terms: list[Term] = []
    for weight, canonical, c in scored:
        if len(terms) >= max_terms:
            break
        spoken = spoken_forms(canonical, c.kind)
        if spoken:
            terms.append(Term(canonical, spoken, weight, c.kind, c.source))
    return tuple(terms)
