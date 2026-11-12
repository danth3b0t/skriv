"""skriv.v1.Asr servicer (grpc.aio)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import grpc

from skriv.v1 import skriv_pb2 as pb
from skriv.v1 import skriv_pb2_grpc as pb_grpc

from .errors import E, SkrivError, protocol_violation

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .session import AsrCore, Session

    type _Context[Req, Resp] = grpc.aio.ServicerContext[Req, Resp]

log = logging.getLogger("skriv_server.asr")

STATUS_INTERVAL_S = 0.5  # proposal-a §2.2 rule 7


class _ClientCancelError(Exception):
    pass


def _ended(
    reason: pb.SessionEndReason.ValueType, err: SkrivError, utterance_id: str
) -> pb.TranscribeResponse:
    return pb.TranscribeResponse(
        session_ended=pb.SessionEnded(
            reason=reason, error=err.detail(utterance_id), utterance_id=utterance_id
        )
    )


class AsrServicer(pb_grpc.AsrServicer):
    def __init__(self, core: AsrCore) -> None:
        self.core = core

    async def GetCapabilities(
        self,
        request: pb.GetCapabilitiesRequest,
        context: _Context[pb.GetCapabilitiesRequest, pb.Capabilities],
    ) -> pb.Capabilities:
        return self.core.capabilities()

    async def Transcribe(  # noqa: PLR0912, PLR0915 - one streaming state machine over shared locals
        self,
        request_iterator: AsyncIterator[pb.TranscribeRequest],
        context: _Context[pb.TranscribeRequest, pb.TranscribeResponse],
    ) -> AsyncIterator[pb.TranscribeResponse]:
        loop = asyncio.get_running_loop()
        it = request_iterator.__aiter__()
        session: Session | None = None
        accepted = False
        utterance_id = ""
        read: asyncio.Future[pb.TranscribeRequest] | None = None
        fin: asyncio.Future[pb.FinalResult] | None = None
        try:
            try:
                first = await it.__anext__()
            except StopAsyncIteration:
                raise protocol_violation("stream closed before start") from None
            if first.WhichOneof("msg") != "start":
                raise protocol_violation("first message must be start")
            utterance_id = first.start.utterance_id
            session = await self.core.open(first.start)
            yield pb.TranscribeResponse(accepted=session.accepted())
            accepted = True
            log.info("session %s: accepted", session.session_id)
            for w in session.start_warnings:
                yield pb.TranscribeResponse(warning=w)

            next_status = loop.time() + STATUS_INTERVAL_S
            closed = False
            while True:
                if read is None and not closed:
                    read = asyncio.ensure_future(it.__anext__())
                waits = {t for t in (read, fin) if t is not None}
                timeout = None if session.ended else max(0.0, next_status - loop.time())
                done, _ = await asyncio.wait(
                    waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )

                if fin is not None and fin in done:
                    final = fin.result()
                    for w in session.final_warnings:
                        yield pb.TranscribeResponse(warning=w)
                    yield pb.TranscribeResponse(final_result=final)
                    return

                if read is not None and read in done:
                    try:
                        msg = read.result()
                    except StopAsyncIteration:
                        msg = None
                    read = None
                    if msg is None:  # half-close
                        closed = True
                        if not session.ended:  # == end_of_audio{UNSPECIFIED}
                            session.end_of_audio(None)
                            fin = asyncio.ensure_future(session.finalize())
                        continue
                    kind = msg.WhichOneof("msg")
                    if kind == "audio":
                        for w in session.add_audio(msg.audio):
                            yield pb.TranscribeResponse(warning=w)
                    elif kind == "context_update":
                        for w in session.update_context(msg.context_update):
                            yield pb.TranscribeResponse(warning=w)
                    elif kind == "eager_end":
                        session.eager_end(msg.eager_end)
                    elif kind == "end_of_audio":
                        session.end_of_audio(msg.end_of_audio)
                        fin = asyncio.ensure_future(session.finalize())
                    elif kind == "cancel":
                        raise _ClientCancelError()
                    elif kind == "start":
                        raise protocol_violation("duplicate start")
                    else:
                        raise protocol_violation("empty TranscribeRequest")

                if not session.ended and loop.time() >= next_status:
                    yield pb.TranscribeResponse(status=session.status())
                    next_status = loop.time() + STATUS_INTERVAL_S

        except _ClientCancelError:
            err = SkrivError(E.ERROR_CODE_CANCELLED, "cancelled by client")
            yield _ended(pb.SESSION_END_REASON_CANCELLED, err, utterance_id)
            await err.abort(context, utterance_id)
        except SkrivError as e:
            reason = pb.SESSION_END_REASON_FAILED if accepted else pb.SESSION_END_REASON_REJECTED
            log.info(
                "transcribe %s: %s %s",
                pb.SessionEndReason.Name(reason), pb.ErrorCode.Name(e.code), e.message,
            )  # fmt: skip
            yield _ended(reason, e, utterance_id)
            await e.abort(context, utterance_id)
        except (asyncio.CancelledError, grpc.aio.AbortError):
            raise
        except Exception:
            log.exception("transcribe failed")
            err = SkrivError(E.ERROR_CODE_BACKEND_FAILURE, "internal error", retryable=True)
            reason = pb.SESSION_END_REASON_FAILED if accepted else pb.SESSION_END_REASON_REJECTED
            yield _ended(reason, err, utterance_id)
            await err.abort(context, utterance_id)
        finally:
            for task in (read, fin):
                if task is not None:
                    task.cancel()
            if session is not None:
                session.close()
