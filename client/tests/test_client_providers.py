"""Provider routing: ordered fallback per dictation, health states, privacy gating."""

from __future__ import annotations

import json
import socket
import time
from concurrent import futures
from typing import TYPE_CHECKING, cast

import grpc
import pytest
from grpc_health.v1 import health_pb2

from skriv_client.__main__ import main
from skriv_client.config import Config, ConfigError, ProviderConfig, load
from skriv_client.daemon import Controller
from skriv_client.inject import DryRunInjector
from skriv_client.pipeline import Services
from skriv_client.providers import Router

from .conftest import RecordingFeedback, SkrivServer, pcm, snapshot, wait_idle, write_wav

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from skriv_client.history import Record

    from .conftest import FakeContext


def closed_port() -> str:
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{cast('tuple[str, int]', s.getsockname())[1]}"


@pytest.fixture
def slow_server() -> Iterator[str]:
    """Answers health checks after 1 s (an overloaded or far-away server)."""

    def check(req: health_pb2.HealthCheckRequest, ctx: grpc.ServicerContext) -> object:
        time.sleep(1.0)
        return health_pb2.HealthCheckResponse(status="SERVING")

    server = grpc.server(futures.ThreadPoolExecutor(2))
    handler = grpc.method_handlers_generic_handler(
        "grpc.health.v1.Health",
        {
            "Check": grpc.unary_unary_rpc_method_handler(
                check,
                request_deserializer=health_pb2.HealthCheckRequest.FromString,
                response_serializer=health_pb2.HealthCheckResponse.SerializeToString,
            )
        },
    )
    server.add_generic_rpc_handlers((handler,))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield f"127.0.0.1:{port}"
    server.stop(0)


def once(tmp_path: Path, providers: str) -> tuple[int, dict[str, object]]:
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[history]\ndir = "{tmp_path / "data"}"\n' + providers)
    wav = write_wav(tmp_path / "in.wav", 1.0)
    code = main(["--config", str(cfg), "once", "--wav", str(wav), "--dry-run"])
    (line,) = (tmp_path / "data" / "history.jsonl").read_text().splitlines()
    return code, cast("dict[str, object]", json.loads(line))


def test_dictation_falls_back_past_unreachable_and_slow_providers_in_priority_order(
    tmp_path: Path, skriv_server: SkrivServer, slow_server: str, fake_context: FakeContext
):
    fake_context.snapshots = [snapshot("com.apple.Notes")]
    t0 = time.perf_counter()
    code, rec = once(
        tmp_path,
        # file order differs from priority order on purpose
        f'[[providers]]\nname = "local"\naddress = "{skriv_server.target}"\npriority = 3\n'
        f'[[providers]]\nname = "desktop"\naddress = "{closed_port()}"\npriority = 1\n'
        f'[[providers]]\nname = "far"\naddress = "{slow_server}"\npriority = 2\n'
        "max_latency_ms = 100\n",
    )
    assert code == 0
    assert time.perf_counter() - t0 < 1.5  # did not wait for the slow server's answer
    assert rec["status"] == "pasted"
    assert cast("dict[str, object]", rec["provider"])["name"] == "local"
    route = cast("list[dict[str, object]]", rec["route"])
    assert [(h["name"], h["state"]) for h in route] == [
        ("desktop", "down"),
        ("far", "slow"),
        ("local", "ok"),
    ]


def test_send_context_false_sends_audio_only_and_canonicalizes_locally(
    tmp_path: Path, skriv_server: SkrivServer, fake_context: FakeContext
):
    fake_context.snapshots = [snapshot("com.apple.Notes")]  # shortlist: kubectl ("cube control")
    code, rec = once(
        tmp_path,
        f'[[providers]]\naddress = "{skriv_server.target}"\nsend_context = false\n'
        "[cleanup]\nenabled = false\n",
    )
    assert code == 0
    assert rec["bias_terms_sent"] == 0
    assert rec["raw_text"] == "please run cube control get"  # the mock rewrites only when biased
    assert rec["canonical_text"] == "please run kubectl get"
    assert cast("dict[str, object]", rec["provider"])["send_context"] is False


def test_a_provider_that_stops_answering_fails_over_at_once_and_is_skipped_next_time(
    tmp_path: Path, fake_context: FakeContext
):
    fake_context.snapshots = [snapshot("kitty")]
    primary, fallback = SkrivServer(), SkrivServer()
    cfg = Config()
    cfg.history.dir = str(tmp_path)
    cfg.audio.min_ms = 0
    router = Router(
        [
            ProviderConfig(name="primary", address=primary.target),
            ProviderConfig(name="fallback", address=fallback.target),
        ]
    )
    c = Controller(
        Services(cfg, router, DryRunInjector(out=lambda _: None), RecordingFeedback()), None
    )

    def dictate() -> None:
        c.handle("start")
        c.on_pcm(pcm(0.6))
        c.handle("stop")
        wait_idle(c)

    try:
        dictate()
        primary.close()
        dictate()  # health still cached as ok: the primary fails, the fallback gets the audio
        dictate()
    finally:
        router.close()
        fallback.close()
    lines = (tmp_path / "history.jsonl").read_text().splitlines()
    records = [cast("Record", json.loads(x)) for x in lines]
    served = [(cast("dict[str, str]", r["provider"])["name"], r["status"]) for r in records]
    assert served == [("primary", "pasted"), ("fallback", "pasted"), ("fallback", "pasted")]
    assert [r["fallback"] for r in records] == [False, True, True]
    assert [h["state"] for h in cast("list[dict[str, str]]", records[1]["route"])] == [
        "ok",  # the cached health that sent it to the primary
        "failed",
        "down",
        "ok",
    ]
    assert cast("list[dict[str, str]]", records[2]["route"])[0]["state"] == "down"


def test_the_default_is_one_local_provider_and_providers_are_validated(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("")
    (p,) = load(path).providers
    assert (p.address, p.model_id, p.send_context) == ("127.0.0.1:50051", "", True)

    path.write_text('[[providers]]\nname = "x"\n')
    with pytest.raises(ConfigError, match=r"providers\[0\]\.address is required"):
        load(path)
    path.write_text('[[providers]]\naddress = "a:1"\n[[providers]]\naddress = "a:1"\n')
    with pytest.raises(ConfigError, match="two providers named 'a:1'"):
        load(path)
