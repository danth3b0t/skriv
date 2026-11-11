"""Deterministic alias -> canonical rewriting of an ASR transcript.

Rules (each guards against false substitutions):

- Matching is case- and punctuation-insensitive on whole words: the transcript is split into
  whitespace-separated chunks, leading/trailing punctuation is ignored, and a match must start
  at the beginning of a chunk and end at the end of a chunk (``session_store_v2`` never
  matches the alias ``session store``).
- A match never crosses a sentence/clause boundary (``, ; : ! ?`` or a period followed by
  whitespace between two chunks).
- Longest match first, left to right, non-overlapping.
- Single-word aliases only count when the canonical form carries information a plain word
  cannot (internal capitals, digits or punctuation: ``TypeScript``, ``utf8``); otherwise
  ``store`` -> ``Store`` style rewrites would mangle prose.
- An alias shared by two different canonicals of equal weight is ambiguous and ignored.
- When word timings/confidences are given, every word overlapping the match must have
  confidence >= ``min_confidence`` (words without a confidence don't gate).
- Only aliases present in the transcript are rewritten, so no term is ever introduced.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from hark_client.context.types import Replacement, Term, WordInfo

if TYPE_CHECKING:
    from collections.abc import Sequence

_TOKEN_RE = re.compile(r"[^\W_]+")
_CHUNK_RE = re.compile(r"\S+")
_LEAD = "\"'([{<\u00ab\u201c\u2018*"  # quotes incl. typographic ones
_TRAIL = "\"')]}>\u00bb\u201d\u2019*.,;:!?"
_CLAUSE_PUNCT = frozenset(",;:!?")


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(m.group().lower() for m in _TOKEN_RE.finditer(text))


def _single_word_ok(canonical: str) -> bool:
    core = canonical.strip()
    if not core:
        return False
    if any(not c.isalpha() for c in core):
        return True  # digits/punctuation: utf8, store.ts, --verbose
    return core not in {core.lower(), core.capitalize(), core.upper()}


class _Tok:
    __slots__ = ("chunk", "end", "first", "last", "norm", "start")

    def __init__(self, norm: str, start: int, end: int, chunk: int) -> None:
        self.norm = norm
        self.start = start
        self.end = end
        self.chunk = chunk
        self.first = False
        self.last = False


def _tokenize(text: str) -> tuple[list[_Tok], list[bool]]:
    """Tokens with char spans, plus per-chunk "a boundary follows this chunk" flags."""
    toks: list[_Tok] = []
    boundary_after: list[bool] = []
    for ci, m in enumerate(_CHUNK_RE.finditer(text)):
        chunk = m.group()
        lead = len(chunk) - len(chunk.lstrip(_LEAD))
        core_end = len(chunk.rstrip(_TRAIL))
        trail = chunk[core_end:]
        boundary_after.append(bool(set(trail) & _CLAUSE_PUNCT) or "." in trail)
        core = chunk[lead:core_end] if core_end > lead else ""
        chunk_toks = [
            _Tok(t.group().lower(), m.start() + lead + t.start(), m.start() + lead + t.end(), ci)
            for t in _TOKEN_RE.finditer(core)
        ]
        if chunk_toks:
            chunk_toks[0].first = True
            chunk_toks[-1].last = True
            # boundary inside a chunk only at its edges; core start/end define the match span
            chunk_toks[0].start = m.start() + lead
            chunk_toks[-1].end = m.start() + core_end
        toks.extend(chunk_toks)
    return toks, boundary_after


def _build_index(terms: Sequence[Term]) -> dict[tuple[str, ...], Term | None]:
    """alias tokens -> term (None = ambiguous)."""
    index: dict[tuple[str, ...], Term | None] = {}
    for term in terms:
        for alias in term.spoken:
            key = _tokens(alias)
            if not key or (len(key) == 1 and not _single_word_ok(term.canonical)):
                continue
            if key not in index:
                index[key] = term
                continue
            other = index[key]
            if other is None or other.canonical == term.canonical:
                continue
            if term.weight > other.weight:
                index[key] = term
            elif term.weight == other.weight:
                index[key] = None
    return index


def _word_spans(text: str, words: Sequence[WordInfo]) -> list[tuple[int, int, float | None]]:
    """Locate each word in ``text`` (in order); unlocatable words are skipped."""
    spans: list[tuple[int, int, float | None]] = []
    cursor = 0
    lower = text.lower()
    for w in words:
        needle = w.text.strip().lower()
        if not needle:
            continue
        at = lower.find(needle, cursor)
        if at < 0:
            continue
        spans.append((at, at + len(needle), w.confidence))
        cursor = at + len(needle)
    return spans


def _min_conf(spans: list[tuple[int, int, float | None]], start: int, end: int) -> float | None:
    confs = [c for s, e, c in spans if s < end and e > start and c is not None]
    return min(confs) if confs else None


def _byte_offset(text: str, index: int) -> int:
    return len(text[:index].encode())


def canonicalize(
    text: str,
    words: Sequence[WordInfo],
    terms: Sequence[Term],
    min_confidence: float = 0.5,
) -> tuple[str, list[Replacement]]:
    """Rewrite spoken aliases in ``text`` to canonical spellings; see module docstring."""
    index = _build_index(terms)
    if not index or not text:
        return text, []
    max_len = max(len(k) for k in index)
    toks, boundary_after = _tokenize(text)
    spans = _word_spans(text, words) if words else []
    out: list[str] = []
    reps: list[Replacement] = []
    pos = 0
    i = 0
    while i < len(toks):
        if not toks[i].first:
            i += 1
            continue
        match = _longest_match(toks, boundary_after, i, max_len, index)
        if match is None:
            i += 1
            continue
        j, term = match
        start, end = toks[i].start, toks[j].end
        before = text[start:end]
        conf = _min_conf(spans, start, end)
        if before == term.canonical or (conf is not None and conf < min_confidence):
            i = j + 1
            continue
        out.append(text[pos:start])
        out.append(term.canonical)
        pos = end
        reason = f"alias match conf={conf:.2f}" if conf is not None else "alias match conf=n/a"
        reps.append(
            Replacement(
                start=_byte_offset(text, start),
                end=_byte_offset(text, end),
                before=before,
                after=term.canonical,
                term=term.canonical,
                reason=reason,
            )
        )
        i = j + 1
    out.append(text[pos:])
    return "".join(out), reps


def _longest_match(
    toks: list[_Tok],
    boundary_after: list[bool],
    i: int,
    max_len: int,
    index: dict[tuple[str, ...], Term | None],
) -> tuple[int, Term] | None:
    """Longest alias starting at token ``i`` that ends on a chunk end, without crossing a boundary."""
    stop = i
    while stop + 1 < len(toks) and stop + 1 - i < max_len:
        if toks[stop].last and boundary_after[toks[stop].chunk]:
            break
        stop += 1
    for j in range(stop, i - 1, -1):
        if not toks[j].last:
            continue
        term = index.get(tuple(t.norm for t in toks[i : j + 1]))
        if term is not None:
            return j, term
    return None
