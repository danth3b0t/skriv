"""Optional TextCleanup with protected spans, under a deadline.

The server already validates edits, but the client validator is authoritative
(ADR-001 #6): an edit that fails a check here is dropped together with its group
(edits at most one word apart), and a failed whole-text rule that cannot be pinned
on some edits keeps the text as it was. Timeouts and RPC errors also fall back to
the input text.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import cache
from typing import TYPE_CHECKING, cast

import grpc

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from hark_client.asr import rpc_error_text
from hark_client.context import TermKind

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from hark_client.context import Term

log = logging.getLogger("hark.cleanup")

K = pb.EditKind
R = pb.EditRejectionReason
S = pb.ProtectedSpanKind

# Mirrors the server validator (hark_server.cleanup); keep the two in step.
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "mhm"}
DISCOURSE_FILLERS = {"like", "basically", "literally"}  # only the model knows where
# Not "I mean": "I mean it, ..." is not a filler (as a correction cue it is handled there).
FILLER_PHRASES = {("you", "know"), ("kind", "of"), ("sort", "of")}
CORRECTION_CUES = (
    ("no", "wait"), ("wait", "no"), ("no", "sorry"), ("sorry", "no"), ("i", "mean"),
    ("scratch", "that"), ("actually", "no"), ("no", "actually"), ("or", "rather"),
    ("make", "that"),
)  # fmt: skip
BARE_CUES = (("no",), ("sorry",), ("actually",))  # stricter: see self_correction_cut
MAX_RETRACTED_WORDS = 6  # not counting fillers
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_TERM_EDGE = "\"'()[]{}.,;:!?\u2019-"  # may change around a merged term
MAX_REMOVED_SHARE = 0.5  # of the words, by filler removal
GROUP_GAP_WORDS = 1  # edits at most this many words apart are kept or dropped together
PUNCTUATION = set(".,;:?!'\u2019-")  # marks a punctuation edit may add or remove
NEGATIONS = {
    "not", "no", "never", "nothing", "none", "nobody", "nowhere", "neither", "nor",
    "cannot", "dont", "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't",
    "won't", "wouldn't", "can't", "couldn't", "shouldn't", "haven't", "hasn't", "hadn't",
    "mustn't",
}  # fmt: skip
_WORDS = re.compile(r"[\w']+")
_NEGATION = re.compile(r"(?i)(?<![\w'])(?:[a-z]+n't|" + "|".join(sorted(NEGATIONS)) + r")(?![\w'])")
_NUMBER = re.compile(r"\d+(?:[.,:/]\d+)*")
_TOKEN = re.compile(r"\S+")
_CODE_INNER = re.compile(r"[_/\\.:@#$=<>{}\[\]()`~|*]|[a-z][A-Z]|[A-Za-z]\d|\d[A-Za-z]")
_FLAG = re.compile(r"^-{1,2}[A-Za-z0-9]")
_TRAILING_PUNCT = ".,;:!?\"')]"
_IDENTIFIER_KINDS = {TermKind.IDENTIFIER, TermKind.PATH, TermKind.FLAG, TermKind.COMMAND}


@dataclass(frozen=True, slots=True)
class Span:
    start: int  # UTF-8 byte offsets, half-open
    end: int
    kind: pb.ProtectedSpanKind.ValueType


def _is_code_like(token: str) -> bool:
    core = token.rstrip(_TRAILING_PUNCT).lstrip("(\"'[")
    return bool(core) and (bool(_FLAG.match(core)) or bool(_CODE_INNER.search(core[1:])))


def protected_spans(text: str, terms: Sequence[Term]) -> list[Span]:
    """Identifiers (shortlist canonicals + code-like tokens), numbers and negations,
    as merged, sorted UTF-8 byte spans into `text`."""
    found: list[tuple[int, int, pb.ProtectedSpanKind.ValueType]] = []  # code points
    for t in terms:
        if len(t.canonical) < 2:
            continue
        kind = (
            S.PROTECTED_SPAN_KIND_IDENTIFIER
            if t.kind in _IDENTIFIER_KINDS
            else S.PROTECTED_SPAN_KIND_BIAS_TERM
        )
        pattern = re.compile(r"(?<!\w)" + re.escape(t.canonical) + r"(?!\w)")
        found += [(m.start(), m.end(), kind) for m in pattern.finditer(text)]
    for m in _TOKEN.finditer(text):
        if _is_code_like(m.group()):
            core = m.group().rstrip(_TRAILING_PUNCT)
            found.append((m.start(), m.start() + len(core), S.PROTECTED_SPAN_KIND_IDENTIFIER))
    found += [(m.start(), m.end(), S.PROTECTED_SPAN_KIND_NUMBER) for m in _NUMBER.finditer(text)]
    found += [
        (m.start(), m.end(), S.PROTECTED_SPAN_KIND_NEGATION) for m in _NEGATION.finditer(text)
    ]
    merged: list[tuple[int, int, pb.ProtectedSpanKind.ValueType]] = []
    for start, end, kind in sorted(found):
        if merged and start <= merged[-1][1]:
            prev = merged[-1]
            merged[-1] = (prev[0], max(prev[1], end), prev[2])
        else:
            merged.append((start, end, kind))

    def b(i: int) -> int:
        return len(text[:i].encode())

    return [Span(b(s), b(e), k) for s, e, k in merged]


@dataclass
class CleanupOutcome:
    text: str
    status: str  # applied | partial | rejected | no_edits | skipped | timeout | error
    reason: str = ""
    edits: list[dict[str, str]] = field(default_factory=list[dict[str, str]])  # applied
    ms: float = 0.0
    # Edits the server or the client validator dropped: find, replace, reason, by.
    dropped: list[dict[str, str]] = field(default_factory=list[dict[str, str]])


def _words(s: str) -> list[str]:
    return _WORDS.findall(s.lower())


def _set_off(gap: str) -> bool:
    """The text between two words holds punctuation, not just spaces and fillers."""
    return bool(_WORDS.sub("", gap).strip())


def self_correction_cut(find: str, replace: str) -> tuple[int, int] | None:
    """find = kept words + retracted words + cue (fillers around it allowed) +
    correction; replace = kept words + correction. Returns the character range
    of `find` that is dropped (retracted words + cue + fillers). Same rules as
    the server: 1..MAX_RETRACTED_WORDS retracted words (fillers not counted), a
    correction right after the cue; a phrase cue set off by punctuation on one
    side, a bare cue ("no", "sorry", "actually") on both and followed by a
    correction that repeats the first retracted word or replaces a single word
    after a kept one; no sentence end inside the retracted words, no "?"/"!"
    next to the cue, a "." next to it only when the correction repeats the
    first retracted word."""
    tokens = [(m.group().lower(), m.start(), m.end()) for m in _WORDS.finditer(find)]
    before, after = [t[0] for t in tokens], _words(replace)
    for i in range(1, len(before)):
        for cue in (*CORRECTION_CUES, *BARE_CUES):
            n = len(cue)
            if tuple(before[i : i + n]) != cue:
                continue
            c = i + n  # the correction's first word
            while c < len(before) and before[c] in FILLERS:
                c += 1
            tail = before[c:]
            j = len(after) - len(tail)  # kept words before the retracted ones
            retracted = [k for k in range(max(j, 0), i) if before[k] not in FILLERS]
            if not (
                tail
                and 0 <= j < i
                and after == before[:j] + tail
                and 1 <= len(retracted) <= MAX_RETRACTED_WORDS
            ):
                continue
            first, last = tokens[retracted[0]], tokens[retracted[-1]]
            gaps = find[last[2] : tokens[i][1]], find[tokens[i + n - 1][2] : tokens[c][1]]
            restart = before[c] == first[0]
            if (
                _SENTENCE_END.search(find, first[1], last[2])
                or any("?" in g or "!" in g for g in gaps)
                or (any("." in g for g in gaps) and not restart)
            ):
                continue
            if n > 1:
                ok = _set_off(gaps[0]) or _set_off(gaps[1])
            else:
                slot = (
                    len(retracted) == 1
                    and j > 0
                    and not _SENTENCE_END.search(find, tokens[j - 1][2], first[1])
                )
                ok = _set_off(gaps[0]) and _set_off(gaps[1]) and (restart or slot)
            if ok:
                return tokens[j][1], tokens[c][1]
    return None


def respaced_term(find: str, replace: str, terms: Collection[str]) -> tuple[int, int] | None:
    """`find` -> `replace` only merges or splits words and changes their case,
    into a known term ("Super Whisper." -> "Superwhisper."); edge punctuation
    may change like a punctuation edit. Returns the character range of the
    changed words in `find`, or None. Mirrors the server."""
    a, b = list(_TOKEN.finditer(find)), list(_TOKEN.finditer(replace))
    p = 0
    while p < min(len(a), len(b)) and a[p].group() == b[p].group():
        p += 1
    s = 0
    while s < min(len(a), len(b)) - p and a[-1 - s].group() == b[-1 - s].group():
        s += 1
    old, new = a[p : len(a) - s], b[p : len(b) - s]
    if not old or not new or len(old) == len(new):
        return None  # not a merge or split
    f, r = find[old[0].start() : old[-1].end()], replace[new[0].start() : new[-1].end()]
    f_core, r_core = f.strip(_TERM_EDGE), r.strip(_TERM_EDGE)
    f_lead, r_lead = len(f) - len(f.lstrip(_TERM_EDGE)), len(r) - len(r.lstrip(_TERM_EDGE))
    f_trail, r_trail = f[f_lead + len(f_core) :], r[r_lead + len(r_core) :]
    if (
        not f_core
        or r_core not in terms
        or "".join(f_core.split()).casefold() != "".join(r_core.split()).casefold()
        or not _punctuation_ok(f[:f_lead] + "x" + f_trail, r[:r_lead] + "x" + r_trail)
    ):
        return None
    return old[0].start() + f_lead, old[0].start() + f_lead + len(f_core)


def _only_fillers_removed(before: list[str], after: list[str]) -> bool:
    """`after` is `before` minus fillers, filler phrases and repeated words."""

    @cache
    def ok(i: int, j: int) -> bool:
        if i == len(before):
            return j == len(after)
        w = before[i]
        return (
            (j < len(after) and w == after[j] and ok(i + 1, j + 1))
            or ((w in FILLERS or w in DISCOURSE_FILLERS) and ok(i + 1, j))
            or (tuple(before[i : i + 2]) in FILLER_PHRASES and ok(i + 2, j))
            or (i + 1 < len(before) and before[i + 1] == w and ok(i + 1, j))
        )

    return len(after) < len(before) and ok(0, 0)


_UNITS = {w: i for i, w in enumerate((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
))}  # fmt: skip
_TENS = {w: 10 * i for i, w in enumerate(
    ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"), 2
)}  # fmt: skip
_SCALES = {"thousand": 1000, "million": 10**6, "billion": 10**9}
_ORDINALS = {
    **{w: i for i, w in enumerate((
        "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
        "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
        "seventeenth", "eighteenth", "nineteenth",
    ), 1)},
    **{w.removesuffix("y") + "ieth": v for w, v in _TENS.items()},
}  # fmt: skip
_NUMERAL = re.compile(r"\$?\d[\d,]*(?:st|nd|rd|th|%)?|[^\W\d_][\w']*")


def _spoken_numbers(words: list[str]) -> list[str]:
    """Runs of number words -> digits ("twenty three percent" -> "23%")."""
    out: list[str] = []
    total = current = 0
    last = ""  # unit | teen | ten | hundred | scale | ""

    def flush() -> None:
        nonlocal total, current, last
        if last:
            out.append(str(total + current))
        total = current = 0
        last = ""

    for w in words:
        if w in _UNITS:
            v = _UNITS[w]
            if last in ("unit", "teen") or (last == "ten" and v >= 10) or (last and v == 0):
                flush()
            current += v
            last = "teen" if v >= 10 else "unit"
        elif w in _TENS:
            if last in ("unit", "teen", "ten"):
                flush()
            current += _TENS[w]
            last = "ten"
        elif w == "hundred" and last in ("unit", "teen", "ten") and current < 100:
            current *= 100
            last = "hundred"
        elif w in _SCALES and last in ("unit", "teen", "ten", "hundred"):
            total += current * _SCALES[w]
            current = 0
            last = "scale"
        elif w == "and" and last == "hundred":
            continue
        elif w in _ORDINALS and last in ("", "ten", "hundred", "scale"):
            n = total + current + _ORDINALS[w]
            total = current = 0
            last = ""
            suffix = (
                "th" if n % 100 in (11, 12, 13) else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            )
            out.append(f"{n}{suffix}")
        elif w == "percent" and last:
            flush()
            out[-1] += "%"
        elif w in ("dollar", "dollars") and last:
            flush()
            out[-1] = "$" + out[-1]
        else:
            flush()
            out.append(w)
    flush()
    return out


def _number_format_ok(find: str, replace: str) -> bool:
    spoken = _words(find)
    written = [t.replace(",", "") for t in cast("list[str]", _NUMERAL.findall(replace.lower()))]
    converted = _spoken_numbers(spoken)
    return converted != spoken and converted == written


def _punctuation_ok(find: str, replace: str) -> bool:
    if [c for c in find if c.isalnum()] != [c for c in replace if c.isalnum()]:
        return False
    ops = SequenceMatcher(None, find, replace, autojunk=False).get_opcodes()
    changed = "".join(find[i1:i2] + replace[j1:j2] for tag, i1, i2, j1, j2 in ops if tag != "equal")
    return all(c in PUNCTUATION or c.isspace() for c in changed)


_INVARIANTS: dict[pb.EditKind.ValueType, Callable[[str, str], bool]] = {
    K.EDIT_KIND_PUNCTUATION: _punctuation_ok,
    K.EDIT_KIND_CAPITALIZATION: lambda f, r: f.casefold() == r.casefold(),
    K.EDIT_KIND_SPACING: lambda f, r: "".join(f.split()) == "".join(r.split()),
    K.EDIT_KIND_FILLER_REMOVAL: lambda f, r: _only_fillers_removed(_words(f), _words(r)),
    K.EDIT_KIND_SELF_CORRECTION: lambda f, r: self_correction_cut(f, r) is not None,
    K.EDIT_KIND_NUMBER_FORMAT: _number_format_ok,
}


def _invariant_ok(kind: pb.EditKind.ValueType, find: str, replace: str) -> bool:
    check = _INVARIANTS.get(kind)
    return check is not None and check(find, replace)  # no check => not accepted


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c == "_"


def _span_kept(find: str, replace: str, s: int, e: int, prefix: str) -> bool:
    """find[s:e] (a protected span) reappears unchanged in `replace` and gains no
    letters or digits on either side ("main.py" -> "main.py." is fine, "80 80" ->
    "8080" is not). A negation may change case where it starts a sentence of the
    result ("never" -> "Never"). `prefix`: the text before the edit."""
    fold = _NEGATION.fullmatch(find[s:e]) is not None  # may change case at a sentence start
    a, b = (find.lower(), replace.lower()) if fold else (find, replace)
    new_s = new_e = -1
    for tag, i1, i2, j1, _ in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            if i1 <= s < i2:
                new_s = j1 + s - i1
            if i1 < e <= i2:
                new_e = j1 + e - i1
        elif (i1 < e and s < i2) or (i1 == i2 and s < i1 < e):
            return False
    if new_s < 0 or new_e - new_s != e - s:
        return False
    lead = (prefix + replace[:new_s]).rstrip()  # the result before the span
    if replace[new_s:new_e] != find[s:e] and lead and not lead.endswith((".", "!", "?")):
        return False
    before = (
        new_s == 0
        or not _is_word_char(replace[new_s - 1])
        or (s > 0 and replace[new_s - 1] == find[s - 1])
    )
    after = (
        new_e == len(replace)
        or not _is_word_char(replace[new_e])
        or (e < len(find) and replace[new_e] == find[e])
    )
    return before and after


def _touches_protected(
    e: pb.Edit, spans: Sequence[Span], raw: bytes, terms: Collection[str]
) -> bool:
    """Protected spans inside the edit must survive it (_span_kept). A
    self-correction may instead drop them with the words it retracts (the "no"
    of "in C, no, in Rust", "port 8080, sorry, I mean ..."); the others must
    reappear in `replace`. Spans inside words merged into a known term may
    change too ("Super Whisper" -> "Superwhisper")."""
    cut = self_correction_cut(e.find, e.replace) if e.kind == K.EDIT_KIND_SELF_CORRECTION else None
    free = cut or respaced_term(e.find, e.replace, terms)
    lo, hi = (
        (
            e.byte_start + len(e.find[: free[0]].encode()),
            e.byte_start + len(e.find[: free[1]].encode()),
        )
        if free
        else (-1, -1)
    )
    for sp in spans:
        if not (e.byte_start < sp.end and sp.start < e.byte_end) or (
            lo <= sp.start and sp.end <= hi
        ):
            continue
        if sp.start < e.byte_start or sp.end > e.byte_end:  # the edit cuts through the span
            return True
        if cut is not None:
            if raw[sp.start : sp.end].decode() not in e.replace:
                return True
            continue
        s = len(raw[e.byte_start : sp.start].decode())
        prefix = raw[: e.byte_start].decode()
        if not _span_kept(e.find, e.replace, s, s + len(raw[sp.start : sp.end].decode()), prefix):
            return True
    return False


def _count(text: str, find: str) -> int:
    n, i = 0, text.find(find)
    while i >= 0:
        n += 1
        i = text.find(find, i + 1)
    return n


def _edit_error(
    text: str, raw: bytes, e: pb.Edit, spans: Sequence[Span], terms: Collection[str]
) -> str:
    """Why a single in-range edit fails, or ""."""
    try:
        quoted = raw[e.byte_start : e.byte_end].decode()
    except UnicodeDecodeError:
        return "not on a character boundary"
    if quoted != e.find or _count(text, e.find) != 1:
        return "does not quote a unique substring"
    if _touches_protected(e, spans, raw, terms):
        return "touches a protected span"
    respaced = e.kind == K.EDIT_KIND_SPACING and respaced_term(e.find, e.replace, terms)
    if e.find == e.replace or not (_invariant_ok(e.kind, e.find, e.replace) or respaced):
        return f"breaks its {K.Name(e.kind)} invariant"
    return ""


def _edit_groups(text: str, ranges: Sequence[tuple[int, int]]) -> list[int]:
    """A group id per byte range (in any order). Ranges that overlap or have at
    most GROUP_GAP_WORDS words between them are in one group, transitively."""
    raw = text.encode()
    ids = [0] * len(ranges)
    group, end = -1, -1
    for i in sorted(range(len(ranges)), key=lambda i: ranges[i]):
        start = ranges[i][0]
        between = raw[end:start].decode(errors="ignore") if start > end else ""
        if group < 0 or len(_words(between)) > GROUP_GAP_WORDS:
            group += 1
        ids[i] = group
        end = max(end, ranges[i][1])
    return ids


def _global_error(text: str, edits: Sequence[pb.Edit]) -> tuple[str, list[int]]:
    """("", []) if the result of `edits` passes the whole-text rules, else the
    failed rule and the indices of the edits it is pinned on ([]: not pinned).
    Negations, digits and "!" must survive every edit except what a spoken
    correction retracts (and digits a number_format edit writes); fillers may
    make up at most MAX_REMOVED_SHARE of the words."""
    # What was meant: the text with spoken corrections and number formatting applied.
    meant = _apply(text, [e for e in edits if e.kind in _MEANING])
    after = _apply(text, edits)
    pinned = (
        ("negation count changed", _negations),
        ("digits changed", _digits),
        ("exclamation mark removed", _exclamations),
    )
    for rule, measure in pinned:
        if measure(meant) != measure(after):
            return rule, [
                i
                for i, e in enumerate(edits)
                if e.kind not in _MEANING and measure(e.find) != measure(e.replace)
            ]
    words = len(_words(meant))
    if words - len(_words(after)) > max(2, MAX_REMOVED_SHARE * words):
        return "too many words removed", []
    return "", []


def _negations(s: str) -> int:
    return len(_NEGATION.findall(s))


def _digits(s: str) -> list[str]:
    return sorted(c for c in s if c.isdigit())


def _exclamations(s: str) -> int:
    return s.count("!")


@dataclass(frozen=True, slots=True)
class Validated:
    text: str  # `text` with the accepted edits applied
    accepted: list[pb.Edit]
    dropped: list[tuple[pb.Edit, str]]  # (edit, reason)


def validate_edits(
    text: str, edits: Sequence[pb.Edit], spans: Sequence[Span], terms: Collection[str] = ()
) -> Validated:
    """Apply the edits that pass every check, group by group.

    `terms`: known terms (vocabulary canonicals) a spacing edit may merge or
    split words into; the text of protected spans other than numbers and
    negations counts too.

    An edit that fails a per-edit check (quote, protected span, kind invariant)
    takes the edits of its group with it (_edit_groups): "step, uh," ->
    "step." dropped must not leave "can" -> "Can". An edit out of range or
    overlapping an accepted one is dropped alone. The whole-text rules then run
    on the result: a failure pinned on some edits drops their groups and the
    rules run again; any other failure drops every edit."""
    raw = text.encode()
    known = {
        *terms,
        *(raw[s.start : s.end].decode() for s in spans if s.kind not in _NOT_TERMS),
    }
    accepted: list[pb.Edit] = []
    dropped: list[tuple[pb.Edit, str]] = []
    failed: list[pb.Edit] = []  # dropped with their group
    last = 0
    for e in sorted(edits, key=lambda e: e.byte_start):
        if not (e.byte_start < e.byte_end <= len(raw)):
            dropped.append((e, "out of range"))
        elif e.byte_start < last:
            dropped.append((e, "overlaps an earlier edit"))
        elif error := _edit_error(text, raw, e, spans, known):
            dropped.append((e, error))
            failed.append(e)
        else:
            accepted.append(e)
            last = e.byte_end
    ids = _edit_groups(text, [(e.byte_start, e.byte_end) for e in (*accepted, *failed)])
    tainted = set(ids[len(accepted) :])
    grouped = list(zip(accepted, ids[: len(accepted)], strict=True))
    dropped += [(e, "dropped with its group") for e, g in grouped if g in tainted]
    grouped = [(e, g) for e, g in grouped if g not in tainted]
    while grouped:
        rule, blame = _global_error(text, [e for e, _ in grouped])
        if not rule:
            break
        bad = {grouped[i][1] for i in blame} if blame else {g for _, g in grouped}
        dropped += [(e, rule) for e, g in grouped if g in bad]
        grouped = [(e, g) for e, g in grouped if g not in bad]
    kept = [e for e, _ in grouped]
    return Validated(_apply(text, kept), kept, dropped)


_MEANING = (K.EDIT_KIND_SELF_CORRECTION, K.EDIT_KIND_NUMBER_FORMAT)
_NOT_TERMS = (S.PROTECTED_SPAN_KIND_NUMBER, S.PROTECTED_SPAN_KIND_NEGATION)


def _apply(text: str, edits: Sequence[pb.Edit]) -> str:
    raw = text.encode()
    out, last = bytearray(), 0
    for e in sorted(edits, key=lambda e: e.byte_start):
        out += raw[last : e.byte_start] + e.replace.encode()
        last = e.byte_end
    return (out + raw[last:]).decode()


# S1-mini is English-only and the edit-list model re-cased Czech: skip
# cleanup for other languages. Stopwords that are not also English words.
_EN_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been", "to", "of",
    "in", "on", "at", "for", "with", "it", "this", "that", "i", "you", "we", "they", "he", "she",
    "not", "do", "does", "did", "can", "will", "would", "what", "how", "why", "my", "your", "me",
    "so", "if", "have", "has", "just", "there", "from", "by", "as",
}  # fmt: skip
_FOREIGN_STOPWORDS = {
    # cs/sk
    "je", "jsem", "jsi", "jsme", "jste", "jsou", "tohle", "toto", "tento", "tato", "tady", "že",
    "se", "si", "na", "ve", "v", "z", "ze", "k", "ke", "s", "ale", "nebo", "jak", "kde", "co",
    "proč", "když", "už", "taky", "také", "není", "bude", "byl", "bylo", "mám", "máme", "ještě",
    "jen", "tak", "aby", "který", "která", "které", "protože", "prosím", "děkuji", "ano", "ne",
    "mi", "mě", "tam", "teď", "som", "sa", "nie", "áno", "aj", "ako",
    # de
    "der", "das", "und", "ist", "nicht", "ich", "du", "wir", "ein", "eine", "mit", "auf", "für",
    "zu", "dem", "sie", "von", "nein", "dass", "sind", "auch", "oder", "aber",
    # fr / es / it / pl / pt
    "le", "la", "les", "et", "est", "une", "des", "tu", "nous", "vous", "pas", "que", "qui",
    "avec", "dans", "ce", "sur", "el", "los", "las", "y", "es", "por", "para", "con", "una", "lo",
    "pero", "como", "il", "che", "di", "sono", "è", "się", "não", "você",
}  # fmt: skip


def likely_non_english(text: str) -> bool:
    """At least two foreign stopwords and more than English ones, or over a
    quarter of the words have non-ASCII letters (diacritics, other scripts)."""
    words = _words(text)
    if not words:
        return False
    english = sum(w in _EN_STOPWORDS for w in words)
    foreign = sum(w in _FOREIGN_STOPWORDS for w in words)
    accented = sum(any(c.isalpha() and not c.isascii() for c in w) for w in words)
    return foreign > max(1, english) or accented > 0.25 * len(words)


def run(  # noqa: PLR0913, PLR0911 - keyword-only args; linear fallback chain
    channel: grpc.Channel,
    *,
    text: str,
    terms: Sequence[Term],
    utterance_id: str,
    profile_id: str,
    app_category: str,
    language: str,
    deadline_ms: int,
) -> CleanupOutcome:
    t0 = time.perf_counter()

    def done(
        status: str,
        reason: str = "",
        result: str = text,
        edits: list[dict[str, str]] | None = None,
        dropped: list[dict[str, str]] | None = None,
    ) -> CleanupOutcome:
        return CleanupOutcome(
            result, status, reason, edits or [], (time.perf_counter() - t0) * 1000, dropped or []
        )

    if not text.strip():
        return done("skipped", "empty text")
    if (language and not language.lower().startswith("en")) or likely_non_english(text):
        return done("skipped", "non_english")
    spans = protected_spans(text, terms)
    known_terms = [t.canonical for t in terms]
    request = pb.CleanupRequest(
        request_id=utterance_id + "/cleanup",
        utterance_id=utterance_id,
        text=text,
        protected_spans=[
            pb.ProtectedSpan(byte_start=s.start, byte_end=s.end, kind=s.kind) for s in spans
        ],
        profile_id=profile_id,
        language=language,
        context=pb.CleanupContext(app_category=app_category),
        budget_ms=max(1, deadline_ms * 4 // 5),  # leave room for the round trip
        known_terms=known_terms,
    )
    try:
        resp = pb_grpc.TextCleanupStub(channel).Cleanup(request, timeout=deadline_ms / 1000)
    except grpc.RpcError as e:
        if isinstance(e, grpc.Call) and e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            return done("timeout", f"no response within {deadline_ms} ms")
        return done("error", rpc_error_text(e))
    if resp.truncated:
        return done("timeout", "server budget exhausted")
    dropped = [
        {"find": r.find, "replace": r.replace, "by": "server",
         "reason": R.Name(r.reason).removeprefix("EDIT_REJECTION_REASON_").lower()}
        for r in resp.rejections
        if r.reason != R.EDIT_REJECTION_REASON_NO_OP  # echoed text: nothing was lost
    ]  # fmt: skip
    if not resp.edits and not dropped:
        return done("no_edits")
    checked = validate_edits(text, resp.edits, spans, known_terms)
    dropped += [
        {"find": e.find, "replace": e.replace, "by": "client", "reason": why}
        for e, why in checked.dropped
    ]
    applied = [
        {"find": e.find, "replace": e.replace, "kind": K.Name(e.kind)} for e in checked.accepted
    ]
    total = len(applied) + len(dropped)
    if checked.dropped:
        log.warning("cleanup: client validator dropped %d edit(s)", len(checked.dropped))
    if not dropped:
        return done("applied", result=checked.text, edits=applied)
    reasons = ", ".join(sorted({d["reason"] for d in dropped}))
    summary = f"{len(applied)} of {total} edits applied; dropped: {reasons}"
    if not applied:
        return done("rejected", summary, dropped=dropped)
    return done("partial", summary, result=checked.text, edits=applied, dropped=dropped)
