"""Capability negotiation, health and warmup."""

from __future__ import annotations

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from conftest import kubectl_term, speech, utterance
from hark.v1 import hark_pb2 as pb
from hark_server.backends.mock import MockBackend

SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING


async def test_capabilities_describe_model_limits_and_privacy(serve):
    client = await serve(max_concurrent_sessions=3)
    caps = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest(client_protocol_minor=0))
    assert (caps.protocol.major, caps.protocol.minor) == (1, 0)
    (model,) = caps.models
    assert model.model_id == "mock" and model.state == pb.MODEL_STATE_READY
    assert model.bias.mechanism == pb.BIAS_MECHANISM_DECODE_TIME_BOOST
    assert (model.bias.boost_min, model.bias.boost_max) == (1.0, 5.0)
    assert {"hark.recognize", "hark.eager_end"} <= set(caps.features)
    assert "hark.cleanup" not in caps.features  # no cleanup backend configured
    assert caps.limits.max_concurrent_sessions == 3
    assert caps.privacy == pb.PrivacyCapabilities()  # retains nothing => supports no allow_* flag

    # The etag is stable and echoed in SessionAccepted.
    again = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert again.etag == caps.etag
    out = await client.transcribe(utterance(speech(200)))
    assert out.of("accepted")[0].capabilities_etag == caps.etag


async def test_required_features_that_exist_are_accepted(serve):
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


async def test_cold_model_rejects_until_warmup_then_serves(serve):
    client = await serve(backend=MockBackend(load_ms=50), warmup_on_start=False)
    health = health_pb2_grpc.HealthStub(client.channel)
    check = await health.Check(health_pb2.HealthCheckRequest(service="hark.v1.Asr"))
    assert check.status == NOT_SERVING
    cold = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert cold.models[0].state == pb.MODEL_STATE_COLD

    out = await client.transcribe(utterance(speech(200)))
    assert out.code == grpc.StatusCode.UNAVAILABLE
    assert out.error.code == pb.ERROR_CODE_MODEL_NOT_READY and out.error.retryable

    warm = await client.asr.Warmup(pb.WarmupRequest())
    assert warm.state == pb.MODEL_STATE_READY and warm.warm_ms >= 50
    check = await health.Check(health_pb2.HealthCheckRequest(service=""))
    assert check.status == SERVING
    ready = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert ready.etag != cold.etag  # model state changed
    assert (await client.transcribe(utterance(speech(200)))).code == grpc.StatusCode.OK
