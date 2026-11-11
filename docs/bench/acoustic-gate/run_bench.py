"""Acoustic-gate bench: adversarial TTS clips through a hark server, scored with the gate off/on.

Usage (repo root; server already running on a spare port):
  uv run python docs/bench/acoustic-gate/run_bench.py make-clips OUT_DIR
  uv run python docs/bench/acoustic-gate/run_bench.py collect OUT_DIR --server HOST:PORT [--tag TAG]
  uv run python docs/bench/acoustic-gate/run_bench.py score OUT_DIR [--tag TAG] [--max-distance 0.5]

`collect` sends every clip with its own bias list (hark-probe, --words, anchor IF_CHEAP)
and stores the FinalResult as OUT_DIR/TAG/<clip>-<voice>.json. `score` replays the
client pipeline offline on those results: gate (on/off) -> canonicalize with the same
list, which is what `pipeline.Utterance` does after FinalResult.

Systems: anchor (unbiased decode, canonicalized with the list: post-hoc only), biased
(gate off) and gated (gate on). The "canon w/o conf" variants canonicalize without word
confidences, which separates the gate's text decisions from its confidence hand-off
(canonicalization skips words below 0.5; boosted beam words score ~0.1).
Metrics:
- WER over lower-cased words with edge punctuation stripped, against `ref`.
- term recall: target-clip terms whose canonical occurs in the output (case-sensitive,
  counted per occurrence in `ref`).
- false substitutions: list-term occurrences (canonical or alias, case-insensitive) in
  the output beyond those in `ref`, per 1k reference words, over all clips.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

from google.protobuf import json_format

from hark.v1 import hark_pb2 as pb
from hark_client import gate
from hark_client.config import GateConfig
from hark_client.context import Term, TermKind, TermSource, WordInfo, canonicalize

HERE = Path(__file__).resolve().parent
PROBE = HERE.parents[2] / "tools" / "hark-probe" / "hark-probe"
type Json = dict[str, object]


def manifest() -> tuple[list[str], dict[str, Json], list[Json]]:
    m = cast("Json", json.loads((HERE / "clips.json").read_text()))
    return (
        cast("list[str]", m["voices"]),
        cast("dict[str, Json]", m["catalog"]),
        cast("list[Json]", m["clips"]),
    )


def make_clips(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    voices, _, clips = manifest()
    for v in voices:
        for c in clips:
            name = out / f"{c['id']}-{v}"
            aiff, wav = f"{name}.aiff", f"{name}.wav"
            for cmd in (
                ["say", "-v", v, "-o", aiff, str(c["spoken"])],
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav],
            ):
                subprocess.run(cmd, check=True)  # noqa: S603 - fixed macOS tools
            Path(aiff).unlink()


def bias_json(catalog: dict[str, Json], ids: list[str]) -> Json:
    return {"terms": [{"id": i, **catalog[i]} for i in ids]}


def collect(out: Path, server: str, tag: str) -> None:
    voices, catalog, clips = manifest()
    dest = out / tag
    dest.mkdir(parents=True, exist_ok=True)
    for c in clips:
        bias = dest / f"bias-{c['id']}.json"
        bias.write_text(json.dumps(bias_json(catalog, cast("list[str]", c["terms"]))))
        for v in voices:
            cmd = [str(PROBE), str(out / f"{c['id']}-{v}.wav"), "--server", server, "--words"]
            cmd += ["--quiet", "--fast", "--bias", str(bias)]
            res = subprocess.run(cmd, check=True, capture_output=True, text=True)  # noqa: S603
            (dest / f"{c['id']}-{v}.json").write_text(res.stdout)
            print(f"{c['id']}-{v}", file=sys.stderr)


def words(s: str) -> list[str]:
    return [w for w in (x.strip(".,!?;:\"'").lower() for x in s.split()) if w]


def edits(ref: str, hyp: str) -> int:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)]


def count_form(text: str, form: str) -> int:
    pat = r"(?<![\w.])" + re.escape(form) + r"(?![\w])"
    return len(re.findall(pat, text, flags=re.IGNORECASE))


def term_occurrences(text: str, t: Json) -> int:
    """Occurrences of the term written as its canonical or as one of its aliases, ignoring
    case (a casing-only difference such as "Nemo" / "NeMo" is not a substitution)."""
    forms = {cast("str", t["canonical"]).lower()} | {
        a.lower() for a in cast("list[str]", t["aliases"])
    }
    return sum(count_form(text, f) for f in forms)


def client_terms(ts: list[Json]) -> list[Term]:
    return [
        Term(
            canonical=cast("str", t["canonical"]),
            spoken=tuple(a.lower() for a in cast("list[str]", t["aliases"])),
            weight=cast("float", t["weight"]),
            kind=TermKind.NAME,
            source=TermSource.VOCABULARY,
        )
        for t in ts
    ]


def word_infos(h: pb.Hypothesis) -> list[WordInfo]:
    return [
        WordInfo(w.text, w.start_ms, w.end_ms, w.confidence if w.HasField("confidence") else None)
        for w in h.words
    ]


def score(out: Path, tag: str, cfg: GateConfig, *, verbose: bool) -> None:  # noqa: PLR0912
    voices, catalog, clips = manifest()
    systems = ("anchor", "biased", "gated", "biased, canon w/o conf", "gated, canon w/o conf")
    agg = {
        (s, k): dict.fromkeys(("e", "n", "rec", "any", "terms", "false"), 0)
        for s in systems
        for k in ("target", "distractor", "all")
    }
    decisions: dict[str, int] = {}
    for c in clips:
        ts = [{"id": i, **catalog[i]} for i in cast("list[str]", c["terms"])]
        pbterms = [
            pb.BiasTerm(
                id=cast("str", t["id"]),
                canonical=cast("str", t["canonical"]),
                aliases=[pb.SpokenAlias(text=x) for x in cast("list[str]", t["aliases"])],
            )
            for t in ts
        ]
        cterms = client_terms(ts)
        ref, kind = cast("str", c["ref"]), cast("str", c["kind"])
        for v in voices:
            final = json_format.Parse(
                (out / tag / f"{c['id']}-{v}.json").read_text(), pb.FinalResult()
            )
            biased = final.n_best[0]
            g = gate.apply(biased, final.anchor if final.HasField("anchor") else None,
                           final.bias_hits, pbterms, cfg)  # fmt: skip
            for d in g.decisions:
                key = f"{d.decision}:{d.reason}"
                decisions[key] = decisions.get(key, 0) + 1
            texts = {
                "anchor": canonicalize(final.anchor.text, word_infos(final.anchor), cterms)[0],
                "biased": canonicalize(biased.text, word_infos(biased), cterms)[0],
                "gated": canonicalize(g.text, g.words, cterms)[0],
                "biased, canon w/o conf": canonicalize(biased.text, [], cterms)[0],
                "gated, canon w/o conf": canonicalize(g.text, [], cterms)[0],
            }
            n = len(words(ref))
            for s, text in texts.items():
                for k in (kind, "all"):
                    a = agg[s, k]
                    a["e"] += edits(ref, text)
                    a["n"] += n
                    for t in ts:
                        in_ref = term_occurrences(ref, t)
                        got = term_occurrences(text, t)
                        if kind == "target":
                            a["terms"] += in_ref
                            canon_got = len(re.findall(
                                r"(?<![\w.])" + re.escape(cast("str", t["canonical"])) + r"(?![\w])", text))  # fmt: skip
                            a["rec"] += min(in_ref, canon_got)
                            a["any"] += min(in_ref, got)
                        a["false"] += max(0, got - in_ref)
            if verbose:
                print(f"{c['id']}-{v}", file=sys.stderr)
                for s, text in texts.items():
                    print(f"   {s:7s} {text}", file=sys.stderr)
                for d in g.decisions:
                    print(f"   gate    {d}", file=sys.stderr)
    print(
        "| system | clips | WER | term recall: canonical | term recall: canonical or alias |"
        " false substitutions (count, per 1k words) |"
    )
    print("|---|---|---|---|---|---|")
    for k in ("target", "distractor", "all"):
        for s in systems:
            a = agg[s, k]
            rec = f"{a['rec']}/{a['terms']}" if k != "distractor" else "—"
            rec_any = f"{a['any']}/{a['terms']}" if k != "distractor" else "—"
            per_k = 1000 * a["false"] / a["n"] if a["n"] else 0.0
            print(
                f"| {s} | {k} | {a['e'] / a['n']:.3f} | {rec} | {rec_any} | {a['false']} ({per_k:.1f}) |"
            )
    print()
    print("| gate decision | regions |")
    print("|---|---|")
    for key, n in sorted(decisions.items(), key=lambda kv: -kv[1]):
        print(f"| {key} | {n} |")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["make-clips", "collect", "score"])
    p.add_argument("out", type=Path)
    p.add_argument("--server", default="127.0.0.1:50081")
    p.add_argument("--tag", default="default")
    p.add_argument("--max-distance", type=float, default=GateConfig().max_distance)
    p.add_argument("--confident-anchor", type=float, default=GateConfig().confident_anchor)
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    out, what, tag = cast("Path", a.out), cast("str", a.what), cast("str", a.tag)
    if what == "make-clips":
        make_clips(out)
    elif what == "collect":
        collect(out, cast("str", a.server), tag)
    else:
        cfg = GateConfig(
            max_distance=cast("float", a.max_distance),
            confident_anchor=cast("float", a.confident_anchor),
        )
        score(out, tag, cfg, verbose=cast("bool", a.verbose))


if __name__ == "__main__":
    main()
