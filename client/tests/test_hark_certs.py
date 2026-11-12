"""scripts/hark-certs: the certificates it makes give a working mTLS session between hark and
skriv-server, and the server turns away clients without one of them."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast

import grpc
import pytest
from grpc_health.v1 import health_pb2

from skriv_client import asr
from skriv_client.config import ProviderConfig
from skriv_server.backends.mock import MockBackend
from skriv_server.config import Config
from skriv_server.server import start

if TYPE_CHECKING:
    from collections.abc import Iterator

    from skriv_server.server import Running

CERTS = Path(__file__).parents[2] / "scripts" / "hark-certs"
SERVING = cast("int", health_pb2.HealthCheckResponse.SERVING)

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")


def certs(pki: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/bash", str(CERTS), "--dir", str(pki), *args],
        capture_output=True, text=True, check=False,
    )  # fmt: skip


def health(cfg: ProviderConfig) -> int:
    with asr.open_channel(cfg) as channel:
        check = channel.unary_unary(
            "/grpc.health.v1.Health/Check",
            request_serializer=health_pb2.HealthCheckRequest.SerializeToString,
            response_deserializer=health_pb2.HealthCheckResponse.FromString,
        )
        resp = check(health_pb2.HealthCheckRequest(service="hark.v1.Asr"), timeout=5)
        return cast("int", resp.status)


@pytest.fixture
def mtls_server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    pki = tmp_path / "pki"
    for args in (["ca"], ["server", "desktop", "127.0.0.1"], ["client", "laptop"]):
        p = certs(pki, *args)
        assert p.returncode == 0, p.stderr
    cfg = Config()
    cfg.server.listen = "127.0.0.1:0"
    cfg.server.tls.cert = str(pki / "desktop.crt")
    cfg.server.tls.key = str(pki / "desktop.key")
    cfg.server.tls.client_ca = str(pki / "ca.crt")
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    running: Running = asyncio.run_coroutine_threadsafe(start(cfg, MockBackend()), loop).result(10)
    yield f"127.0.0.1:{running.port}", pki
    asyncio.run_coroutine_threadsafe(running.stop(0), loop).result(5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


def test_client_cert_from_the_ca_gets_through(mtls_server: tuple[str, Path]):
    address, pki = mtls_server
    cfg = ProviderConfig(
        address=address,
        tls_ca=str(pki / "ca.crt"),
        tls_cert=str(pki / "laptop.crt"),
        tls_key=str(pki / "laptop.key"),
    )
    assert health(cfg) == SERVING


def test_clients_without_a_cert_from_the_ca_are_refused(
    mtls_server: tuple[str, Path], tmp_path: Path
):
    address, pki = mtls_server
    with pytest.raises(grpc.RpcError):
        health(ProviderConfig(address=address, tls_ca=str(pki / "ca.crt")))
    other = tmp_path / "other"
    assert certs(other, "ca").returncode == 0
    assert certs(other, "client", "intruder").returncode == 0
    with pytest.raises(grpc.RpcError):
        health(
            ProviderConfig(
                address=address,
                tls_ca=str(pki / "ca.crt"),
                tls_cert=str(other / "intruder.crt"),
                tls_key=str(other / "intruder.key"),
            )
        )


def test_keys_are_private_and_nothing_is_overwritten(tmp_path: Path):
    pki = tmp_path / "pki"
    assert certs(pki, "client", "laptop").returncode == 1  # no CA yet
    assert certs(pki, "ca").returncode == 0
    assert certs(pki, "client", "laptop").returncode == 0
    for key in ("ca.key", "laptop.key"):
        assert (pki / key).stat().st_mode & 0o777 == 0o600
    before = (pki / "ca.key").read_bytes()
    again = certs(pki, "ca")
    assert again.returncode == 1
    assert "exists" in again.stderr
    assert (pki / "ca.key").read_bytes() == before
    assert certs(pki, "client", "../escape").returncode == 2
    assert sorted(p.name for p in pki.iterdir()) == ["ca.crt", "ca.key", "laptop.crt", "laptop.key"]
