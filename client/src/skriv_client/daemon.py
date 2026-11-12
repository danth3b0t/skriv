"""`skriv run`: mic + hotkey/ctl socket -> Utterance pipeline, until SIGINT/SIGTERM.

Threads: the main thread runs the recording indicator's UI loop (AppKit / GTK; they want the
main thread) or just waits for a signal; the mic callback, the hotkey listener, the ctl socket
and each dictation's finish run on their own threads and drive the indicator from there.
What `status` and `subscribe` report is skriv_client.status.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import signal
import sys
import threading
import time
from typing import TYPE_CHECKING, Protocol, cast

from skriv.v1 import skriv_pb2 as pb
from skriv_client import config, context, guardrails, history, indicator, retry
from skriv_client.asr import SAMPLE_RATE, unix_us
from skriv_client.audio import PreRoll
from skriv_client.config import ConfigError, expand
from skriv_client.continuation import ShadowBuffer
from skriv_client.pipeline import Utterance
from skriv_client.status import Report, State

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path
    from types import FrameType

    from skriv_client.continuation import KeyPress
    from skriv_client.hotkey import PushToTalk
    from skriv_client.indicator import Indicator, Refusal
    from skriv_client.mic import Mic
    from skriv_client.pipeline import Result, Services

log = logging.getLogger("skriv")


def _focus_token() -> str:
    # Looked up at call time so tests can substitute the context engine.
    return context.focus_token()


MIC_SILENCE_S = 1.0  # a running stream that has not called back this long has stopped
MIC_RETRY_S = 5.0  # a lost mic is reopened at most this often (no device: PortAudio re-init)
TICK_S = 1.0  # mic watchdog, config and server checks (status changes for `subscribe`)
# Applied by `reload` for the next dictation; every other table needs a restart.
RELOADABLE = (
    "words", "context", "gate", "cleanup", "feedback", "profiles", "retry",
)  # fmt: skip
PASTE_RESTART_KEYS = ("method", "restore_clipboard", "restore_delay_ms", "join_window_ms")


def _label(mode: str, utt: Utterance) -> str:
    """The indicator's recording label: the mode and a profile picked by hand."""
    return f"{mode} · {utt.profile_override}" if utt.profile_override else mode


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
        self._cue_pending = False  # the start sound waits for the recording's first audio
        # skriv's pastes + the keys typed since: the caret when the app does not expose it
        self.shadow = ShadowBuffer(svc.cfg.paste.join_window_ms / 1000, _focus_token)
        self.last_result: tuple[float, Result] | None = None  # (unix time, result)
        self.last_text = ""  # newest pasted/copied text (`skriv ctl paste-last`)
        self.indicator: Indicator | None = None
        # told (under the lock, so in order) when a recording starts or ends: the hotkey listener
        self.on_recording: Callable[[bool], None] | None = None
        # `skriv ctl profile` and the menu bar's Profile menu; None = by app
        self.profile_override: str | None = None
        self.paused = False  # menu bar: hotkey/ctl start refused, mic released
        self.started = 0  # dictations started (onboarding's hotkey test watches it)
        self.finishing: Utterance | None = None  # stopped, not yet pasted (one at a time)
        # told when a dictation starts/stops finishing: the hotkey listener (Esc cancels it)
        self.on_finishing: Callable[[bool], None] | None = None
        self.mic_status = "off"  # ok | lost | off
        self._mic_retry_at = 0.0  # time.monotonic() of the next reopen attempt
        self.report = Report(self)  # `status` / `subscribe`

    def on_pcm(self, pcm: bytes) -> None:
        """Mic callback thread."""
        with self._lock:
            utt = self.current
            if utt is None:
                self.preroll.push(pcm)
                return
            # never more than audio.max_ms (pre-roll included): the server refuses longer audio
            room = SAMPLE_RATE * self.svc.cfg.audio.max_ms // 1000 * 2 - len(utt.pcm)
            utt.feed(pcm[: max(0, room)])
            over = room <= len(pcm) and not self._max_hit
            if over:
                self._max_hit = True
            cue, self._cue_pending = self._cue_pending, False
        if cue:  # capture is live: what is said after the sound is recorded
            self.svc.feedback.sound("start")
        if self.indicator is not None:
            self.indicator.level(pcm)
        if over:
            log.info("max duration reached; stopping")
            threading.Thread(
                target=lambda: self.stop(reason="limit", only=utt), daemon=True
            ).start()

    # -- dictation ---------------------------------------------------------------------

    def _refuse(self, reply: str, why: Refusal) -> str:
        if self.on_recording is not None:
            # nothing records: the hotkey must not wait for a release, a tap or an Esc
            self.on_recording(False)  # noqa: FBT003 - Callable[[bool], None]
        if self.indicator is not None:
            self.indicator.refused(why)
        return reply

    def start(self) -> str:
        with self._lock:
            if self.paused:
                return self._refuse("error paused", "paused")
            if self.current is not None:
                return "ok already recording"
            if self.finishing is not None:
                # one at a time: a second dictation could paste out of order or into a prompt
                # edited meanwhile; the indicator shows the first one working
                return self._refuse("error busy", "busy")
        error = self._mic_ready()
        if error:
            with self._lock:
                return self._refuse(f"error {error}", "mic")
        with self._lock:
            if self.current is not None or self.finishing is not None:
                return self._refuse("error busy", "busy")
            profile, self.profile_override = self.profile_override, None  # used up by it
            self.current = utt = Utterance(self.svc, profile=profile, preroll=self.preroll.take())
            utt.input_device = self.mic.device_name if self.mic is not None else ""
            self._max_hit = False
            self._cue_pending = self._start_cue()
            if self.on_recording is not None:
                self.on_recording(True)  # noqa: FBT003 - Callable[[bool], None]
            self.started += 1
        if self.indicator is not None:
            self.indicator.recording(utt.id, _label("Listening", utt))
        log.info("recording...")
        self.report.touch()
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
                self.indicator.mic_lost()
            self.svc.feedback.notify("Microphone lost; skriv keeps trying to reopen it")
        self.report.touch()

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
        """Play the start sound (on the recording's first audio)? ``auto``: not when the
        indicator already shows it. Off by default: the indicator is the signal."""
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
            self.report.touch()
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
        keyup, released = unix_us(), time.perf_counter()  # release_to_paste_ms starts here
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
            res: Result | None = None
            try:
                utt.wait_started()  # bounded; the key-down context goes with the audio
                kept = self._keep(utt, None)  # before transcribing: a crash keeps the audio
                res = utt.finish(
                    min_ms=self.svc.cfg.audio.min_ms, keyup_unix_us=keyup, released=released,
                    shadow=self.shadow, reason=end,
                )  # fmt: skip
                self.last_result = (time.time(), res)
                if res.flat_signal:
                    self.svc.feedback.notify(
                        f"No audio from {res.input_device or 'the microphone'}: it sends a flat"
                        " signal (muted, or the wrong input?); run `skriv doctor`"
                    )
                elif res.status == "no_speech":
                    log.info(
                        "no speech heard from %s; `skriv doctor` tests the mic", res.input_device
                    )
                if res.final_text and res.status != "empty":
                    self.last_text = res.final_text
                self._kept(kept, utt, res)
                timing = res.timings.get("release_to_paste_ms")
                log.info(
                    "%s: %r%s", res.status, res.final_text,
                    f" ({timing:.0f} ms release->paste)" if timing is not None else "",
                )  # fmt: skip
            finally:
                if self.indicator is not None:
                    cfg = self.svc.cfg
                    prof = cfg.profiles.get(res.profile) if res is not None else None
                    expected = prof is not None and prof.cleanup and cfg.cleanup.enabled
                    self.indicator.outcome(utt.id, res, cleanup_expected=expected)
                with self._lock:
                    self.busy -= 1
                    self.finishing = None
                    if self.on_finishing is not None:
                        self.on_finishing(False)  # noqa: FBT003
                self.report.touch()

        threading.Thread(target=work, daemon=True, name="skriv-finish").start()
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
        self.report.touch()
        log.info("dictation %s", "paused" if paused else "resumed")

    def _pause(self, *, paused: bool) -> str:
        """`pause` / `resume`: idempotent."""
        self.set_paused(paused)
        return "ok paused" if paused else "ok resumed"

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
        """Recovery ring for `skriv retry`; None if not kept."""
        base = expand(self.svc.cfg.history.dir)
        try:
            return retry.save(
                base, self.svc.cfg.retry.keep, bytes(utt.pcm), utt.snapshot_at_start,
                self._meta(utt, res),
            )  # fmt: skip
        except OSError as e:
            log.warning("could not keep the recording for `skriv retry`: %s", e)
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
        return f"ok pasted {len(text)} chars"

    def profile(self, args: Sequence[str]) -> str:
        """``[]`` (show) | ``["auto"]`` | ``["set", NAME]``: the profile of the next dictation."""
        names = list(self.svc.cfg.profiles)
        with self._lock:
            current = self.profile_override
            match list(args):
                case []:
                    new = current
                case ["auto"]:
                    new = None
                case ["set", name] if name in names:
                    new = name
                case ["set", name]:
                    return f"error unknown profile {name!r} (have: {', '.join(names)})"
                case _:
                    return "error usage: profile [auto | set NAME]"
            self.profile_override = new
        self.report.touch()
        label = new or "auto"
        if args and self.indicator is not None:
            self.indicator.notice(f"Profile: {label}")
        if args:
            log.info("profile override: %s", label)
        return f"ok {label}" if new is None else f"ok {label} (next dictation)"

    def user_input(self, press: KeyPress) -> None:
        """A key press or click from the hotkey listener: keeps the shadow buffer current."""
        self.shadow.key(press)

    def state(self) -> State:
        with self._lock:
            return State(
                recording=self.current is not None, finishing=self.finishing is not None,
                busy=self.busy, paused=self.paused, last=self.last_result,
            )  # fmt: skip

    def status(self) -> str:
        with self._lock:
            state = "recording" if self.current is not None else "idle"
            return f"ok {state} finishing={self.busy}"

    def tick(self) -> None:
        """Every TICK_S: mic watchdog; config and server changes reach `subscribe`."""
        self.check_mic()
        self.report.check()

    def reload(self) -> str:
        """Re-read the config: per-dictation tables now, the rest named for a restart."""
        path = self.report.config_path
        if path is None:
            return "error no config file to reload"
        try:
            new = config.load(path)
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
        self.report.restart_needed = restart
        self.report.watch_config(path)
        self.report.touch()
        log.info("config reloaded%s", f"; restart for {', '.join(restart)}" if restart else "")
        return f"ok reloaded; restart for {', '.join(restart)}" if restart else "ok reloaded"

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
            "pause": lambda: self._pause(paused=True),
            "resume": lambda: self._pause(paused=False),
            "reload": self.reload,
        }
        action = actions.get(verb)
        return action() if action else f"error unknown command {command!r}"


class StartupError(RuntimeError):
    pass


class AppUi(Protocol):
    """Main-thread UI beside the indicator (Skriv.app's menu-bar item)."""

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
        log.info("no global hotkey; bind `skriv ctl start` / `skriv ctl stop` in your compositor")
        return None
    try:
        from skriv_client.hotkey import PushToTalk  # noqa: PLC0415
    except ImportError as e:
        raise StartupError(
            "hotkey.listener = pynput needs pynput (Linux: skriv-client[x11])"
        ) from e
    swallow_esc = cfg.swallow_esc
    if sys.platform == "darwin":
        from skriv_client import macos  # noqa: PLC0415

        if not macos.can_listen_events():
            log.warning(
                "no Input Monitoring permission: the hotkey will not fire (see `skriv doctor`)"
            )
        if swallow_esc and not macos.can_post_events():
            # a filtering event tap needs Accessibility; without it the tap is not created at all
            log.warning("no Accessibility permission: Esc will also reach the focused app")
            swallow_esc = False
    return PushToTalk(
        cfg.key, controller.start, controller.stop, controller.cancel, controller.user_input,
        on_tap=controller.hands_free, tap_s=cfg.tap_ms / 1000, swallow_esc=swallow_esc,
    )  # fmt: skip


def _make_mic(svc: Services, controller: Controller) -> Mic:
    try:
        from skriv_client.mic import Mic  # noqa: PLC0415 - loads PortAudio

        mic = Mic(controller.on_pcm, svc.cfg.audio.device)
    except Exception as e:  # PortAudioError / OSError (no PortAudio library)
        raise StartupError(
            f"cannot open the microphone ({type(e).__name__}: {e}); see `skriv doctor`"
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
    """``app_ui(controller, stop)`` builds a main-thread UI (``skriv app``); setting ``stop``
    quits. ``config_path``: the file `reload` re-reads."""
    from skriv_client import ctl  # noqa: PLC0415 - keep `skriv ctl` imports light
    from skriv_client.service import linux as systemd  # noqa: PLC0415 - no-ops outside systemd

    _log_providers(svc)
    svc.router.start_background()

    controller = Controller(svc, None)
    controller.report.watch_config(config_path)
    try:
        hotkey = _make_hotkey(svc, controller)
        mic = _make_mic(svc, controller)
    except StartupError as e:
        log.error("%s", e)
        return 1
    controller.mic = mic
    # Warm per-window term pools in the background so key-down only refreshes cheap sources.
    context.start_prefetch(str(expand(svc.cfg.history.dir)))
    ui = indicator.make(
        svc.cfg.indicator, retry_hint=svc.cfg.retry.keep > 0, limit_s=svc.cfg.audio.max_ms / 1000
    )
    if ui is not None:
        controller.indicator = ui
        svc.on_stage = ui.stage
    server = ctl.CtlServer(
        ctl.socket_path(), controller.handle, controller.report.json, controller.report.wait_change
    )
    controller.open_mic()
    server.start()
    systemd.notify("READY=1")  # Type=notify unit: ready once `skriv ctl` gets answers
    if hotkey is not None:
        controller.on_recording = hotkey.recording
        controller.on_finishing = hotkey.finishing
        hotkey.start()

    stop = threading.Event()
    systemd.start_watchdog(lambda: ctl.send("ping", timeout=5.0).startswith("ok"), stop)

    _start_ticks(controller, stop)

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    log.info("skriv ready (paste: %s)", svc.injector.name)
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

    threading.Thread(target=ticks, daemon=True, name="skriv-tick").start()


def _x11_only() -> bool:
    return bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY")
