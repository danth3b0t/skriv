"""Daemon controller (what hotkeys and `hark ctl` drive), without a microphone."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING, override

import grpc
import numpy as np
import pytest

from hark_client import asr, daemon
from hark_client.audio import speech_ms
from hark_client.config import Config, FeedbackConfig
from hark_client.daemon import Controller
from hark_client.feedback import Feedback, Sound
from hark_client.inject import DryRunInjector
from hark_client.pipeline import LastPaste, Services, join_prefix

from .conftest import snapshot, speech

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import numpy.typing as npt

    from .conftest import FakeContext, HarkServer


def pcm(seconds: float) -> bytes:
    return speech(seconds).astype("<i2").tobytes()


class RecordingFeedback(Feedback):
    def __init__(self) -> None:
        super().__init__(FeedbackConfig(sounds=False, notify=False))
        self.sounds_played: list[Sound] = []

    @override
    def sound(self, kind: Sound) -> None:
        self.sounds_played.append(kind)


@pytest.fixture
def controller(
    tmp_path: Path, hark_server: HarkServer, fake_context: FakeContext
) -> Iterator[tuple[Controller, DryRunInjector]]:
    fake_context.snapshots = [snapshot("kitty")]
    cfg = Config()
    cfg.history.dir = str(tmp_path)
    cfg.audio.min_ms = 0
    injector = DryRunInjector(out=lambda _: None)
    with grpc.insecure_channel(hark_server.target) as channel:
        caps = asr.Capabilities(channel)
        yield (
            Controller(Services(cfg, channel, caps, injector, RecordingFeedback()), None),
            injector,
        )


def wait_idle(c: Controller) -> None:
    for _ in range(200):
        if c.status() == "ok idle finishing=0":
            return
        time.sleep(0.01)
    raise AssertionError(c.status())


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
