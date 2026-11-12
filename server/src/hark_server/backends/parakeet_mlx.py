"""Parakeet (TDT/RNNT/CTC) on Apple Silicon via parakeet-mlx (>=0.7, <0.8).

API used (read from parakeet_mlx 0.7.0 source):
  from_pretrained(hf_id_or_path, dtype=mx.bfloat16) -> BaseParakeet
  parakeet_mlx.audio.get_logmel(mx.array float32 [-1,1], model.preprocessor_config)
  model.encoder(mel) -> (features, lengths)
  model.decode(features, lengths, config=DecodingConfig(decoding=Greedy() | Beam(...))) ->
      TDT/RNNT: (list[list[AlignedToken]], states);  CTC (greedy only): list[list[AlignedToken]]
  AlignedToken{text (leading " " starts a word), start, duration, end (s), confidence}
  Beam(beam_size, length_penalty, patience, duration_reward, booster)
  boost.lift(boost.premap(str.lower, boost.words({form: weight})), model.vocabulary)

Decode-time bias (TDT/RNNT models): with a BiasPlan the primary decode is a
beam search whose booster is a character trie over the surface forms below;
without one it is greedy. Mapping of a hark.v1 term (canonical, aliases, w):
  - native boost b = min(boost_max, strength * (boost_min + w * (boost_max - boost_min)))
    (Backend.plan_bias, then clamped; w absent = 1). b is a log-score bonus for completing
    the form at word boundaries; the trie grants it character by character
    and takes it back if the beam leaves the form, so prefixes cost nothing.
    In TDT beam search token log-probs are scaled by (1 - duration_reward) =
    0.3, so b = 1 is worth about 3.3 nats of acoustic evidence per form.
  - every alias is boosted with b ("use session store", "hyper land"), and
    the canonical too if the tokenizer can spell all of its characters
    ("Hyprland", "useSessionStore", "tsconfig.json"; not "parse_args": the v3
    vocabulary has no "_"). Matching is case-insensitive. A form shared by two
    terms gets the larger boost. Terms with b <= 0 are not boosted.
  The client still canonicalizes: hits on aliases are reported via_alias.

Speed: parakeet-mlx tabulates a booster by stepping all 8192 tokens from
every booster state the beam reaches (~20 ms per state, ~1.4 s on a 20 s
clip). `TrieBooster` reimplements `boost.words` with a vectorized table and
is plugged in by wrapping `parakeet_mlx.parakeet.tabulate` (other machines
still use the library's); see the bench doc.
Beam: patience is raised so the search does not stop while hypotheses that
still have to emit the last tokens are alive (with beam_size >= 3 and
patience 1 it drops the final word piece / punctuation on most clips).

Anchor and deletion guard: a biased decode first runs greedy over the same
encoder output, then the boosted beam. If the beam leaves >= 2 consecutive
greedy words without any word over their time span (boosted beams can skip
speech, see the bench doc), the greedy result is returned instead and
backend_ms["beam_discarded"] is set. The greedy result is kept with the
encoder output for as long as the session holds that audio array, so the
session's anchor decode returns it for free (ANCHOR_COST_FREE; its cost is in
the primary's decode_ms).

Nothing outlives a session (the server promises to cache nothing; BiasList
.content_hash, which would allow caching compiled bias, does not reach the
backend): the booster is built when the plan is made, on the worker thread
while audio streams, and dropped with the plan.
CTC models have no beam search here: bias mechanism NONE, anchor == primary.

Long utterances (>= incremental_min_ms, default 30 s) are decoded while they
stream in, a sentence at a time (IncrementalStream); end of audio then decodes
only the last few seconds. See docs/bench/2026-11-12-parakeet-mlx-incremental.md.

English companion: beside a multilingual model (v3: language auto-detected, which
turns a short accented "Hi" into "Hej") the English-only v2 (`english_model`) is
served as a second model on the same worker thread, loaded on first use. The
server routes sessions with languages ["en"] to it and uses it for the language
fallback (hark_server.session). Its 1024-token vocabulary goes through the same
booster code (VocabIndex/TrieBooster are per model). See
docs/bench/2026-11-12-language.md.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import importlib
import itertools
import logging
import re
import threading
import time
import unicodedata
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np
import numpy.typing as npt

from hark.v1 import hark_pb2 as pb

from . import SAMPLE_RATE, Backend, BackendUnavailable, BiasPlan, DecodeOut, WordOut

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future, ThreadPoolExecutor

    import mlx.core as mx
    from parakeet_mlx import AlignedToken, DecodingConfig  # pyright: ignore[reportMissingTypeStubs]
    from parakeet_mlx.audio import PreprocessArgs  # pyright: ignore[reportMissingTypeStubs]
    from parakeet_mlx.boost import Machine  # pyright: ignore[reportMissingTypeStubs]

    from . import Pcm

    type F64 = npt.NDArray[np.float64]
    type State = tuple[frozenset[int], bool]  # (live trie nodes, previous char was a word char)
    type Tabulate = Callable[[Machine[State, int], int], Callable[[State], mx.array]]


class _Model(Protocol):
    """The part of parakeet_mlx.BaseParakeet (0.7) used here. parakeet-mlx ships no
    type information, and `decode` is only defined on its TDT/RNNT/CTC subclasses."""

    vocabulary: list[str]

    @property
    def preprocessor_config(self) -> PreprocessArgs: ...
    @property
    def time_ratio(self) -> float: ...
    def encoder(self, mel: mx.array, /) -> tuple[mx.array, mx.array]: ...
    def decode(
        self, features: mx.array, lengths: mx.array, *, config: DecodingConfig
    ) -> tuple[list[list[AlignedToken]], object] | list[list[AlignedToken]]: ...


# Parakeet-TDT-0.6B-v3 model card: 25 European languages, auto-detected.
V3_LANGUAGES = [
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
]  # fmt: skip

DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
# English-only Parakeet-TDT-0.6B-v2, the parakeet-mlx README's example model.
DEFAULT_ENGLISH_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"
# Defaults chosen in docs/bench/2026-11-11-parakeet-mlx-boost.md.
DEFAULT_BEAM_SIZE = 4
DEFAULT_PATIENCE = 20.0
DEFAULT_BOOST_MIN = 1.0
DEFAULT_BOOST_MAX = 3.0
DEFAULT_INCREMENTAL_MIN_MS = 30_000
_SPECIAL_TOKEN = re.compile(r"<.*>")  # "<unk>", "<|nospeech|>": never part of the text
_STEPS_CACHED = 2**18
_GUARD_WORDS = 2  # see uncovered_run
_TABLES_CACHED = 4096

log = logging.getLogger("hark_server.parakeet_mlx")


def spellable_chars(vocabulary: list[str]) -> frozenset[str]:
    """Lower-cased characters the tokenizer can emit as text."""
    text = "".join(t for t in vocabulary if not _SPECIAL_TOKEN.fullmatch(t))
    return frozenset(text.replace("\u2581", " ").lower())


def boost_forms(
    terms: list[pb.BiasTerm], term_boosts: dict[str, float], chars: frozenset[str]
) -> tuple[dict[str, float], list[str]]:
    """Map terms to booster input: ({lower-cased surface form: boost}, ids of
    terms whose canonical is not boosted because the tokenizer cannot spell it)."""
    forms: dict[str, float] = {}
    unspellable: list[str] = []
    for t in terms:
        b = term_boosts.get(t.id, 0.0)
        if b <= 0:
            continue
        canonical = " ".join(t.canonical.lower().split())
        candidates = [" ".join(a.text.lower().split()) for a in t.aliases]
        if set(canonical) <= chars:
            candidates.append(canonical)
        else:
            unspellable.append(t.id)
        for form in candidates:
            if form and set(form) <= chars:
                forms[form] = max(b, forms.get(form, 0.0))
    return forms, unspellable


def _is_word(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LMN" and unicodedata.east_asian_width(ch) not in "WFH"


class VocabIndex:
    """Token texts as the booster sees them, sorted so that the tokens sharing a
    prefix form one contiguous range. Built once per model."""

    def __init__(self, vocabulary: list[str]) -> None:
        # premap(str.lower) lower-cases character by character
        self.texts = ["".join(c.lower() for c in t.replace("\u2581", " ")) for t in vocabulary]
        by_text: list[int] = sorted(range(len(self.texts)), key=self.texts.__getitem__)
        self.order = np.array(by_text, np.intp)
        self.sorted: list[str] = [self.texts[i] for i in by_text]
        self.lengths = np.array([len(t) for t in self.texts], np.intp)
        longest = int(self.lengths.max(initial=0))
        # word_at[i, t]: char i of token t is a word char (False past the end)
        self.word_at = np.zeros((longest + 1, len(self.texts)), np.bool_)
        for t, text in enumerate(self.texts):
            for i, ch in enumerate(text):
                self.word_at[i, t] = _is_word(ch)

    def subrange(self, lo: int, hi: int, prefix: str) -> tuple[int, int]:
        """Sorted positions [lo', hi') within [lo, hi) of the texts starting with prefix."""
        a = bisect.bisect_left(self.sorted, prefix, lo, hi)
        b = bisect.bisect_left(self.sorted, prefix + "\U0010ffff", a, hi)
        return a, b


class TrieBooster:
    """`boost.lift(boost.premap(str.lower, boost.words(forms)), vocabulary)` of
    parakeet-mlx 0.7 (same trie and node numbering, same step, potential and
    at_end, so beam scores are identical) plus a fast delta table.

    A state's node set evolves as independent tracks: nodes carried in from
    the state, and fresh matches started inside the token at word boundaries.
    The fresh part depends only on (token, after_word) and is precomputed;
    carried nodes die after a character or two, so the table for a state is
    one vector op plus a walk over the vocabulary ranges that keep them alive.
    """

    def __init__(self, forms: dict[str, float], vocab: VocabIndex) -> None:
        from parakeet_mlx.boost import Machine  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        self.vocab = vocab
        self.children: dict[tuple[int, str], int] = {}
        self.node_weights: dict[int, float] = {}
        self.potentials: dict[int, float] = {}
        self.next_chars: dict[int, set[str]] = {}
        for form, weight in forms.items():
            if not form:
                raise ValueError("boost forms cannot be empty")
            node = 0
            for depth, ch in enumerate(form, start=1):
                self.next_chars.setdefault(node, set()).add(ch)
                node = self.children.setdefault((node, ch), len(self.children) + 1)
                if weight > 0:
                    self.potentials[node] = max(
                        self.potentials.get(node, 0.0), weight * depth / len(form)
                    )
            self.node_weights[node] = weight
        self._steps: dict[tuple[State, int], tuple[State, float]] = {}
        self._tables: OrderedDict[State, mx.array] = OrderedDict()
        self.machine: Machine[State, int] = Machine(
            (frozenset(), False), self.step, self.potential, self.at_end
        )
        # Fresh track from (empty, after_word): end potential and weights per token.
        self._fresh = (self._walk_all(after_word=False), self._walk_all(after_word=True))

    # --- boost.Machine interface (boost.words semantics) ---
    def step(self, state: State, token: int) -> tuple[State, float]:
        key = (state, token)
        hit = self._steps.get(key)
        if hit is not None:
            return hit
        total = 0.0
        for ch in self.vocab.texts[token]:
            nodes, after_word = state
            is_word = _is_word(ch)
            at_boundary = not (after_word and is_word)
            if at_boundary:
                total += sum(self.node_weights.get(n, 0.0) for n in nodes)
            heads = nodes | {0} if at_boundary else nodes
            nxt = frozenset(self.children[n, ch] for n in heads if (n, ch) in self.children)
            state = (nxt, is_word)
        if len(self._steps) >= _STEPS_CACHED:
            self._steps.clear()
        self._steps[key] = (state, total)
        return state, total

    def potential(self, state: State) -> float:
        return self._pot(state[0])

    def at_end(self, state: State) -> float:
        return self._weight(state[0])

    def _pot(self, nodes: frozenset[int]) -> float:
        return max((self.potentials.get(n, 0.0) for n in nodes), default=0.0)

    def _weight(self, nodes: frozenset[int]) -> float:
        return sum((self.node_weights.get(n, 0.0) for n in nodes), 0.0)

    def _walk_all(self, *, after_word: bool) -> tuple[F64, F64, F64]:
        pot = np.zeros(len(self.vocab.texts))
        weight = np.zeros(len(self.vocab.texts))
        for t in range(len(self.vocab.texts)):
            end, weight[t] = self.step((frozenset(), after_word), t)
            pot[t] = self.potential(end)
        return pot, weight, pot + weight

    # --- the delta table parakeet-mlx's beam search asks `tabulate` for ---
    def __call__(self, state: State) -> mx.array:
        import mlx.core as mx  # noqa: PLC0415 - platform-specific

        table = self._tables.get(state)
        if table is not None:
            self._tables.move_to_end(state)
            return table
        nodes, after_word = state
        fresh = self._fresh[after_word]
        out = fresh[2].copy()  # no carried nodes: delta == fresh potential + weights
        if nodes:
            self._carry(out, fresh, nodes, prev_word=after_word, pot_s=self._pot(nodes))
        table = mx.array(out.astype(np.float32))
        self._tables[state] = table
        if len(self._tables) > _TABLES_CACHED:
            self._tables.popitem(last=False)
        return table

    def _carry(  # noqa: PLR0913 - recursion state
        self,
        out: F64,
        fresh: tuple[F64, F64, F64],
        carried: frozenset[int],
        *,
        prev_word: bool,
        pot_s: float,
        span: tuple[int, int, str, float] | None = None,
    ) -> None:
        """Deltas of the tokens in the sorted range `span` = (lo, hi, prefix,
        weight earned by carried nodes over the prefix), all of which share the
        prefix that `carried` survived. Default span: the whole vocabulary."""
        v = self.vocab
        lo, hi, prefix, earned = span or (0, len(v.texts), "", 0.0)
        depth = len(prefix)
        ids = v.order[lo:hi]
        w_carried = self._weight(carried)
        # Carried nodes die at the next char; a boundary there pays their completion bonus.
        word_next = cast("npt.NDArray[np.bool_]", v.word_at[min(depth, len(v.word_at) - 1), ids])
        boundary = ~(prev_word & word_next)
        vals: F64 = fresh[2][ids] + earned + w_carried * boundary - pot_s
        ended = cast("npt.NDArray[np.bool_]", v.lengths[ids] == depth)  # carried survive the token
        if bool(ended.any()):
            e = ids[ended]
            vals[ended] = np.maximum(self._pot(carried), fresh[0][e]) + fresh[1][e] + earned - pot_s
        out[ids] = vals
        for ch in {c for n in carried for c in self.next_chars.get(n, ())}:
            a, b = v.subrange(lo, hi, prefix + ch)
            if a == b:
                continue
            nxt = frozenset(self.children[n, ch] for n in carried if (n, ch) in self.children)
            is_word = _is_word(ch)
            paid = earned + (w_carried if not (prev_word and is_word) else 0.0)
            span = (a, b, prefix + ch, paid)
            self._carry(out, fresh, nxt, prev_word=is_word, pot_s=pot_s, span=span)

    def cache_clear(self) -> None:
        """Called by the beam search after every decode; the table outlives it."""


def install_fast_tabulate() -> None:
    """Route parakeet-mlx's beam search to TrieBooster tables (idempotent)."""
    import parakeet_mlx.parakeet as pp  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

    generic = cast("Tabulate", pp.tabulate)
    if generic.__module__ == __name__:
        return

    def tabulate(machine: Machine[State, int], tokens: int) -> Callable[[State], mx.array]:
        owner = getattr(machine.step, "__self__", None)
        if isinstance(owner, TrieBooster) and len(owner.vocab.texts) == tokens:
            return owner
        return generic(machine, tokens)

    # (typed as the library's lru_cache wrapper; beam search only calls it)
    setattr(pp, "tabulate", tabulate)  # noqa: B010


@dataclass
class _Utterance:
    """Encoder output (and greedy tokens, once decoded) of one session's audio. An
    incremental decode leaves only the greedy tokens, for the anchor."""

    pcm: weakref.ref[Pcm]
    features: mx.array | None
    lengths: mx.array | None
    greedy: list[AlignedToken] | None = None


@dataclass(frozen=True)
class Incremental:
    """Windowed decoding while audio streams in (IncrementalStream). Times in ms."""

    min_ms: int = DEFAULT_INCREMENTAL_MIN_MS  # shorter: one offline decode; < 0: never
    stride_ms: int = 6_000  # a window is tried once this much audio follows the cut
    left_ms: int = 16_000  # encoder-only left context (a multiple of 80 ms)
    right_ms: int = 2_000  # a cut needs this much audio after it
    min_segment_ms: int = 1_000  # no cut closer than this to the previous one
    retry_ms: int = 3_000  # no sentence end found: try again with this much more audio
    max_pending_ms: int = 30_000  # stop trying past this much uncut audio (finish decodes it)


@dataclass
class _Segment:
    """Decode of [start, end) of an utterance; token times are utterance-absolute.
    A window that found no sentence end returns an empty segment ending at start."""

    greedy: list[AlignedToken]
    primary: list[AlignedToken]
    end: int  # sample
    encoder_ms: int = 0
    decode_ms: int = 0
    backend_ms: dict[str, int] = field(default_factory=dict[str, int])


@dataclass
class _PlanBooster:
    plan: weakref.ref[BiasPlan]
    forms: dict[str, float]
    build: Future[TrieBooster]


class ParakeetMlxBackend(Backend):
    def __init__(  # noqa: PLR0913 - keyword-only knobs, set from [backend.options]
        self,
        model: str = "",
        model_id: str = "",
        *,
        dtype: str = "bfloat16",
        languages: list[str] | None = None,
        beam_size: int = DEFAULT_BEAM_SIZE,
        patience: float = DEFAULT_PATIENCE,
        boost_min: float = DEFAULT_BOOST_MIN,
        boost_max: float = DEFAULT_BOOST_MAX,
        incremental_min_ms: int = DEFAULT_INCREMENTAL_MIN_MS,
        incremental_stride_ms: int = Incremental.stride_ms,
        english_model: str | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        """english_model: an English-only model served beside a multilingual `model`
        (companions()); None: DEFAULT_ENGLISH_MODEL if `model` speaks several languages,
        "": none. executor: another backend's worker thread (companions share one)."""
        try:  # mlx and parakeet-mlx are imported lazily: macOS on Apple Silicon only
            importlib.import_module("mlx.core")
            importlib.import_module("parakeet_mlx")
        except ImportError as e:  # pragma: no cover - platform dependent
            raise BackendUnavailable(
                "parakeet_mlx backend needs macOS on Apple Silicon and "
                "`uv sync --extra mlx` (parakeet-mlx>=0.7)"
            ) from e
        if beam_size < 1 or patience < 1:
            raise ValueError("need beam_size >= 1 and patience >= 1")
        if not 0 <= boost_min <= boost_max:
            raise ValueError("need 0 <= boost_min <= boost_max")
        if incremental_stride_ms < 1000:
            raise ValueError("need incremental_stride_ms >= 1000")
        model = model or DEFAULT_MODEL
        super().__init__(model_id or model.rstrip("/").rsplit("/", 1)[-1], executor)
        self.hf_model = model
        self.dtype = dtype
        if languages is None:
            languages = V3_LANGUAGES if "v3" in model else ["en"]
        self.languages = languages
        if english_model is None:
            english_model = DEFAULT_ENGLISH_MODEL if len(languages) > 1 else ""
        self.english_model = english_model
        self.beam_size = beam_size
        self.patience = patience
        self.boost_min = boost_min
        self.boost_max = boost_max
        self.incremental = Incremental(min_ms=incremental_min_ms, stride_ms=incremental_stride_ms)
        # CTC checkpoints have greedy decoding only (TDT-CTC hybrids decode with TDT).
        name = self.hf_model.lower()
        self.boosting = "ctc" not in name or "tdt" in name
        self._model: _Model | None = None
        self._time_ratio = 0.08
        self._chars: frozenset[str] = frozenset()
        self._vocab = VocabIndex([])
        # Both live only as long as the session's audio array / bias plan (by
        # identity, through weakrefs): this server caches nothing across calls.
        self._utterances: dict[int, _Utterance] = {}
        self._plan_boosters: dict[int, _PlanBooster] = {}

    def capabilities(self) -> pb.ModelCapabilities:
        if self.boosting:
            bias = pb.BiasCapabilities(
                mechanism=pb.BIAS_MECHANISM_DECODE_TIME_BOOST,
                max_terms=100,
                max_aliases_per_term=8,
                max_term_bytes=128,
                per_term_weight=True,
                aliases_supported=True,
                case_insensitive=True,
                midstream_update=pb.MIDSTREAM_UPDATE_FULL,  # incremental windows are redone
                bias_with_n_best=False,
                hit_scores=False,
                boost_min=self.boost_min,
                boost_max=self.boost_max,
            )
        else:  # terms are only used for hit detection
            bias = pb.BiasCapabilities(
                mechanism=pb.BIAS_MECHANISM_NONE, midstream_update=pb.MIDSTREAM_UPDATE_FULL
            )
        return pb.ModelCapabilities(
            display_name=f"Parakeet MLX ({self.hf_model})",
            family=self.hf_model.rsplit("/", 1)[-1],
            is_default=True,
            languages=self.languages,
            language_detection=len(self.languages) > 1,
            streaming_mode=pb.STREAMING_MODE_INCREMENTAL
            if self.incremental.min_ms >= 0
            else pb.STREAMING_MODE_BUFFERED,
            partials=False,
            audio_formats=[
                pb.AudioFormatSupport(
                    encoding=pb.AUDIO_ENCODING_PCM_S16LE, sample_rates_hz=[SAMPLE_RATE]
                )
            ],
            bias=bias,
            free_text=pb.FreeTextCapabilities(),
            n_best=pb.NBestCapabilities(max_n=1, score_kind=pb.SCORE_KIND_UNSPECIFIED),
            timestamps=pb.TimestampCapabilities(
                source=pb.TIMESTAMP_SOURCE_DECODER_EMISSION,
                resolution_ms=round(self._time_ratio * 1000),
                # greedy: entropy-based, beam: token x duration probability; min per word
                word_confidence=True,
                confidence_calibrated=False,
            ),
            anchor_cost=pb.ANCHOR_COST_FREE,  # biased decodes compute and cache it
            punctuation=True,
            casing=True,
            inverse_text_normalization=False,
        )

    def companions(self) -> list[Backend]:
        """The English-only model, with this model's decoding knobs, on this worker thread."""
        if not self.english_model:
            return []
        english = ParakeetMlxBackend(
            self.english_model, dtype=self.dtype, languages=["en"], beam_size=self.beam_size,
            patience=self.patience, boost_min=self.boost_min, boost_max=self.boost_max,
            incremental_min_ms=self.incremental.min_ms,
            incremental_stride_ms=self.incremental.stride_ms, english_model="",
            executor=self._executor,
        )  # fmt: skip
        return [english]

    def plan_bias(self, terms: list[pb.BiasTerm], strength: float) -> BiasPlan | None:
        if not self.boosting:
            return None  # mechanism NONE: the decoder never sees bias terms
        plan = super().plan_bias(terms, strength)
        if plan is None:
            return None
        # strength > 1 must not leave the advertised range: deletions start above it.
        plan.term_boosts = {k: min(v, self.boost_max) for k, v in plan.term_boosts.items()}
        forms, unspellable = boost_forms(plan.terms, plan.term_boosts, self._chars)
        if forms:  # build on the worker thread while the audio streams in
            key = id(plan)
            self._plan_boosters[key] = _PlanBooster(
                weakref.ref(plan, lambda _: self._plan_boosters.pop(key, None)),
                forms,
                self._executor.submit(TrieBooster, forms, self._vocab),
            )
        plan.backend_params.update(
            decoder=f"tdt_beam{self.beam_size}+greedy" if forms else "greedy",
            boosted_forms=str(len(forms)),
            canonical_not_spellable=str(len(unspellable)),
        )
        return plan

    def estimate_ms(self, audio_ms: int) -> int:
        """The only extra decode is the anchor, cached by the primary decode."""
        return 0

    def _from_pretrained(self) -> _Model:
        import mlx.core as mx  # noqa: PLC0415 - platform-specific, see __init__
        from parakeet_mlx import from_pretrained  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        dtype = cast("mx.Dtype", getattr(mx, self.dtype))  # e.g. "bfloat16" -> mx.bfloat16
        return cast("_Model", from_pretrained(self.hf_model, dtype=dtype))

    def _load_sync(self) -> None:
        model = self._from_pretrained()
        if self.boosting != hasattr(model, "decode_beam"):
            raise BackendUnavailable(f"{self.hf_model}: cannot tell whether it has beam search")
        self._model = model
        self._time_ratio = model.time_ratio
        self._chars = spellable_chars(model.vocabulary)
        self._vocab = VocabIndex(model.vocabulary)
        install_fast_tabulate()

    def _warmup_sync(self) -> None:
        rng = np.random.default_rng(0)
        warm = BiasPlan([pb.BiasTerm(id="w", canonical="warmup")], {"w": self.boost_max})
        self._decode_sync((rng.standard_normal(SAMPLE_RATE) * 300).astype(np.int16), warm)
        ten = (rng.standard_normal(10 * SAMPLE_RATE) * 300).astype(np.int16)
        t0 = time.perf_counter()
        self._decode_sync(ten, None)
        ms = (time.perf_counter() - t0) * 1000
        self.perf.finalize_ms_10s = int(
            ms
        )  # unbiased; noise makes few tokens, so beam would be optimistic
        self.perf.rtfx = 10_000 / ms if ms > 0 else 0.0

    def _booster(self, plan: BiasPlan, forms: dict[str, float]) -> TrieBooster:
        """The booster plan_bias started building; built here if it is not ready
        (this runs on the worker thread, so waiting for a queued build would hang)."""
        entry = self._plan_boosters.get(id(plan))
        if entry and entry.plan() is plan and entry.forms == forms and entry.build.done():
            return entry.build.result()
        return TrieBooster(forms, self._vocab)

    def _utterance(self, model: _Model, pcm: Pcm) -> tuple[_Utterance, int]:
        """Encoder output for `pcm` and the encoder ms spent now (0: reused, e.g.
        for the anchor: the session passes the same array to both decodes)."""
        import mlx.core as mx  # noqa: PLC0415 - platform-specific, see __init__
        from parakeet_mlx.audio import get_logmel  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        key = id(pcm)
        u = self._utterances.get(key)
        if u is not None and u.pcm() is pcm:
            return u, 0
        t0 = time.perf_counter()
        audio = mx.array(pcm).astype(mx.float32) / 32768.0
        mel = get_logmel(audio, model.preprocessor_config)
        features, lengths = model.encoder(mel)
        mx.eval(features, lengths)  # pyright: ignore[reportUnknownMemberType] - mlx stub: untyped *args
        ref = weakref.ref(pcm, lambda _: self._utterances.pop(key, None))
        u = self._utterances[key] = _Utterance(ref, features, lengths)
        return u, int((time.perf_counter() - t0) * 1000)

    def _decode_sync(self, pcm: Pcm, plan: BiasPlan | None) -> DecodeOut:
        model = self._loaded()
        if len(pcm) < model.preprocessor_config.hop_length:
            return DecodeOut(text="", words=[])
        u, encoder_ms = self._utterance(model, pcm)
        t0 = time.perf_counter()
        if plan is None and u.greedy is not None:  # the anchor of a biased decode
            out = _result(u.greedy)
            out.encoder_ms = encoder_ms
            return out
        if u.features is None or u.lengths is None:
            raise RuntimeError("no encoder output for this audio")
        seg = self._decode_segment(model, u.features, u.lengths, plan, greedy=u.greedy)
        u.greedy = seg.greedy
        out = _result(seg.primary)
        out.encoder_ms = encoder_ms
        out.decode_ms = int((time.perf_counter() - t0) * 1000)
        out.backend_ms = seg.backend_ms
        return out

    def _loaded(self) -> _Model:
        if self._model is None:
            raise RuntimeError("model not loaded")
        return self._model

    def _decode_segment(  # noqa: PLR0913 - one decode, all of its inputs
        self,
        model: _Model,
        features: mx.array,
        lengths: mx.array,
        plan: BiasPlan | None,
        *,
        greedy: list[AlignedToken] | None = None,
        shift_s: float = 0.0,
    ) -> _Segment:
        """Greedy and primary decode of `features` (a fresh decoder state).

        Greedy is the anchor and the reference of the deletion guard; primary is
        the boosted beam, or greedy without effective bias or when the guard
        discards the beam. Decoded token times are shifted by `shift_s` (a given
        `greedy` is taken as already shifted); `end` is left 0."""
        from parakeet_mlx import Beam, DecodingConfig  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        backend_ms: dict[str, int] = {}
        t0 = time.perf_counter()
        if greedy is None:
            greedy = _shift(_decode(model, features, lengths, DecodingConfig()), shift_s)
            backend_ms["greedy"] = int((time.perf_counter() - t0) * 1000)
        forms = boost_forms(plan.terms, plan.term_boosts, self._chars)[0] if plan else {}
        if plan is None or not forms:
            return _Segment(greedy, greedy, 0, backend_ms=backend_ms)

        t1 = time.perf_counter()
        booster = self._booster(plan, forms)
        t2 = time.perf_counter()
        beam = Beam(beam_size=self.beam_size, patience=self.patience, booster=booster.machine)
        tokens = _shift(_decode(model, features, lengths, DecodingConfig(decoding=beam)), shift_s)
        t3 = time.perf_counter()
        backend_ms.update(booster_build=int((t2 - t1) * 1000), beam=int((t3 - t2) * 1000))
        spans = [(int(t.start * 1000), int(t.end * 1000)) for t in tokens]
        if uncovered_run(spans, _result(greedy).words) >= _GUARD_WORDS:
            backend_ms["beam_discarded"] = backend_ms["beam"]  # deleted speech: use the anchor
            tokens = greedy
        return _Segment(greedy, tokens, 0, backend_ms=backend_ms)

    # --- incremental decoding (IncrementalStream) ---
    def open_stream(self, plan: BiasPlan | None) -> IncrementalStream | None:
        return IncrementalStream(self, plan) if self.incremental.min_ms >= 0 else None

    def start_window(
        self, pcm: Pcm, offset: int, cut: int, plan: BiasPlan | None
    ) -> Future[_Segment]:
        """window_sync on the worker thread."""
        return self._executor.submit(self.window_sync, pcm, offset, cut, plan)

    def _encode_from(
        self, model: _Model, pcm: Pcm, offset: int, start: int
    ) -> tuple[mx.array, mx.array, int]:
        """Encoder output of `pcm` (utterance samples from `offset`, full attention)
        from sample `start` on (a frame boundary): (features, lengths, ms)."""
        import mlx.core as mx  # noqa: PLC0415 - platform-specific, see __init__
        from parakeet_mlx.audio import get_logmel  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        t0 = time.perf_counter()
        audio = mx.array(pcm).astype(mx.float32) / 32768.0
        features, lengths = model.encoder(get_logmel(audio, model.preprocessor_config))
        frame0 = min((start - offset) // _frame(model), features.shape[1])
        features, lengths = features[:, frame0:], lengths - frame0
        mx.eval(features, lengths)  # pyright: ignore[reportUnknownMemberType] - mlx stub: untyped *args
        return features, lengths, int((time.perf_counter() - t0) * 1000)

    def window_sync(self, pcm: Pcm, offset: int, cut: int, plan: BiasPlan | None) -> _Segment:
        """Decode from `cut` to the next sentence end. `pcm` holds utterance samples
        from `offset` (left context) to the end of the window: a greedy pass over
        all of it finds the sentence ends, the segment up to the widest pause
        after one (at least `right_ms` before the end) is decoded like an
        utterance of its own."""
        from parakeet_mlx import DecodingConfig  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        model = self._loaded()
        inc, frame = self.incremental, _frame(model)
        features, lengths, encoder_ms = self._encode_from(model, pcm, offset, cut)
        t0 = time.perf_counter()
        shift = cut / SAMPLE_RATE
        ahead = _shift(_decode(model, features, lengths, DecodingConfig()), shift)
        end = offset + len(pcm)
        lo, hi = (
            cut + inc.min_segment_ms * SAMPLE_RATE // 1000,
            end - inc.right_ms * SAMPLE_RATE // 1000,
        )
        at = sentence_cut(ahead, lo / SAMPLE_RATE, hi / SAMPLE_RATE)
        if at is None:
            return _Segment([], [], cut, encoder_ms, int((time.perf_counter() - t0) * 1000))
        frames = max(1, round((at * SAMPLE_RATE - cut) / frame))
        new_cut = cut + frames * frame
        seg = self._decode_segment(
            model, features[:, :frames], _mx_min(lengths, frames), plan, shift_s=shift,
            greedy=[t for t in ahead if t.start < new_cut / SAMPLE_RATE],
        )  # fmt: skip
        seg.end = new_cut
        seg.encoder_ms, seg.decode_ms = encoder_ms, int((time.perf_counter() - t0) * 1000)
        return seg

    def tail_sync(self, pcm: Pcm, done: _Committed, plan: BiasPlan | None) -> DecodeOut:
        """Decode the audio after the committed cut and append it to the
        committed tokens."""
        model = self._loaded()
        seg = _Segment([], [], len(pcm))
        if len(pcm) - done.cut >= model.preprocessor_config.hop_length:
            lo = max(0, done.cut - self.incremental.left_ms * SAMPLE_RATE // 1000)
            features, lengths, encoder_ms = self._encode_from(model, pcm[lo:], lo, done.cut)
            t0 = time.perf_counter()
            seg = self._decode_segment(
                model, features, lengths, plan, shift_s=done.cut / SAMPLE_RATE
            )
            seg.encoder_ms, seg.decode_ms = encoder_ms, int((time.perf_counter() - t0) * 1000)
        greedy, primary = done.greedy + seg.greedy, done.primary + seg.primary
        key = id(pcm)  # the session's anchor decode of the same array returns greedy
        ref = weakref.ref(pcm, lambda _: self._utterances.pop(key, None))
        self._utterances[key] = _Utterance(ref, None, None, greedy)
        out = _result(primary)
        out.encoder_ms, out.decode_ms = seg.encoder_ms, seg.decode_ms
        out.backend_ms = {
            **seg.backend_ms,
            "windows": done.windows,
            "windows_discarded": done.discarded,
            "tail_audio": (len(pcm) - done.cut) * 1000 // SAMPLE_RATE,
        }
        return out


def _frame(model: _Model) -> int:
    """Samples per encoder frame (1280 for parakeet: 10 ms hop x 8)."""
    return round(model.time_ratio * SAMPLE_RATE)


def _mx_min(lengths: mx.array, n: int) -> mx.array:
    import mlx.core as mx  # noqa: PLC0415 - platform-specific

    return mx.minimum(lengths, n)


def _decode(
    model: _Model, features: mx.array, lengths: mx.array, config: DecodingConfig
) -> list[AlignedToken]:
    out = model.decode(features, lengths, config=config)
    hyps = out[0] if isinstance(out, tuple) else out
    return hyps[0]


def _shift(tokens: list[AlignedToken], s: float) -> list[AlignedToken]:
    return [replace(t, start=t.start + s) for t in tokens] if s else tokens


def _result(tokens: list[AlignedToken]) -> DecodeOut:
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
    for w, c in zip(words, conf, strict=True):
        w.confidence = float(min(c))
    words = [w for w in words if w.text]
    return DecodeOut(text="".join(t.text for t in tokens).strip(), words=words)


def uncovered_run(primary: list[tuple[int, int]], anchor: list[WordOut], slack_ms: int = 80) -> int:
    """Longest run of consecutive anchor words that no primary token span
    (start_ms, end_ms) overlaps (+- slack). Boosting merges and respells words
    but keeps their time; a run of uncovered words means the beam skipped
    speech. Token spans, not word spans: a skipping hypothesis can keep a word
    open across the skipped audio ("parse arg" ... "es")."""
    longest = run = 0
    for a in anchor:
        covered = any(s < a.end_ms + slack_ms and e > a.start_ms - slack_ms for s, e in primary)
        run = 0 if covered else run + 1
        longest = max(longest, run)
    return longest


def sentence_cut(tokens: list[AlignedToken], lo_s: float, hi_s: float) -> float | None:
    """A cut point in [lo_s, hi_s] between a sentence-final token and the next
    word: halfway between their starts (TDT token durations run up to the next
    token, so starts are the only reliable word timing), at the widest such
    pause, the later on ties; None if no sentence ends there."""
    best, width = None, -1.0
    for t, nxt in itertools.pairwise(tokens):
        if t.text.rstrip()[-1:] in {".", "?", "!"} and nxt.text.startswith(" "):
            at = (t.start + nxt.start) / 2
            if lo_s <= at <= hi_s and nxt.start - t.start >= width:
                best, width = at, nxt.start - t.start
    return best


@dataclass(frozen=True)
class _Committed:
    """Decoded audio before sample `cut`, by `windows` windows."""

    cut: int = 0
    greedy: list[AlignedToken] = field(default_factory=list["AlignedToken"])
    primary: list[AlignedToken] = field(default_factory=list["AlignedToken"])
    windows: int = 0
    discarded: int = 0


class IncrementalStream:
    """Decodes a session's audio in sentence-sized segments while it streams in,
    so that end of audio only has to decode the tail.

    Segments end at sentence ends and each is decoded like an utterance of its
    own (fresh decoder state, greedy + boosted beam + deletion guard), with
    `left_ms` of earlier audio as encoder-only context and full attention
    (parakeet-mlx's own streaming mode uses local attention and loses 15-30
    WER points). Cutting anywhere else is unsafe: a TDT decoder that starts,
    or continues with a carried state, in the middle of a sentence often emits
    nothing until the next sentence starts, dropping whole clauses.

    A window covers [cut - left, cut + stride + right] at first: a greedy pass
    from the cut finds the sentence ends, and the segment up to the widest
    pause after one is decoded. Without a sentence end the window is retried
    with `retry_ms` more audio (up to `max_pending_ms` of uncut audio). Windows
    run on the worker thread, one at a time, in line with other sessions'
    decodes, and only once the audio reaches `min_ms`; shorter utterances are
    decoded offline at the end.

    Thread-safety: feed/set_plan/finish/close run on the event loop, window
    completions on the worker thread; `_lock` guards the state.
    """

    def __init__(self, backend: ParakeetMlxBackend, plan: BiasPlan | None) -> None:
        self.backend = backend
        self.cfg = backend.incremental
        self._lock = threading.RLock()  # a done-callback may run inside _pump
        self._audio = bytearray()
        self._plan = plan
        self._generation = 0
        self._done = _Committed()
        self._need = self._samples(self.cfg.stride_ms + self.cfg.right_ms)  # next window's end
        self._running: Future[_Segment] | None = None
        self._finishing = False
        self._failed = False
        self._closed = False

    def _samples(self, ms: int) -> int:
        return ms * SAMPLE_RATE // 1000

    def feed(self, data: bytes) -> None:
        with self._lock:
            self._audio += data
            self._finishing = False
            self._pump()

    def set_plan(self, plan: BiasPlan | None) -> None:
        """New context: the boosted segments are stale, start over."""
        with self._lock:
            self._plan = plan
            self._generation += 1
            self._done = _Committed()
            self._need = self._samples(self.cfg.stride_ms + self.cfg.right_ms)
            self._pump()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._running is not None:
                self._running.cancel()

    def _pump(self) -> None:
        c, n, cut = self.cfg, len(self._audio) // 2, self._done.cut
        if self._closed or self._failed or self._finishing or self._running is not None:
            return
        if n < max(self._need, self._samples(c.min_ms)):
            return
        if self._need - cut > self._samples(c.max_pending_ms):
            return  # no sentence end in sight: finish decodes the rest
        lo = max(0, cut - self._samples(c.left_ms))
        end = self._need
        pcm = np.frombuffer(bytes(self._audio[2 * lo : 2 * end]), dtype="<i2")
        fut = self.backend.start_window(pcm, lo, cut, self._plan)
        self._running = fut
        generation = self._generation
        fut.add_done_callback(lambda f: self._on_window(f, generation, end))

    def _on_window(self, fut: Future[_Segment], generation: int, end: int) -> None:
        with self._lock:
            if self._running is fut:
                self._running = None
            if fut.cancelled() or self._closed:
                return
            if generation == self._generation:
                if (exc := fut.exception()) is not None:
                    log.warning("incremental window failed (%r); decoding at the end", exc)
                    self._failed = True
                    return
                seg, d = fut.result(), self._done
                if seg.end > d.cut:
                    self._done = _Committed(
                        seg.end, d.greedy + seg.greedy, d.primary + seg.primary, d.windows + 1,
                        d.discarded + ("beam_discarded" in seg.backend_ms),
                    )  # fmt: skip
                    self._need = seg.end + self._samples(self.cfg.stride_ms + self.cfg.right_ms)
                else:
                    self._need = end + self._samples(self.cfg.retry_ms)
            self._pump()

    async def finish(self, pcm: Pcm, plan: BiasPlan | None) -> DecodeOut:
        """The primary decode of `pcm` (all audio fed so far): the committed
        segments plus a decode of the tail, or an offline decode if no window
        applies (short audio, other audio or plan, failed window)."""
        with self._lock:
            usable = (
                not self._failed
                and plan is self._plan
                and len(pcm) == len(self._audio) // 2
                and len(pcm) >= self._samples(self.cfg.min_ms)
            )
            self._finishing = usable  # no new windows: the tail is cheaper
            running = self._running
        if not usable:
            return await self.backend.decode(pcm, plan)
        if running is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wrap_future(running)
        with self._lock:
            failed, done = self._failed, self._done  # _Committed is replaced, never mutated
        if failed:
            return await self.backend.decode(pcm, plan)
        return await self.backend.run(self.backend.tail_sync, pcm, done, plan)
