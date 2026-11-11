"""Daemon controller (what hotkeys and `hark ctl` drive), without a microphone."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import grpc
import pytest

from hark_client import asr
from hark_client.config import Config, FeedbackConfig
from hark_client.daemon import Controller
from hark_client.feedback import Feedback
from hark_client.inject import DryRunInjector
from hark_client.pipeline import LastPaste, Services, join_prefix

from .conftest import snapshot, speech

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from .conftest import FakeContext, HarkServer


def pcm(seconds: float) -> bytes:
    return speech(seconds).astype("<i2").tobytes()


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
        svc = Services(
            cfg, channel, caps, injector, Feedback(FeedbackConfig(sounds=False, notify=False))
        )
        yield Controller(svc, mic=None), injector


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


def test_dictations_into_the_same_window_are_joined_until_enter(
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
    c.typed_enter()
    dictate()
    assert [text for text, _ in injector.pasted] == ["please run", " please run", "please run"]


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


def test_cancel_and_too_short_paste_nothing(controller: tuple[Controller, DryRunInjector]):
    c, injector = controller
    assert c.handle("stop") == "ok idle"
    c.handle("start")
    c.on_pcm(pcm(1.0))
    assert c.handle("cancel") == "ok cancelled"
    c.svc.cfg.audio.min_ms = 10_000
    c.handle("start")
    c.on_pcm(pcm(1.0))
    c.handle("stop")
    wait_idle(c)
    assert injector.pasted == []


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
