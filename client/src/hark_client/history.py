"""Local history: ``<history.dir>/history.jsonl`` (default ~/.local/share/hark), one line per
utterance. Audio is kept only by the retry ring (hark_client.retry)."""

from __future__ import annotations

import collections
import dataclasses
import json
import os
import threading
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from hark_client.context import ContextSnapshot

_lock = threading.Lock()


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def append(dir_: Path, record: Mapping[str, object]) -> Path:
    path = _private_dir(dir_) / "history.jsonl"
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with _lock:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line)
    return path


type Record = dict[str, object]


def _parse(line: str) -> Record | None:
    try:
        obj = cast("object", json.loads(line))
    except ValueError:
        return None  # a torn last line after a crash
    return cast("Record", obj) if isinstance(obj, dict) else None


def recent(dir_: Path, n: int) -> list[Record]:
    """The last ``n`` records, oldest first; [] without a history file."""
    try:
        with (dir_ / "history.jsonl").open(encoding="utf-8") as f:
            lines = collections.deque(f, maxlen=max(0, n))
    except FileNotFoundError:
        return []
    return [r for r in map(_parse, lines) if r is not None]


def find(dir_: Path, query: str) -> list[Record]:
    """Records whose utterance_id contains ``query`` (a full id or e.g. its last 8 characters)."""
    out: list[Record] = []
    try:
        with (dir_ / "history.jsonl").open(encoding="utf-8") as f:
            for line in f:
                if query in line:
                    r = _parse(line)
                    if r is not None and query in str(r.get("utterance_id", "")):
                        out.append(r)
    except FileNotFoundError:
        pass
    return out


def last_text(dir_: Path, scan: int = 200) -> str:
    """final_text of the newest record that has one ("" if none in the last ``scan``)."""
    for r in reversed(recent(dir_, scan)):
        text = r.get("final_text")
        if isinstance(text, str) and text:
            return text
    return ""


def snapshot_json(snapshot: ContextSnapshot) -> dict[str, object]:
    return dataclasses.asdict(snapshot)
