"""Anchor gate: span alignment and keep/revert decisions on hand-built hypotheses."""

from __future__ import annotations

import pytest

from hark.v1 import hark_pb2 as pb
from hark_client import gate
from hark_client.config import GateConfig

TERMS = [
    pb.BiasTerm(id=i, canonical=c, aliases=[pb.SpokenAlias(text=a) for a in aliases])
    for i, c, aliases in (
        ("kubectl", "kubectl", ["cube control"]),
        ("hark", "hark", ["hark"]),
        ("parse_config", "parse_config", ["parse config"]),
        ("parse_args", "parse_args", ["parse args"]),
        ("is_terminal", "is_terminal", ["is terminal"]),
        ("ruff", "ruff", ["ruff"]),
        ("Notion", "Notion", ["notion"]),
        ("Tauri", "Tauri", ["tauri"]),
    )
]


def hyp(spec: str) -> pb.Hypothesis:
    """Words separated by spaces; "word@0.95" sets a confidence (default 0.8)."""
    h = pb.Hypothesis()
    for k, tok in enumerate(spec.split()):
        text, _, conf = tok.partition("@")
        h.words.add(
            text=text, start_ms=300 * k, end_ms=300 * k + 250, confidence=float(conf or 0.8)
        )
    h.text = " ".join(w.text for w in h.words)
    return h


def hit(biased: pb.Hypothesis, start: int, end: int, term_id: str) -> pb.BiasHit:
    """BiasHit over biased words [start, end), byte span as the server reports it."""
    before = " ".join(w.text for w in biased.words[:start])
    first = len(before.encode()) + (1 if before else 0)
    span = " ".join(w.text for w in biased.words[start:end])
    return pb.BiasHit(
        term_id=term_id,
        matched_form=span,
        decision=pb.BIAS_HIT_DECISION_APPLIED,
        text_byte_start=first,
        text_byte_end=first + len(span.encode()),
    )


# (anchor, biased, hits [(start, end, term)], gated text, [(decision, reason, biased, anchor)])
type Case = tuple[str, str, list[tuple[int, int, str]], str, list[tuple[str, str, str, str]]]
CASES: dict[str, Case] = {
    "close spelling is kept": (
        "please run cubectal get pods", "please run kubectl get pods", [(2, 3, "kubectl")],
        "please run kubectl get pods", [("kept", "close", "kubectl", "cubectal")],
    ),
    "a re-split of the anchor's letters is kept": (
        "switched to Harcon today", "switched to Hark on today", [(2, 3, "hark")],
        "switched to Hark on today", [("kept", "close", "Hark on", "Harcon")],
    ),
    "same letters: the biased spelling, however sure the anchor": (
        "run Kube@1.0 Ctl@1.0 now", "run kubectl now", [(1, 2, "kubectl")],
        "run kubectl now", [("kept", "same_letters", "kubectl", "Kube Ctl")],
    ),
    "the anchor dropped a word outside the term": (
        "rename this terminal flag", "rename the is terminal flag", [(2, 4, "is_terminal")],
        "rename the is terminal flag",
        [("kept", "anchor_dropped", "the is terminal", "this terminal")],
    ),
    "the region spans a word the bias dropped": (
        "Parse the config file", "Parse config file", [(0, 2, "parse_config")],
        "Parse the config file", [("reverted", "word_dropped", "Parse config", "Parse the config")],
    ),
    "a change outside any hit is kept": (
        "pause the config file", "parse the config file", [],
        "parse the config file", [("kept", "no_hit", "parse", "pause")],
    ),
    "a neighbour changed inside a widened hit region": (
        "run cube controls now", "run kubectl control now", [(1, 2, "kubectl")],
        "run cube controls now", [("reverted", "outside_term", "kubectl control", "cube controls")],
    ),
    "a term the anchor did not hear at all": (
        "ready for review", "ready for review ruff", [(3, 4, "ruff")],
        "ready for review", [("reverted", "inserted", "ruff", "")],
    ),
    "the anchor already said an alias": (
        "run cube control now", "run kubectl now", [(1, 2, "kubectl")],
        "run cube control now", [("reverted", "in_anchor", "kubectl", "cube control")],
    ),
    "a negation disappears": (
        "it is not working", "it Notion working", [(1, 2, "Notion")],
        "it is not working", [("reverted", "negation", "Notion", "is not")],
    ),
    "a number disappears": (
        "give it two more minutes", "give it Tauri more minutes", [(2, 3, "Tauri")],
        "give it two more minutes", [("reverted", "number", "Tauri", "two")],
    ),
    "only an inflection changes": (
        "he parsed args", "he parse args", [(1, 3, "parse_args")],
        "he parsed args", [("reverted", "inflection", "parse args", "parsed args")],
    ),
    "the anchor heard something else": (
        "the rough draft", "the ruff draft", [(1, 2, "ruff")],
        "the rough draft", [("reverted", "not_close", "ruff", "rough")],
    ),
    "the anchor was sure of a different word": (
        "this is hard@1.0", "this is hark", [(2, 3, "hark")],
        "this is hard", [("reverted", "anchor_confident", "hark", "hard")],
    ),
    "reverted hit and kept neighbour in one utterance": (
        "call the rough draft form main", "call the ruff draft from main", [(2, 3, "ruff")],
        "call the rough draft from main",
        [("reverted", "not_close", "ruff", "rough"), ("kept", "no_hit", "from", "form")],
    ),
    "adjacent hits are judged together": (
        "run Heart Doctor now", "run hark hark now", [(1, 2, "hark"), (2, 3, "hark")],
        "run Heart Doctor now", [("reverted", "not_close", "hark hark", "Heart Doctor")],
    ),
    "case and punctuation only: the biased words": (
        "train it with NeMo, then", "train it with Nemo then", [],
        "train it with Nemo then", [],
    ),
}  # fmt: skip


@pytest.mark.parametrize(("anchor", "biased", "hits", "text", "decisions"), CASES.values(), ids=CASES.keys())  # fmt: skip
def test_gate_decisions(
    anchor: str,
    biased: str,
    hits: list[tuple[int, int, str]],
    text: str,
    decisions: list[tuple[str, str, str, str]],
):
    b = hyp(biased)
    out = gate.apply(b, hyp(anchor), [hit(b, s, e, t) for s, e, t in hits], TERMS, GateConfig())
    assert out.text == text
    assert [w.text for w in out.words] == text.split()
    assert [(d.decision, d.reason, d.biased, d.anchor) for d in out.decisions] == decisions


def test_biased_words_carry_the_anchor_confidence():
    # equal words: their anchor word's; a kept hit: the anchor's least confident word there;
    # words the anchor did not hear at all (a clause it dropped): their own, and they stay
    b = hyp("I@0.3 asked@0.2 you@0.4 please@0.1 run@0.2 kubectl@0.1 get@0.1")
    a = hyp("please@0.9 run@0.95 cubectal@0.77 get@0.99")
    out = gate.apply(b, a, [hit(b, 5, 6, "kubectl")], TERMS, GateConfig())
    assert out.text == b.text
    assert [(w.text, w.confidence) for w in out.words] == [
        ("I", pytest.approx(0.3)), ("asked", pytest.approx(0.2)), ("you", pytest.approx(0.4)),
        ("please", pytest.approx(0.9)), ("run", pytest.approx(0.95)),
        ("kubectl", pytest.approx(0.77)), ("get", pytest.approx(0.99)),
    ]  # fmt: skip


# rough -> ruff: 3 letter edits over 5 letters = 0.6 (the phonetic keys "rgh" / "rf" are farther)
@pytest.mark.parametrize(("max_distance", "decision"), [(0.6, "kept"), (0.59, "reverted")])
def test_max_distance_is_inclusive(max_distance: float, decision: str):
    b = hyp("the ruff draft")
    cfg = GateConfig(max_distance=max_distance)
    out = gate.apply(b, hyp("the rough draft"), [hit(b, 1, 2, "ruff")], TERMS, cfg)
    assert [d.decision for d in out.decisions] == [decision]


def test_without_anchor_the_biased_result_passes():
    b = hyp("this is hark")
    out = gate.apply(b, None, [hit(b, 2, 3, "hark")], TERMS, GateConfig())
    assert (out.text, out.decisions) == ("this is hark", [])
