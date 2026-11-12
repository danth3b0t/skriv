"""eval-cleanup: run the cleanup eval set against an OpenAI-compatible endpoint.

Each case goes through the real hark.v1 TextCleanup path: an in-process hark server (mock ASR
backend) asks the endpoint for a rewrite, and the client's `cleanup.run` checks it
(`skriv_client.fidelity`) with the case's protected strings and terms as its shortlist. The
output (the rewrite if it passed, else the input) is compared with the expected texts.

Metrics:
  exact        output is one of the expected texts
  words        same words as an expected text (case and punctuation ignored)
  clean damage cases whose input is already expected, but the output is not
  leaked       protected substrings missing from the output (must be 0)
  status       applied / partly (some sentences kept as said) / no_edits / rejected (by the
               check's reason) / errors
  latency      client wall time per cleanup (p50/p95), and generation_ms
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypedDict, cast

import grpc

from skriv_client import cleanup
from skriv_client.context import Term, TermKind, TermSource
from skriv_server.backends.mock import MockBackend
from skriv_server.config import Config
from skriv_server.server import start

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "server/tests/data/cleanup_eval.jsonl"
_WORD = re.compile(r"[\w']+")


class Case(TypedDict):
    id: str
    category: str
    profile: str
    text: str
    protected: list[str]
    expected: list[str]
    terms: NotRequired[list[str]]  # vocabulary canonicals, protected like the client's shortlist


@dataclass
class Result:
    id: str
    category: str
    profile: str
    text: str
    output: str
    rewrite: str
    status: str
    reason: str
    exact: bool
    words: bool
    clean: bool
    leaked: list[str]
    latency_ms: float


class Args(argparse.Namespace):
    llm: str
    model: str
    data: str
    budget_ms: int
    target_ms: int
    only: str | None
    json: str | None
    verbose: bool


def load_cases(path: Path) -> list[Case]:
    cases: list[Case] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        case = cast("Case", json.loads(line))
        for p in case["protected"]:
            if case["text"].count(p) != 1:
                raise SystemExit(f"{path}:{n}: protected {p!r} must occur exactly once")
        cases.append(case)
    return cases


def terms(case: Case) -> list[Term]:
    """The case's protected strings and terms, as the client's shortlist."""
    return [
        Term(t, (), 1.0, TermKind.IDENTIFIER, TermSource.VOCABULARY)
        for t in (*case["protected"], *case.get("terms", []))
    ]


def norm(s: str) -> list[str]:
    return _WORD.findall(s.lower())


def pct(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(q * len(ordered) + 0.5) - 1))]


def run_case(channel: grpc.Channel, case: Case, budget_ms: int) -> Result:
    text = case["text"]
    out = cleanup.run(
        channel,
        text=text,
        terms=terms(case),
        utterance_id=case["id"],
        profile_id=case["profile"],
        app_category=case["profile"],
        language="",
        deadline_ms=budget_ms,
    )
    return Result(
        id=case["id"],
        category=case["category"],
        profile=case["profile"],
        text=text,
        output=out.text,
        rewrite=out.rewrite,
        status=out.status,
        reason=out.reason,
        exact=out.text in case["expected"],
        words=norm(out.text) in [norm(e) for e in case["expected"]],
        clean=text in case["expected"],
        leaked=[p for p in case["protected"] if p not in out.text],
        latency_ms=out.ms,
    )


def rate(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def report(results: list[Result], cases: list[Case], target_ms: int) -> dict[str, object]:
    total = len(results)
    clean = [r for r in results if r.clean]
    lat = [r.latency_ms for r in results if r.status not in ("error", "timeout")]
    baseline = sum(c["text"] in c["expected"] for c in cases)
    exact = sum(r.exact for r in results)
    words = sum(r.words for r in results)
    damaged = sum(not r.exact for r in clean)
    # applied with a reason: some sentences stayed as said
    status = Counter("partly" if r.status == "applied" and r.reason else r.status for r in results)
    reasons = Counter(r.reason.split(maxsplit=1)[0] for r in results if r.status == "rejected")
    summary: dict[str, object] = {
        "cases": total,
        "exact": exact,
        "words": words,
        "baseline_exact_no_cleanup": baseline,
        "clean_cases": len(clean),
        "clean_damaged": damaged,
        "protected_leaked": sum(len(r.leaked) for r in results),
        "status": dict(status.most_common()),
        "rejected_by_reason": dict(reasons.most_common()),
        "latency_ms_p50": round(pct(lat, 0.5)),
        "latency_ms_p95": round(pct(lat, 0.95)),
        f"within_{target_ms}ms": sum(x <= target_ms for x in lat),
    }
    print(f"exact match        {rate(exact, total)}"
          f"   (no cleanup: {rate(baseline, total)})")  # fmt: skip
    print(f"same words         {rate(words, total)}")
    print(f"clean damaged      {rate(damaged, len(clean))}")
    print(f"protected leaked   {summary['protected_leaked']}")
    print(f"status             {dict(status.most_common())}")
    print(f"rejected by        {dict(reasons.most_common())}")
    print(f"latency ms         p50 {summary['latency_ms_p50']}  p95 {summary['latency_ms_p95']};"
          f"  <= {target_ms} ms: {rate(sum(x <= target_ms for x in lat), len(lat))}")  # fmt: skip
    by: dict[str, dict[str, int]] = {}
    for r in results:
        for key in (f"category:{r.category}", f"profile:{r.profile}"):
            row = by.setdefault(key, {"n": 0, "exact": 0, "words": 0})
            row["n"] += 1
            row["exact"] += r.exact
            row["words"] += r.words
    print("\n  group                     exact   words")
    for key, row in sorted(by.items()):
        print(f"  {key:24} {row['exact']:>3}/{row['n']:<3} {row['words']:>3}/{row['n']:<3}")
    summary["groups"] = by
    return summary


def show_failure(r: Result, case: Case) -> None:
    print(f"\n✗ {r.id} [{r.profile}] {r.status}{f' ({r.reason})' if r.reason else ''}")
    print(f"  text:     {r.text!r}")
    if r.rewrite and r.rewrite != r.output:
        print(f"  rewrite:  {r.rewrite!r}")
    print(f"  output:   {r.output!r}")
    print(f"  expected: {case['expected'][0]!r}")


async def main(a: Args) -> int:
    cases = load_cases(Path(a.data))
    if a.only:
        cases = [c for c in cases if a.only in (c["id"], c["category"], c["profile"])]
    cfg = Config()
    cfg.server.listen = "127.0.0.1:0"
    cfg.cleanup.base_url = a.llm
    cfg.cleanup.model = a.model
    cfg.cleanup.default_budget_ms = a.budget_ms
    running = await start(cfg, MockBackend())
    channel = grpc.insecure_channel(f"127.0.0.1:{running.port}")
    try:
        results = [await asyncio.to_thread(run_case, channel, c, a.budget_ms) for c in cases]
    finally:
        channel.close()
        await running.stop(0)
    print(f"model {a.model} @ {a.llm}, {len(cases)} cases, budget {a.budget_ms} ms\n")
    summary = report(results, cases, a.target_ms)
    if a.verbose:
        for r, case in zip(results, cases, strict=True):
            if not r.exact:
                show_failure(r, case)
    if a.json:
        out = {"model": a.model, "summary": summary, "results": [asdict(r) for r in results]}
        data = json.dumps(out, indent=1, ensure_ascii=False)
        await asyncio.to_thread(Path(a.json).write_text, data)
    return 0


def parse_args(argv: list[str] | None = None) -> Args:
    p = argparse.ArgumentParser(prog="eval-cleanup", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    p.add_argument("--llm", default="http://127.0.0.1:8080/v1", help="OpenAI-compatible base URL")
    p.add_argument("--model", default="cleanup", help="model name sent upstream and reported")
    p.add_argument("--data", default=str(DATA), help="eval JSONL")
    p.add_argument("--budget-ms", type=int, default=3000, help="per-request deadline")
    p.add_argument("--target-ms", type=int, default=400, help="report the share within this")
    p.add_argument("--only", help="run one case id, category or profile")
    p.add_argument("--json", help="write the summary and per-case results here")
    p.add_argument("-v", "--verbose", action="store_true", help="print every non-exact case")
    return p.parse_args(argv, namespace=Args())


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
