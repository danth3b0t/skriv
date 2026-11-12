"""Per-option comparison for docs/bench/2026-11-13-w3-recognition.md.

    uv run python docs/bench/w3-recognition/compare.py [--base v3-default] CONDITION ...

For every condition (a report docs/bench/w3-recognition/CONDITION.json, scored again with the
set's current labels): failed / accepted on all clips and on the 26 real ones, mean recognition
WER (capped at 100%) on all, real and the triage section E clips, E clips whose target word is in
the raw text, false substitutions (trap and real clips), server decode timings (greedy = the anchor, beam) from the
"server" field that docs/bench/w3-recognition/run.py adds, and the clips whose recognized text differs from
--base, with the WER change and failed/accepted state.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import cast

from hark_client import evalset

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SET = ROOT / "eval/dev-v1"
# Triage section E of 2026-11-13-eval-merged.md: the word recognition misses, as a regex on the
# raw (recognized) text. Real clips: the misheard opening/negation, judged by WER only.
TARGETS = {
    "syn-005": r"\bgit\b", "syn-041": r"\bgit push\b", "syn-012": r"\bmain\b",
    "syn-016": r"\bmake\b", "syn-017": r"\badd\b", "syn-019": r"\bterminal\b(?! light)",
    "syn-003": r"good,? ship", "syn-032": r"\bhttpx\b", "syn-042": r"\bzod\b",
    "syn-043": r"\bdir\b", "syn-046": r"\bkube", "syn-049": r"\btauri\b",
    "syn-051": r"3\.12\D+3\.13", "syn-056": r"\bcuda\b", "syn-063": r"\bbar\b",
    "syn-069": r"\bfallback\b", "syn-073": r"\bapp id\b", "syn-074": r"dry",
    "syn-079": r"\bdir\b", "syn-080": r"\bp ?n ?p ?m\b", "syn-082": r"\btraining run\b",
    "syn-090": r"\boffsite\b",
}  # fmt: skip
REAL_E = ("real-002", "real-003", "real-008", "real-009", "real-011", "real-012", "real-013",
          "real-015", "real-017", "real-019", "real-020", "real-024")  # fmt: skip
type Rec = dict[str, object]


def load(name: str) -> dict[str, Rec]:
    out: dict[str, Rec] = {}
    for r in cast("list[Rec]", json.loads((HERE / f"{name}.json").read_text())):
        clip = str(r["clip"])
        lab = evalset.load_label(SET / clip)
        s = evalset.score(clip, lab, str(r["raw"]), str(r["canonical"]), str(r["final"]))
        r["accepted"], r["failed"], r["forbidden"] = s.accepted, s.failed, s.forbidden
        # false substitution as in eval-baseline/analyze.py: a forbidden string (a context
        # term) in the final text of a trap or real-speech clip
        r["false_subs"] = s.forbidden if clip.startswith("real") or "trap" in lab.note else []
        out[clip] = r
    return out


def wer(r: Rec) -> float:
    return min(1.0, float(cast("float", r["wer"] or 0.0)))


def mean(xs: list[float]) -> str:
    return f"{100 * statistics.fmean(xs):.1f}%" if xs else "-"


def ms(rs: list[Rec], key: str) -> str:
    vals = sorted(
        int(cast("dict[str, dict[str, int]]", r.get("server") or {}).get("timings", {}).get(
            "backendMs", {}).get(key, 0))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportCallIssue, reportArgumentType]
        for r in rs
    )  # fmt: skip
    vals = [v for v in vals if v]
    if not vals:
        return "-"
    return f"{statistics.median(vals):.0f} / {vals[int(0.95 * (len(vals) - 1))]}"


def row(name: str, recs: dict[str, Rec]) -> str:
    rs = list(recs.values())
    real = [r for c, r in recs.items() if c.startswith("real")]
    e = [r for c, r in recs.items() if c in TARGETS or c in REAL_E]
    hit = sum(bool(re.search(p, str(recs[c]["raw"]), re.IGNORECASE)) for c, p in TARGETS.items())
    fs = sum(len(cast("list[str]", r["false_subs"])) for r in rs)
    return (
        f"| {name} | {sum(bool(r['failed']) for r in rs)} / {sum(bool(r['accepted']) for r in rs)} "
        f"| {sum(bool(r['failed']) for r in real)} / {sum(bool(r['accepted']) for r in real)} "
        f"| {mean([wer(r) for r in rs])} | {mean([wer(r) for r in real])} "
        f"| {mean([wer(r) for r in e])} | {hit}/{len(TARGETS)} | {fs} "
        f"| {ms(rs, 'greedy')} | {ms(rs, 'beam')} |"
    )


def diff(base: dict[str, Rec], other: dict[str, Rec]) -> list[str]:
    out: list[str] = []
    for c, b in base.items():
        o = other[c]
        if o["raw"] == b["raw"] and o["final"] == b["final"]:
            continue

        def state(r: Rec) -> str:
            return "FAIL" if r["failed"] else "acc" if r["accepted"] else "rej"

        d = wer(o) - wer(b)
        out.append(
            f"  {c} {state(b)}->{state(o)} WER {100 * wer(b):.0f}->{100 * wer(o):.0f}% "
            f"({'+' if d > 0 else ''}{100 * d:.0f}) raw: {o['raw']!r}"
        )
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="v3-default")
    p.add_argument("--diff", action="store_true", help="list clips that differ from --base")
    p.add_argument("conditions", nargs="+")
    a = p.parse_args()
    print(
        "| option | failed / accepted (122) | real failed / accepted (26) | WER all | WER real "
        "| WER E clips | E targets in raw | false substitutions | greedy (anchor) ms p50 / p95 "
        "| beam ms p50 / p95 |\n|---|---|---|---|---|---|---|---|---|---|"
    )
    base = load(a.base)
    loaded = {n: load(n) for n in a.conditions}
    for n, recs in loaded.items():
        print(row(n, recs))
    if a.diff:
        for n, recs in loaded.items():
            if n != a.base:
                print(f"\n## {n} vs {a.base}")
                print("\n".join(diff(base, recs)))


if __name__ == "__main__":
    main()
