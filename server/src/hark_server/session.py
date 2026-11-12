"""Utterance sessions: model routing, validation, audio placement, context, eager
finalize, and the finalize pipeline (primary -> language fallback -> anchor -> N-best
within finalize_budget_ms).

Models: the first backend is the default; the others (e.g. an English-only model beside
a multilingual one) are loaded on first use and stay resident. A session without
model_id goes to the model with the fewest languages that covers all of
SessionStart.languages (none: the default). If a model that detects the language writes
text implausible for the requested languages (hark_server.language) and English is one
of them, the audio is decoded again with an English-only model (language fallback).

Logs never contain audio, transcripts or context; only ids, sizes and timings.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import platform
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from hark.v1 import hark_pb2 as pb

from . import __version__, language
from .backends import SAMPLE_RATE, Backend, BiasPlan, DecodeOut
from .errors import E, HarkError, check_privacy, protocol_violation
from .hits import derive_hits, word_byte_spans

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .backends import Pcm

log = logging.getLogger("hark_server.session")

PROTOCOL = pb.ProtocolVersion(major=1, minor=1)
FS = pb.FieldStatus
# A session that needs a model that is not loaded yet waits this long for it, then
# goes to the default model (the load goes on in the background).
LAZY_LOAD_WAIT_S = 3.0


def _now_us() -> int:
    return time.time_ns() // 1000


def _warning(
    code: pb.WarningCode.ValueType, message: str, term_ids: list[str] | None = None
) -> pb.ServerWarning:
    return pb.ServerWarning(code=code, message=message, term_ids=term_ids or [])


@dataclass
class CoreSettings:
    name: str
    accelerator: str
    backend_kind: str
    max_concurrent_sessions: int = 4
    max_audio_ms: int = 300_000
    default_finalize_budget_ms: int = 1000
    eager_end: bool = True
    max_message_bytes: int = 32 * 1024 * 1024


def _languages(backend: Backend) -> frozenset[str]:
    return language.bases(backend.capabilities().languages)


class AsrCore:
    """Process-wide state: the backends, routing, admission control, capabilities."""

    def __init__(self, backends: Sequence[Backend], settings: CoreSettings) -> None:
        if not backends:
            raise ValueError("need at least one backend")
        self.backends = list(backends)
        self.backend = self.backends[0]  # the default model
        self._loading: dict[str, asyncio.Task[bool]] = {}
        self.settings = settings
        self.active = 0
        self.instance_id = uuid.uuid4().hex
        self.started_unix_ms = int(time.time() * 1000)
        self.cleanup_profiles: list[pb.CleanupProfile] = []
        self.privacy = pb.PrivacyCapabilities()  # this server retains nothing

    def features(self) -> list[str]:
        caps = self.backend.capabilities()
        out: list[str] = []
        if self.settings.eager_end:
            out.append("hark.eager_end")
        if caps.bias.midstream_update not in (
            pb.MIDSTREAM_UPDATE_UNSPECIFIED,
            pb.MIDSTREAM_UPDATE_UNSUPPORTED,
        ):
            out.append("hark.midstream_context")
        if self.cleanup_profiles:
            out.append("hark.cleanup")
        return out

    def capabilities(self) -> pb.Capabilities:
        s = self.settings
        caps = pb.Capabilities(
            protocol=PROTOCOL,
            server=pb.ServerInfo(
                name=s.name,
                implementation=f"hark-server-py/{__version__} backend={s.backend_kind}",
                accelerator=s.accelerator or f"{platform.system()} {platform.machine()}",
                started_unix_ms=self.started_unix_ms,
                instance_id=self.instance_id,
            ),
            models=[b.describe() for b in self.backends],
            cleanup_profiles=self.cleanup_profiles,
            limits=pb.ServerLimits(
                max_concurrent_sessions=s.max_concurrent_sessions,
                max_audio_ms=s.max_audio_ms,
                max_message_bytes=s.max_message_bytes,
                replay_cache_ttl_ms=0,
            ),
            features=self.features(),
            privacy=self.privacy,
        )
        for model in caps.models[1:]:
            model.is_default = False
        caps.etag = hashlib.sha256(caps.SerializeToString(deterministic=True)).hexdigest()[:16]
        return caps

    def find(self, model_id: str) -> Backend | None:
        """The backend serving `model_id`; "" => the default."""
        if not model_id:
            return self.backend
        return next((b for b in self.backends if b.model_id == model_id), None)

    def route(self, start: pb.SessionStart) -> Backend:
        """start.model_id if set (unknown: the default, rejected by validation), else the
        model with the fewest languages that covers start.languages (ties: config order),
        else the default (which rejects an unsupported primary language)."""
        if start.model_id:
            return self.find(start.model_id) or self.backend
        wanted = language.bases(start.languages)
        best, best_n = self.backend, None
        for b in self.backends if wanted else ():
            have = _languages(b)
            ok = b.state != pb.MODEL_STATE_FAILED and wanted <= have
            if ok and (best_n is None or len(have) < best_n):
                best, best_n = b, len(have)
        return best

    def english_only(self, exclude: Backend) -> Backend | None:
        """A model that only speaks English (the language fallback's target)."""
        return next(
            (b for b in self.backends if b is not exclude and _languages(b) == frozenset({"en"})),
            None,
        )

    async def ready(self, backend: Backend, wait_s: float) -> bool:
        """Load and warm `backend` if it is cold (once; in the background), waiting at most
        `wait_s`. True if it is ready."""
        if backend.state == pb.MODEL_STATE_READY:
            return True
        if backend.state == pb.MODEL_STATE_FAILED:
            return False
        task = self._loading.get(backend.model_id)
        if task is None:
            task = asyncio.ensure_future(self._load(backend))
            self._loading[backend.model_id] = task
        try:
            return await asyncio.wait_for(asyncio.shield(task), wait_s)
        except TimeoutError:
            return False

    async def _load(self, backend: Backend) -> bool:
        t0 = time.perf_counter()
        try:
            await backend.warmup()
        except Exception:
            log.exception("model %s failed to load", backend.model_id)
            return False
        finally:
            self._loading.pop(backend.model_id, None)
        log.info(
            "model %s loaded on first use in %d ms",
            backend.model_id, (time.perf_counter() - t0) * 1000,
        )  # fmt: skip
        return True

    async def open(self, start: pb.SessionStart) -> Session:
        """Route, validate and admit. Raises HarkError (session is then REJECTED)."""
        backend = self.route(start)
        unready = backend is not self.backend and not await self.ready(backend, LAZY_LOAD_WAIT_S)
        if unready and not start.model_id:  # one asked for by model_id is reported not ready
            log.info("model %s not ready; using %s", backend.model_id, self.backend.model_id)
            backend = self.backend
        session = Session(self, start, backend)  # validation only, no side effects
        if self.active >= self.settings.max_concurrent_sessions:
            raise HarkError(
                E.ERROR_CODE_OVERLOADED,
                f"{self.active} sessions active (max {self.settings.max_concurrent_sessions})",
                retryable=True,
                retry_after_ms=500,
            )
        self.active += 1
        session.admitted = True
        return session


@dataclass
class _Context:
    revision: int
    plan: BiasPlan | None
    hit_terms: list[pb.BiasTerm]
    truncated: bool
    strength: float = 1.0


type _Computed = tuple[pb.FinalResult, list[pb.ServerWarning]]


@dataclass
class _Eager:
    task: asyncio.Task[_Computed]
    samples: int
    revision: int


class Session:
    def __init__(self, core: AsrCore, start: pb.SessionStart, backend: Backend) -> None:
        self.core = core
        self.backend = backend
        self.start = start
        self.final_warnings: list[pb.ServerWarning] = []  # sent before the FinalResult
        self.session_id = uuid.uuid4().hex
        self.utterance_id = start.utterance_id
        self.admitted = False
        self.caps = self.backend.describe()
        self.start_warnings: list[pb.ServerWarning] = []
        self.pcm = bytearray()
        self.last_seq = -1
        self.audio_gap = False
        self.ended = False
        self.t_end = 0.0
        self.end_unix_us = 0
        self.eager: _Eager | None = None

        if not start.utterance_id:
            raise protocol_violation("start.utterance_id is required")
        self._check_model()
        self._check_audio_format()
        self._check_languages()
        check_privacy(start.privacy, core.privacy)
        self._check_required_features()

        s = core.settings
        max_ms = min(start.deadlines.max_audio_ms or s.max_audio_ms, s.max_audio_ms)
        self.max_samples = max_ms * SAMPLE_RATE // 1000
        self.budget_ms = start.deadlines.finalize_budget_ms or s.default_finalize_budget_ms
        if start.options.partials and not self.caps.partials:
            self.start_warnings.append(
                _warning(pb.WARNING_CODE_FEATURE_DEGRADED, "partials not supported by this model")
            )
        t0 = time.perf_counter()
        self.ctx, warnings = self._build_context(start.context)
        self.bias_prepare_ms = int((time.perf_counter() - t0) * 1000)
        self.start_warnings.extend(warnings)
        self.stream = self.backend.open_stream(self.ctx.plan)

    # ------------------------------------------------------------------ validation
    def _check_model(self) -> None:
        wanted = self.start.model_id
        if wanted and wanted != self.backend.model_id:
            raise HarkError(E.ERROR_CODE_MODEL_NOT_FOUND, "unknown model_id")
        if self.backend.state != pb.MODEL_STATE_READY:
            raise HarkError(
                E.ERROR_CODE_MODEL_NOT_READY,
                f"model state {pb.ModelState.Name(self.backend.state)}",
                retryable=True,
                retry_after_ms=2000,
            )

    def _check_audio_format(self) -> None:
        fmt = self.start.audio_format
        enc = fmt.encoding or pb.AUDIO_ENCODING_PCM_S16LE
        rate = fmt.sample_rate_hz or SAMPLE_RATE
        channels = fmt.channels or 1
        ok = channels == 1 and any(
            f.encoding == enc and rate in f.sample_rates_hz for f in self.caps.audio_formats
        )
        if not ok:
            raise HarkError(
                E.ERROR_CODE_UNSUPPORTED_AUDIO_FORMAT,
                f"unsupported audio format {pb.AudioEncoding.Name(enc)} {rate} Hz {channels} ch",
            )

    def _check_languages(self) -> None:
        if not self.start.languages or not self.caps.languages:
            return
        primary = self.start.languages[0].split("-")[0].lower()
        if primary not in {lang.split("-")[0].lower() for lang in self.caps.languages}:
            raise HarkError(E.ERROR_CODE_UNSUPPORTED_LANGUAGE, "unsupported primary language")

    def _feature_available(self, name: str) -> bool:
        c = self.caps
        return {
            "bias": c.bias.mechanism not in (pb.BIAS_MECHANISM_UNSPECIFIED, pb.BIAS_MECHANISM_NONE),
            "free_text": c.free_text.description or c.free_text.preceding_text,
            "result.word_timestamps": c.timestamps.source
            not in (pb.TIMESTAMP_SOURCE_UNSPECIFIED, pb.TIMESTAMP_SOURCE_NONE),
            "result.word_confidence": c.timestamps.word_confidence,
            "result.n_best": c.n_best.max_n > 1,
            "result.anchor": c.anchor_cost
            not in (pb.ANCHOR_COST_UNSPECIFIED, pb.ANCHOR_COST_UNAVAILABLE),
        }.get(name, name in self.core.features())

    def _check_required_features(self) -> None:
        missing = [f for f in self.start.required_features if not self._feature_available(f)]
        if missing:
            raise HarkError(
                E.ERROR_CODE_REQUIRED_FEATURE_UNAVAILABLE,
                f"required features unavailable: {', '.join(missing)}",
                unavailable_features=missing,
            )

    def _build_context(self, ctx: pb.RecognitionContext) -> tuple[_Context, list[pb.ServerWarning]]:
        terms = list(ctx.bias.terms)
        seen: set[str] = set()
        for t in terms:
            if not t.id or t.id in seen:
                raise protocol_violation("bias term ids must be non-empty and unique")
            seen.add(t.id)
            if not t.canonical.strip():
                raise protocol_violation(f"bias term {t.id}: empty canonical")
            if t.HasField("weight") and not (0.0 <= t.weight <= 1.0):
                raise protocol_violation(f"bias term {t.id}: weight outside [0, 1]")
        strength = ctx.bias.strength if ctx.bias.HasField("strength") else 1.0
        if not math.isfinite(strength) or strength < 0:
            raise protocol_violation("bias strength must be a finite value >= 0")

        warnings: list[pb.ServerWarning] = []
        bias = self.caps.bias
        boosting = bias.mechanism not in (pb.BIAS_MECHANISM_UNSPECIFIED, pb.BIAS_MECHANISM_NONE)
        dropped: list[str] = []
        aliases_cut: list[str] = []
        if not boosting:
            kept = terms
            if terms:
                warnings.append(
                    _warning(
                        pb.WARNING_CODE_CONTEXT_IGNORED,
                        "bias mechanism NONE: terms are used for hit detection only",
                    )
                )
        else:
            kept, dropped, aliases_cut = self._limit_terms(terms)
            if dropped:
                warnings.append(
                    _warning(
                        pb.WARNING_CODE_BIAS_TERMS_TRUNCATED,
                        f"{len(dropped)} terms dropped",
                        dropped,
                    )
                )
            if aliases_cut:
                warnings.append(
                    _warning(
                        pb.WARNING_CODE_ALIASES_DROPPED, "aliases over limit dropped", aliases_cut
                    )
                )
        free = ctx.free_text
        if (free.description or free.preceding_text) and not self._feature_available("free_text"):
            warnings.append(
                _warning(
                    pb.WARNING_CODE_CONTEXT_IGNORED, "free-text context not supported by this model"
                )
            )
        plan = self.backend.plan_bias(kept, strength) if boosting else None
        self.dropped_ids = dropped
        truncated = bool(dropped or aliases_cut)
        return _Context(ctx.revision, plan, kept, truncated, strength), warnings

    def _limit_terms(
        self, terms: list[pb.BiasTerm]
    ) -> tuple[list[pb.BiasTerm], list[str], list[str]]:
        """Apply the model's bias limits: (kept terms, dropped ids, ids with aliases cut)."""
        bias = self.caps.bias
        kept: list[pb.BiasTerm] = []
        dropped: list[str] = []
        aliases_cut: list[str] = []
        for t in terms:
            if bias.max_term_bytes and len(t.canonical.encode()) > bias.max_term_bytes:
                dropped.append(t.id)
                continue
            if bias.max_aliases_per_term and len(t.aliases) > bias.max_aliases_per_term:
                cut = pb.BiasTerm()
                cut.CopyFrom(t)
                del cut.aliases[bias.max_aliases_per_term :]
                aliases_cut.append(t.id)
                kept.append(cut)
            else:
                kept.append(t)
        if bias.max_terms and len(kept) > bias.max_terms:
            dropped += [t.id for t in kept[bias.max_terms :]]
            kept = kept[: bias.max_terms]
        return kept, dropped, aliases_cut

    # ------------------------------------------------------------------ messages
    def accepted(self) -> pb.SessionAccepted:
        o, c, plan = self.start.options, self.caps, self.ctx.plan
        n_req = max(o.n_best, 2 if "result.n_best" in self.start.required_features else 0)
        applied = pb.AppliedConfig(
            bias_mechanism=c.bias.mechanism if plan else pb.BIAS_MECHANISM_NONE,
            bias_terms_used=len(plan.terms) if plan else 0,
            bias_terms_dropped=self.dropped_ids,
            free_text_used=False,
            n_best=min(n_req, c.n_best.max_n) if n_req > 1 else 1,
            anchor_planned=self._anchor_policy(plan) in (None, "decode"),
            word_timestamps=o.word_timestamps and self._feature_available("result.word_timestamps"),
            word_confidence=o.word_confidence and c.timestamps.word_confidence,
        )
        if plan:
            applied.backend_params.update(plan.backend_params)
            applied.term_boosts.update(plan.term_boosts)
        return pb.SessionAccepted(
            session_id=self.session_id,
            model_id=self.backend.model_id,
            applied=applied,
            server_time_unix_ms=int(time.time() * 1000),
            capabilities_etag=self.core.capabilities().etag,
        )

    def status(self) -> pb.StreamStatus:
        received = len(self.pcm) // 2
        ms = received * 1000 // SAMPLE_RATE
        return pb.StreamStatus(
            audio_received_ms=ms,
            audio_processed_ms=ms,  # BUFFERED
            queued_sessions=self.backend.queued_jobs,
            samples_received=received,
        )

    # ------------------------------------------------------------------ client input
    def add_audio(self, chunk: pb.AudioChunk) -> list[pb.ServerWarning]:
        if self.ended:
            raise protocol_violation("audio after end_of_audio")
        if chunk.seq <= self.last_seq:
            raise protocol_violation("audio seq must be strictly increasing")
        if len(chunk.data) != 2 * chunk.num_samples:
            raise protocol_violation("num_samples does not match PCM data length")
        received = len(self.pcm) // 2
        if chunk.start_sample < received:
            raise protocol_violation(
                f"overlapping audio: start_sample {chunk.start_sample} < received {received}"
            )
        if chunk.start_sample + chunk.num_samples > self.max_samples:
            raise HarkError(
                E.ERROR_CODE_AUDIO_TOO_LONG, f"audio exceeds {self.max_samples} samples"
            )
        warnings: list[pb.ServerWarning] = []
        gap = chunk.start_sample - received
        if gap:
            self.pcm.extend(bytes(2 * gap))
            self.audio_gap = True
            warnings.append(
                _warning(
                    pb.WARNING_CODE_AUDIO_GAP,
                    f"{gap} samples zero-filled before seq {chunk.seq} (start_sample {chunk.start_sample})",
                )
            )
        self.pcm.extend(chunk.data)
        if self.stream is not None:
            if gap:
                self.stream.feed(bytes(2 * gap))
            self.stream.feed(chunk.data)
        self.last_seq = chunk.seq
        self._invalidate_eager()
        return warnings

    def update_context(self, update: pb.ContextUpdate) -> list[pb.ServerWarning]:
        if self.ended:
            raise protocol_violation("context_update after end_of_audio")
        ctx = update.context
        if ctx.revision <= self.ctx.revision:
            raise protocol_violation("context revision must strictly increase")
        if self.caps.bias.midstream_update in (
            pb.MIDSTREAM_UPDATE_UNSPECIFIED,
            pb.MIDSTREAM_UPDATE_UNSUPPORTED,
        ):
            return [
                _warning(pb.WARNING_CODE_CONTEXT_IGNORED, "mid-stream context updates unsupported")
            ]
        self.ctx, warnings = self._build_context(ctx)
        if self.stream is not None:
            self.stream.set_plan(self.ctx.plan)
        self._invalidate_eager()
        return warnings

    def eager_end(self, msg: pb.EagerEnd) -> None:
        if not self.core.settings.eager_end or self.ended:
            return
        received = len(self.pcm) // 2
        if msg.total_samples != received:
            log.info("session %s: eager_end ignored (sample mismatch)", self.session_id)
            return
        self._invalidate_eager()
        pcm = np.frombuffer(bytes(self.pcm), dtype="<i2")
        task = asyncio.create_task(self._compute(pcm, self.ctx, time.perf_counter()))
        self.eager = _Eager(task, received, self.ctx.revision)

    def end_of_audio(self, eoa: pb.EndOfAudio | None) -> None:
        """eoa=None: half-close without EndOfAudio."""
        if self.ended:
            raise protocol_violation("duplicate end_of_audio")
        self.ended = True
        self.t_end = time.perf_counter()
        self.end_unix_us = _now_us()
        if eoa is not None:
            received = len(self.pcm) // 2
            if eoa.total_samples != received or eoa.last_seq != max(self.last_seq, 0):
                raise protocol_violation(
                    f"end_of_audio mismatch: total_samples {eoa.total_samples} vs {received}, "
                    f"last_seq {eoa.last_seq} vs {max(self.last_seq, 0)}"
                )

    def _invalidate_eager(self) -> None:
        if self.eager is not None:
            self.eager.task.cancel()
            self.eager = None

    def close(self) -> None:
        self._invalidate_eager()
        if self.stream is not None:
            self.stream.close()
        if self.admitted:
            self.admitted = False
            self.core.active -= 1

    # ------------------------------------------------------------------ finalize
    async def finalize(self) -> pb.FinalResult:
        """The FinalResult; warnings about it (language fallback) in final_warnings."""
        received = len(self.pcm) // 2
        eager, self.eager = self.eager, None
        result: pb.FinalResult | None = None
        if eager and eager.samples == received and eager.revision == self.ctx.revision:
            try:
                result, self.final_warnings = await eager.task
                result.timings.eager_hit = True
            except Exception:
                log.exception("session %s: eager decode failed; decoding again", self.session_id)
        elif eager:
            eager.task.cancel()
        if result is None:
            pcm = np.frombuffer(bytes(self.pcm), dtype="<i2")
            result, self.final_warnings = await self._compute(pcm, self.ctx, self.t_end)
        end_to_final = int((time.perf_counter() - self.t_end) * 1000)
        t = result.timings
        t.end_to_final_ms = end_to_final
        t.server_end_received_unix_us = self.end_unix_us
        t.server_final_sent_unix_us = _now_us()
        result.flags.finalize_budget_exceeded = end_to_final > self.budget_ms
        result.flags.audio_gap = self.audio_gap
        log.info(
            "session %s: final audio_ms=%d end_to_final_ms=%d eager_hit=%s",
            self.session_id, t.audio_ms, end_to_final, t.eager_hit,
        )  # fmt: skip
        return result

    def _anchor_policy(self, plan: BiasPlan | None) -> str | None:
        """None: anchor == primary (no context reached the decoder); "decode":
        separate unbiased decode; otherwise the FieldStatus name to report."""
        mode = self.start.options.anchor or pb.ANCHOR_MODE_IF_CHEAP
        required = "result.anchor" in self.start.required_features
        if mode == pb.ANCHOR_MODE_OFF and not required:
            return "NOT_REQUESTED"
        if plan is None:
            return None
        cost = self.caps.anchor_cost
        if cost in (pb.ANCHOR_COST_UNSPECIFIED, pb.ANCHOR_COST_UNAVAILABLE):
            return "UNSUPPORTED"
        if cost == pb.ANCHOR_COST_FULL_PASS and mode != pb.ANCHOR_MODE_ALWAYS and not required:
            return "NOT_REQUESTED"
        return "decode"

    async def _language_fallback(
        self, pcm: Pcm, ctx: _Context, primary: DecodeOut
    ) -> tuple[Backend, BiasPlan | None, DecodeOut, pb.ServerWarning] | None:
        """Decode again with an English-only model if this model detects the language, wrote
        text implausible for SessionStart.languages, and English is one of them (not for a
        model asked for by model_id)."""
        allowed = language.bases(self.start.languages)
        if "en" not in allowed or not self.caps.language_detection or self.start.model_id:
            return None
        reason = language.implausible(primary.text, allowed)
        target = self.core.english_only(self.backend) if reason else None
        if target is None or not await self.core.ready(target, LAZY_LOAD_WAIT_S):
            return None
        plan = target.plan_bias(ctx.hit_terms, ctx.strength) if ctx.plan is not None else None
        out = await target.decode(pcm, plan)
        log.info(
            "session %s: language fallback (%s) %s -> %s",
            self.session_id, reason, self.backend.model_id, target.model_id,
        )  # fmt: skip
        warning = _warning(
            pb.WARNING_CODE_LANGUAGE_FALLBACK,
            f"{reason}: {self.backend.model_id} output implausible for "
            f"{','.join(sorted(allowed))}; decoded again with {target.model_id}",
        )
        return target, plan, out, warning

    async def _compute(  # noqa: PLR0912, PLR0915 - one pipeline sharing budget, flags and timings
        self, pcm: Pcm, ctx: _Context, t0: float
    ) -> _Computed:
        """Primary decode (and language fallback), then optional work in shedding order
        (anchor before N-best: N-best is shed first). Required features are never shed."""
        opts, req, caps = self.start.options, set(self.start.required_features), self.caps
        audio_ms = len(pcm) * 1000 // SAMPLE_RATE

        def remaining_ms() -> float:
            return self.budget_ms - (time.perf_counter() - t0) * 1000

        flags = pb.ResultFlags(context_truncated=ctx.truncated)
        timings = pb.Timings(audio_ms=audio_ms, bias_prepare_ms=self.bias_prepare_ms)

        if self.stream is not None:
            primary = await self.stream.finish(pcm, ctx.plan)
        else:
            primary = await self.backend.decode(pcm, ctx.plan)
        timings.queue_ms = primary.queue_ms
        timings.encoder_ms = primary.encoder_ms
        timings.decode_ms = primary.decode_ms
        for k, v in primary.backend_ms.items():
            timings.backend_ms[k] = v

        # Language fallback: from here on, the model that wrote the result.
        backend, plan, warnings = self.backend, ctx.plan, list[pb.ServerWarning]()
        t = time.perf_counter()
        fallback = await self._language_fallback(pcm, ctx, primary)
        if fallback is not None:
            backend, plan, primary, warning = fallback
            caps = backend.describe()
            warnings.append(warning)
            timings.backend_ms["language_fallback"] = int((time.perf_counter() - t) * 1000)

        # Anchor.
        anchor: DecodeOut | None = None
        policy = self._anchor_policy(plan)
        if policy is None:
            anchor, flags.anchor = primary, FS.FIELD_STATUS_PRESENT
        elif policy != "decode":
            flags.anchor = FS.Value(f"FIELD_STATUS_{policy}")
        elif "result.anchor" not in req and backend.estimate_ms(audio_ms) > remaining_ms():
            flags.anchor = FS.FIELD_STATUS_SKIPPED_BUDGET
        else:
            t = time.perf_counter()
            try:
                anchor = await backend.decode(pcm, None)
                flags.anchor = FS.FIELD_STATUS_PRESENT
            except Exception:
                log.exception("session %s: anchor decode failed", self.session_id)
                flags.anchor = FS.FIELD_STATUS_FAILED
            timings.anchor_ms = int((time.perf_counter() - t) * 1000)

        # N-best.
        n_req = max(opts.n_best, 2 if "result.n_best" in req else 0)
        alternatives: list[DecodeOut] = []
        if n_req <= 1:
            flags.n_best = FS.FIELD_STATUS_NOT_REQUESTED
        elif caps.n_best.max_n <= 1:
            flags.n_best = FS.FIELD_STATUS_UNSUPPORTED
        elif "result.n_best" not in req and backend.estimate_ms(audio_ms) > remaining_ms():
            flags.n_best = FS.FIELD_STATUS_SKIPPED_BUDGET
        else:
            t = time.perf_counter()
            try:
                alternatives = await backend.n_best(pcm, plan, min(n_req, caps.n_best.max_n))
                flags.n_best = FS.FIELD_STATUS_PRESENT
            except Exception:
                log.exception("session %s: n-best failed", self.session_id)
                flags.n_best = FS.FIELD_STATUS_FAILED
            timings.n_best_ms = int((time.perf_counter() - t) * 1000)

        # Words, confidence, hits.
        t_post = time.perf_counter()
        ts_ok = self._feature_available("result.word_timestamps")
        want_ts = opts.word_timestamps or "result.word_timestamps" in req
        want_conf = opts.word_confidence or "result.word_confidence" in req
        with_words = (want_ts or want_conf) and ts_ok
        flags.word_timestamps = (
            FS.FIELD_STATUS_NOT_REQUESTED
            if not want_ts
            else FS.FIELD_STATUS_PRESENT
            if ts_ok
            else FS.FIELD_STATUS_UNSUPPORTED
        )
        conf_ok = caps.timestamps.word_confidence and with_words
        flags.word_confidence = (
            FS.FIELD_STATUS_NOT_REQUESTED
            if not want_conf
            else FS.FIELD_STATUS_PRESENT
            if conf_ok
            else FS.FIELD_STATUS_UNSUPPORTED
        )

        spans = word_byte_spans(primary.text, primary.words)
        hits: list[pb.BiasHit] = []
        word_terms = [""] * len(primary.words)
        if ctx.hit_terms:
            hits, word_terms = derive_hits(
                primary.words,
                spans,
                anchor.words if anchor else None,
                ctx.hit_terms,
                mechanism=caps.bias.mechanism if plan else pb.BIAS_MECHANISM_NONE,
                term_boosts=plan.term_boosts if plan else {},
            )
            flags.bias_hits = FS.FIELD_STATUS_PRESENT
        else:
            flags.bias_hits = FS.FIELD_STATUS_NOT_REQUESTED

        def hyp(
            out: DecodeOut,
            origin: pb.HypothesisOrigin.ValueType,
            *,
            words: bool,
            terms: list[str] | None = None,
        ) -> pb.Hypothesis:
            h = pb.Hypothesis(text=out.text, origin=origin)
            if out.score is not None:
                h.score = out.score
                h.score_kind = caps.n_best.score_kind
            if words:
                out_spans = word_byte_spans(out.text, out.words)
                for k, (w, (bs, be)) in enumerate(zip(out.words, out_spans, strict=True)):
                    pw = h.words.add(
                        text=w.text, start_ms=w.start_ms, end_ms=w.end_ms,
                        text_byte_start=bs, text_byte_end=be,
                        bias_term_id=terms[k] if terms else "",
                    )  # fmt: skip
                    if conf_ok and w.confidence is not None:
                        pw.confidence = w.confidence
            return h

        biased = plan is not None
        result = pb.FinalResult(
            utterance_id=self.utterance_id, context_revision=ctx.revision, model_id=backend.model_id
        )
        spoken = _languages(backend)
        if len(spoken) == 1:  # a single-language model: the only language it can write
            (result.detected_language,) = spoken
        origin = pb.HYPOTHESIS_ORIGIN_BIASED if biased else pb.HYPOTHESIS_ORIGIN_UNBIASED
        result.n_best.append(hyp(primary, origin, words=with_words, terms=word_terms))
        for alt in alternatives:
            result.n_best.append(
                hyp(alt, pb.HYPOTHESIS_ORIGIN_BEAM, words=with_words and opts.n_best_words)
            )
        if anchor is not None:
            result.anchor.CopyFrom(hyp(anchor, pb.HYPOTHESIS_ORIGIN_UNBIASED, words=with_words))
        result.bias_hits.extend(hits)
        flags.no_speech = not primary.text.strip()
        timings.postprocess_ms = int((time.perf_counter() - t_post) * 1000)
        result.timings.CopyFrom(timings)
        result.flags.CopyFrom(flags)
        result.privacy.CopyFrom(pb.PrivacyAck())  # nothing retained, logged or cached
        return result, warnings
