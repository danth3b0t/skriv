"""NeMo Parakeet-TDT + GPU-PB per-stream phrase boosting (Linux/NVIDIA). STUB.

Capabilities and the weight -> boost mapping are real; model loading and
decoding are not implemented. Construction raises BackendUnavailable on hosts
without NeMo; on hosts with NeMo, load() raises BackendUnavailable ("stub").
The intended call path (NeMo docs, "Per-Stream Phrase
Boosting", checked 2026-11-11; per-stream support = greedy label-looping
RNN-T/TDT only) is:

  load (worker thread that owns the CUDA context):
    from omegaconf import open_dict
    from nemo.collections.asr.models import ASRModel
    m = ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v3").to("cuda").eval()
    with open_dict(m.cfg.decoding):
        m.cfg.decoding.strategy = "greedy_batch"
        m.cfg.decoding.greedy.loop_labels = True
        m.cfg.decoding.greedy.enable_per_stream_biasing = True
        # word confidence (uncalibrated):
        m.cfg.decoding.confidence_cfg.preserve_word_confidence = True
    m.change_decoding_strategy(m.cfg.decoding)
    then warm up: 1 s and 10 s decodes, with and without a 100-term list
    (CUDA graphs), and fill PerfEstimate.

  plan_bias (per context revision; see plan_bias below):
    from nemo.collections.asr.parts.context_biasing.biasing_multi_model import (
        BiasingRequestItemConfig)
    from nemo.collections.asr.parts.context_biasing.boosting_graph_batched import (
        BoostingTreeModelConfig)
    req = BiasingRequestItemConfig(
        boosting_model_cfg=BoostingTreeModelConfig(
            key_phrase_items_list=[{"phrase": p, "alpha": a}, ...],  # per-phrase alpha
            context_score=1.0, depth_scaling=2.0, bpe_mode="case_insensitive"),
        boosting_model_alpha=strength * alpha_model,
        cache_key=f"{model_id}:{bias_list.content_hash}")  # in-RAM compiled-tree cache
    Phrases: canonical always; SPOKEN_FORM/SPELLED aliases only for
    IDENTIFIER/COMMAND/PATH terms; MISRECOGNITION aliases never (hit
    detection only). [U] per-stream acceptance of key_phrase_items_list with
    per-phrase alpha; fallback: <= 3 alpha buckets via key_phrases_list.

  decode at EndOfAudio (biased + anchor in ONE batch of two):
    from nemo.collections.asr.parts.utils.rnnt_utils import Hypothesis
    biased, anchor = m.transcribe(
        audio=[wav, wav],                          # float32 16 kHz numpy
        partial_hypothesis=[Hypothesis.empty_with_biasing_cfg(biasing_cfg=req), None],
        return_hypotheses=True, timestamps=True)
    -> words from hyp.timestamp["word"] (start/end offsets * 80 ms),
       confidence from hyp.word_confidence.
    The batch runs the encoder twice (ANCHOR_COST_FULL_PASS, but in one
    GPU batch). [U] Reusing one encoder pass for both decodes would make it
    DECODER_ONLY. N-best: unbiased malsd_batch beam (origin BEAM).
"""

from __future__ import annotations

import numpy as np

from hark.v1 import hark_pb2 as pb

from . import SAMPLE_RATE, Backend, BackendUnavailable, BiasPlan, DecodeOut

DEFAULT_MODEL = "nvidia/parakeet-tdt-0.6b-v3"
ALPHA_MODEL = 2.0  # NeMo docs example boosting_model_alpha; to be tuned on hark data
_SPOKEN_ALIAS_KINDS = {pb.TERM_KIND_IDENTIFIER, pb.TERM_KIND_COMMAND, pb.TERM_KIND_PATH}


class NemoGpuPbBackend(Backend):
    def __init__(self, model: str = "", model_id: str = "", *, alpha_model: float = ALPHA_MODEL):
        try:
            import nemo.collections.asr  # noqa: F401
        except ImportError as e:
            raise BackendUnavailable(
                "nemo_gpupb needs NVIDIA NeMo (Linux + CUDA GPU): "
                "pip install 'nemo_toolkit[asr]'; it is not installed on this host"
            ) from e
        super().__init__(model_id or (model or DEFAULT_MODEL).rsplit("/", 1)[-1])
        self.alpha_model = alpha_model

    def capabilities(self) -> pb.ModelCapabilities:
        return pb.ModelCapabilities(
            display_name="Parakeet-TDT 0.6B v3 + GPU-PB (NeMo)",
            family="parakeet-tdt-0.6b-v3",
            is_default=True,
            languages=["en"],  # v3 is multilingual; list what hark has evaluated
            streaming_mode=pb.STREAMING_MODE_BUFFERED,
            audio_formats=[
                pb.AudioFormatSupport(
                    encoding=pb.AUDIO_ENCODING_PCM_S16LE, sample_rates_hz=[SAMPLE_RATE]
                )
            ],
            bias=pb.BiasCapabilities(
                mechanism=pb.BIAS_MECHANISM_DECODE_TIME_BOOST,
                max_terms=200,
                max_aliases_per_term=4,
                max_term_bytes=128,
                per_term_weight=True,  # per-phrase alpha [U for per-stream]
                aliases_supported=True,
                case_insensitive=True,  # bpe_mode="case_insensitive"
                midstream_update=pb.MIDSTREAM_UPDATE_FULL,  # buffered: latest revision
                bias_with_n_best=False,  # per-stream boosting is greedy-only
                hit_scores=False,
                # Native scale: effective boost = boosting_model_alpha * phrase alpha,
                # phrase alpha = 0.5 + weight.
                boost_min=0.5 * self.alpha_model,
                boost_max=1.5 * self.alpha_model,
            ),
            free_text=pb.FreeTextCapabilities(),
            n_best=pb.NBestCapabilities(max_n=4, score_kind=pb.SCORE_KIND_LOG_PROB),
            timestamps=pb.TimestampCapabilities(
                source=pb.TIMESTAMP_SOURCE_DECODER_EMISSION,
                resolution_ms=80,
                word_confidence=True,
                confidence_calibrated=False,
            ),
            anchor_cost=pb.ANCHOR_COST_FULL_PASS,
            punctuation=True,
            casing=True,
        )

    def plan_bias(self, terms: list[pb.BiasTerm], strength: float) -> BiasPlan | None:
        if not terms or strength <= 0:
            return None
        boosts, phrases = {}, 0
        for t in terms:
            w = t.weight if t.HasField("weight") else 1.0
            boosts[t.id] = strength * self.alpha_model * (0.5 + w)
            phrases += 1 + sum(
                1
                for a in t.aliases
                if t.kind in _SPOKEN_ALIAS_KINDS and a.kind != pb.ALIAS_KIND_MISRECOGNITION
            )
        return BiasPlan(
            terms=list(terms),
            term_boosts=boosts,
            backend_params={
                "nemo.boosting_model_alpha": f"{strength * self.alpha_model:g}",
                "nemo.phrases": str(phrases),
            },
        )

    def _load_sync(self) -> None:
        raise BackendUnavailable(
            "nemo_gpupb is a stub: load/decode are not implemented yet "
            "(see the call path in hark_server/backends/nemo_gpupb.py)"
        )

    def _warmup_sync(self) -> None:
        raise NotImplementedError("see module docstring")

    def _decode_sync(self, pcm: np.ndarray, plan: BiasPlan | None) -> DecodeOut:
        raise NotImplementedError("see module docstring")
