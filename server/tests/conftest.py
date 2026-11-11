"""In-process hark server (mock backend) + client helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import grpc
import numpy as np
import pytest
from grpc_status import _async as rpc_status_async

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from hark_server.backends.mock import MockBackend
from hark_server.config import Config
from hark_server.server import start

SR = 16000
CHUNK = 640  # 40 ms


def speech(ms: int, seed: int = 0) -> np.ndarray:
    """Loud noise: every mock word window in it emits a word."""
    return (np.random.default_rng(seed).standard_normal(SR * ms // 1000) * 3000).astype(np.int16)


def silence(ms: int) -> np.ndarray:
    return np.zeros(SR * ms // 1000, np.int16)


def start_req(**kw: Any) -> pb.TranscribeRequest:
    kw.setdefault("utterance_id", "utt-1")
    return pb.TranscribeRequest(start=pb.SessionStart(**kw))


def audio_reqs(pcm: np.ndarray, *, seq: int = 0, start_sample: int = 0) -> list[pb.TranscribeRequest]:
    out = []
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


def utterance(pcm: np.ndarray, **start_kw: Any) -> list:
    chunks = audio_reqs(pcm)
    return [start_req(**start_kw), *chunks, eoa_req(len(pcm), len(chunks) - 1)]


def kubectl_term(**kw: Any) -> pb.BiasTerm:
    return pb.BiasTerm(
        id="t-kubectl",
        canonical="kubectl",
        aliases=[pb.SpokenAlias(text="cube control", kind=pb.ALIAS_KIND_SPOKEN_FORM)],
        kind=pb.TERM_KIND_COMMAND,
        **kw,
    )


@dataclass
class Outcome:
    responses: list[pb.TranscribeResponse]
    code: grpc.StatusCode
    error: pb.ErrorDetail | None  # from grpc-status-details-bin

    def of(self, kind: str) -> list:
        return [getattr(r, kind) for r in self.responses if r.WhichOneof("msg") == kind]

    @property
    def kinds(self) -> list[str]:
        return [r.WhichOneof("msg") for r in self.responses]

    @property
    def final(self) -> pb.FinalResult:
        (f,) = self.of("final_result")
        return f


async def error_detail(call) -> pb.ErrorDetail | None:
    status = await rpc_status_async.from_call(call)
    if status is None or not status.details:
        return None
    detail = pb.ErrorDetail()
    status.details[0].Unpack(detail)
    return detail


class Client:
    def __init__(self, channel: grpc.aio.Channel) -> None:
        self.channel = channel
        self.asr = pb_grpc.AsrStub(channel)
        self.cleanup = pb_grpc.TextCleanupStub(channel)

    async def transcribe(self, script: list) -> Outcome:
        """Script items: TranscribeRequest (sent) or float (sleep seconds).

        Like a well-behaved client, waits for the server's first response
        after the first message and stops writing after a terminal message.
        (grpcio's Python client replaces the real status with INTERNAL when a
        write races with the server ending the call.)
        """
        first_response, terminal = asyncio.Event(), asyncio.Event()

        async def requests():
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
        responses = []
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

    async def unary(self, method, request):
        """Returns (response | None, code, ErrorDetail | None)."""
        call = method(request)
        try:
            return await call, grpc.StatusCode.OK, None
        except grpc.aio.AioRpcError:
            return None, await call.code(), await error_detail(call)


@pytest.fixture
async def serve():
    """`client = await serve(backend=..., **server_config_overrides)`."""
    started = []

    async def _serve(backend=None, cleanup=None, **server_kw: Any) -> Client:
        cfg = Config()
        cfg.server.listen = "127.0.0.1:0"
        cfg.server.name = "test"
        for k, v in server_kw.items():
            setattr(cfg.server, k, v)
        if cleanup:
            for k, v in cleanup.items():
                setattr(cfg.cleanup, k, v)
        running = await start(cfg, backend or MockBackend())
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{running.port}")
        started.append((running, channel))
        return Client(channel)

    yield _serve
    for running, channel in started:
        await channel.close()
        await running.stop(0)
