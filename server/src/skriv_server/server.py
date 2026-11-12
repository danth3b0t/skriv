"""Assemble and run the grpc.aio server: Asr, TextCleanup (optional), Health."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

# grpc_health.v1.health.aio is this module; typeshed's grpcio-health-checking stubs omit it.
from grpc_health.v1._async import (  # pyright: ignore[reportMissingTypeStubs]
    HealthServicer,
)

from skriv.v1 import skriv_pb2_grpc as pb_grpc

from .asr import AsrServicer
from .backends import Backend, create_backend
from .cleanup import CleanupServicer
from .session import AsrCore, CoreSettings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .config import Config

log = logging.getLogger("skriv_server")

MAX_MESSAGE_BYTES = 32 * 1024 * 1024
ASR_SERVICE = "skriv.v1.Asr"
CLEANUP_SERVICE = "skriv.v1.TextCleanup"
# typeshed's health_pb2 stub (basedpyright) types ServingStatus values as Incomplete (Any);
# grpcio-health-checking's own stub (ty) types them as ServingStatus, an int subclass.
SERVING = cast("int", health_pb2.HealthCheckResponse.SERVING)
NOT_SERVING = cast("int", health_pb2.HealthCheckResponse.NOT_SERVING)


@dataclass
class Running:
    server: grpc.aio.Server
    port: int
    core: AsrCore
    health: HealthServicer
    cleanup: CleanupServicer | None

    async def stop(self, grace: float | None = 1.0) -> None:
        await self.health.enter_graceful_shutdown()
        await self.server.stop(grace)
        if self.cleanup is not None:
            await self.cleanup.close()
        for backend in self.core.backends:
            backend.close()


async def set_asr_health(h: HealthServicer, *, ready: bool) -> None:
    for name in ("", ASR_SERVICE):
        await h.set(name, SERVING if ready else NOT_SERVING)  # ty: ignore[invalid-argument-type]


def _credentials(cfg: Config) -> grpc.ServerCredentials | None:
    tls = cfg.server.tls
    if not tls.cert:
        return None

    def read(path: str) -> bytes:
        return Path(path).read_bytes()

    return grpc.ssl_server_credentials(
        [(read(tls.key), read(tls.cert))],
        root_certificates=read(tls.client_ca) if tls.client_ca else None,
        require_client_auth=bool(tls.client_ca),
    )


async def start(cfg: Config, backend: Backend | Sequence[Backend] | None = None) -> Running:
    """Bind, start, and (if configured) load + warm the default model before SERVING.
    `backend`: instead of [backend] in `cfg`; a sequence is [default, others...]; a single
    backend brings its companions."""
    if backend is None:
        backend = create_backend(
            cfg.backend.kind, cfg.backend.model, cfg.backend.model_id, dict(cfg.backend.options)
        )
    backends = [backend, *backend.companions()] if isinstance(backend, Backend) else backend
    backend = backends[0]
    core = AsrCore(
        backends,
        CoreSettings(
            name=cfg.server.name,
            accelerator=cfg.server.accelerator,
            backend_kind=cfg.backend.kind,
            max_concurrent_sessions=cfg.server.max_concurrent_sessions,
            max_audio_ms=cfg.server.max_audio_ms,
            default_finalize_budget_ms=cfg.server.default_finalize_budget_ms,
            eager_end=cfg.server.eager_end,
            max_message_bytes=MAX_MESSAGE_BYTES,
        ),
    )
    server = grpc.aio.server(
        options=[
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.min_ping_interval_without_data_ms", 15_000),
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
        ]
    )
    health_servicer = HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)  # pyright: ignore[reportUnknownMemberType] - untyped stub
    pb_grpc.add_AsrServicer_to_server(AsrServicer(core), server)
    cleanup = None
    if cfg.cleanup.base_url:
        cleanup = CleanupServicer(cfg.cleanup, core.privacy)
        pb_grpc.add_TextCleanupServicer_to_server(cleanup, server)
        core.cleanup_profiles = list(cleanup.profiles.values())

    creds = _credentials(cfg)
    if creds is None:
        port = server.add_insecure_port(cfg.server.listen)
    else:
        port = server.add_secure_port(cfg.server.listen, creds)
    await set_asr_health(health_servicer, ready=False)
    if cleanup is not None:
        await health_servicer.set(CLEANUP_SERVICE, SERVING)  # ty: ignore[invalid-argument-type]
    await server.start()
    log.info(
        "listening on %s (port %d, %s), backend=%s models=%s",
        cfg.server.listen, port, "TLS" + (" mTLS" if cfg.server.tls.client_ca else "")
        if creds else "plaintext", cfg.backend.kind,
        ", ".join(b.model_id + ("" if b is backend else " (on first use)") for b in backends),
    )  # fmt: skip

    running = Running(server, port, core, health_servicer, cleanup)
    if cfg.server.warmup_on_start:
        t0 = time.perf_counter()
        try:
            await backend.load()
            load_ms = int((time.perf_counter() - t0) * 1000)
            warm_ms = await backend.warmup()
        except Exception:
            await running.stop(0)
            raise
        log.info(
            "model ready: load %d ms, warmup decodes %d ms, perf rtfx=%.1f finalize_ms_10s=%d",
            load_ms, warm_ms, backend.perf.rtfx, backend.perf.finalize_ms_10s,
        )  # fmt: skip
        await set_asr_health(health_servicer, ready=True)
    return running
