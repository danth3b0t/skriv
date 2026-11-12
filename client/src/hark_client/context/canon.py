"""Deterministic alias -> canonical rewriting of an ASR transcript.

Rules (each guards against false substitutions):

- Matching is case- and punctuation-insensitive on whole words: the transcript is split into
  whitespace-separated chunks, leading/trailing punctuation is ignored, and a match must start
  at the beginning of a chunk and end at the end of a chunk (``session_store_v2`` never
  matches the alias ``session store``).
- Words are compared normalized, the same way on both sides: letters and digits are split
  (``S1`` = ``s 1``), number words are digits (``twenty two`` = ``22``), and ``point``/``dot``
  between two numbers is dropped (``zero point six`` = ``0.6``).
- A match never crosses a sentence/clause boundary (``, ; : ! ?`` or a period followed by
  whitespace between two chunks).
- Longest match first, left to right, non-overlapping.
- Single-word aliases only count when the canonical form carries information a plain word
  cannot (internal capitals, digits or punctuation: ``TypeScript``, ``utf8``), or when the
  alias is no English word (``ghosty`` -> ``Ghostty``); otherwise ``store`` -> ``Store``
  style rewrites would mangle prose.
- An alias shared by two different canonicals of equal weight is ambiguous and ignored.
- When word timings/confidences are given, every word overlapping the match must have
  confidence >= ``min_confidence`` (words without a confidence don't gate).
- A match made only of everyday words (``each other``, ``session start``: every word among
  the most frequent English words, ``common_words.txt``) needs evidence that an identifier
  was meant: a spoken separator in it (``underscore``, ``dot``), a capital the recognizer
  wrote inside it mid-sentence, or identifier syntax around it (``called X``, ``rename X``,
  ``the X function``, ``X is false``).
- A match right after a spoken separator (``tilde slash dot`` + ``kube slash config``) is
  part of a longer spoken path that did not match as a whole, and is left alone.
- Only aliases present in the transcript are rewritten, so no term is ever introduced.
- A rewrite that would only lower-case the text (``Hark doctor`` -> ``hark doctor``) is
  skipped, except for a command mid-sentence (``run Hark doctor`` -> ``run hark doctor``).
- Aliases are the term's own plus the ones :func:`spoken_forms` derives now, so a shortlist
  saved by an older version gets the current spoken forms too.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import TYPE_CHECKING

from hark_client.context.aliases import LEXICON, english_words, is_english, spoken_forms
from hark_client.context.types import Replacement, Term, TermKind, WordInfo

if TYPE_CHECKING:
    from collections.abc import Sequence

_TOKEN_RE = re.compile(r"[^\W_]+")
_PART_RE = re.compile(r"\d+|[^\W\d_]+")  # s1 -> s, 1
_CHUNK_RE = re.compile(r"\S+")
_LEAD = "\"'([{<\u00ab\u201c\u2018*"  # quotes incl. typographic ones
_TRAIL = "\"')]}>\u00bb\u201d\u2019*.,;:!?"
_CLAUSE_PUNCT = frozenset(",;:!?")
_SENTENCE_PUNCT = frozenset(".!?")

_UNITS = (
    *("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
    *("eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen"),
    *("eighteen", "nineteen"),
)
_NUMBERS = {w: i for i, w in enumerate(_UNITS)}
_TENS = {
    w: 10 * i
    for i, w in enumerate(
        ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"), 2
    )
}
_DECIMAL_POINT = frozenset({"point", "dot"})
# Spoken symbols: a match next to one of these is part of a spoken path or identifier.
_SEPARATOR_WORDS = frozenset(
    {"underscore", "dash", "dot", "slash", "backslash", "colon", "tilde", "dollar"}
)
# What an identifier is called: "the X hook", "the hook X".
_KIND_NOUNS = frozenset(
    [
        "function",
        "functions",
        "method",
        "methods",
        "class",
        "classes",
        "hook",
        "hooks",
        "variable",
        "variables",
        "var",
        "field",
        "fields",
        "flag",
        "flags",
        "type",
        "types",
        "struct",
        "enum",
        "constant",
        "constants",
        "const",
        "component",
        "components",
        "module",
        "modules",
        "property",
        "properties",
        "prop",
        "props",
        "attribute",
        "attributes",
        "parameter",
        "parameters",
        "param",
        "params",
        "argument",
        "arguments",
        "arg",
        "args",
        "option",
        "options",
        "setting",
        "settings",
        "interface",
        "decorator",
        "fixture",
        "fixtures",
        "handler",
        "helper",
        "constructor",
        "column",
        "endpoint",
        "command",
    ]
)
_NAMING = frozenset({"called", "named", "rename", "renamed"})  # "a field called X"
_AFTER_IS = frozenset({"true", "false", "none", "null", "nil"})  # "X is false"
_AFTER = frozenset({"returns", "returned", "raises"})  # "X returns true for ..."
_WORDS_FILE = Path(__file__).with_name("common_words.txt")
# One-word spoken forms from the lexicon: chosen by hand, so a rare dictionary word counts
_LEXICON_WORDS = frozenset(f for forms in LEXICON.values() for f in forms if " " not in f)


@functools.cache
def common_words() -> frozenset[str]:
    """The most frequent English words (``common_words.txt``)."""
    lines = _WORDS_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(w for line in lines if not line.startswith("#") for w in line.split())


def _single_word_ok(canonical: str) -> bool:
    core = canonical.strip()
    if not core:
        return False
    if any(not c.isalpha() for c in core):
        return True  # digits/punctuation: utf8, store.ts, --verbose
    return core not in {core.lower(), core.capitalize(), core.upper()}


def _ordinary(norm: str) -> bool:
    """An everyday word (or a number): on its own no evidence of an identifier."""
    if norm.isdigit():
        return True
    return (len(norm) > 1 or norm in {"a", "i"}) and norm in common_words()


class _Tok:
    __slots__ = ("chunk", "end", "first", "last", "norm", "raw", "start")

    def __init__(self, raw: str, start: int, end: int, chunk: int) -> None:
        self.raw = raw
        self.norm = raw.lower()
        self.start = start
        self.end = end
        self.chunk = chunk
        self.first = False
        self.last = False


def _normalize(toks: list[_Tok], boundary_after: list[bool]) -> list[_Tok]:
    """Number words -> digits (``twenty two`` -> ``22``); ``point``/``dot`` between two
    numbers dropped. Never across a boundary."""

    def joined(a: _Tok) -> bool:  # nothing but spaces between a and the next token
        return not (a.last and boundary_after[a.chunk])

    for t in toks:
        if t.norm in _NUMBERS:
            t.norm = str(_NUMBERS[t.norm])
    out: list[_Tok] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.norm in _TENS:
            t.norm = str(_TENS[t.norm])
            if nxt is not None and joined(t) and nxt.norm.isdigit() and len(nxt.norm) == 1:
                t.norm = str(int(t.norm) + int(nxt.norm))
                t.raw, t.end, t.last, t.chunk = f"{t.raw} {nxt.raw}", nxt.end, nxt.last, nxt.chunk
                i += 1
        elif (
            t.norm in _DECIMAL_POINT
            and out
            and out[-1].norm.isdigit()
            and joined(out[-1])
            and joined(t)
            and nxt is not None
            and (nxt.norm.isdigit() or nxt.norm in _TENS)
            and t.first
            and t.last
        ):
            i += 1
            continue
        out.append(t)
        i += 1
    return out


def _tokenize(text: str) -> tuple[list[_Tok], list[bool], list[bool]]:
    """Normalized tokens with char spans, plus per-chunk "a boundary follows this chunk" and
    "a sentence ends after this chunk" flags."""
    toks: list[_Tok] = []
    boundary_after: list[bool] = []
    sentence_after: list[bool] = []
    for ci, m in enumerate(_CHUNK_RE.finditer(text)):
        chunk = m.group()
        lead = len(chunk) - len(chunk.lstrip(_LEAD))
        core_end = len(chunk.rstrip(_TRAIL))
        trail = chunk[core_end:]
        boundary_after.append(bool(set(trail) & _CLAUSE_PUNCT) or "." in trail)
        sentence_after.append(bool(set(trail) & _SENTENCE_PUNCT))
        core = chunk[lead:core_end] if core_end > lead else ""
        base = m.start() + lead
        chunk_toks = [
            _Tok(p.group(), base + t.start() + p.start(), base + t.start() + p.end(), ci)
            for t in _TOKEN_RE.finditer(core)
            for p in _PART_RE.finditer(t.group())
        ]
        if chunk_toks:
            chunk_toks[0].first = True
            chunk_toks[-1].last = True
            # boundary inside a chunk only at its edges; core start/end define the match span
            chunk_toks[0].start = base
            chunk_toks[-1].end = m.start() + core_end
        toks.extend(chunk_toks)
    return _normalize(toks, boundary_after), boundary_after, sentence_after


def _key(alias: str) -> tuple[str, ...]:
    toks, boundary_after, _ = _tokenize(alias)
    return tuple(t.norm for t in toks) if not any(boundary_after[:-1]) else ()


def _single_alias_ok(key: tuple[str, ...], term: Term) -> bool:
    """A one-word alias counts for a canonical that a plain word cannot be, or when the alias
    is a spoken form that is no English word, or a rare one from the lexicon (``ghosty`` ->
    ``Ghostty``)."""
    if _single_word_ok(term.canonical):
        return True
    word = key[0]
    return (
        word != term.canonical.lower()
        and word.isalpha()
        and word not in common_words()
        and (word in _LEXICON_WORDS or not is_english(word, english_words()))
    )


def _build_index(terms: Sequence[Term]) -> dict[tuple[str, ...], Term | None]:
    """alias tokens -> term (None = ambiguous)."""
    index: dict[tuple[str, ...], Term | None] = {}
    for term in terms:
        aliases = dict.fromkeys([*term.spoken, *spoken_forms(term.canonical, term.kind)])
        for key in dict.fromkeys(_key(a) for a in aliases):
            if not key or (len(key) == 1 and not _single_alias_ok(key, term)):
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


class _Text:
    """Tokens of the transcript and what surrounds a match."""

    def __init__(self, text: str) -> None:
        self.toks, self.boundary_after, self.sentence_after = _tokenize(text)

    def joined(self, a: int) -> bool:
        """Tokens a and a + 1 are in the same clause."""
        t = self.toks[a]
        return a + 1 < len(self.toks) and not (t.last and self.boundary_after[t.chunk])

    def sentence_start(self, i: int) -> bool:
        prev = self.toks[i - 1] if i > 0 else None
        return prev is None or (prev.last and self.sentence_after[prev.chunk])

    def before(self, i: int, n: int) -> list[str]:
        """Up to n normalized tokens before token i in the same clause, nearest first."""
        out: list[str] = []
        k = i - 1
        while k >= 0 and len(out) < n and self.joined(k):
            out.append(self.toks[k].norm)
            k -= 1
        return out

    def after(self, j: int, n: int) -> list[str]:
        out: list[str] = []
        k = j
        while len(out) < n and self.joined(k):
            k += 1
            out.append(self.toks[k].norm)
        return out

    def evidence(self, i: int, j: int) -> bool:
        """Something besides the words says an identifier was meant (see module docstring)."""
        span = self.toks[i : j + 1]
        if any(t.norm in _SEPARATOR_WORDS for t in span):
            return True
        skip_first = self.sentence_start(i)
        if any(
            any(c.isupper() for c in t.raw) and t.raw != "I"
            for k, t in enumerate(span)
            if k or not skip_first
        ):
            return True
        before, after = self.before(i, 6), self.after(j, 2)
        if (before and before[0] in _KIND_NOUNS) or _NAMING & set(before):
            return True
        if before[:2] == ["it", "call"] or before[:2] == ["it", "name"]:
            return True
        if after and (after[0] in _KIND_NOUNS or after[0] in _AFTER):
            return True
        return after[:1] == ["is"] and after[1:2] != [] and after[1] in _AFTER_IS


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
    tx = _Text(text)
    toks = tx.toks
    spans = _word_spans(text, words) if words else []
    out: list[str] = []
    reps: list[Replacement] = []
    pos = 0
    i = 0
    while i < len(toks):
        if not toks[i].first:
            i += 1
            continue
        match = _longest_match(toks, tx.boundary_after, i, max_len, index)
        if match is None:
            i += 1
            continue
        j, term = match
        start, end = toks[i].start, toks[j].end
        before = text[start:end]
        conf = _min_conf(spans, start, end)
        # "Hark doctor" -> "hark doctor" would only lose a capital (a command mid-sentence
        # has no reason to have one)
        case_only = (
            before.lower() == term.canonical
            and term.canonical.islower()
            and (term.kind is not TermKind.COMMAND or tx.sentence_start(i))
        )
        if before == term.canonical or case_only or (conf is not None and conf < min_confidence):
            i = j + 1
            continue
        prev = tx.before(i, 1)
        if (prev and prev[0] in _SEPARATOR_WORDS) or (
            all(_ordinary(t.norm) for t in toks[i : j + 1]) and not tx.evidence(i, j)
        ):
            i += 1  # part of a longer spoken path, or everyday words: maybe a later match
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
