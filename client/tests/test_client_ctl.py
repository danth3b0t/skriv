"""Control socket protocol (`skriv ctl`)."""

from __future__ import annotations

import json
import shutil
import socket
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from skriv_client import ctl
from skriv_client.__main__ import main

from .conftest import pcm, wait_idle

if TYPE_CHECKING:
    from collections.abc import Iterator

    from skriv_client.daemon import Controller
    from skriv_client.inject import DryRunInjector


@pytest.fixture
def sock_path() -> Iterator[Path]:
    # AF_UNIX paths are limited to ~104 bytes; pytest's tmp_path on macOS is too long.
    d = Path(tempfile.mkdtemp(prefix="skriv-", dir="/tmp"))
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
        assert raw_exchange(sock_path, b"x" * 600 + b"\n") == "error command too long"
        assert received == ["toggle", "cancel"]  # invalid commands never reach the handler
        assert stat.S_IMODE(sock_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(sock_path.parent.stat().st_mode) == 0o700
        with pytest.raises(ctl.DaemonRunningError):
            ctl.CtlServer(sock_path, handler)
    finally:
        server.close()
    assert not sock_path.exists()


def _json(path: Path, payload: object) -> dict[str, object]:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return cast("dict[str, object]", json.loads(raw_exchange(path, raw + b"\n")))


def _shape(obj: object) -> object:
    """Keys and value types, recursively: what the contract pins (values vary)."""
    if isinstance(obj, dict):
        return {k: _shape(v) for k, v in cast("dict[str, object]", obj).items()}
    return type(obj).__name__


V1_ERROR = {"version": "int", "ok": "bool", "error": {"code": "str", "message": "str"}}
V1_ACTION = {"version": "int", "ok": "bool", "command": "str", "result": "str"}
V1_LAST = {
    "at": "float",
    "utterance_id": "str",
    "status": "str",
    "profile": "str",
    "keyup_to_paste_ms": "float",
    "release_to_paste_ms": "float",
    "failure": "NoneType",
    "stop_reason": "str",
    "provider": "str",
    "fallback": "bool",
    "cleanup": "str",
    "delivery": "str",
    "retry_available": "bool",
    "mic_interrupted": "bool",
    "input_device": "NoneType",
    "flat_signal": "bool",
}


def v1_status(last: object, rtt: str = "float") -> dict[str, object]:
    return {
        "version": "int",
        "ok": "bool",
        "command": "str",
        "state": "str",
        "phase": "str",
        "finishing": "int",
        "paused": "bool",
        "mic": {"status": "str"},
        "server": {"name": "str", "state": "str", "rtt_ms": rtt, "fallback": "bool"},
        "config": {"changed": "bool", "restart_needed": "list"},
        "last": last,
        "profile_override": "NoneType",
    }


def test_json_v1_contract(sock_path: Path, controller: tuple[Controller, DryRunInjector]):
    """docs/protocol/ctl-v1.md: shapes are pinned; text and JSON share the socket."""
    c, _ = controller
    server = ctl.CtlServer(sock_path, c.handle, c.report.json)
    server.start()
    try:
        idle = _json(sock_path, {"version": 1, "command": "status"})
        assert _shape(idle) == v1_status("NoneType", rtt="NoneType")  # no health check yet
        assert (idle["ok"], idle["state"], idle["finishing"]) == (True, "idle", 0)

        started = _json(sock_path, {"version": 1, "command": "start"})
        assert _shape(started) == V1_ACTION
        assert (started["ok"], started["command"], started["result"]) == (
            True,
            "start",
            "recording",
        )
        assert _json(sock_path, {"version": 1, "command": "status"})["state"] == "recording"
        c.on_pcm(pcm(0.6))
        assert ctl.send("stop", sock_path) == "ok stopped"  # text protocol, same socket
        wait_idle(c)
        done = _json(sock_path, {"version": 1, "command": "status"})
        assert _shape(done) == v1_status(V1_LAST)
        last = cast("dict[str, object]", done["last"])
        assert (last["status"], last["delivery"]) == ("pasted", "sent")
        assert (done["phase"], last["stop_reason"], last["fallback"]) == ("idle", "key_up", False)
        assert "text" not in json.dumps(last)  # never the dictated text
        again = _json(sock_path, {"version": 1, "command": "paste-last"})
        assert (again["ok"], again["result"]) == (
            True,
            "pasted 15 chars",
        )  # "please run cube": the count only

        picked = _json(sock_path, {"version": 1, "command": "profile", "args": ["set", "prose"]})
        assert (picked["ok"], picked["result"]) == (True, "prose (next dictation)")
        assert _json(sock_path, {"version": 1, "command": "status"})["profile_override"] == "prose"
        assert ctl.send("profile   auto", sock_path) == "ok auto"  # text: words are arguments
        paused = _json(sock_path, {"version": 1, "command": "pause"})
        assert (paused["ok"], paused["result"]) == (True, "paused")
        assert _json(sock_path, {"version": 1, "command": "status"})["paused"] is True
        assert ctl.send("resume", sock_path) == "ok resumed"

        for request, code in [
            ({"version": 1, "command": "profile", "args": "next"}, "bad_args"),
            ({"version": 1, "command": "profile", "args": ["set prose"]}, "bad_args"),
            ({"version": 1, "command": "profile", "args": ["set", "nope"]}, "failed"),
            (b"{not json", "bad_json"),
            ({"command": "status"}, "unsupported_version"),
            ({"version": 2, "command": "status"}, "unsupported_version"),
            ({"version": 1, "command": "reboot"}, "unknown_command"),
            ({"version": 1, "command": "x" * 600}, "too_long"),
        ]:
            reply = _json(sock_path, request)
            assert _shape(reply) == V1_ERROR, request
            assert (reply["ok"], cast("dict[str, str]", reply["error"])["code"]) == (False, code)
    finally:
        server.close()


def test_subscribe_sends_the_status_now_and_on_every_change(
    sock_path: Path, controller: tuple[Controller, DryRunInjector]
):
    c, _ = controller
    server = ctl.CtlServer(sock_path, c.handle, c.report.json, c.report.wait_change)
    server.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(str(sock_path))
            s.sendall(b'{"version": 1, "command": "subscribe"}\n')
            lines = s.makefile("rb")

            def phase() -> object:
                return cast("dict[str, object]", json.loads(lines.readline()))["phase"]

            assert phase() == "idle"
            c.handle("start")
            assert phase() == "recording"
            c.handle("cancel")
            assert phase() == "idle"
        assert ctl.send("subscribe", sock_path).startswith("error unknown command")
    finally:
        server.close()


def test_skriv_ctl_json_flag(
    sock_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(ctl, "socket_path", lambda: sock_path)
    server = ctl.CtlServer(
        sock_path, lambda cmd: "ok pong" if cmd == "ping" else f"error nope: {cmd}"
    )
    server.start()
    try:
        assert main(["ctl", "--json", "ping"]) == 0
        assert main(["ctl", "--json", "profile", "set", "prose"]) == 1
        assert main(["ctl", "profile", "next"]) == 1
        out = capsys.readouterr().out.splitlines()
        assert [json.loads(line) for line in out[:2]] == [
            {"version": 1, "ok": True, "command": "ping", "result": "pong"},
            {
                "version": 1,
                "ok": False,
                "error": {"code": "failed", "message": "nope: profile set prose"},
            },
        ]
        assert out[2] == "error nope: profile next"
    finally:
        server.close()


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


def test_skriv_ctl_exit_codes(
    sock_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("SKRIV_CTL_SOCKET", str(sock_path))  # the override Skriv.app tests use
    assert main(["ctl", "start"]) == 2  # no daemon
    assert "skriv run" in capsys.readouterr().err
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
