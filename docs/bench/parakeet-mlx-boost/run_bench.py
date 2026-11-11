"""Accuracy + latency bench for parakeet_mlx decode-time bias, through hark-probe.

Usage (from the repo root, server already running, clips made by make_clips):
  uv run python docs/bench/parakeet-mlx-boost/run_bench.py make-clips OUT_DIR
  uv run python docs/bench/parakeet-mlx-boost/run_bench.py accuracy OUT_DIR --server HOST:PORT
  uv run python docs/bench/parakeet-mlx-boost/run_bench.py latency OUT_DIR --server HOST:PORT \
      --clips kubectl-Samantha,ten-Samantha,twenty-Samantha --repeat 20

Conditions: none (no terms -> greedy); posthoc (shortlist with strength 0:
greedy, terms only used for hits/canonicalization); relevant (the clip's
terms); shortlist (all relevant + distractor terms, 39); distractors
(distractor terms only).
Metrics: WER on lower-cased words with edge punctuation stripped, raw and after
client-style canonicalization (every BiasHit span replaced by its canonical);
term recall (clip terms whose canonical is in the canonicalized text); false
APPLIED hits (hits on terms not in the clip that the anchor did not have).
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import cast

HERE = Path(__file__).resolve().parent
PROBE = HERE.parents[2] / "tools" / "hark-probe" / "hark-probe"
type Json = dict[str, object]


def load(name: str) -> Json:
    return cast("Json", json.loads((HERE / name).read_text()))


def clips() -> tuple[list[str], list[Json]]:
    m = load("clips.json")
    return cast("list[str]", m["voices"]), cast("list[Json]", m["clips"])


def terms() -> tuple[list[Json], list[Json]]:
    t = load("terms.json")
    return cast("list[Json]", t["relevant"]), cast("list[Json]", t["distractors"])


def make_clips(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    voices, items = clips()
    for v in voices:
        for c in items:
            name = out / f"{c['id']}-{v}"
            aiff, wav = f"{name}.aiff", f"{name}.wav"
            for cmd in (
                ["say", "-v", v, "-o", aiff, str(c["spoken"])],
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav],
            ):
                subprocess.run(cmd, check=True)  # noqa: S603 - fixed macOS tools
            Path(f"{name}.aiff").unlink()


def words(s: str) -> list[str]:
    return [w for w in (x.strip(".,!?;:\"'").lower() for x in s.split()) if w]


def edits(ref: str, hyp: str) -> tuple[int, int]:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)], len(r)


def canonicalize(final: Json, canon: dict[str, str]) -> str:
    text = cast("str", cast("list[Json]", final["n_best"])[0].get("text", "")).encode()
    hits = cast("list[Json]", final.get("bias_hits", []))
    for h in sorted(hits, key=lambda h: -cast("int", h.get("text_byte_start", 0))):
        a, b = cast("int", h.get("text_byte_start", 0)), cast("int", h.get("text_byte_end", 0))
        text = text[:a] + canon[cast("str", h["term_id"])].encode() + text[b:]
    return text.decode()


def probe(server: str, wav: Path, bias: Path | None, extra: list[str]) -> tuple[Json, Json]:
    summary = wav.with_suffix(".summary.json")
    cmd = [
        str(PROBE),
        str(wav),
        "--server",
        server,
        "--words",
        "--quiet",
        "--json-summary",
        str(summary),
        *extra,
    ]
    if bias is not None:
        cmd += ["--bias", str(bias)]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout  # noqa: S603
    return cast("Json", json.loads(out)), cast("Json", json.loads(summary.read_text()))


def bias_file(out: Path, name: str, ts: list[Json], strength: float = 1.0) -> Path:
    p = out / f"bias-{name}.json"
    p.write_text(json.dumps({"terms": ts, "strength": strength}))
    return p


def conditions(out: Path, clip: Json, rel: list[Json], dis: list[Json]) -> dict[str, Path | None]:
    mine = [t for t in rel if t["id"] in cast("list[str]", clip["terms"])]
    return {
        "none": None,
        "posthoc": bias_file(out, "posthoc", rel + dis, strength=0.0),
        "relevant": bias_file(out, f"relevant-{clip['id']}", mine),
        "shortlist": bias_file(out, "shortlist", rel + dis),
        "distractors": bias_file(out, "distractors", dis),
    }


def accuracy(out: Path, server: str) -> None:
    voices, items = clips()
    rel, dis = terms()
    canon = {cast("str", t["id"]): cast("str", t["canonical"]) for t in rel + dis}
    rows: dict[str, dict[str, float]] = {}
    for c in items:
        for cond, bias in conditions(out, c, rel, dis).items():
            row = rows.setdefault(
                cond, dict.fromkeys(("e", "ce", "n", "rec", "terms", "false", "disc", "ae"), 0.0)
            )
            for v in voices:
                final, _ = probe(server, out / f"{c['id']}-{v}.wav", bias, ["--fast"])
                ref, mine = cast("str", c["ref"]), cast("list[str]", c["terms"])
                text = cast("str", cast("list[Json]", final["n_best"])[0].get("text", ""))
                fixed = canonicalize(final, canon)
                e, n = edits(ref, text)
                row["e"] += e
                row["n"] += n
                row["ce"] += edits(ref, fixed)[0]
                row["ae"] += edits(
                    ref, cast("str", cast("Json", final.get("anchor", {})).get("text", text))
                )[0]
                row["terms"] += len(mine)
                row["rec"] += sum(canon[t].lower() in fixed.lower() for t in mine)
                hits = cast("list[Json]", final.get("bias_hits", []))
                applied = [h for h in hits if h.get("decision") == "BIAS_HIT_DECISION_APPLIED"]
                row["false"] += sum(h["term_id"] not in mine for h in applied)
                row["disc"] += "beam_discarded" in cast(
                    "Json", cast("Json", final["timings"]).get("backend_ms", {})
                )
                print(f"{cond:11s} {c['id']}-{v}: {text} || {fixed}", file=sys.stderr)
    print(
        "| condition | WER raw | WER canonicalized | anchor WER | term recall | false APPLIED hits | beam discarded |"
    )
    print("|---|---|---|---|---|---|---|")
    for cond, r in rows.items():
        print(
            f"| {cond} | {r['e'] / r['n']:.3f} | {r['ce'] / r['n']:.3f} | {r['ae'] / r['n']:.3f} | "
            f"{int(r['rec'])}/{int(r['terms'])} | {int(r['false'])} | {int(r['disc'])} |"
        )


def latency(out: Path, server: str, names: list[str], repeat: int) -> None:
    rel, dis = terms()
    print(
        "| clip | audio s | condition | eoa→final p50 | p95 | max | server: encoder / decode / anchor (p50) |"
    )
    print("|---|---|---|---|---|---|---|")
    for name in names:
        clip_id = name.rsplit("-", 1)[0]
        clip = next(c for c in clips()[1] if c["id"] == clip_id)
        conds = conditions(out, clip, rel, dis)
        for cond in ("none", "shortlist"):
            final, summary = probe(
                server, out / f"{name}.wav", conds[cond], ["--repeat", str(repeat)]
            )
            e2f = cast("Json", summary["eoa_to_final_ms"])
            runs = cast("list[Json]", summary["runs"])
            enc = statistics.median(cast("float", r["encoder_ms"]) for r in runs)
            dec = statistics.median(cast("float", r["decode_ms"]) for r in runs)
            anchor = cast("Json", final["timings"]).get("anchor_ms", 0)
            print(
                f"| {name} | {cast('float', runs[0]['audio_s']):.1f} | {cond} | {cast('float', e2f['p50']):.0f} | "
                f"{cast('float', e2f['p95']):.0f} | {cast('float', e2f['max']):.0f} | {enc:.0f} / {dec:.0f} / {anchor} |",
                flush=True,
            )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["make-clips", "accuracy", "latency"])
    p.add_argument("out", type=Path)
    p.add_argument("--server", default="127.0.0.1:50051")
    p.add_argument("--clips", default="kubectl-Samantha,ten-Samantha,twenty-Samantha")
    p.add_argument("--repeat", type=int, default=20)
    a = p.parse_args()
    out, server, what = cast("Path", a.out), cast("str", a.server), cast("str", a.what)
    if what == "make-clips":
        make_clips(out)
    elif what == "accuracy":
        accuracy(out, server)
    else:
        latency(out, server, cast("str", a.clips).split(","), cast("int", a.repeat))


if __name__ == "__main__":
    main()
