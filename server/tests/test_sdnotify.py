"""skriv-server under a Type=notify unit: READY only once SERVING, watchdog pings, STOPPING."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import grpc
from grpc_health.v1 import health_pb2

if TYPE_CHECKING:
    import pytest

SERVING = cast("int", health_pb2.HealthCheckResponse.SERVING)


def health_status(target: str) -> int:
    with grpc.insecure_channel(target) as ch:
        check = ch.unary_unary(
            "/grpc.health.v1.Health/Check",
            request_serializer=health_pb2.HealthCheckRequest.SerializeToString,
            response_deserializer=health_pb2.HealthCheckResponse.FromString,
        )
        resp = check(health_pb2.HealthCheckRequest(service="hark.v1.Asr"), timeout=2)
        return cast("int", resp.status)


def test_ready_after_serving_then_watchdog_then_stopping():
    # AF_UNIX paths are short (104 bytes on macOS): keep them out of pytest's long tmp dirs.
    with tempfile.TemporaryDirectory(dir="/tmp") as d:
        notify_path, listen = Path(d, "notify"), Path(d, "hark.sock")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(str(notify_path))
        sock.settimeout(20)
        env = {
            **os.environ,
            "NOTIFY_SOCKET": str(notify_path),
            "WATCHDOG_USEC": "200000",  # pings every 0.1 s
        }
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            [
                sys.executable,
                "-m",
                "skriv_server",
                "--backend",
                "mock",
                "--listen",
                f"unix:{listen}",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert sock.recv(64) == b"READY=1"
            assert health_status(f"unix:{listen}") == SERVING
            t0 = time.monotonic()
            assert [sock.recv(64) for _ in range(3)] == [b"WATCHDOG=1"] * 3
            assert time.monotonic() - t0 < 2
            proc.send_signal(signal.SIGTERM)
            msgs = [sock.recv(64)]
            while msgs[-1] == b"WATCHDOG=1":
                msgs.append(sock.recv(64))
            assert msgs[-1] == b"STOPPING=1"
            assert proc.wait(10) == 0
        finally:
            proc.kill()
            proc.wait()
            sock.close()


def test_no_notify_socket_is_a_no_op(monkeypatch: pytest.MonkeyPatch):
    from skriv_server import sdnotify  # noqa: PLC0415

    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.setenv("WATCHDOG_USEC", "0")
    assert sdnotify.notify("READY=1") is False
    assert sdnotify.watchdog_interval() is None
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid() + 1))  # meant for another process
    assert sdnotify.watchdog_interval() is None
