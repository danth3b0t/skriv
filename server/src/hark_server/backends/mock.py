"""Deterministic mock backend for tests and protocol work.

"Recognition": the audio is cut into windows of `word_ms`; every window whose
RMS is above `silence_rms` emits the next word of `script` (window i emits
script[i % len(script)]). Silence and zero-filled gaps therefore emit nothing,
and more audio yields more words. A trailing partial window counts if it is at
least half a window long.

Bias: with a BiasPlan, every occurrence of a term's alias (case-insensitive,
whole words) is replaced by the term's canonical form; the unbiased decode
(plan=None) keeps the alias. Latency is simulated by sleeping on the worker
thread: decode_ms + decode_ms_per_s * audio seconds per decode.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np

from hark.v1 import hark_pb2 as pb

from . import SAMPLE_RATE, Backend, BiasPlan, DecodeOut, WordOut

if TYPE_CHECKING:
    from . import Pcm

DEFAULT_SCRIPT = (  # noqa: SIM905 - reads as the sentence the mock "speaks"
    "please run cube control get pods in the default name space "
    "and then show me the logs of the failing one"
).split()


class MockBackend(Backend):
    def __init__(  # noqa: PLR0913 - keyword-only knobs, set from [backend.options]
        self,
        model_id: str = "mock",
        *,
        script: list[str] | None = None,
        word_ms: int = 200,
        silence_rms: float = 50.0,
        decode_ms: int = 0,
        decode_ms_per_s: float = 0.0,
        load_ms: int = 0,
        max_n_best: int = 3,
        max_terms: int = 100,
        max_aliases_per_term: int = 8,
        max_term_bytes: int = 128,
        languages: list[str] | None = None,
    ) -> None:
        super().__init__(model_id)
        self.languages = languages or ["en"]  # more than one: "detects" the language
        self.script = list(script or DEFAULT_SCRIPT)
        self.word_ms = word_ms
        self.silence_rms = silence_rms
        self.decode_ms = decode_ms
        self.decode_ms_per_s = decode_ms_per_s
        self.load_ms = load_ms
        self.max_n_best = max_n_best
        self.max_terms = max_terms
        self.max_aliases_per_term = max_aliases_per_term
        self.max_term_bytes = max_term_bytes

    def capabilities(self) -> pb.ModelCapabilities:
        return pb.ModelCapabilities(
            display_name="Mock (deterministic)",
            family="mock",
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
            bias=pb.BiasCapabilities(
                mechanism=pb.BIAS_MECHANISM_DECODE_TIME_BOOST,
                max_terms=self.max_terms,
                max_aliases_per_term=self.max_aliases_per_term,
                max_term_bytes=self.max_term_bytes,
                per_term_weight=True,
                aliases_supported=True,
                case_insensitive=True,
                midstream_update=pb.MIDSTREAM_UPDATE_FULL,
                bias_with_n_best=False,
                boost_min=1.0,
                boost_max=5.0,
            ),
            free_text=pb.FreeTextCapabilities(),
            n_best=pb.NBestCapabilities(max_n=self.max_n_best, score_kind=pb.SCORE_KIND_RANK_ONLY),
            timestamps=pb.TimestampCapabilities(
                source=pb.TIMESTAMP_SOURCE_DECODER_EMISSION,
                resolution_ms=self.word_ms,
                word_confidence=True,
                confidence_calibrated=False,
            ),
            anchor_cost=pb.ANCHOR_COST_DECODER_ONLY,
        )

    def _cost_ms(self, audio_ms: int) -> float:
        return self.decode_ms + self.decode_ms_per_s * audio_ms / 1000

    def estimate_ms(self, audio_ms: int) -> int:
        return int(self._cost_ms(audio_ms))

    def _load_sync(self) -> None:
        time.sleep(self.load_ms / 1000)

    def _warmup_sync(self) -> None:
        self.perf.finalize_ms_10s = int(self._cost_ms(10_000))
        cost_10s = self._cost_ms(10_000)
        self.perf.rtfx = 10_000 / cost_10s if cost_10s > 0 else 0.0

    def _recognize(self, pcm: Pcm) -> list[WordOut]:
        win = SAMPLE_RATE * self.word_ms // 1000
        n_full, rest = divmod(len(pcm), win)
        n = n_full + (1 if rest * 2 >= win else 0)
        words: list[WordOut] = []
        for i in range(n):
            window = pcm[i * win : (i + 1) * win].astype(np.float64)
            if np.sqrt(np.mean(window**2)) < self.silence_rms:
                continue
            start = i * self.word_ms
            words.append(
                WordOut(
                    self.script[i % len(self.script)], start, start + self.word_ms * 3 // 4, 0.9
                )
            )
        return words

    @staticmethod
    def _apply_bias(words: list[WordOut], plan: BiasPlan) -> list[WordOut]:
        forms: list[tuple[list[str], str]] = []  # (alias tokens, canonical), longest first
        for term in plan.terms:
            for alias in term.aliases:
                tokens = alias.text.lower().split()
                if tokens:
                    forms.append((tokens, term.canonical))
        forms.sort(key=lambda f: -len(f[0]))
        out: list[WordOut] = []
        i = 0
        while i < len(words):
            for tokens, canonical in forms:
                span = words[i : i + len(tokens)]
                if [w.text.lower() for w in span] == tokens:
                    out.append(WordOut(canonical, span[0].start_ms, span[-1].end_ms, 0.8))
                    i += len(tokens)
                    break
            else:
                out.append(words[i])
                i += 1
        return out

    def _decode_sync(self, pcm: Pcm, plan: BiasPlan | None) -> DecodeOut:
        t0 = time.perf_counter()
        audio_ms = len(pcm) * 1000 // SAMPLE_RATE
        time.sleep(self._cost_ms(audio_ms) / 1000)
        words = self._recognize(pcm)
        if plan is not None:
            words = self._apply_bias(words, plan)
        ms = int((time.perf_counter() - t0) * 1000)
        return DecodeOut(text=" ".join(w.text for w in words), words=words, decode_ms=ms)

    def _n_best_sync(self, pcm: Pcm, plan: BiasPlan | None, n: int) -> list[DecodeOut]:
        # Unbiased "beam" alternatives (bias_with_n_best=false): base text + marker.
        base = self._decode_sync(pcm, None)
        out: list[DecodeOut] = []
        for k in range(1, n):
            words = [*base.words, WordOut(f"alt{k}", 0, 0, None)]
            out.append(DecodeOut(text=" ".join(w.text for w in words), words=words))
        return out
