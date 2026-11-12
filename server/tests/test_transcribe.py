"""Transcribe behaviour against the in-process server (mock backend).

Mock recognition (see backends/mock.py): every 200 ms window of non-silent audio
emits the next word of "please run cube control get pods in the default ...";
with a bias plan the alias "cube control" becomes the canonical "kubectl".
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import grpc
import pytest
from conftest import audio_reqs, eoa_req, kubectl_term, silence, speech, start_req, utterance

from hark.v1 import hark_pb2 as pb
from skriv_server.backends.mock import MockBackend

if TYPE_CHECKING:
    from conftest import Serve

FS = pb.FieldStatus


async def test_streaming_happy_path_with_bias(serve: Serve):
    client = await serve()
    pcm = speech(1200)  # 6 words
    out = await client.transcribe(
        utterance(
            pcm,
            languages=["en"],
            context=pb.RecognitionContext(
                revision=1, bias=pb.BiasList(terms=[kubectl_term(weight=0.5)])
            ),
            options=pb.ResultOptions(word_timestamps=True, word_confidence=True),
        )
    )
    assert out.code == grpc.StatusCode.OK
    assert out.kinds[0] == "accepted"
    assert out.kinds[-1] == "final_result"

    applied = out.of("accepted")[0].applied
    assert applied.bias_mechanism == pb.BIAS_MECHANISM_DECODE_TIME_BOOST
    assert applied.bias_terms_used == 1
    # Mock native scale is [1, 5]: 1 + 0.5 * (5 - 1).
    assert applied.term_boosts["t-kubectl"] == pytest.approx(3.0)

    final = out.final
    primary = final.n_best[0]
    assert primary.text == "please run kubectl get pods"
    assert primary.origin == pb.HYPOTHESIS_ORIGIN_BIASED
    assert final.anchor.text == "please run cube control get pods"
    assert final.anchor.origin == pb.HYPOTHESIS_ORIGIN_UNBIASED

    (hit,) = final.bias_hits
    assert hit.term_id == "t-kubectl"
    assert hit.decision == pb.BIAS_HIT_DECISION_APPLIED
    assert hit.matched_form == "kubectl"
    assert not hit.via_alias
    assert hit.anchor_text == "cube control"
    assert primary.text.encode()[hit.text_byte_start : hit.text_byte_end] == b"kubectl"
    assert (hit.start_ms, hit.end_ms) == (400, 750)  # windows 2-3 of 200 ms
    assert hit.boost == pytest.approx(3.0)

    assert [w.text for w in primary.words] == primary.text.split()
    for w in primary.words:
        assert primary.text.encode()[w.text_byte_start : w.text_byte_end].decode() == w.text
        assert w.HasField("confidence")
    assert [w.bias_term_id for w in primary.words] == ["", "", "t-kubectl", "", ""]

    assert final.flags.anchor == FS.FIELD_STATUS_PRESENT
    assert final.flags.word_timestamps == FS.FIELD_STATUS_PRESENT
    assert final.flags.word_confidence == FS.FIELD_STATUS_PRESENT
    assert final.flags.bias_hits == FS.FIELD_STATUS_PRESENT
    assert final.flags.n_best == FS.FIELD_STATUS_NOT_REQUESTED
    assert not final.flags.audio_gap
    assert not final.flags.no_speech
    assert final.timings.audio_ms == 1200
    assert final.context_revision == 1
    assert final.utterance_id == "utt-1"


async def test_start_sample_gap_is_zero_filled_flagged_and_acked(serve: Serve):
    client = await serve()
    first = audio_reqs(speech(400))  # seq 0..9, samples [0, 6400)
    # 400 ms never arrive: seq continues, start_sample jumps to 12800.
    second = audio_reqs(speech(400, seed=1), seq=len(first), start_sample=12800)
    total = 12800 + 6400
    out = await client.transcribe(
        [start_req(), *first, *second, 0.7, eoa_req(total, len(first) + len(second) - 1)]
    )
    assert out.code == grpc.StatusCode.OK
    (gap,) = out.of("warning")
    assert gap.code == pb.WARNING_CODE_AUDIO_GAP
    final = out.final
    assert final.flags.audio_gap
    assert final.timings.audio_ms == 1200
    # Windows 2-3 are zero-filled silence: "cube control" was never heard.
    assert final.n_best[0].text == "please run get pods"
    # Status acks report the end sample received, gap included.
    assert out.of("status")[-1].samples_received == total


@pytest.mark.parametrize("after_eager", ["nothing", "more_audio", "context_update"])
async def test_eager_end_result_used_unless_invalidated(serve: Serve, after_eager: str):
    client = await serve(backend=MockBackend(decode_ms=300))
    first = speech(800)  # "please run cube control"
    chunks = audio_reqs(first)
    script: list[pb.TranscribeRequest | float] = [
        start_req(),
        *chunks,
        pb.TranscribeRequest(eager_end=pb.EagerEnd(total_samples=len(first))),
    ]
    script.append(0.6)  # speculative decode (300 ms) completes meanwhile
    total, last_seq = len(first), len(chunks) - 1
    if after_eager == "more_audio":
        more = audio_reqs(speech(400, seed=2), seq=len(chunks), start_sample=len(first))
        script += more
        total, last_seq = total + 6400, last_seq + len(more)
    elif after_eager == "context_update":
        script.append(
            pb.TranscribeRequest(
                context_update=pb.ContextUpdate(
                    context=pb.RecognitionContext(
                        revision=2, bias=pb.BiasList(terms=[kubectl_term()])
                    )
                )
            )
        )
    script.append(eoa_req(total, last_seq))

    out = await client.transcribe(script)
    assert out.code == grpc.StatusCode.OK
    final = out.final
    if after_eager == "nothing":
        assert final.timings.eager_hit
        assert final.timings.end_to_final_ms < 150  # the 300 ms decode was already done
        assert final.n_best[0].text == "please run cube control"
    elif after_eager == "more_audio":
        assert not final.timings.eager_hit
        assert final.timings.end_to_final_ms >= 300
        assert final.n_best[0].text == "please run cube control get pods"
    else:
        assert not final.timings.eager_hit
        assert final.context_revision == 2
        assert final.n_best[0].text == "please run kubectl"


async def test_admission_limit_and_cancel_frees_the_slot(serve: Serve):
    client = await serve(backend=MockBackend(decode_ms=400), max_concurrent_sessions=1)
    pcm = speech(400)
    chunks = audio_reqs(pcm)
    cancelled = asyncio.create_task(
        client.transcribe(
            [start_req(), *chunks, eoa_req(len(pcm), len(chunks) - 1), 0.2,
             pb.TranscribeRequest(cancel=pb.Cancel(reason=pb.CANCEL_REASON_USER_ABORT))]
        )
    )  # fmt: skip
    await asyncio.sleep(0.1)  # first session is admitted and finalizing
    busy = await client.transcribe(utterance(pcm, utterance_id="utt-2"))
    assert busy.code == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert busy.error is not None
    assert busy.error.code == pb.ERROR_CODE_OVERLOADED
    assert busy.error.retryable
    assert busy.error.retry_after_ms > 0

    out = await cancelled  # cancelled during finalize: no result
    assert out.kinds == ["accepted", "session_ended"]
    assert out.code == grpc.StatusCode.CANCELLED

    again = await client.transcribe(utterance(pcm, utterance_id="utt-3"))
    assert again.code == grpc.StatusCode.OK


@pytest.mark.parametrize(
    ("budget_ms", "required", "anchor", "n_best", "exceeded"),
    [
        (1000, [], FS.FIELD_STATUS_PRESENT, FS.FIELD_STATUS_PRESENT, False),
        (150, [], FS.FIELD_STATUS_SKIPPED_BUDGET, FS.FIELD_STATUS_SKIPPED_BUDGET, False),
        (150, ["result.anchor"], FS.FIELD_STATUS_PRESENT, FS.FIELD_STATUS_SKIPPED_BUDGET, True),
        (50, [], FS.FIELD_STATUS_SKIPPED_BUDGET, FS.FIELD_STATUS_SKIPPED_BUDGET, True),
    ],
)
async def test_finalize_budget_sheds_optional_work(
    serve: Serve,
    budget_ms: int,
    required: list[str],
    anchor: pb.FieldStatus.ValueType,
    n_best: pb.FieldStatus.ValueType,
    exceeded: bool,
):
    # Each decode (primary, anchor, n-best) costs 100 ms on the mock.
    client = await serve(backend=MockBackend(decode_ms=100))
    out = await client.transcribe(
        utterance(
            speech(800),
            context=pb.RecognitionContext(bias=pb.BiasList(terms=[kubectl_term()])),
            options=pb.ResultOptions(n_best=3, anchor=pb.ANCHOR_MODE_ALWAYS),
            deadlines=pb.Deadlines(finalize_budget_ms=budget_ms),
            required_features=required,
        )
    )
    assert out.code == grpc.StatusCode.OK
    final = out.final
    assert final.n_best[0].text == "please run kubectl"  # primary is never shed
    assert final.flags.anchor == anchor
    assert final.HasField("anchor") == (anchor == FS.FIELD_STATUS_PRESENT)
    assert final.flags.n_best == n_best
    assert len(final.n_best) == (3 if n_best == FS.FIELD_STATUS_PRESENT else 1)
    assert final.flags.finalize_budget_exceeded == exceeded


async def test_silence_yields_empty_final_with_no_speech(serve: Serve):
    client = await serve()
    out = await client.transcribe(utterance(silence(600)))
    final = out.final
    assert final.n_best[0].text == ""
    assert final.flags.no_speech
