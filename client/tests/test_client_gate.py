"""Anchor gate: the biased words get the anchor's confidences; one narrow text revert."""

from __future__ import annotations

import pytest

from skriv.v1 import skriv_pb2 as pb
from skriv_client import gate

TERMS = [pb.BiasTerm(id="LLM", canonical="LLM", aliases=[pb.SpokenAlias(text="L L M")])]


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


def test_biased_words_carry_the_anchor_confidence():
    # equal words (case and punctuation aside): their anchor word's; a differing region: the
    # anchor's least confident word there; words the anchor did not hear at all (a clause it
    # dropped): their own. The text stays the biased one.
    b = hyp("I@0.3 asked@0.2 you@0.4 please@0.1 run@0.2 kubectl@0.1 parse@0.1 config@0.1")
    a = hyp("Please,@0.9 run@0.95 cubectal@0.77 pause@0.6 the@0.99 config@0.99")
    out = gate.apply(b, a, TERMS)
    assert (out.text, out.reverted) == (b.text, [])
    assert [(w.text, w.confidence) for w in out.words] == [
        ("I", pytest.approx(0.3)), ("asked", pytest.approx(0.2)), ("you", pytest.approx(0.4)),
        ("please", pytest.approx(0.9)), ("run", pytest.approx(0.95)),
        ("kubectl", pytest.approx(0.6)), ("parse", pytest.approx(0.6)),
        ("config", pytest.approx(0.99)),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("anchor", "biased", "text"),
    [
        ("handle empty@0.7 transcripts", "handle MT transcripts", "handle empty transcripts"),
        ("made in Oz", "made in US", "made in US"),  # an all-caps English word
        ("ask the well", "ask the LLM", "ask the LLM"),  # a shortlist word
        ("handle empty files", "handle MT logs", "handle MT logs"),  # more than one word differs
    ],
)
def test_an_all_caps_non_word_takes_the_anchor_word(anchor: str, biased: str, text: str):
    out = gate.apply(hyp(biased), hyp(anchor), TERMS)
    assert out.text == text
    assert [w.text for w in out.words] == text.split()
    if text != biased:
        assert out.reverted == ["MT -> empty"]
        assert out.words[1].confidence == pytest.approx(0.7)
