"""Parakeet TDT GGUF via transcribe.cpp (ggml): CPU anywhere, Metal / Vulkan / CUDA where built.

The offline path for machines without a GPU server in reach (ADR-002 #2). The model
runs in a long-lived worker child (`transcribe_worker`, JSON lines over pipes), the
pattern Superwhisper's asr-worker uses: a crash or a hang in native code costs one
dictation, not the server. A dead worker is restarted on the next decode; a decode
that exceeds `decode_timeout_s` kills it.

Setup: scripts/fetch-transcribe builds the pinned library (build/transcribe/<variant>/lib)
and downloads the GGUF; the worker finds the native library through `library`
(default: $TRANSCRIBE_LIBRARY) and the matching ctypes binding through `binding`
(default: $HARK_TRANSCRIBE_BINDING, else an installed `transcribe_cpp`).
scripts/cpu-server-up sets both.

Capabilities, honestly: transcribe.cpp 0.4 has no vocabulary, prompt or hotword
input for Parakeet (its prompting table lists none; `model.supports("vocabulary")`
is false), so bias is NONE: terms reach the server only for hit detection, and the
client's canonicalization does the rest. No N-best. BUFFERED: the whole utterance is
decoded at end of audio (Parakeet v3 is offline-only in transcribe.cpp). Word times
come from TDT token emission (80 ms frames); word confidence is the lowest token
probability, uncalibrated. Without a bias plan the anchor is the primary result.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np

from skriv.v1 import skriv_pb2 as pb

from . import SAMPLE_RATE, Backend, BackendUnavailable, BiasPlan, DecodeOut, WordOut

if TYPE_CHECKING:
    from collections.abc import Iterator

    from . import Pcm

DEVICES = ("cpu", "metal", "vulkan", "cuda", "rocm", "auto")
log = logging.getLogger("skriv_server.transcribe_cpp")


class WorkerError(RuntimeError):
    pass


class TranscribeCppBackend(Backend):
    def __init__(  # noqa: PLR0913 - keyword-only knobs, set from [backend.options]
        self,
        model: str = "",
        model_id: str = "",
        *,
        device: str = "cpu",
        threads: int = 0,
        library: str = "",
        binding: str = "",
        load_timeout_s: float = 120.0,
        decode_timeout_s: float = 60.0,
    ) -> None:
        if not model:
            raise ValueError(
                "transcribe_cpp needs backend.model = a GGUF path (scripts/fetch-transcribe)"
            )
        path = Path(model).expanduser()
        if not path.is_file():
            raise BackendUnavailable(f"{path}: no such model file; run scripts/fetch-transcribe")
        if device not in DEVICES:
            raise ValueError(f"device must be one of {', '.join(DEVICES)}")
        if threads < 0 or load_timeout_s <= 0 or decode_timeout_s <= 0:
            raise ValueError("need threads >= 0 and positive timeouts")
        super().__init__(model_id or path.stem)
        self.path = path
        self.device = device
        self.threads = threads
        self.library = library or os.environ.get("TRANSCRIBE_LIBRARY", "")
        self.binding = binding or os.environ.get("HARK_TRANSCRIBE_BINDING", "")
        self.load_timeout_s = load_timeout_s
        self.decode_timeout_s = decode_timeout_s
        self.worker: dict[str, object] = {}  # the worker's ready line: device, version, languages
        self.restarts = 0
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        self._seq = 0

    def capabilities(self) -> pb.ModelCapabilities:
        languages = cast("list[str]", self.worker.get("languages", []))
        version = self.worker.get("version", "?")
        return pb.ModelCapabilities(
            display_name=f"Parakeet ({self.path.name}, transcribe.cpp {version}, {self.device})",
            family=self.path.stem,
            is_default=True,
            languages=languages,
            language_detection=len(languages) > 1,
            streaming_mode=pb.STREAMING_MODE_BUFFERED,
            partials=False,
            audio_formats=[
                pb.AudioFormatSupport(
                    encoding=pb.AUDIO_ENCODING_PCM_S16LE, sample_rates_hz=[SAMPLE_RATE]
                )
            ],
            bias=pb.BiasCapabilities(
                mechanism=pb.BIAS_MECHANISM_NONE, midstream_update=pb.MIDSTREAM_UPDATE_FULL
            ),
            free_text=pb.FreeTextCapabilities(),
            n_best=pb.NBestCapabilities(max_n=1, score_kind=pb.SCORE_KIND_UNSPECIFIED),
            timestamps=pb.TimestampCapabilities(
                source=pb.TIMESTAMP_SOURCE_DECODER_EMISSION,
                resolution_ms=80,
                word_confidence=True,
                confidence_calibrated=False,
            ),
            anchor_cost=pb.ANCHOR_COST_FREE,  # no bias reaches the decoder: anchor == primary
            punctuation=True,
            casing=True,
            inverse_text_normalization=False,
        )

    def plan_bias(self, terms: list[pb.BiasTerm], strength: float) -> BiasPlan | None:
        return None  # mechanism NONE: the decoder never sees bias terms

    # ----- worker process ----------------------------------------------------
    def _command(self) -> list[str]:
        return [
            sys.executable, "-m", "skriv_server.backends.transcribe_worker", str(self.path),
            "--device", self.device, "--threads", str(self.threads),
        ]  # fmt: skip

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.library:
            env["TRANSCRIBE_LIBRARY"] = str(Path(self.library).expanduser())
        if self.binding:
            path = [str(Path(self.binding).expanduser()), env.get("PYTHONPATH", "")]
            env["PYTHONPATH"] = os.pathsep.join(p for p in path if p)
        return env

    def _spawn(self) -> None:
        proc = subprocess.Popen(  # noqa: S603 - our own interpreter and module
            self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=self._env(),
        )
        lines: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        stdout = proc.stdout
        if stdout is None:
            raise WorkerError("worker has no stdout pipe")

        def read() -> None:
            for line in cast("Iterator[str]", stdout):
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read, daemon=True, name="transcribe-worker-read").start()
        self._proc, self._lines = proc, lines
        ready = self._reply(self.load_timeout_s, "load")
        if ready.get("ready") is not True:
            self._kill()
            raise BackendUnavailable(f"transcribe_cpp worker failed to load: {ready.get('error')}")
        self.worker = ready
        log.info(
            "transcribe.cpp %s worker ready on %s in %s ms (pid %d)",
            ready.get("version"), ready.get("device"), ready.get("load_ms"), proc.pid,
        )  # fmt: skip

    def _reply(self, timeout_s: float, what: str) -> dict[str, object]:
        try:
            line = self._lines.get(timeout=timeout_s)
        except queue.Empty:
            self._kill()
            raise WorkerError(
                f"transcribe_cpp worker: no {what} reply in {timeout_s:g} s; killed"
            ) from None
        if line is None:
            code = self._kill()
            raise WorkerError(f"transcribe_cpp worker exited (code {code}) during {what}")
        try:
            return cast("dict[str, object]", json.loads(line))
        except json.JSONDecodeError:
            self._kill()
            raise WorkerError(f"transcribe_cpp worker: bad {what} reply {line[:80]!r}") from None

    def _kill(self) -> int | None:
        proc, self._proc = self._proc, None
        if proc is None:
            return None
        if proc.poll() is None:
            proc.kill()
        try:
            return proc.wait(5)
        except subprocess.TimeoutExpired:
            return None

    def close(self) -> None:
        super().close()
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            if proc.stdin is not None:
                proc.stdin.close()  # EOF: the worker exits
            try:
                proc.wait(2)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ----- Backend ------------------------------------------------------------
    def _load_sync(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()

    def _warmup_sync(self) -> None:
        rng = np.random.default_rng(0)
        self._decode_sync((rng.standard_normal(SAMPLE_RATE) * 300).astype(np.int16), None)
        ten = (rng.standard_normal(10 * SAMPLE_RATE) * 300).astype(np.int16)
        t0 = time.perf_counter()
        self._decode_sync(ten, None)
        ms = (time.perf_counter() - t0) * 1000
        self.perf.finalize_ms_10s = int(ms)
        self.perf.rtfx = 10_000 / ms if ms > 0 else 0.0

    def _decode_sync(self, pcm: Pcm, plan: BiasPlan | None) -> DecodeOut:
        if self._proc is None or self._proc.poll() is not None:
            if self.worker:  # it ran before: a crash or a timeout killed it
                self.restarts += 1
                log.warning("transcribe_cpp worker is gone; restarting (restart %d)", self.restarts)
            self._spawn()
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise WorkerError("transcribe_cpp worker is not running")
        self._seq += 1
        data = base64.b64encode(pcm.astype("<i2").tobytes()).decode("ascii")
        t0 = time.perf_counter()
        try:
            proc.stdin.write(json.dumps({"id": self._seq, "pcm": data}) + "\n")
            proc.stdin.flush()
        except OSError as e:  # BrokenPipeError: it died between decodes
            code = self._kill()
            raise WorkerError(f"transcribe_cpp worker exited (code {code})") from e
        reply = self._reply(self.decode_timeout_s, "decode")
        rtt = (time.perf_counter() - t0) * 1000
        if reply.get("id") != self._seq:
            self._kill()
            raise WorkerError(
                f"transcribe_cpp worker answered request {reply.get('id')}, not {self._seq}"
            )
        if "error" in reply:
            raise WorkerError(f"transcribe_cpp: {reply['error']}")
        return _result(reply, rtt)


def _num(reply: dict[str, object], key: str) -> float:
    v = reply.get(key, 0)
    return float(v) if isinstance(v, int | float) else 0.0


def _result(reply: dict[str, object], rtt_ms: float) -> DecodeOut:
    words = [
        WordOut(str(w[0]), int(w[1]), int(w[2]), None if w[3] is None else float(w[3]))
        for w in cast("list[tuple[str, int, int, float | None]]", reply.get("words", []))
    ]
    mel, enc, dec = _num(reply, "mel_ms"), _num(reply, "encode_ms"), _num(reply, "decode_ms")
    return DecodeOut(
        text=str(reply.get("text", "")),
        words=words,
        encoder_ms=round(mel + enc),
        decode_ms=round(dec),
        backend_ms={"worker_ipc": max(0, round(rtt_ms - mel - enc - dec))},
    )
