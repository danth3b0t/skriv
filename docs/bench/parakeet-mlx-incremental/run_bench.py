"""Incremental (windowed) vs offline decoding in the parakeet_mlx backend, in process.

Usage (repo root; loads the real model, ~5 s):
  uv run python docs/bench/parakeet-mlx-incremental/run_bench.py make-clips OUT_DIR
  uv run python docs/bench/parakeet-mlx-incremental/run_bench.py accuracy OUT_DIR \
      [--short DIR_OF_BOOST_BENCH_CLIPS] [--stride-ms 6000] [--min-ms 0]

Each clip is decoded twice with the same BiasPlan: offline (`Backend.decode`, the
whole utterance at end of audio) and incremental (the production
`IncrementalStream`: audio fed in 40 ms chunks; whenever a window starts the
driver waits for it, as a worker that keeps up with real time would, then
`finish`). Conditions: none (greedy) and shortlist (the 39 boost-bench terms,
beam 4 + boost). Reported: raw WER of each (lower-cased words, edge punctuation
stripped), WER of incremental against offline (how much the text changes),
the end-of-audio cost of each (offline decode vs `finish`), and the windows'
total time per audio second (worker duty while streaming).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "parakeet-mlx-boost"))
from run_bench import edits  # noqa: E402  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

from hark.v1 import hark_pb2 as pb  # noqa: E402
from hark_server.backends.parakeet_mlx import Incremental, IncrementalStream, ParakeetMlxBackend  # noqa: E402

type Json = dict[str, object]


def load(path: Path) -> Json:
    return cast("Json", json.loads(path.read_text()))


def long_clips() -> tuple[Json, list[Json]]:
    spec = load(HERE / "long.json")
    items = cast("list[Json]", spec["clips"])
    by_id = {c["id"]: c for c in items}
    for cat in cast("list[Json]", spec["concat"]):
        parts = [by_id[i] for i in cast("list[str]", cat["of"])]
        items.append({
            "id": cat["id"],
            "spoken": " ".join(str(c["spoken"]) for c in parts),
            "ref": " ".join(str(c["ref"]) for c in parts),
        })  # fmt: skip
    return spec, items


def make_clips(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    spec, items = long_clips()
    for v in cast("list[str]", spec["voices"]):
        for c in items:
            name = out / f"{c['id']}-{v}"
            for cmd in (
                [
                    "say",
                    "-v",
                    v,
                    "-r",
                    str(spec["rate_wpm"]),
                    "-o",
                    f"{name}.aiff",
                    str(c["spoken"]),
                ],
                [
                    "afconvert",
                    "-f",
                    "WAVE",
                    "-d",
                    "LEI16@16000",
                    "-c",
                    "1",
                    f"{name}.aiff",
                    f"{name}.wav",
                ],
            ):
                subprocess.run(cmd, check=True)  # noqa: S603 - fixed macOS tools
            Path(f"{name}.aiff").unlink()


def read_wav(path: Path) -> np.ndarray[tuple[int], np.dtype[np.int16]]:
    import wave  # noqa: PLC0415

    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()


def shortlist() -> list[pb.BiasTerm]:
    t = load(HERE.parent / "parakeet-mlx-boost" / "terms.json")
    return [
        pb.BiasTerm(
            id=cast("str", term["id"]),
            canonical=cast("str", term["canonical"]),
            aliases=[pb.SpokenAlias(text=a) for a in cast("list[str]", term.get("aliases", []))],
            weight=cast("float", term.get("weight", 1.0)),
        )
        for term in cast("list[Json]", t["relevant"]) + cast("list[Json]", t["distractors"])
    ]


async def accuracy(clips: list[tuple[str, Path, str]], backend: ParakeetMlxBackend) -> None:
    window_ms: list[float] = []
    inner = backend.window_sync

    def timed(*args: object) -> object:
        t = time.perf_counter()
        try:
            return inner(*args)  # pyright: ignore[reportArgumentType]
        finally:
            window_ms.append((time.perf_counter() - t) * 1000)

    backend.window_sync = timed  # pyright: ignore[reportAttributeAccessIssue]
    rows: dict[str, dict[str, float]] = {}
    for cond in ("none", "shortlist"):
        plan = backend.plan_bias(shortlist(), 1.0) if cond == "shortlist" else None
        await asyncio.sleep(0.3)  # booster build on the worker
        r = rows.setdefault(cond, dict.fromkeys(("off", "inc", "diff", "n", "audio", "win"), 0.0))
        offline_ms: list[float] = []
        finish_ms: list[float] = []
        for name, wav, ref in clips:
            pcm = read_wav(wav)
            t = time.perf_counter()
            off = await backend.decode(pcm.copy(), plan)
            offline_ms.append((time.perf_counter() - t) * 1000)

            window_ms.clear()
            stream = IncrementalStream(backend, plan)
            for i in range(0, len(pcm), 640):
                stream.feed(pcm[i : i + 640].tobytes())
                running = stream._running  # pyright: ignore[reportPrivateUsage]
                if running is not None:
                    await asyncio.wrap_future(running)
            t = time.perf_counter()
            inc = await stream.finish(pcm, plan)
            finish_ms.append((time.perf_counter() - t) * 1000)
            stream.close()
            r["win"] += sum(window_ms)
            r["audio"] += len(pcm) / 16
            e_off, n = edits(ref, off.text)
            e_inc, _ = edits(ref, inc.text)
            r["off"] += e_off
            r["inc"] += e_inc
            r["n"] += n
            r["diff"] += edits(off.text, inc.text)[0]
            print(
                f"{cond:9s} {name:22s} {len(pcm) / 16000:5.1f}s off {e_off:3d} inc {e_inc:3d} "
                f"end: off {offline_ms[-1]:5.0f} ms, inc {finish_ms[-1]:5.0f} ms "
                f"windows={inc.backend_ms.get('windows', 0)} tail={inc.backend_ms.get('tail_audio', 0)} ms",
                file=sys.stderr,
            )
            if e_off != e_inc:
                print(f"    OFF: {off.text}\n    INC: {inc.text}", file=sys.stderr)
        r["off_p50"], r["off_max"] = statistics.median(offline_ms), max(offline_ms)
        r["inc_p50"], r["inc_max"] = statistics.median(finish_ms), max(finish_ms)
    c = backend.incremental
    print(f"\nstride {c.stride_ms} left {c.left_ms} right {c.right_ms} min {c.min_ms}")
    print(
        "| condition | WER offline | WER incremental | incremental vs offline | end of audio: offline p50 / max | incremental p50 / max | window work per audio s |"
    )
    print("|---|---|---|---|---|---|---|")
    for cond, r in rows.items():
        print(
            f"| {cond} | {r['off'] / r['n']:.3f} | {r['inc'] / r['n']:.3f} | {r['diff'] / r['n']:.3f} | "
            f"{r['off_p50']:.0f} / {r['off_max']:.0f} ms | {r['inc_p50']:.0f} / {r['inc_max']:.0f} ms | "
            f"{r['win'] / r['audio'] * 1000:.0f} ms |"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["make-clips", "accuracy"])
    p.add_argument("out", type=Path)
    p.add_argument("--short", type=Path, help="also the boost bench's 20 clips (their WAV dir)")
    p.add_argument("--only-short", action="store_true")
    d = Incremental()
    p.add_argument("--stride-ms", type=int, default=d.stride_ms)
    p.add_argument("--left-ms", type=int, default=d.left_ms)
    p.add_argument("--right-ms", type=int, default=d.right_ms)
    p.add_argument("--min-ms", type=int, default=0, help="0: windows for every clip")
    a = p.parse_args()
    out = cast("Path", a.out)
    if a.what == "make-clips":
        make_clips(out)
        return
    clips: list[tuple[str, Path, str]] = []
    if not a.only_short:
        spec, items = long_clips()
        clips += [
            (f"{c['id']}-{v}", out / f"{c['id']}-{v}.wav", cast("str", c["ref"]))
            for c in items
            for v in cast("list[str]", spec["voices"])
        ]
    if a.short is not None:
        spec = load(HERE.parent / "parakeet-mlx-boost" / "clips.json")
        for c in cast("list[Json]", spec["clips"]):
            for v in cast("list[str]", spec["voices"]):
                clips.append(
                    (f"{c['id']}-{v}", a.short / f"{c['id']}-{v}.wav", cast("str", c["ref"]))
                )

    async def run() -> None:
        backend = ParakeetMlxBackend()
        backend.incremental = replace(
            backend.incremental,
            stride_ms=a.stride_ms, left_ms=a.left_ms,
            right_ms=a.right_ms, min_ms=a.min_ms,
        )  # fmt: skip
        await backend.warmup()
        await accuracy(clips, backend)

    asyncio.run(run())


if __name__ == "__main__":
    main()
