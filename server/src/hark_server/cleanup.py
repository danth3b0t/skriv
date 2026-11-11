"""hark.v1.TextCleanup: proxy to an OpenAI-compatible /v1/chat/completions with a
JSON-schema constrained edit list (ADR-001 #6).

Edits are quoted-text anchored: `find` must occur exactly once in the text.
The server resolves edits to UTF-8 byte ranges and runs the same validator as
the client (proposal-a §8.2). The client's validator stays authoritative.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, TypedDict, TypeGuard, cast

import grpc
import httpx

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc

from .errors import E, HarkError, check_privacy, protocol_violation

if TYPE_CHECKING:
    from .config import CleanupConfig

log = logging.getLogger("hark_server.cleanup")

K = pb.EditKind
R = pb.EditRejectionReason
KINDS = {
    "punctuation": K.EDIT_KIND_PUNCTUATION,
    "capitalization": K.EDIT_KIND_CAPITALIZATION,
    "spacing": K.EDIT_KIND_SPACING,
    "filler_removal": K.EDIT_KIND_FILLER_REMOVAL,
    "number_format": K.EDIT_KIND_NUMBER_FORMAT,
    "self_correction": K.EDIT_KIND_SELF_CORRECTION,
    "grammar": K.EDIT_KIND_GRAMMAR,
}
KIND_NAMES = {v: k for k, v in KINDS.items()}
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "mhm"}
NEGATIONS = {
    "not", "no", "never", "nothing", "none", "nobody", "cannot", "dont", "don't",
    "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't", "won't", "wouldn't",
    "can't", "couldn't", "shouldn't", "haven't", "hasn't", "hadn't", "mustn't",
}  # fmt: skip
_WORDS = re.compile(r"[\w']+")
_UNAVAILABLE = grpc.StatusCode.UNAVAILABLE

SYSTEM_PROMPT = """You clean up dictated text with minimal, mechanical edits.
Reply with JSON {"edits": [...]} and nothing else. Each edit:
- "find": an exact substring of "text" that occurs EXACTLY ONCE in it; quote
  neighbouring words if needed to make it unique.
- "replace": the replacement for that substring.
- "kind": one of "allowed_kinds".
- "reason": a few words.
Never touch strings listed in "protected", never change numbers, negations or
meaning, never add content. If nothing needs fixing reply {"edits": []}."""


class ProposedEdit(TypedDict):
    """One edit as returned by the LLM (see edit_schema)."""

    find: str
    replace: str
    kind: str
    reason: str


class _ChatMessage(TypedDict):
    content: str


class _ChatChoice(TypedDict):
    message: _ChatMessage


class _ChatCompletion(TypedDict):
    """The part of an OpenAI-compatible chat completion that is read here."""

    choices: list[_ChatChoice]


def edit_schema(max_edits: int) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["edits"],
        "properties": {
            "edits": {
                "type": "array",
                "maxItems": max_edits,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["find", "replace", "kind", "reason"],
                    "properties": {
                        "find": {"type": "string", "minLength": 1, "maxLength": 200},
                        "replace": {"type": "string", "maxLength": 200},
                        "kind": {"enum": list(KINDS)},
                        "reason": {"type": "string", "maxLength": 80},
                    },
                },
            }
        },
    }


def _words(s: str) -> list[str]:
    return _WORDS.findall(s)


def _invariant_ok(kind: pb.EditKind.ValueType, find: str, replace: str) -> bool:
    """Mechanical check per kind. Kinds without one (number_format,
    self_correction, grammar) pass here; they are off in the default profile."""
    if kind == K.EDIT_KIND_PUNCTUATION:
        return [c for c in find if c.isalnum()] == [c for c in replace if c.isalnum()]
    if kind == K.EDIT_KIND_CAPITALIZATION:
        return find.casefold() == replace.casefold()
    if kind == K.EDIT_KIND_SPACING:
        return "".join(find.split()) == "".join(replace.split())
    if kind == K.EDIT_KIND_FILLER_REMOVAL:
        before, after = _words(find.lower()), _words(replace.lower())
        return len(after) < len(before) and [w for w in before if w not in FILLERS] == after
    return kind in KIND_NAMES


def _occurrences(text: str, find: str) -> list[int]:
    out: list[int] = []
    i = text.find(find)
    while i >= 0:
        out.append(i)
        i = text.find(find, i + 1)  # overlapping occurrences count
    return out


def _global_ok(text: str, edits: list[pb.Edit]) -> bool:
    raw = text.encode()
    new, last = bytearray(), 0
    for e in edits:
        new += raw[last : e.byte_start] + e.replace.encode()
        last = e.byte_end
    new += raw[last:]
    after = new.decode()

    def negations(s: str) -> int:
        return sum(w in NEGATIONS for w in _words(s.lower()))

    changed = sum(max(len(e.find.encode()), len(e.replace.encode())) for e in edits)
    return (
        negations(text) == negations(after)
        and sorted(c for c in text if c.isdigit()) == sorted(c for c in after if c.isdigit())
        and changed <= max(8, 0.15 * len(raw))
    )


def resolve_edits(
    text: str,
    proposed: list[ProposedEdit],
    protected: list[tuple[int, int]],
    allowed_kinds: list[pb.EditKind.ValueType],
) -> tuple[list[pb.Edit], list[pb.EditRejection]]:
    """Resolve quoted `find`s to byte ranges and validate (proposal-a §8.2)."""
    rejections: list[pb.EditRejection] = []
    candidates: list[pb.Edit] = []
    for p in proposed:
        find, replace = p["find"], p["replace"]
        kind = KINDS.get(p["kind"], K.EDIT_KIND_UNSPECIFIED)

        def reject(
            reason: pb.EditRejectionReason.ValueType, find: str = find, replace: str = replace
        ) -> None:
            rejections.append(pb.EditRejection(find=find, replace=replace, reason=reason))

        if find == replace:
            reject(R.EDIT_REJECTION_REASON_NO_OP)
            continue
        hits = _occurrences(text, find) if find else []
        if not hits:
            reject(R.EDIT_REJECTION_REASON_NOT_FOUND)
            continue
        if len(hits) > 1:
            reject(R.EDIT_REJECTION_REASON_NOT_UNIQUE)
            continue
        start = len(text[: hits[0]].encode())
        end = start + len(find.encode())
        if any(start < pe and ps < end for ps, pe in protected):
            reject(R.EDIT_REJECTION_REASON_PROTECTED_SPAN)
            continue
        if kind not in allowed_kinds:
            reject(R.EDIT_REJECTION_REASON_KIND_NOT_ALLOWED)
            continue
        if not _invariant_ok(kind, find, replace):
            reject(R.EDIT_REJECTION_REASON_INVARIANT)
            continue
        candidates.append(
            pb.Edit(byte_start=start, byte_end=end, find=find, replace=replace, kind=kind,
                    reason=p["reason"][:80])
        )  # fmt: skip
    edits: list[pb.Edit] = []
    for c in sorted(candidates, key=lambda e: e.byte_start):
        if edits and c.byte_start < edits[-1].byte_end:
            rejections.append(
                pb.EditRejection(
                    find=c.find, replace=c.replace, reason=R.EDIT_REJECTION_REASON_OVERLAP
                )
            )
            continue
        edits.append(c)
    # Global rules: a misbehaving model loses all its edits.
    if edits and (2 * len(rejections) > len(proposed) or not _global_ok(text, edits)):
        rejections += [
            pb.EditRejection(find=e.find, replace=e.replace, reason=R.EDIT_REJECTION_REASON_GLOBAL)
            for e in edits
        ]
        edits = []
    return edits, rejections


def _is_proposed_edit(e: object) -> TypeGuard[ProposedEdit]:
    if not isinstance(e, dict):
        return False
    fields = cast("dict[str, object]", e)
    return all(isinstance(fields.get(k), str) for k in ("find", "replace", "kind", "reason"))


def _parse_edits(body: object, max_edits: int) -> list[ProposedEdit]:
    """Strictly check the JSON-schema shape; engines differ in how strictly they
    enforce it. Raises ValueError, TypeError, KeyError or IndexError."""
    content = cast("_ChatCompletion", body)["choices"][0]["message"]["content"]
    data = cast("object", json.loads(content))
    if not isinstance(data, dict):
        raise TypeError("no edits array")
    raw_items = cast("dict[str, object]", data).get("edits")
    if not isinstance(raw_items, list):
        raise TypeError("no edits array")
    items = cast("list[object]", raw_items)
    if len(items) > max_edits:
        raise ValueError("too many edits")
    edits: list[ProposedEdit] = []
    for e in items:
        if not _is_proposed_edit(e):
            raise TypeError("malformed edit")
        edits.append(e)
    return edits


class CleanupServicer(pb_grpc.TextCleanupServicer):
    def __init__(
        self,
        cfg: CleanupConfig,
        privacy: pb.PrivacyCapabilities,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.cfg = cfg
        self.privacy = privacy
        self.http = http or httpx.AsyncClient(base_url=cfg.base_url.rstrip("/") + "/")
        self.profiles = {
            "prose": pb.CleanupProfile(
                id="prose",
                description="punctuation, capitalization, spacing and filler removal only",
                allowed_kinds=[
                    K.EDIT_KIND_PUNCTUATION,
                    K.EDIT_KIND_CAPITALIZATION,
                    K.EDIT_KIND_SPACING,
                    K.EDIT_KIND_FILLER_REMOVAL,
                ],
                max_input_bytes=cfg.max_input_bytes,
                typical_ms=cfg.default_budget_ms,
                model=cfg.model,
            )
        }

    async def close(self) -> None:
        await self.http.aclose()

    async def Cleanup(
        self,
        request: pb.CleanupRequest,
        context: grpc.aio.ServicerContext[pb.CleanupRequest, pb.CleanupResponse],
    ) -> pb.CleanupResponse:
        t0 = time.perf_counter()
        try:
            check_privacy(request.privacy, self.privacy)
            profile = self.profiles.get(request.profile_id or "prose")
            if profile is None:
                raise protocol_violation("unknown profile_id")
            raw = request.text.encode()
            if len(raw) > profile.max_input_bytes:
                raise protocol_violation(
                    f"text is {len(raw)} bytes (max {profile.max_input_bytes})"
                )
            protected: list[tuple[int, int]] = []
            for s in request.protected_spans:
                if not (0 <= s.byte_start < s.byte_end <= len(raw)) or any(
                    b < len(raw) and raw[b] & 0xC0 == 0x80 for b in (s.byte_start, s.byte_end)
                ):
                    raise protocol_violation(
                        "protected span out of range or not on a character boundary"
                    )
                protected.append((s.byte_start, s.byte_end))
            max_edits = min(request.max_edits or self.cfg.max_edits, self.cfg.max_edits)
            budget_s = (request.budget_ms or self.cfg.default_budget_ms) / 1000
            # Typed `float` in grpc-stubs, but grpc.aio returns None without a deadline.
            remaining = cast("float | None", context.time_remaining())
            if remaining is not None:
                budget_s = min(budget_s, remaining)

            response = pb.CleanupResponse(model=self.cfg.model)
            if not request.text.strip():
                return response
            t_gen = time.perf_counter()
            try:
                async with asyncio.timeout(budget_s):
                    proposed = await self._generate(request, profile, protected, max_edits)
            except TimeoutError:
                response.truncated = True  # nothing complete within budget
                response.total_ms = int((time.perf_counter() - t0) * 1000)
                return response
            response.generation_ms = int((time.perf_counter() - t_gen) * 1000)
            edits, rejections = resolve_edits(
                request.text, proposed, protected, list(profile.allowed_kinds)
            )
            response.edits.extend(edits)
            response.rejections.extend(rejections)
            response.rejected_by_server = len(rejections)
            response.total_ms = int((time.perf_counter() - t0) * 1000)
            log.info(
                "cleanup: %d bytes, %d proposed, %d accepted, %d ms",
                len(raw), len(proposed), len(edits), response.total_ms,
            )  # fmt: skip
            return response
        except HarkError as e:
            await e.abort(context, request.utterance_id)

    async def _generate(
        self, request: pb.CleanupRequest, profile: pb.CleanupProfile,
        protected: list[tuple[int, int]], max_edits: int,
    ) -> list[ProposedEdit]:  # fmt: skip
        raw = request.text.encode()
        user: dict[str, str | list[str]] = {
            "text": request.text,
            "protected": [raw[s:e].decode() for s, e in protected],
            "allowed_kinds": [KIND_NAMES[k] for k in profile.allowed_kinds],
        }
        if request.language:
            user["language"] = request.language
        if request.context.app_category:
            user["app_category"] = request.context.app_category
        if request.context.preceding_text:
            user["preceding_text"] = request.context.preceding_text
        payload = {
            "model": self.cfg.model,
            "temperature": 0,
            "max_tokens": 64 + 64 * max_edits,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "hark_edits_v1",
                    "strict": True,
                    "schema": edit_schema(max_edits),
                },
            },
        }
        headers: dict[str, str] = {}
        token = os.environ.get(self.cfg.api_key_env, "") if self.cfg.api_key_env else ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            r = await self.http.post("chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise HarkError(
                E.ERROR_CODE_BACKEND_FAILURE, f"cleanup LLM unreachable ({type(e).__name__})",
                retryable=True, grpc_code=_UNAVAILABLE,
            ) from e  # fmt: skip
        if r.status_code != 200:
            raise HarkError(
                E.ERROR_CODE_BACKEND_FAILURE, f"cleanup LLM returned HTTP {r.status_code}",
                retryable=r.status_code >= 500, grpc_code=_UNAVAILABLE,
            )  # fmt: skip
        try:
            return _parse_edits(cast("object", r.json()), max_edits)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise HarkError(
                E.ERROR_CODE_BACKEND_FAILURE, f"cleanup LLM returned a malformed edit list ({e})"
            ) from e
