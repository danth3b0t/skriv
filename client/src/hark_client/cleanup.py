"""Optional TextCleanup with protected spans, under a deadline.

The server already validates edits, but the client validator is authoritative
(ADR-001 #6): any edit that fails a check here discards the whole edit set and the
text stays as it was. Timeouts and RPC errors also fall back to the input text.
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
    from collections.abc import Callable, Sequence

    from hark_client.context import Term

log = logging.getLogger("hark.cleanup")

K = pb.EditKind
S = pb.ProtectedSpanKind

# Mirrors the server validator (hark_server.cleanup); keep the two in step.
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "mhm"}
DISCOURSE_FILLERS = {"like", "basically", "literally"}  # only the model knows where
# Not "I mean": "I mean it, ..." is not a filler (as a correction cue it is handled there).
FILLER_PHRASES = {("you", "know"), ("kind", "of"), ("sort", "of")}
CORRECTION_CUES = (
    ("no", "wait"), ("wait", "no"), ("no", "sorry"), ("sorry", "no"), ("i", "mean"),
    ("scratch", "that"), ("actually", "no"), ("no", "actually"), ("or", "rather"),
)  # fmt: skip
MAX_RETRACTED_WORDS = 8
MAX_REMOVED_SHARE = 0.5  # of the words, by filler removal
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
    status: str  # applied | no_edits | skipped | timeout | error | rejected
    reason: str = ""
    edits: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    ms: float = 0.0


def _words(s: str) -> list[str]:
    return _WORDS.findall(s.lower())


def self_correction_cut(find: str, replace: str) -> tuple[int, int] | None:
    """find = kept words + 1..MAX_RETRACTED_WORDS retracted words + a cue set off
    by punctuation + correction; replace = kept words + correction. Returns the
    character range of `find` that is dropped (retracted words + cue)."""
    tokens = [(m.group().lower(), m.start(), m.end()) for m in _WORDS.finditer(find)]
    before, after = [t[0] for t in tokens], _words(replace)
    for i in range(1, len(before)):
        for cue in CORRECTION_CUES:
            n = len(cue)
            tail = before[i + n :]
            j = len(after) - len(tail)  # kept words before the retracted ones
            if (
                tuple(before[i : i + n]) == cue
                and tail
                and 0 <= j < i
                and i - j <= MAX_RETRACTED_WORDS
                and after == before[:j] + tail
                and not (
                    find[tokens[i - 1][2] : tokens[i][1]].isspace()
                    and find[tokens[i + n - 1][2] : tokens[i + n][1]].isspace()
                )
            ):
                return tokens[j][1], tokens[i + n][1]
    return None


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


def _touches_protected(e: pb.Edit, spans: Sequence[Span], raw: bytes) -> bool:
    """Protected spans inside the edit must survive it (_span_kept). A
    self-correction may instead drop them with the words it retracts (the "no"
    of "no wait", "port 8080, sorry, I mean ..."); the others must reappear in
    `replace`."""
    cut = self_correction_cut(e.find, e.replace) if e.kind == K.EDIT_KIND_SELF_CORRECTION else None
    lo, hi = (
        (
            e.byte_start + len(e.find[: cut[0]].encode()),
            e.byte_start + len(e.find[: cut[1]].encode()),
        )
        if cut is not None
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


def validate_edits(text: str, edits: Sequence[pb.Edit], spans: Sequence[Span]) -> str:
    """Apply `edits` to `text` if every check passes; raise ValueError otherwise."""
    raw = text.encode()
    out, last = bytearray(), 0
    for e in sorted(edits, key=lambda e: e.byte_start):
        if not (last <= e.byte_start < e.byte_end <= len(raw)):
            raise ValueError("edit out of range or overlapping")
        try:
            quoted = raw[e.byte_start : e.byte_end].decode()
        except UnicodeDecodeError:
            raise ValueError("edit not on a character boundary") from None
        if quoted != e.find or _count(text, e.find) != 1:
            raise ValueError(f"edit {e.find!r} does not quote a unique substring")
        if _touches_protected(e, spans, raw):
            raise ValueError(f"edit {e.find!r} touches a protected span")
        if e.find == e.replace or not _invariant_ok(e.kind, e.find, e.replace):
            raise ValueError(
                f"edit {e.find!r} -> {e.replace!r} breaks its {K.Name(e.kind)} invariant"
            )
        out += raw[last : e.byte_start] + e.replace.encode()
        last = e.byte_end
    out += raw[last:]
    after = out.decode()
    # What was meant: the text with spoken corrections and number formatting applied.
    meant = _apply(text, [e for e in edits if e.kind in _MEANING])
    if len(_NEGATION.findall(meant)) != len(_NEGATION.findall(after)):
        raise ValueError("negation count changed")
    if sorted(c for c in meant if c.isdigit()) != sorted(c for c in after if c.isdigit()):
        raise ValueError("digits changed")
    words = len(_words(meant))
    if words - len(_words(after)) > max(2, MAX_REMOVED_SHARE * words):
        raise ValueError("too many words removed")
    if after.count("!") < meant.count("!"):
        raise ValueError("exclamation mark removed")
    return after


_MEANING = (K.EDIT_KIND_SELF_CORRECTION, K.EDIT_KIND_NUMBER_FORMAT)


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
        status: str, reason: str = "", result: str = text, edits: list[dict[str, str]] | None = None
    ) -> CleanupOutcome:
        return CleanupOutcome(
            result, status, reason, edits or [], (time.perf_counter() - t0) * 1000
        )

    if not text.strip():
        return done("skipped", "empty text")
    if (language and not language.lower().startswith("en")) or likely_non_english(text):
        return done("skipped", "non_english")
    spans = protected_spans(text, terms)
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
    )
    try:
        resp = pb_grpc.TextCleanupStub(channel).Cleanup(request, timeout=deadline_ms / 1000)
    except grpc.RpcError as e:
        if isinstance(e, grpc.Call) and e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            return done("timeout", f"no response within {deadline_ms} ms")
        return done("error", rpc_error_text(e))
    if resp.truncated:
        return done("timeout", "server budget exhausted")
    if not resp.edits:
        return done("no_edits")
    applied = [{"find": e.find, "replace": e.replace, "kind": K.Name(e.kind)} for e in resp.edits]
    try:
        cleaned = validate_edits(text, resp.edits, spans)
    except ValueError as e:
        log.warning("cleanup rejected by client validator: %s", e)
        return done("rejected", str(e), edits=applied)
    return done("applied", result=cleaned, edits=applied)
