"""Hark.app menu bar: which state icon the daemon's state maps to (no status item is created)."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

import pytest

from skriv_client.pipeline import Result

from .conftest import pcm

if TYPE_CHECKING:
    from skriv_client.daemon import Controller
    from skriv_client.inject import DryRunInjector

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="AppKit")


def test_state_icon_follows_pause_recording_and_a_recent_failure(
    controller: tuple[Controller, DryRunInjector],
):
    from skriv_client.menubar import menu_state, preview  # noqa: PLC0415 - AppKit

    c, _ = controller
    now = time.time()
    assert menu_state(c, now) == "idle"
    c.handle("start")
    c.on_pcm(pcm(0.1))
    assert menu_state(c, now) == "recording"
    c.set_paused(True)  # cancels the recording
    assert menu_state(c, now) == "paused"
    c.set_paused(False)
    c.last_result = (now, Result("u1", "error"))
    assert menu_state(c, now + 9) == "error"
    assert menu_state(c, now + 11) == "idle"  # the warning icon does not stay forever
    c.last_result = (now, Result("u2", "no_speech", flat_signal=True))  # the mic sent nothing
    assert menu_state(c, now) == "error"
    c.last_result = (now, Result("u3", "no_speech"))  # silence is no failure
    assert menu_state(c, now) == "idle"
    c.last_result = (now, Result("u4", "pasted"))
    assert menu_state(c, now) == "idle"
    assert preview("a\nb  c") == "a b c"
    assert preview("x" * 50, 10) == "x" * 9 + "…"
