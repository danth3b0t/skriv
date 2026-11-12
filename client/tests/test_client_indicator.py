"""Recording indicator: state machine (fake backend + clock), daemon hooks, macOS panel focus safety."""
# pyright: reportAny=false, reportExplicitAny=false

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from hark_client.indicator import Indicator, View

from .conftest import pcm, snapshot, wait_idle

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from hark_client.daemon import Controller
    from hark_client.inject import DryRunInjector

    from .conftest import FakeContext


class FakeBackend:
    def __init__(self) -> None:
        self.rendered: list[View | None] = []

    def render(self, view: View | None) -> None:
        self.rendered.append(view)

    def run(self, stop: threading.Event, tick: Callable[[], None]) -> None:
        raise AssertionError("not used")

    def states(self) -> list[str | None]:
        """Rendered states, consecutive repeats (level redraws) collapsed."""
        out: list[str | None] = []
        for v in self.rendered:
            s = None if v is None else v.state
            if not out or out[-1] != s:
                out.append(s)
        return out


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def make(*, retry_hint: bool = False) -> tuple[Indicator, FakeBackend, Clock]:
    backend, clock = FakeBackend(), Clock()
    return Indicator(backend, retry_hint=retry_hint, clock=clock), backend, clock


def test_final_state_lingers_then_hides():
    ind, backend, clock = make()
    ind.recording("a")
    ind.stage("a", "transcribing")
    ind.finished("a", "pasted")
    clock.t += 0.7
    ind.tick()
    assert ind.view == View("done", "Pasted")
    clock.t += 0.2
    ind.tick()
    assert ind.view is None
    assert backend.states() == ["recording", "transcribing", "done", None]
    with pytest.raises(ValueError, match="unknown indicator stage"):
        ind.stage("a", "pasting")


def test_a_new_recording_wins_over_an_older_dictation_still_finishing():
    ind, _, clock = make(retry_hint=True)
    ind.recording("a")
    ind.stage("a", "transcribing")
    ind.recording("b")
    ind.stage("a", "cleaning")
    ind.finished("a", "pasted")
    assert ind.view is not None
    assert ind.view.state == "recording"
    clock.t += 10
    ind.tick()  # nothing scheduled to hide while recording
    assert ind.view is not None
    ind.finished("b", "error")
    assert ind.view == View("error", "Failed: hark retry")
    ind.finished("a", "cancelled")  # b is final: a late update may show
    assert ind.view == View("cancelled", "Cancelled")


def test_input_level_is_scaled_and_rate_limited():
    ind, backend, clock = make()
    loud = np.full(320, 32767, dtype="<i2").tobytes()  # 0 dBFS
    ind.level(loud)  # not recording: ignored
    assert backend.rendered == []
    ind.recording("a")
    ind.level(loud)
    ind.level(loud)  # same instant: dropped
    clock.t += 0.06
    ind.level(np.zeros(320, dtype="<i2").tobytes())
    levels = [v.level for v in backend.rendered if v is not None]
    assert levels == [0.0, pytest.approx(1.0, abs=1e-4), 0.0]


def test_daemon_drives_the_indicator_through_a_dictation_and_a_cancel(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, _ = controller
    fake_context.snapshots = [snapshot("com.apple.Notes")]  # prose: cleaned up
    c.svc.router.refresh()  # learns the server's cleanup profiles
    backend = FakeBackend()
    c.indicator = ind = Indicator(backend)
    c.svc.on_stage = ind.stage
    c.handle("start")
    c.on_pcm(pcm(0.6))
    c.handle("stop")
    wait_idle(c)
    assert backend.states() == ["recording", "transcribing", "cleaning", "done"]
    assert any(v is not None and v.level > 0 for v in backend.rendered)
    c.handle("start")
    c.handle("cancel")
    assert ind.view == View("cancelled", "Cancelled")


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit panel")
def test_macos_panel_cannot_take_focus():
    import importlib  # noqa: PLC0415

    from hark_client.indicator import macos  # noqa: PLC0415

    appkit: Any = importlib.import_module("AppKit")
    appkit.NSApplication.sharedApplication().setActivationPolicy_(
        appkit.NSApplicationActivationPolicyAccessory
    )
    panel = macos.make_panel()
    assert panel.styleMask() & appkit.NSWindowStyleMaskNonactivatingPanel
    assert panel.ignoresMouseEvents()
    assert not panel.canBecomeKeyWindow()
    assert not panel.canBecomeMainWindow()
    assert not panel.hidesOnDeactivate()
    assert macos.is_passive(panel)
    panel.setStyleMask_(appkit.NSWindowStyleMaskTitled)  # an ordinary window: rejected
    assert not macos.is_passive(panel)
