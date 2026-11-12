"""Bias-hit derivation shared by all backends (proposal-a §7).

Decode-time boosting does not report which phrase fired, so hits are derived
from the output: canonical forms and aliases are matched against the primary
hypothesis' words (case-insensitive, surrounding punctuation ignored), and the
anchor words over the same time span decide APPLIED vs ALREADY_IN_ANCHOR.
"""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

from skriv.v1 import skriv_pb2 as pb

if TYPE_CHECKING:
    from .backends import WordOut

_STRIP = string.punctuation + "\u201c\u201d\u2018\u2019«»…"  # curly double/single quotes


def norm(token: str) -> str:
    return token.strip(_STRIP).lower()


def word_byte_spans(text: str, words: list[WordOut]) -> list[tuple[int, int]]:
    """UTF-8 byte span of each word in `text`, searched left to right."""
    spans: list[tuple[int, int]] = []
    cursor = cursor_b = 0
    for w in words:
        i = text.find(w.text, cursor)
        if i < 0:  # backend text/word mismatch: no span rather than a wrong one
            spans.append((0, 0))
            continue
        start_b = cursor_b + len(text[cursor:i].encode())
        end_b = start_b + len(w.text.encode())
        spans.append((start_b, end_b))
        cursor, cursor_b = i + len(w.text), end_b
    return spans


def derive_hits(  # noqa: PLR0913 - independent inputs, one caller
    words: list[WordOut],
    spans: list[tuple[int, int]],
    anchor: list[WordOut] | None,
    terms: list[pb.BiasTerm],
    *,
    mechanism: pb.BiasMechanism.ValueType,
    term_boosts: dict[str, float],
) -> tuple[list[pb.BiasHit], list[str]]:
    """Returns (hits, term id per word or "")."""
    forms: list[tuple[list[str], pb.BiasTerm, str, bool]] = []  # (tokens, term, form, via_alias)
    for t in terms:
        forms.append(([norm(x) for x in t.canonical.split()], t, t.canonical, False))
        forms.extend(([norm(x) for x in a.text.split()], t, a.text, True) for a in t.aliases)
    forms = [f for f in forms if f[0] and all(f[0])]
    forms.sort(key=lambda f: -len(f[0]))

    normed = [norm(w.text) for w in words]
    word_terms = [""] * len(words)
    hits: list[pb.BiasHit] = []
    i = 0
    while i < len(words):
        for tokens, term, form, via_alias in forms:
            n = len(tokens)
            if normed[i : i + n] != tokens:
                continue
            first, last = words[i], words[i + n - 1]
            # Span without the punctuation the words carry ("kubectl," -> "kubectl").
            lead = len(first.text.encode()) - len(first.text.lstrip(_STRIP).encode())
            trail = len(last.text.encode()) - len(last.text.rstrip(_STRIP).encode())
            hit = pb.BiasHit(
                term_id=term.id,
                matched_form=form,
                via_alias=via_alias,
                mechanism=mechanism,
                text_byte_start=spans[i][0] + lead,
                text_byte_end=spans[i + n - 1][1] - trail,
                start_ms=first.start_ms,
                end_ms=last.end_ms,
            )
            if term.id in term_boosts:
                hit.boost = term_boosts[term.id]
            if anchor is None:
                hit.decision = pb.BIAS_HIT_DECISION_UNSPECIFIED
            else:
                over = [
                    w.text for w in anchor if w.start_ms < last.end_ms and w.end_ms > first.start_ms
                ]
                hit.anchor_text = " ".join(over)
                over_n = [norm(x) for x in over]
                present = any(over_n[k : k + n] == tokens for k in range(len(over_n) - n + 1))
                hit.decision = (
                    pb.BIAS_HIT_DECISION_ALREADY_IN_ANCHOR
                    if present
                    else pb.BIAS_HIT_DECISION_APPLIED
                )
            hits.append(hit)
            for k in range(i, i + n):
                word_terms[k] = term.id
            i += n
            break
        else:
            i += 1
    return hits, word_terms
