"""Capability negotiation and health."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import grpc
from conftest import kubectl_term, speech, utterance
from grpc_health.v1 import health_pb2, health_pb2_grpc

from hark.v1 import hark_pb2 as pb

if TYPE_CHECKING:
    from conftest import Serve

# typeshed's grpcio-health-checking stubs type this as Incomplete and give HealthStub
# no members; the value is an int.
NOT_SERVING = cast("int", health_pb2.HealthCheckResponse.NOT_SERVING)


class _HealthCheckResponse(Protocol):
    @property
    def status(self) -> int: ...


class _HealthStub(Protocol):
    Check: grpc.aio.UnaryUnaryMultiCallable[health_pb2.HealthCheckRequest, _HealthCheckResponse]


async def test_capabilities_describe_model_limits_and_privacy(serve: Serve):
    client = await serve(max_concurrent_sessions=3)
    caps = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest(client_protocol_minor=0))
    assert (caps.protocol.major, caps.protocol.minor) == (1, 1)
    (model,) = caps.models
    assert model.model_id == "mock"
    assert model.state == pb.MODEL_STATE_READY
    assert model.bias.mechanism == pb.BIAS_MECHANISM_DECODE_TIME_BOOST
    assert (model.bias.boost_min, model.bias.boost_max) == (1.0, 5.0)
    assert "hark.eager_end" in caps.features
    assert "hark.cleanup" not in caps.features  # no cleanup backend configured
    assert caps.limits.max_concurrent_sessions == 3
    assert caps.privacy == pb.PrivacyCapabilities()  # retains nothing => supports no allow_* flag

    # The etag is stable and echoed in SessionAccepted.
    again = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert again.etag == caps.etag
    out = await client.transcribe(utterance(speech(200)))
    assert out.of("accepted")[0].capabilities_etag == caps.etag


async def test_required_features_that_exist_are_accepted(serve: Serve):
    client = await serve()
    out = await client.transcribe(
        utterance(
            speech(600),
            context=pb.RecognitionContext(bias=pb.BiasList(terms=[kubectl_term()])),
            required_features=["bias", "result.anchor", "result.word_timestamps", "hark.eager_end"],
        )
    )
    assert out.code == grpc.StatusCode.OK
    assert out.final.flags.anchor == pb.FIELD_STATUS_PRESENT
    # Required word timestamps are delivered even though options didn't ask.
    assert len(out.final.n_best[0].words) == 3


async def test_cold_model_is_not_serving_and_rejects_sessions(serve: Serve):
    client = await serve(warmup_on_start=False)
    health = cast("_HealthStub", health_pb2_grpc.HealthStub(client.channel))
    check = await health.Check(health_pb2.HealthCheckRequest(service="hark.v1.Asr"))
    assert check.status == NOT_SERVING
    cold = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert cold.models[0].state == pb.MODEL_STATE_COLD

    out = await client.transcribe(utterance(speech(200)))
    assert out.code == grpc.StatusCode.UNAVAILABLE
    assert out.error is not None
    assert out.error.code == pb.ERROR_CODE_MODEL_NOT_READY
    assert out.error.retryable
