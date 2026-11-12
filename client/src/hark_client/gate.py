"""Acoustic anchor gate (docs/research-ro/context-architecture.md §7.1).

Runs on the FinalResult before canonicalization. The server decodes the same audio
twice: biased (``n_best[0]``) and unbiased (``anchor``). The gate has two jobs.

Confidence: a boosted beam's word confidence is not acoustic evidence (~0.1 on boosted
words, < 0.5 on a quarter of the others), the anchor's is. Every biased word gets the
confidence of the anchor word aligned to it; a word in a differing region gets the
anchor's least confident word there (its own confidence if the anchor heard nothing).
Canonicalization reads these confidences whether or not any text is reverted.

Text: each place where the two decodes differ is a region. A region that overlaps no
bias hit keeps the biased words (``no_hit``): the bias did not act there, and the anchor
is no better a guess. One exception reverts it (``caps_oov``): a single all-caps token that is
no English word and no shortlist term where the anchor heard one dictionary word (biased
"MT", anchor "empty"). A bias-hit region (``BiasHit`` on the biased text) keeps them too
when the anchor's words have the same letters (``same_letters``: case, spacing or
punctuation only). Otherwise it keeps the biased words only if all of these hold, checked
in this order (the first failure is the logged reason):

- ``inserted``: the anchor heard something there;
- ``outside_term``: the other biased words in the region only re-split the anchor's
  letters (anchor "Harkon", biased "Hark on"). If they don't and the anchor has fewer
  words, it dropped words the term does not cover: the biased words stay
  (``anchor_dropped``: "the is terminal" is not reverted to "this terminal");
- ``in_anchor``: the anchor did not already say one of the term's forms (then the
  anchor's words stay and canonicalization writes the term). If the anchor's spelling
  differs from that form in case or symbols only, the biased words stay (``anchor_case``:
  "model.Train" for ``model.train``, which canonicalization would not fix);
- ``negation`` / ``number``: no negation added or removed, no number (digits or number
  words) changed;
- ``inflection``: not an inflection-only change (``parse`` -> ``parsed``). Not checked
  when the biased words are exactly one of the term's forms and the bias only added the
  suffix (anchor "Hyper LAN", biased "hyper land"): it did not strip one the speaker said;
- ``not_close``: the anchor heard something close to one of the term's forms: normalized
  edit distance on letters or on a phonetic key <= ``max_distance``;
- ``word_dropped``: no function word dropped that the term does not contain
  (``parse the config`` -> ``parse config``). A function word whose letters align with
  letters of the closest form is part of the term, not dropped ("a val" for "eval");
- ``anchor_confident``: the anchor was not sure of what it heard: its least confident
  word in the region is below ``confident_anchor`` (a confidently heard "hard" stays
  "hard" even though it is close to "hark"). A COMMAND term followed by one of its
  subcommands ("Git push") is evidence enough for the biased words.

A failed region gets the anchor's words back. The output is the biased words with the
reverted regions spliced in. Alignment: token Levenshtein (difflib) on normalized words; a
hit span is widened to whole replaced runs so both sides of a region cover the same speech.
"""

from __future__ import annotations

import itertools
import re
import string
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from hark.v1 import hark_pb2 as pb
from hark_client.context import WordInfo
from hark_client.context.canon import common_words
from hark_client.symbols import SUBCOMMANDS

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

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
# reasons that keep
_KEEP = frozenset(("close", "no_hit", "same_letters", "anchor_dropped", "anchor_case"))


@dataclass(frozen=True, slots=True)
class GateDecision:
    decision: str  # kept | reverted
    reason: str  # why it was kept (see _KEEP) or reverted
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


def _suffix_added(a: Sequence[str], b: Sequence[str]) -> bool:
    """Every differing pair: the biased word is the longer one ("lan" -> "land")."""
    return all(len(y) > len(x) for x, y in zip(a, b, strict=True) if x != y)


def _dropped(word_letters: list[str], heard: str, said: str) -> list[bool]:
    """Per anchor word (its letters, in order, making up ``heard``): are most of its letters
    unaligned with ``said`` (deleted, not replaced)?"""
    deleted = [False] * len(heard)
    for tag, i1, i2, _, _ in SequenceMatcher(None, heard, said, autojunk=False).get_opcodes():
        if tag == "delete":
            deleted[i1:i2] = [True] * (i2 - i1)
    out: list[bool] = []
    pos = 0
    for w in word_letters:
        out.append(sum(deleted[pos : pos + len(w)]) * 2 > len(w))
        pos += len(w)
    return out


def _judge(  # noqa: PLR0911, PLR0913 - one return per rule, in order
    raw: list[str],
    b: list[str],
    hit: tuple[int, int],
    forms: list[str],
    *,
    anchor_conf: float | None,
    subcommand: bool,
    cfg: GateConfig,
) -> tuple[str, float | None]:
    """Reason for a hit region (a reason in _KEEP keeps it). raw: the anchor words of the
    region as heard; b: normalized biased words of the region; hit: the bias-hit words within
    b; forms: what those words may stand for (every combination of the hits' forms);
    anchor_conf: least confident anchor word; subcommand: a COMMAND term of the hits is
    followed by one of its subcommands in the biased text."""
    a = [_norm(w) for w in raw]
    if not a:
        return "inserted", None
    heard = letters(" ".join(a))
    if heard == letters(" ".join(b)):
        return "same_letters", 0.0
    pre, post = letters(" ".join(b[: hit[0]])), letters(" ".join(b[hit[1] :]))
    if not (heard.startswith(pre) and heard.endswith(post) and len(pre) + len(post) < len(heard)):
        return ("anchor_dropped" if len(a) < len(b) else "outside_term"), None
    for f in forms:
        if a == [*b[: hit[0]], *(_norm(x) for x in f.split()), *b[hit[1] :]]:
            spelled = " ".join(raw[hit[0] : len(raw) - (len(b) - hit[1])]).strip(_STRIP)
            return ("in_anchor" if spelled == f else "anchor_case"), 0.0
    if _negations(a) != _negations(b):
        return "negation", None
    if _numbers(a) != _numbers(b):
        return "number", None
    exact = any(b[hit[0] : hit[1]] == [_norm(x) for x in f.split()] for f in forms)
    if _inflection_only(a, b) and not (exact and _suffix_added(a, b)):
        return "inflection", None
    mid = heard[len(pre) : len(heard) - len(post)]
    best = min(forms, key=lambda f: closeness(mid, [f]))
    d = round(closeness(mid, [best]), 3)
    if d > cfg.max_distance:
        return "not_close", d
    form_words = {_norm(x) for f in forms for x in f.split()}
    said = pre + letters(best) + post
    unaligned = _dropped([letters(w) for w in a], heard, said)
    if any(
        w in _FUNCTION_WORDS and w not in b and w not in form_words and gone
        for w, gone in zip(a, unaligned, strict=True)
    ):
        return "word_dropped", d
    if anchor_conf is not None and anchor_conf >= cfg.confident_anchor and not subcommand:
        return "anchor_confident", d
    return "close", d


def _caps_oov(biased: list[str], anchor: list[str], term_words: frozenset[str]) -> bool:
    """One all-caps token that is no English word and no shortlist word, where the anchor
    heard one English word (biased "MT", anchor "empty")."""
    if len(biased) != 1 or len(anchor) != 1:
        return False
    token, heard = biased[0].strip(_STRIP), _norm(anchor[0])
    return (
        len(token) > 1
        and token.isalpha()
        and token.isupper()
        and token.lower() not in term_words
        and token.lower() not in common_words()
        and heard in common_words()
    )


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


def _hit_spans(
    biased: pb.Hypothesis, b_words: Sequence[WordInfo], hits: Sequence[pb.BiasHit]
) -> list[tuple[int, int, pb.BiasHit]]:
    """Each hit as the biased words [start, end) its byte span covers."""
    spans = _byte_spans(biased.text, b_words)
    out: list[tuple[int, int, pb.BiasHit]] = []
    for hit in hits:
        if hit.text_byte_end <= hit.text_byte_start:
            continue  # REJECTED: no span
        inside = [
            k for k, (s, e) in enumerate(spans)
            if e > s and s < hit.text_byte_end and e > hit.text_byte_start
        ]  # fmt: skip
        if inside:
            out.append((inside[0], inside[-1] + 1, hit))
    return out


def apply(  # noqa: PLR0915 - one pass over the regions
    biased: pb.Hypothesis,
    anchor: pb.Hypothesis | None,
    hits: Sequence[pb.BiasHit],
    terms: Sequence[pb.BiasTerm],
    cfg: GateConfig,
) -> GateResult:
    """Gate ``biased`` against ``anchor``; without an anchor the biased result passes as is.
    Biased words carry the anchor's confidence (see the module docstring)."""
    b_words = _words(biased)
    if anchor is None:
        return GateResult(biased.text, b_words, [])
    a_words = _words(anchor)
    a = [_norm(w.text) for w in a_words]
    b = [_norm(w.text) for w in b_words]

    forms_of = {t.id: [t.canonical, *(x.text for x in t.aliases)] for t in terms}
    canonical_of = {t.id: t.canonical for t in terms}
    commands = {t.id: t.canonical.lower() for t in terms if t.kind == pb.TERM_KIND_COMMAND}
    term_words = frozenset(
        _norm(x) for forms in forms_of.values() for f in forms for x in f.split()
    )

    def aligned(a0: int, a1: int, b0: int, b1: int) -> None:
        """Equal words: the biased spelling (case) with the anchor's confidence."""
        out.extend(
            replace(bw, confidence=aw.confidence)
            for aw, bw in zip(a_words[a0:a1], b_words[b0:b1], strict=True)
        )

    out: list[WordInfo] = []
    decisions: list[GateDecision] = []
    pos_a = pos_b = 0  # next anchor / biased word
    reverted = False
    for r in _regions(a, b, _hit_spans(biased, b_words, hits)):
        aligned(pos_a, r.a0, pos_b, r.b0)
        pos_a, pos_b = r.a1, r.b1
        ar, br = a[r.a0 : r.a1], b[r.b0 : r.b1]
        if ar == br:  # a hit the anchor already had
            aligned(r.a0, r.a1, r.b0, r.b1)
            continue
        confs = [w.confidence for w in a_words[r.a0 : r.a1] if w.confidence is not None]
        conf = min(confs) if confs else None
        term, dist = "", None
        b_raw = [w.text for w in b_words[r.b0 : r.b1]]
        a_raw = [w.text for w in a_words[r.a0 : r.a1]]
        if not r.hits:
            reason = "caps_oov" if _caps_oov(b_raw, a_raw, term_words) else "no_hit"
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
            subcommand = end < len(b) and any(
                b[end] in SUBCOMMANDS.get(commands.get(h.term_id, ""), ()) for _, _, h in r.hits
            )
            reason, dist = _judge(
                a_raw, br, hit, forms, anchor_conf=conf, subcommand=subcommand, cfg=cfg
            )
        keep = reason in _KEEP
        decisions.append(
            GateDecision(
                KEPT if keep else REVERTED,
                reason,
                " ".join(b_raw),
                " ".join(a_raw),
                term,
                dist,
            )
        )
        if not keep:
            out.extend(a_words[r.a0 : r.a1])
            reverted = True
        elif conf is None:  # the anchor heard nothing here: only the beam's own confidence
            out.extend(b_words[r.b0 : r.b1])
        else:
            out.extend(replace(w, confidence=conf) for w in b_words[r.b0 : r.b1])
    aligned(pos_a, len(a_words), pos_b, len(b_words))
    text = " ".join(w.text for w in out) if reverted else biased.text
    return GateResult(text, out, decisions)
