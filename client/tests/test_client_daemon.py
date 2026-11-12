"""Daemon controller (what hotkeys and `hark ctl` drive), without a microphone."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from hark_client import retry
from hark_client.audio import speech_ms
from hark_client.config import ProviderConfig
from hark_client.context import CaretContext
from hark_client.continuation import PASTE_ECHO_S, KeyPress
from hark_client.indicator import Indicator
from hark_client.inject import DryRunInjector, PasteResult
from hark_client.providers import Router
from hark_server.backends.mock import MockBackend

from .conftest import HarkServer, RecordingFeedback, make_controller, pcm, snapshot, wait_idle
from .test_client_indicator import FakeBackend

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    import numpy.typing as npt

    from hark_client.daemon import Controller
    from hark_client.mic import Mic

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


class FakeMic:
    device_name, rate = "fake", 16000

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.delivering = True  # callbacks arrive
        self.reopen_fails = False

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

    def healthy(self, max_silence_s: float) -> bool:
        return self.delivering

    def reopen(self) -> None:
        self.calls.append("reopen")
        if self.reopen_fails:
            raise OSError("no input device")
        self.delivering = True


def test_pause_cancels_refuses_new_dictations_and_releases_the_mic(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller
    mic = FakeMic()
    c.mic = cast("Mic", mic)
    hotkey_told: list[bool] = []
    c.on_recording = hotkey_told.append
    c.handle("start")
    c.on_pcm(pcm(0.4))
    c.set_paused(True)
    assert c.status() == "ok idle finishing=0"  # the recording was cancelled
    assert c.handle("start") == "error paused"
    assert c.handle("toggle") == "error paused"
    # a refused hotkey press must not leave the listener in hold/hands-free (Esc swallowed)
    assert hotkey_told == [True, False, False, False]
    c.set_paused(True)  # no-op
    assert mic.calls == ["stop"]  # keep_open: the always-open stream is released
    c.set_paused(False)
    assert mic.calls == ["stop", "start"]
    dictate(c)
    assert injector.pasted == [("please run", True)]


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


def pcm16(signal: npt.NDArray[np.float64]) -> bytes:
    return (signal * 32768).astype("<i2").tobytes()


@pytest.mark.parametrize("start_cue", ["always", "never"])
def test_the_start_cue_is_masked_out_of_the_audio_and_the_speech_gate(
    controller: tuple[Controller, DryRunInjector], start_cue: str
):
    c, _ = controller
    c.svc.cfg.feedback.start_cue = start_cue
    c.svc.cfg.feedback.cue_mask_ms = 250
    cue = tone(200, -10) + noise(200, -60)  # the start sound, picked up by the mic
    speech = tone(150, -20) + noise(150, -60)  # too little speech on its own
    room = noise(50, -60), noise(600, -60)
    c.handle("start")
    assert c.current is not None
    for part in (cue, room[0], speech, room[1]):
        c.on_pcm(pcm16(part))
    heard = np.frombuffer(bytes(c.current.pcm), "<i2")
    c.handle("stop")
    wait_idle(c)
    assert c.last_result is not None
    status = c.last_result[1].status
    feedback = c.svc.feedback
    assert isinstance(feedback, RecordingFeedback)
    if start_cue == "always":
        assert feedback.sounds_played[0] == "start"
        assert not heard[: 250 * 16].any()  # the server never hears the cue
        assert heard[250 * 16 :].any()
        assert status == "no_speech"  # 150 ms of speech; the cue would have made it 350
    else:
        assert "start" not in feedback.sounds_played
        assert status != "no_speech"


def test_auto_start_cue_only_without_the_indicator(
    controller: tuple[Controller, DryRunInjector],
):
    c, _ = controller
    c.svc.cfg.feedback.start_cue = "auto"
    c.handle("start")
    c.handle("cancel")
    c.indicator = Indicator(FakeBackend())
    c.handle("start")
    c.handle("cancel")
    feedback = c.svc.feedback
    assert isinstance(feedback, RecordingFeedback)
    assert feedback.sounds_played == ["start", "error", "error"]


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


def test_a_profile_picked_by_hand_applies_to_the_next_dictation(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller
    c.indicator = ind = Indicator(FakeBackend())
    assert c.handle("profile") == "ok auto"
    assert [c.handle("profile next") for _ in range(4)] == [
        "ok terminal (next dictation)",
        "ok chat (next dictation)",
        "ok prose (next dictation)",
        "ok auto",
    ]
    assert c.handle("profile set nope").startswith("error unknown profile 'nope'")
    assert c.handle("profile sideways") == "error usage: profile [next | auto | set NAME]"
    assert c.handle("start now") == "error start takes no arguments"
    assert c.handle("profile set chat") == "ok chat (next dictation)"
    assert ind.view is not None
    assert ind.view.label == "Profile: chat"
    c.handle("start")
    assert ind.view is not None
    assert (ind.view.glyph, ind.view.pinned) == ("dot", "chat")
    c.hands_free()
    assert (ind.view.glyph, ind.view.pinned) == ("lock", "chat")
    c.on_pcm(pcm(0.4))
    c.handle("stop")
    wait_idle(c)
    dictate(c)  # used up: kitty is a terminal again
    c.svc.cfg.profile_switch.sticky = True
    assert c.handle("profile set chat") == "ok chat"
    dictate(c)
    dictate(c)
    assert [terminal for _, terminal in injector.pasted] == [False, True, False, False]
    assert c.status_json()["profile_override"] == "chat"


def until(cond: Callable[[], bool]) -> None:
    for _ in range(500):
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


class GatedInjector(DryRunInjector):
    """Pastes only once released: a dictation stuck right at the point of no return."""

    def __init__(self) -> None:
        super().__init__(out=lambda _: None)
        self.entered = threading.Event()
        self.release = threading.Event()

    def paste(self, text: str, *, terminal: bool, target: str = "") -> PasteResult:
        self.entered.set()
        self.release.wait(5)
        return super().paste(text, terminal=terminal, target=target)


@pytest.fixture
def slow(tmp_path: Path, fake_context: FakeContext) -> Iterator[tuple[Controller, GatedInjector]]:
    """A controller whose server takes 300 ms per decode and whose pastes wait for release."""
    fake_context.snapshots = [snapshot("kitty")]
    server = HarkServer(backend=MockBackend(decode_ms=300))
    router = Router([ProviderConfig(address=server.target)])
    yield make_controller(tmp_path, router, GatedInjector())
    router.close()
    server.close()


def test_one_dictation_at_a_time_cancellable_until_it_is_pasted(
    slow: tuple[Controller, GatedInjector], tmp_path: Path
):
    c, injector = slow
    hotkey_told: list[bool] = []
    c.on_recording = hotkey_told.append
    finishing: list[bool] = []
    c.on_finishing = finishing.append
    c.handle("start")
    c.on_pcm(pcm(0.6))
    c.handle("stop")
    assert c.status_json()["phase"] == "finishing"
    assert c.handle("start") == "error busy"  # nothing records; the listener is told so
    assert hotkey_told == [True, False, False]
    # the audio is kept for `hark retry` before it is transcribed
    until(lambda: [e.meta.get("status") for e in retry.entries(tmp_path)] == ["finishing"])
    assert c.handle("cancel") == "ok cancelled"
    wait_idle(c)
    assert injector.pasted == []
    assert c.last_result is not None
    assert c.last_result[1].status == "cancelled"
    assert [e.meta["status"] for e in retry.entries(tmp_path)] == ["cancelled"]
    assert finishing == [True, False]

    c.handle("start")
    c.on_pcm(pcm(0.6))
    c.handle("stop")
    assert injector.entered.wait(5)
    assert c.handle("cancel") == "error too late: already pasting"
    injector.release.set()
    wait_idle(c)
    assert len(injector.pasted) == 1
    assert c.last_result[1].status == "pasted"


def test_the_length_limit_stops_only_its_own_recording(
    controller: tuple[Controller, DryRunInjector],
):
    c, _ = controller
    c.svc.cfg.audio.max_ms = 400
    c.handle("start")
    first = c.current
    c.on_pcm(pcm(0.6))  # over the limit: stopped in the background
    wait_idle(c)
    assert c.last_result is not None
    assert c.last_result[1].stop_reason == "limit"
    c.handle("start")
    assert c.stop(reason="limit", only=first) == "ok idle"  # a late stop of the first one
    assert c.status() == "ok recording finishing=0"
    c.handle("cancel")


def test_a_mic_that_stops_delivering_is_reopened_and_the_dictation_flagged(
    controller: tuple[Controller, DryRunInjector],
):
    c, injector = controller
    mic = FakeMic()
    c.mic = cast("Mic", mic)
    c.handle("start")
    c.on_pcm(pcm(0.3))
    mic.delivering = False  # unplugged mid-dictation
    c.check_mic()
    assert (mic.calls, c.mic_status) == (["reopen"], "lost")
    c.check_mic()
    assert c.mic_status == "ok"
    c.on_pcm(pcm(0.3))
    c.handle("stop")
    wait_idle(c)
    assert c.last_result is not None
    assert c.last_result[1].mic_interrupted
    assert len(injector.pasted) == 1  # what was heard is still pasted
    mic.delivering, mic.reopen_fails = False, True
    assert c.handle("start").startswith("error mic unavailable")
    assert c.status_json()["mic"] == {"status": "lost"}
    assert c.status() == "ok idle finishing=0"


def test_reload_applies_per_dictation_settings_and_names_the_rest(
    controller: tuple[Controller, DryRunInjector], tmp_path: Path
):
    c, _ = controller
    path = tmp_path / "config.toml"
    base = f'[history]\ndir = "{tmp_path}"\n[audio]\nmin_ms = 0\n[feedback]\nstart_cue = "never"\n'
    path.write_text(base)
    c.watch_config(path)
    assert c.handle("reload") == "ok reloaded"
    path.write_text(base.replace("min_ms = 0", "min_ms = 5") + '[[words]]\nword = "Hyprland"\n')
    os.utime(path, (time.time() + 5, time.time() + 5))  # mtime resolution
    assert c.status_json()["config"] == {"changed": True, "restart_needed": []}
    assert c.handle("reload") == "ok reloaded; restart for audio"
    assert [w.word for w in c.svc.cfg.words] == ["Hyprland"]  # used by the next dictation
    assert c.svc.cfg.audio.min_ms == 0  # needs a restart
    assert c.status_json()["config"] == {"changed": False, "restart_needed": ["audio"]}
    path.write_text("[[words]]\n")
    assert c.handle("reload").startswith("error words[0].word is required")
    assert [w.word for w in c.svc.cfg.words] == ["Hyprland"]  # the running config stays
