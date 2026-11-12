"""Recovery ring: the last ``retry.keep`` dictations' audio, kept so `hark retry` can re-run one.

Layout: ``<history.dir>/retry/<utterance_id>/{audio.wav, context.json, meta.json}`` (directories
0700, files 0600); hark's only audio store (`hark eval add` copies from it). Older entries are
deleted after each save. ``context.json`` is the snapshot taken at key-down (without the selection,
agent reply and window title), so a retry is biased and targeted like the original: it pastes
only if that window is focused again, else copies.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from hark_client.audio import write_wav
from hark_client.context import ContextSnapshot, FocusInfo, Term, TermKind, TermSource
from hark_client.context.aliases import spoken_forms
from hark_client.history import snapshot_json

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

RING = "retry"


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    path: Path
    meta: dict[str, object]

    @property
    def audio(self) -> Path:
        return self.path / "audio.wav"


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def save(
    base: Path, keep: int, pcm16: bytes, snapshot: ContextSnapshot, meta: Mapping[str, object]
) -> Path | None:
    """Store one dictation (``meta["utterance_id"]`` names it) and purge the ring down to
    ``keep``; None when keep is 0."""
    ring = base / RING
    if keep <= 0:
        if ring.is_dir():
            purge(ring, 0)
        return None
    ring.mkdir(parents=True, exist_ok=True, mode=0o700)
    ring.chmod(0o700)
    out = ring / str(meta["utterance_id"])
    out.mkdir(mode=0o700, exist_ok=True)
    wav = out / "audio.wav"
    write_wav(wav, pcm16)  # inside a 0700 directory until the chmod
    wav.chmod(0o600)
    ctx = snapshot_json(snapshot)
    for private in ("selection", "agent_last_reply"):  # not needed to re-run; kept out
        ctx.pop(private, None)
    cast("dict[str, object]", ctx["focus"])["title"] = ""
    _write_private(out / "context.json", json.dumps(ctx, ensure_ascii=False, sort_keys=True) + "\n")
    _write_private(out / "meta.json", json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")
    purge(ring, keep)
    return out


def update_meta(entry: Path, meta: Mapping[str, object]) -> None:
    """Replace a kept dictation's meta.json (its outcome, once known)."""
    _write_private(entry / "meta.json", json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")


def drop(entry: Path) -> None:
    shutil.rmtree(entry, ignore_errors=True)


def purge(ring: Path, keep: int) -> int:
    """Delete all but the newest ``keep`` entries (UUIDv7 names sort by time); returns the count."""
    dirs = sorted(p for p in ring.iterdir() if p.is_dir() and not p.is_symlink())
    old = dirs[: max(0, len(dirs) - keep)]
    for p in old:
        shutil.rmtree(p, ignore_errors=True)
    return len(old)


def entries(base: Path) -> list[Entry]:
    """Newest first; entries without audio are skipped."""
    ring = base / RING
    if not ring.is_dir():
        return []
    out: list[Entry] = []
    for p in sorted(ring.iterdir(), reverse=True):
        if not (p / "audio.wav").is_file():
            continue
        meta: dict[str, object] = {}
        with contextlib.suppress(OSError, ValueError):
            raw = cast("object", json.loads((p / "meta.json").read_text()))
            if isinstance(raw, dict):
                meta = cast("dict[str, object]", raw)
        out.append(Entry(p.name, p, meta))
    return out


def find(base: Path, query: str) -> Entry:
    """``last`` (or "") = newest; else a full id or a unique part of one (e.g. its last 8
    characters, as `hark history` shows). Raises LookupError with a message for the user."""
    kept = entries(base)
    if not kept:
        raise LookupError(f"no kept recordings in {base / RING} (retry.keep = 0, or none yet)")
    if query in ("", "last"):
        return kept[0]
    matches = [e for e in kept if query == e.id] or [e for e in kept if query in e.id]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"no kept recording matches {query!r} (see `hark history`)")
    raise LookupError(f"{query!r} matches {len(matches)} recordings; give more characters")


def _str_tuple(v: object) -> tuple[str, ...]:
    items = cast("list[object]", v) if isinstance(v, list) else []
    return tuple(str(x) for x in items)


def _opt_str(v: object) -> str | None:
    return v if isinstance(v, str) else None


def _term(t: dict[str, object]) -> Term:
    """A stored term with today's spoken forms first (as a live dictation's ``rank`` derives
    them), then the stored ones it lacks: a replay biases the recognizer like a dictation now."""
    canonical, kind = str(t["canonical"]), TermKind(t["kind"])
    spoken = (*spoken_forms(canonical, kind), *_str_tuple(t.get("spoken")))
    return Term(
        canonical=canonical,
        spoken=tuple(dict.fromkeys(spoken)),
        weight=float(cast("float", t["weight"])),
        kind=kind,
        source=TermSource(t["source"]),
    )


def load_snapshot(entry: Entry) -> ContextSnapshot:
    """The key-down context of a kept dictation; an empty one (with an error) if unreadable."""
    try:
        data = cast("dict[str, object]", json.loads((entry.path / "context.json").read_text()))
        f = cast("dict[str, object]", data["focus"])
        pid = f.get("pid")
        focus = FocusInfo(
            app=str(f.get("app", "")),
            app_id=str(f.get("app_id", "")),
            title=str(f.get("title", "")),
            pid=pid if isinstance(pid, int) else None,
            is_terminal=f.get("is_terminal") is True,
            window_id=str(f.get("window_id", "")),
        )
        terms = tuple(_term(t) for t in cast("list[dict[str, object]]", data.get("terms", [])))
        return ContextSnapshot(
            focus=focus,
            cwd=_opt_str(data.get("cwd")),
            repo_root=_opt_str(data.get("repo_root")),
            branch=_opt_str(data.get("branch")),
            agent=_opt_str(data.get("agent")),
            terms=terms,
            errors=_str_tuple(data.get("errors")),
        )
    except (OSError, ValueError, KeyError, TypeError) as e:
        return ContextSnapshot(errors=(f"retry: stored context unreadable ({e})",))
