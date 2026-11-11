"""eval-cleanup: run the cleanup eval set against an OpenAI-compatible endpoint.

Each case goes through the real hark.v1 TextCleanup path: an in-process hark
server (mock ASR backend) proxies to the endpoint, then resolves and validates
the edits. As in the client pipeline, the protected spans are the case's plus
the client's own (code-like tokens, numbers, negations), and the client
validator (authoritative) applies the returned edits or keeps the text. The
output is compared with the expected texts.

Metrics:
  exact        output is one of the expected texts
  words        same words as an expected text (case and punctuation ignored)
  clean damage cases whose input is already expected, but the output is not
               an expected text
  protected    edits rejected for touching a protected span (caught), and
               protected substrings missing from the output (leaked; must be 0)
  invalid      rejected edits / proposed edits, by rejection reason
  client       edits the client validator dropped after the server passed them
  partial      cases where some edits were applied and others dropped
  latency      client wall time per Cleanup RPC (p50/p95), and generation_ms
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

import grpc

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from hark_client.cleanup import Span, protected_spans, validate_edits
from hark_server.backends.mock import MockBackend
from hark_server.config import Config
from hark_server.server import start

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


@dataclass
class Result:
    id: str
    category: str
    profile: str
    text: str
    output: str
    exact: bool
    words: bool
    clean: bool
    error: str = ""
    client_dropped: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    truncated: bool = False
    proposed: int = 0
    rejections: dict[str, int] = field(default_factory=dict[str, int])
    leaked: list[str] = field(default_factory=list[str])
    latency_ms: float = 0.0
    generation_ms: int = 0
    edits: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    rejected: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])


class Args(argparse.Namespace):
    llm: str
    model: str
    engine: str
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


def spans(case: Case) -> list[Span]:
    """The case's protected substrings plus the client's own spans."""
    raw = case["text"].encode()
    out = protected_spans(case["text"], [])
    for p in case["protected"]:
        start = raw.index(p.encode())
        out.append(Span(start, start + len(p.encode()), pb.PROTECTED_SPAN_KIND_BIAS_TERM))
    return sorted(out, key=lambda s: (s.start, s.end))


def norm(s: str) -> list[str]:
    return _WORD.findall(s.lower())


def pct(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(q * len(ordered) + 0.5) - 1))]


async def run_case(stub: pb_grpc.TextCleanupAsyncStub, case: Case, budget_ms: int) -> Result:
    text = case["text"]
    clean = text in case["expected"]
    protected = spans(case)
    req = pb.CleanupRequest(
        utterance_id=case["id"],
        text=text,
        protected_spans=[
            pb.ProtectedSpan(byte_start=s.start, byte_end=s.end, kind=s.kind) for s in protected
        ],
        profile_id=case["profile"],
        budget_ms=budget_ms,
    )
    t0 = time.perf_counter()
    try:
        resp = await stub.Cleanup(req, timeout=budget_ms / 1000 + 1)
    except grpc.aio.AioRpcError as e:
        return Result(
            case["id"], case["category"], case["profile"], text, text,
            exact=clean, words=clean, clean=clean, error=f"{e.code().name}: {e.details()}",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )  # fmt: skip
    latency = (time.perf_counter() - t0) * 1000
    checked = validate_edits(text, resp.edits, protected)
    output = checked.text
    reasons = Counter(pb.EditRejectionReason.Name(r.reason) for r in resp.rejections)
    return Result(
        id=case["id"],
        category=case["category"],
        profile=case["profile"],
        text=text,
        output=output,
        exact=output in case["expected"],
        words=norm(output) in [norm(e) for e in case["expected"]],
        clean=clean,
        client_dropped=[(e.find, e.replace, why) for e, why in checked.dropped],
        truncated=resp.truncated,
        proposed=len(resp.edits) + len(resp.rejections),
        rejections=dict(reasons),
        leaked=[p for p in case["protected"] if p not in output],
        latency_ms=latency,
        generation_ms=resp.generation_ms,
        edits=[(e.find, e.replace, pb.EditKind.Name(e.kind)) for e in checked.accepted],
        rejected=[
            (r.find, r.replace, pb.EditRejectionReason.Name(r.reason)) for r in resp.rejections
        ],
    )


def rate(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def report(results: list[Result], cases: list[Case], target_ms: int) -> dict[str, object]:
    total = len(results)
    clean = [r for r in results if r.clean]
    proposed = sum(r.proposed for r in results)
    reasons: Counter[str] = Counter()
    for r in results:
        reasons.update(r.rejections)
    rejected = sum(reasons.values())
    ok = [r for r in results if not r.error]
    lat = [r.latency_ms for r in ok]
    gen = [float(r.generation_ms) for r in ok]
    baseline = sum(c["text"] in c["expected"] for c in cases)
    exact = sum(r.exact for r in results)
    words = sum(r.words for r in results)
    damaged = sum(not r.exact for r in clean)
    summary: dict[str, object] = {
        "cases": total,
        "exact": exact,
        "words": words,
        "baseline_exact_no_cleanup": baseline,
        "clean_cases": len(clean),
        "clean_damaged": damaged,
        "protected_caught": reasons.get("EDIT_REJECTION_REASON_PROTECTED_SPAN", 0),
        "protected_leaked": sum(len(r.leaked) for r in results),
        "proposed_edits": proposed,
        "rejected_edits": rejected,
        "rejections": dict(reasons.most_common()),
        "client_dropped": sum(len(r.client_dropped) for r in results),
        "partial": sum(
            bool(r.edits)
            and (
                bool(r.client_dropped)
                or any(x[2] != "EDIT_REJECTION_REASON_NO_OP" for x in r.rejected)
            )
            for r in results
        ),
        "errors": sum(bool(r.error) for r in results),
        "truncated": sum(r.truncated for r in results),
        "latency_ms_p50": round(pct(lat, 0.5)),
        "latency_ms_p95": round(pct(lat, 0.95)),
        "generation_ms_p50": round(pct(gen, 0.5)),
        "generation_ms_p95": round(pct(gen, 0.95)),
        f"within_{target_ms}ms": sum(x <= target_ms for x in lat),
    }
    print(f"exact match        {rate(exact, total)}"
          f"   (no cleanup: {rate(baseline, total)})")  # fmt: skip
    print(f"same words         {rate(words, total)}")
    print(f"clean damaged      {rate(damaged, len(clean))}")
    print(f"protected          {summary['protected_caught']} caught, "
          f"{summary['protected_leaked']} leaked")  # fmt: skip
    print(f"invalid edits      {rate(rejected, proposed)}  {dict(reasons.most_common())}")
    print(f"client dropped     {summary['client_dropped']}")
    print(f"partial            {summary['partial']}")
    print(f"errors/truncated   {summary['errors']}/{summary['truncated']}")
    print(f"latency ms         p50 {summary['latency_ms_p50']}  p95 {summary['latency_ms_p95']}"
          f"  (generation p50 {summary['generation_ms_p50']}  p95 {summary['generation_ms_p95']});"
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
    print(f"\n✗ {r.id} [{r.profile}] {r.error or ''}"
          f"{' client dropped: ' + str(r.client_dropped) if r.client_dropped else ''}")  # fmt: skip
    print(f"  text:     {r.text!r}")
    print(f"  output:   {r.output!r}")
    print(f"  expected: {case['expected'][0]!r}")
    for find, replace, kind in r.edits:
        print(f"  + {kind.removeprefix('EDIT_KIND_').lower()}: {find!r} -> {replace!r}")
    for find, replace, reason in r.rejected:
        print(
            f"  - {reason.removeprefix('EDIT_REJECTION_REASON_').lower()}: {find!r} -> {replace!r}"
        )


async def main(a: Args) -> int:
    cases = load_cases(Path(a.data))
    if a.only:
        cases = [c for c in cases if a.only in (c["id"], c["category"], c["profile"])]
    cases.sort(key=lambda c: c["profile"])  # profile switches invalidate the prompt cache
    cfg = Config()
    cfg.server.listen = "127.0.0.1:0"
    cfg.cleanup.base_url = a.llm
    cfg.cleanup.model = a.model
    cfg.cleanup.engine = a.engine
    cfg.cleanup.default_budget_ms = a.budget_ms
    running = await start(cfg, MockBackend())
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{running.port}")
    stub = pb_grpc.TextCleanupStub(channel)
    try:
        results: list[Result] = []
        profile = ""
        for case in cases:
            if case["profile"] != profile:  # untimed: load this profile's prompt prefix
                profile = case["profile"]
                await run_case(stub, case, a.budget_ms)
            results.append(await run_case(stub, case, a.budget_ms))
    finally:
        await channel.close()
        await running.stop(0)
    print(f"model {a.model} ({a.engine}) @ {a.llm}, {len(cases)} cases, budget {a.budget_ms} ms\n")
    summary = report(results, cases, a.target_ms)
    if a.verbose:
        for r, case in zip(results, cases, strict=True):
            if not r.exact:
                show_failure(r, case)
    if a.json:
        out = {
            "model": a.model,
            "engine": a.engine,
            "summary": summary,
            "results": [asdict(r) for r in results],
        }
        data = json.dumps(out, indent=1, ensure_ascii=False)
        await asyncio.to_thread(Path(a.json).write_text, data)
    return 0


def parse_args(argv: list[str] | None = None) -> Args:
    p = argparse.ArgumentParser(prog="eval-cleanup", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    p.add_argument("--llm", default="http://127.0.0.1:8080/v1", help="OpenAI-compatible base URL")
    p.add_argument("--model", default="cleanup", help="model name sent upstream and reported")
    p.add_argument("--engine", choices=["s1", "edits"], default="s1", help="server cleanup engine")
    p.add_argument("--data", default=str(DATA), help="eval JSONL")
    p.add_argument("--budget-ms", type=int, default=3000, help="per-request generation budget")
    p.add_argument("--target-ms", type=int, default=400, help="report the share within this")
    p.add_argument("--only", help="run one case id, category or profile")
    p.add_argument("--json", help="write the summary and per-case results here")
    p.add_argument("-v", "--verbose", action="store_true", help="print every non-exact case")
    return p.parse_args(argv, namespace=Args())


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
