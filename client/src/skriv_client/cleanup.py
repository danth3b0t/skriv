"""Optional TextCleanup under a deadline: the server's rewrite, kept only if it is faithful.

The server returns a rewrite of the whole text (S1-mini, or an instruct model that also gets the
shortlist; `prime` sends it the shortlist at key-down). Before it replaces the text, the
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

from skriv.v1 import skriv_pb2 as pb
from skriv.v1 import skriv_pb2_grpc as pb_grpc
from skriv_client import fidelity
from skriv_client.asr import bias_list, rpc_error_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from skriv_client.context import Term

# A cold prompt (the first dictation after the model loaded) takes ~1.5 s on a 4B model; giving
# up early would waste the work, and nobody waits for it.
PRIME_BUDGET_MS = 5000
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
    "Skriv-server should" -> "skriv-server should"): same count ignoring case, fewer exact."""
    for p in keep:
        if fidelity.count(p, rewrite) < fidelity.count(p, text) and fidelity.count(
            p, rewrite, re.IGNORECASE
        ) == fidelity.count(p, text, re.IGNORECASE):
            pattern = re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.IGNORECASE)
            rewrite = pattern.sub(p.replace("\\", "\\\\"), rewrite)
    return rewrite


def judge(text: str, rewrite: str, terms: Sequence[Term], *, single_line: bool) -> tuple[str, str]:
    """`fidelity.accept` with what the shortlist allows: what to keep of `rewrite`, and why
    the rest was refused ("" if nothing was)."""
    keep = protected(text, terms)
    # What a merge or a repair may produce: "Super Whisper" -> "Superwhisper".
    keep |= {t.canonical for t in terms if fidelity.count(t.canonical, rewrite)}
    spoken = {t.canonical: t.spoken for t in terms if len(t.canonical) >= 2}
    return fidelity.accept(text, rewrite, keep, single_line=single_line, terms=spoken)


@dataclass
class CleanupOutcome:
    text: str  # what goes on: the rewrite if it was accepted, else the input
    status: str  # applied | no_edits | rejected | skipped | timeout | error
    reason: str = ""  # why it was rejected, skipped or failed, or which sentences stayed
    ms: float = 0.0
    rewrite: str = ""  # the server's rewrite (also when rejected)


def prime(  # noqa: PLR0913 - keyword-only args
    channel: grpc.Channel,
    *,
    terms: Sequence[Term],
    send_terms: bool,
    utterance_id: str,
    profile_id: str,
    app_category: str,
) -> str:
    """At key-down: the server sends its model the prompt this utterance's `run` will start
    with, so only the transcript is new at key-up. "" when done, else why not."""
    request = pb.CleanupRequest(
        request_id=utterance_id + "/prime",
        utterance_id=utterance_id,
        profile_id=profile_id,
        context=pb.CleanupContext(app_category=app_category),
        budget_ms=PRIME_BUDGET_MS,
        terms=bias_list(terms).terms if send_terms else (),
        prime=True,
    )
    try:
        resp = pb_grpc.TextCleanupStub(channel).Cleanup(request, timeout=PRIME_BUDGET_MS / 1000 + 1)
    except grpc.RpcError as e:
        return rpc_error_text(e)
    return "server budget exhausted" if resp.truncated else ""


def run(  # noqa: PLR0913, PLR0911 - keyword-only args; linear fallback chain
    channel: grpc.Channel,
    *,
    text: str,
    terms: Sequence[Term],
    send_terms: bool,
    utterance_id: str,
    profile_id: str,
    app_category: str,
    language: str,
    deadline_ms: int,
) -> CleanupOutcome:
    """The shortlist (`terms`) decides what the rewrite must keep and which new spellings it
    may write; it goes to the server too if `send_terms` (the provider gets context)."""
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
        terms=bias_list(terms).terms if send_terms else (),
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
    rewrite = restore_case(text, resp.text, protected(text, terms))
    result, why = judge(text, rewrite, terms, single_line=profile_id in SINGLE_LINE_PROFILES)
    if why and result == text:
        return done("rejected", why, rewrite=rewrite)
    return done("applied", why, result=result, rewrite=rewrite)
