"""Tables for docs/bench/2026-11-13-eval-baseline.md from `hark eval run --report` files.

    uv run python docs/bench/eval-baseline/analyze.py [--set eval/dev-v1] [CONDITION ...]

CONDITION names a report docs/bench/eval-baseline/CONDITION.json (default: baseline no-context
no-cleanup no-gate). Labels (said, critical, forbidden, accepted, the note's voice/noise/context
description) are read from the set, so `accepted` reflects the current reviews.

Stages of a clip's text, in pipeline order: raw (recognized, biased) -> gated (anchor gate) ->
canonical (shortlist aliases -> spellings) -> uncleaned (spoken symbols, self-corrections,
guardrails) -> final (cleanup model, guardrails). A critical string missing from the final text
is "lost" at the stage after the last one that had it, or "never" produced.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import cast

from hark_client import evalset, fidelity

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STAGES = ("raw", "gated", "canonical", "uncleaned", "final")
FILLERS = re.compile(r"\b(um+|uh+|erm|er|eh|mm+|hmm+|ah)\b", re.IGNORECASE)
type Rec = dict[str, object]


def load(set_dir: Path, condition: str) -> list[tuple[Rec, evalset.Label]]:
    recs = cast("list[Rec]", json.loads((HERE / f"{condition}.json").read_text()))
    out: list[tuple[Rec, evalset.Label]] = []
    for r in recs:  # scored again: reviews made after the run count
        lab = evalset.load_label(set_dir / str(r["clip"]))
        s = evalset.score(str(r["clip"]), lab, str(r["raw"]), str(r["canonical"]), str(r["final"]))
        r["accepted"], r["failed"] = s.accepted, s.failed
        out.append((r, lab))
    return out


def group(note: str, clip: str) -> dict[str, str]:
    """Voice, noise and context descriptors from a label note (scripts/make-eval-set)."""
    g: dict[str, str] = {"part": "real" if clip.startswith("real") else "synthetic"}
    if m := re.match(r"synthetic \(([^)]+)\)", note):
        g["accent"] = m.group(1).split()[0] if m.group(1).startswith("en_") else "L2"
    elif "EdACC" in note:
        g["accent"] = "EdACC"
    else:
        g["accent"] = "LibriSpeech"
    noise = re.search(
        r"(quiet room|office|cafeteria|living room|meeting babble|fan|keyboard)", note
    )
    g["noise"] = "dataset" if g["part"] == "real" else (noise.group(1) if noise else "?")
    if "keyboard + office" in note:
        g["noise"] = "keyboard + office"
    ctx = re.search(r"context (full|sparse|empty)", note)
    g["context"] = ctx.group(1) if ctx else "?"
    g["app"] = "terminal" if re.search(r"app (Ghostty|Alacritty|kitty)", note) else "prose/chat"
    tags = re.search(r"tags ([^;]+)", note)
    g["tags"] = tags.group(1) if tags else ""
    return g


def capped(rows: list[tuple[Rec, evalset.Label]]) -> list[float]:
    """Per-clip recognition WER, at most 100% (one degenerate output would dominate a mean)."""
    return [min(1.0, float(cast("float", r["wer"]))) for r, _ in rows if r["wer"] is not None]


def critical_by_stage(
    rows: list[tuple[Rec, evalset.Label]],
) -> tuple[Counter[str], Counter[str], int]:
    present: Counter[str] = Counter()
    lost: Counter[str] = Counter()
    total = 0
    for r, lab in rows:
        for c in lab.critical:
            total += 1
            has = [c in str(r[s]) for s in STAGES]
            for s, h in zip(STAGES, has, strict=True):
                present[s] += h
            if not has[-1]:
                last = max((i for i, h in enumerate(has) if h), default=-1)
                lost["never produced" if last < 0 else STAGES[last + 1]] += 1
    return present, lost, total


def false_subs(rows: list[tuple[Rec, evalset.Label]]) -> tuple[Counter[str], int, list[str]]:
    """Forbidden strings in the final text by the stage that introduced them (trap and
    real-speech clips only: their forbidden strings are context terms)."""
    where: Counter[str] = Counter()
    n = 0
    examples: list[str] = []
    for r, lab in rows:
        if not (str(r["clip"]).startswith("real") or "trap" in lab.note):
            continue
        for f in lab.forbidden:
            n += 1
            if f in str(r["final"]):
                first = next(s for s in STAGES if f in str(r[s]))
                where[first] += 1
                examples.append(f"{r['clip']} {f!r} ({first})")
    return where, n, examples


def negation_losses(rows: list[tuple[Rec, evalset.Label]]) -> tuple[int, int, Counter[str]]:
    """Clips with fewer negations in the final text than said, the accepted ones and those
    with a resolved self-correction (a retracted "no"), and the stage where the others lost it."""
    lost = ok = 0
    stage: Counter[str] = Counter()
    for r, lab in rows:
        want = fidelity.negations(lab.said)
        if fidelity.negations(str(r["final"])) >= want:
            continue
        if r["accepted"] or fidelity.negations_kept(lab.said, str(r["final"])):
            ok += 1
            continue
        lost += 1
        stage[next(s for s in STAGES if fidelity.negations(str(r[s])) < want)] += 1
    return lost, ok, stage


def ms(rows: list[tuple[Rec, evalset.Label]], key: str) -> str:
    vals = sorted(
        float(cast("dict[str, float]", r["timings_ms"])[key])
        for r, _ in rows
        if key in cast("dict[str, float]", r["timings_ms"])
    )
    if not vals:
        return "-"
    p95 = vals[min(len(vals) - 1, round(0.95 * (len(vals) - 1)))]
    return f"{statistics.median(vals):.0f} / {p95:.0f}"


def summary(condition: str, rows: list[tuple[Rec, evalset.Label]]) -> list[str]:
    n = len(rows)
    failed = sum(bool(r["failed"]) for r, _ in rows)
    accepted = sum(bool(r["accepted"]) for r, _ in rows)
    wers = capped(rows)
    present, lost, total = critical_by_stage(rows)
    fs, fs_n, _ = false_subs(rows)
    neg, _neg_ok, _ = negation_losses(rows)
    fillers = sum(bool(FILLERS.search(str(r["final"]))) for r, _ in rows)
    filler_said = sum(bool(FILLERS.search(lab.said)) for _, lab in rows)
    recall = " / ".join(f"{present[s] / total:.0%}" for s in STAGES)
    return [
        (
            f"| {condition} | {failed} | {n - failed - accepted} | {accepted} | "
            f"{statistics.mean(wers):.1%} (median {statistics.median(wers):.1%}) | {recall} | "
            f"{sum(fs.values())}/{fs_n} | {neg} | {fillers}/{filler_said} | "
            f"{ms(rows, 'keyup_to_final_ms')} | {ms(rows, 'server_end_to_final_ms')} | "
            f"{ms(rows, 'cleanup_ms')} |"
        ),
        f"<!-- {condition}: critical lost at {dict(lost)} -->",
    ]


def by(rows: list[tuple[Rec, evalset.Label]], key: str) -> list[str]:
    out: list[str] = []
    groups: dict[str, list[tuple[Rec, evalset.Label]]] = {}
    for r, lab in rows:
        groups.setdefault(group(lab.note, str(r["clip"]))[key], []).append((r, lab))
    for name, rs in sorted(groups.items()):
        wers = capped(rs)
        present, _, total = critical_by_stage(rs)
        crit = f"{present['final']}/{total}" if total else "-"
        out.append(
            f"| {name} | {len(rs)} | {statistics.mean(wers):.1%} | {statistics.median(wers):.1%} | "
            f"{sum(bool(r['failed']) for r, _ in rs)} | {sum(bool(r['accepted']) for r, _ in rs)} | {crit} |"
        )
    return out


def gate_stats(rows: list[tuple[Rec, evalset.Label]]) -> list[str]:
    decisions: Counter[str] = Counter()
    better = worse = same = 0
    for r, lab in rows:
        g = cast("dict[str, object]", r["gate"])
        for d in cast("list[dict[str, str]]", g.get("decisions", [])):
            decisions[f"{d['decision']}/{d['reason']}"] += 1
        if r["gated"] != r["raw"] and lab.said:
            a, b = evalset.wer(lab.said, str(r["raw"])), evalset.wer(lab.said, str(r["gated"]))
            better += b < a
            worse += b > a
            same += b == a
    return [
        f"decisions: {dict(decisions.most_common())}",
        f"clips where the gate changed the text: WER better {better}, worse {worse}, equal {same}",
    ]


def cleanup_stats(rows: list[tuple[Rec, evalset.Label]]) -> list[str]:
    status: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    for r, _ in rows:
        c = cast("dict[str, object] | None", r["cleanup"])
        if c is None:
            continue
        status[str(c["status"])] += 1
        reason = str(c.get("reason") or "")
        if m := re.search(r"dropped: (.*)", reason):  # edit-list reports (before 2026-11-13)
            dropped.update(x.strip() for x in m.group(1).split(","))
        elif c["status"] == "rejected" and reason:  # the fidelity check's first failed rule
            dropped[reason.split(maxsplit=1)[0]] += 1
    return [
        f"status: {dict(status.most_common())}",
        f"dropped edits / rejected rewrites by reason: {dict(dropped.most_common())}",
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(ROOT / "eval" / "dev-v1"))
    ap.add_argument(
        "conditions", nargs="*", default=["baseline", "no-context", "no-cleanup", "no-gate"]
    )
    args = ap.parse_args()
    set_dir = Path(args.set)
    print(
        "| condition | failed | unreviewed | accepted | recognition WER (mean, capped) | critical recall "
        "raw / gated / canonical / uncleaned / final | false substitutions | negations lost "
        "(not accepted) | finals with fillers / said with fillers | key-up -> final ms p50 / p95 "
        "| server end -> final ms p50 / p95 | cleanup ms p50 / p95 |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    data = {c: load(set_dir, c) for c in args.conditions}
    for c, rows in data.items():
        print("\n".join(summary(c, rows)))
    for c, rows in data.items():
        print(f"\n## {c}")
        _present, lost, total = critical_by_stage(rows)
        print(f"critical strings: {total}; lost at: {dict(lost.most_common())}")
        fs, fs_n, ex = false_subs(rows)
        print(f"false substitutions: {sum(fs.values())}/{fs_n} by first stage {dict(fs)}: {ex}")
        neg, ok, stage = negation_losses(rows)
        print(f"negations lost: {neg} (by first stage {dict(stage)}), accepted corrections: {ok}")
        print("\n".join(gate_stats(rows)))
        print("\n".join(cleanup_stats(rows)))
        for key in ("part", "accent", "noise", "context", "app"):
            print(
                f"\n| {key} | clips | WER mean | WER median | failed | accepted | critical in final |"
            )
            print("|---|---|---|---|---|---|---|")
            print("\n".join(by(rows, key)))


if __name__ == "__main__":
    main()
