"""Recovery ring (`retry.keep`) and `hark retry`."""

from __future__ import annotations

import json
import socket
import stat
from typing import TYPE_CHECKING, cast

import pytest

from hark_client import retry
from hark_client.__main__ import main
from hark_client.config import Config, ProviderConfig
from hark_client.context import ContextSnapshot, FocusInfo
from hark_client.daemon import Controller
from hark_client.inject import DryRunInjector
from hark_client.pipeline import Services
from hark_client.providers import Router

from .conftest import KUBECTL, RecordingFeedback, pcm, snapshot, wait_idle

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import FakeContext, HarkServer


def dictate(c: Controller, seconds: float = 0.6) -> None:
    c.handle("start")
    c.on_pcm(pcm(seconds))
    c.handle("stop")
    wait_idle(c)


def history_ids(base: Path) -> list[str]:
    lines = (base / "history.jsonl").read_text().splitlines()
    return [cast("str", json.loads(line)["utterance_id"]) for line in lines]


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_ring_keeps_the_newest_n_privately_and_purges_the_rest(
    tmp_path: Path, controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, _ = controller
    focus = FocusInfo(app="kitty", app_id="kitty", title="secret title", window_id="w1")
    fake_context.snapshots = [ContextSnapshot(focus=focus, selection="secret", terms=(KUBECTL,))]
    c.svc.cfg.retry.keep = 2
    for _ in range(3):
        dictate(c)
    ring = tmp_path / "retry"
    ids = history_ids(tmp_path)
    assert sorted(p.name for p in ring.iterdir()) == ids[1:]
    assert [e.id for e in retry.entries(tmp_path)] == ids[:0:-1]  # newest first
    newest = ring / ids[2]
    assert {p.name: mode(p) for p in newest.iterdir()} == dict.fromkeys(
        ("audio.wav", "context.json", "meta.json"), 0o600
    )
    assert (mode(ring), mode(newest)) == (0o700, 0o700)
    stored = (newest / "context.json").read_text()
    assert "secret" not in stored
    assert retry.load_snapshot(retry.find(tmp_path, ids[2][-8:])).terms == (KUBECTL,)
    assert json.loads((newest / "meta.json").read_text())["status"] == "pasted"

    c.svc.cfg.retry.keep = 0
    dictate(c)
    assert list(ring.iterdir()) == []
    with pytest.raises(LookupError, match=r"retry\.keep = 0"):
        retry.find(tmp_path, "last")


def test_failed_dictation_is_retried_with_its_original_context(
    tmp_path: Path,
    hark_server: HarkServer,
    fake_context: FakeContext,
    capsys: pytest.CaptureFixture[str],
):
    """Server down at dictation time -> audio kept, notification points at `hark retry`; the
    retry runs against the live server with the key-down context (bias terms), and copies
    because another window has focus now."""
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = cast("tuple[str, int]", s.getsockname())[1]
    cfg = Config()
    cfg.history.dir = str(tmp_path)
    cfg.audio.min_ms = 0
    cfg.feedback.start_cue = "never"
    fake_context.snapshots = [snapshot("com.apple.Notes", "w1")]
    feedback = RecordingFeedback()
    router = Router([ProviderConfig(address=f"127.0.0.1:{port}")])
    svc = Services(cfg, router, DryRunInjector(out=lambda _: None), feedback)
    dictate(Controller(svc, None), 1.0)  # 5 mock words: the 3rd+4th are "cube control"
    router.close()
    assert any("`hark retry`" in n for n in feedback.notifications)
    failed_id = history_ids(tmp_path)[0]

    # now: another window, no context terms; the retry must not use either
    fake_context.snapshots = [snapshot("com.apple.TextEdit", "w9", terms=())]
    fake_context.calls.clear()
    config = tmp_path / "config.toml"
    config.write_text(f'[history]\ndir = "{tmp_path}"\n')
    capsys.readouterr()
    code = main(["--config", str(config), "--server", hark_server.target, "retry", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert f"retry     {failed_id} (was error: asr:" in out
    assert "[dry-run] would copy: 'please run kubectl get'" in out  # biased, not pasted
    assert fake_context.calls == []
    records = (tmp_path / "history.jsonl").read_text().splitlines()
    assert [json.loads(r)["status"] for r in records] == ["error", "focus_changed"]


def test_retry_without_a_known_window_copies_and_bad_ids_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    config = tmp_path / "config.toml"
    config.write_text(f'[history]\ndir = "{tmp_path}"\n')
    argv = ["--config", str(config), "--server", "127.0.0.1:1", "retry"]
    assert main(argv) == 1
    assert "no kept recordings" in capsys.readouterr().err
    for uid in ("0199aaaa-0000-7000-8000-00000000abcd", "0199aaaa-0000-7000-8000-00000000abce"):
        retry.save(tmp_path, 10, pcm(0.1), ContextSnapshot(), {"utterance_id": uid})
    assert main([*argv, "zzzz"]) == 1
    assert "no kept recording matches 'zzzz'" in capsys.readouterr().err
    assert main([*argv, "0000000ab"]) == 1
    assert "matches 2 recordings" in capsys.readouterr().err
