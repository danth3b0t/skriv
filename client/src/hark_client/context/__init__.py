"""Passive context engine (the "LOOK" stage).

Public API (stable contract; implementations live in submodules):

- ``collect(deadline_s: float, max_terms: int = 30) -> ContextSnapshot``
  Never raises; returns a partial snapshot with ``errors`` populated on failures/timeouts.
- ``canonicalize(text: str, words: Sequence[WordInfo], terms: Sequence[Term],
  min_confidence: float = 0.5) -> tuple[str, list[Replacement]]``
  Deterministically rewrites spoken aliases to canonical spellings only when the alias
  appears in the transcript (optionally gated on word confidence). Never introduces a
  term that was not acoustically present.
- ``focus_token() -> str``
  Cheap (~1 ms on macOS) id of the focused window; equals ``FocusInfo.window_id`` while the
  same window stays focused, "" when unknown. For re-checking focus right before pasting.
- ``start_prefetch(data_dir: str | None = None, *, learn: bool = True) -> Prefetcher`` /
  ``stop_prefetch()``: the background daemon that keeps the focused window's slow context
  (repository, agent session, learned vocabulary) warm, so ``collect()`` only reads the
  volatile sources at key-down; it also learns vocabulary from ended agent sessions into
  ``data_dir`` (the client's ``history.dir``). Optional; ``collect()`` works without it.
- ``caret_context(focus: FocusInfo) -> CaretContext``
  Text just before/after the caret of the focused field (Accessibility, AT-SPI, tmux), with
  whether it can be trusted; for smart spacing right before pasting. Never raises; ~200 ms
  hard deadline.
"""

from __future__ import annotations

from hark_client.context.canon import canonicalize
from hark_client.context.caret import CaretContext, caret_context
from hark_client.context.engine import collect
from hark_client.context.focus import focus_token
from hark_client.context.prefetch import start_prefetch, stop_prefetch
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
    "CaretContext",
    "ContextSnapshot",
    "FocusInfo",
    "Replacement",
    "Term",
    "TermKind",
    "TermSource",
    "WordInfo",
    "canonicalize",
    "caret_context",
    "collect",
    "focus_token",
    "start_prefetch",
    "stop_prefetch",
]
