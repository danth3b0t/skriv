"""Audio plumbing: device rate -> 16 kHz mono PCM s16le, pre-roll ring buffer, WAV I/O."""

from __future__ import annotations

import math
import threading
import wave
from collections import deque
from typing import TYPE_CHECKING

import numpy as np
import soxr  # pyright: ignore[reportMissingTypeStubs] - no stubs; typed at the call sites

if TYPE_CHECKING:
    from pathlib import Path

    import numpy.typing as npt

SAMPLE_RATE = 16000

type Float32 = npt.NDArray[np.float32]


def to_pcm16(x: Float32) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes()


def downmix(frames: Float32) -> Float32:
    """(n, channels) or (n,) -> (n,) float32."""
    if frames.ndim == 1:
        return frames
    return frames.mean(axis=1, dtype=np.float32)


class Resampler:
    """Streaming soxr resampler, device rate -> 16 kHz mono PCM16. One per utterance or
    one per open mic stream; not thread-safe."""

    def __init__(self, in_rate: int) -> None:
        self.in_rate = in_rate
        self._stream = (
            None
            if in_rate == SAMPLE_RATE
            else soxr.ResampleStream(in_rate, SAMPLE_RATE, 1, dtype="float32", quality="HQ")
        )

    def process(self, frames: Float32, *, last: bool = False) -> bytes:
        mono = downmix(frames)
        if self._stream is None:
            return to_pcm16(mono)
        out: Float32 = self._stream.resample_chunk(mono, last=last)
        return to_pcm16(out)


class PreRoll:
    """Keeps the most recent `ms` of 16 kHz PCM16 while no utterance is active."""

    def __init__(self, ms: int) -> None:
        self.max_bytes = SAMPLE_RATE * ms // 1000 * 2
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._lock = threading.Lock()

    def push(self, pcm: bytes) -> None:
        if self.max_bytes == 0:
            return
        with self._lock:
            self._chunks.append(pcm)
            self._size += len(pcm)
            while self._chunks and self._size - len(self._chunks[0]) >= self.max_bytes:
                self._size -= len(self._chunks.popleft())

    def take(self) -> bytes:
        with self._lock:
            data = b"".join(self._chunks)
            self._chunks.clear()
            self._size = 0
        return data[-self.max_bytes :] if self.max_bytes else b""


def read_wav(path: Path) -> bytes:
    """Any PCM WAV (8/16/32-bit, any rate, any channels) -> 16 kHz mono PCM16."""
    with wave.open(str(path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"{path}: unsupported sample width {width * 8} bit")
    mono = downmix(x.reshape(-1, channels))
    if rate == SAMPLE_RATE:
        return to_pcm16(mono)
    out: Float32 = soxr.resample(mono, rate, SAMPLE_RATE, quality="VHQ")
    return to_pcm16(out.astype(np.float32))


def write_wav(path: Path, pcm16: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm16)


def rms_dbfs(pcm16: bytes) -> float:
    if len(pcm16) < 2:
        return float("-inf")
    x = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], "<i2").astype(np.float64) / 32768.0
    rms = float(np.linalg.norm(x)) / math.sqrt(len(x))
    return 20 * math.log10(rms) if rms > 0 else float("-inf")


def flat(pcm16: bytes) -> bool:
    """No signal at all: every sample the same value (a muted or dead input sends digital
    silence or a stuck level; a real room never does), or no samples."""
    x: npt.NDArray[np.int16] = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], "<i2")
    return x.size == 0 or int(np.ptp(x)) == 0


SPEECH_FRAME_MS = 20
SPEECH_OVER_FLOOR_DB = 12.0
SPEECH_THRESHOLD_DBFS = (-55.0, -40.0)  # louder than -40 always counts, -55 or less never


def speech_ms(pcm16: bytes) -> int:
    """Rough amount of speech in 16 kHz PCM16: 20 ms frames louder than the noise
    floor (the 10th percentile frame) + 12 dB, the threshold kept within
    SPEECH_THRESHOLD_DBFS."""
    n = SAMPLE_RATE * SPEECH_FRAME_MS // 1000
    usable = len(pcm16) // (2 * n) * 2 * n
    if not usable:
        return 0
    frames = np.frombuffer(pcm16[:usable], "<i2").astype(np.float64).reshape(-1, n) / 32768.0
    db: npt.NDArray[np.float64] = 10 * np.log10(np.mean(frames**2, axis=1) + 1e-12)
    floor = float(np.percentile(db, 10))
    threshold = min(
        max(floor + SPEECH_OVER_FLOOR_DB, SPEECH_THRESHOLD_DBFS[0]), SPEECH_THRESHOLD_DBFS[1]
    )
    return int((db > threshold).sum()) * SPEECH_FRAME_MS
