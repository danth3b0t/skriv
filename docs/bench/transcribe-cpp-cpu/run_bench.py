"""Latency and WER of the transcribe_cpp backend (Parakeet TDT 0.6B v3 GGUF) on 3/10/20/60 s TTS clips.

Usage (repo root, after scripts/fetch-transcribe):
  uv run python docs/bench/transcribe-cpp-cpu/run_bench.py make-clips OUT_DIR
  uv run python docs/bench/transcribe-cpp-cpu/run_bench.py run OUT_DIR \
      [--config cpu:0 --config cpu:4 --config cpu:2 --config metal:0] [--mlx] [--reps 5]

Clips: the first 9/29/58/180 words from the `review` and `cluster` texts of the incremental
bench (150 wpm, macOS `say`, Samantha and Daniel). Each configuration (device:threads) runs
the production backend in process: the worker child is spawned, warmed up like the server
does, then every clip is decoded `reps` times; reported is the median end-to-end decode
(IPC included) and its split into encoder / decoder, the real-time factor, the worker's
resident memory, and WER against the spoken text (lower-cased, edge punctuation stripped).
`--mlx` adds the parakeet_mlx backend's greedy decode (bf16, Metal) as the reference.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "parakeet-mlx-boost"))
from run_bench import edits  # noqa: E402  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

from hark_server.backends.transcribe_cpp import TranscribeCppBackend  # noqa: E402

if TYPE_CHECKING:
    import numpy.typing as npt

    from hark_server.backends import Backend

WORDS = {"3s": 9, "10s": 29, "20s": 58, "60s": 180}  # `say -r 150` speaks ~175 wpm
TEXTS = ("review", "cluster")
VOICES = ("Samantha", "Daniel")
MODEL = ROOT / "models" / "parakeet-tdt-0.6b-v3-Q4_K_M.gguf"


def clip_texts() -> dict[str, str]:
    spec = cast(
        "dict[str, object]",
        json.loads((HERE.parent / "parakeet-mlx-incremental" / "long.json").read_text()),
    )
    order = [(c["id"], c["spoken"]) for c in cast("list[dict[str, str]]", spec["clips"])]
    out: dict[str, str] = {}
    for tid in TEXTS:
        start = next(i for i, (cid, _) in enumerate(order) if cid == tid)
        words = " ".join(text for _, text in order[start:]).split()  # runs into the next text
        for name, n in WORDS.items():
            out[f"{name}-{tid}"] = " ".join(words[:n]).rstrip(",.;:") + "."
    return out


def make_clips(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    texts = clip_texts()
    for cid, text in texts.items():
        for v in VOICES:
            name = out / f"{cid}-{v}"
            subprocess.run(["say", "-v", v, "-r", "150", "-o", f"{name}.aiff", text], check=True)  # noqa: S603, S607
            convert = ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1"]
            subprocess.run([*convert, f"{name}.aiff", f"{name}.wav"], check=True)  # noqa: S603 - macOS tool
            Path(f"{name}.aiff").unlink()
    (out / "texts.json").write_text(json.dumps(texts, indent=1))


def read_wav(path: Path) -> npt.NDArray[np.int16]:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()


def rss_mb(pid: int) -> float:
    argv = ["ps", "-o", "rss=", "-p", str(pid)]
    out = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    return int(out.stdout.strip() or 0) / 1024


async def bench(
    label: str, backend: Backend, clips: list[tuple[str, npt.NDArray[np.int16], str]], reps: int
) -> list[dict[str, object]]:
    t0 = time.perf_counter()
    await backend.load()
    load_ms = (time.perf_counter() - t0) * 1000
    warm_ms = await backend.warmup()
    print(
        f"# {label}: load {load_ms:.0f} ms, warmup {warm_ms} ms, rtfx(10 s noise) {backend.perf.rtfx:.1f}",
        flush=True,
    )
    rows: list[dict[str, object]] = []
    for name, pcm, ref in clips:
        times: list[float] = []
        enc: list[int] = []
        dec: list[int] = []
        text = ""
        for _ in range(reps):
            fresh = pcm.copy()  # parakeet_mlx caches encoder output per audio array
            t = time.perf_counter()
            out = await backend.decode(fresh, None)
            times.append((time.perf_counter() - t) * 1000)
            enc.append(out.encoder_ms)
            dec.append(out.decode_ms)
            text = out.text
        e, n = cast("tuple[int, int]", edits(ref, text))
        audio_s = len(pcm) / 16000
        rows.append({
            "config": label, "clip": name, "audio_s": round(audio_s, 2),
            "ms_p50": round(statistics.median(times)), "ms_min": round(min(times)),
            "encoder_ms": round(statistics.median(enc)), "decoder_ms": round(statistics.median(dec)),
            "rtfx": round(audio_s * 1000 / statistics.median(times), 1), "errors": e, "words": n,
        })  # fmt: skip
        print(json.dumps(rows[-1]), flush=True)
    if isinstance(backend, TranscribeCppBackend) and backend._proc is not None:
        print(f"# {label}: worker RSS {rss_mb(backend._proc.pid):.0f} MB", flush=True)
    backend.close()
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["make-clips", "run"])
    p.add_argument("dir", type=Path)
    p.add_argument(
        "--config", action="append", help="device:threads (default cpu:0 cpu:4 cpu:2 metal:0)"
    )
    p.add_argument("--mlx", action="store_true")
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--json", type=Path, help="write all rows here")
    a = p.parse_args()
    if cast("str", a.cmd) == "make-clips":
        make_clips(cast("Path", a.dir))
        return
    clip_dir = cast("Path", a.dir)
    texts = cast("dict[str, str]", json.loads((clip_dir / "texts.json").read_text()))
    clips = [
        (f"{cid}-{v}", read_wav(clip_dir / f"{cid}-{v}.wav"), text)
        for cid, text in texts.items()
        for v in VOICES
    ]
    lib = next(p for p in (ROOT / "build" / "transcribe").glob("*/lib"))
    binding = ROOT / "build" / "transcribe" / "python"
    rows: list[dict[str, object]] = []

    async def run() -> None:
        for cfg in cast("list[str] | None", a.config) or ["cpu:0", "cpu:4", "cpu:2", "metal:0"]:
            device, threads = cfg.split(":")
            backend = TranscribeCppBackend(
                str(MODEL),
                device=device,
                threads=int(threads),
                library=str(lib),
                binding=str(binding),
            )
            rows.extend(await bench(cfg, backend, clips, cast("int", a.reps)))
        if a.mlx:
            from hark_server.backends.parakeet_mlx import ParakeetMlxBackend  # noqa: PLC0415

            rows.extend(
                await bench(
                    "mlx-greedy",
                    ParakeetMlxBackend(incremental_min_ms=-1),
                    clips,
                    cast("int", a.reps),
                )
            )

    asyncio.run(run())
    if a.json:
        cast("Path", a.json).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
