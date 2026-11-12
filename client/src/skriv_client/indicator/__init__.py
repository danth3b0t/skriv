"""Recording indicator: a small passive overlay showing what the daemon is doing (the pill,
design/SPEC.md §4).

recording (with the live input level) -> working -> an outcome (✓, Copied, Failed...), which
lingers briefly and hides by itself. It never takes focus or clicks, so the paste still goes to
the window that had focus at key-down. Backends:

- macOS: a non-activating, click-through NSPanel (``indicator.macos``). AppKit wants the main
  thread, so the daemon runs the AppKit event loop there and everything else on threads.
- Linux: GTK 4 + gtk4-layer-shell via PyGObject when installed (``indicator.gtk``): an overlay
  layer surface with no keyboard interactivity and an empty input region (Hyprland, sway,
  KDE...). Without them, or on compositors without layer-shell (GNOME), there is no indicator.

:class:`Indicator` is the toolkit-independent state machine; it may be driven from any thread.
What a pill shows for a fact is decided by the pure functions :func:`recording_view`,
:func:`outcome_view` and :func:`status_view`; backends only draw a :class:`View`.
"""

from __future__ import annotations

import collections
import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from skriv_client.audio import rms_dbfs
from skriv_client.status import failure

if TYPE_CHECKING:
    from collections.abc import Callable

    from skriv_client.config import IndicatorConfig
    from skriv_client.pipeline import Result

log = logging.getLogger("skriv.indicator")

# info: a short notice while idle (the profile picked for the next dictation, Paused)
type State = Literal["recording", "working", "done", "error", "cancelled", "info"]
# what the backend draws in front of the words
type Glyph = Literal[
    "dot", "lock", "dots", "check", "cross", "copy", "alert", "mic-off", "pause", "info"
]
# the glyph's colour role (design/SPEC.md §3): live = mic live or failed
type Tone = Literal["fg", "dim", "live", "ok", "warn", "accent"]
type Refusal = Literal["busy", "paused", "mic"]

FINAL_STATES: frozenset[State] = frozenset({"done", "error", "cancelled", "info"})
STAGES = frozenset({"transcribing", "cleaning"})
LEVEL_INTERVAL_S = 0.05  # input level redraws at most 20 times a second
LEVEL_FLOOR_DB = -60.0
METER_BARS = 8  # the newest samples, one bar each
RECOVERY_KEY = "Super Alt D"  # skriv-panel on Omarchy (packaging/omarchy)
OMARCHY_THEME = Path.home() / ".local/state/omarchy/current/theme"  # where Omarchy's shell reads it
LONG_S = 60  # a recording longer than this shows its elapsed time
COUNTDOWN_S = 30  # this close to audio.max_ms the time left shows instead
CLEANUP_APPLIED = frozenset({"applied", "no_edits"})  # cleanup ran and was kept
# the words after ``Failed · `` per `last.failure` (other: none)
FAILURE_WORDS = {
    "server_timeout": "server timeout",
    "server_down": "server down",
    "degenerate_output": "garbled transcript",
}
# linger (s) per outcome, design/SPEC.md §4
LINGER_SENT = 0.5
LINGER_TAG = 0.8  # ✓ via provider, ✓ cleanup skipped
LINGER_LONG_TAG = 1.5  # ✓ mic dropped, ✓ Stopped at m:ss
LINGER_CANCELLED = 0.6
LINGER_COPIED = 1.0
LINGER_FOCUS_MOVED = 2.5
LINGER_PROBLEM = 4.0  # paste failed, failed, mic lost
LINGER_INFO = 1.0  # paused, busy, profile notices
CANCELLED: dict[str, str] = {
    "cancelled": "Cancelled",
    "no_speech": "No speech",
    "too_short": "Too short",
    "empty": "Nothing to paste",
}


@dataclass(frozen=True, slots=True)
class View:
    """One pill. ``label`` is the primary text, ``detail`` the secondary one (a ✓ tag, the
    elapsed time), ``hint`` the recovery key; ``name`` is the whole sentence (accessibility,
    and the macOS panel, which draws no glyphs)."""

    state: State
    glyph: Glyph
    tone: Tone
    name: str
    label: str = ""
    detail: str = ""
    hint: str = ""
    pinned: str = ""  # recording: the profile picked by hand
    levels: tuple[float, ...] = ()  # recording: input levels 0..1, newest last

    @property
    def level(self) -> float:
        """The newest input level (0 when not recording)."""
        return self.levels[-1] if self.levels else 0.0


class Backend(Protocol):
    def render(self, view: View | None) -> None:
        """Show ``view``, or hide on None. Called from any thread: the backend hands it to its
        UI thread (latest view wins)."""
        ...

    def run(self, stop: threading.Event, tick: Callable[[], None]) -> None:
        """Run the UI loop on the calling (main) thread until ``stop`` is set, calling ``tick``
        at least every 0.5 s and every 0.1 s while something is shown."""
        ...


def level_of(pcm16: bytes) -> float:
    """RMS of PCM16 as 0..1 on a dB scale (-60 dBFS and below = 0)."""
    db = rms_dbfs(pcm16)
    return min(1.0, max(0.0, (db - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB))


def clock_text(seconds: float) -> str:
    """m:ss"""
    s = max(0, round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def recording_view(
    *,
    hands_free: bool,
    pinned: str,
    levels: tuple[float, ...],
    elapsed_s: float,
    limit_s: float = 0.0,
) -> View:
    """Red dot (lock when hands-free) and the meter; the pinned profile; m:ss after a minute,
    "m:ss left" in the last COUNTDOWN_S before ``limit_s`` (audio.max_ms; 0: none)."""
    mode = "Hands-free" if hands_free else "Recording"
    left = limit_s - elapsed_s
    if limit_s and left <= COUNTDOWN_S:
        elapsed = f"{clock_text(max(0.0, left))} left"
    else:
        elapsed = clock_text(elapsed_s) if elapsed_s > LONG_S else ""
    name = " · ".join(x for x in (mode, pinned, elapsed) if x)
    return View(
        "recording", "lock" if hands_free else "dot", "live", name,
        detail=elapsed, pinned=pinned, levels=levels,
    )  # fmt: skip


WORKING = View("working", "dots", "accent", "Working")


def status_view(status: str) -> tuple[View, float] | None:
    """The pill for a status that needs no other fact (cancelled, no speech...), or None."""
    label = CANCELLED.get(status)
    if label is None:
        return None
    return View("cancelled", "cross", "dim", label, label), LINGER_CANCELLED


def no_speech_view(res: Result) -> tuple[View, float]:
    """Nothing heard: which mic it listened to and `skriv doctor`. Quiet is not an error (a
    press with nothing said); a flat signal from the mic is."""
    hint = "skriv doctor"
    if res.flat_signal:
        label = "No audio from mic"
        name = _join(label, res.input_device, hint)
        return (
            View("error", "mic-off", "live", name, label, detail=res.input_device, hint=hint),
            LINGER_PROBLEM,
        )
    name = _join("No speech", res.input_device, hint)
    view = View("cancelled", "cross", "dim", name, "No speech", detail=res.input_device, hint=hint)
    return view, LINGER_LONG_TAG


def failure_reason(error: str) -> str:
    """A short reason for ``Failed · …`` from a Result.error ("" = none worth showing)."""
    return FAILURE_WORDS.get(failure(error), "")


def outcome_view(
    res: Result | None, *, cleanup_expected: bool, recovery: str = "", retry_hint: bool = False
) -> tuple[View, float]:
    """The pill for a finished dictation (None: it crashed) and how long it lingers.

    ``cleanup_expected``: the profile asks for cleanup, so a result without it is tagged.
    ``recovery``: the key that opens the panel (failures name it); ``retry_hint``: name
    `skriv retry` on a failure instead when there is no such key."""
    status = "error" if res is None else res.status
    if res is not None and status == "no_speech" and res.input_device:
        simple = no_speech_view(res)
    else:
        simple = status_view(status)
    if simple is not None:
        return simple
    if res is None or status not in ("pasted", "copied", "focus_changed", "paste_failed"):
        reason = failure_reason(res.error) if res is not None else ""
        label = f"Failed · {reason}" if reason else "Failed"
        hint = recovery or ("skriv retry" if retry_hint else "")
        return View("error", "alert", "live", _join(label, hint), label, hint=hint), LINGER_PROBLEM
    delivery = res.delivery
    if status == "paste_failed" or (status == "focus_changed" and delivery != "copied"):
        what = "Paste failed" if status == "paste_failed" else "Focus moved"
        label = f"{what} · copied" if delivery == "copied" else what
        return (
            View("error", "copy", "warn", _join(label, recovery), label, hint=recovery),
            LINGER_PROBLEM,
        )
    if status == "focus_changed":
        label = "Copied · focus moved"
        return View("done", "copy", "warn", label, label), LINGER_FOCUS_MOVED
    if status == "copied" or delivery != "sent":
        return View("done", "copy", "fg", "Copied", "Copied"), LINGER_COPIED
    return _sent(res, cleanup_expected=cleanup_expected)


def _sent(res: Result, *, cleanup_expected: bool) -> tuple[View, float]:
    """✓ with at most one tag: mic dropped, stopped at the limit, cleanup skipped, via
    provider (the first that applies)."""
    if res.stop_reason == "limit" and not res.mic_interrupted:
        label = f"Stopped at {clock_text(res.timings.get('held_ms', 0.0) / 1000)}"
        return View("done", "check", "ok", label, label), LINGER_LONG_TAG
    cleanup = res.cleanup.status if res.cleanup is not None else None
    tag, linger = "", LINGER_SENT
    if res.mic_interrupted:
        tag, linger = "mic dropped", LINGER_LONG_TAG
    elif cleanup_expected and cleanup not in CLEANUP_APPLIED:
        tag, linger = "cleanup skipped", LINGER_TAG
    elif res.fallback and res.provider:
        tag, linger = f"via {res.provider}", LINGER_TAG
    return View("done", "check", "ok", _join("Sent", tag), detail=tag), linger


def _join(*parts: str) -> str:
    return " · ".join(p for p in parts if p)


def refusal_view(why: Refusal, recovery: str) -> tuple[View, float]:
    """A key press that started nothing."""
    if why == "busy":
        return WORKING, LINGER_INFO
    if why == "paused":
        return View("info", "pause", "fg", "Paused", "Paused"), LINGER_INFO
    return mic_lost_view(recovery)


def mic_lost_view(recovery: str) -> tuple[View, float]:
    return (
        View("error", "mic-off", "live", _join("Mic lost", recovery), "Mic lost", hint=recovery),
        LINGER_PROBLEM,
    )


class Indicator:
    """Shows the newest dictation: a recording always takes over; an older dictation that is
    still finishing only updates the display once the current one has reached a final state.
    Notices (owner "") never cover a dictation and give way to any dictation update."""

    def __init__(
        self,
        backend: Backend,
        *,
        recovery: str = "",
        retry_hint: bool = False,
        limit_s: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.limit_s = limit_s  # audio.max_ms: the recording counts down to it
        self.recovery = recovery
        self.retry_hint = retry_hint
        self.clock = clock
        self._lock = threading.Lock()
        self._view: View | None = None
        self._owner = ""
        self._hide_at: float | None = None
        self._level_at = float("-inf")
        self._started_at = 0.0
        self._levels: collections.deque[float] = collections.deque(maxlen=METER_BARS)

    @property
    def view(self) -> View | None:
        return self._view

    def _show(self, uid: str, view: View | None, *, hide_after: float | None = None) -> None:
        # caller holds the lock
        self._owner, self._view = uid, view
        self._hide_at = None if hide_after is None else self.clock() + hide_after
        self.backend.render(view)

    def _may_update(self, uid: str) -> bool:
        view = self._view
        return self._owner in (uid, "") or view is None or view.state in FINAL_STATES

    def _idle(self) -> bool:
        """Nothing but a lingering outcome or notice on display (a notice may show)."""
        return self._view is None or self._view.state in FINAL_STATES

    def recording(self, uid: str, label: str = "Listening") -> None:
        """Recording started, or its mode changed. ``label``: the daemon's mode word
        ("Listening", "Hands-free" after a tap), then `` · <profile>`` when one was picked."""
        mode, _, pinned = label.partition(" · ")
        with self._lock:
            if uid != self._owner or self._view is None or self._view.state != "recording":
                self._started_at = self.clock()
                self._levels.clear()
                self._levels.extend([0.0] * METER_BARS)
            self._level_at = float("-inf")
            self._show(uid, self._recording_view(hands_free=mode == "Hands-free", pinned=pinned))

    def _recording_view(self, *, hands_free: bool, pinned: str) -> View:
        return recording_view(
            hands_free=hands_free, pinned=pinned, levels=tuple(self._levels),
            elapsed_s=self.clock() - self._started_at, limit_s=self.limit_s,
        )  # fmt: skip

    def level(self, pcm16: bytes) -> None:
        """Mic callback thread: redraws the meter while recording (rate-limited)."""
        with self._lock:
            view = self._view
            now = self.clock()
            if view is None or view.state != "recording" or now - self._level_at < LEVEL_INTERVAL_S:
                return
            self._level_at = now
            self._levels.append(level_of(pcm16))
            hands_free = view.glyph == "lock"
            self._show(self._owner, self._recording_view(hands_free=hands_free, pinned=view.pinned))

    def stage(self, uid: str, stage: str) -> None:
        """``transcribing`` (key up) or ``cleaning`` (cleanup request sent): both show working."""
        if stage not in STAGES:
            raise ValueError(f"unknown indicator stage {stage!r}")
        with self._lock:
            if self._may_update(uid) and self._view != WORKING:
                self._show(uid, WORKING)
            elif self._may_update(uid):
                self._owner, self._hide_at = uid, None  # a busy notice becomes this dictation

    def outcome(self, uid: str, res: Result | None, *, cleanup_expected: bool = False) -> None:
        """A finished dictation (None: its finish crashed); shown briefly, then hidden."""
        view, linger = outcome_view(
            res, cleanup_expected=cleanup_expected, recovery=self.recovery,
            retry_hint=self.retry_hint,
        )  # fmt: skip
        with self._lock:
            if self._may_update(uid):
                self._show(uid, view, hide_after=linger)

    def finished(self, uid: str, status: str) -> None:
        """A dictation ended with a status that needs no other fact (``cancelled``)."""
        shown = status_view(status)
        if shown is None:
            raise ValueError(f"status {status!r} needs a Result: use outcome()")
        with self._lock:
            if self._may_update(uid):
                self._show(uid, shown[0], hide_after=shown[1])

    def notice(self, label: str) -> None:
        """A short message, shown only if no dictation is on display (never over a recording)."""
        with self._lock:
            if self._idle():
                self._show("", View("info", "info", "fg", label, label), hide_after=LINGER_INFO)

    def mic_lost(self) -> None:
        """The mic stopped delivering audio (not drawn over a recording or a working pill)."""
        view, linger = mic_lost_view(self.recovery)
        with self._lock:
            if self._idle():
                self._show("", view, hide_after=linger)

    def refused(self, why: Refusal) -> None:
        """A start was refused: ``busy`` (a dictation is still finishing: working stays or
        shows again), ``paused`` or ``mic`` (the mic could not be opened)."""
        with self._lock:
            if why == "busy" and self._view is not None and self._view.state == "working":
                return  # the finishing dictation is already on display
            if self._idle():
                view, linger = refusal_view(why, self.recovery)
                self._show("", view, hide_after=linger)

    def tick(self) -> None:
        """UI thread, periodically: hides a final state once it has been shown long enough."""
        with self._lock:
            if self._hide_at is not None and self.clock() >= self._hide_at:
                self._show(self._owner, None)

    def run(self, stop: threading.Event, also: Callable[[], None] | None = None) -> None:
        """The UI loop on this (main) thread until ``stop``; ``also`` runs on every tick."""
        if also is None:
            self.backend.run(stop, self.tick)
            return

        def tick() -> None:
            self.tick()
            also()

        self.backend.run(stop, tick)


def make(
    cfg: IndicatorConfig, *, retry_hint: bool = False, limit_s: float = 0.0
) -> Indicator | None:
    """The platform's indicator, or None (disabled, or no usable toolkit). Main thread only.
    On Omarchy failure pills name the key that opens skriv's panel (``skriv-panel``, bound by
    omarchy-install-dictation-skriv)."""
    if not cfg.enabled:
        return None
    backend: Backend | None = None
    try:
        if sys.platform == "darwin":
            from skriv_client.indicator.macos import PanelBackend  # noqa: PLC0415 - pyobjc

            backend = PanelBackend(cfg.position)
        elif sys.platform.startswith("linux"):
            from skriv_client.indicator.gtk import create  # noqa: PLC0415 - optional PyGObject

            backend = create(cfg.position)
    except Exception as e:  # noqa: BLE001 - the indicator is optional; dictation must still work
        log.warning("recording indicator unavailable: %s: %s", type(e).__name__, e)
        return None
    if backend is None:
        return None
    recovery = RECOVERY_KEY if OMARCHY_THEME.is_dir() else ""
    return Indicator(backend, recovery=recovery, retry_hint=retry_hint, limit_s=limit_s)
