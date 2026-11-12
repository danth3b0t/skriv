"""What the daemon reports: the JSON v1 `status` reply and `subscribe` (docs/protocol/ctl-v1.md).

Facts only, never dictated text: the controller's state, the provider the next dictation would
use (cached health), whether the config file changed, and the last result. Every change bumps a
version, so `subscribe` can wait for the next one.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hark_client import retry
from hark_client.config import expand

if TYPE_CHECKING:
    from pathlib import Path

    from hark_client.daemon import Controller
    from hark_client.pipeline import Result


_TIMEOUT = re.compile(r"within|deadline|timeout|timed out", re.IGNORECASE)
_DOWN = re.compile(r"unavailable|refused|no stream|closed", re.IGNORECASE)


def failure(error: str) -> str:
    """The category of a failed dictation's Result.error (`last.failure`): degenerate_output
    (the recognizer returned no text, guardrails.degenerate), server_timeout, server_down or
    other."""
    if error.startswith("asr: degenerate output"):
        return "degenerate_output"
    if _TIMEOUT.search(error):
        return "server_timeout"
    if _DOWN.search(error):
        return "server_down"
    return "other"


@dataclass(frozen=True, slots=True)
class State:
    """The controller's dictation state, read at once under its lock."""

    recording: bool
    finishing: bool  # a stopped dictation is being transcribed/pasted
    busy: int  # dictations still finishing
    paused: bool
    last: tuple[float, Result] | None  # (unix time, result)


class Report:
    """`status` and `subscribe` for one :class:`~hark_client.daemon.Controller`."""

    def __init__(self, controller: Controller) -> None:
        self.c = controller
        self.config_path: Path | None = None  # what `reload` re-reads (None: not reloadable)
        self._config_mtime: float | None = None
        self.restart_needed: list[str] = []  # tables the last `reload` could not apply
        self._seen: dict[str, object] = {}
        self._changed = threading.Condition()
        self._version = 0  # bumped on every status change (`subscribe`)

    # -- versioning (`subscribe`) -------------------------------------------------------

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

    def check(self) -> None:
        """Periodically: config and server changes reach `subscribe`."""
        seen: dict[str, object] = {"server": self.server(), "config": self.config_changed()}
        if seen != self._seen:
            self._seen = seen
            self.touch()

    # -- config file --------------------------------------------------------------------

    def watch_config(self, path: Path | None) -> None:
        """`reload` re-reads ``path``; `status` reports when it changes."""
        self.config_path = path
        try:
            self._config_mtime = path.stat().st_mtime if path is not None else None
        except OSError:
            self._config_mtime = None

    def config_changed(self) -> bool:
        path, seen = self.config_path, self._config_mtime
        if path is None or seen is None:
            return False
        try:
            return path.stat().st_mtime != seen
        except OSError:
            return False

    # -- the reply ----------------------------------------------------------------------

    def server(self) -> dict[str, object]:
        """The provider the next dictation would use, from cached health (never probes)."""
        providers = self.c.svc.router.providers
        for i, p in enumerate(providers):
            h = p.health
            if h.state == "ok":
                return {"name": p.name, "state": "ok", "rtt_ms": h.rtt_ms, "fallback": i > 0}
        first = providers[0].health
        state = "unknown" if all(p.health.state == "unknown" for p in providers) else "down"
        if first.state == "slow":
            state = "slow"
        return {"name": providers[0].name, "state": state, "rtt_ms": None, "fallback": False}

    def json(self) -> dict[str, object]:
        """JSON v1 ``status`` fields. Never includes dictated text."""
        s = self.c.state()
        state = "recording" if s.recording else "idle"
        phase = "finishing" if not s.recording and s.finishing else state
        return {
            "state": state,
            "phase": phase,
            "finishing": s.busy,
            "paused": s.paused,
            "mic": {"status": self.c.mic_status},
            "server": self.server(),
            "config": {"changed": self.config_changed(), "restart_needed": self.restart_needed},
            "last": None if s.last is None else self._last(*s.last),
            "profile_override": self.c.profile_override,
        }

    def _last(self, at: float, res: Result) -> dict[str, object]:
        return {
            "at": round(at, 3),
            "utterance_id": res.utterance_id,
            "status": res.status,
            "profile": res.profile,
            "keyup_to_paste_ms": res.timings.get("keyup_to_paste_ms"),
            "release_to_paste_ms": res.timings.get("release_to_paste_ms"),
            "failure": failure(res.error) if res.status == "error" else None,
            "stop_reason": res.stop_reason or None,
            "provider": res.provider or None,
            "fallback": res.fallback,
            "cleanup": res.cleanup.status if res.cleanup is not None else None,
            "delivery": res.delivery,
            "retry_available": self._retry_available(res),
            "mic_interrupted": res.mic_interrupted,
            "input_device": res.input_device or None,
            "flat_signal": res.flat_signal,
        }

    def _retry_available(self, res: Result) -> bool:
        cfg = self.c.svc.cfg
        if cfg.retry.keep <= 0 or res.status == "too_short":
            return False
        return (expand(cfg.history.dir) / retry.RING / res.utterance_id).is_dir()
