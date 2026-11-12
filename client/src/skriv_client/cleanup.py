"""Optional TextCleanup under a deadline: the server's rewrite, kept only if it is faithful.

The server returns a rewrite of the whole text (S1-mini). Before it replaces the text, the
rewrite must pass `fidelity.check` against the text: protected identifiers, negations, numbers
and content words survive. If it does not, the sentences that pass are kept and the others stay
as said (`fidelity.accept`; status applied, with the reason). If none passes, and on timeouts
and RPC errors, the text stays as it was and the reason is recorded.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import grpc

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from skriv_client import fidelity
from skriv_client.asr import rpc_error_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from skriv_client.context import Term

# Used by corrections.py, which resolves restarts before cleanup.
FILLERS = fidelity.FILLERS
CORRECTION_CUES = fidelity.CORRECTION_CUES
BARE_CUES = fidelity.BARE_CUES
MAX_RETRACTED_WORDS = fidelity.MAX_RETRACTED_WORDS

SINGLE_LINE_PROFILES = frozenset({"terminal", "chat"})
_TOKEN = re.compile(r"\S+")
_CODE_INNER = re.compile(r"[_/\\.:@#$=<>{}\[\]()`~|*]|[a-z][A-Z]|[A-Za-z]\d|\d[A-Za-z]")
_FLAG = re.compile(r"^-{1,2}[A-Za-z0-9]")
_TRAILING_PUNCT = ".,;:!?\"')]"


def _is_code_like(token: str) -> bool:
    core = token.rstrip(_TRAILING_PUNCT).lstrip("(\"'[")
    return bool(core) and (bool(_FLAG.match(core)) or bool(_CODE_INNER.search(core[1:])))


def protected(text: str, terms: Sequence[Term]) -> set[str]:
    """Strings of `text` the rewrite must keep exactly: shortlist canonicals and code-like
    tokens (paths, flags, snake_case, camelCase, dotted names, versions)."""
    found = {
        t.canonical
        for t in terms
        if len(t.canonical) >= 2
        and re.search(r"(?<!\w)" + re.escape(t.canonical) + r"(?!\w)", text)
    }
    for m in _TOKEN.finditer(text):
        if _is_code_like(m.group()):
            found.add(m.group().rstrip(_TRAILING_PUNCT).lstrip("(\"'["))
    return found


def restore_case(text: str, rewrite: str, keep: set[str]) -> str:
    """Put back protected strings the rewrite only re-cased ("skriv-server should" ->
    "Hark-server should" -> "skriv-server should"): same count ignoring case, fewer exact."""
    for p in keep:
        if fidelity.count(p, rewrite) < fidelity.count(p, text) and fidelity.count(
            p, rewrite, re.IGNORECASE
        ) == fidelity.count(p, text, re.IGNORECASE):
            pattern = re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.IGNORECASE)
            rewrite = pattern.sub(p.replace("\\", "\\\\"), rewrite)
    return rewrite


@dataclass
class CleanupOutcome:
    text: str  # what goes on: the rewrite if it was accepted, else the input
    status: str  # applied | no_edits | rejected | skipped | timeout | error
    reason: str = ""  # why it was rejected, skipped or failed, or which sentences stayed
    ms: float = 0.0
    rewrite: str = ""  # the server's rewrite (also when rejected)


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
    """The shortlist (`terms`) stays here: it only decides what the rewrite must keep."""
    t0 = time.perf_counter()

    def done(
        status: str, reason: str = "", result: str = text, rewrite: str = ""
    ) -> CleanupOutcome:
        return CleanupOutcome(result, status, reason, (time.perf_counter() - t0) * 1000, rewrite)

    if not text.strip():
        return done("skipped", "empty text")
    if language and not language.lower().startswith("en"):
        return done("skipped", "non_english")
    request = pb.CleanupRequest(
        request_id=utterance_id + "/cleanup",
        utterance_id=utterance_id,
        text=text,
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
    if not resp.HasField("text"):
        return done("skipped", resp.skipped or "no rewrite")
    if resp.text == text:
        return done("no_edits")
    keep = protected(text, terms)
    rewrite = restore_case(text, resp.text, keep)
    single_line = profile_id in SINGLE_LINE_PROFILES
    # What a merge may produce: "Super Whisper" -> "Superwhisper".
    keep |= {t.canonical for t in terms if fidelity.count(t.canonical, rewrite)}
    result, why = fidelity.accept(text, rewrite, keep, single_line=single_line)
    if why and result == text:
        return done("rejected", why, rewrite=rewrite)
    return done("applied", why, result=result, rewrite=rewrite)
