"""transcribe.cpp worker child: `python -m hark_server.backends.transcribe_worker MODEL.gguf`.

Runs one transcribe.cpp model through its ctypes Python binding (`transcribe_cpp`,
the version that matches the native library: see scripts/fetch-transcribe) and
talks JSON lines on stdin/stdout, so a native crash takes down this process and
not hark-server (the backend restarts it):

  -> stdout {"ready": true, "load_ms": .., "device": "cpu", "version": "0.4.2", "languages": [..]}
            or {"ready": false, "error": ".."} (then exit 1)
  <- stdin  {"id": 1, "pcm": "<base64 PCM s16le, 16 kHz mono>"}
  -> stdout {"id": 1, "text": .., "words": [[text, start_ms, end_ms, confidence], ..],
             "language": .., "mel_ms": .., "encode_ms": .., "decode_ms": ..}
            or {"id": 1, "error": ".."}
EOF on stdin ends the worker. Native code that writes to fd 1 is redirected to
stderr, so stdout carries the protocol only. Word confidence = the lowest token
probability in the word (uncalibrated).
"""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import math
import os
import sys
import time
from typing import IO, TYPE_CHECKING, Protocol, cast

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import numpy.typing as npt


class _Word(Protocol):
    text: str
    t0_ms: int
    t1_ms: int
    first_token: int
    n_tokens: int


class _Token(Protocol):
    p: float


class _Timings(Protocol):
    mel_ms: float
    encode_ms: float
    decode_ms: float


class _Result(Protocol):
    text: str
    language: str
    words: Sequence[_Word]
    tokens: Sequence[_Token]
    timings: _Timings


class _Session(Protocol):
    def run(self, pcm: npt.NDArray[np.float32], *, timestamps: str) -> _Result: ...


class _Device(Protocol):
    name: str
    kind: str


class _Capabilities(Protocol):
    languages: Sequence[str]


class _Model(Protocol):
    device: _Device
    capabilities: _Capabilities

    def session(self, *, n_threads: int = 0) -> _Session: ...


class _Binding(Protocol):
    """The part of transcribe_cpp (0.4) used here; imported at run time because it
    is not a dependency of hark-server (it comes with the native build)."""

    __version__: str

    def Model(self, path: str, *, backend: str = "auto") -> _Model: ...  # noqa: N802 - binding's name
    def set_log_callback(self, handler: Callable[[int, str], None] | None) -> None: ...


class _Args(argparse.Namespace):
    # Annotations only: argparse fills in the values.
    model: str
    device: str
    threads: int


LOG_WARN, LOG_ERROR = 2, 3  # TRANSCRIBE_LOG_LEVEL_*; info (1) and debug (4) are per-run chatter


def words_out(result: _Result) -> list[tuple[str, int, int, float | None]]:
    out: list[tuple[str, int, int, float | None]] = []
    for w in result.words:
        probs = [t.p for t in result.tokens[w.first_token : w.first_token + w.n_tokens]]
        conf = min(probs) if probs and not any(math.isnan(p) for p in probs) else None
        out.append((w.text, w.t0_ms, w.t1_ms, conf))
    return out


def serve(session: _Session, requests: IO[str], out: IO[str]) -> None:
    for line in requests:
        if not line.strip():
            continue
        rid: object = None
        try:
            req = cast("dict[str, object]", json.loads(line))
            rid = req.get("id")
            data = base64.b64decode(cast("str", req["pcm"]))
            pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            if len(pcm) == 0:
                reply: dict[str, object] = {"id": rid, "text": "", "words": []}
            else:
                r = session.run(pcm, timestamps="token")
                reply = {
                    "id": rid,
                    "text": r.text.strip(),
                    "words": words_out(r),
                    "language": r.language,
                    "mel_ms": r.timings.mel_ms,
                    "encode_ms": r.timings.encode_ms,
                    "decode_ms": r.timings.decode_ms,
                }
        except Exception as e:  # noqa: BLE001 - report per request; the worker keeps serving
            reply = {"id": rid, "error": f"{type(e).__name__}: {e}"}
        out.write(json.dumps(reply) + "\n")
        out.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="transcribe_worker")
    p.add_argument("model", help="GGUF model path")
    p.add_argument("--device", default="cpu", help="cpu | metal | vulkan | cuda | rocm | auto")
    p.add_argument("--threads", type=int, default=0, help="CPU threads (0: library default)")
    a = p.parse_args(argv, namespace=_Args())

    # The protocol owns the original stdout; anything else written to fd 1 goes to stderr.
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    t0 = time.perf_counter()
    try:
        tc = cast("_Binding", importlib.import_module("transcribe_cpp"))

        def log(level: int, msg: str) -> None:
            if level in (LOG_WARN, LOG_ERROR):
                sys.stderr.write(msg if msg.endswith("\n") else msg + "\n")

        tc.set_log_callback(log)
        model = tc.Model(a.model, backend=a.device)
        session = model.session(n_threads=a.threads)
    except Exception as e:  # noqa: BLE001 - ImportError, TranscribeError, OSError: all fatal here
        out.write(json.dumps({"ready": False, "error": f"{type(e).__name__}: {e}"}) + "\n")
        out.flush()
        return 1
    ready = {
        "ready": True,
        "load_ms": round((time.perf_counter() - t0) * 1000),
        "device": f"{model.device.kind}:{model.device.name}",
        "version": tc.__version__,
        "languages": list(model.capabilities.languages),
    }
    out.write(json.dumps(ready) + "\n")
    out.flush()
    serve(session, sys.stdin, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
