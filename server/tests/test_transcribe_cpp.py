"""transcribe_cpp backend: worker crash isolation and timeouts (fake worker), and the real
model through the server when scripts/fetch-transcribe has been run."""

from __future__ import annotations

import os
import sys
import time
import wave
from pathlib import Path
from typing import TYPE_CHECKING, override

import numpy as np
import pytest
from conftest import utterance

from hark.v1 import hark_pb2 as pb
from hark_server.backends import BackendUnavailable
from hark_server.backends.transcribe_cpp import TranscribeCppBackend, WorkerError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from conftest import Serve

CRASH, HANG = 1234, 4321  # sample counts that make the fake worker crash / hang
FAKE_WORKER = f"""
import base64, json, os, sys, time
if sys.argv[1] == "fail":
    print(json.dumps({{"ready": False, "error": "no native library"}}), flush=True)
    sys.exit(1)
print(json.dumps({{"ready": True, "version": "0.0", "languages": ["en", "de"]}}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    n = len(base64.b64decode(req["pcm"])) // 2
    if n == {CRASH}:
        os._exit(3)
    if n == {HANG}:
        time.sleep(60)
    words = [[str(n), 0, 80, 0.5]]
    reply = {{"id": req["id"], "text": f"{{n}} samples", "words": words, "encode_ms": 2, "decode_ms": 3}}
    print(json.dumps(reply), flush=True)
"""


class FakeWorkerBackend(TranscribeCppBackend):
    def __init__(self, tmp: Path, mode: str = "ok", decode_timeout_s: float = 10.0) -> None:
        model = tmp / "fake.gguf"
        model.write_bytes(b"")
        (tmp / "worker.py").write_text(FAKE_WORKER)
        super().__init__(str(model), decode_timeout_s=decode_timeout_s)
        self.mode = mode
        self.tmp = tmp

    @override
    def _command(self) -> list[str]:
        return [sys.executable, str(self.tmp / "worker.py"), self.mode]

    def pid(self) -> int:
        assert self._proc is not None
        return self._proc.pid


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[FakeWorkerBackend]:
    b = FakeWorkerBackend(tmp_path, decode_timeout_s=0.5)
    yield b
    b.close()


def pcm(n: int) -> np.ndarray[tuple[int], np.dtype[np.int16]]:
    return np.zeros(n, np.int16)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_a_worker_crash_fails_one_decode_and_the_next_decode_restarts_it(
    backend: FakeWorkerBackend,
):
    await backend.load()
    with pytest.raises(WorkerError, match=r"exited \(code 3\)"):
        await backend.decode(pcm(CRASH), None)
    out = await backend.decode(pcm(16000), None)
    assert (out.text, out.words[0].confidence, backend.restarts) == ("16000 samples", 0.5, 1)


async def test_a_hung_worker_is_killed_at_the_decode_timeout(backend: FakeWorkerBackend):
    await backend.load()
    pid = backend.pid()
    t0 = time.perf_counter()
    with pytest.raises(WorkerError, match=r"no decode reply in 0\.5 s"):
        await backend.decode(pcm(HANG), None)
    assert time.perf_counter() - t0 < 3
    assert not alive(pid)


async def test_a_worker_that_cannot_load_makes_the_backend_unavailable(tmp_path: Path):
    b = FakeWorkerBackend(tmp_path, mode="fail")
    with pytest.raises(BackendUnavailable, match="no native library"):
        await b.load()
    b.close()


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "models" / "parakeet-tdt-0.6b-v3-Q4_K_M.gguf"
SAMPLE = next(ROOT.glob("build/transcribe.cpp-*/samples/jfk.wav"), None)
NATIVE = [p for p in ROOT.glob("build/transcribe/*/lib") if p.exists()]


@pytest.mark.skipif(
    not (MODEL.is_file() and SAMPLE and NATIVE), reason="needs scripts/fetch-transcribe"
)
async def test_real_model_transcribes_on_cpu_and_reports_no_decode_time_bias(serve: Serve):
    assert SAMPLE is not None
    with wave.open(str(SAMPLE), "rb") as w:  # 16 kHz mono PCM16 (11 s)
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    backend = TranscribeCppBackend(
        str(MODEL), library=str(NATIVE[0]), binding=str(ROOT / "build" / "transcribe" / "python")
    )
    client = await serve(backend=backend)
    term = pb.BiasTerm(id="t1", canonical="JFK")
    out = await client.transcribe(
        utterance(
            audio,
            context=pb.RecognitionContext(bias=pb.BiasList(terms=[term])),
            options=pb.ResultOptions(word_timestamps=True, word_confidence=True),
        )
    )
    final = out.final
    assert "ask not what your country can do for you" in final.n_best[0].text.lower()
    assert final.n_best[0].words[0].HasField("confidence")
    assert final.flags.anchor == pb.FIELD_STATUS_PRESENT  # anchor == primary, for free
    (model,) = (await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())).models
    assert model.bias.mechanism == pb.BIAS_MECHANISM_NONE
    assert "en" in model.languages
