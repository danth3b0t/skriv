"""Acoustic anchor gate (docs/research-ro/context-architecture.md §7.1).

Runs on the FinalResult before canonicalization. The server decodes the same audio
twice: biased (``n_best[0]``) and unbiased (``anchor``). Each place where they differ
is a region. A region keeps the biased words only if all of these hold, checked in
this order (the first failure is the logged reason):

- ``inserted``: the anchor heard something there;
- ``outside_term``: it is a bias-hit span (``BiasHit`` on the biased text), and the other
  biased words in it only re-split the anchor's letters (anchor "Harkon", biased "Hark on");
- ``in_anchor``: the anchor did not already say one of the term's forms (then the
  anchor's words stay and canonicalization writes the term);
- ``negation`` / ``number``: no negation added or removed, no number (digits or number
  words) changed;
- ``inflection``: not an inflection-only change (``parse`` -> ``parsed``);
- ``word_dropped``: no function word dropped that the term does not contain
  (``parse the config`` -> ``parse config``);
- ``not_close``: the anchor heard something close to one of the term's forms: normalized
  edit distance on letters or on a phonetic key <= ``max_distance``;
- ``anchor_confident``: the anchor was not sure of what it heard: its least confident
  word in the region is below ``confident_anchor`` (a confidently heard "hard" stays
  "hard" even though it is close to "hark").

Every other region, including every change outside a bias-hit span, gets the anchor's
words back. The output is the anchor with the kept regions spliced in. Alignment: token
Levenshtein (difflib) on normalized words; a hit span is widened to whole replaced runs
so both sides of a region cover the same speech.
"""

from __future__ import annotations

import itertools
import re
import string
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from hark_client.context import WordInfo

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from hark.v1 import hark_pb2 as pb
    from hark_client.config import GateConfig

_STRIP = string.punctuation + "\u201c\u201d\u2018\u2019\u00ab\u00bb\u2026"
_NEGATIONS = frozenset(
    ("not", "no", "never", "nothing", "none", "nobody", "nowhere", "neither", "nor", "cannot", "without")
)  # fmt: skip
_FUNCTION_WORDS = frozenset(
    ("the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or", "is", "it", "by", "with",
     "from", "as", "be", "this", "that")
)  # fmt: skip
_INFLECTIONS = frozenset(("", "s", "es", "ed", "d", "ing", "er", "ers", "'s", "ies", "ied"))
_NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen")
    )
} | {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "million": 10**6,
}  # fmt: skip
_DIGITS = re.compile(r"\d+")
_MAX_FORMS = 256  # form combinations tried for a region with several hits

KEPT, REVERTED = "kept", "reverted"


@dataclass(frozen=True, slots=True)
class GateDecision:
    decision: str  # kept | reverted
    reason: str  # "close" when kept, else the failed rule (see the module docstring)
    biased: str  # the biased words of the region
    anchor: str  # the anchor words of the same region
    term: str = ""  # canonical(s) of the bias hit(s) ("" outside any hit)
    distance: float | None = None  # best normalized distance anchor <-> term form


@dataclass(frozen=True, slots=True)
class GateResult:
    text: str
    words: list[WordInfo]
    decisions: list[GateDecision]


@dataclass
class _Region:
    a0: int
    a1: int
    b0: int
    b1: int
    hits: list[tuple[int, int, pb.BiasHit]]  # (b0, b1, hit): the hit's own biased words


_TOKEN_RE = re.compile(r"[^\W_]+")
_PHONETIC = (("ph", "f"), ("ck", "k"), ("c", "k"), ("q", "k"), ("x", "ks"), ("z", "s"))


def letters(s: str) -> str:
    return "".join(_TOKEN_RE.findall(s.lower()))


def phonetic_key(s: str) -> str:
    """Crude sound key: c/k/q merge, vowels after the first letter dropped, repeats collapsed."""
    s = letters(s)
    for a, b in _PHONETIC:
        s = s.replace(a, b)
    if not s:
        return ""
    key = s[0] + "".join(c for c in s[1:] if c not in "aeiouy")
    return re.sub(r"(.)\1+", r"\1", key)


def _norm(token: str) -> str:
    return token.strip(_STRIP).lower().replace("\u2019", "'")


def _words(h: pb.Hypothesis) -> list[WordInfo]:
    if h.words:
        return [
            WordInfo(w.text, w.start_ms, w.end_ms, w.confidence if w.HasField("confidence") else None)
            for w in h.words
        ]  # fmt: skip
    return [WordInfo(t, 0, 0, None) for t in h.text.split()]


def _byte_spans(text: str, words: Sequence[WordInfo]) -> list[tuple[int, int]]:
    """UTF-8 byte span of each word in ``text``, searched left to right ((0, 0) if absent)."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for w in words:
        i = text.find(w.text, cursor)
        if i < 0:
            spans.append((0, 0))
            continue
        start = len(text[:i].encode())
        spans.append((start, start + len(w.text.encode())))
        cursor = i + len(w.text)
    return spans


def distance(a: str, b: str) -> float:
    """Levenshtein distance / length of the longer string (1.0 if either is empty)."""
    if not a or not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(len(a), len(b))


def closeness(heard: str, forms: Iterable[str]) -> float:
    """Best distance between what the anchor heard and any form, on letters or sound."""
    return min(
        (
            min(distance(letters(heard), letters(f)), distance(phonetic_key(heard), phonetic_key(f)))
            for f in forms
        ),
        default=1.0,
    )  # fmt: skip


def _numbers(tokens: Sequence[str]) -> list[int]:
    out: list[int] = []
    for t in tokens:
        if t in _NUMBER_WORDS:
            out.append(_NUMBER_WORDS[t])
        out.extend(int(m.group()) for m in _DIGITS.finditer(t))
    return sorted(out)


def _negations(tokens: Sequence[str]) -> int:
    return sum(t in _NEGATIONS or t.endswith("n't") for t in tokens)


def _inflection_only(a: Sequence[str], b: Sequence[str]) -> bool:
    """Same word count and every differing pair shares a stem (>= 3 letters) up to a suffix."""
    if len(a) != len(b) or a == b:
        return False
    for x, y in zip(a, b, strict=True):
        if x == y:
            continue
        n = 0
        while n < min(len(x), len(y)) and x[n] == y[n]:
            n += 1
        if n < 3 or x[n:] not in _INFLECTIONS or y[n:] not in _INFLECTIONS:
            return False
    return True


def _judge(  # noqa: PLR0911, PLR0913 - one return per rule, in order
    a: list[str],
    b: list[str],
    hit: tuple[int, int],
    forms: list[str],
    *,
    anchor_conf: float | None,
    cfg: GateConfig,
) -> tuple[str, float | None]:
    """Reason for a hit region ("close" keeps it). a/b: normalized anchor/biased words of
    the region; hit: the bias-hit words within b; forms: what those words may stand for
    (every combination of the hits' forms); anchor_conf: least confident anchor word."""
    if not a:
        return "inserted", None
    heard = letters(" ".join(a))
    pre, post = letters(" ".join(b[: hit[0]])), letters(" ".join(b[hit[1] :]))
    if not (heard.startswith(pre) and heard.endswith(post) and len(pre) + len(post) < len(heard)):
        return "outside_term", None
    if any(a == [*b[: hit[0]], *(_norm(x) for x in f.split()), *b[hit[1] :]] for f in forms):
        return "in_anchor", 0.0
    if _negations(a) != _negations(b):
        return "negation", None
    if _numbers(a) != _numbers(b):
        return "number", None
    if _inflection_only(a, b):
        return "inflection", None
    form_words = {_norm(x) for f in forms for x in f.split()}
    if any(w in _FUNCTION_WORDS and w not in b and w not in form_words for w in a):
        return "word_dropped", None
    d = round(closeness(heard[len(pre) : len(heard) - len(post)], forms), 3)
    if d > cfg.max_distance:
        return "not_close", d
    if anchor_conf is not None and anchor_conf >= cfg.confident_anchor:
        return "anchor_confident", d
    return "close", d


def _regions(
    a: list[str], b: list[str], hit_spans: list[tuple[int, int, pb.BiasHit]]
) -> list[_Region]:
    """Differing regions, in order: one per bias hit (widened to whole replaced runs;
    overlapping hits merge) and one per other non-equal opcode."""
    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    op_of = {j: op for op in ops for j in range(op[3], op[4])}  # biased word -> its opcode

    regions: list[_Region] = []
    for h0, h1, hit in sorted(hit_spans, key=lambda s: s[0]):
        tag, i1, _, j1, _ = op_of[h0]
        b0, a0 = (h0, i1 + h0 - j1) if tag == "equal" else (j1, i1)
        tag, _, i2, j1, j2 = op_of[h1 - 1]
        b1, a1 = (h1, i2 - (j2 - h1)) if tag == "equal" else (j2, i2)
        if regions and b0 < regions[-1].b1:
            last = regions[-1]
            last.b1, last.a1 = max(last.b1, b1), max(last.a1, a1)
            last.hits.append((h0, h1, hit))
        else:
            regions.append(_Region(a0, a1, b0, b1, [(h0, h1, hit)]))
    for tag, i1, i2, j1, j2 in ops:
        covered = any(r.a0 <= i1 and i2 <= r.a1 and r.b0 <= j1 and j2 <= r.b1 for r in regions)
        if tag != "equal" and not covered:
            regions.append(_Region(i1, i2, j1, j2, []))
    return sorted(regions, key=lambda r: (r.b0, r.a0))


def apply(
    biased: pb.Hypothesis,
    anchor: pb.Hypothesis | None,
    hits: Sequence[pb.BiasHit],
    terms: Sequence[pb.BiasTerm],
    cfg: GateConfig,
) -> GateResult:
    """Gate ``biased`` against ``anchor``; without an anchor the biased result passes as is.

    Kept words carry the lowest anchor confidence of their region: a boosted beam's word
    confidence is not acoustic evidence (~0.1 on boosted words), the anchor's is."""
    b_words = _words(biased)
    if anchor is None:
        return GateResult(biased.text, b_words, [])
    a_words = _words(anchor)
    a = [_norm(w.text) for w in a_words]
    b = [_norm(w.text) for w in b_words]

    forms_of = {t.id: [t.canonical, *(x.text for x in t.aliases)] for t in terms}
    canonical_of = {t.id: t.canonical for t in terms}
    spans = _byte_spans(biased.text, b_words)
    hit_spans: list[tuple[int, int, pb.BiasHit]] = []
    for hit in hits:
        if hit.text_byte_end <= hit.text_byte_start:
            continue  # REJECTED: no span
        inside = [
            k for k, (s, e) in enumerate(spans)
            if e > s and s < hit.text_byte_end and e > hit.text_byte_start
        ]  # fmt: skip
        if inside:
            hit_spans.append((inside[0], inside[-1] + 1, hit))

    out: list[WordInfo] = []
    decisions: list[GateDecision] = []
    pos = 0  # next anchor word
    kept = False
    for r in _regions(a, b, hit_spans):
        out.extend(a_words[pos : r.a0])
        pos = r.a1
        ar, br = a[r.a0 : r.a1], b[r.b0 : r.b1]
        if ar == br:  # a hit the anchor already had
            out.extend(a_words[r.a0 : r.a1])
            continue
        confs = [w.confidence for w in a_words[r.a0 : r.a1] if w.confidence is not None]
        conf = min(confs) if confs else None
        term, dist = "", None
        if not r.hits:
            reason = "outside_term"
        else:
            parts: list[list[str]] = []  # per hit its forms, between hits the biased words
            end = r.hits[0][0]
            for h0, h1, h in r.hits:
                if h0 > end:
                    parts.append([" ".join(w.text for w in b_words[end:h0])])
                parts.append(forms_of.get(h.term_id, [h.matched_form]))
                end = h1
            forms = [" ".join(p) for p in itertools.islice(itertools.product(*parts), _MAX_FORMS)]
            hit = (r.hits[0][0] - r.b0, end - r.b0)
            term = ", ".join(canonical_of.get(h.term_id, h.matched_form) for _, _, h in r.hits)
            reason, dist = _judge(ar, br, hit, forms, anchor_conf=conf, cfg=cfg)
        keep = reason == "close"
        decisions.append(
            GateDecision(
                KEPT if keep else REVERTED,
                reason,
                " ".join(w.text for w in b_words[r.b0 : r.b1]),
                " ".join(w.text for w in a_words[r.a0 : r.a1]),
                term,
                dist,
            )
        )
        if keep:
            out.extend(replace(w, confidence=conf) for w in b_words[r.b0 : r.b1])
            kept = True
        else:
            out.extend(a_words[r.a0 : r.a1])
    out.extend(a_words[pos:])
    text = " ".join(w.text for w in out) if kept else anchor.text
    return GateResult(text, out, decisions)
