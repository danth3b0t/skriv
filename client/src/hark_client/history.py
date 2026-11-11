"""Local history (JSONL, one line per utterance) and opt-in recordings for dataset seeding.

Layout under `history.dir` (default ~/.local/share/hark):
    history.jsonl
    recordings/YYYY/MM/<utterance_id>/{audio.wav, context.json, result.json}
`audio.wav` is exactly the 16 kHz mono PCM16 stream sent to the server (pre-roll included).
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from typing import TYPE_CHECKING

from hark_client.audio import write_wav

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


def snapshot_json(snapshot: ContextSnapshot) -> dict[str, object]:
    return dataclasses.asdict(snapshot)


def save_recording(
    dir_: Path,
    utterance_id: str,
    pcm16: bytes,
    snapshot: ContextSnapshot,
    result: Mapping[str, object],
) -> Path:
    month = time.strftime("%Y/%m", time.gmtime())
    out = _private_dir(dir_ / "recordings" / month / utterance_id)
    write_wav(out / "audio.wav", pcm16)
    (out / "context.json").write_text(
        json.dumps(snapshot_json(snapshot), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return out
