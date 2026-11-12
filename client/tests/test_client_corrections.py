from __future__ import annotations

import pytest

from hark_client import corrections


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # real dictations (ASR drops the commas around the cue)
        ("I would like to write a program in C um no in Rust.",
         "I would like to write a program in Rust."),
        ("I would like to write an application in C a no uh in Rust.",
         "I would like to write an application in Rust."),
        ("I want it written in C, uh no, in Rust.", "I want it written in Rust."),
        ("Um my name is David. Uh no, uh my name is Dan.", "Um my name is Dan."),
        # phrase cues
        ("Send it to Bob, sorry, to Alice.", "Send it to Alice."),
        ("Run it on the CPU, no wait, on the GPU.", "Run it on the GPU."),
        ("Use the red one I mean the blue one.", "Use the blue one."),
        # sentence-initial retraction keeps the capital
        ("In C no in Rust please.", "In Rust please."),
        # value substitutions: a number before a repeated unit, or one value for another
        ("Set the timeout to 3 seconds, sorry 5 seconds.", "Set the timeout to 5 seconds."),
        ("make the limit ten megabytes, no, twenty megabytes", "make the limit twenty megabytes"),
        ("use Postgres, no wait, SQLite for the cache", "use SQLite for the cache"),
        ("Call it fetch_rows, I mean fetch_row_batch.", "Call it fetch_row_batch."),
        ("Use port 8080, no, 8081.", "Use port 8081."),
        ("Two coffees, make that three coffees.", "Three coffees."),
        # not corrections: answers, negations, no repeated word, too long, crossing sentences
        ("Is it done? No, it is not.", "Is it done? No, it is not."),
        ("No, I don't want C.", "No, I don't want C."),
        ("Ship it, no tests needed.", "Ship it, no tests needed."),
        ("There is no wait time.", "There is no wait time."),
        ("Use C not Rust.", "Use C not Rust."),
        ("Meet on Tuesday no Wednesday.", "Meet on Tuesday no Wednesday."),
        ("Put it in the big red old wooden box no in the bag.",
         "Put it in the big red old wooden box no in the bag."),
        ("Use Rust in it. Then C, no, in Go.", "Use Rust in it. Then C, no, in Go."),
        ("in no in", "in no in"),
        # a bare cue that starts a sentence (real speech: a misheard "you know")
        ("I didn't even realize the ghost one left. No, the only character I remember was him.",
         "I didn't even realize the ghost one left. No, the only character I remember was him."),
        # not value substitutions: across a sentence, a new clause, a bare cue without
        # commas or between names, different kinds, a negated repeat
        ("It has 2 bugs. No, 3 bugs.", "It has 2 bugs. No, 3 bugs."),
        ("Ask Sarah, no wait, John wants to know.", "Ask Sarah, no wait, John wants to know."),
        ("Ask Sarah, sorry, John.", "Ask Sarah, sorry, John."),
        ("Use port 8080 no 8081.", "Use port 8080 no 8081."),
        ("Set it to 3, I mean, Python.", "Set it to 3, I mean, Python."),
        ("I said 5 seconds, no, not 5 seconds.", "I said 5 seconds, no, not 5 seconds."),
    ],
)  # fmt: skip
def test_resolve(text: str, expected: str):
    assert corrections.resolve(text)[0] == expected


def test_resolve_reports_what_was_removed():
    _, found = corrections.resolve("write it in C um no in Rust")
    assert [(c.retracted, c.anchor) for c in found] == [("in C um no", "in")]


def test_resolve_handles_several_corrections():
    text = "Use C no use Rust and run it on Monday sorry on Friday."
    assert corrections.resolve(text)[0] == "Use Rust and run it on Friday."
