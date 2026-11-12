"""`hark run`: mic + hotkey/ctl socket -> Utterance pipeline, until SIGINT/SIGTERM.

Threads: the main thread runs the recording indicator's UI loop (AppKit / GTK; they want the
main thread) or just waits for a signal; the mic callback, the hotkey listener, the ctl socket
and each dictation's finish run on their own threads and drive the indicator from there.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from hark.v1 import hark_pb2 as pb
from hark_client import config, context, guardrails, history, indicator, retry
from hark_client.asr import unix_us
from hark_client.audio import PreRoll
from hark_client.config import ConfigError, expand
from hark_client.continuation import ShadowBuffer, backspaces
from hark_client.pipeline import Utterance

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path
    from types import FrameType

    from hark_client.context import CaretContext, FocusInfo
    from hark_client.continuation import KeyPress
    from hark_client.hotkey import PushToTalk
    from hark_client.indicator import Indicator
    from hark_client.mic import Mic
    from hark_client.pipeline import Result, Services

log = logging.getLogger("hark")


def _focus_token() -> str:
    # Looked up at call time so tests can substitute the context engine.
    return context.focus_token()


def _caret_context(focus: FocusInfo) -> CaretContext:
    # Looked up at call time so tests can substitute the context engine.
    return context.caret_context(focus)


CARET_CHECK_CHARS = 64  # context.caret reads at most this much before the caret
MIC_SILENCE_S = 1.0  # a running stream that has not called back this long has stopped
MIC_RETRY_S = 5.0  # a lost mic is reopened at most this often (no device: PortAudio re-init)
TICK_S = 1.0  # mic watchdog, config and server checks (status changes for `subscribe`)
# Applied by `reload` for the next dictation; every other table needs a restart.
RELOADABLE = (
    "words", "context", "gate", "cleanup", "feedback", "profiles", "profile_switch", "retry",
)  # fmt: skip
PASTE_RESTART_KEYS = ("method", "restore_clipboard", "restore_delay_ms", "join_window_ms")


def _label(mode: str, utt: Utterance) -> str:
    """The indicator's recording label: the mode and a profile picked by hand."""
    return f"{mode} · {utt.profile_override}" if utt.profile_override else mode


@dataclass(frozen=True, slots=True)
class Insertion:
    """hark's newest paste, for `hark ctl undo` / `swap-raw`."""

    text: str  # exactly what was pasted
    swap: str  # the other version (uncleaned <-> cleaned); "" = none
    terminal: bool


class Controller:
    """State machine shared by the hotkey listener and the ctl socket (any thread)."""

    def __init__(self, svc: Services, mic: Mic | None) -> None:
        self.svc = svc
        self.mic = mic
        cfg = svc.cfg.audio
        self.keep_open = cfg.keep_open
        self.preroll = PreRoll(cfg.preroll_ms if cfg.keep_open else 0)
        self._lock = threading.Lock()
        self.current: Utterance | None = None
        self.busy = 0  # utterances still finishing
        self._max_hit = False
        # hark's pastes + the keys typed since: the caret when the app does not expose it
        self.shadow = ShadowBuffer(svc.cfg.paste.join_window_ms / 1000, _focus_token)
        self.last_result: tuple[float, Result] | None = None  # (unix time, result)
        self.last_text = ""  # newest pasted/copied text (`hark ctl paste-last`)
        self.indicator: Indicator | None = None
        # told (under the lock, so in order) when a recording starts or ends: the hotkey listener
        self.on_recording: Callable[[bool], None] | None = None
        # the hotkey listener reports every key and click (shadow buffer): undo can trust it
        self.keys_observed = False
        self.insertion: Insertion | None = None  # the newest dictation's paste, if any
        # `hark ctl profile` and the menu bar's Profile menu; None = by app
        self.profile_override: str | None = None
        self._undo_lock = threading.Lock()
        self.paused = False  # menu bar: hotkey/ctl start refused, mic released
        self.started = 0  # dictations started (onboarding's hotkey test watches it)
        self.finishing: Utterance | None = None  # stopped, not yet pasted (one at a time)
        # told when a dictation starts/stops finishing: the hotkey listener (Esc cancels it)
        self.on_finishing: Callable[[bool], None] | None = None
        self.mic_status = "off"  # ok | lost | off
        self._mic_retry_at = 0.0  # time.monotonic() of the next reopen attempt
        self.config_path: Path | None = None  # what `reload` re-reads (None: not reloadable)
        self._config_mtime: float | None = None
        self.restart_needed: list[str] = []
        self._server_seen: dict[str, object] = {}
        self._changed = threading.Condition()
        self._version = 0  # bumped on every status change (`subscribe`)

    def on_pcm(self, pcm: bytes) -> None:
        """Mic callback thread."""
        with self._lock:
            utt = self.current
            if utt is None:
                self.preroll.push(pcm)
                return
            utt.feed(pcm)
            over = utt.audio_ms - utt.preroll_ms >= self.svc.cfg.audio.max_ms and not self._max_hit
            if over:
                self._max_hit = True
        if self.indicator is not None:
            self.indicator.level(pcm)
        if over:
            log.info("max duration reached; stopping")
            threading.Thread(
                target=lambda: self.stop(reason="limit", only=utt), daemon=True
            ).start()

    # -- status changes (`subscribe`) ------------------------------------------------

    def touch(self) -> None:
        """Something `status` reports has changed."""
        with self._changed:
            self._version += 1
            self._changed.notify_all()

    def wait_change(self, seen: int, timeout_s: float) -> int:
        """The status version, once it differs from ``seen`` (or after ``timeout_s``)."""
        with self._changed:
            self._changed.wait_for(lambda: self._version != seen, timeout_s)
            return self._version

    # -- dictation ---------------------------------------------------------------------

    def _refuse(self, reply: str) -> str:
        if self.on_recording is not None:
            # nothing records: the hotkey must not wait for a release, a tap or an Esc
            self.on_recording(False)  # noqa: FBT003 - Callable[[bool], None]
        return reply

    def start(self) -> str:
        with self._lock:
            if self.paused:
                return self._refuse("error paused")
            if self.current is not None:
                return "ok already recording"
            if self.finishing is not None:
                # one at a time: a second dictation could paste out of order or into a prompt
                # edited meanwhile; the indicator already shows the first one working
                return self._refuse("error busy")
        error = self._mic_ready()
        if error:
            with self._lock:
                return self._refuse(f"error {error}")
        with self._lock:
            if self.current is not None or self.finishing is not None:
                return self._refuse("error busy")
            cue = self._start_cue()
            mask_ms = self.svc.cfg.feedback.cue_mask_ms if cue else 0
            profile = self.profile_override
            if not self.svc.cfg.profile_switch.sticky:
                self.profile_override = None  # used up by this dictation
            self.current = utt = Utterance(
                self.svc, profile=profile, preroll=self.preroll.take(), mask_ms=mask_ms
            )
            self._max_hit = False
            self.insertion = None  # undo is for the newest dictation only
            if self.on_recording is not None:
                self.on_recording(True)  # noqa: FBT003 - Callable[[bool], None]
            self.started += 1
        if self.indicator is not None:
            self.indicator.recording(utt.id, _label("Listening", utt))
        if self.svc.learner is not None:
            self.svc.learner.cancel()  # the next paste changes the field: stop watching the last
        if cue:
            self.svc.feedback.sound("start")
        log.info("recording...")
        self.touch()
        return "ok recording"

    def _mic_ready(self) -> str:
        """Start (or, if it stopped delivering audio, reopen) the mic before announcing a
        recording; "" or why not."""
        mic = self.mic
        if mic is None:
            return ""
        try:
            if not self.keep_open:
                mic.start()
            elif not mic.healthy(MIC_SILENCE_S):
                log.warning("mic stopped delivering audio; reopening")
                mic.reopen()
        except Exception as e:  # noqa: BLE001 - PortAudioError (sounddevice not imported here)
            log.warning("mic: %s", e)
            self._set_mic("lost")
            return f"mic unavailable: {e}"
        self._set_mic("ok")
        return ""

    def open_mic(self) -> None:
        """Daemon start: an always-open mic starts now (on failure the watchdog keeps trying)."""
        if self.mic is None or not self.keep_open:
            return
        try:
            self.mic.start()
        except Exception as e:  # noqa: BLE001 - PortAudioError (sounddevice not imported here)
            log.warning("mic: %s", e)
            self._set_mic("lost")
        else:
            self._set_mic("ok")

    def _set_mic(self, status: str) -> None:
        if status == self.mic_status:
            return
        self.mic_status = status
        if status == "lost":
            if self.indicator is not None:
                self.indicator.notice("Mic lost")
            self.svc.feedback.notify("Microphone lost; hark keeps trying to reopen it")
        self.touch()

    def check_mic(self) -> None:
        """Watchdog (every TICK_S): a stream that should run but stopped calling back is lost:
        flag a recording in progress (words are missing) and reopen it."""
        mic = self.mic
        if mic is None:
            return
        with self._lock:
            utt = self.current
            paused = self.paused
        if paused or not (self.keep_open or utt is not None):
            self._set_mic("off")
            return
        if mic.healthy(MIC_SILENCE_S):
            self._mic_retry_at = 0.0
            self._set_mic("ok")
            return
        if utt is not None:
            utt.mic_interrupted = True
        self._set_mic("lost")
        now = time.monotonic()
        if now < self._mic_retry_at:
            return
        self._mic_retry_at = now + MIC_RETRY_S
        try:
            mic.reopen()
        except Exception as e:  # noqa: BLE001 - PortAudioError: still no device
            log.warning("mic reopen failed: %s", e)
        else:
            log.info("mic reopened: %s @ %d Hz", mic.device_name, mic.rate)

    def _start_cue(self) -> bool:
        """Play the start sound? (``auto``: not when the indicator already shows it.)"""
        cfg = self.svc.cfg.feedback
        mode = cfg.start_cue
        return cfg.sounds and (mode == "always" or (mode == "auto" and self.indicator is None))

    def _detach(self, *, finishing: bool, only: Utterance | None = None) -> Utterance | None:
        """End the recording (``only``: if it is that one); ``finishing``: it goes on to be
        transcribed and pasted, as the one dictation in flight (atomically, so a start in
        between is refused)."""
        if self.mic is not None and not self.keep_open:
            self.mic.stop()  # outside the lock: waits for the last callback
        with self._lock:
            if only is not None and self.current is not only:
                return None  # a later recording: not this stop's business
            utt, self.current = self.current, None
            if utt is not None and finishing:
                self.finishing = utt
                self.busy += 1
            if utt is not None and self.on_recording is not None:
                self.on_recording(False)  # noqa: FBT003
            if utt is not None and finishing and self.on_finishing is not None:
                self.on_finishing(True)  # noqa: FBT003
        if utt is not None:
            self.touch()
        return utt

    def hands_free(self) -> None:
        """The hotkey was tapped: the recording goes on until the next tap."""
        with self._lock:
            utt = self.current
        if utt is None:
            return
        log.info("hands-free: tap the hotkey again to stop")
        if self.indicator is not None:
            self.indicator.recording(utt.id, _label("Hands-free", utt))

    def stop(self, reason: str = "key_up", only: Utterance | None = None) -> str:
        """``reason``: key_up (release, tap, `stop`) or limit (`audio.max_ms`, for ``only``)."""
        keyup = unix_us()
        utt = self._detach(finishing=True, only=only)
        if utt is None:
            return "ok idle"
        self.svc.feedback.sound("stop")
        if self.indicator is not None:
            self.indicator.stage(utt.id, "transcribing")
        if reason == "limit":
            self.svc.feedback.notify("Stopped at the length limit (audio.max_ms)")
        end = pb.END_REASON_MAX_DURATION if reason == "limit" else pb.END_REASON_KEY_UP

        def work() -> None:
            status = "error"
            try:
                utt.wait_started()  # bounded; the key-down context goes with the audio
                kept = self._keep(utt, None)  # before transcribing: a crash keeps the audio
                res = utt.finish(
                    min_ms=self.svc.cfg.audio.min_ms, keyup_unix_us=keyup, shadow=self.shadow,
                    reason=end,
                )  # fmt: skip
                status = res.status
                self.last_result = (time.time(), res)
                if res.final_text and res.status != "empty":
                    self.last_text = res.final_text
                if res.pasted_text and res.paste_verdict != "missing":
                    swap = res.swap_text if res.swap_text != res.pasted_text else ""
                    terminal = self.svc.cfg.profile(res.profile).terminal
                    self.insertion = Insertion(res.pasted_text, swap, terminal)
                self._kept(kept, utt, res)
                timing = res.timings.get("keyup_to_paste_ms")
                log.info(
                    "%s: %r%s", res.status, res.final_text,
                    f" ({timing:.0f} ms key-up->paste)" if timing is not None else "",
                )  # fmt: skip
            finally:
                if self.indicator is not None:
                    self.indicator.finished(utt.id, status)
                with self._lock:
                    self.busy -= 1
                    self.finishing = None
                    if self.on_finishing is not None:
                        self.on_finishing(False)  # noqa: FBT003
                self.touch()

        threading.Thread(target=work, daemon=True, name="hark-finish").start()
        return "ok stopped"

    def toggle(self) -> str:
        with self._lock:
            recording = self.current is not None
        return self.stop() if recording else self.start()

    def cancel(self) -> str:
        """The recording, else the dictation still finishing, unless it is being pasted."""
        utt = self._detach(finishing=False)
        if utt is None:
            with self._lock:
                utt = self.finishing
            if utt is None:
                return "ok idle"
            if not utt.cancel(pb.CANCEL_REASON_USER_ABORT):
                return "error too late: already pasting"
            self.svc.feedback.sound("error")
            log.info("cancelled while finishing")
            return "ok cancelled"  # its finish thread reports the outcome
        utt.cancel(pb.CANCEL_REASON_USER_ABORT)
        if self.indicator is not None:
            self.indicator.finished(utt.id, "cancelled")
        self.svc.feedback.sound("error")
        log.info("cancelled")
        return "ok cancelled"

    def set_paused(self, paused: bool) -> None:  # noqa: FBT001 - a menu toggle
        """Pause: a recording in progress is cancelled and an always-open mic is released."""
        with self._lock:
            if paused == self.paused:
                return
            self.paused = paused
        if paused:
            self.cancel()
        if self.mic is not None and self.keep_open:
            if paused:
                self.mic.stop()
            else:
                self.mic.start()
        self._set_mic("off" if paused or not self.keep_open else "ok")
        self.touch()
        log.info("dictation %s", "paused" if paused else "resumed")

    def _meta(self, utt: Utterance, res: Result | None) -> dict[str, object]:
        focus = utt.snapshot_at_start.focus
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(utt.created_unix)),
            "utterance_id": utt.id,
            "status": res.status if res is not None else "finishing",
            "error": res.error if res is not None else "",
            "profile": res.profile if res is not None else "",
            "app": focus.app,
            "app_id": focus.app_id,
            "audio_ms": utt.audio_ms,
        }

    def _keep(self, utt: Utterance, res: Result | None) -> Path | None:
        """Recovery ring for `hark retry`; None if not kept."""
        base = expand(self.svc.cfg.history.dir)
        try:
            return retry.save(
                base, self.svc.cfg.retry.keep, bytes(utt.pcm), utt.snapshot_at_start,
                self._meta(utt, res),
            )  # fmt: skip
        except OSError as e:
            log.warning("could not keep the recording for `hark retry`: %s", e)
            return None

    def _kept(self, kept: Path | None, utt: Utterance, res: Result) -> None:
        """The outcome into the entry kept before transcribing (presses under min_ms: dropped)."""
        if kept is None:
            return
        try:
            if res.status == "too_short":
                retry.drop(kept)
            else:
                retry.update_meta(kept, self._meta(utt, res))
        except OSError as e:
            log.warning("could not update the kept recording: %s", e)

    def paste_last(self) -> str:
        """Re-insert the newest dictated text into the focused window (guardrails re-applied
        for it: a prose result pasted into a terminal loses its newlines)."""
        text = self.last_text
        if not text and self.svc.cfg.history.enabled:
            text = history.last_text(expand(self.svc.cfg.history.dir))
        if not text:
            return "error nothing to paste yet"
        cfg = self.svc.cfg
        focus = context.collect(cfg.context.deadline_ms / 1000, 0).focus
        terminal = cfg.profile(cfg.profile_for(focus)).terminal
        text, _ = guardrails.apply(text, terminal=terminal)
        self.shadow.pasted(focus.window_id, text)
        res = self.svc.injector.paste(text, terminal=terminal, target=focus.window_id)
        if not res.ok:
            self.shadow.forget()
            return f"error paste failed: {res.detail}"
        self.insertion = Insertion(text, "", terminal)
        return f"ok pasted {len(text)} chars"

    def undo(self) -> str:
        """Delete hark's newest paste, only if the caret is known to be right after it."""
        return self._retract(swap=False)

    def swap_raw(self) -> str:
        """Replace hark's newest paste with its uncleaned text (again: back), same condition."""
        return self._retract(swap=True)

    def _retract(self, *, swap: bool) -> str:
        if not self._undo_lock.acquire(blocking=False):
            return "error an undo is already running"
        try:
            reply = self._retract_locked(swap=swap)
        finally:
            self._undo_lock.release()
        if reply.startswith("error "):
            self.svc.feedback.notify(f"Nothing {'swapped' if swap else 'undone'}: {reply[6:]}")
        return reply

    def _refusal(self, ins: Insertion | None, *, swap: bool) -> str:
        """Why undo/swap must not run now, from the daemon's state alone ("" = go on)."""
        with self._lock:
            busy = self.current is not None or self.busy > 0
        if busy:
            return "a dictation is in progress"
        if ins is None:
            return "no dictation to undo (only the newest paste can be undone)"
        if swap and not ins.swap:
            return "no other version of the text (cleanup changed nothing)"
        if not self.keys_observed:
            # without the key stream, typing or a selection since the paste would go unseen
            return "needs the hotkey listener's key events (macOS/X11), not available"
        if backspaces(ins.text) is None:
            return "the text has combined characters; the Backspace count would be a guess"
        return ""

    def _retract_locked(self, *, swap: bool) -> str:  # noqa: PLR0911 - one reply per refusal
        ins = self.insertion
        refusal = self._refusal(ins, swap=swap)
        if refusal or ins is None:
            return f"error {refusal}"
        cfg = self.svc.cfg
        focus = context.collect(cfg.context.deadline_ms / 1000, 0).focus
        target = focus.window_id
        if self.shadow.last_insertion(target) != ins.text:
            return "error the caret may have moved: keys, clicks or focus changes since the paste"
        caret = _caret_context(focus)
        tail = ins.text[-CARET_CHECK_CHARS:]
        if caret.trusted and not (caret.before or "").endswith(tail):
            return "error the text before the caret is not hark's last paste"
        n = len(ins.text)
        if not self.shadow.retract(target, ins.text):
            return "error the caret may have moved"
        self.insertion = None
        res = self.svc.injector.backspace(n)
        if not res.ok:
            self.shadow.forget()
            return f"error {res.detail}"
        log.info("undo: removed %d chars", n)
        if not swap:
            return f"ok removed {n} chars"
        self.shadow.pasted(target, ins.swap)
        res = self.svc.injector.paste(ins.swap, terminal=ins.terminal, target=target)
        if not res.ok:
            self.shadow.forget()
            return f"error removed the text, but the paste failed: {res.detail}"
        self.insertion = Insertion(ins.swap, ins.text, ins.terminal)
        return f"ok swapped {n} for {len(ins.swap)} chars"

    def profile(self, args: Sequence[str]) -> str:
        """``[]`` (show) | ``["next"]`` (auto -> each profile in table order -> auto) | ``["auto"]``
        | ``["set", NAME]``: the profile of the next dictation (``profile_switch.sticky``: of
        every one until ``auto``)."""
        names = list(self.svc.cfg.profiles)
        with self._lock:
            current = self.profile_override
            match list(args):
                case []:
                    new = current
                case ["auto"]:
                    new = None
                case ["next"]:
                    order: list[str | None] = [None, *names]
                    new = order[(order.index(current) + 1) % len(order)]
                case ["set", name] if name in names:
                    new = name
                case ["set", name]:
                    return f"error unknown profile {name!r} (have: {', '.join(names)})"
                case _:
                    return "error usage: profile [next | auto | set NAME]"
            self.profile_override = new
        self.touch()
        label = new or "auto"
        if args and self.indicator is not None:
            self.indicator.notice(f"Profile: {label}")
        if args:
            log.info("profile override: %s", label)
        scope = "" if new is None or self.svc.cfg.profile_switch.sticky else " (next dictation)"
        return f"ok {label}{scope}"

    def user_input(self, press: KeyPress) -> None:
        """A key press or click from the hotkey listener: keeps the shadow buffer current and
        feeds the learner's terminal key replay (one listener for both)."""
        self.shadow.key(press)
        if self.svc.learner is not None:
            self.svc.learner.key(press)

    def status(self) -> str:
        with self._lock:
            state = "recording" if self.current is not None else "idle"
            return f"ok {state} finishing={self.busy}"

    def server_status(self) -> dict[str, object]:
        """The provider the next dictation would use, from cached health (never probes)."""
        providers = self.svc.router.providers
        for i, p in enumerate(providers):
            h = p.health
            if h.state == "ok":
                return {"name": p.name, "state": "ok", "rtt_ms": h.rtt_ms, "fallback": i > 0}
        first = providers[0].health
        state = "unknown" if all(p.health.state == "unknown" for p in providers) else "down"
        if first.state == "slow":
            state = "slow"
        return {"name": providers[0].name, "state": state, "rtt_ms": None, "fallback": False}

    def config_changed(self) -> bool:
        path, seen = self.config_path, self._config_mtime
        if path is None or seen is None:
            return False
        try:
            return path.stat().st_mtime != seen
        except OSError:
            return False

    def watch_config(self, path: Path | None) -> None:
        """`reload` re-reads ``path``; `status` reports when it changes."""
        self.config_path = path
        try:
            self._config_mtime = path.stat().st_mtime if path is not None else None
        except OSError:
            self._config_mtime = None

    def tick(self) -> None:
        """Every TICK_S: mic watchdog; config and server changes reach `subscribe`."""
        self.check_mic()
        seen: dict[str, object] = {"server": self.server_status(), "config": self.config_changed()}
        if seen != self._server_seen:
            self._server_seen = seen
            self.touch()

    def reload(self) -> str:
        """Re-read the config: per-dictation tables now, the rest named for a restart."""
        if self.config_path is None:
            return "error no config file to reload"
        try:
            new = config.load(self.config_path)
        except ConfigError as e:
            return f"error {e}"
        cfg = self.svc.cfg
        restart = [
            f.name for f in dataclasses.fields(cfg)
            if f.name not in (*RELOADABLE, "paste") and getattr(cfg, f.name) != getattr(new, f.name)
        ]  # fmt: skip
        restart += [
            f"paste.{k}" for k in PASTE_RESTART_KEYS if getattr(cfg.paste, k) != getattr(new.paste, k)
        ]  # fmt: skip
        for name in RELOADABLE:
            current, value = cast("object", getattr(cfg, name)), cast("object", getattr(new, name))
            if dataclasses.is_dataclass(current) and not isinstance(current, type):
                for f in dataclasses.fields(current):  # in place: services hold these objects
                    setattr(current, f.name, getattr(value, f.name))
            else:
                setattr(cfg, name, value)
        for f in dataclasses.fields(cfg.paste):
            if f.name not in PASTE_RESTART_KEYS:
                setattr(cfg.paste, f.name, getattr(new.paste, f.name))
        self.restart_needed = restart
        self.watch_config(self.config_path)
        self.touch()
        log.info("config reloaded%s", f"; restart for {', '.join(restart)}" if restart else "")
        return f"ok reloaded; restart for {', '.join(restart)}" if restart else "ok reloaded"

    def status_json(self) -> dict[str, object]:
        """JSON v1 ``status`` fields (docs/protocol/ctl-v1.md). Never includes dictated text."""
        with self._lock:
            state = "recording" if self.current is not None else "idle"
            phase = state if self.current is not None or self.finishing is None else "finishing"
            busy = self.busy
            last = self.last_result
            paused = self.paused
        learner = self.svc.learner
        return {
            "state": state,
            "phase": phase,
            "finishing": busy,
            "paused": paused,
            "mic": {"status": self.mic_status},
            "server": self.server_status(),
            "config": {"changed": self.config_changed(), "restart_needed": self.restart_needed},
            "last": None if last is None else _last_json(*last, self._retry_available(last[1])),
            "learning": {
                "enabled": learner is not None,
                "watching": learner is not None and learner.watching,
            },
            "profile_override": self.profile_override,
        }

    def _retry_available(self, res: Result) -> bool:
        if self.svc.cfg.retry.keep <= 0 or res.status == "too_short":
            return False
        return (expand(self.svc.cfg.history.dir) / retry.RING / res.utterance_id).is_dir()

    def handle(self, command: str) -> str:
        verb, *args = command.split() or [""]
        if verb == "profile":
            return self.profile(args)
        if args:
            return f"error {verb} takes no arguments"
        actions: dict[str, Callable[[], str]] = {
            "start": self.start,
            "stop": self.stop,
            "toggle": self.toggle,
            "cancel": self.cancel,
            "status": self.status,
            "ping": lambda: "ok pong",
            "paste-last": self.paste_last,
            "undo": self.undo,
            "swap-raw": self.swap_raw,
            "reload": self.reload,
        }
        action = actions.get(verb)
        return action() if action else f"error unknown command {command!r}"


def _last_json(at: float, res: Result, retry_available: bool) -> dict[str, object]:  # noqa: FBT001
    return {
        "at": round(at, 3),
        "utterance_id": res.utterance_id,
        "status": res.status,
        "profile": res.profile,
        "paste_verdict": res.paste_verdict,
        "keyup_to_paste_ms": res.timings.get("keyup_to_paste_ms"),
        "stop_reason": res.stop_reason or None,
        "provider": res.provider or None,
        "fallback": res.fallback,
        "cleanup": res.cleanup.status if res.cleanup is not None else None,
        "delivery": res.delivery,
        "retry_available": retry_available,
        "mic_interrupted": res.mic_interrupted,
    }


class StartupError(RuntimeError):
    pass


class AppUi(Protocol):
    """Main-thread UI beside the indicator (Hark.app's menu-bar item)."""

    def tick(self) -> None:
        """Called from the UI loop at least every 0.5 s."""
        ...

    def run(self, stop: threading.Event) -> None:
        """Run the UI loop itself until ``stop`` (when there is no indicator to run it)."""
        ...


def _make_hotkey(svc: Services, controller: Controller) -> PushToTalk | None:
    cfg = svc.cfg.hotkey
    listener = cfg.listener
    if listener == "auto":
        listener = "pynput" if sys.platform == "darwin" or _x11_only() else "none"
    if listener != "pynput":
        log.info("no global hotkey; bind `hark ctl start` / `hark ctl stop` in your compositor")
        return None
    try:
        from hark_client.hotkey import PushToTalk  # noqa: PLC0415
    except ImportError as e:
        raise StartupError("hotkey.listener = pynput needs pynput (Linux: hark-client[x11])") from e
    swallow_esc = cfg.swallow_esc
    if sys.platform == "darwin":
        from hark_client import macos  # noqa: PLC0415

        if not macos.can_listen_events():
            log.warning(
                "no Input Monitoring permission: the hotkey will not fire (see `hark doctor`)"
            )
        if swallow_esc and not macos.can_post_events():
            # a filtering event tap needs Accessibility; without it the tap is not created at all
            log.warning("no Accessibility permission: Esc will also reach the focused app")
            swallow_esc = False
    actions = (
        (cfg.undo_key, controller.undo),
        (cfg.swap_key, controller.swap_raw),
        (cfg.profile_key, lambda: controller.profile(["next"])),
    )
    return PushToTalk(
        cfg.key, controller.start, controller.stop, controller.cancel, controller.user_input,
        on_tap=controller.hands_free, tap_s=cfg.tap_ms / 1000, swallow_esc=swallow_esc,
        keys={key: action for key, action in actions if key},
    )  # fmt: skip


def _make_mic(svc: Services, controller: Controller) -> Mic:
    try:
        from hark_client.mic import Mic  # noqa: PLC0415 - loads PortAudio

        mic = Mic(controller.on_pcm, svc.cfg.audio.device)
    except Exception as e:  # PortAudioError / OSError (no PortAudio library)
        raise StartupError(
            f"cannot open the microphone ({type(e).__name__}: {e}); see `hark doctor`"
        ) from e
    log.info("mic: %s @ %d Hz -> 16 kHz", mic.device_name, mic.rate)
    return mic


def _log_providers(svc: Services) -> None:
    svc.router.refresh()
    for p in svc.router.providers:
        caps, h = p.caps.get(), p.health
        if caps is None:
            log.warning("provider %s (%s): %s %s", p.name, p.cfg.address, h.state, h.detail)
            continue
        models = ", ".join(f"{m.model_id}({pb.ModelState.Name(m.state)})" for m in caps.models)
        log.info(
            "provider %s (%s): %s, %s; features: %s",
            p.name, p.cfg.address, h.state, models, ", ".join(caps.features),
        )  # fmt: skip


def _main_loop(ui: Indicator | None, extra: AppUi | None, stop: threading.Event) -> None:
    """This (main) thread until ``stop``: the toolkit's loop, if there is a UI."""
    if ui is not None:
        ui.run(stop, extra.tick if extra is not None else None)
    elif extra is not None:
        extra.run(stop)
    else:
        while not stop.wait(0.5):
            pass


def run(
    svc: Services,
    app_ui: Callable[[Controller, threading.Event], AppUi] | None = None,
    config_path: Path | None = None,
) -> int:
    """``app_ui(controller, stop)`` builds a main-thread UI (``hark app``); setting ``stop``
    quits. ``config_path``: the file `reload` re-reads."""
    from hark_client import ctl  # noqa: PLC0415 - keep `hark ctl` imports light
    from hark_client.service import linux as systemd  # noqa: PLC0415 - no-ops outside systemd

    _log_providers(svc)
    svc.router.start_background()

    controller = Controller(svc, None)
    controller.watch_config(config_path)
    try:
        hotkey = _make_hotkey(svc, controller)
        mic = _make_mic(svc, controller)
    except StartupError as e:
        log.error("%s", e)
        return 1
    controller.mic = mic
    # Warm per-window term pools in the background so key-down only refreshes cheap sources.
    context.start_prefetch(str(expand(svc.cfg.history.dir)))
    ui = indicator.make(svc.cfg.indicator, retry_hint=svc.cfg.retry.keep > 0)
    if ui is not None:
        controller.indicator = ui
        svc.on_stage = ui.stage
    server = ctl.CtlServer(
        ctl.socket_path(), controller.handle, controller.status_json, controller.wait_change
    )
    controller.open_mic()
    server.start()
    systemd.notify("READY=1")  # Type=notify unit: ready once `hark ctl` gets answers
    if hotkey is not None:
        controller.on_recording = hotkey.recording
        controller.on_finishing = hotkey.finishing
        controller.keys_observed = True
        hotkey.start()

    stop = threading.Event()
    systemd.start_watchdog(lambda: ctl.send("ping", timeout=5.0).startswith("ok"), stop)

    _start_ticks(controller, stop)

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    log.info("hark ready (paste: %s)", svc.injector.name)
    try:
        _main_loop(ui, app_ui(controller, stop) if app_ui is not None else None, stop)
    finally:
        systemd.notify("STOPPING=1")
        if hotkey is not None:
            hotkey.stop()
        context.stop_prefetch()
        controller.cancel()
        server.close()
        mic.stop()
        mic.close()
    return 0


def _start_ticks(controller: Controller, stop: threading.Event) -> None:
    def ticks() -> None:
        while not stop.wait(TICK_S):
            try:
                controller.tick()
            except Exception:  # the watchdog must outlive one bad check
                log.exception("status tick failed")

    threading.Thread(target=ticks, daemon=True, name="hark-tick").start()


def _x11_only() -> bool:
    return bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY")
