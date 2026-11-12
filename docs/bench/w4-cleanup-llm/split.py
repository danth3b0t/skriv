"""Tables for docs/bench/2026-11-13-w4-cleanup-llm.md: `skriv eval run --report` files scored
per half of eval/dev-v1.

    uv run python docs/bench/w4-cleanup-llm/split.py REPORT.json [REPORT.json ...]

The halves are fixed by clip number parity: odd (real-001, syn-003, ...) is the tuning half, even
is held out. The cleanup prompt (prompt v2) was written after reading failures on all of
dev-v1; nothing in this branch was tuned on the even half. Labels are read from the set, as in
docs/bench/eval-baseline/analyze.py.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path
from typing import cast

from skriv_client import evalset

ROOT = Path(__file__).resolve().parents[3]
SET = ROOT / "eval" / "dev-v1"
FILLERS = re.compile(r"\b(um+|uh+|erm|er|eh|mm+|hmm+|ah)\b", re.IGNORECASE)
type Rec = dict[str, object]


def half(clip: str) -> str:
    return "tune" if int(clip.rsplit("-", 1)[1]) % 2 else "held-out"


def pct(vals: list[float]) -> str:
    if not vals:
        return "-"
    vals = sorted(vals)
    p95 = vals[min(len(vals) - 1, round(0.95 * (len(vals) - 1)))]
    return f"{statistics.median(vals):.0f} / {p95:.0f}"


def row(name: str, recs: list[Rec]) -> str:
    failed = accepted = crit = crit_n = fs = fs_n = neg = fill = 0
    for r in recs:
        clip = str(r["clip"])
        lab = evalset.load_label(SET / clip)
        final = str(r["final"])
        s = evalset.score(clip, lab, str(r["raw"]), str(r["canonical"]), final)
        failed += s.failed
        accepted += s.accepted
        crit_n += len(lab.critical)
        crit += sum(c in final for c in lab.critical)
        if clip.startswith("real") or "trap" in lab.note:
            fs_n += len(lab.forbidden)
            fs += sum(f in final for f in lab.forbidden)
        neg += s.negations_lost and not s.accepted
        fill += bool(FILLERS.search(final))
    t = [cast("dict[str, float]", r["timings_ms"]) for r in recs]

    def ms(key: str) -> str:
        return pct([x[key] for x in t if key in x])

    return (
        f"| {name} | {len(recs)} | {failed} | {accepted} | {crit}/{crit_n} ({crit / crit_n:.0%}) "
        f"| {fs}/{fs_n} | {neg} | {fill} | {ms('cleanup_ms')} | {ms('keyup_to_paste_ms')} "
        f"| {ms('prime_ms')} |"
    )


def main() -> None:
    print(
        "| report / half | clips | failed | accepted | critical in final | false subs | "
        "negations lost | finals with fillers | cleanup ms p50 / p95 | key-up -> paste ms "
        "p50 / p95 | priming ms p50 / p95 |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for path in sys.argv[1:]:
        recs = cast("list[Rec]", json.loads(Path(path).read_text()))
        name = Path(path).stem
        for h in ("tune", "held-out"):
            print(row(f"{name} / {h}", [r for r in recs if half(str(r["clip"])) == h]))
        print(row(f"{name} / all", recs))


if __name__ == "__main__":
    main()
