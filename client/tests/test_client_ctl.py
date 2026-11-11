"""Control socket protocol (`hark ctl`)."""

from __future__ import annotations

import shutil
import socket
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from hark_client import ctl
from hark_client.__main__ import main

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def sock_path() -> Iterator[Path]:
    # AF_UNIX paths are limited to ~104 bytes; pytest's tmp_path on macOS is too long.
    d = Path(tempfile.mkdtemp(prefix="hark-", dir="/tmp"))
    yield d / "run" / "ctl.sock"
    shutil.rmtree(d)


def raw_exchange(path: Path, payload: bytes) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(str(path))
        s.sendall(payload)
        return s.recv(4096).decode().strip()


def test_ctl_round_trip_and_errors(sock_path: Path):
    received: list[str] = []

    def handler(cmd: str) -> str:
        received.append(cmd)
        if cmd == "cancel":
            raise RuntimeError("boom")
        return f"ok {cmd}"

    server = ctl.CtlServer(sock_path, handler)
    server.start()
    try:
        assert ctl.send("toggle", sock_path) == "ok toggle"
        assert ctl.send("cancel", sock_path) == "error RuntimeError: boom"
        assert ctl.send("reboot", sock_path).startswith("error unknown command 'reboot'")
        assert raw_exchange(sock_path, b"x" * 100 + b"\n") == "error command too long"
        assert received == ["toggle", "cancel"]  # invalid commands never reach the handler
        assert stat.S_IMODE(sock_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(sock_path.parent.stat().st_mode) == 0o700
        with pytest.raises(ctl.DaemonRunningError):
            ctl.CtlServer(sock_path, handler)
    finally:
        server.close()
    assert not sock_path.exists()


def test_stale_socket_is_replaced(sock_path: Path):
    sock_path.parent.mkdir(mode=0o700)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.bind(str(sock_path))  # bound but never listening: a crashed daemon's leftover
    server = ctl.CtlServer(sock_path, lambda cmd: f"ok {cmd}")
    server.start()
    try:
        assert ctl.send("ping", sock_path) == "ok ping"
    finally:
        server.close()


def test_hark_ctl_exit_codes(
    sock_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(ctl, "socket_path", lambda: sock_path)
    assert main(["ctl", "start"]) == 2  # no daemon
    assert "hark run" in capsys.readouterr().err
    server = ctl.CtlServer(
        sock_path, lambda cmd: "ok recording" if cmd == "start" else "error nope"
    )
    server.start()
    try:
        assert main(["ctl", "start"]) == 0
        assert main(["ctl", "stop"]) == 1
        assert capsys.readouterr().out.split("\n")[:2] == ["ok recording", "error nope"]
    finally:
        server.close()
