"""Replay the client's fidelity net on the rewrites of a `skriv eval run --report` file.

    uv run python docs/bench/w4-cleanup-llm/replay.py REPORT.json OUT.json

For each clip whose cleanup returned a rewrite (`cleanup.rewrite`), `skriv_client.cleanup.judge`
decides again what to keep, with the shortlist the pipeline held (the clip's context cut to
`context.max_terms`, plus the base commands in a terminal) and the clip's profile. The input is
the report's `uncleaned` text (the cleanup input after guardrails, which only touch whitespace
and control characters). OUT.json is the report with `final` and `cleanup` replaced, for
split.py.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

from skriv_client import cleanup, evalset, guardrails, retry
from skriv_client.config import Config
from skriv_client.context.words import with_terminal_commands

ROOT = Path(__file__).resolve().parents[3]
SET = ROOT / "eval" / "dev-v1"
type Rec = dict[str, object]


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    cfg = Config()
    recs = cast("list[Rec]", json.loads(src.read_text()))
    for r in recs:
        c = cast("dict[str, object] | None", r["cleanup"])
        if not c or not c.get("rewrite"):
            continue
        clip = SET / str(r["clip"])
        snap = retry.load_snapshot(retry.Entry(clip.name, clip, {}))
        snap = replace(snap, terms=snap.terms[: cfg.context.max_terms])
        prof = cfg.profile(cfg.profile_for(snap.focus))
        if prof.terminal:
            snap = replace(snap, terms=with_terminal_commands(snap.terms, cfg.context.max_terms))
        text, rewrite = str(r["uncleaned"]), str(c["rewrite"])
        result, why = cleanup.judge(
            text, rewrite, snap.terms,
            single_line=prof.cleanup_profile in cleanup.SINGLE_LINE_PROFILES,
        )  # fmt: skip
        status = "rejected" if why and result == text else "applied"
        r["cleanup"] = {**c, "status": status, "reason": why}
        r["final"], _ = guardrails.apply(result, terminal=prof.terminal)
        lab = evalset.load_label(clip)
        s = evalset.score(clip.name, lab, str(r["raw"]), str(r["canonical"]), str(r["final"]))
        r["failed"], r["accepted"] = s.failed, s.accepted
    out.write_text(json.dumps(recs, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
