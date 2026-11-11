"""Daemon controller (what hotkeys and `hark ctl` drive), without a microphone."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from hark_client import daemon
from hark_client.audio import speech_ms
from hark_client.fields import FieldText
from hark_client.inject import DryRunInjector
from hark_client.learn import LearnedStore, Learner
from hark_client.pipeline import LastPaste, join_prefix

from .conftest import RecordingFeedback, pcm, snapshot, wait_idle

if TYPE_CHECKING:
    from pathlib import Path

    import numpy.typing as npt

    from hark_client.context import FocusInfo
    from hark_client.daemon import Controller

    from .conftest import FakeContext


def test_toggle_streams_preroll_and_speech_then_pastes(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller
    c.on_pcm(pcm(0.4))  # before the key: only the last 200 ms are kept
    assert c.handle("toggle") == "ok recording"
    assert c.handle("start") == "ok already recording"
    c.on_pcm(pcm(0.8))
    assert c.handle("toggle") == "ok stopped"
    wait_idle(c)
    # 200 ms pre-roll + 800 ms = 5 mock words; 1.2 s would have produced 6.
    assert injector.pasted == [("please run kubectl get", True)]


def test_dictations_into_the_same_window_are_joined_until_the_user_types(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller

    def dictate() -> None:
        c.handle("start")
        c.on_pcm(pcm(0.4))
        c.handle("stop")
        wait_idle(c)

    dictate()
    dictate()
    time.sleep(daemon.PASTE_ECHO_S)  # past the echo of hark's own paste chord
    c.user_input()  # e.g. Enter, Backspace or a click into an empty field
    dictate()
    assert [text for text, _ in injector.pasted] == ["please run", " please run", "please run"]


def test_hark_own_paste_keystroke_does_not_reset_join(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller

    def dictate() -> None:
        c.handle("start")
        c.on_pcm(pcm(0.4))
        c.handle("stop")
        wait_idle(c)

    dictate()
    c.user_input()  # the listener sees hark's synthetic Cmd+V right after the paste
    dictate()
    assert [text for text, _ in injector.pasted] == ["please run", " please run"]


@pytest.mark.parametrize(
    ("prev", "target", "text", "now", "expected"),
    [
        (LastPaste(("kitty", "w1"), 0.0, "."), ("kitty", "w1"), "Next", 60.0, " "),
        (LastPaste(("kitty", "w1"), 0.0, "."), ("kitty", "w1"), "Next", 60.1, ""),  # too late
        (LastPaste(("kitty", "w1"), 0.0, "."), ("kitty", "w2"), "Next", 1.0, ""),  # other window
        (LastPaste(("kitty", "w1"), 0.0, "."), None, "Next", 1.0, ""),  # unknown target
        (LastPaste(("kitty", "w1"), 0.0, " "), ("kitty", "w1"), "Next", 1.0, ""),
        (LastPaste(("kitty", "w1"), 0.0, "\n"), ("kitty", "w1"), "Next", 1.0, ""),
        (LastPaste(("kitty", "w1"), 0.0, "a"), ("kitty", "w1"), ", then", 1.0, ""),
        (LastPaste(("kitty", "w1"), 0.0, "a"), ("kitty", "w1"), " x", 1.0, ""),
        (None, ("kitty", "w1"), "Next", 1.0, ""),
    ],
)
def test_join_prefix(
    prev: LastPaste | None, target: tuple[str, str] | None, text: str, now: float, expected: str
):
    assert join_prefix(prev, target, text, now, window_s=60.0) == expected


def test_cancelled_too_short_and_silent_dictations_paste_nothing(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller
    feedback = c.svc.feedback
    assert isinstance(feedback, RecordingFeedback)
    assert c.handle("stop") == "ok idle"
    c.handle("start")
    c.on_pcm(pcm(1.0))
    assert c.handle("cancel") == "ok cancelled"
    c.handle("start")  # 2 s held, but only 200 ms of it is speech
    c.on_pcm(bytes(32000) + pcm(0.2) + bytes(32000))
    c.handle("stop")
    wait_idle(c)
    c.svc.cfg.audio.min_ms = 10_000
    c.handle("start")
    c.on_pcm(pcm(1.0))
    c.handle("stop")
    wait_idle(c)
    assert injector.pasted == []
    assert [s for s in feedback.sounds_played if s not in {"start", "stop"}] == [
        "error",  # Esc
        "cancel",  # no speech
        "cancel",  # too short
    ]


def tone(ms: int, dbfs: float) -> npt.NDArray[np.float64]:
    t = np.arange(ms * 16, dtype=np.float64) / 16000
    return np.sin(2 * np.pi * 220 * t) * (math.sqrt(2) * 10 ** (dbfs / 20))


def noise(ms: int, dbfs: float) -> npt.NDArray[np.float64]:
    return np.random.default_rng(1).standard_normal(ms * 16) * 10 ** (dbfs / 20)


@pytest.mark.parametrize(
    ("signal", "expected_ms"),
    [
        (np.zeros(16000), 0),
        (noise(1000, -60), 0),
        (noise(1000, -45), 0),  # noisy room, no speech
        (np.concatenate([noise(500, -60), tone(280, -20) + noise(280, -60), noise(500, -60)]), 280),
        (np.concatenate([noise(500, -65), tone(400, -45) + noise(400, -65), noise(500, -65)]), 400),
        (tone(1000, -25), 1000),  # speech throughout: no quiet frames to take a floor from
    ],
)
def test_speech_ms(signal: npt.NDArray[np.float64], expected_ms: int):
    assert speech_ms((signal * 32768).astype("<i2").tobytes()) == expected_ms


def test_forks_do_not_run_grpc_fork_handlers():
    # grpcio's fork handlers paused the gRPC threads on every fork (afplay for the
    # key-up sound), holding EndOfAudio back for up to ~2 s. With a call in flight,
    # active handlers log "Other threads are currently calling into gRPC, skipping
    # fork() handlers"; disabled ones are never registered.
    script = (
        "import subprocess, time, hark_client, grpc\n"
        "ch = grpc.insecure_channel('127.0.0.1:9')\n"
        "call = ch.unary_unary('/x.Y/Z').future(b'', wait_for_ready=True, timeout=5)\n"
        "time.sleep(0.05)\n"
        "subprocess.run(['true'], check=True)\n"
        "call.cancel()\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "GRPC_ENABLE_FORK_SUPPORT"}
    env["GRPC_VERBOSITY"] = "INFO"
    out = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
    )
    assert "fork" not in out.stderr, out.stderr


class EditableField:
    """The target app's focused field: shows whatever was pasted after `before`, then `edit()`s."""

    def __init__(self, injector: DryRunInjector, before: str = "Dear Ann, ") -> None:
        self.injector = injector
        self.before = before
        self.edited: str | None = None

    def read(self, focus: FocusInfo) -> FieldText:
        if self.edited is not None:
            return FieldText("text", self.edited)
        return FieldText("text", self.before + "".join(t for t, _ in self.injector.pasted))


def test_paste_read_back_feeds_the_learner(
    tmp_path: Path, controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, injector = controller
    fake_context.snapshots = [snapshot("com.apple.Notes", terms=())]
    field = EditableField(injector)
    c.svc.field_reader = field
    c.svc.learner = Learner(LearnedStore(tmp_path / "learned.json"), field, window_s=5, poll_s=0.02)
    c.handle("start")
    c.on_pcm(pcm(1.0))  # no bias term: the mock says "please run cube control ..."
    c.handle("stop")
    wait_idle(c)
    ((pasted, _),) = injector.pasted
    assert "cube control" in pasted
    assert c.svc.learner.watching
    field.edited = "Dear Ann, " + pasted.replace("cube control", "kubectl")
    time.sleep(0.1)
    c.handle("start")  # the next dictation ends the watch
    c.handle("cancel")
    c.svc.learner.join(2)
    assert [(e.heard, e.written) for e in c.svc.learner.store.entries()] == [
        ("cube control", "kubectl")
    ]
    status = c.status_json()
    assert cast("dict[str, object]", status["last"])["paste_verdict"] == "landed"
    record = cast(
        "dict[str, object]", json.loads((tmp_path / "history.jsonl").read_text().splitlines()[0])
    )
    assert record["paste_verdict"] == "landed"
    assert "kubectl" not in json.dumps(record)  # the edit itself is never written to history


def test_a_paste_missing_from_the_field_goes_back_to_the_clipboard(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, injector = controller
    fake_context.snapshots = [snapshot("com.apple.Notes")]
    c.svc.field_reader = EditableField(DryRunInjector(out=lambda _: None))  # never shows it
    c.handle("start")
    c.on_pcm(pcm(0.4))
    c.handle("stop")
    wait_idle(c)
    ((pasted, _),) = injector.pasted
    assert injector.copied == [pasted]
    feedback = c.svc.feedback
    assert isinstance(feedback, RecordingFeedback)
    assert any("did not show up" in n for n in feedback.notifications)
    last = cast("dict[str, object]", c.status_json()["last"])
    assert (last["status"], last["paste_verdict"]) == ("pasted", "missing")
