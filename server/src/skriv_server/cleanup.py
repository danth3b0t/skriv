"""skriv.v1.TextCleanup: "S1-mini" by "Superwhisper" rewrites the text (ADR-001 #6).

S1-mini (a Qwen3-0.6B text normalizer, https://huggingface.co/superwhisper/s1-mini) is served
by an OpenAI-compatible /v1/chat/completions (llama-server). It removes fillers, punctuates,
cases and smooths the wording, and writes spoken numbers as digits. It is not a chat model: a
dictated instruction ("ignore previous instructions ...") is cleaned like any other text.

The server returns the rewrite, after a few deterministic touches per profile (_finish). It
does not judge fidelity: the client checks the rewrite against its input (skriv_client.fidelity)
and keeps the input when the rewrite adds, drops or reverses anything.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict, cast

import grpc
import httpx

from skriv.v1 import skriv_pb2 as pb
from skriv.v1 import skriv_pb2_grpc as pb_grpc

from .errors import E, SkrivError, check_privacy, protocol_violation

if TYPE_CHECKING:
    from .config import CleanupConfig

log = logging.getLogger("skriv_server.cleanup")
_UNAVAILABLE = grpc.StatusCode.UNAVAILABLE
_WORDS = re.compile(r"[\w']+")

# The exact system prompt and control-line values S1-mini was trained on (model card).
# Anything else can make it hallucinate, so the prompt itself cannot be tuned: the levers
# are the control line and the deterministic touches in _finish.
S1_SYSTEM = (
    "You are a text normalizer for speech-to-text transcripts. The input begins "
    "with a control line specifying the styling, structure, and context settings; "
    "clean the transcript to match those settings and output only the cleaned text."
)
# semi-formal for every profile: ASR output arrives cased and punctuated, and
# casual/semi-casual lowercase sentence starts and drop final periods. Structure "lists"
# would turn spoken enumerations into Markdown bullets, and Context "email" adds layout.
S1_CONTROL = "[Styling: semi-formal] [Structure: prose] [Context: general]"


@dataclass(frozen=True)
class ProfileSpec:
    id: str
    description: str
    single_line: bool  # line breaks in the rewrite become spaces
    add_final_period: bool  # False: drop a final period the input did not have


PROFILES = (
    ProfileSpec(
        "terminal",
        "terminal or coding-agent prompt: fillers, casing, punctuation, light rewording; one "
        "line; no final period added to a command",
        single_line=True,
        add_final_period=False,
    ),
    ProfileSpec(
        "chat",
        "chat message: fillers, casing, punctuation, light rewording; one line; the writer's "
        "casual final punctuation is kept",
        single_line=True,
        add_final_period=False,
    ),
    ProfileSpec(
        "prose",
        "email or document: fillers, full punctuation and casing, light rewording",
        single_line=False,
        add_final_period=True,
    ),
)
DEFAULT_PROFILE = "prose"

# Hesitations S1-mini sometimes keeps ("eh" is not in its training data), with the commas
# that set them off. "mm" after a number is millimetres.
_HESITATION = re.compile(
    r"(,\s*)?(?<![\w'])(?<!\d )(?:um+|uh+|uhm|erm?|eh|ah|hmm+|mhm|mm+)(?![\w'])(\s*,)?",
    re.IGNORECASE,
)
# "you know" set off by commas, or opening a sentence, is a filler ("do you know" is not).
_YOU_KNOW = (
    (re.compile(r",\s*you know\s*,", re.IGNORECASE), ","),
    (re.compile(r",\s*you know(?=\s*[.?!]|\s*$)", re.IGNORECASE), ""),
    (re.compile(r"(?:^|(?<=[.?!] ))you know,\s*", re.IGNORECASE), ""),
)


def _drop_fillers(text: str) -> str:
    out = _HESITATION.sub(lambda m: "," if m.group(1) and m.group(2) else "", text)
    for pattern, repl in _YOU_KNOW:
        out = pattern.sub(repl, out)
    if out == text:
        return text
    out = re.sub(r" {2,}", " ", out)
    out = re.sub(r" +(?=[,.?!;:])", "", out)
    out = re.sub(r"^[\s,]+", "", out).rstrip()
    # A sentence that now starts lowercase was opened by the removed filler.
    return re.sub(r"(^|[.?!] )([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)


def _finish(text: str, output: str, spec: ProfileSpec) -> str:
    """The deterministic touches after S1-mini: one line, leftover fillers, final marks."""
    out = output.strip()
    if spec.single_line:
        out = re.sub(r"\s*\n\s*", " ", out)
    out = _drop_fillers(out)
    end = text.rstrip()[-1:]
    if not spec.add_final_period and end not in (".", "!", "?") and out.endswith("."):
        out = out.removesuffix(".")  # "git commit --amend" stays a command
    if end == "!" and out.endswith("."):
        out = out.removesuffix(".") + "!"  # S1-mini flattens "!" to "."
    return out


class _ChatMessage(TypedDict):
    content: str


class _ChatChoice(TypedDict):
    message: _ChatMessage
    finish_reason: str


class _ChatCompletion(TypedDict):
    """The part of an OpenAI-compatible chat completion that is read here."""

    choices: list[_ChatChoice]


def _parse_rewrite(body: object) -> str | None:
    """The rewrite, or None if it was cut off by max_tokens. Raises ValueError, TypeError,
    KeyError or IndexError."""
    choice = cast("_ChatCompletion", body)["choices"][0]
    content = cast("object", choice["message"]["content"])
    if not isinstance(content, str):
        raise TypeError("no text")
    if "<think>" in content:
        raise ValueError("thinking output; the template must disable thinking")
    return None if cast("object", choice.get("finish_reason")) == "length" else content


# Language guard: S1-mini is English-only. Stopwords that are not also English words.
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
    words = cast("list[str]", _WORDS.findall(text.lower()))
    if not words:
        return False
    english = sum(w in _EN_STOPWORDS for w in words)
    foreign = sum(w in _FOREIGN_STOPWORDS for w in words)
    accented = sum(any(c.isalpha() and not c.isascii() for c in w) for w in words)
    return foreign > max(1, english) or accented > 0.25 * len(words)


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
        self.specs = {spec.id: spec for spec in PROFILES}
        self.profiles = {
            spec.id: pb.CleanupProfile(
                id=spec.id,
                description=spec.description,
                max_input_bytes=cfg.max_input_bytes,
                typical_ms=cfg.default_budget_ms,
                model=cfg.model,
            )
            for spec in PROFILES
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
            profile = self.profiles.get(request.profile_id or DEFAULT_PROFILE)
            if profile is None:
                raise protocol_violation("unknown profile_id")
            size = len(request.text.encode())
            if size > profile.max_input_bytes:
                raise protocol_violation(f"text is {size} bytes (max {profile.max_input_bytes})")
            budget_s = (request.budget_ms or self.cfg.default_budget_ms) / 1000
            # Typed `float` in grpc-stubs, but grpc.aio returns None without a deadline.
            remaining = cast("float | None", context.time_remaining())
            if remaining is not None:
                budget_s = min(budget_s, remaining)

            response = pb.CleanupResponse(model=self.cfg.model)
            language = request.language.lower()
            if not request.text.strip():
                response.skipped = "empty"
            elif (language and not language.startswith("en")) or likely_non_english(request.text):
                log.info("cleanup: skipped, not English (%d bytes)", size)
                response.skipped = "not_english"
            else:
                t_gen = time.perf_counter()
                try:
                    async with asyncio.timeout(budget_s):
                        output = await self._rewrite(request.text)
                except TimeoutError:
                    response.truncated = True
                else:
                    response.generation_ms = int((time.perf_counter() - t_gen) * 1000)
                    if output is None:
                        log.warning("cleanup: rewrite hit max_tokens; no rewrite")
                        response.skipped = "max_tokens"
                    else:
                        response.text = _finish(request.text, output, self.specs[profile.id])
            response.total_ms = int((time.perf_counter() - t0) * 1000)
            log.info("cleanup: %d bytes, %d ms", size, response.total_ms)
            return response
        except SkrivError as e:
            await e.abort(context, request.utterance_id)

    async def _rewrite(self, text: str) -> str | None:
        body = await self._complete({
            "model": self.cfg.model,
            "temperature": 0,  # the model card: greedy
            "max_tokens": len(text.encode()) // 2 + 32,  # output length tracks the input
            "stream": False,
            "messages": [
                {"role": "system", "content": S1_SYSTEM},
                {"role": "user", "content": f"{S1_CONTROL}\n{text}"},
            ],
            # The exact training prefix: an empty <think> block (llama.cpp --jinja, vLLM).
            "chat_template_kwargs": {"enable_thinking": False},
        })  # fmt: skip
        try:
            return _parse_rewrite(body)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise SkrivError(
                E.ERROR_CODE_BACKEND_FAILURE, f"cleanup LLM returned a malformed rewrite ({e})"
            ) from e

    async def _complete(self, payload: dict[str, object]) -> object:
        headers: dict[str, str] = {}
        token = os.environ.get(self.cfg.api_key_env, "") if self.cfg.api_key_env else ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            r = await self.http.post("chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise SkrivError(
                E.ERROR_CODE_BACKEND_FAILURE, f"cleanup LLM unreachable ({type(e).__name__})",
                retryable=True, grpc_code=_UNAVAILABLE,
            ) from e  # fmt: skip
        if r.status_code != 200:
            raise SkrivError(
                E.ERROR_CODE_BACKEND_FAILURE, f"cleanup LLM returned HTTP {r.status_code}",
                retryable=r.status_code >= 500, grpc_code=_UNAVAILABLE,
            )  # fmt: skip
        try:
            return cast("object", r.json())
        except ValueError as e:
            raise SkrivError(E.ERROR_CODE_BACKEND_FAILURE, "cleanup LLM returned no JSON") from e
