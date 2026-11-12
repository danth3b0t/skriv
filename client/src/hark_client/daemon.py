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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hark.v1 import hark_pb2 as pb
from hark_client import context, guardrails, history, indicator, retry
from hark_client.asr import unix_us
from hark_client.audio import PreRoll
from hark_client.config import expand
from hark_client.continuation import ShadowBuffer, backspaces
from hark_client.pipeline import Utterance

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
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
        self.profile_override: str | None = None  # `hark ctl profile`; None = by app
        self._undo_lock = threading.Lock()

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
        if self.indicator is not None:
            self.indicator.recording(utt.id, _label("Listening", utt))
        if self.svc.learner is not None:
            self.svc.learner.cancel()  # the next paste changes the field: stop watching the last
        if self.mic is not None and not self.keep_open:
            self.mic.start()
        if cue:
            self.svc.feedback.sound("start")
        log.info("recording...")
        return "ok recording"

    def _start_cue(self) -> bool:
        """Play the start sound? (``auto``: not when the indicator already shows it.)"""
        cfg = self.svc.cfg.feedback
        mode = cfg.start_cue
        return cfg.sounds and (mode == "always" or (mode == "auto" and self.indicator is None))

    def _detach(self) -> Utterance | None:
        if self.mic is not None and not self.keep_open:
            self.mic.stop()  # outside the lock: waits for the last callback
        with self._lock:
            utt, self.current = self.current, None
            if utt is not None and self.on_recording is not None:
                self.on_recording(False)  # noqa: FBT003
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
                if res.pasted_text and res.paste_verdict != "missing":
                    swap = res.swap_text if res.swap_text != res.pasted_text else ""
                    terminal = self.svc.cfg.profile(res.profile).terminal
                    self.insertion = Insertion(res.pasted_text, swap, terminal)
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
            "profile_override": self.profile_override,
        }

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
        }
        action = actions.get(verb)
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


def run(svc: Services) -> int:
    from hark_client import ctl  # noqa: PLC0415 - keep `hark ctl` imports light

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
    if hotkey is not None:
        controller.on_recording = hotkey.recording
        controller.keys_observed = True
        hotkey.start()

    stop = threading.Event()

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
