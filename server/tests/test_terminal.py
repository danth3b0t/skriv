"""ADR-001 #4: every Transcribe call ends with exactly one terminal message
(final_result or session_ended) as the last message, and the gRPC status
mirrors it (OK, or the ErrorDetail code's canonical status)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import grpc
import pytest
from conftest import audio_reqs, eoa_req, speech, start_req, utterance

from skriv.v1 import skriv_pb2 as pb
from skriv_server.backends.mock import MockBackend

if TYPE_CHECKING:
    from conftest import Script, Serve

    from skriv_server.backends import Backend, BiasPlan, DecodeOut, Pcm

C = grpc.StatusCode
E = pb.ErrorCode
REJECTED, FAILED, CANCELLED = (
    pb.SESSION_END_REASON_REJECTED,
    pb.SESSION_END_REASON_FAILED,
    pb.SESSION_END_REASON_CANCELLED,
)

PCM = speech(400)
CHUNKS = audio_reqs(PCM)  # 10 chunks
EOA = eoa_req(len(PCM), len(CHUNKS) - 1)
CANCEL = pb.TranscribeRequest(cancel=pb.Cancel(reason=pb.CANCEL_REASON_USER_ABORT))


class Exploding(MockBackend):
    def _decode_sync(self, pcm: Pcm, plan: BiasPlan | None) -> DecodeOut:
        raise RuntimeError("boom")


def overlapping() -> list[pb.TranscribeRequest]:
    bad = pb.TranscribeRequest()
    bad.CopyFrom(CHUNKS[1])
    bad.audio.start_sample = 0
    return [start_req(), CHUNKS[0], bad]


# Failure scripts end at the offending message (see Client.transcribe).
type Case = tuple[
    Script,
    Backend | None,
    pb.SessionEndReason.ValueType | None,
    grpc.StatusCode,
    pb.ErrorCode.ValueType | None,
    list[str] | None,
]

CASES: dict[str, Case] = {
    # name: (script, backend, terminal reason (None = final), status, error code, unavailable)
    "final": (utterance(PCM), None, None, C.OK, None, None),
    "half-close without end_of_audio": ([start_req(), *CHUNKS], None, None, C.OK, None, None),
    "cancel": ([start_req(), *CHUNKS, CANCEL], None, CANCELLED, C.CANCELLED, E.ERROR_CODE_CANCELLED, None),
    "empty stream": ([], None, REJECTED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None),
    "audio before start": ([*CHUNKS, EOA], None, REJECTED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None),
    "missing utterance_id": (
        [start_req(utterance_id=""), *CHUNKS, EOA],
        None, REJECTED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None,
    ),
    "unknown model": (
        utterance(PCM, model_id="nope"), None, REJECTED, C.NOT_FOUND, E.ERROR_CODE_MODEL_NOT_FOUND, None,
    ),
    "8 kHz audio": (
        utterance(PCM, audio_format=pb.AudioFormat(sample_rate_hz=8000)),
        None, REJECTED, C.INVALID_ARGUMENT, E.ERROR_CODE_UNSUPPORTED_AUDIO_FORMAT, None,
    ),
    "unsupported language": (
        utterance(PCM, languages=["ja-JP"]),
        None, REJECTED, C.INVALID_ARGUMENT, E.ERROR_CODE_UNSUPPORTED_LANGUAGE, None,
    ),
    "privacy flags": (
        utterance(PCM, privacy=pb.PrivacyFlags(allow_eval_capture=True, allow_replay_cache=True)),
        None, REJECTED, C.FAILED_PRECONDITION, E.ERROR_CODE_PRIVACY_UNSATISFIABLE,
        ["privacy.allow_eval_capture", "privacy.allow_replay_cache"],
    ),
    "required features": (
        utterance(PCM, required_features=["bias", "free_text", "skriv.opus", "result.anchor"]),
        None, REJECTED, C.FAILED_PRECONDITION, E.ERROR_CODE_REQUIRED_FEATURE_UNAVAILABLE,
        ["free_text", "skriv.opus"],
    ),
    "bias weight out of range": (
        utterance(PCM, context=pb.RecognitionContext(
            bias=pb.BiasList(terms=[pb.BiasTerm(id="a", canonical="x", weight=1.5)]))),
        None, REJECTED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None,
    ),
    "duplicate start": (
        [start_req(), start_req()], None, FAILED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None,
    ),
    "overlapping start_sample": (
        overlapping(), None, FAILED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None,
    ),
    "num_samples mismatch": (
        [start_req(), pb.TranscribeRequest(audio=pb.AudioChunk(data=b"\0" * 10, num_samples=4))],
        None, FAILED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None,
    ),
    "end_of_audio total mismatch": (
        [start_req(), *CHUNKS, eoa_req(len(PCM) + 1, len(CHUNKS) - 1)],
        None, FAILED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None,
    ),
    "audio after end_of_audio": (
        [start_req(), *CHUNKS, EOA, CHUNKS[0]],
        MockBackend(decode_ms=300), FAILED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None,
    ),
    "context revision not increasing": (
        [start_req(context=pb.RecognitionContext(revision=3)), *CHUNKS,
         pb.TranscribeRequest(context_update=pb.ContextUpdate(context=pb.RecognitionContext(revision=3)))],
        None, FAILED, C.INVALID_ARGUMENT, E.ERROR_CODE_PROTOCOL_VIOLATION, None,
    ),
    "audio too long": (  # 200 ms = 5 chunks; the 6th overflows
        [start_req(deadlines=pb.Deadlines(max_audio_ms=200)), *CHUNKS[:6]],
        None, FAILED, C.OUT_OF_RANGE, E.ERROR_CODE_AUDIO_TOO_LONG, None,
    ),
    "backend failure": (
        utterance(PCM), Exploding(), FAILED, C.INTERNAL, E.ERROR_CODE_BACKEND_FAILURE, None,
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", list(CASES))
async def test_exactly_one_terminal_message(serve: Serve, name: str):
    script, backend, reason, code, error_code, unavailable = CASES[name]
    client = await serve(backend=backend)
    out = await client.transcribe(script)

    terminal = [k for k in out.kinds if k in ("final_result", "session_ended")]
    assert len(terminal) == 1, out.kinds
    assert out.kinds[-1] == terminal[0]
    assert out.code == code
    # `accepted` comes first, exactly once, unless the session was rejected.
    assert out.kinds.count("accepted") == (0 if reason == REJECTED else 1)
    if reason != REJECTED:
        assert out.kinds[0] == "accepted"

    if reason is None:
        assert terminal == ["final_result"]
        assert out.error is None
        return
    (ended,) = out.of("session_ended")
    assert ended.reason == reason
    assert ended.error.code == error_code
    assert out.error is not None
    assert out.error.code == error_code  # also in the trailers
    first = script[0] if script else None
    if isinstance(first, pb.TranscribeRequest) and first.WhichOneof("msg") == "start":
        assert ended.utterance_id == first.start.utterance_id
    if unavailable is not None:
        assert list(ended.error.unavailable_features) == unavailable
