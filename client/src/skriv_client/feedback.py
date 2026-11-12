"""Start/stop/done/error sounds and desktop notifications; all fire-and-forget."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from skriv_client.config import FeedbackConfig

log = logging.getLogger("skriv.feedback")

type Sound = Literal["start", "stop", "done", "error", "cancel"]

_MAC_SOUNDS: dict[Sound, str] = {
    "start": "/System/Library/Sounds/Tink.aiff",
    "stop": "/System/Library/Sounds/Pop.aiff",
    "done": "/System/Library/Sounds/Bottle.aiff",
    "error": "/System/Library/Sounds/Basso.aiff",
    "cancel": "/System/Library/Sounds/Morse.aiff",
}
_FREEDESKTOP = Path("/usr/share/sounds/freedesktop/stereo")
_LINUX_SOUNDS: dict[Sound, str] = {
    "start": str(_FREEDESKTOP / "message-new-instant.oga"),
    "stop": str(_FREEDESKTOP / "message.oga"),
    "done": str(_FREEDESKTOP / "complete.oga"),
    "error": str(_FREEDESKTOP / "dialog-error.oga"),
    "cancel": str(_FREEDESKTOP / "audio-volume-change.oga"),
}


def _spawn(argv: list[str]) -> None:
    def run() -> None:
        try:
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, check=False,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as e:
            log.debug("%s failed: %s", argv[0], e)

    threading.Thread(target=run, daemon=True, name="skriv-feedback").start()


def sound_player() -> list[str] | None:
    if sys.platform == "darwin":
        return ["afplay"] if shutil.which("afplay") else None
    for player in ("pw-play", "paplay"):
        if shutil.which(player):
            return [player]
    return None


class Feedback:
    def __init__(self, cfg: FeedbackConfig) -> None:
        self.cfg = cfg
        self.player = sound_player() if cfg.sounds else None
        self.sounds = _MAC_SOUNDS if sys.platform == "darwin" else _LINUX_SOUNDS

    def sound(self, kind: Sound) -> None:
        path = self.sounds[kind]
        if self.player and Path(path).exists():
            _spawn([*self.player, path])

    def notify(self, body: str, title: str = "hark") -> None:
        log.info("%s", body)
        if not self.cfg.notify:
            return
        if sys.platform == "darwin":
            script = f"display notification {_applescript_str(body)} with title {_applescript_str(title)}"
            _spawn(["osascript", "-e", script])
        elif shutil.which("notify-send"):
            _spawn(["notify-send", "--app-name=hark", title, body])


def _applescript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
