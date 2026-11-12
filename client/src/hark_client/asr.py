"""hark.v1 Asr client side: channel, capabilities cache, one Transcribe stream per utterance.

Uses the synchronous grpcio API: hotkey, audio callback and ctl socket all live on
threads, so a blocking request iterator fed from a queue is the simplest bridge.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import grpc

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from hark_client import __version__
from hark_client.context import TermKind, TermSource

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from hark_client.config import ProviderConfig, ServerConfig
    from hark_client.context import Term

log = logging.getLogger("hark.asr")

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 640  # 40 ms (ADR-001 #8)
CLIENT_VERSION = f"hark-client/{__version__}"

_TERM_KINDS = {
    TermKind.IDENTIFIER: pb.TERM_KIND_IDENTIFIER,
    TermKind.PATH: pb.TERM_KIND_PATH,
    TermKind.FLAG: pb.TERM_KIND_IDENTIFIER,  # no FLAG kind in hark.v1
    TermKind.COMMAND: pb.TERM_KIND_COMMAND,
    TermKind.NAME: pb.TERM_KIND_PRODUCT,
    TermKind.WORD: pb.TERM_KIND_WORD,
}
_SOURCES = {
    TermSource.DICTIONARY: pb.CONTEXT_SOURCE_USER_DICTIONARY,
    TermSource.VOCABULARY: pb.CONTEXT_SOURCE_USER_DICTIONARY,
    TermSource.SELECTION: pb.CONTEXT_SOURCE_SELECTION,
    TermSource.FOCUSED_TEXT: pb.CONTEXT_SOURCE_FOCUSED_TEXT,
    TermSource.WINDOW_TITLE: pb.CONTEXT_SOURCE_WINDOW_TITLE,
    TermSource.TERMINAL_TEXT: pb.CONTEXT_SOURCE_TERMINAL_TEXT,
    TermSource.AGENT_SESSION: pb.CONTEXT_SOURCE_AGENT_SESSION,
    TermSource.REPO_RECENT: pb.CONTEXT_SOURCE_GIT_REPO,
    TermSource.REPO: pb.CONTEXT_SOURCE_GIT_REPO,
}


class AsrError(RuntimeError):
    pass


def unix_us() -> int:
    return time.time_ns() // 1000


def uuid7() -> str:
    """RFC 9562 UUIDv7: 48-bit Unix ms timestamp, version, variant, random."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10))
    value = (
        (ms & ((1 << 48) - 1)) << 80
        | 0x7 << 76
        | ((rand >> 62) & 0xFFF) << 64
        | 0b10 << 62
        | rand & ((1 << 62) - 1)
    )
    return str(uuid.UUID(int=value))


# A provider that was down is noticed within a few seconds of coming back (gRPC's default
# reconnect backoff grows to 120 s).
CHANNEL_OPTIONS: list[tuple[str, int | str]] = [
    ("grpc.initial_reconnect_backoff_ms", 500),
    ("grpc.max_reconnect_backoff_ms", 5000),
]


def open_channel(cfg: ServerConfig | ProviderConfig) -> grpc.Channel:
    if not cfg.tls_ca:
        return grpc.insecure_channel(cfg.address, options=CHANNEL_OPTIONS)

    def read(p: str) -> bytes:
        return Path(p).expanduser().read_bytes()

    creds = grpc.ssl_channel_credentials(
        root_certificates=read(cfg.tls_ca),
        private_key=read(cfg.tls_key) if cfg.tls_key else None,
        certificate_chain=read(cfg.tls_cert) if cfg.tls_cert else None,
    )
    options = list(CHANNEL_OPTIONS)
    if cfg.tls_server_name:
        options.append(("grpc.ssl_target_name_override", cfg.tls_server_name))
    return grpc.secure_channel(cfg.address, creds, options=options)


class Capabilities:
    """Cached GetCapabilities; refreshed when SessionAccepted reports a new etag."""

    def __init__(self, channel: grpc.Channel) -> None:
        self._stub = pb_grpc.AsrStub(channel)
        self._lock = threading.Lock()
        self._caps: pb.Capabilities | None = None

    def refresh(self, timeout: float = 2.0) -> pb.Capabilities | None:
        try:
            caps = self._stub.GetCapabilities(
                pb.GetCapabilitiesRequest(client_version=CLIENT_VERSION), timeout=timeout
            )
        except grpc.RpcError as e:
            log.warning("GetCapabilities failed: %s", rpc_error_text(e))
            return self.get()
        with self._lock:
            self._caps = caps
        return caps

    def get(self) -> pb.Capabilities | None:
        with self._lock:
            return self._caps

    def note_etag(self, etag: str) -> None:
        caps = self.get()
        if etag and (caps is None or caps.etag != etag):
            threading.Thread(target=self.refresh, daemon=True, name="hark-caps").start()

    def cleanup_profile(self, profile_id: str) -> bool:
        caps = self.get()
        return (
            caps is not None
            and "hark.cleanup" in caps.features
            and any(p.id == profile_id for p in caps.cleanup_profiles)
        )


def rpc_error_text(e: grpc.RpcError) -> str:
    if isinstance(e, grpc.Call):
        return f"{e.code().name}: {e.details()}"
    return type(e).__name__


def bias_list(terms: Sequence[Term]) -> pb.BiasList:
    """Context shortlist -> hark.v1 BiasList (ranked order kept, ids t0, t1, ...)."""
    out = pb.BiasList()
    seen: set[str] = set()
    for t in terms:
        if not t.canonical or t.canonical in seen:
            continue
        seen.add(t.canonical)
        out.terms.append(
            pb.BiasTerm(
                id=f"t{len(out.terms)}",
                canonical=t.canonical,
                aliases=[
                    pb.SpokenAlias(text=a, kind=pb.ALIAS_KIND_SPOKEN_FORM)
                    for a in dict.fromkeys(t.spoken)
                    if a and a != t.canonical
                ],
                weight=min(1.0, max(0.0, t.weight)),
                kind=_TERM_KINDS.get(t.kind, pb.TERM_KIND_UNSPECIFIED),
                provenance=[_SOURCES.get(t.source, pb.CONTEXT_SOURCE_UNSPECIFIED)],
            )
        )
    if out.terms:
        out.content_hash = hashlib.sha256(
            pb.BiasList(terms=out.terms).SerializeToString(deterministic=True)
        ).hexdigest()
    return out


class TranscribeStream:
    """One Transcribe call. The call opens at construction; SessionStart is sent by
    `start()` (possibly later); audio fed before that is queued behind it."""

    def __init__(self, channel: grpc.Channel, *, timeout_s: float = 600.0) -> None:
        self._stub = pb_grpc.AsrStub(channel)
        self._start: pb.SessionStart | None = None
        self._start_ready = threading.Event()
        self._q: queue.SimpleQueue[pb.TranscribeRequest | None] = queue.SimpleQueue()
        self._terminal = threading.Event()
        self._closed = False  # EndOfAudio or Cancel queued
        self._pending = bytearray()  # < CHUNK_SAMPLES of PCM16
        self._seq = 0
        self.samples_sent = 0
        self.accepted: pb.SessionAccepted | None = None
        self.final: pb.FinalResult | None = None
        self.ended: pb.SessionEnded | None = None
        self.error: str | None = None
        self.warnings: list[pb.ServerWarning] = []
        self.t_open = time.perf_counter()
        self.t_accepted: float | None = None
        self.t_end: float | None = None
        self.t_final: float | None = None
        # Wall-clock marks (us), comparable with the server's *_unix_us timings:
        # end_enqueued (end() called), end_dequeued (the request iterator took it),
        # end_sent (gRPC asked for the next request, i.e. the EndOfAudio write
        # completed), final_received.
        self.unix_us: dict[str, int] = {}
        self._call = self._stub.Transcribe(self._requests(), timeout=timeout_s)
        self._reader = threading.Thread(target=self._read, daemon=True, name="hark-asr-read")
        self._reader.start()

    def _requests(self) -> Iterator[pb.TranscribeRequest]:
        self._start_ready.wait()
        if self._start is None or self._terminal.is_set():
            return
        yield pb.TranscribeRequest(start=self._start)
        while True:
            item = self._q.get()
            if item is None or self._terminal.is_set():
                return
            last = item.WhichOneof("msg") in ("end_of_audio", "cancel")
            if last:
                self.unix_us["end_dequeued"] = unix_us()
            yield item
            if last:
                self.unix_us["end_sent"] = unix_us()
                return  # half-close

    def _read(self) -> None:
        try:
            for resp in self._call:
                kind = resp.WhichOneof("msg")
                if kind == "accepted":
                    self.accepted, self.t_accepted = resp.accepted, time.perf_counter()
                elif kind == "warning":
                    self.warnings.append(resp.warning)
                elif kind == "final_result":
                    self.final, self.t_final = resp.final_result, time.perf_counter()
                    self.unix_us["final_received"] = unix_us()
                    self._terminal.set()
                elif kind == "session_ended":
                    self.ended = resp.session_ended
                    self._terminal.set()
        except grpc.RpcError as e:
            if self.final is None and self.ended is None:
                self.error = rpc_error_text(e)
        finally:
            self._terminal.set()
            self._start_ready.set()
            self._q.put(None)

    def start(self, start: pb.SessionStart) -> None:
        self._start = start
        self._start_ready.set()

    def feed(self, pcm16: bytes) -> None:
        """Append PCM s16le 16 kHz mono; sent in 40 ms chunks."""
        if self._closed:
            return
        self._pending += pcm16
        size = CHUNK_SAMPLES * 2
        while len(self._pending) >= size:
            self._send_chunk(bytes(self._pending[:size]))
            del self._pending[:size]

    def _send_chunk(self, data: bytes) -> None:
        n = len(data) // 2
        self._q.put(
            pb.TranscribeRequest(
                audio=pb.AudioChunk(
                    data=data, seq=self._seq, start_sample=self.samples_sent, num_samples=n
                )
            )
        )
        self._seq += 1
        self.samples_sent += n

    def end(self, reason: pb.EndReason.ValueType = pb.END_REASON_KEY_UP) -> None:
        if self._closed:
            return
        if len(self._pending) >= 2:
            self._send_chunk(bytes(self._pending[: len(self._pending) // 2 * 2]))
        self._pending.clear()
        self._closed = True
        self.t_end = time.perf_counter()
        self.unix_us["end_enqueued"] = unix_us()
        self._q.put(
            pb.TranscribeRequest(
                end_of_audio=pb.EndOfAudio(
                    last_seq=max(self._seq - 1, 0), total_samples=self.samples_sent, reason=reason
                )
            )
        )

    def cancel(self, reason: pb.CancelReason.ValueType = pb.CANCEL_REASON_USER_ABORT) -> None:
        if self._terminal.is_set():
            return
        if not self._start_ready.is_set():
            self._call.cancel()  # nothing sent yet; just drop the call
            return
        self._closed = True
        self._q.put(pb.TranscribeRequest(cancel=pb.Cancel(reason=reason)))

    def wait_final(self, timeout_s: float) -> pb.FinalResult:
        if not self._terminal.wait(timeout_s):
            self._call.cancel()
            raise AsrError(f"no FinalResult within {timeout_s:.1f} s")
        self._reader.join(1.0)
        if self.final is not None:
            return self.final
        if self.ended is not None:
            reason = pb.SessionEndReason.Name(self.ended.reason)
            code = pb.ErrorCode.Name(self.ended.error.code)
            raise AsrError(f"{reason} {code}: {self.ended.error.message}")
        raise AsrError(self.error or "stream closed without a result")
