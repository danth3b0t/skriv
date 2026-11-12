"""`hark eval`: a small real-speech regression set, scored per stage (docs/eval.md).

A clip is a kept dictation (`hark retry` ring) copied into the set with its key-down context and a
hand-edited ``label.json``:

- ``said``: what was actually said, verbatim (fillers included) -> recognition word error rate.
- ``critical``: strings the final text must contain exactly (identifiers, paths, flags, numbers).
- ``forbidden``: strings it must not contain (false substitutions: a dictionary word where the
  ordinary word was meant).
- ``accepted``: final texts a human has reviewed as saying the same thing as ``said``. Cleanup may
  reword, so a new wording is *unreviewed*, not a failure: `hark eval accept ID` adds it.

A run fails (exit 1) on a missing critical string, a forbidden one, or fewer negations in an
unreviewed final text than in ``said`` (after a spoken self-correction in it is resolved). Words, negations and exact strings are counted as
`hark_client.fidelity` counts them for cleanup.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, cast

from hark_client import fidelity, history, retry
from hark_client.asr import CHUNK_SAMPLES
from hark_client.audio import read_wav
from hark_client.pipeline import Utterance

if TYPE_CHECKING:
    from pathlib import Path

    from hark_client.pipeline import Result, Services

LABEL = "label.json"
LAST = "last.json"


@dataclass
class Label:
    said: str = ""
    critical: list[str] = field(default_factory=list[str])
    forbidden: list[str] = field(default_factory=list[str])
    accepted: list[str] = field(default_factory=list[str])
    note: str = ""


@dataclass
class Score:
    clip: str
    wer: float | None  # recognition (raw text) against `said`; None without `said`
    missing: list[tuple[str, str]]  # (critical string, stage that lost it)
    forbidden: list[str]
    negations_lost: bool
    accepted: bool

    @property
    def failed(self) -> bool:
        return bool(self.missing or self.forbidden or (self.negations_lost and not self.accepted))


def wer(ref: str, hyp: str) -> float:
    """Word error rate of ``hyp`` against ``ref`` (case and punctuation ignored)."""
    r, h = fidelity.tokens(ref), fidelity.tokens(hyp)
    if not r:
        return float(bool(h))
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return prev[-1] / len(r)


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def score(clip: str, label: Label, raw: str, canonical: str, final: str) -> Score:
    missing = [
        (c, "cleanup" if c in canonical else "recognition")
        for c in fidelity.missing(label.critical, final)
    ]
    return Score(
        clip=clip,
        wer=round(wer(label.said, raw), 3) if label.said else None,
        missing=missing,
        forbidden=[f for f in label.forbidden if f in final],
        negations_lost=bool(label.said) and not fidelity.negations_kept(label.said, final),
        accepted=any(_same(final, a) for a in label.accepted),
    )


def line(s: Score, status: str, final: str) -> str:
    """One clip's result: FAIL / ok (an accepted text) / new (unreviewed wording)."""
    problems = [f"missing {c!r} ({stage})" for c, stage in s.missing]
    problems += [f"forbidden {f!r}" for f in s.forbidden]
    if s.negations_lost and not s.accepted:
        problems.append("negation lost")
    verdict = "FAIL" if s.failed else "ok  " if s.accepted else "new "
    wer = f"wer {s.wer:.0%}" if s.wer is not None else "wer -"
    return f"{verdict} {s.clip[-8:]}  {status:<8} {wer:<8} {'; '.join(problems) or final!r}"


# -- the set on disk -------------------------------------------------------------------------


def load_label(clip: Path) -> Label:
    data = cast("dict[str, object]", json.loads((clip / LABEL).read_text()))

    def strs(key: str) -> list[str]:
        v = data.get(key, [])
        return [str(x) for x in cast("list[object]", v)] if isinstance(v, list) else []

    return Label(
        said=str(data.get("said", "")),
        critical=strs("critical"),
        forbidden=strs("forbidden"),
        accepted=strs("accepted"),
        note=str(data.get("note", "")),
    )


def _write(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def clips(set_dir: Path) -> list[Path]:
    if not set_dir.is_dir():
        return []
    return sorted(
        p for p in set_dir.iterdir() if (p / LABEL).is_file() and (p / "audio.wav").is_file()
    )


def add(history_dir: Path, set_dir: Path, query: str) -> Path:
    """Copy a kept dictation into the set with a label prefilled from its history record (the
    recognized text as `said`, its canonicalized terms as `critical`): edit it by hand."""
    entry = retry.find(history_dir, query)
    out = set_dir / entry.id
    if out.exists():
        raise FileExistsError(f"{out} is already in the set")
    set_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.mkdir(mode=0o700)
    for name in ("audio.wav", "context.json"):
        if (entry.path / name).is_file():
            shutil.copy2(entry.path / name, out / name)
    records = history.find(history_dir, entry.id)
    rec = records[-1] if records else {}
    replaced = cast("list[dict[str, object]]", rec.get("replacements") or [])
    _write(
        out / LABEL,
        {
            "said": str(rec.get("raw_text") or ""),
            "critical": sorted({str(r.get("term", "")) for r in replaced} - {""}),
            "forbidden": [],
            "accepted": [],
            "note": f"added {time.strftime('%Y-%m-%d')}; app {entry.meta.get('app', '?')}; "
            "edit `said` to what you said, verbatim",
        },
    )
    return out


def run_clip(svc: Services, clip: Path) -> Result:
    """The clip through the whole pipeline with its key-down context (nothing is pasted:
    ``svc`` should carry a dry-run injector). The shortlist is cut to ``context.max_terms``
    (0: recognition and cleanup without context)."""
    snap = retry.load_snapshot(retry.Entry(clip.name, clip, {}))
    snap = replace(snap, terms=snap.terms[: max(0, svc.cfg.context.max_terms)])
    utt = Utterance(svc, replay=snap)
    pcm = read_wav(clip / "audio.wav")
    step = CHUNK_SAMPLES * 2
    for i in range(0, len(pcm), step):
        utt.feed(pcm[i : i + step])
    res = utt.finish()
    _write(
        clip / LAST,
        {
            "status": res.status,
            "raw": res.raw_text,
            "canonical": res.canonical_text,
            "final": res.final_text,
            "cleanup": res.cleanup.status if res.cleanup else None,
            "provider": res.provider,
        },
    )
    return res


def report(s: Score, res: Result) -> dict[str, object]:
    """One clip for `hark eval run --report`: the score and every stage's text and timing."""
    gate = res.gate or {}
    return {
        "clip": s.clip,
        "failed": s.failed,
        "accepted": s.accepted,
        "wer": s.wer,
        "missing": [{"string": c, "stage": stage} for c, stage in s.missing],
        "forbidden": s.forbidden,
        "negations_lost": s.negations_lost,
        "status": res.status,
        "provider": res.provider,
        "raw": res.raw_text,
        "gated": gate.get("text", res.raw_text),
        "canonical": res.canonical_text,
        "uncleaned": res.uncleaned_text,  # symbols, self-corrections and guardrails, no cleanup
        "final": res.final_text,
        "gate": gate,
        "replacements": res.replacements,
        "symbols": res.symbols,
        "corrections": res.corrections,
        "cleanup": asdict(res.cleanup) if res.cleanup else None,
        "guard_events": res.guard_events,
        "timings_ms": res.timings,
    }


def accept(set_dir: Path, clip_id: str) -> str:
    """Add the clip's last final text to its accepted texts; returns it."""
    matches = [c for c in clips(set_dir) if clip_id in c.name]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} clips match {clip_id!r}")
    clip = matches[0]
    try:
        final = str(cast("dict[str, object]", json.loads((clip / LAST).read_text()))["final"])
    except (OSError, ValueError, KeyError) as e:
        raise LookupError(f"{clip.name}: no run to accept yet (`hark eval run`)") from e
    data = cast("dict[str, object]", json.loads((clip / LABEL).read_text()))
    accepted = cast("list[object]", data.setdefault("accepted", []))
    if final not in accepted:
        accepted.append(final)
    _write(clip / LABEL, data)
    return final
