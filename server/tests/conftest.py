"""In-process hark server (mock backend) + client helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, Unpack, cast, overload

import grpc
import numpy as np
import pytest
from grpc_status import _async as rpc_status_async

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from hark_server.backends.mock import MockBackend
from hark_server.config import Config
from hark_server.server import start

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Sequence

    import numpy.typing as npt
    from google.protobuf.message import Message
    from google.rpc.status_pb2 import Status

    from hark_server.backends import Backend
    from hark_server.server import Running

SR = 16000
CHUNK = 640  # 40 ms

type Script = Sequence[pb.TranscribeRequest | float]
"""Client.transcribe input: requests to send, or seconds to sleep."""


class StartFields(TypedDict, total=False):
    """SessionStart fields the tests set."""

    utterance_id: str
    model_id: str
    languages: list[str]
    audio_format: pb.AudioFormat
    context: pb.RecognitionContext
    privacy: pb.PrivacyFlags
    options: pb.ResultOptions
    deadlines: pb.Deadlines
    required_features: list[str]


def speech(ms: int, seed: int = 0) -> npt.NDArray[np.int16]:
    """Loud noise: every mock word window in it emits a word."""
    return (np.random.default_rng(seed).standard_normal(SR * ms // 1000) * 3000).astype(np.int16)


def silence(ms: int) -> npt.NDArray[np.int16]:
    return np.zeros(SR * ms // 1000, np.int16)


def start_req(**kw: Unpack[StartFields]) -> pb.TranscribeRequest:
    kw.setdefault("utterance_id", "utt-1")
    return pb.TranscribeRequest(start=pb.SessionStart(**kw))


def audio_reqs(
    pcm: npt.NDArray[np.int16], *, seq: int = 0, start_sample: int = 0
) -> list[pb.TranscribeRequest]:
    out: list[pb.TranscribeRequest] = []
    for i in range(0, len(pcm), CHUNK):
        part = pcm[i : i + CHUNK]
        out.append(
            pb.TranscribeRequest(
                audio=pb.AudioChunk(
                    data=part.astype("<i2").tobytes(),
                    seq=seq,
                    start_sample=start_sample + i,
                    num_samples=len(part),
                )
            )
        )
        seq += 1
    return out


def eoa_req(total_samples: int, last_seq: int) -> pb.TranscribeRequest:
    return pb.TranscribeRequest(
        end_of_audio=pb.EndOfAudio(
            total_samples=total_samples, last_seq=last_seq, reason=pb.END_REASON_KEY_UP
        )
    )


def utterance(
    pcm: npt.NDArray[np.int16], **start_kw: Unpack[StartFields]
) -> list[pb.TranscribeRequest]:
    chunks = audio_reqs(pcm)
    return [start_req(**start_kw), *chunks, eoa_req(len(pcm), len(chunks) - 1)]


def kubectl_term(*, weight: float | None = None) -> pb.BiasTerm:
    return pb.BiasTerm(
        id="t-kubectl",
        canonical="kubectl",
        aliases=[pb.SpokenAlias(text="cube control", kind=pb.ALIAS_KIND_SPOKEN_FORM)],
        kind=pb.TERM_KIND_COMMAND,
        weight=weight,  # None: unset
    )


def dig(value: object, *path: str | int) -> object:
    """value[path[0]][path[1]]... for parsed JSON."""
    for key in path:
        if isinstance(key, int):
            value = cast("list[object]", value)[key]
        else:
            value = cast("dict[str, object]", value)[key]
    return value


@dataclass
class Outcome:
    responses: list[pb.TranscribeResponse]
    code: grpc.StatusCode
    error: pb.ErrorDetail | None  # from grpc-status-details-bin

    @overload
    def of(self, kind: Literal["accepted"]) -> list[pb.SessionAccepted]: ...
    @overload
    def of(self, kind: Literal["warning"]) -> list[pb.ServerWarning]: ...
    @overload
    def of(self, kind: Literal["status"]) -> list[pb.StreamStatus]: ...
    @overload
    def of(self, kind: Literal["final_result"]) -> list[pb.FinalResult]: ...
    @overload
    def of(self, kind: Literal["session_ended"]) -> list[pb.SessionEnded]: ...
    def of(self, kind: str) -> Sequence[Message]:
        return [
            cast("Message", getattr(r, kind)) for r in self.responses if r.WhichOneof("msg") == kind
        ]

    @property
    def kinds(self) -> list[str | None]:
        return [r.WhichOneof("msg") for r in self.responses]

    @property
    def final(self) -> pb.FinalResult:
        (f,) = self.of("final_result")
        return f


async def error_detail(call: grpc.aio.Call) -> pb.ErrorDetail | None:
    # typeshed: untyped `from_call`, returns Incomplete | None (a google.rpc.Status).
    status = cast("Status | None", await rpc_status_async.from_call(call))  # pyright: ignore[reportUnknownMemberType]
    if status is None or not status.details:
        return None
    detail = pb.ErrorDetail()
    status.details[0].Unpack(detail)
    return detail


class Client:
    def __init__(self, channel: grpc.aio.Channel, target: str) -> None:
        self.channel = channel
        self.target = target
        self.asr = pb_grpc.AsrStub(channel)
        self.cleanup = pb_grpc.TextCleanupStub(channel)

    async def transcribe(self, script: Script) -> Outcome:
        """Script items: TranscribeRequest (sent) or float (sleep seconds).

        Like a well-behaved client, waits for the server's first response
        after the first message and stops writing after a terminal message.
        (grpcio's Python client replaces the real status with INTERNAL when a
        write races with the server ending the call.)
        """
        first_response, terminal = asyncio.Event(), asyncio.Event()

        async def requests() -> AsyncIterator[pb.TranscribeRequest]:
            for i, item in enumerate(script):
                if terminal.is_set():
                    return
                if isinstance(item, (int, float)):
                    await asyncio.sleep(item)
                    continue
                yield item
                if i == 0:
                    await first_response.wait()

        call = self.asr.Transcribe(requests())
        responses: list[pb.TranscribeResponse] = []
        try:
            async for r in call:
                responses.append(r)
                first_response.set()
                if r.WhichOneof("msg") in ("final_result", "session_ended"):
                    terminal.set()
        except grpc.aio.AioRpcError:
            pass
        finally:
            first_response.set()
            terminal.set()
        return Outcome(responses, await call.code(), await error_detail(call))

    async def unary[Req, Resp](
        self, method: grpc.aio.UnaryUnaryMultiCallable[Req, Resp], request: Req
    ) -> tuple[Resp | None, grpc.StatusCode, pb.ErrorDetail | None]:
        """Returns (response | None, code, ErrorDetail | None)."""
        call = method(request)
        try:
            return await call, grpc.StatusCode.OK, None
        except grpc.aio.AioRpcError:
            return None, await call.code(), await error_detail(call)


class Serve(Protocol):
    """The `serve` fixture: `client = await serve(backend=..., **server_config_overrides)`."""

    def __call__(
        self,
        backend: Backend | None = None,
        cleanup: dict[str, object] | None = None,
        **server_kw: object,
    ) -> Awaitable[Client]: ...


@pytest.fixture
async def serve() -> AsyncIterator[Serve]:
    """`client = await serve(backend=..., **server_config_overrides)`."""
    started: list[tuple[Running, grpc.aio.Channel]] = []

    async def _serve(
        backend: Backend | None = None,
        cleanup: dict[str, object] | None = None,
        **server_kw: object,
    ) -> Client:
        cfg = Config()
        cfg.server.listen = "127.0.0.1:0"
        cfg.server.name = "test"
        for k, v in server_kw.items():
            setattr(cfg.server, k, v)
        if cleanup:
            for k, v in cleanup.items():
                setattr(cfg.cleanup, k, v)
        running = await start(cfg, backend or MockBackend())
        target = f"127.0.0.1:{running.port}"
        channel = grpc.aio.insecure_channel(target)
        started.append((running, channel))
        return Client(channel, target)

    yield _serve
    for running, channel in started:
        await channel.close()
        await running.stop(0)
