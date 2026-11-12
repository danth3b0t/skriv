"""One dictation, end to end.

hotkey down -> Utterance():
    context.collect() starts on a thread; the provider router picks the server
    (hark_client.providers) meanwhile. SessionStart goes out when the context is ready or
    the deadline passes (with an empty context then; without context at all if the
    provider has send_context = false); audio recorded meanwhile is replayed first.
audio -> Utterance.feed(): 40 ms chunks streamed while the key is held.
hotkey up -> Utterance.finish():
    EndOfAudio -> FinalResult -> anchor gate -> canonicalize -> spoken symbols (terminal profile) ->
    optional cleanup (deadline, falls back to the canonical text) -> guardrails ->
    focus re-check -> caret context / shadow buffer -> smart spacing -> paste ->
    history.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING

from google.protobuf import json_format

from hark.v1 import hark_pb2 as pb
from hark_client import (
    asr,
    cleanup,
    context,
    continuation,
    corrections,
    gate,
    guardrails,
    history,
    symbols,
)
from hark_client.asr import SAMPLE_RATE, AsrError, TranscribeStream, uuid7
from hark_client.audio import flat, speech_ms
from hark_client.cleanup import CleanupOutcome
from hark_client.config import expand
from hark_client.context import CaretContext, ContextSnapshot, FocusInfo, WordInfo
from hark_client.context.words import dictionary, dictionary_terms, merge_terms
from hark_client.inject import PasteResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.config import Config
    from hark_client.continuation import ShadowBuffer
    from hark_client.feedback import Feedback
    from hark_client.inject import Injector
    from hark_client.providers import Provider, Router

log = logging.getLogger("hark")


@dataclass
class Services:
    cfg: Config
    router: Router
    injector: Injector
    feedback: Feedback
    out: Callable[[str], None] | None = None  # per-stage report (`hark once`)
    on_stage: Callable[[str, str], None] | None = None  # (utterance id, "cleaning"): indicator


@dataclass
class Result:
    utterance_id: str
    status: str  # pasted | copied | paste_failed | focus_changed | empty | too_short | no_speech | cancelled | error
    profile: str = ""
    raw_text: str = ""
    canonical_text: str = ""
    uncleaned_text: str = (
        ""  # what would be pasted without cleanup (symbols, corrections, guardrails)
    )
    final_text: str = ""
    replacements: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    cleanup: CleanupOutcome | None = None
    guard_events: list[str] = field(default_factory=list[str])
    paste: PasteResult | None = None
    error: str = ""
    timings: dict[str, float] = field(default_factory=dict[str, float])
    symbols: list[str] = field(default_factory=list[str])  # spoken-symbol rules applied
    corrections: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    spacing: dict[str, object] | None = None  # smart-spacing decision (see _spacing)
    gate: dict[str, object] | None = None  # anchor gate status and per-region decisions
    pasted_text: str = ""  # exactly what was pasted (smart spacing applied)
    stop_reason: str = ""  # key_up | limit ("" = never got that far)
    provider: str = ""  # the provider that transcribed it
    fallback: bool = False  # ... was not the first in priority order (a failover counts)
    mic_interrupted: bool = False  # the mic dropped out while recording
    input_device: str = ""  # the microphone it was recorded from ("" = not a live recording)
    flat_signal: bool = False  # no_speech because the input sent no signal at all (a mic problem)
    delivery: str = "none"  # sent | copied | failed | none (docs/protocol/ctl-v1.md)


FAILOVER_MIN_S = 0.5  # less time left than this: no failover, the dictation fails


def delivery(res: Result) -> str:
    """Where the text went, from the paste outcome."""
    paste = res.paste
    if paste is None:
        return "none"
    if paste.ok and paste.method != "clipboard":
        return "sent"
    return "copied" if paste.in_clipboard else "failed"


def _collect(deadline_s: float, max_terms: int) -> ContextSnapshot:
    # Looked up at call time so tests can substitute the context engine.
    return context.collect(deadline_s, max_terms)


def _focus_token() -> str:
    # Looked up at call time so tests can substitute the context engine.
    return context.focus_token()


def _caret_context(focus: FocusInfo) -> CaretContext:
    # Looked up at call time so tests can substitute the context engine.
    return context.caret_context(focus)


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)


class Utterance:
    def __init__(
        self,
        svc: Services,
        *,
        profile: str | None = None,
        preroll: bytes = b"",
        replay: ContextSnapshot | None = None,  # `hark retry`: the original key-down context
    ) -> None:
        self.svc = svc
        self.replay = replay
        self.id = uuid7()
        self.created_unix = time.time()
        self.t0 = time.perf_counter()
        self.profile_override = profile
        self.preroll_ms = len(preroll) // 2 * 1000 // SAMPLE_RATE
        self.pcm = bytearray()
        self.timings: dict[str, float] = {}
        self.start_message: pb.SessionStart | None = None
        self.snapshot_at_start = ContextSnapshot()
        self.provider: Provider | None = None
        self.route: list[dict[str, object]] = []
        self.stream: TranscribeStream | None = None
        self._ctx: Future[ContextSnapshot] = Future()
        self._lock = threading.Lock()
        self._done = False
        self._cancelled = False
        self._committed = False  # past the point of no return (clipboard write / paste)
        self.keyup_unix_us = 0
        self.t_release = 0.0  # time.perf_counter() at key release (set by finish)
        self.stop_reason = ""
        self.mic_interrupted = False  # set by the daemon's mic watchdog
        self.input_device = ""  # set by the daemon: the mic's name
        self.failed_over = False
        threading.Thread(target=self._run_collect, daemon=True, name="hark-context").start()
        self._starter = threading.Thread(target=self._send_start, daemon=True, name="hark-start")
        self._starter.start()
        if preroll:
            self.feed(preroll)

    # -- hotkey down ---------------------------------------------------------------

    def _run_collect(self) -> None:
        t = time.perf_counter()
        cfg = self.svc.cfg.context
        try:
            snap = self.replay or _collect(cfg.deadline_ms / 1000, cfg.max_terms)
        except Exception as e:  # the contract says never raises; do not lose the dictation
            log.exception("context.collect failed")
            snap = ContextSnapshot(errors=(f"collect: {type(e).__name__}: {e}",))
        user_words = dictionary(self.svc.cfg.words, snap.repo_root, cfg.max_terms)
        if user_words:  # hand-written spellings go first
            words = dictionary_terms(user_words)
            snap = replace(snap, terms=merge_terms(snap.terms, words, cfg.max_terms))
        self.timings["context_ms"] = _ms(t)
        self._ctx.set_result(snap)

    def _send_start(self) -> None:
        t = time.perf_counter()
        provider, self.route = self.svc.router.pick()  # while the context is collected
        self.provider = provider
        self.timings["route_ms"] = _ms(t)
        deadline_s = self.svc.cfg.context.deadline_ms / 1000
        try:
            snap = self._ctx.result(timeout=max(0.0, deadline_s - (time.perf_counter() - t)))
        except TimeoutError:
            snap = ContextSnapshot(errors=("collect: missed the SessionStart deadline",))
        self.snapshot_at_start = snap
        self.start_message = self._session_start(snap, provider)
        with self._lock:
            if self._cancelled:
                return
            self.stream = TranscribeStream(provider.channel)
            self.stream.feed(bytes(self.pcm))  # recorded while the provider was chosen
            self.stream.start(self.start_message)
        self.timings["session_start_ms"] = _ms(self.t0)

    def _session_start(self, snap: ContextSnapshot, provider: Provider) -> pb.SessionStart:
        cfg = self.svc.cfg
        ctx = pb.RecognitionContext(revision=1)
        bias = asr.bias_list(snap.terms) if provider.cfg.send_context else pb.BiasList()
        if bias.terms:
            ctx.bias.CopyFrom(bias)
        return pb.SessionStart(
            utterance_id=self.id,
            model_id=provider.cfg.model_id,
            languages=[cfg.server.language] if cfg.server.language else [],
            audio_format=pb.AudioFormat(
                encoding=pb.AUDIO_ENCODING_PCM_S16LE,
                sample_rate_hz=SAMPLE_RATE,
                channels=1,
                preroll_ms=self.preroll_ms,
            ),
            context=ctx,
            options=pb.ResultOptions(
                word_timestamps=True, word_confidence=True, anchor=pb.ANCHOR_MODE_IF_CHEAP
            ),
            deadlines=pb.Deadlines(finalize_budget_ms=cfg.server.finalize_budget_ms),
            client=pb.ClientInfo(client_version=asr.CLIENT_VERSION),
        )

    # -- while speaking ------------------------------------------------------------

    def feed(self, pcm16: bytes) -> None:
        with self._lock:
            if self._done:
                return
            self.pcm += pcm16
            if self.stream is not None:
                self.stream.feed(pcm16)

    @property
    def audio_ms(self) -> int:
        return len(self.pcm) // 2 * 1000 // SAMPLE_RATE

    def cancel(self, reason: pb.CancelReason.ValueType = pb.CANCEL_REASON_USER_ABORT) -> bool:
        """Drop the dictation; False when it is already being pasted (too late)."""
        with self._lock:
            if self._committed:
                return False
            self._done = self._cancelled = True
            stream = self.stream
        if stream is not None:
            stream.cancel(reason)
        return True

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _commit(self) -> bool:
        """The point of no return before the clipboard write / paste; False if cancelled."""
        with self._lock:
            if self._cancelled:
                return False
            self._committed = True
            return True

    def wait_started(self) -> None:
        """Until the provider is chosen and the key-down context is known (bounded)."""
        self._starter.join()

    # -- hotkey up -----------------------------------------------------------------

    def _report(self, stage: str, text: str) -> None:
        if self.svc.out is not None:
            self.svc.out(f"{stage:<9} {text}")

    def finish(
        self,
        *,
        min_ms: int = 0,
        reason: pb.EndReason.ValueType = pb.END_REASON_KEY_UP,
        keyup_unix_us: int | None = None,
        released: float | None = None,
        shadow: ShadowBuffer | None = None,
    ) -> Result:
        """keyup_unix_us: when the key was released, if earlier than this call; released: the
        same moment on the time.perf_counter() clock (release_to_paste_ms starts there).
        shadow: the daemon's record of its pastes and later keys (spacing fallback)."""
        self.keyup_unix_us = keyup_unix_us or asr.unix_us()
        self.t_release = time.perf_counter() if released is None else released
        self.stop_reason = "limit" if reason == pb.END_REASON_MAX_DURATION else "key_up"
        held_ms = _ms(self.t0)
        with self._lock:
            speech = speech_ms(bytes(self.pcm))
        if held_ms < min_ms or speech < self.svc.cfg.audio.min_speech_ms:
            self.cancel(pb.CANCEL_REASON_TOO_SHORT)
            self.svc.feedback.sound("cancel")
            res = Result(
                self.id,
                "too_short" if held_ms < min_ms else "no_speech",
                timings={"held_ms": held_ms, "audio_ms": self.audio_ms, "speech_ms": speech},
            )
            with self._lock:  # quiet speech or a silent pause is no_speech; this is the mic
                res.flat_signal = res.status == "no_speech" and flat(bytes(self.pcm))
            self._report("gate", f"{res.status}: {res.timings}")
            if res.status == "no_speech":
                self._record(self._stamp(res))
            return self._stamp(res)
        self._starter.join()  # bounded: health probes + the context deadline
        with self._lock:
            self._done = True
            stream = self.stream
        if stream is None:  # cancelled while the provider was being chosen
            return self._stamp(Result(self.id, "cancelled", timings=self.timings))
        stream.end(reason)
        t_up = time.perf_counter()
        self.timings["held_ms"] = held_ms
        self.timings["speech_ms"] = speech
        self.timings["audio_ms"] = self.audio_ms
        budget_s = self.svc.cfg.server.final_timeout_ms / 1000
        try:
            result = self._finish(t_up, shadow, budget_s)
        except AsrError as e:
            result = self._asr_failed(e, stream, t_up, shadow, reason)
        self._record(self._stamp(result))
        return result

    def _asr_failed(
        self,
        e: AsrError,
        stream: TranscribeStream,
        t_up: float,
        shadow: ShadowBuffer | None,
        reason: pb.EndReason.ValueType,
    ) -> Result:
        """Cancelled, failed over to the next healthy provider, or failed."""
        if self.cancelled:
            return Result(self.id, "cancelled", timings=self.timings)
        if self.provider is not None and stream.final is None and stream.ended is None:
            self.provider.mark_down(str(e))  # no answer at all: the next dictation moves on
            try:
                result = self._failover(t_up, shadow, reason, str(e))
            except AsrError as e2:
                if self.cancelled:
                    return Result(self.id, "cancelled", timings=self.timings)
                e = e2
            else:
                if result is not None:
                    return result
        self._report("asr", f"FAILED: {e}")
        self.svc.feedback.sound("error")
        hint = "; `hark retry` re-runs it" if self.svc.cfg.retry.keep > 0 else ""
        self.svc.feedback.notify(f"Transcription failed: {e}{hint}")
        return Result(self.id, "error", error=f"asr: {e}", timings=self.timings)

    def _failover(
        self, t_up: float, shadow: ShadowBuffer | None, reason: pb.EndReason.ValueType, error: str
    ) -> Result | None:
        """Once: the buffered audio to the next healthy provider, within what is left of the
        key-up budget. None: no other healthy provider, or too little time."""
        left = self.svc.cfg.server.final_timeout_ms / 1000 - (time.perf_counter() - t_up)
        failed = self.provider
        if left < FAILOVER_MIN_S or failed is None:
            return None
        provider, route = self.svc.router.pick()
        if provider is failed or provider.health.state != "ok":
            return None
        left = self.svc.cfg.server.final_timeout_ms / 1000 - (time.perf_counter() - t_up)
        if left < FAILOVER_MIN_S:
            return None
        self._report("failover", f"{failed.name} failed ({error}); {provider.name} gets the audio")
        self.route = [
            *self.route,
            {"name": failed.name, "state": "failed", "detail": error},
            *route,
        ]
        self.start_message = self._session_start(self.snapshot_at_start, provider)
        stream = TranscribeStream(provider.channel)
        with self._lock:
            if self._cancelled:
                stream.cancel()
                return Result(self.id, "cancelled", timings=self.timings)
            self.provider, self.stream, self.failed_over = provider, stream, True
            stream.feed(bytes(self.pcm))
        stream.start(self.start_message)
        stream.end(reason)
        try:
            return self._finish(t_up, shadow, left)
        except AsrError:
            if stream.final is None and stream.ended is None:
                provider.mark_down("failover: no answer")
            raise

    def _stamp(self, res: Result) -> Result:
        """The facts every result carries (docs/protocol/ctl-v1.md `last`)."""
        res.stop_reason = self.stop_reason
        res.mic_interrupted = self.mic_interrupted
        res.input_device = self.input_device
        provider = self.provider
        if provider is not None and res.status not in ("too_short", "no_speech", "cancelled"):
            res.provider = provider.name
            res.fallback = self.failed_over or provider is not self.svc.router.providers[0]
        res.delivery = delivery(res)
        return res

    def _finish(self, t_up: float, shadow: ShadowBuffer | None, timeout_s: float) -> Result:  # noqa: PLR0912, PLR0915 - the pipeline, stage by stage
        svc, cfg = self.svc, self.svc.cfg
        stream, provider = self.stream, self.provider
        if stream is None or provider is None:
            raise AsrError("no stream")  # finish() returns before this
        final = stream.wait_final(timeout_s)
        snap0 = self.snapshot_at_start  # set before SessionStart, hence before any result
        self._report(
            "context",
            f"{self.timings.get('context_ms', float('nan')):.1f} ms, app={snap0.focus.app or snap0.focus.app_id or '?'!r}, "
            f"terms={len(snap0.terms)}, errors={list(snap0.errors)}",
        )
        t = self.timings
        t["keyup_to_final_ms"] = _ms(t_up)
        if stream.t_accepted is not None:
            t["accepted_ms"] = round((stream.t_accepted - self.t0) * 1000, 1)
        if stream.accepted is not None:
            provider.caps.note_etag(stream.accepted.capabilities_etag)
        t["server_end_to_final_ms"] = final.timings.end_to_final_ms
        n_terms = len(self.start_message.context.bias.terms) if self.start_message else 0
        self._report(
            "session",
            f"SessionStart at {t.get('session_start_ms', 0):.1f} ms, bias terms={n_terms}",
        )
        self._report(
            "provider",
            f"{provider.name} ({provider.cfg.address}), route "
            + " -> ".join(f"{r['name']}:{r['state']}" for r in self.route),
        )

        # A context result that arrived after the SessionStart deadline is still
        # useful for canonicalization, profile choice and the focus check.
        snap = self._ctx.result() if self._ctx.done() else snap0
        profile_name = self.profile_override or cfg.profile_for(snap.focus)
        prof = cfg.profile(profile_name)
        res = Result(self.id, "pasted", profile=profile_name, timings=t)

        hyp = final.n_best[0] if final.n_best else pb.Hypothesis()
        res.raw_text = hyp.text
        self._report(
            "asr",
            f"{hyp.text!r} (audio {t['audio_ms']} ms, key-up->final {t['keyup_to_final_ms']:.1f} ms, server {final.timings.end_to_final_ms} ms)",
        )

        t1 = time.perf_counter()
        words = [
            WordInfo(
                w.text, w.start_ms, w.end_ms, w.confidence if w.HasField("confidence") else None
            )
            for w in hyp.words
        ]
        text = hyp.text
        gated = self._gate(final, hyp, res)
        if gated is not None:
            text, words = gated.text, gated.words
            t1 = time.perf_counter()  # gate_ms is its own stage
        try:
            canon, replacements = context.canonicalize(text, words, snap.terms)
        except Exception:  # keep the raw text rather than lose the dictation
            log.exception("canonicalize failed; using the raw text")
            canon, replacements = text, []
        t["canonicalize_ms"] = _ms(t1)
        res.canonical_text = canon
        res.replacements = [asdict(r) for r in replacements]
        self._report(
            "canon", f"{canon!r} ({len(replacements)} replacements, {t['canonicalize_ms']:.1f} ms)"
        )

        if prof.terminal:
            canon, res.symbols = symbols.apply(canon)
            if res.symbols:
                self._report("symbols", f"{canon!r} ({', '.join(res.symbols)})")

        if cfg.cleanup.resolve_corrections:
            canon, found = corrections.resolve(canon)
            res.corrections = [asdict(c) for c in found]
            if found:
                self._report("correct", f"{canon!r} ({len(found)} self-correction(s))")

        res.cleanup = self._cleanup(canon, snap, profile_name, provider)
        t["cleanup_ms"] = round(res.cleanup.ms, 1)
        detail = f" [{res.cleanup.reason}]" if res.cleanup.reason else ""
        self._report(
            "cleanup",
            f"{res.cleanup.status}{detail}: {res.cleanup.text!r} ({t['cleanup_ms']:.1f} ms)",
        )

        t1 = time.perf_counter()
        res.final_text, res.guard_events = guardrails.apply(
            res.cleanup.text, terminal=prof.terminal
        )
        res.uncleaned_text, _ = guardrails.apply(canon, terminal=prof.terminal)
        t["guard_ms"] = _ms(t1)
        self._report(
            "guard", f"{res.final_text!r} profile={profile_name} events={res.guard_events}"
        )

        if not res.final_text:
            res.status = "empty"
            self._report("paste", "nothing to paste")
            svc.feedback.sound("error")
            return res
        if not self._commit():
            res.status = "cancelled"
            self._report("paste", "cancelled before the paste")
            return res

        t1 = time.perf_counter()
        changed_to = self._focus_changed(snap.focus)
        t["focus_check_ms"] = _ms(t1)
        t1 = time.perf_counter()
        text = res.final_text  # what is pasted (smart spacing below)
        if changed_to is not None:
            ok = svc.injector.copy(res.final_text)
            res.status = "focus_changed"
            res.paste = PasteResult(
                ok=False,
                method=svc.injector.name,
                detail="focus changed; text copied" if ok else "focus changed; copy failed",
                in_clipboard=ok,
            )
            svc.feedback.notify(
                "Focus moved to another window; the text is in the clipboard"
                if ok else "Focus changed and the clipboard write failed; see history"
            )  # fmt: skip
        else:
            if cfg.paste.smart_spacing and cfg.paste.join_space:
                text = self._spacing(snap.focus, res, shadow)
                t["spacing_ms"] = _ms(t1)
                t1 = time.perf_counter()
            if shadow is not None:  # before pasting: the listener sees the paste chord
                shadow.pasted(snap.focus.window_id, text)
            res.paste = svc.injector.paste(
                text, terminal=prof.terminal, target=snap.focus.window_id
            )
            res.status = "pasted" if res.paste.ok else "paste_failed"
            res.pasted_text = text if res.paste.ok else ""
            if res.paste.focus_changed:
                res.status = "focus_changed"
                svc.feedback.notify("Focus moved to another window; the text is in the clipboard")
            elif not res.paste.ok:
                svc.feedback.notify(f"Paste failed: {res.paste.detail}")
            if not res.paste.ok and shadow is not None:
                shadow.forget()
        t["paste_ms"] = _ms(t1)
        t["keyup_to_paste_ms"] = _ms(t_up)
        t["release_to_paste_ms"] = _ms(self.t_release)  # what the user waits: key up -> text sent
        self._report(
            "paste",
            f"{res.status} via {res.paste.method}: {res.paste.detail} ({t['paste_ms']:.1f} ms)",
        )
        self._report(
            "total",
            f"release->paste {t['release_to_paste_ms']:.1f} ms"
            f" (key-up->paste {t['keyup_to_paste_ms']:.1f} ms)",
        )
        svc.feedback.sound("done" if res.paste.ok else "error")
        return res

    def _gate(
        self, final: pb.FinalResult, hyp: pb.Hypothesis, res: Result
    ) -> gate.GateResult | None:
        """Biased spans the anchor does not support get the anchor's words back (hark_client.gate).
        None: nothing to gate (off, no bias terms sent, no anchor) or the gate failed."""
        cfg = self.svc.cfg.gate
        terms = self.start_message.context.bias.terms if self.start_message else ()
        anchor = final.anchor if final.HasField("anchor") else None
        if not cfg.enabled or not terms or anchor is None:
            status = "off" if not cfg.enabled else "no_terms" if not terms else "no_anchor"
            res.gate = {"status": status}
            return None
        t1 = time.perf_counter()
        try:
            out = gate.apply(hyp, anchor, final.bias_hits, terms, cfg)
        except Exception as e:  # keep the biased text rather than lose the dictation
            log.exception("anchor gate failed; using the biased text")
            res.gate = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            return None
        self.timings["gate_ms"] = _ms(t1)
        res.gate = {
            "status": "applied",
            "anchor_text": anchor.text,
            "text": out.text,
            "decisions": [asdict(d) for d in out.decisions],
        }
        if out.decisions:
            summary = ", ".join(f"{d.biased!r}:{d.decision}/{d.reason}" for d in out.decisions)
            self._report("anchor", f"{out.text!r} ({summary})")
        return out

    def _spacing(self, focus: FocusInfo, res: Result, shadow: ShadowBuffer | None) -> str:
        """Text to paste: ``res.final_text`` spaced/cased for the caret; records the decision."""
        caret = _caret_context(focus)
        if not caret.trusted:
            before = shadow.before(focus.window_id) if shadow is not None else None
            caret = CaretContext(
                before, None, "shadow" if before is not None else "none", detail=caret.detail
            )
        sp = continuation.decide(caret.before, caret.after, res.final_text)
        res.spacing = {
            "before_repr": repr(caret.before[-16:]) if caret.before is not None else None,
            "after_repr": repr(caret.after) if caret.after is not None else None,
            "source": caret.source,
            "detail": caret.detail,
            "leading": sp.leading,
            "trailing": sp.trailing,
            "lowercased": sp.lowercased,
        }
        self._report(
            "spacing",
            f"{caret.source}: before={res.spacing['before_repr']} after={res.spacing['after_repr']}"
            f" -> {sp.text!r}{f' ({caret.detail})' if caret.detail else ''}",
        )
        return sp.text

    def _cleanup(
        self, text: str, snap: ContextSnapshot, profile_name: str, provider: Provider
    ) -> CleanupOutcome:
        cfg = self.svc.cfg
        prof = cfg.profile(profile_name)
        if not (prof.cleanup and cfg.cleanup.enabled):
            return CleanupOutcome(text, "skipped", "off for this profile")
        if not provider.caps.cleanup_profile(prof.cleanup_profile):
            return CleanupOutcome(
                text, "skipped", f"server has no cleanup profile {prof.cleanup_profile!r}"
            )
        if self.svc.on_stage is not None:
            self.svc.on_stage(self.id, "cleaning")
        return cleanup.run(
            provider.channel,
            text=text,
            # protected spans are offsets into `text`: no shortlist leaves with send_context = false
            terms=snap.terms,
            utterance_id=self.id,
            profile_id=prof.cleanup_profile,
            app_category=profile_name,
            language=cfg.server.language,
            deadline_ms=cfg.cleanup.deadline_ms,
            send_terms=provider.cfg.send_context,
        )

    def _focus_changed(self, before: FocusInfo) -> str | None:
        """None if focus is unchanged or cannot be verified, else "" (another window).

        Uses the cheap ``focus_token()`` (~1 ms) instead of a full context collect.
        """
        if not (self.svc.cfg.paste.check_focus and before.window_id):
            return None
        now = _focus_token()
        if now and now != before.window_id:
            return ""
        return None

    # -- history -------------------------------------------------------------------

    def _record(self, res: Result) -> None:
        cfg = self.svc.cfg
        if not cfg.history.enabled:
            return
        snap = self._ctx.result() if self._ctx.done() else self.snapshot_at_start
        record: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.created_unix)),
            "utterance_id": self.id,
            "status": res.status,
            "error": res.error,
            "profile": res.profile,
            "app": snap.focus.app,
            "app_id": snap.focus.app_id,
            "raw_text": res.raw_text,
            "canonical_text": res.canonical_text,
            "uncleaned_text": res.uncleaned_text,
            "final_text": res.final_text,
            "joined": bool(res.spacing and res.spacing["leading"]),
            "spacing": res.spacing,
            "replacements": res.replacements,
            "gate": res.gate,
            "symbols": res.symbols,
            "corrections": res.corrections,
            "cleanup": asdict(res.cleanup) if res.cleanup else None,
            "guard_events": res.guard_events,
            "paste": asdict(res.paste) if res.paste else None,
            "delivery": res.delivery,
            "stop_reason": res.stop_reason,
            "fallback": res.fallback,
            "mic_interrupted": res.mic_interrupted,
            "input_device": res.input_device,
            "flat_signal": res.flat_signal,
            "timings_ms": res.timings,
            "client_unix_us": {
                "keyup": self.keyup_unix_us,
                **(self.stream.unix_us if self.stream else {}),
            },
            "context": {
                "terms": len(snap.terms),
                "collected_ms": snap.collected_ms,
                "errors": list(snap.errors),
                # Why each term was on the list (no raw context text).
                "provenance": [
                    {
                        "term": t.canonical,
                        "score": round(t.score, 3),
                        "sources": [e.origin for e in t.evidence[:3]],
                    }
                    for t in snap.provenance
                ],
            },
            "bias_terms_sent": len(self.start_message.context.bias.terms)
            if self.start_message
            else 0,
            "provider": self.provider.describe() if self.provider else None,
            "route": self.route,
        }
        final = self.stream.final if self.stream else None
        if final is not None:
            record["server_timings_ms"] = json_format.MessageToDict(
                final.timings, preserving_proto_field_name=True
            )
        base = expand(cfg.history.dir)
        try:
            path = history.append(base, record)
            self._report("history", str(path))
        except OSError as e:
            log.warning("could not write history: %s", e)
