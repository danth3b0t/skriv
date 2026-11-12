"""Recording indicator: a small passive overlay showing what the daemon is doing.

recording (with the live input level) -> transcribing -> cleaning -> done | error | cancelled,
then it hides by itself. It never takes focus or clicks, so the paste still goes to the window
that had focus at key-down. Backends:

- macOS: a non-activating, click-through NSPanel (``indicator.macos``). AppKit wants the main
  thread, so the daemon runs the AppKit event loop there and everything else on threads.
- Linux: GTK 4 + gtk4-layer-shell via PyGObject when installed (``indicator.gtk``): an overlay
  layer surface with no keyboard interactivity and an empty input region (Hyprland, sway,
  KDE...). Without them, or on compositors without layer-shell (GNOME), there is no indicator.

:class:`Indicator` is the toolkit-independent state machine; it may be driven from any thread.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from hark_client.audio import rms_dbfs

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.config import IndicatorConfig

log = logging.getLogger("hark.indicator")

type State = Literal["recording", "transcribing", "cleaning", "done", "error", "cancelled", "info"]

# info: a short notice while idle (the profile picked for the next dictation)
FINAL_STATES: frozenset[State] = frozenset({"done", "error", "cancelled", "info"})
STAGES: dict[str, str] = {"transcribing": "Transcribing…", "cleaning": "Cleaning up…"}
# history status (pipeline.Result.status) -> what the indicator shows
FINISHED: dict[str, tuple[State, str]] = {
    "pasted": ("done", "Pasted"),
    "copied": ("done", "Copied"),
    "focus_changed": ("done", "Focus moved: copied"),
    "paste_failed": ("error", "Paste failed"),
    "error": ("error", "Transcription failed"),
    "empty": ("cancelled", "Nothing to paste"),
    "no_speech": ("cancelled", "No speech"),
    "too_short": ("cancelled", "Too short"),
    "cancelled": ("cancelled", "Cancelled"),
}
LINGER_S: dict[State, float] = {"done": 0.8, "cancelled": 1.0, "error": 3.0, "info": 1.5}
LEVEL_INTERVAL_S = 0.05  # input level redraws at most 20 times a second
LEVEL_FLOOR_DB = -60.0


@dataclass(frozen=True, slots=True)
class View:
    state: State
    label: str
    level: float = 0.0  # input level 0..1 while recording


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


class Indicator:
    """Shows the newest dictation: a recording always takes over; an older dictation that is
    still finishing only updates the display once the current one has reached a final state."""

    def __init__(
        self,
        backend: Backend,
        *,
        retry_hint: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.retry_hint = retry_hint
        self.clock = clock
        self._lock = threading.Lock()
        self._view: View | None = None
        self._owner = ""
        self._hide_at: float | None = None
        self._level_at = float("-inf")

    @property
    def view(self) -> View | None:
        return self._view

    def _show(self, uid: str, view: View | None, *, hide_after: float | None = None) -> None:
        # caller holds the lock
        self._owner, self._view = uid, view
        self._hide_at = None if hide_after is None else self.clock() + hide_after
        self.backend.render(view)

    def _may_update(self, uid: str) -> bool:
        return uid == self._owner or self._view is None or self._view.state in FINAL_STATES

    def recording(self, uid: str, label: str = "Listening") -> None:
        """Recording started, or its mode changed (``label``: "Hands-free" after a tap)."""
        with self._lock:
            self._level_at = float("-inf")
            self._show(uid, View("recording", label))

    def level(self, pcm16: bytes) -> None:
        """Mic callback thread: redraws the level while recording (rate-limited)."""
        with self._lock:
            view = self._view
            now = self.clock()
            if view is None or view.state != "recording" or now - self._level_at < LEVEL_INTERVAL_S:
                return
            self._level_at = now
            self._show(self._owner, View("recording", view.label, level_of(pcm16)))

    def stage(self, uid: str, stage: str) -> None:
        """``transcribing`` (key up) or ``cleaning`` (cleanup request sent)."""
        label = STAGES.get(stage)
        if label is None:
            raise ValueError(f"unknown indicator stage {stage!r}")
        with self._lock:
            if self._may_update(uid):
                self._show(
                    uid, View("transcribing" if stage == "transcribing" else "cleaning", label)
                )

    def finished(self, uid: str, status: str) -> None:
        """A history status (pasted, error, cancelled...); shown briefly, then hidden."""
        state, label = FINISHED.get(status, ("error", status))
        if status == "error" and self.retry_hint:
            label = "Failed: hark retry"
        with self._lock:
            if self._may_update(uid):
                self._show(uid, View(state, label), hide_after=LINGER_S[state])

    def notice(self, label: str) -> None:
        """A short message, shown only if no dictation is on display (never over a recording)."""
        with self._lock:
            if self._view is None or self._view.state in FINAL_STATES:
                self._show("", View("info", label), hide_after=LINGER_S["info"])

    def tick(self) -> None:
        """UI thread, periodically: hides a final state once it has been shown long enough."""
        with self._lock:
            if self._hide_at is not None and self.clock() >= self._hide_at:
                self._show(self._owner, None)

    def run(self, stop: threading.Event) -> None:
        self.backend.run(stop, self.tick)


def make(cfg: IndicatorConfig, *, retry_hint: bool = False) -> Indicator | None:
    """The platform's indicator, or None (disabled, or no usable toolkit). Main thread only."""
    if not cfg.enabled:
        return None
    backend: Backend | None = None
    try:
        if sys.platform == "darwin":
            from hark_client.indicator.macos import PanelBackend  # noqa: PLC0415 - pyobjc

            backend = PanelBackend(cfg.position)
        elif sys.platform.startswith("linux"):
            from hark_client.indicator.gtk import create  # noqa: PLC0415 - optional PyGObject

            backend = create(cfg.position)
    except Exception as e:  # noqa: BLE001 - the indicator is optional; dictation must still work
        log.warning("recording indicator unavailable: %s: %s", type(e).__name__, e)
        return None
    if backend is None:
        return None
    return Indicator(backend, retry_hint=retry_hint)
