"""`hark history`: a table of recent dictations and one dictation's stages, from history.jsonl."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Collection

    from hark_client.history import Record

PREVIEW = 48
APP_W = 12


def short_id(utterance_id: str) -> str:
    """Last 8 characters: the random part of a UUIDv7 (the start is a timestamp)."""
    return utterance_id[-8:]


def _str(r: Record, key: str) -> str:
    v = r.get(key)
    return v if isinstance(v, str) else ""


def _dict(v: object) -> dict[str, object]:
    return cast("dict[str, object]", v) if isinstance(v, dict) else {}


def _list(v: object) -> list[object]:
    return cast("list[object]", v) if isinstance(v, list) else []


def _local(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).astimezone()
    except ValueError:
        return None


def _when(ts: str, today: datetime) -> str:
    t = _local(ts)
    if t is None:
        return "?"
    return t.strftime("%H:%M:%S") if t.date() == today.date() else t.strftime("%m-%d %H:%M")


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())  # newlines/tabs would break the table
    return text if len(text) <= width else text[: width - 1] + "…"


def latency_ms(r: Record) -> float | None:
    """Key release -> paste, else key-up -> paste (older records), else key-up -> final result
    (failed pastes, errors after the ASR)."""
    t = _dict(r.get("timings_ms"))
    for key in ("release_to_paste_ms", "keyup_to_paste_ms", "keyup_to_final_ms"):
        v = t.get(key)
        if isinstance(v, int | float):
            return float(v)
    return None


def table(records: list[Record], kept: Collection[str] = (), now: datetime | None = None) -> str:
    """One line per record, oldest first. ``kept``: utterance ids with audio for `hark retry`
    (marked ``*``)."""
    today = now or datetime.now().astimezone()
    lines = [f"{'TIME':<11} {'ID':<9} {'APP':<{APP_W}} {'STATUS':<13} {'LATENCY':>8}  TEXT"]
    for r in records:
        uid = _str(r, "utterance_id")
        lat = latency_ms(r)
        text = _str(r, "final_text") or _str(r, "raw_text")
        if not text and _str(r, "error"):
            text = f"[{_str(r, 'error')}]"
        mark = "*" if uid in kept else " "
        lines.append(
            f"{_when(_str(r, 'ts'), today):<11} {short_id(uid)}{mark} "
            f"{_clip(_str(r, 'app') or _str(r, 'app_id') or '-', APP_W):<{APP_W}} "
            f"{_str(r, 'status'):<13} {'-' if lat is None else f'{lat:.0f} ms':>8}  "
            f"{_clip(text, PREVIEW)}"
        )
    if kept and any(_str(r, "utterance_id") in kept for r in records):
        lines.append("* = audio kept: `hark retry ID` re-runs it")
    return "\n".join(lines)


def _pairs(d: dict[str, object], skip: Collection[str] = ()) -> str:
    return ", ".join(f"{k}={v}" for k, v in d.items() if k not in skip and v not in ("", None))


def show(r: Record, *, kept: bool = False) -> str:
    """Every stage of one dictation."""
    uid = _str(r, "utterance_id")
    out: list[str] = []

    def line(label: str, text: str) -> None:
        out.append(f"{label:<10} {text}")

    t = _local(_str(r, "ts"))
    line("id", uid + (f"  (audio kept: hark retry {short_id(uid)})" if kept else ""))
    line("time", t.strftime("%Y-%m-%d %H:%M:%S") if t else _str(r, "ts"))
    app = _str(r, "app") or "?"
    line("app", f"{app} ({_str(r, 'app_id')})  profile={_str(r, 'profile') or '-'}")
    line("status", _str(r, "status") + (f"  error: {_str(r, 'error')}" if _str(r, "error") else ""))
    provider = _dict(r.get("provider"))
    if provider:
        route = " -> ".join(
            f"{h.get('name')}:{h.get('state')}" for h in map(_dict, _list(r.get("route")))
        )
        private = "" if provider.get("send_context", True) else ", audio only"
        line(
            "provider",
            f"{provider.get('name')} ({provider.get('address')}{private})  route {route}",
        )
    line("raw", repr(_str(r, "raw_text")))
    line("canonical", repr(_str(r, "canonical_text")))
    out.extend(
        f"{'':<10}   {rep.get('before')!r} -> {rep.get('after')!r} ({rep.get('reason', '')})"
        for rep in map(_dict, _list(r.get("replacements")))
    )
    symbols = [str(s) for s in _list(r.get("symbols"))]
    if symbols:
        line("symbols", ", ".join(symbols))
    cleanup = _dict(r.get("cleanup"))
    if cleanup:
        reason = f" [{cleanup['reason']}]" if cleanup.get("reason") else ""
        ms = cleanup.get("ms")
        timing = f" ({ms:.0f} ms)" if isinstance(ms, int | float) else ""
        line("cleanup", f"{cleanup.get('status')}{reason}{timing}")
        out.extend(
            f"{'':<10}   edit {e.get('find')!r} -> {e.get('replace')!r}"
            for e in map(_dict, _list(cleanup.get("edits")))
        )
        out.extend(
            f"{'':<10}   dropped {e.get('find')!r} -> {e.get('replace')!r} "
            f"({e.get('reason', '')}, by {e.get('by', '?')})"
            for e in map(_dict, _list(cleanup.get("dropped")))
        )
    for key in ("gate", "gates"):  # pipeline gate decisions, when that stage records them
        if key in r:
            line("gate", json.dumps(r[key], ensure_ascii=False))
    line("final", repr(_str(r, "final_text")))
    guard = [str(g) for g in _list(r.get("guard_events"))]
    if guard:
        line("guard", ", ".join(guard))
    spacing = _dict(r.get("spacing"))
    if spacing:
        line("spacing", _pairs(spacing))
    paste = _dict(r.get("paste"))
    if paste:
        line("paste", _pairs(paste))
    timings = _dict(r.get("timings_ms"))
    release = timings.get("release_to_paste_ms")
    if isinstance(release, int | float):
        line("latency", f"release->paste {release:.0f} ms")
    if timings:
        line("timings", ", ".join(f"{k.removesuffix('_ms')}={v}" for k, v in timings.items()))
    return "\n".join(out)
