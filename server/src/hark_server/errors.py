"""hark ErrorDetail <-> gRPC status mapping.

Messages in HarkError MUST NOT contain user text or context (they reach logs
and the wire); use sizes, ids and counts only.
"""

from __future__ import annotations

from typing import Never

import grpc
from google.protobuf import any_pb2
from google.rpc.status_pb2 import Status
from grpc_status import rpc_status

from hark.v1 import hark_pb2 as pb

E = pb.ErrorCode

# ErrorCode -> canonical gRPC status (proto comments in hark.proto).
_GRPC_CODE = {
    E.ERROR_CODE_MODEL_NOT_FOUND: grpc.StatusCode.NOT_FOUND,
    E.ERROR_CODE_MODEL_NOT_READY: grpc.StatusCode.UNAVAILABLE,
    E.ERROR_CODE_OVERLOADED: grpc.StatusCode.RESOURCE_EXHAUSTED,
    E.ERROR_CODE_UNSUPPORTED_AUDIO_FORMAT: grpc.StatusCode.INVALID_ARGUMENT,
    E.ERROR_CODE_UNSUPPORTED_LANGUAGE: grpc.StatusCode.INVALID_ARGUMENT,
    E.ERROR_CODE_REQUIRED_FEATURE_UNAVAILABLE: grpc.StatusCode.FAILED_PRECONDITION,
    E.ERROR_CODE_PROTOCOL_VIOLATION: grpc.StatusCode.INVALID_ARGUMENT,
    E.ERROR_CODE_AUDIO_TOO_LONG: grpc.StatusCode.OUT_OF_RANGE,
    E.ERROR_CODE_BACKEND_FAILURE: grpc.StatusCode.INTERNAL,
    E.ERROR_CODE_PRIVACY_UNSATISFIABLE: grpc.StatusCode.FAILED_PRECONDITION,
    E.ERROR_CODE_CANCELLED: grpc.StatusCode.CANCELLED,
}


class HarkError(Exception):
    def __init__(  # noqa: PLR0913 - keyword-only, mirrors ErrorDetail
        self,
        code: pb.ErrorCode.ValueType,
        message: str,
        *,
        retryable: bool = False,
        retry_after_ms: int = 0,
        unavailable_features: list[str] | None = None,
        grpc_code: grpc.StatusCode | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms
        self.unavailable_features = list(unavailable_features or [])
        self.grpc_code = grpc_code or _GRPC_CODE.get(code, grpc.StatusCode.UNKNOWN)

    def detail(self, utterance_id: str = "") -> pb.ErrorDetail:
        return pb.ErrorDetail(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            retry_after_ms=self.retry_after_ms,
            utterance_id=utterance_id,
            unavailable_features=self.unavailable_features,
        )

    async def abort[Req, Resp](
        self, context: grpc.aio.ServicerContext[Req, Resp], utterance_id: str = ""
    ) -> Never:
        """End the RPC with this error; ErrorDetail goes into grpc-status-details-bin."""
        detail = any_pb2.Any()
        detail.Pack(self.detail(utterance_id))
        status = rpc_status.to_status(  # pyright: ignore[reportUnknownMemberType] - typeshed: untyped param
            Status(code=self.grpc_code.value[0], message=self.message, details=[detail])
        )
        await context.abort(status.code, status.details, status.trailing_metadata)


def protocol_violation(message: str) -> HarkError:
    return HarkError(E.ERROR_CODE_PROTOCOL_VIOLATION, message)


# PrivacyFlags field -> PrivacyCapabilities field.
_PRIVACY_FLAGS = {
    "allow_audio_retention": "audio_retention",
    "allow_transcript_retention": "transcript_retention",
    "allow_context_logging": "context_logging",
    "allow_eval_capture": "eval_capture",
    "allow_replay_cache": "replay_cache",
}


def check_privacy(flags: pb.PrivacyFlags, supported: pb.PrivacyCapabilities) -> None:
    """ADR-001 #7: reject allow_* flags this server cannot act on, never ignore them.

    This server retains nothing, so a false flag is always honoured.
    """
    unsupported = [
        f"privacy.{flag}"
        for flag, cap in _PRIVACY_FLAGS.items()
        if getattr(flags, flag) and not getattr(supported, cap)
    ]
    if unsupported:
        raise HarkError(
            E.ERROR_CODE_PRIVACY_UNSATISFIABLE,
            f"privacy flags not supported by this server: {', '.join(unsupported)}",
            unavailable_features=unsupported,
        )
