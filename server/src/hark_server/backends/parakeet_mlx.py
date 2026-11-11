"""Parakeet (TDT/RNNT/CTC) on Apple Silicon via parakeet-mlx (>=0.7, <0.8).

API used (read from parakeet_mlx 0.7.0 source):
  from_pretrained(hf_id_or_path, dtype=mx.bfloat16) -> BaseParakeet
  parakeet_mlx.audio.get_logmel(mx.array float32 [-1,1], model.preprocessor_config)
  model.encoder(mel) -> (features, lengths)
  model.decode(features, lengths, config=DecodingConfig()) ->
      TDT/RNNT: (list[list[AlignedToken]], states);  CTC: list[list[AlignedToken]]
  AlignedToken{text (leading " " starts a word), start, duration, end (s),
               confidence (entropy-based, 0..1, uncalibrated)}

Bias: this backend decodes greedily and advertises BIAS_MECHANISM_NONE, so the
anchor is the primary hypothesis (ANCHOR_COST_FREE). parakeet-mlx 0.7 does
offer decode-time word boosting, but only inside beam search
(`Beam(booster=boost.lift(boost.words({...}), model.vocabulary))`); wiring it
up (and pricing the beam + a separate greedy anchor) is a follow-up.
"""

from __future__ import annotations

import time

import numpy as np

from hark.v1 import hark_pb2 as pb

from . import SAMPLE_RATE, Backend, BackendUnavailable, BiasPlan, DecodeOut, WordOut

# Parakeet-TDT-0.6B-v3 model card: 25 European languages, auto-detected.
V3_LANGUAGES = [
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
]  # fmt: skip

DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"


class ParakeetMlxBackend(Backend):
    def __init__(
        self,
        model: str = "",
        model_id: str = "",
        *,
        dtype: str = "bfloat16",
        languages: list[str] | None = None,
    ) -> None:
        try:
            import mlx.core  # noqa: F401
            import parakeet_mlx  # noqa: F401
        except ImportError as e:  # pragma: no cover - platform dependent
            raise BackendUnavailable(
                "parakeet_mlx backend needs macOS on Apple Silicon and "
                "`uv sync --extra mlx` (parakeet-mlx>=0.7)"
            ) from e
        model = model or DEFAULT_MODEL
        super().__init__(model_id or model.rstrip("/").rsplit("/", 1)[-1])
        self.hf_model = model
        self.dtype = dtype
        if languages is None:
            languages = V3_LANGUAGES if "v3" in model else ["en"]
        self.languages = languages
        self._model = None
        self._time_ratio = 0.08

    def capabilities(self) -> pb.ModelCapabilities:
        return pb.ModelCapabilities(
            display_name=f"Parakeet MLX ({self.hf_model})",
            family=self.hf_model.rsplit("/", 1)[-1],
            is_default=True,
            languages=self.languages,
            language_detection=len(self.languages) > 1,
            streaming_mode=pb.STREAMING_MODE_BUFFERED,
            partials=False,
            audio_formats=[
                pb.AudioFormatSupport(
                    encoding=pb.AUDIO_ENCODING_PCM_S16LE, sample_rates_hz=[SAMPLE_RATE]
                )
            ],
            # No decode-time bias: terms are only used for hit detection.
            bias=pb.BiasCapabilities(
                mechanism=pb.BIAS_MECHANISM_NONE,
                midstream_update=pb.MIDSTREAM_UPDATE_FULL,
            ),
            free_text=pb.FreeTextCapabilities(),
            n_best=pb.NBestCapabilities(max_n=1, score_kind=pb.SCORE_KIND_UNSPECIFIED),
            timestamps=pb.TimestampCapabilities(
                source=pb.TIMESTAMP_SOURCE_DECODER_EMISSION,
                resolution_ms=round(self._time_ratio * 1000),
                word_confidence=True,  # entropy-based token confidence, min per word
                confidence_calibrated=False,
            ),
            anchor_cost=pb.ANCHOR_COST_FREE,
            punctuation=True,
            casing=True,
            inverse_text_normalization=False,
        )

    def plan_bias(self, terms: list[pb.BiasTerm], strength: float) -> BiasPlan | None:
        return None  # mechanism NONE: the decoder never sees bias terms

    def _load_sync(self) -> None:
        import mlx.core as mx
        from parakeet_mlx import from_pretrained

        self._model = from_pretrained(self.hf_model, dtype=getattr(mx, self.dtype))
        self._time_ratio = self._model.time_ratio

    def _warmup_sync(self) -> None:
        rng = np.random.default_rng(0)
        self._decode_sync((rng.standard_normal(SAMPLE_RATE) * 300).astype(np.int16), None)
        ten = (rng.standard_normal(10 * SAMPLE_RATE) * 300).astype(np.int16)
        t0 = time.perf_counter()
        self._decode_sync(ten, None)
        ms = (time.perf_counter() - t0) * 1000
        self.perf.finalize_ms_10s = int(ms)
        self.perf.rtfx = 10_000 / ms if ms > 0 else 0.0

    def _decode_sync(self, pcm: np.ndarray, plan: BiasPlan | None) -> DecodeOut:
        import mlx.core as mx
        from parakeet_mlx import DecodingConfig
        from parakeet_mlx.audio import get_logmel

        model = self._model
        if model is None:
            raise RuntimeError("model not loaded")
        if len(pcm) < model.preprocessor_config.hop_length:
            return DecodeOut(text="", words=[])

        t0 = time.perf_counter()
        audio = mx.array(pcm).astype(mx.float32) / 32768.0
        mel = get_logmel(audio, model.preprocessor_config)
        features, lengths = model.encoder(mel)
        mx.eval(features, lengths)
        t1 = time.perf_counter()
        out = model.decode(features, lengths, config=DecodingConfig())
        hyps = out[0] if isinstance(out, tuple) else out
        tokens = hyps[0]
        t2 = time.perf_counter()

        words: list[WordOut] = []
        conf: list[list[float]] = []
        for tok in tokens:
            if not words or tok.text.startswith(" "):
                words.append(WordOut(tok.text.strip(), int(tok.start * 1000), int(tok.end * 1000)))
                conf.append([tok.confidence])
            else:
                w = words[-1]
                w.text += tok.text
                w.end_ms = max(w.end_ms, int(tok.end * 1000))
                conf[-1].append(tok.confidence)
        for w, c in zip(words, conf):
            w.confidence = float(min(c))
        words = [w for w in words if w.text]
        text = "".join(t.text for t in tokens).strip()
        return DecodeOut(
            text=text,
            words=words,
            encoder_ms=int((t1 - t0) * 1000),
            decode_ms=int((t2 - t1) * 1000),
        )
