"""One dictation, end to end.

hotkey down -> Utterance():
    context.collect() starts on a thread; the Transcribe call opens immediately.
    SessionStart goes out when the context is ready or the deadline passes (with an
    empty context then); audio fed meanwhile queues behind it.
audio -> Utterance.feed(): 40 ms chunks streamed while the key is held.
hotkey up -> Utterance.finish():
    EndOfAudio -> FinalResult -> canonicalize -> spoken symbols (terminal profile) ->
    optional cleanup (deadline, falls back to the canonical text) -> guardrails ->
    focus re-check -> caret context / shadow buffer -> smart spacing -> paste -> history.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from google.protobuf import json_format

from hark.v1 import hark_pb2 as pb
from hark_client import asr, cleanup, context, continuation, guardrails, history, symbols
from hark_client.asr import SAMPLE_RATE, AsrError, TranscribeStream, uuid7
from hark_client.audio import speech_ms
from hark_client.cleanup import CleanupOutcome
from hark_client.config import expand
from hark_client.context import CaretContext, ContextSnapshot, FocusInfo, WordInfo
from hark_client.inject import PasteResult

if TYPE_CHECKING:
    from collections.abc import Callable

    import grpc

    from hark_client.config import Config
    from hark_client.continuation import ShadowBuffer
    from hark_client.feedback import Feedback
    from hark_client.inject import Injector

log = logging.getLogger("hark")


@dataclass
class Services:
    cfg: Config
    channel: grpc.Channel
    caps: asr.Capabilities
    injector: Injector
    feedback: Feedback
    out: Callable[[str], None] | None = None  # per-stage report (`hark once`)


@dataclass
class Result:
    utterance_id: str
    status: str  # pasted | copied | paste_failed | focus_changed | empty | too_short | no_speech | cancelled | error
    profile: str = ""
    raw_text: str = ""
    canonical_text: str = ""
    final_text: str = ""
    replacements: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    cleanup: CleanupOutcome | None = None
    guard_events: list[str] = field(default_factory=list[str])
    paste: PasteResult | None = None
    error: str = ""
    timings: dict[str, float] = field(default_factory=dict[str, float])
    symbols: list[str] = field(default_factory=list[str])  # spoken-symbol rules applied
    spacing: dict[str, object] | None = None  # smart-spacing decision (see _spacing)


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
    def __init__(self, svc: Services, *, profile: str | None = None, preroll: bytes = b"") -> None:
        self.svc = svc
        self.id = uuid7()
        self.created_unix = time.time()
        self.t0 = time.perf_counter()
        self.profile_override = profile
        self.preroll_ms = len(preroll) // 2 * 1000 // SAMPLE_RATE
        self.pcm = bytearray()
        self.timings: dict[str, float] = {}
        self.start_message: pb.SessionStart | None = None
        self.snapshot_at_start = ContextSnapshot()
        self._ctx: Future[ContextSnapshot] = Future()
        self._lock = threading.Lock()
        self._done = False
        self.keyup_unix_us = 0
        threading.Thread(target=self._run_collect, daemon=True, name="hark-context").start()
        self.stream = TranscribeStream(svc.channel)
        self._starter = threading.Thread(target=self._send_start, daemon=True, name="hark-start")
        self._starter.start()
        if preroll:
            self.feed(preroll)

    # -- hotkey down ---------------------------------------------------------------

    def _run_collect(self) -> None:
        t = time.perf_counter()
        cfg = self.svc.cfg.context
        try:
            snap = _collect(cfg.deadline_ms / 1000, cfg.max_terms)
        except Exception as e:  # the contract says never raises; do not lose the dictation
            log.exception("context.collect failed")
            snap = ContextSnapshot(errors=(f"collect: {type(e).__name__}: {e}",))
        self.timings["context_ms"] = _ms(t)
        self._ctx.set_result(snap)

    def _send_start(self) -> None:
        deadline_s = self.svc.cfg.context.deadline_ms / 1000
        try:
            snap = self._ctx.result(timeout=deadline_s)
        except TimeoutError:
            snap = ContextSnapshot(errors=("collect: missed the SessionStart deadline",))
        self.snapshot_at_start = snap
        self.start_message = self._session_start(snap)
        self.stream.start(self.start_message)
        self.timings["session_start_ms"] = _ms(self.t0)

    def _session_start(self, snap: ContextSnapshot) -> pb.SessionStart:
        cfg = self.svc.cfg
        ctx = pb.RecognitionContext(revision=1)
        bias = asr.bias_list(snap.terms)
        if bias.terms:
            ctx.bias.CopyFrom(bias)
        return pb.SessionStart(
            utterance_id=self.id,
            model_id=cfg.server.model_id,
            languages=[cfg.server.language] if cfg.server.language else [],
            audio_format=pb.AudioFormat(
                encoding=pb.AUDIO_ENCODING_PCM_S16LE,
                sample_rate_hz=SAMPLE_RATE,
                channels=1,
                preroll_ms=self.preroll_ms,
            ),
            context=ctx,
            options=pb.ResultOptions(word_timestamps=True, word_confidence=True),
            deadlines=pb.Deadlines(finalize_budget_ms=cfg.server.finalize_budget_ms),
            client=pb.ClientInfo(client_version=asr.CLIENT_VERSION),
        )

    # -- while speaking ------------------------------------------------------------

    def feed(self, pcm16: bytes) -> None:
        with self._lock:
            if self._done:
                return
            self.pcm += pcm16
            self.stream.feed(pcm16)

    @property
    def audio_ms(self) -> int:
        return len(self.pcm) // 2 * 1000 // SAMPLE_RATE

    def cancel(self, reason: pb.CancelReason.ValueType = pb.CANCEL_REASON_USER_ABORT) -> None:
        with self._lock:
            self._done = True
        self.stream.cancel(reason)

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
        shadow: ShadowBuffer | None = None,
    ) -> Result:
        """keyup_unix_us: when the key was released, if earlier than this call.
        shadow: the daemon's record of its pastes and later keys (spacing fallback)."""
        self.keyup_unix_us = keyup_unix_us or asr.unix_us()
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
            self._report("gate", f"{res.status}: {res.timings}")
            if res.status == "no_speech":
                self._record(res)
            return res
        with self._lock:
            self._done = True
            self.stream.end(reason)
        t_up = time.perf_counter()
        self.timings["held_ms"] = held_ms
        self.timings["speech_ms"] = speech
        self.timings["audio_ms"] = self.audio_ms
        try:
            result = self._finish(t_up, shadow)
        except AsrError as e:
            result = Result(self.id, "error", error=f"asr: {e}", timings=self.timings)
            self._report("asr", f"FAILED: {e}")
            self.svc.feedback.sound("error")
            self.svc.feedback.notify(f"Transcription failed: {e}")
        self._record(result)
        return result

    def _finish(self, t_up: float, shadow: ShadowBuffer | None) -> Result:  # noqa: PLR0915 - the pipeline, stage by stage
        svc, cfg = self.svc, self.svc.cfg
        final = self.stream.wait_final(cfg.server.final_timeout_ms / 1000)
        snap0 = self.snapshot_at_start  # set before SessionStart, hence before any result
        self._report(
            "context",
            f"{self.timings.get('context_ms', float('nan')):.1f} ms, app={snap0.focus.app or snap0.focus.app_id or '?'!r}, "
            f"terms={len(snap0.terms)}, errors={list(snap0.errors)}",
        )
        t = self.timings
        t["keyup_to_final_ms"] = _ms(t_up)
        if self.stream.t_accepted is not None:
            t["accepted_ms"] = round((self.stream.t_accepted - self.t0) * 1000, 1)
        if self.stream.accepted is not None:
            svc.caps.note_etag(self.stream.accepted.capabilities_etag)
        t["server_end_to_final_ms"] = final.timings.end_to_final_ms
        n_terms = len(self.start_message.context.bias.terms) if self.start_message else 0
        self._report(
            "session",
            f"SessionStart at {t.get('session_start_ms', 0):.1f} ms, bias terms={n_terms}",
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
        try:
            canon, replacements = context.canonicalize(hyp.text, words, snap.terms)
        except Exception:  # keep the raw text rather than lose the dictation
            log.exception("canonicalize failed; using the raw text")
            canon, replacements = hyp.text, []
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

        res.cleanup = self._cleanup(canon, snap, profile_name)
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
        t["guard_ms"] = _ms(t1)
        self._report(
            "guard", f"{res.final_text!r} profile={profile_name} events={res.guard_events}"
        )

        if not res.final_text:
            res.status = "empty"
            self._report("paste", "nothing to paste")
            svc.feedback.sound("error")
            return res

        t1 = time.perf_counter()
        changed_to = self._focus_changed(snap.focus)
        t["focus_check_ms"] = _ms(t1)
        t1 = time.perf_counter()
        if changed_to is not None:
            ok = svc.injector.copy(res.final_text)
            res.status = "focus_changed"
            res.paste = PasteResult(
                ok=False,
                method=svc.injector.name,
                detail="focus changed; text copied" if ok else "focus changed; copy failed",
            )
            svc.feedback.notify(
                "Focus moved to another window; the text is in the clipboard"
                if ok else "Focus changed and the clipboard write failed; see history"
            )  # fmt: skip
        else:
            text = res.final_text
            if cfg.paste.smart_spacing and cfg.paste.join_space:
                text = self._spacing(snap.focus, res, shadow)
                t["spacing_ms"] = _ms(t1)
                t1 = time.perf_counter()
            if shadow is not None:  # before pasting: the listener sees the paste chord
                shadow.pasted(snap.focus.window_id, text)
            res.paste = svc.injector.paste(text, terminal=prof.terminal)
            res.status = "pasted" if res.paste.ok else "paste_failed"
            if not res.paste.ok:
                svc.feedback.notify(f"Paste failed: {res.paste.detail}")
                if shadow is not None:
                    shadow.forget()
        t["paste_ms"] = _ms(t1)
        t["keyup_to_paste_ms"] = _ms(t_up)
        self._report(
            "paste",
            f"{res.status} via {res.paste.method}: {res.paste.detail} ({t['paste_ms']:.1f} ms)",
        )
        self._report("total", f"key-up->paste {t['keyup_to_paste_ms']:.1f} ms")
        svc.feedback.sound("done" if res.paste.ok else "error")
        return res

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

    def _cleanup(self, text: str, snap: ContextSnapshot, profile_name: str) -> CleanupOutcome:
        cfg = self.svc.cfg
        prof = cfg.profile(profile_name)
        if not (prof.cleanup and cfg.cleanup.enabled):
            return CleanupOutcome(text, "skipped", "off for this profile")
        if not self.svc.caps.cleanup_profile(prof.cleanup_profile):
            return CleanupOutcome(
                text, "skipped", f"server has no cleanup profile {prof.cleanup_profile!r}"
            )
        return cleanup.run(
            self.svc.channel,
            text=text,
            terms=snap.terms,
            utterance_id=self.id,
            profile_id=prof.cleanup_profile,
            app_category=profile_name,
            language=cfg.server.language,
            deadline_ms=cfg.cleanup.deadline_ms,
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
            "final_text": res.final_text,
            "joined": bool(res.spacing and res.spacing["leading"]),
            "spacing": res.spacing,
            "replacements": res.replacements,
            "symbols": res.symbols,
            "cleanup": asdict(res.cleanup) if res.cleanup else None,
            "guard_events": res.guard_events,
            "paste": asdict(res.paste) if res.paste else None,
            "timings_ms": res.timings,
            "client_unix_us": {"keyup": self.keyup_unix_us, **self.stream.unix_us},
            "context": {
                "terms": len(snap.terms),
                "collected_ms": snap.collected_ms,
                "errors": list(snap.errors),
            },
            "bias_terms_sent": len(self.start_message.context.bias.terms)
            if self.start_message
            else 0,
        }
        final = self.stream.final
        if final is not None:
            record["server_timings_ms"] = json_format.MessageToDict(
                final.timings, preserving_proto_field_name=True
            )
        base = expand(cfg.history.dir)
        try:
            path = history.append(base, record)
            self._report("history", str(path))
            if cfg.history.save_recordings:
                full = dict(record)
                if self.start_message is not None:
                    full["session_start"] = json_format.MessageToDict(
                        self.start_message, preserving_proto_field_name=True
                    )
                if final is not None:
                    full["final_result"] = json_format.MessageToDict(
                        final, preserving_proto_field_name=True
                    )
                rec = history.save_recording(base, self.id, bytes(self.pcm), snap, full)
                self._report("recording", str(rec))
        except OSError as e:
            log.warning("could not write history: %s", e)
