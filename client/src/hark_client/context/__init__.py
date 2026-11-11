"""Passive context engine (the "LOOK" stage).

Public API (stable contract; implementations live in submodules):

- ``collect(deadline_s: float, max_terms: int = 60) -> ContextSnapshot``
  Never raises; returns a partial snapshot with ``errors`` populated on failures/timeouts.
- ``canonicalize(text: str, words: Sequence[WordInfo], terms: Sequence[Term],
  min_confidence: float = 0.5) -> tuple[str, list[Replacement]]``
  Deterministically rewrites spoken aliases to canonical spellings only when the alias
  appears in the transcript (optionally gated on word confidence). Never introduces a
  term that was not acoustically present.
"""

from __future__ import annotations

from hark_client.context.canon import canonicalize
from hark_client.context.engine import collect
from hark_client.context.types import (
    ContextSnapshot,
    FocusInfo,
    Replacement,
    Term,
    TermKind,
    TermSource,
    WordInfo,
)

__all__ = [
    "ContextSnapshot",
    "FocusInfo",
    "Replacement",
    "Term",
    "TermKind",
    "TermSource",
    "WordInfo",
    "canonicalize",
    "collect",
]
