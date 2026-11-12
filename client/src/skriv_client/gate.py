"""Anchor confidence for the biased decode (docs/context-engine.md, "Anchor confidence").

The server decodes the same audio twice: biased (``n_best[0]``) and unbiased (``anchor``).
A boosted beam's word confidence is not acoustic evidence (~0.1 on boosted words, < 0.5 on a
quarter of the others), the anchor's is. The two word sequences are aligned (token
Levenshtein, difflib, on normalized words). A word both decodes heard gets its anchor word's
confidence. The words of a region where they differ get the confidence of the anchor's least
confident word there, or keep their own if the anchor heard nothing. Canonicalization reads
these confidences.

The text stays the biased text, with one exception: a single all-caps token that is no
English word and no shortlist word, where the anchor heard one English word, is the
anchor's word (biased "MT", anchor "empty"; docs/bench/2026-11-13-w4-rules.md).
"""

from __future__ import annotations

import string
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from skriv_client.context import WordInfo
from skriv_client.context.canon import common_words

if TYPE_CHECKING:
    from collections.abc import Sequence

    from skriv.v1 import skriv_pb2 as pb

_STRIP = string.punctuation + "\u201c\u201d\u2018\u2019\u00ab\u00bb\u2026"


@dataclass(frozen=True, slots=True)
class GateResult:
    text: str
    words: list[WordInfo]
    reverted: list[str]  # "biased -> anchor" per region that took the anchor's word


def _norm(token: str) -> str:
    return token.strip(_STRIP).lower().replace("\u2019", "'")


def _words(h: pb.Hypothesis) -> list[WordInfo]:
    if h.words:
        return [
            WordInfo(w.text, w.start_ms, w.end_ms, w.confidence if w.HasField("confidence") else None)
            for w in h.words
        ]  # fmt: skip
    return [WordInfo(t, 0, 0, None) for t in h.text.split()]


def _caps_oov(
    biased: Sequence[WordInfo], anchor: Sequence[WordInfo], terms: frozenset[str]
) -> bool:
    if len(biased) != 1 or len(anchor) != 1:
        return False
    token = biased[0].text.strip(_STRIP)
    return (
        len(token) > 1
        and token.isalpha()
        and token.isupper()
        and token.lower() not in terms
        and token.lower() not in common_words()
        and _norm(anchor[0].text) in common_words()
    )


def apply(biased: pb.Hypothesis, anchor: pb.Hypothesis, terms: Sequence[pb.BiasTerm]) -> GateResult:
    """The biased words, each with the anchor's confidence (see the module docstring)."""
    b_words, a_words = _words(biased), _words(anchor)
    a = [_norm(w.text) for w in a_words]
    b = [_norm(w.text) for w in b_words]
    term_words = frozenset(
        _norm(x)
        for t in terms
        for f in (t.canonical, *(s.text for s in t.aliases))
        for x in f.split()
    )
    out: list[WordInfo] = []
    reverted: list[str] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        aw, bw = a_words[i1:i2], b_words[j1:j2]
        if tag == "equal":
            out.extend(replace(w, confidence=x.confidence) for x, w in zip(aw, bw, strict=True))
        elif _caps_oov(bw, aw, term_words):
            out.extend(aw)
            reverted.append(f"{bw[0].text} -> {aw[0].text}")
        elif confs := [w.confidence for w in aw if w.confidence is not None]:
            out.extend(replace(w, confidence=min(confs)) for w in bw)
        else:  # the anchor heard nothing here: only the beam's own confidence
            out.extend(bw)
    text = " ".join(w.text for w in out) if reverted else biased.text
    return GateResult(text, out, reverted)
