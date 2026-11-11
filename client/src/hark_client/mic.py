"""Typed wrapper around sounddevice (PortAudio) input streams.

sounddevice ships no type information, so the unknown-type rules are relaxed for this
module only; everything it exports has precise signatures.
"""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAny=false

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import numpy as np
import sounddevice as sd

from hark_client.audio import Resampler

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.audio import Float32

log = logging.getLogger("hark.mic")


def _device(spec: str) -> int | str | None:
    if not spec:
        return None
    return int(spec) if spec.isdigit() else spec


def describe_input(spec: str = "") -> tuple[str, int]:
    """(device name, default sample rate) of the input device; raises on failure."""
    info = cast("dict[str, object]", sd.query_devices(_device(spec), kind="input"))
    return str(info["name"]), int(cast("float", info["default_samplerate"]))


class Mic:
    """Input stream at the device's native rate; delivers 16 kHz mono PCM16 to `on_pcm`
    from the PortAudio callback thread (keep `on_pcm` short and non-blocking)."""

    def __init__(self, on_pcm: Callable[[bytes], None], device: str = "") -> None:
        self.on_pcm = on_pcm
        self.device_name, rate = describe_input(device)
        self.rate = rate
        self._resampler = Resampler(rate)
        self.status_errors = 0

        def callback(indata: Float32, frames: int, time: object, status: object) -> None:
            if status:
                self.status_errors += 1
            self.on_pcm(self._resampler.process(indata.copy()))

        self._stream = sd.InputStream(
            device=_device(device),
            samplerate=rate,
            channels=1,
            dtype="float32",
            blocksize=rate // 50,  # 20 ms
            latency="low",
            callback=callback,
        )

    @property
    def active(self) -> bool:
        return bool(self._stream.active)

    def start(self) -> None:
        if not self.active:
            self._stream.start()

    def stop(self) -> None:
        """Blocks until the last callback has run; do not call while holding a lock the
        callback needs."""
        if self.active:
            self._stream.stop()

    def close(self) -> None:
        self._stream.close()


def record(seconds: float, device: str = "") -> bytes:
    """Blocking capture for `hark doctor`; returns 16 kHz mono PCM16."""
    _, rate = describe_input(device)
    frames = sd.rec(
        int(seconds * rate), samplerate=rate, channels=1, dtype="float32", device=_device(device)
    )
    sd.wait()
    return Resampler(rate).process(np.asarray(frames, dtype=np.float32), last=True)
