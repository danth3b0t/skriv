"""parakeet_mlx backend: decode-time bias, anchor reuse and the deletion guard.

The fast tests replace the model with a fake that has the real mel front end
and returns scripted tokens, so they need mlx + parakeet-mlx but no weights.
`HARK_SLOW=1` adds one end-to-end test with the real model on a `say` clip.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import string
import subprocess
import wave
from collections import deque
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from conftest import kubectl_term, speech, utterance

from hark.v1 import hark_pb2 as pb

pytest.importorskip("parakeet_mlx")  # macOS on Apple Silicon, `uv sync --extra mlx`

import mlx.core as mx
from parakeet_mlx import AlignedToken, Beam, DecodingConfig, boost  # pyright: ignore[reportMissingTypeStubs]
from parakeet_mlx import parakeet as pp  # pyright: ignore[reportMissingTypeStubs]
from parakeet_mlx.audio import PreprocessArgs  # pyright: ignore[reportMissingTypeStubs]

from hark_server.backends.parakeet_mlx import (
    ParakeetMlxBackend,
    TrieBooster,
    VocabIndex,
    boost_forms,
    install_fast_tabulate,
    spellable_chars,
)

if TYPE_CHECKING:
    from pathlib import Path

    from conftest import Serve
    from parakeet_mlx.boost import Machine  # pyright: ignore[reportMissingTypeStubs]

    from hark_server.backends.parakeet_mlx import State

type Script = list[tuple[str, float]]  # (token text, start s); every token lasts 0.2 s

GREEDY: Script = [
    ("▁please", 0.0), ("▁run", 0.3), ("▁cube", 0.6), ("▁control", 0.9), ("▁get", 1.2), ("▁pods", 1.5),
]  # fmt: skip
BOOSTED: Script = [("▁please", 0.0), ("▁run", 0.3), ("▁kube", 0.6), ("ctl", 0.9), ("▁get", 1.2), ("▁pods", 1.5)]  # fmt: skip
SKIPPING: Script = [("▁please", 0.0), ("▁run", 0.3), ("▁pods", 1.5)]  # beam jumped over 0.6-1.4 s


class FakeModel:
    """ParakeetTDT stand-in: real preprocessor config, scripted decodes."""

    time_ratio = 0.08

    def __init__(self, beam_script: Script) -> None:
        # every ASCII letter, but no "_" (like parakeet-tdt-0.6b-v3)
        self.vocabulary = sorted(
            {t for t, _ in GREEDY + BOOSTED} | {"▁", ".", *string.ascii_letters}
        )
        self.beam_script = beam_script
        self.encoder_calls = 0
        self.configs: list[DecodingConfig] = []
        self._pre = PreprocessArgs(16000, "per_feature", 0.025, 0.01, "hann", 128, 512, 1e-5)

    @property
    def preprocessor_config(self) -> PreprocessArgs:
        return self._pre

    def encoder(self, mel: mx.array, /) -> tuple[mx.array, mx.array]:
        self.encoder_calls += 1
        frames = mel.shape[1] // 8
        return mx.zeros((1, frames, 4)), mx.array([frames])

    def decode(
        self, features: mx.array, lengths: mx.array, *, config: DecodingConfig
    ) -> tuple[list[list[AlignedToken]], object]:
        self.configs.append(config)
        script = self.beam_script if isinstance(config.decoding, Beam) else GREEDY
        ids = {t: i for i, t in enumerate(self.vocabulary)}
        tokens = [AlignedToken(ids[t], t.replace("▁", " "), s, 0.2, 0.9) for t, s in script]
        return [tokens], [None]

    def decode_beam(self) -> None: ...  # TDT/RNNT marker checked by _load_sync


class FakeParakeet(ParakeetMlxBackend):
    def __init__(self, model: FakeModel) -> None:
        super().__init__()
        self.fake = model

    def _from_pretrained(self) -> FakeModel:
        return self.fake

    def held(self) -> int:
        """Per-session state still referenced by the backend."""
        return len(self._utterances) + len(self._plan_boosters)


def bias_ctx(*terms: pb.BiasTerm, strength: float | None = None) -> pb.RecognitionContext:
    return pb.RecognitionContext(revision=1, bias=pb.BiasList(terms=terms, strength=strength))


async def test_biased_decode_is_boosted_beam_with_reused_greedy_anchor(serve: Serve):
    model = FakeModel(BOOSTED)
    backend = FakeParakeet(model)
    client = await serve(backend=backend)
    model.encoder_calls = 0
    model.configs.clear()
    out = await client.transcribe(
        utterance(
            speech(2000),
            context=bias_ctx(kubectl_term(weight=0.5)),
            options=pb.ResultOptions(word_timestamps=True),
        )
    )
    applied = out.of("accepted")[0].applied
    assert applied.bias_mechanism == pb.BIAS_MECHANISM_DECODE_TIME_BOOST
    assert applied.term_boosts["t-kubectl"] == pytest.approx(2.0)  # 1 + 0.5 * (3 - 1)
    assert applied.backend_params["decoder"] == "tdt_beam4+greedy"
    assert applied.backend_params["boosted_forms"] == "2"  # "kubectl" and "cube control"

    final = out.final
    assert final.n_best[0].text == "please run kubectl get pods"
    assert final.anchor.text == "please run cube control get pods"
    (hit,) = final.bias_hits
    assert (hit.decision, hit.matched_form, hit.anchor_text) == (
        pb.BIAS_HIT_DECISION_APPLIED,
        "kubectl",
        "cube control",
    )
    assert (hit.start_ms, hit.end_ms) == (600, 1100)
    assert "beam_discarded" not in final.timings.backend_ms

    # One encoder pass and one greedy decode serve primary and anchor.
    assert model.encoder_calls == 1
    assert [type(c.decoding).__name__ for c in model.configs] == ["Greedy", "Beam"]
    beam = model.configs[1].decoding
    assert isinstance(beam, Beam)
    assert (beam.beam_size, beam.patience) == (4, 20.0)
    owner: object = getattr(cast("Machine[State, int]", beam.booster).step, "__self__", None)
    assert isinstance(owner, TrieBooster)
    assert backend.held() == 0  # audio features, greedy text and booster died with the session


async def test_beam_that_skips_speech_is_replaced_by_greedy(serve: Serve):
    model = FakeModel(SKIPPING)
    client = await serve(backend=FakeParakeet(model))
    final = (
        await client.transcribe(utterance(speech(2000), context=bias_ctx(kubectl_term())))
    ).final
    assert final.n_best[0].text == final.anchor.text == "please run cube control get pods"
    assert "beam_discarded" in final.timings.backend_ms
    assert [h.decision for h in final.bias_hits] == [pb.BIAS_HIT_DECISION_ALREADY_IN_ANCHOR]


@pytest.mark.parametrize("ctx", [pb.RecognitionContext(), bias_ctx(kubectl_term(), strength=0.0)])
async def test_without_effective_bias_only_greedy_runs(serve: Serve, ctx: pb.RecognitionContext):
    model = FakeModel(BOOSTED)
    client = await serve(backend=FakeParakeet(model))
    model.configs.clear()
    out = await client.transcribe(utterance(speech(2000), context=ctx))
    assert out.of("accepted")[0].applied.bias_mechanism == pb.BIAS_MECHANISM_NONE
    assert out.final.n_best[0].text == "please run cube control get pods"
    assert [type(c.decoding).__name__ for c in model.configs] == ["Greedy"]


def test_weights_map_to_clamped_boosts_and_spellable_forms():
    backend = FakeParakeet(FakeModel(BOOSTED))
    backend._load_sync()  # pyright: ignore[reportPrivateUsage]
    terms = [
        pb.BiasTerm(id="a", canonical="parse_args", aliases=[pb.SpokenAlias(text="Parse  Args")], weight=0.0),
        pb.BiasTerm(id="b", canonical="Hyprland", aliases=[pb.SpokenAlias(text="hyper land")], weight=1.0),
        pb.BiasTerm(id="c", canonical="hyper land"),  # same form as b's alias
    ]  # fmt: skip
    plan = backend.plan_bias(terms, strength=2.0)
    assert plan is not None
    # [0, 1] -> [1, 3], times strength, clamped to boost_max
    assert plan.term_boosts == {"a": 2.0, "b": 3.0, "c": 3.0}
    assert plan.backend_params["canonical_not_spellable"] == "1"

    chars = spellable_chars(["<unk>", "▁Par", "se", "▁", "A", "rgs", "H", "y", "p", "r", "l", "a", "n", "d", "e"])  # fmt: skip
    assert "_" not in chars
    assert "<" not in chars  # special tokens never surface as text
    forms, unspellable = boost_forms(terms, {"a": 1.0, "b": 2.0, "c": 2.5}, chars)
    assert forms == {"parse args": 1.0, "hyprland": 2.0, "hyper land": 2.5}
    assert unspellable == ["a"]
    assert boost_forms(terms, {"a": 0.0, "b": 0.0, "c": 0.0}, chars) == ({}, [])


def test_trie_booster_matches_parakeet_mlx_words():
    vocab = [
        "<unk>", "<|nospeech|>", "▁", "▁Hy", "per", "▁hyper", "▁land", "land", "pr", "▁Hyprland",
        "▁cube", "▁control", "▁c", "ontrol", "▁kube", "ctl", ".", ",", "-", "▁pre", "-commit", "s",
        "▁the", "▁a", "1", "▁ü", "BER",
    ]  # fmt: skip
    forms = {"hyprland": 2.0, "hyper land": 1.5, "kubectl": 3.0, "cube control": 2.5, "pre-commit": 1.0, "a": 0.5}  # fmt: skip
    ours = TrieBooster(forms, VocabIndex(vocab))
    reference = boost.lift(boost.premap(str.lower, boost.words(forms)), vocab)
    table = boost.tabulate(reference, len(vocab))

    # Every state reachable within three tokens, compared on every token.
    start: State = (frozenset(), False)
    seen: set[State] = {start, (frozenset(), True)}
    frontier: deque[tuple[State, int]] = deque((s, 0) for s in seen)
    while frontier:
        state, depth = frontier.popleft()
        assert np.array_equal(np.array(ours(state), np.float32), np.array(table(state), np.float32))
        assert ours.potential(state) == reference.potential(state)
        assert ours.at_end(state) == reference.at_end(state)
        for t in range(len(vocab)):
            nxt = ours.step(state, t)
            assert nxt == reference.step(state, t)
            if depth < 3 and nxt[0] not in seen:
                seen.add(nxt[0])
                frontier.append((nxt[0], depth + 1))
    assert len(seen) > 15  # the walk reached into the multi-word forms

    install_fast_tabulate()
    install_fast_tabulate()  # idempotent: still wraps the library's function once
    assert pp.tabulate(ours.machine, len(vocab)) is ours
    generic = np.array(pp.tabulate(reference, len(vocab))(start), np.float32)
    assert np.array_equal(generic, np.array(table(start), np.float32))


@pytest.mark.skipif(os.environ.get("HARK_SLOW") != "1", reason="set HARK_SLOW=1 (real model)")
@pytest.mark.skipif(shutil.which("say") is None, reason="needs macOS `say`")
async def test_slow_real_model_boosts_kubectl(serve: Serve, tmp_path: Path):
    aiff, wav = tmp_path / "k.aiff", tmp_path / "k.wav"
    text = "Please run kubectl get pods in the default namespace."
    for cmd in (
        ["say", "-v", "Samantha", "-o", str(aiff), text],
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
    ):
        await asyncio.to_thread(subprocess.run, cmd, check=True)
    with wave.open(str(wav), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")

    client = await serve(backend=ParakeetMlxBackend())
    final = (
        await client.transcribe(utterance(pcm, context=bias_ctx(kubectl_term(weight=0.8))))
    ).final
    assert "kubectl" in final.n_best[0].text
    assert "kubectl" not in final.anchor.text.lower()
    (hit,) = final.bias_hits
    assert hit.decision == pb.BIAS_HIT_DECISION_APPLIED
