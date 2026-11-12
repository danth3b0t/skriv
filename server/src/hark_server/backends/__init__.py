"""ASR backend interface.

A backend owns one model and a single worker thread. All model work (load,
warmup, decode) runs on that thread: accelerator runtimes (MLX streams, CUDA
contexts) are thread-affine, and one thread also serializes jobs the way a
single GPU would. Backends of one accelerator share the thread (`executor`). Sessions, validation, budgets, anchors and bias hits live in
the server (`hark_server.session`); backends only decode.
"""

from __future__ import annotations

import abc
import asyncio
import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from hark.v1 import hark_pb2 as pb

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import numpy as np
    import numpy.typing as npt

    type Pcm = npt.NDArray[np.int16]  # mono 16 kHz samples

SAMPLE_RATE = 16000


@dataclass
class WordOut:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None  # None: backend has none / placeholder


@dataclass
class DecodeOut:
    text: str
    words: list[WordOut]
    encoder_ms: int = 0
    decode_ms: int = 0
    backend_ms: dict[str, int] = field(default_factory=dict)
    queue_ms: int = 0  # filled by Backend.run: wait for the worker thread
    score: float | None = None


@dataclass
class BiasPlan:
    """What the decoder will actually receive for one context revision."""

    terms: list[pb.BiasTerm]  # after server-side truncation, in rank order
    term_boosts: dict[str, float]  # id -> native boost (after strength)
    backend_params: dict[str, str] = field(default_factory=dict)


class AudioStream(Protocol):
    """Per-session work done while audio streams in (Backend.open_stream).

    Called from the event loop: `feed` with every sample the session appends
    (zero-filled gaps included), `set_plan` on a context update, `finish` instead
    of Backend.decode(pcm, plan) for the primary decode, `close` when the
    session ends."""

    def feed(self, data: bytes) -> None: ...
    def set_plan(self, plan: BiasPlan | None) -> None: ...
    async def finish(self, pcm: Pcm, plan: BiasPlan | None) -> DecodeOut: ...
    def close(self) -> None: ...


class BackendUnavailable(RuntimeError):  # noqa: N818 - public name, imported by __main__ and tests
    """The backend cannot run on this host (missing runtime, wrong platform)."""


class Backend(abc.ABC):
    """One loaded model. Subclasses implement the `_..._sync` methods."""

    def __init__(self, model_id: str, executor: ThreadPoolExecutor | None = None) -> None:
        """`executor`: another backend's worker thread, shared (one accelerator)."""
        self.model_id = model_id
        self.state = pb.MODEL_STATE_COLD
        self.perf = pb.PerfEstimate()
        self._executor = executor or ThreadPoolExecutor(1, thread_name_prefix=f"backend-{model_id}")
        self._pending = 0
        self._lock = threading.Lock()

    # ----- static description -------------------------------------------
    @abc.abstractmethod
    def capabilities(self) -> pb.ModelCapabilities:
        """Model capabilities; `state` and `perf` are filled by the caller."""

    def describe(self) -> pb.ModelCapabilities:
        caps = self.capabilities()
        caps.model_id = self.model_id
        caps.state = self.state
        caps.perf.CopyFrom(self.perf)
        return caps

    # ----- bias ----------------------------------------------------------
    def plan_bias(self, terms: list[pb.BiasTerm], strength: float) -> BiasPlan | None:
        """Map normalized weights to the native scale. None => decoder gets no bias.

        Default: linear [0,1] -> [boost_min, boost_max], times strength
        (ADR-001 #1). Backends with mechanism NONE override or never
        reach here.
        """
        bias = self.capabilities().bias
        if not terms or strength <= 0:
            return None
        boosts: dict[str, float] = {}
        for t in terms:
            w = t.weight if t.HasField("weight") else 1.0
            boosts[t.id] = strength * (bias.boost_min + w * (bias.boost_max - bias.boost_min))
        return BiasPlan(terms=list(terms), term_boosts=boosts)

    def companions(self) -> list[Backend]:
        """Models served beside this one (e.g. an English-only model next to a multilingual
        one), usually on its worker thread. The server loads them on first use."""
        return []

    def open_stream(self, plan: BiasPlan | None) -> AudioStream | None:
        """A per-session incremental decoder, or None: decode at end of audio only."""
        return None

    # ----- work (async wrappers around the worker thread) ----------------
    async def run[*Ts, T](self, fn: Callable[[*Ts], T], *args: *Ts) -> T:
        submitted = time.perf_counter()
        with self._lock:
            self._pending += 1

        def job() -> T:
            started = time.perf_counter()
            try:
                out = fn(*args)
            finally:
                with self._lock:
                    self._pending -= 1
            if isinstance(out, DecodeOut):
                out.queue_ms = int((started - submitted) * 1000)
            return out

        return await asyncio.get_running_loop().run_in_executor(self._executor, job)

    @property
    def queued_jobs(self) -> int:
        with self._lock:
            return max(0, self._pending - 1)

    async def load(self) -> None:
        self.state = pb.MODEL_STATE_LOADING
        try:
            await self.run(self._load_sync)
        except Exception:
            self.state = pb.MODEL_STATE_FAILED
            raise
        self.state = pb.MODEL_STATE_READY

    async def warmup(self) -> int:
        """Load if needed, run synthetic decodes, fill `perf`. Returns ms."""
        t0 = time.perf_counter()
        if self.state != pb.MODEL_STATE_READY:
            await self.load()
        await self.run(self._warmup_sync)
        return int((time.perf_counter() - t0) * 1000)

    async def decode(self, pcm: Pcm, plan: BiasPlan | None) -> DecodeOut:
        """Primary (plan) or unbiased (plan=None) decode of int16 mono 16 kHz audio."""
        return await self.run(self._decode_sync, pcm, plan)

    async def n_best(self, pcm: Pcm, plan: BiasPlan | None, n: int) -> list[DecodeOut]:
        """Entries 1..n-1 (origin BEAM). Only called if capabilities().n_best.max_n > 1."""
        return await self.run(self._n_best_sync, pcm, plan, n)

    def estimate_ms(self, audio_ms: int) -> int:
        """Expected cost of one extra decode (anchor / N-best), for budget planning."""
        if self.perf.rtfx > 0:
            return int(audio_ms / self.perf.rtfx)
        return 0

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ----- implemented by backends (run on the worker thread) ------------
    @abc.abstractmethod
    def _load_sync(self) -> None: ...

    @abc.abstractmethod
    def _warmup_sync(self) -> None: ...

    @abc.abstractmethod
    def _decode_sync(self, pcm: Pcm, plan: BiasPlan | None) -> DecodeOut: ...

    def _n_best_sync(self, pcm: Pcm, plan: BiasPlan | None, n: int) -> list[DecodeOut]:
        raise NotImplementedError


def create_backend(kind: str, model: str, model_id: str, options: Mapping[str, object]) -> Backend:
    """Raises ValueError for an unknown kind or invalid [backend.options]."""
    # Backend modules import this package, so they are imported here, lazily.
    cls: Callable[..., Backend]  # options are only checked at runtime, by bind() below
    args: tuple[str, ...]
    if kind == "mock":
        from .mock import MockBackend  # noqa: PLC0415 - import cycle

        cls, args = MockBackend, (model_id or "mock",)
    elif kind == "parakeet_mlx":
        from .parakeet_mlx import ParakeetMlxBackend  # noqa: PLC0415 - import cycle

        cls, args = ParakeetMlxBackend, (model, model_id)
    elif kind == "transcribe_cpp":
        from .transcribe_cpp import TranscribeCppBackend  # noqa: PLC0415 - import cycle

        cls, args = TranscribeCppBackend, (model, model_id)
    else:
        raise ValueError(f"unknown backend {kind!r} (mock | parakeet_mlx | transcribe_cpp)")
    try:
        inspect.signature(cls).bind(*args, **options)
    except TypeError as e:
        raise ValueError(f"invalid [backend.options] for {kind}: {e}") from None
    # ty narrows `cls` and `args` to unions and pairs them crosswise; option values are untyped TOML.
    return cls(*args, **options)  # ty: ignore[invalid-argument-type, too-many-positional-arguments]
