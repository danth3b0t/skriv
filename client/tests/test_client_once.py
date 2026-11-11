"""`hark once` end to end: WAV -> in-process mock server (+ fake cleanup LLM) -> dry-run paste."""

from __future__ import annotations

import json
import socket
import time
from typing import TYPE_CHECKING, cast

import pytest

from hark_client import context
from hark_client.__main__ import main
from hark_client.context import ContextSnapshot

from .conftest import edit, snapshot, write_wav

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import FakeContext, FakeLLM, HarkServer


def run_once(
    tmp_path: Path,
    server: str,
    *extra: str,
    cleanup_deadline_ms: int = 2000,
    wav_rate: int = 16000,
    channels: int = 1,
) -> tuple[int, dict[str, object]]:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[history]\ndir = "{tmp_path / "data"}"\n'
        f"[cleanup]\ndeadline_ms = {cleanup_deadline_ms}\n"
        "[server]\nfinal_timeout_ms = 3000\n"
    )
    wav = write_wav(tmp_path / "in.wav", 1.0, rate=wav_rate, channels=channels)
    code = main(
        ["--config", str(cfg), "--server", server, "once", "--wav", str(wav), "--dry-run", *extra]
    )
    lines = (tmp_path / "data" / "history.jsonl").read_text().splitlines()
    assert len(lines) == 1
    return code, cast("dict[str, object]", json.loads(lines[0]))


def test_prose_dictation_is_biased_cleaned_and_pasted(
    tmp_path: Path,
    hark_server: HarkServer,
    fake_llm: FakeLLM,
    fake_context: FakeContext,
    capsys: pytest.CaptureFixture[str],
):
    fake_context.snapshots = [snapshot("com.apple.Notes")]
    # The server drops edit sets that change more than max(8 bytes, 15%) of the text.
    fake_llm.respond([edit("please", "Please", "capitalization")])

    # 48 kHz stereo input exercises the resampler; 1 s = 5 mock words.
    code, rec = run_once(tmp_path, hark_server.target, wav_rate=48000, channels=2)

    assert code == 0
    out = capsys.readouterr().out
    assert "[dry-run] would paste: 'Please run kubectl get'" in out
    # The mock server only rewrites "cube control" when the bias term reached it.
    assert rec["raw_text"] == "please run kubectl get"
    assert rec["final_text"] == "Please run kubectl get"
    assert (rec["status"], rec["profile"], rec["bias_terms_sent"]) == ("pasted", "prose", 1)
    assert cast("dict[str, object]", rec["cleanup"])["status"] == "applied"
    timings = cast("dict[str, float]", rec["timings_ms"])
    for stage in (
        "context_ms",
        "session_start_ms",
        "keyup_to_final_ms",
        "cleanup_ms",
        "paste_ms",
        "keyup_to_paste_ms",
    ):
        assert stage in timings
    # Identifiers from the shortlist were sent as protected spans.
    assert fake_llm.user_payload()["protected"] == ["kubectl"]


def test_terminal_app_gets_terminal_profile_and_terminal_cleanup(
    tmp_path: Path,
    hark_server: HarkServer,
    fake_llm: FakeLLM,
    fake_context: FakeContext,
    capsys: pytest.CaptureFixture[str],
):
    fake_context.snapshots = [snapshot("com.mitchellh.ghostty")]

    code, rec = run_once(tmp_path, hark_server.target)

    assert code == 0
    assert "[dry-run] would terminal paste: 'please run kubectl get'" in capsys.readouterr().out
    assert rec["profile"] == "terminal"
    assert len(fake_llm.requests) == 1
    messages = cast("list[dict[str, str]]", fake_llm.requests[0]["messages"])
    assert 'Profile "terminal"' in messages[0]["content"]


@pytest.mark.parametrize(
    ("profile", "pasted"), [("terminal", "open canon.py"), ("prose", "open canon dot py")]
)
def test_spoken_symbols_are_written_in_the_terminal_profile_only(
    tmp_path: Path,
    hark_server: HarkServer,
    fake_llm: FakeLLM,
    fake_context: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    pasted: str,
):
    def canonicalize(text: str, words: object, terms: object) -> tuple[str, list[object]]:
        return "open canon dot py", []

    monkeypatch.setattr(context, "canonicalize", canonicalize)
    fake_context.snapshots = [snapshot("com.mitchellh.ghostty")]
    code, rec = run_once(tmp_path, hark_server.target, "--profile", profile)
    assert (code, rec["final_text"]) == (0, pasted)


def test_cleanup_timeout_falls_back_to_canonical_text(
    tmp_path: Path, hark_server: HarkServer, fake_llm: FakeLLM, fake_context: FakeContext
):
    fake_context.snapshots = [snapshot()]
    fake_llm.respond([edit("please run", "Please run", "capitalization")], delay=1.0)

    t0 = time.perf_counter()
    code, rec = run_once(tmp_path, hark_server.target, cleanup_deadline_ms=200)

    assert code == 0
    assert rec["final_text"] == "please run kubectl get"
    assert cast("dict[str, object]", rec["cleanup"])["status"] == "timeout"
    assert time.perf_counter() - t0 < 1.0


def test_slow_context_does_not_hold_back_the_session(
    tmp_path: Path, hark_server: HarkServer, fake_context: FakeContext
):
    fake_context.snapshots = [snapshot()]
    fake_context.delay_s = 0.6  # default deadline is 150 ms

    code, rec = run_once(tmp_path, hark_server.target, "--profile", "terminal")

    assert code == 0
    timings = cast("dict[str, float]", rec["timings_ms"])
    assert timings["session_start_ms"] < 400
    assert rec["bias_terms_sent"] == 0  # sent with whatever was ready: nothing
    assert rec["raw_text"] == "please run cube control get"


def test_focus_change_copies_instead_of_pasting(
    tmp_path: Path,
    hark_server: HarkServer,
    fake_context: FakeContext,
    capsys: pytest.CaptureFixture[str],
):
    fake_context.snapshots = [
        snapshot(window_id="w1"),
        snapshot("com.tinyspeck.slackmacgap", window_id="w2"),
    ]

    code, rec = run_once(tmp_path, hark_server.target, "--profile", "terminal")

    assert code == 0
    out = capsys.readouterr().out
    assert "would copy: 'please run kubectl get'" in out
    assert "would terminal paste" not in out
    assert rec["status"] == "focus_changed"


def test_unreachable_server_fails_without_pasting(
    tmp_path: Path, fake_context: FakeContext, capsys: pytest.CaptureFixture[str]
):
    fake_context.snapshots = [ContextSnapshot()]
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = cast("tuple[str, int]", s.getsockname())[1]

    code, rec = run_once(tmp_path, f"127.0.0.1:{port}")

    assert code == 1
    assert rec["status"] == "error"
    assert "UNAVAILABLE" in cast("str", rec["error"])
    assert "[dry-run]" not in capsys.readouterr().out
