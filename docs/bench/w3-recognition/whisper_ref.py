"""Quality reference: whisper.cpp large-v3-turbo on every clip of eval/dev-v1.

    uv run python docs/bench/w3-recognition/whisper_ref.py MODEL.bin OUT.json [--prompt]

`whisper-cli -l en -nt` (beam 5, best of 5, Metal), no context; --prompt passes the clip's
replayed shortlist (canonical spellings, as `retry.load_snapshot` returns them, first 30) as
the initial prompt, Whisper's only way to take context. Writes per clip the text and the
model time (total - load, ms), and prints WER against `said` and the section E targets for
the bench note. Not part of hark: a bound on what better acoustics would buy.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from hark_client import evalset, retry

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compare import REAL_E, SET, TARGETS  # noqa: E402


def run(model: str, clip: Path, prompt: str) -> tuple[str, float]:
    cmd = ["whisper-cli", "-m", model, "-f", str(clip / "audio.wav"), "-nt", "-l", "en"]
    if prompt:
        cmd += ["--prompt", prompt]
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603
    total = float(re.search(r"total time =\s*([\d.]+)", p.stderr).group(1))  # pyright: ignore[reportOptionalMemberAccess]
    load = float(re.search(r"load time =\s*([\d.]+)", p.stderr).group(1))  # pyright: ignore[reportOptionalMemberAccess]
    return " ".join(p.stdout.split()), total - load


def main() -> None:
    model, out = sys.argv[1], Path(sys.argv[2])
    use_prompt = "--prompt" in sys.argv
    recs: list[dict[str, object]] = []
    for clip in evalset.clips(SET):
        lab = evalset.load_label(clip)
        prompt = ""
        if use_prompt:
            snap = retry.load_snapshot(retry.Entry(clip.name, clip, {}))
            prompt = ", ".join(t.canonical for t in snap.terms[:30])
        text, ms = run(model, clip, prompt)
        recs.append({"clip": clip.name, "raw": text, "wer": evalset.wer(lab.said, text), "ms": ms})
        print(clip.name, f"{ms:.0f} ms", text, flush=True)
    out.write_text(json.dumps(recs, indent=1) + "\n")
    hit = sum(
        bool(re.search(p, str(r["raw"]), re.IGNORECASE)) for r in recs for c, p in TARGETS.items()
        if r["clip"] == c
    )  # fmt: skip

    def mean(rs: list[dict[str, object]]) -> str:
        return f"{100 * sum(min(1.0, float(r['wer'])) for r in rs) / len(rs):.1f}%"  # pyright: ignore[reportArgumentType]

    real = [r for r in recs if str(r["clip"]).startswith("real")]
    e = [r for r in recs if r["clip"] in TARGETS or r["clip"] in REAL_E]
    ms = sorted(float(r["ms"]) for r in recs)  # pyright: ignore[reportArgumentType]
    print(
        f"WER all {mean(recs)} real {mean(real)} E {mean(e)}; E targets {hit}/{len(TARGETS)}; "
        f"model ms p50 {ms[len(ms) // 2]:.0f} p95 {ms[int(0.95 * (len(ms) - 1))]:.0f}"
    )


if __name__ == "__main__":
    main()
