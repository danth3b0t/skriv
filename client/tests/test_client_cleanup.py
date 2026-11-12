"""Protected spans and the client-side (authoritative) edit validator."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import grpc
import pytest

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from hark_client import cleanup
from hark_client.context import Term, TermKind, TermSource

from .conftest import KUBECTL

if TYPE_CHECKING:
    from collections.abc import Iterator

K = pb.EditKind
R = pb.EditRejectionReason
TEXT = "Grüße, don't run kubectl on port 8080 in parse_config"
RAW = TEXT.encode()


def span_text(s: cleanup.Span) -> str:
    return RAW[s.start : s.end].decode()


def test_protected_spans_cover_identifiers_numbers_and_negations():
    spans = cleanup.protected_spans(TEXT + ".", [KUBECTL])
    assert [(span_text(s), s.kind) for s in spans] == [
        ("don't", pb.PROTECTED_SPAN_KIND_NEGATION),
        ("kubectl", pb.PROTECTED_SPAN_KIND_IDENTIFIER),
        ("8080", pb.PROTECTED_SPAN_KIND_NUMBER),
        ("parse_config", pb.PROTECTED_SPAN_KIND_IDENTIFIER),  # code-like, not in the shortlist
    ]


def make_edit(find: str, replace: str, kind: pb.EditKind.ValueType, text: str = TEXT) -> pb.Edit:
    start = text.encode().index(find.encode())
    return pb.Edit(
        byte_start=start, byte_end=start + len(find.encode()), find=find, replace=replace, kind=kind
    )


@pytest.mark.parametrize(
    ("edit", "error"),
    [
        (make_edit("kubectl", "Kubectl", K.EDIT_KIND_CAPITALIZATION), "protected span"),
        (make_edit("run", "ran", K.EDIT_KIND_CAPITALIZATION), "invariant"),
        (make_edit("on port", "on port", K.EDIT_KIND_SPACING), "invariant"),  # no-op
        (make_edit("on", "On", K.EDIT_KIND_CAPITALIZATION), "unique"),  # also in "don't"
        # byte range does not match the quoted text
        (
            pb.Edit(byte_start=2, byte_end=4, find="üß", replace="ss", kind=K.EDIT_KIND_SPACING),
            "unique",
        ),
        (
            make_edit("Grüße", "Grüße.", K.EDIT_KIND_GRAMMAR),
            "invariant",
        ),  # not mechanically checkable
    ],
)
def test_validator_rejects(edit: pb.Edit, error: str):
    checked = cleanup.validate_edits(TEXT, [edit], cleanup.protected_spans(TEXT, [KUBECTL]))
    assert (checked.text, checked.accepted) == (TEXT, [])
    ((dropped, why),) = checked.dropped
    assert dropped == edit
    assert error in why


def test_validator_applies_valid_edits_and_checks_negations():
    text = "so um I think it is not done"
    spans = cleanup.protected_spans(text, [])
    ok = [
        make_edit("so um I", "so I", K.EDIT_KIND_FILLER_REMOVAL, text),
        make_edit("done", "done.", K.EDIT_KIND_PUNCTUATION, text),
    ]
    checked = cleanup.validate_edits(text, ok, spans)
    assert (checked.text, checked.accepted, checked.dropped) == (
        "so I think it is not done.",
        ok,
        [],
    )


GROUPED = "so the build fails on the linker step, uh, can you check it"
IDENT = "Um, so please run kubectl in the cluster, uh, and then check it"
NEG = "Um, so the thing is I can't merge it today, uh, the build is red"
F, C, P = K.EDIT_KIND_FILLER_REMOVAL, K.EDIT_KIND_CAPITALIZATION, K.EDIT_KIND_PUNCTUATION


@pytest.mark.parametrize(
    ("text", "edits", "result", "dropped"),
    [
        (  # a protected identifier edit is dropped; the filler removals around it stay
            IDENT, [("Um, so", "So", F), ("kubectl", "Kubectl", C), ("cluster, uh,", "cluster", F)],
            "So please run kubectl in the cluster and then check it",
            [("kubectl", "touches a protected span")],
        ),
        (  # unprotected, a lost negation is caught globally and pinned on its edit
            NEG, [("Um, so", "So", F), ("can't", "cant", P), ("today, uh,", "today,", F)],
            "So the thing is I can't merge it today, the build is red",
            [("can't", "negation count changed")],
        ),
        (  # a dropped edit takes its neighbour: no "step, uh, Can"
            GROUPED, [("step, uh,", "stage.", F), ("can", "Can", C)], GROUPED,
            [("step, uh,", "breaks its EDIT_KIND_FILLER_REMOVAL invariant"),
             ("can", "dropped with its group")],
        ),
        (  # ... and so does one a word further away; two words away is another group
            GROUPED, [("so the", "So the", C), ("on the", "in the", F), ("step, uh,", "step.", F),
                      ("can", "Can", C)],
            "So the build fails on the linker step, uh, can you check it",
            [("on the", "breaks its EDIT_KIND_FILLER_REMOVAL invariant"),
             ("step, uh,", "dropped with its group"), ("can", "dropped with its group")],
        ),
        (  # an edit overlapping an accepted one is dropped alone
            GROUPED, [("so the", "So the", C), ("the build", "The build", C)],
            "So the build fails on the linker step, uh, can you check it",
            [("the build", "overlaps an earlier edit")],
        ),
        (  # a failed rule that no edit can be blamed for drops them all
            "um uh like you know ok so", [("um uh like", "", F), ("you know ok", "ok", F)],
            "um uh like you know ok so",
            [("um uh like", "too many words removed"), ("you know ok", "too many words removed")],
        ),
    ],
)  # fmt: skip
def test_validator_drops_failing_edits_with_their_group(
    text: str,
    edits: list[tuple[str, str, pb.EditKind.ValueType]],
    result: str,
    dropped: list[tuple[str, str]],
):
    proposed = [make_edit(f, r, k, text) for f, r, k in edits]
    spans = cleanup.protected_spans(text, [KUBECTL]) if text == IDENT else []
    checked = cleanup.validate_edits(text, proposed, spans)
    assert checked.text == result
    assert [(e.find, why) for e, why in checked.dropped] == dropped


CORRECTION = "Run kubectl on port 8080, no wait, on port 8081!"


@pytest.mark.parametrize(
    ("text", "find", "replace", "kind", "result", "error"),
    [
        (  # retracted number and the cue's "no" (protected negation) may go
            CORRECTION, "on port 8080, no wait, on port 8081", "on port 8081",
            K.EDIT_KIND_SELF_CORRECTION, "Run kubectl on port 8081!", "",
        ),
        (  # ... a kept protected identifier may not change
            CORRECTION, "kubectl on port 8080, no wait, on port 8081", "Kubectl on port 8081",
            K.EDIT_KIND_SELF_CORRECTION, "", "protected span",
        ),
        ("so I like want the the report", "like want the the", "want the",
         K.EDIT_KIND_FILLER_REMOVAL, "so I want the report", ""),
        ("it is, uh, you know, broken", "is, uh, you know, broken", "is broken",
         K.EDIT_KIND_FILLER_REMOVAL, "it is broken", ""),
        ("it is, uh, I don't know, broken", "uh, I don't know,", "uh,",
         K.EDIT_KIND_FILLER_REMOVAL, "", "protected span"),
        ("move it to twenty five", "twenty five", "25.", K.EDIT_KIND_NUMBER_FORMAT,
         "move it to 25.", ""),
        ("move it to twenty five", "twenty five", "52", K.EDIT_KIND_NUMBER_FORMAT, "", "invariant"),
        ("say hello", "hello", '"hello"', K.EDIT_KIND_PUNCTUATION, "", "invariant"),
        ("never edit it", "never", "Never", K.EDIT_KIND_CAPITALIZATION, "Never edit it", ""),
        ("There is no wait time", "no", "No", K.EDIT_KIND_CAPITALIZATION, "", "protected span"),
        ("I mean it, ship it", "I mean it", "It", K.EDIT_KIND_FILLER_REMOVAL, "", "invariant"),
        ("edit main.py", "main.py", "main.py.", K.EDIT_KIND_PUNCTUATION, "edit main.py.", ""),
        ("meet at 3:30 pm", "3:30 pm", "3:30pm", K.EDIT_KIND_SPACING, "", "protected span"),
        ("It works!", "works!", "works.", K.EDIT_KIND_PUNCTUATION, "", "exclamation"),
        ("um uh like you know ok", "um uh like you know ok", "ok", K.EDIT_KIND_FILLER_REMOVAL,
         "", "too many words"),
        # bare cues: the retracted words and the cue's "no" may go ...
        ("I want it written in C, uh no, in Rust.", "in C, uh no, in", "in",
         K.EDIT_KIND_SELF_CORRECTION, "I want it written in Rust.", ""),
        ("Um my name is David. Uh no, uh my name is Dan.", "Um my name is David. Uh no, uh my",
         "My", K.EDIT_KIND_SELF_CORRECTION, "My name is Dan.", ""),
        ("Pick the red blue green big car, no, the bus.", "the red blue green big car, no, the",
         "the", K.EDIT_KIND_SELF_CORRECTION, "Pick the bus.", ""),
        # ... but not more than 6 words, across a sentence, after a question or with no
        # retracted words: there the "no" is a protected negation
        ("Pick the red blue green big old car, no, the bus.",
         "the red blue green big old car, no, the", "the", K.EDIT_KIND_SELF_CORRECTION, "",
         "protected span"),
        ("Use Rust. Then C, no, then Go.", "Use Rust. Then C, no, then", "Then",
         K.EDIT_KIND_SELF_CORRECTION, "", "protected span"),
        ("Is it done? No, it is not.", "Is it done? No, it", "It", K.EDIT_KIND_SELF_CORRECTION,
         "", "protected span"),
        ("No, I don't want C.", "No, I", "I", K.EDIT_KIND_SELF_CORRECTION, "", "protected span"),
        ("use C not Rust", "C not Rust", "Rust", K.EDIT_KIND_SELF_CORRECTION, "",
         "protected span"),
        ("It is, actually, fine.", "It is, actually, fine", "Fine", K.EDIT_KIND_SELF_CORRECTION,
         "", "invariant"),  # not the same kind: no repeated word, not a single-word slot
    ],
)  # fmt: skip
def test_validator_s1_edit_kinds(
    text: str, find: str, replace: str, kind: pb.EditKind.ValueType, result: str, error: str
):
    edits, spans = [make_edit(find, replace, kind, text)], cleanup.protected_spans(text, [KUBECTL])
    checked = cleanup.validate_edits(text, edits, spans)
    assert checked.text == (text if error else result)
    assert [error in why for _, why in checked.dropped] == ([True] if error else [])


@pytest.mark.parametrize(
    ("text", "find", "replace", "terms", "error"),
    [
        ("like Super Whisper.", "Super Whisper.", "Superwhisper.", ["Superwhisper"], ""),
        ("in open pencil But", "open pencil", "OpenPencil.", ["OpenPencil"], ""),
        ("like Super Whisper.", "Super Whisper.", "Superwhisper.", [], "protected span"),
        ("like Super Whisperer.", "Super Whisperer.", "Superwhisper.", ["Superwhisper"],
         "protected span"),  # a letter changes too
    ],
)  # fmt: skip
def test_validator_merges_words_into_known_terms(
    text: str, find: str, replace: str, terms: list[str], error: str
):
    # "Super" and "Whisper" are protected bias terms, as in the real dictation.
    shortlist = [
        Term(t, (), 0.5, TermKind.NAME, TermSource.VOCABULARY) for t in ("Super", "Whisper")
    ]
    edits = [make_edit(find, replace, K.EDIT_KIND_SPACING, text)]
    checked = cleanup.validate_edits(text, edits, cleanup.protected_spans(text, shortlist), terms)
    assert checked.text == (text if error else text.replace(find, replace))
    assert [error in why for _, why in checked.dropped] == ([True] if error else [])


@pytest.mark.parametrize(
    ("text", "language", "skipped"),
    [
        ("Tohle je test diktování v češtině.", "", True),
        ("Ich glaube, das ist nicht richtig.", "", True),
        ("please run it", "cs", True),
        ("Grüße! I think the café on main street is fine", "", False),
        ("ls -la ~/projects", "", False),
        ("please run it", "en-US", False),
    ],
)
def test_non_english_text_is_not_cleaned(text: str, language: str, *, skipped: bool):
    with grpc.insecure_channel("127.0.0.1:1") as nowhere:  # English text gets an RPC error
        out = cleanup.run(
            nowhere, text=text, terms=[], utterance_id="u1", profile_id="prose",
            app_category="prose", language=language, deadline_ms=200,
        )  # fmt: skip
    assert out.text == text
    assert (
        (out.status, out.reason) == ("skipped", "non_english") if skipped else out.status == "error"
    )


class BadCleanup(pb_grpc.TextCleanupServicer):
    """A server whose validator let an edit of a protected identifier through,
    next to a filler removal when the text starts with "Um, so"."""

    def Cleanup(
        self, request: pb.CleanupRequest, context: grpc.ServicerContext
    ) -> pb.CleanupResponse:
        edits = [make_edit("kubectl", "Kubectl", K.EDIT_KIND_CAPITALIZATION, request.text)]
        if request.text.startswith("Um, so"):
            edits.insert(0, make_edit("Um, so", "So", K.EDIT_KIND_FILLER_REMOVAL, request.text))
        return pb.CleanupResponse(
            edits=edits,
            rejections=[
                pb.EditRejection(
                    find="get", replace="Get!", reason=R.EDIT_REJECTION_REASON_INVARIANT
                ),
                pb.EditRejection(find="run", replace="run", reason=R.EDIT_REJECTION_REASON_NO_OP),
            ],
        )


@pytest.fixture
def bad_cleanup_channel() -> Iterator[grpc.Channel]:
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_TextCleanupServicer_to_server(BadCleanup(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield channel
    server.stop(0)


@pytest.mark.parametrize(
    ("text", "result", "status", "reason", "applied"),
    [
        ("please run kubectl get", "please run kubectl get", "rejected",
         "0 of 2 edits applied; dropped: invariant, touches a protected span", []),
        ("Um, so please just run kubectl get", "So please just run kubectl get", "partial",
         "1 of 3 edits applied; dropped: invariant, touches a protected span", ["Um, so"]),
    ],
)  # fmt: skip
def test_protected_span_violation_drops_only_that_edit(
    bad_cleanup_channel: grpc.Channel,
    text: str,
    result: str,
    status: str,
    reason: str,
    applied: list[str],
):
    out = cleanup.run(
        bad_cleanup_channel, text=text, terms=[KUBECTL], utterance_id="u1",
        profile_id="prose", app_category="prose", language="", deadline_ms=2000,
    )  # fmt: skip
    assert (out.text, out.status, out.reason) == (result, status, reason)
    assert [e["find"] for e in out.edits] == applied
    assert out.dropped == [  # the server's no-op echo is not a loss
        {"find": "get", "replace": "Get!", "by": "server", "reason": "invariant"},
        {"find": "kubectl", "replace": "Kubectl", "by": "client",
         "reason": "touches a protected span"},
    ]  # fmt: skip
