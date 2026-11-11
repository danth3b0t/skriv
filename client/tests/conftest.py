"""In-process hark server (mock backend) on its own event-loop thread, a fake
OpenAI-compatible cleanup endpoint, and a scriptable context engine.

The client uses synchronous gRPC, so the grpc.aio server cannot share the test's
event loop: it runs on a background thread instead.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import wave
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from hark_client import context
from hark_client.context import ContextSnapshot, FocusInfo, Term, TermKind, TermSource
from hark_server.backends.mock import MockBackend
from hark_server.config import Config as ServerConfig
from hark_server.server import start

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from hark_server.server import Running

KUBECTL = Term(
    canonical="kubectl",
    spoken=("cube control",),
    weight=0.9,
    kind=TermKind.COMMAND,
    source=TermSource.TERMINAL_TEXT,
)


def speech(
    seconds: float, rate: int = 16000, seed: int = 0
) -> np.ndarray[tuple[int], np.dtype[np.int16]]:
    """Loud noise: every 200 ms mock word window in it emits a word."""
    n = int(seconds * rate)
    return (np.random.default_rng(seed).standard_normal(n) * 3000).astype(np.int16)


def write_wav(path: Path, seconds: float, rate: int = 16000, channels: int = 1) -> Path:
    mono = speech(seconds, rate)
    frames = np.repeat(mono[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames.astype("<i2").tobytes())
    return path


class HarkServer:
    def __init__(self, cleanup_url: str = "") -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        cfg = ServerConfig()
        cfg.server.listen = "127.0.0.1:0"
        cfg.server.name = "test"
        if cleanup_url:
            cfg.cleanup.base_url = cleanup_url
            cfg.cleanup.model = "fake-llm"
        self.running: Running = asyncio.run_coroutine_threadsafe(
            start(cfg, MockBackend()), self.loop
        ).result(10)
        self.target = f"127.0.0.1:{self.running.port}"

    def close(self) -> None:
        asyncio.run_coroutine_threadsafe(self.running.stop(0), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


class FakeLLM:
    """OpenAI-compatible /v1/chat/completions for the server's default (S1) engine:
    returns queued rewrites, else the transcript unchanged."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.queue: list[tuple[str, float]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.requests.append(cast("dict[str, object]", json.loads(body)))
                transcript = fake.user_message(-1).split("\n", 1)[1]  # after the control line
                content, delay = fake.queue.pop(0) if fake.queue else (transcript, 0.0)
                time.sleep(delay)
                choice = {"message": {"content": content}, "finish_reason": "stop"}
                data = json.dumps({"choices": [choice]}).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # client gave up (budget)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()

    def respond(self, rewrite: str, delay: float = 0.0) -> None:
        self.queue.append((rewrite, delay))

    def user_message(self, i: int = 0) -> str:
        messages = cast("list[dict[str, str]]", self.requests[i]["messages"])
        return messages[-1]["content"]


@pytest.fixture
def fake_llm() -> Iterator[FakeLLM]:
    fake = FakeLLM()
    yield fake
    fake.httpd.shutdown()
    fake.httpd.server_close()


@pytest.fixture
def hark_server(fake_llm: FakeLLM) -> Iterator[HarkServer]:
    server = HarkServer(cleanup_url=fake_llm.url)
    yield server
    server.close()


@dataclass
class FakeContext:
    """Replaces hark_client.context.collect. `snapshots` are returned in order (the
    last one repeats); calls are recorded as (deadline_s, max_terms)."""

    snapshots: list[ContextSnapshot] = field(default_factory=lambda: [ContextSnapshot()])
    delay_s: float = 0.0
    calls: list[tuple[float, int]] = field(default_factory=list[tuple[float, int]])

    def collect(self, deadline_s: float, max_terms: int = 60) -> ContextSnapshot:
        self.calls.append((deadline_s, max_terms))
        if self.delay_s and len(self.calls) == 1:
            time.sleep(self.delay_s)
        return self.snapshots[min(len(self.calls), len(self.snapshots)) - 1]


@pytest.fixture
def fake_context(monkeypatch: pytest.MonkeyPatch) -> FakeContext:
    fake = FakeContext()
    collect: Callable[[float, int], ContextSnapshot] = fake.collect
    monkeypatch.setattr(context, "collect", collect)
    return fake


def snapshot(
    app_id: str = "com.apple.Notes", window_id: str = "w1", *, terms: tuple[Term, ...] = (KUBECTL,)
) -> ContextSnapshot:
    return ContextSnapshot(
        focus=FocusInfo(app=app_id.rsplit(".", 1)[-1], app_id=app_id, window_id=window_id),
        terms=terms,
    )
