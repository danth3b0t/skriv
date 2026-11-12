"""Cleanup: the server's rewrite, kept only if `fidelity.check` passes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import grpc
import pytest

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from skriv_client import cleanup, fidelity
from skriv_client.context import Term, TermKind, TermSource

from .conftest import KUBECTL

if TYPE_CHECKING:
    from collections.abc import Iterator


def term(canonical: str, kind: TermKind = TermKind.IDENTIFIER) -> Term:
    return Term(canonical, (), 1.0, kind, TermSource.VOCABULARY)


def test_protected_covers_shortlist_terms_and_code_like_tokens():
    text = "don't run kubectl on port 8080 in parse_config, see ./docs/a.md and --force."
    assert cleanup.protected(text, [KUBECTL, term("Hyprland")]) == {
        "kubectl",  # shortlist, present
        "parse_config",
        "./docs/a.md",
        "--force",
    }


@pytest.mark.parametrize(
    ("before", "after", "why"),
    [
        # passes: fillers, casing, punctuation, light rewording, digits for spoken numbers
        ("um so I think it is not done", "So I think it is not done.", ""),
        ("I'm gonna buy you a bike", "I'm going to buy you a bike.", ""),
        ("set the limit to seven", "Set the limit to 7.", ""),
        ("No no not yet", "No, not yet.", ""),  # a stutter counts once
        ("on Hyper LAN the paste", "On HyperLAN, the paste", ""),  # merged
        ("Show me the last_text", "Show me the last text.", ""),  # identifier back to words
        ("Set the timeout to 3 seconds, sorry 5 seconds.", "Set the timeout to 5 seconds.", ""),
        ("Make it 10 MB, no, 20 MB.", "Make it 20 MB.", ""),  # the cue's "no" goes with it
        ("um", "", ""),
        # fails
        ("Okay.", "", "dropped everything"),
        ("run the tests", "Run the tests.\nThen push.", "added a line break"),
        # a sentence's opening verb, though "use" stays elsewhere and the text is long
        ("Use useQuery from TanStack Query instead of fetching in a use effect",
         "useQuery from TanStack Query instead of fetching in a use effect.", "dropped us"),
        ("After after I've done after we're done, I'll eat", "After we're done, I'll eat.", ""),
        ("add type hints to read wave and write wave", "Add type hints to ReadWave and WriteWave.",
         "added ReadWave, WriteWave"),  # invented identifiers
        ("call model dot train", "Call model.train.", ""),  # spoken separators
        ("pass --no-history to it", "Pass no-history to it.", "changed '--no-history'"),
        ("pass --no-history to it", "Pass --no-history to it, please.", ""),
        ("fetch_rows is slow", "Fetch rows is slow.", ""),  # the trap rule cuts both ways
        ("I can't merge it today", "I can merge it today.", "negations 1 -> 0"),
        ("merge it today", "Don't merge it today.", "negations 0 -> 1"),
        ("Is it done? No, it is not.", "It is not.", "negations 2 -> 1"),  # an answer
        ("use C not Rust", "Use Rust.", "negations 1 -> 0"),  # no cue
        ("port 8080", "Port 8081.", "numbers 8080 -> 8081"),
        ("issue four twenty one", "Issue 421.", "numbers 21 4 -> 421"),
        ("show the logs", "Show the logs for the worker.", "added worker"),
        ("restart the worker and the scheduler", "Restart the worker.", "dropped scheduler"),
    ],
)  # fmt: skip
def test_fidelity_check(before: str, after: str, why: str):
    protected = {"--no-history", "fetch_rows", "last_text"} & set(before.split())
    got = fidelity.check(before, after, protected, single_line=True)
    assert got.startswith(why) if why else got == ""


@pytest.mark.parametrize(
    ("before", "after", "kept"),
    [
        ("Um run the tests. Pass --no-history to it.", "Run the tests. Pass no-history to it.",
         "Run the tests. Pass --no-history to it."),
        ("Um run the tests. Then uh push it. Pass --no-history to it.",  # joined
         "Run the tests, then push it. Pass no-history to it.",
         "Run the tests, then push it. Pass --no-history to it."),
        ("um run the tests and then uh push it. Pass --no-history to it.",  # split
         "Run the tests. Then push it. Pass no-history to it.",
         "Run the tests. Then push it. Pass --no-history to it."),
        ("Set it to 3 seconds. Sorry, 5 seconds. Um pass --no-history.",  # corrected
         "Set it to 5 seconds. Pass no-history.",
         "Set it to 5 seconds. Um pass --no-history."),
        ("Um run the tests. Do not push it.", "Run the tests. Push it.",
         "Run the tests. Do not push it."),
        ("Um run the tests. Uh push it.", "Run the tests. Push it. Then deploy to prod.",
         "Run the tests. Push it."),  # an added sentence goes
        ("um do not push. uh do not merge.", "Push. Merge.",
         "um do not push. uh do not merge."),  # no sentence passes
    ],
)  # fmt: skip
def test_accept_keeps_the_faithful_sentences(before: str, after: str, kept: str):
    text, why = fidelity.accept(before, after, {"--no-history"}, single_line=True)
    assert text == kept
    assert why  # the whole rewrite failed


class FixedRewrite(pb_grpc.TextCleanupServicer):
    """Returns `rewrite` for every request (None: no rewrite, with `skipped`)."""

    def __init__(self) -> None:
        self.rewrite: str | None = None
        self.requests: list[pb.CleanupRequest] = []

    def Cleanup(
        self, request: pb.CleanupRequest, context: grpc.ServicerContext
    ) -> pb.CleanupResponse:
        self.requests.append(request)
        if self.rewrite is None:
            return pb.CleanupResponse(skipped="not_english")
        return pb.CleanupResponse(text=self.rewrite)


@pytest.fixture
def server() -> Iterator[tuple[FixedRewrite, grpc.Channel]]:
    servicer = FixedRewrite()
    srv = grpc.server(ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_TextCleanupServicer_to_server(servicer, srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield servicer, channel
    srv.stop(0)


@pytest.mark.parametrize(
    ("text", "rewrite", "terms", "result", "status", "reason"),
    [
        ("um please run kubectl get", "Please run kubectl get.", [KUBECTL],
         "Please run kubectl get.", "applied", ""),
        ("please run kubectl get", "please run kubectl get", [KUBECTL],
         "please run kubectl get", "no_edits", ""),
        # a re-cased identifier is put back; a sentence-start capital is no reason to reject
        ("skriv-server should log it", "Skriv-server should log it.", [term("skriv-server")],
         "skriv-server should log it.", "applied", ""),
        ("please run kubectl get", "Please don't run kubectl get.", [KUBECTL],
         "please run kubectl get", "rejected", "negations 0 -> 1"),
        ("please run kubectl get", "Please run the cube control get.", [KUBECTL],
         "please run kubectl get", "rejected", "changed 'kubectl'"),
        # the sentences that pass are kept
        ("um run kubectl get. Uh then stop.", "Run the cube control get. Then stop.", [KUBECTL],
         "um run kubectl get. Then stop.", "applied", "kept 1 of 2 sentences as said: changed"),
        # merged into a shortlist term the text did not have
        ("from Super Whisper", "From Superwhisper.",
         [term("Super", TermKind.NAME), term("Whisper", TermKind.NAME), term("Superwhisper")],
         "From Superwhisper.", "applied", ""),
        ("Tohle je test.", None, [], "Tohle je test.", "skipped", "not_english"),
    ],
)  # fmt: skip
def test_run_keeps_the_rewrite_only_if_faithful(
    server: tuple[FixedRewrite, grpc.Channel],
    text: str,
    rewrite: str | None,
    terms: list[Term],
    result: str,
    status: str,
    reason: str,
):
    servicer, channel = server
    servicer.rewrite = rewrite
    out = cleanup.run(
        channel, text=text, terms=terms, utterance_id="u1", profile_id="terminal",
        app_category="terminal", language="", deadline_ms=2000,
    )  # fmt: skip
    assert (out.text, out.status) == (result, status)
    assert out.reason.startswith(reason) if reason else out.reason == ""
    (request,) = servicer.requests
    assert (request.text, request.profile_id) == (text, "terminal")  # the shortlist stays here


def test_language_setting_skips_cleanup_without_a_call():
    with grpc.insecure_channel("127.0.0.1:1") as nowhere:
        out = cleanup.run(
            nowhere, text="please run it", terms=[], utterance_id="u1", profile_id="prose",
            app_category="prose", language="cs", deadline_ms=200,
        )  # fmt: skip
    assert (out.text, out.status, out.reason) == ("please run it", "skipped", "non_english")
