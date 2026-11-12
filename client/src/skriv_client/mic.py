"""Typed wrapper around sounddevice (PortAudio) input streams.

sounddevice ships no type information, so the unknown-type rules are relaxed for this
module only; everything it exports has precise signatures.
"""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAny=false, reportPrivateUsage=false

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, cast

import numpy as np
import sounddevice as sd

from skriv_client.audio import Resampler

if TYPE_CHECKING:
    from collections.abc import Callable

    from skriv_client.audio import Float32

log = logging.getLogger("skriv.mic")


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
        self._spec = device
        self.device_name, self.rate = describe_input(device)
        self._resampler = Resampler(self.rate)
        self.status_errors = 0
        self.last_callback = time.monotonic()  # a running stream calls back every 20 ms
        self._stream = self._open()

    def _open(self) -> sd.InputStream:
        return sd.InputStream(
            device=_device(self._spec),
            samplerate=self.rate,
            channels=1,
            dtype="float32",
            blocksize=self.rate // 50,  # 20 ms
            latency="low",
            callback=self._callback,
        )

    def _callback(self, indata: Float32, frames: int, time_info: object, status: object) -> None:
        self.last_callback = time.monotonic()
        if status:
            self.status_errors += 1
        self.on_pcm(self._resampler.process(indata.copy()))

    def healthy(self, max_silence_s: float) -> bool:
        """Running and delivering audio (callbacks, whatever their level) recently."""
        return self.active and time.monotonic() - self.last_callback < max_silence_s

    def reopen(self) -> None:
        """Close the stream and open a fresh one on the configured device (or the current
        default: PortAudio's device list is re-read), started. Raises if that fails."""
        with contextlib.suppress(Exception):
            self._stream.abort()
        with contextlib.suppress(Exception):
            self._stream.close()
        sd._terminate()  # re-read the device list: a docked headset, an unplugged USB mic
        sd._initialize()
        self.device_name, self.rate = describe_input(self._spec)
        self._resampler = Resampler(self.rate)
        self.last_callback = time.monotonic()  # grace until the first callback
        self._stream = self._open()
        self._stream.start()

    @property
    def active(self) -> bool:
        try:
            return bool(self._stream.active)
        except sd.PortAudioError:  # closed after a failed reopen
            return False

    def start(self) -> None:
        if not self.active:
            self.last_callback = time.monotonic()
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
