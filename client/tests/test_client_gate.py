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
        "switched to Harkon today", "switched to Hark on today", [(2, 3, "hark")],
        "switched to Hark on today", [("kept", "close", "Hark on", "Harkon")],
    ),
    "the region spans a word the bias dropped": (
        "Parse the config file", "Parse config file", [(0, 2, "parse_config")],
        "Parse the config file", [("reverted", "word_dropped", "Parse config", "Parse the config")],
    ),
    "a change outside any hit is reverted": (
        "pause the config file", "parse the config file", [],
        "pause the config file", [("reverted", "outside_term", "parse", "pause")],
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
    "kept hit and reverted neighbour in one utterance": (
        "call pass config from main", "call parse config form main", [(1, 3, "parse_config")],
        "call parse config from main",
        [("kept", "close", "parse config", "pass config"), ("reverted", "outside_term", "form", "from")],
    ),
    "adjacent hits are judged together": (
        "run Heart Doctor now", "run hark hark now", [(1, 2, "hark"), (2, 3, "hark")],
        "run Heart Doctor now", [("reverted", "not_close", "hark hark", "Heart Doctor")],
    ),
    "case and punctuation only: the anchor's words": (
        "train it with NeMo, then", "train it with Nemo then", [],
        "train it with NeMo, then", [],
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


def test_kept_words_carry_the_anchor_confidence():
    b = hyp("please run kubectl@0.1 get")
    out = gate.apply(b, hyp("please run cubectal@0.77 get"), [hit(b, 2, 3, "kubectl")], TERMS, GateConfig())  # fmt: skip
    assert [(w.text, w.confidence) for w in out.words][2] == ("kubectl", pytest.approx(0.77))


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
