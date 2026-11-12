"""ASR provider routing: which hark server takes this dictation.

`[[providers]]` lists servers (default: one at 127.0.0.1:50051), e.g. the desktop GPU first and the
laptop's own CPU server (scripts/cpu-server-up) last. At the start of every dictation,
`Router.pick()` walks them in priority order and takes the first that is healthy:

- health = grpc.health.v1 `Check("hark.v1.Asr")` with a deadline of the provider's
  `max_latency_ms`: SERVING in time -> ok; deadline exceeded -> slow; anything else
  (connection refused, NOT_SERVING while the model loads, TLS failure) -> down;
- results are cached (ok for 30 s, slow/down for 5 s), and `hark run` refreshes them in the
  background, so key-down normally probes nothing;
- a dictation that gets no answer from its provider marks it down: the next one moves on;
- if nothing is healthy, the first provider is used anyway (it may be back; its error then
  reaches the user, and `hark retry` can re-run the audio).

The choice is made once, at session start, while the context is collected; audio recorded
meanwhile is replayed to the chosen server.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import grpc
from grpc_health.v1 import health_pb2

from skriv_client import asr

if TYPE_CHECKING:
    from collections.abc import Sequence

    from skriv_client.config import ProviderConfig

log = logging.getLogger("hark.providers")

SERVING_STATUS = {0: "UNKNOWN", 1: "SERVING", 2: "NOT_SERVING", 3: "SERVICE_UNKNOWN"}
FRESH_S = {"ok": 30.0, "slow": 5.0, "down": 5.0}
BACKGROUND_S = 10.0


def health_status(channel: grpc.Channel, service: str, timeout: float) -> str:
    check = channel.unary_unary(
        "/grpc.health.v1.Health/Check",
        request_serializer=health_pb2.HealthCheckRequest.SerializeToString,
        response_deserializer=health_pb2.HealthCheckResponse.FromString,
    )
    resp = check(health_pb2.HealthCheckRequest(service=service), timeout=timeout)
    status = cast("int", resp.status)  # the generated stub types the enum as Any
    return SERVING_STATUS.get(status, str(status))


@dataclass(frozen=True)
class Health:
    state: str = "unknown"  # ok | slow | down | unknown
    rtt_ms: float | None = None
    detail: str = ""
    at: float = 0.0  # time.monotonic() of the check


class Provider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self.name = cfg.label
        self.channel = asr.open_channel(cfg)
        self.caps = asr.Capabilities(self.channel)
        self._lock = threading.Lock()
        self._health = Health()

    @property
    def health(self) -> Health:
        with self._lock:
            return self._health

    def _set(self, h: Health) -> Health:
        with self._lock:
            old, self._health = self._health, h
        if old.state not in (h.state, "unknown"):
            log.info("provider %s: %s -> %s %s", self.name, old.state, h.state, h.detail)
        return h

    def fresh(self) -> bool:
        h = self.health
        return h.state in FRESH_S and time.monotonic() - h.at < FRESH_S[h.state]

    def check(self) -> Health:
        """Health-check now (blocks at most max_latency_ms)."""
        t0 = time.perf_counter()
        try:
            status = health_status(self.channel, "hark.v1.Asr", self.cfg.max_latency_ms / 1000)
        except grpc.RpcError as e:
            slow = isinstance(e, grpc.Call) and e.code() == grpc.StatusCode.DEADLINE_EXCEEDED
            detail = f"no answer in {self.cfg.max_latency_ms} ms" if slow else asr.rpc_error_text(e)
            return self._set(Health("slow" if slow else "down", None, detail, time.monotonic()))
        rtt = round((time.perf_counter() - t0) * 1000, 1)
        ok = status == "SERVING"
        return self._set(
            Health("ok" if ok else "down", rtt, "" if ok else status, time.monotonic())
        )

    def mark_down(self, detail: str) -> None:
        self._set(Health("down", None, detail, time.monotonic()))

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "address": self.cfg.address,
            "send_context": self.cfg.send_context,
        }


class Router:
    def __init__(self, configs: Sequence[ProviderConfig]) -> None:
        if not configs:
            raise ValueError("no ASR provider configured")
        order = sorted(range(len(configs)), key=lambda i: (configs[i].priority, i))
        self.providers = [Provider(configs[i]) for i in order]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def pick(self) -> tuple[Provider, list[dict[str, object]]]:
        """The provider for one dictation and the route that led to it (for history)."""
        route: list[dict[str, object]] = []
        for p in self.providers:
            cached = p.fresh()
            h = p.health if cached else p.check()
            hop: dict[str, object] = {
                "name": p.name, "state": h.state, "rtt_ms": h.rtt_ms, "cached": cached
            }  # fmt: skip
            if h.detail:
                hop["detail"] = h.detail
            route.append(hop)
            if h.state == "ok":
                return p, route
        return self.providers[0], route

    def refresh(self) -> None:
        """Check every provider at once; fetch capabilities from healthy ones that have none."""

        def one(p: Provider) -> None:
            if p.check().state == "ok" and p.caps.get() is None:
                p.caps.refresh()

        threads = [
            threading.Thread(target=one, args=(p,), daemon=True, name=f"hark-health-{p.name}")
            for p in self.providers
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def start_background(self, interval_s: float = BACKGROUND_S) -> None:
        """Keep the health cache warm so that key-down never waits for a probe."""

        def loop() -> None:
            while not self._stop.wait(interval_s):
                self.refresh()

        self._thread = threading.Thread(target=loop, daemon=True, name="hark-health")
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        for p in self.providers:
            p.channel.close()
