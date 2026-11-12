"""Deterministic resolution of restart self-corrections, before the cleanup model.

"I want it written in C um no in Rust" -> "I want it written in Rust": a correction cue
("no", "sorry", "actually", "no wait", "I mean", ...) followed by a word that repeats an
earlier word ("in ... in") within MAX_RETRACTED_WORDS is a restart. The words from that
earlier word up to the cue are retracted, so they are removed together with the cue.

This does not depend on the cleanup model noticing the correction or on the ASR
punctuating the cue, and it uses the same structural rules as the cleanup validator
(cleanup.self_correction_cut): no sentence ends inside the retracted words, and a "?" or
"!" around the cue means an answer, not a correction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hark_client.cleanup import BARE_CUES, CORRECTION_CUES, FILLERS, MAX_RETRACTED_WORDS

_TOKENS = re.compile(r"[\w']+")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_CUES = sorted((*CORRECTION_CUES, *BARE_CUES), key=len, reverse=True)  # longest first
MAX_PASSES = 4


@dataclass(frozen=True, slots=True)
class Correction:
    retracted: str  # removed text (retracted words + cue + fillers)
    anchor: str  # the repeated word the correction restarts from


def _cue_at(words: list[str], i: int) -> int:
    for cue in _CUES:
        if tuple(words[i : i + len(cue)]) == cue:
            return len(cue)
    return 0


def _one(text: str) -> tuple[str, Correction] | None:
    toks = list(_TOKENS.finditer(text))
    words = [t.group().lower() for t in toks]
    for i in range(1, len(words)):
        n = _cue_at(words, i)
        if not n:
            continue
        c = i + n
        while c < len(words) and words[c] in FILLERS:
            c += 1
        if c >= len(words) or words[c] in FILLERS:
            continue
        gap = text[toks[i - 1].end() : toks[c].start()]
        if "?" in gap or "!" in gap:
            continue  # "Is it done? No, it is." is an answer
        last = next((k for k in range(i - 1, -1, -1) if words[k] not in FILLERS), -1)
        counted = 0
        for k in range(i - 1, -1, -1):
            if words[k] in FILLERS:
                continue
            counted += 1
            if counted > MAX_RETRACTED_WORDS:
                break
            if words[k] != words[c]:
                continue
            if counted < 2:  # nothing retracted but the anchor ("in no in")
                break
            if _SENTENCE_END.search(text, toks[k].start(), toks[last].end()):
                break  # the retracted words would cross a sentence
            start, end = toks[k].start(), toks[c].start()
            rest = text[end:]
            if toks[k].group()[:1].isupper() and rest[:1].islower():
                rest = rest[:1].upper() + rest[1:]  # it started a sentence
            return text[:start] + rest, Correction(text[start:end].strip(), words[c])
    return None


def resolve(text: str) -> tuple[str, list[Correction]]:
    """Apply every restart correction in `text` (left to right)."""
    found: list[Correction] = []
    for _ in range(MAX_PASSES):
        hit = _one(text)
        if hit is None:
            break
        text, corr = hit
        found.append(corr)
    return text, found
