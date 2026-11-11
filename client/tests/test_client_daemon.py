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

from hark_client.audio import speech_ms
from hark_client.context import CaretContext
from hark_client.continuation import PASTE_ECHO_S, KeyPress
from hark_client.fields import FieldText
from hark_client.inject import DryRunInjector
from hark_client.learn import LearnedStore, Learner

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


def dictate(c: Controller) -> None:
    c.handle("start")
    c.on_pcm(pcm(0.4))
    c.handle("stop")
    wait_idle(c)


def typed(c: Controller, *keys: str, mods: frozenset[str] = frozenset()) -> None:
    for k in keys:
        c.user_input(KeyPress("char", k, mods) if len(k) == 1 else KeyPress(k, "", mods))


def test_without_a_readable_caret_spacing_follows_the_shadow_of_typed_keys(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller
    dictate(c)  # nothing known before: field start
    typed(c, "x", "y", "backspace", "backspace")  # back to the end of the paste
    dictate(c)
    typed(c, "enter")
    dictate(c)
    typed(c, *["backspace"] * len("please run"))  # deletes everything again
    dictate(c)
    assert [text for text, _ in injector.pasted] == [
        "please run",
        " please run",
        "please run",
        "please run",
    ]


def test_hark_own_paste_chord_is_not_a_user_key(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller
    dictate(c)
    typed(c, "v", mods=frozenset({"cmd"}))  # the listener sees hark's Cmd+V right after
    dictate(c)
    time.sleep(PASTE_ECHO_S)
    typed(c, "v", mods=frozenset({"cmd"}))  # the user's own paste: unknown text
    dictate(c)
    assert [text for text, _ in injector.pasted] == ["please run", " please run", "please run"]


def test_trusted_caret_wins_over_the_shadow_and_is_recorded(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext, tmp_path: Path
):
    c, injector = controller
    fake_context.caret = CaretContext("we went to the", "zoo", "ax", trusted=True)
    dictate(c)
    assert injector.pasted == [(" please run ", True)]
    line = (tmp_path / "history.jsonl").read_text().splitlines()[-1]
    record = cast("dict[str, object]", json.loads(line))
    assert record["spacing"] == {
        "before_repr": "'we went to the'",
        "after_repr": "'zoo'",
        "source": "ax",
        "detail": "",
        "leading": True,
        "trailing": True,
        "lowercased": False,
    }


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


def test_paste_last_reinserts_the_last_text_guarded_for_the_focused_window(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, injector = controller
    assert c.handle("paste-last") == "error nothing to paste yet"
    dictate(c)  # into kitty, a terminal
    assert c.handle("paste-last") == "ok pasted 10 chars"
    assert injector.pasted[-1] == ("please run", True)
    c.last_text = "two\nlines"  # e.g. a prose result, now going into the terminal
    assert c.handle("paste-last") == "ok pasted 9 chars"
    assert injector.pasted[-1] == ("two lines", True)
    c.last_text = ""  # a restarted daemon reads the history
    fake_context.snapshots = [snapshot("com.apple.Notes", "w2")]
    c.handle("paste-last")
    assert injector.pasted[-1] == ("please run", False)


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


def test_a_paste_missing_from_the_field_is_recorded_only(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, injector = controller
    fake_context.snapshots = [snapshot("com.apple.Notes")]
    c.svc.field_reader = EditableField(DryRunInjector(out=lambda _: None))  # never shows the paste
    c.handle("start")
    c.on_pcm(pcm(0.4))
    c.handle("stop")
    wait_idle(c)
    assert len(injector.pasted) == 1
    assert injector.copied == []  # the restored clipboard is left alone
    feedback = c.svc.feedback
    assert isinstance(feedback, RecordingFeedback)
    assert feedback.notifications == []
    last = cast("dict[str, object]", c.status_json()["last"])
    assert (last["status"], last["paste_verdict"]) == ("pasted", "missing")


def test_keys_typed_after_a_terminal_paste_feed_the_learner(
    tmp_path: Path, controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, injector = controller
    fake_context.snapshots = [snapshot("kitty", terms=())]
    c.svc.learner = Learner(LearnedStore(tmp_path / "learned.json"), None, window_s=5)
    c.handle("start")
    c.on_pcm(pcm(1.0))
    c.handle("stop")
    wait_idle(c)
    ((pasted, terminal),) = injector.pasted
    assert terminal
    assert c.svc.learner.watching
    typed(c, "v", mods=frozenset({"ctrl", "shift"}))  # hark's own paste chord, right after
    tail = pasted[pasted.index("cube control") + len("cube control") :]
    typed(c, *["left"] * len(tail))
    typed(c, "backspace", "backspace", mods=frozenset({"alt"}))
    typed(c, *"kubectl", "enter")
    c.svc.learner.join(2)
    assert [(e.heard, e.written) for e in c.svc.learner.store.entries()] == [
        ("cube control", "kubectl")
    ]
