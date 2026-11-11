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
from typing import TYPE_CHECKING

import grpc

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from hark_client.asr import rpc_error_text
from hark_client.context import TermKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hark_client.context import Term

log = logging.getLogger("hark.cleanup")

K = pb.EditKind
S = pb.ProtectedSpanKind

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "mhm"}
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


def _invariant_ok(kind: pb.EditKind.ValueType, find: str, replace: str) -> bool:
    if kind == K.EDIT_KIND_PUNCTUATION:
        return [c for c in find if c.isalnum()] == [c for c in replace if c.isalnum()]
    if kind == K.EDIT_KIND_CAPITALIZATION:
        return find.casefold() == replace.casefold()
    if kind == K.EDIT_KIND_SPACING:
        return "".join(find.split()) == "".join(replace.split())
    if kind == K.EDIT_KIND_FILLER_REMOVAL:
        before, after = _words(find), _words(replace)
        return len(after) < len(before) and [w for w in before if w not in FILLERS] == after
    return False  # no mechanical check => not accepted by the client


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
        if any(e.byte_start < s.end and s.start < e.byte_end for s in spans):
            raise ValueError(f"edit {e.find!r} touches a protected span")
        if e.find == e.replace or not _invariant_ok(e.kind, e.find, e.replace):
            raise ValueError(
                f"edit {e.find!r} -> {e.replace!r} breaks its {K.Name(e.kind)} invariant"
            )
        out += raw[last : e.byte_start] + e.replace.encode()
        last = e.byte_end
    out += raw[last:]
    after = out.decode()
    negations = len(_NEGATION.findall(text)), len(_NEGATION.findall(after))
    if negations[0] != negations[1]:
        raise ValueError("negation count changed")
    if sorted(c for c in text if c.isdigit()) != sorted(c for c in after if c.isdigit()):
        raise ValueError("digits changed")
    return after


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
