"""Deterministic resolution of explicit self-corrections, before the cleanup model.

Two shapes, both after a correction cue ("no", "sorry", "actually", "no wait", "I mean", ...):

- Restart: "I want it written in C um no in Rust" -> "I want it written in Rust". The word
  after the cue repeats an earlier word ("in ... in") within MAX_RETRACTED_WORDS; the words
  from that earlier word up to the cue are retracted, and removed together with the cue.
- Value substitution: the speaker replaces just a value. "ten megabytes, no, twenty
  megabytes" (a number before a unit that is repeated) and "use Postgres, no wait, SQLite"
  (one value-like word for another: a number, a word with a digit or code punctuation, or
  a capitalized word that does not start the sentence). The value and the cue are removed.

This does not depend on the cleanup model noticing the correction or on the ASR
punctuating the cue, and it follows the structural rules of the cleanup validator
(cleanup.self_correction_cut): no sentence ends inside the retracted words, a "?" or "!"
around the cue means an answer, not a correction, and a bare cue that starts a sentence
("...left. No, the only...") is an answer or a misheard "you know", never a cue. A value
substitution never crosses a sentence end at all, and a one-word swap needs the cue set off
by commas (both sides for a bare cue, which also needs numbers on both sides). A missed
correction costs a word for the cleanup model to fix; a false one drops a claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hark_client.cleanup import BARE_CUES, CORRECTION_CUES, FILLERS, MAX_RETRACTED_WORDS

_TOKENS = re.compile(r"[\w']+")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_CUES = sorted((*CORRECTION_CUES, *BARE_CUES), key=len, reverse=True)  # longest first
MAX_PASSES = 4
_NUMBER_WORDS = frozenset(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
     "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
     "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
     "hundred", "thousand", "million", "half")
)  # fmt: skip
_MAX_NUMBER_WORDS = 3  # "twenty five", "two point five" is not one (point is no number)
_CODE = re.compile(r"\w[_./@:-]\w")  # punctuation inside a word: snake_case, a.b, v1-2
_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")
# After a swapped name the clause goes on with one of these, or ends ("SQLite for the
# cache", "on Friday."); "Sarah, no wait, John wants it" starts a new clause instead.
_CLAUSE_GOES_ON = frozenset(
    ("for", "in", "on", "at", "to", "with", "from", "by", "as", "of", "into", "instead",
     "and", "or", "but", "so", "then", "because", "since", "please", "here", "there", "now")
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Correction:
    retracted: str  # removed text (retracted words + cue + fillers)
    anchor: str  # restart: the repeated word; value substitution: the new value


def _cue_at(words: list[str], i: int) -> int:
    for cue in _CUES:
        if tuple(words[i : i + len(cue)]) == cue:
            return len(cue)
    return 0


def _is_number(word: str) -> bool:
    """A number word or digits ("3", "0.5", "8,080"), lower case."""
    return word in _NUMBER_WORDS or _DIGITS.fullmatch(word) is not None


def _chunk(text: str, tok: re.Match[str]) -> str:
    """The whitespace-delimited chunk around a token, without edge punctuation."""
    start = text.rfind(" ", 0, tok.start()) + 1
    end = text.find(" ", tok.end())
    return text[start : end if end >= 0 else len(text)].strip("\"'()[]{},;:.!?")


def _value_like(text: str, toks: list[re.Match[str]], k: int) -> bool:
    """toks[k] is one value: a number, a word with a digit or code punctuation, or a
    capitalized word (not "I") that does not start a sentence."""
    chunk = _chunk(text, toks[k])
    if _is_number(chunk.lower()) or any(ch.isdigit() for ch in chunk) or _CODE.search(chunk):
        return True
    starts_sentence = k == 0 or bool(_SENTENCE_END.search(text, toks[k - 1].end(), toks[k].start()))
    return (
        chunk[:1].isupper() and not starts_sentence and chunk != "I" and not chunk.startswith("I'")
    )


def _set_off(gap: str) -> bool:
    return any(ch in gap for ch in ",;:\u2014-")


def _restart(
    text: str, toks: list[re.Match[str]], words: list[str], i: int, c: int
) -> tuple[str, Correction] | None:
    """The word at c repeats a word before the cue at i: retract from there to c."""
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


def _substitution(
    text: str, toks: list[re.Match[str]], i: int, n: int, c: int
) -> tuple[str, Correction] | None:
    """A value before the cue at i (n words) replaced by the one at c."""
    words = [t.group().lower() for t in toks]
    last = next((k for k in range(i - 1, -1, -1) if words[k] not in FILLERS), -1)
    if last < 0 or _SENTENCE_END.search(text, toks[last].end(), toks[c].start()):
        return None
    gaps = text[toks[last].end() : toks[i].start()], text[toks[i + n - 1].end() : toks[c].start()]
    first = last  # first retracted word
    # number + repeated unit: "ten megabytes, no, twenty megabytes"
    m = c
    while m < len(words) and m - c < _MAX_NUMBER_WORDS and _is_number(words[m]):
        m += 1
    unit = words[last]
    if c < m < len(words) and words[m] == unit and not _is_number(unit):
        first = last - 1
        while first > 0 and last - first < _MAX_NUMBER_WORDS and _is_number(words[first - 1]):
            first -= 1
        ok = first >= 0 and _is_number(words[first]) and words[first:last] != words[c:m]
    # one value for another of its kind: "use Postgres, no wait, SQLite"
    elif _value_like(text, toks, last) and _value_like(text, toks, c):
        old, new = _chunk(text, toks[last]), _chunk(text, toks[c])
        numbers = _is_number(old.lower()), _is_number(new.lower())
        value, _, rest = text[toks[c].start() :].partition(" ")
        nxt = rest.split(maxsplit=1)[:1]
        ends = not nxt or value[-1] in ",;:.!?" or nxt[0].lower() in _CLAUSE_GOES_ON
        ok = old.lower() != new.lower() and numbers[0] == numbers[1] and (numbers[0] or ends) and (
            _set_off(gaps[0]) or _set_off(gaps[1]) if n > 1
            else _set_off(gaps[0]) and _set_off(gaps[1]) and numbers[0]
        )  # fmt: skip
    else:
        return None
    if not ok:
        return None
    start, end = toks[first].start(), toks[c].start()
    start = text.rfind(" ", 0, start) + 1  # the whole chunk ("client/src", "$HOME")
    end = text.rfind(" ", 0, end) + 1
    rest = text[end:]
    if text[start : start + 1].isupper() and rest[:1].islower():
        rest = rest[:1].upper() + rest[1:]  # it started a sentence
    return text[:start] + rest, Correction(text[start:end].strip(), words[c])


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
        if n == 1 and _SENTENCE_END.search(text, toks[i - 1].end(), toks[i].start()):
            continue  # "...left. No, the only..." starts a sentence, it retracts nothing
        hit = _restart(text, toks, words, i, c) or _substitution(text, toks, i, n, c)
        if hit is not None:
            return hit
    return None


def resolve(text: str) -> tuple[str, list[Correction]]:
    """Apply every self-correction in `text` (left to right)."""
    found: list[Correction] = []
    for _ in range(MAX_PASSES):
        hit = _one(text)
        if hit is None:
            break
        text, corr = hit
        found.append(corr)
    return text, found
