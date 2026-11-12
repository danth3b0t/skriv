"""`hark run`: mic + hotkey/ctl socket -> Utterance pipeline, until SIGINT/SIGTERM.

Threads: the main thread runs the recording indicator's UI loop (AppKit / GTK; they want the
main thread) or just waits for a signal; the mic callback, the hotkey listener, the ctl socket
and each dictation's finish run on their own threads and drive the indicator from there.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import TYPE_CHECKING

from hark.v1 import hark_pb2 as pb
from hark_client import context, guardrails, history, indicator, retry
from hark_client.asr import unix_us
from hark_client.audio import PreRoll
from hark_client.config import expand
from hark_client.continuation import ShadowBuffer
from hark_client.pipeline import Utterance

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

    from hark_client.continuation import KeyPress
    from hark_client.hotkey import PushToTalk
    from hark_client.indicator import Indicator
    from hark_client.mic import Mic
    from hark_client.pipeline import Result, Services

log = logging.getLogger("hark")


def _focus_token() -> str:
    # Looked up at call time so tests can substitute the context engine.
    return context.focus_token()


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
            threading.Thread(target=self.stop, daemon=True).start()

    def start(self) -> str:
        with self._lock:
            if self.current is not None:
                return "ok already recording"
            self.current = utt = Utterance(self.svc, preroll=self.preroll.take())
            self._max_hit = False
        if self.indicator is not None:
            self.indicator.recording(utt.id)
        if self.svc.learner is not None:
            self.svc.learner.cancel()  # the next paste changes the field: stop watching the last
        if self.mic is not None and not self.keep_open:
            self.mic.start()
        self.svc.feedback.sound("start")
        log.info("recording...")
        return "ok recording"

    def _detach(self) -> Utterance | None:
        if self.mic is not None and not self.keep_open:
            self.mic.stop()  # outside the lock: waits for the last callback
        with self._lock:
            utt, self.current = self.current, None
        return utt

    def stop(self) -> str:
        keyup = unix_us()
        utt = self._detach()
        if utt is None:
            return "ok idle"
        self.svc.feedback.sound("stop")
        if self.indicator is not None:
            self.indicator.stage(utt.id, "transcribing")
        with self._lock:
            self.busy += 1

        def work() -> None:
            status = "error"
            try:
                res = utt.finish(
                    min_ms=self.svc.cfg.audio.min_ms, keyup_unix_us=keyup, shadow=self.shadow
                )
                status = res.status
                self.last_result = (time.time(), res)
                if res.final_text and res.status != "empty":
                    self.last_text = res.final_text
                self._keep(utt, res)
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

        threading.Thread(target=work, daemon=True, name="hark-finish").start()
        return "ok stopped"

    def toggle(self) -> str:
        with self._lock:
            recording = self.current is not None
        return self.stop() if recording else self.start()

    def cancel(self) -> str:
        utt = self._detach()
        if utt is None:
            return "ok idle"
        utt.cancel(pb.CANCEL_REASON_USER_ABORT)
        if self.indicator is not None:
            self.indicator.finished(utt.id, "cancelled")
        self.svc.feedback.sound("error")
        log.info("cancelled")
        return "ok cancelled"

    def _keep(self, utt: Utterance, res: Result) -> None:
        """Recovery ring for `hark retry` (not for presses under min_ms)."""
        if res.status == "too_short":
            return
        focus = utt.snapshot_at_start.focus
        meta = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(utt.created_unix)),
            "utterance_id": utt.id,
            "status": res.status,
            "error": res.error,
            "profile": res.profile,
            "app": focus.app,
            "app_id": focus.app_id,
            "audio_ms": utt.audio_ms,
        }
        base = expand(self.svc.cfg.history.dir)
        try:
            retry.save(base, self.svc.cfg.retry.keep, bytes(utt.pcm), utt.snapshot_at_start, meta)
        except OSError as e:
            log.warning("could not keep the recording for `hark retry`: %s", e)

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

    def status_json(self) -> dict[str, object]:
        """JSON v1 ``status`` fields (docs/protocol/ctl-v1.md). Never includes dictated text."""
        with self._lock:
            state = "recording" if self.current is not None else "idle"
            busy = self.busy
            last = self.last_result
        learner = self.svc.learner
        return {
            "state": state,
            "finishing": busy,
            "last": None
            if last is None
            else {
                "at": round(last[0], 3),
                "utterance_id": last[1].utterance_id,
                "status": last[1].status,
                "profile": last[1].profile,
                "paste_verdict": last[1].paste_verdict,
                "keyup_to_paste_ms": last[1].timings.get("keyup_to_paste_ms"),
            },
            "learning": {
                "enabled": learner is not None,
                "watching": learner is not None and learner.watching,
            },
        }

    def handle(self, command: str) -> str:
        actions: dict[str, Callable[[], str]] = {
            "start": self.start,
            "stop": self.stop,
            "toggle": self.toggle,
            "cancel": self.cancel,
            "status": self.status,
            "ping": lambda: "ok pong",
            "paste-last": self.paste_last,
        }
        action = actions.get(command)
        return action() if action else f"error unknown command {command!r}"


class StartupError(RuntimeError):
    pass


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
    if sys.platform == "darwin":
        from hark_client import macos  # noqa: PLC0415

        if not macos.can_listen_events():
            log.warning(
                "no Input Monitoring permission: the hotkey will not fire (see `hark doctor`)"
            )
    return PushToTalk(
        cfg.key, controller.start, controller.stop, controller.cancel, controller.user_input
    )


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


def run(svc: Services) -> int:
    from hark_client import ctl  # noqa: PLC0415 - keep `hark ctl` imports light
    from hark_client.service import linux as systemd  # noqa: PLC0415 - no-ops outside systemd

    caps = svc.caps.refresh()
    if caps is None:
        log.warning("server %s unreachable; will retry on each dictation", svc.cfg.server.address)
    else:
        models = ", ".join(f"{m.model_id}({pb.ModelState.Name(m.state)})" for m in caps.models)
        log.info("server %s: %s; features: %s", caps.server.name, models, ", ".join(caps.features))

    controller = Controller(svc, None)
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
    server = ctl.CtlServer(ctl.socket_path(), controller.handle, controller.status_json)
    if svc.cfg.audio.keep_open:
        mic.start()
    server.start()
    systemd.notify("READY=1")  # Type=notify unit: ready once `hark ctl` gets answers
    if hotkey is not None:
        hotkey.start()

    stop = threading.Event()
    systemd.start_watchdog(lambda: ctl.send("ping", timeout=5.0).startswith("ok"), stop)

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    log.info("hark ready (paste: %s)", svc.injector.name)
    try:
        if ui is not None:
            ui.run(stop)  # the toolkit's loop on this (main) thread until a signal
        else:
            while not stop.wait(0.5):
                pass
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


def _x11_only() -> bool:
    return bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY")
