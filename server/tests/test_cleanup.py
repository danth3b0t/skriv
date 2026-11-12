"""TextCleanup proxy against a fake OpenAI-compatible /v1/chat/completions."""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, Unpack, cast

import grpc
import pytest
from conftest import dig

from hark.v1 import hark_pb2 as pb
from hark_server.cleanup import (
    KIND_NAMES,
    KINDS,
    POLICY,
    PROFILES,
    S1_CONTROL,
    S1_SYSTEM,
    ProfileSpec,
    ProposedEdit,
    apply_edits,
    resolve_edits,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from conftest import Client, Serve

R = pb.EditRejectionReason
DATA = Path(__file__).parent / "data"
TEXT = (
    "Grüße! um, so i think the café on main street is fine and the café downtown "
    "is not open, I can't run kubectl get pods now"
)
RAW = TEXT.encode()
KUBECTL = pb.ProtectedSpan(
    byte_start=RAW.index(b"kubectl"),
    byte_end=RAW.index(b"kubectl") + 7,
    kind=pb.PROTECTED_SPAN_KIND_IDENTIFIER,
)


def edit(find: str, replace: str, kind: str) -> dict[str, str]:
    return {"find": find, "replace": replace, "kind": kind, "reason": "test"}


class Recorded(TypedDict):
    path: str
    json: object


class CleanupFields(TypedDict, total=False):
    """CleanupRequest fields the tests set."""

    text: str
    protected_spans: list[pb.ProtectedSpan]
    profile_id: str
    budget_ms: int
    privacy: pb.PrivacyFlags
    context: pb.CleanupContext
    language: str
    known_terms: list[str]


def span(text: str, part: str) -> tuple[int, int]:
    start = text.encode().index(part.encode())
    return start, start + len(part.encode())


class FakeLLM:
    """Serves queued responses; records request bodies."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self.queue: list[tuple[int, str, float]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.requests.append({"path": self.path, "json": cast("object", json.loads(body))})
                status, payload, delay = fake.queue.pop(0)
                time.sleep(delay)
                data = payload.encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # client gave up (budget)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()

    def respond_edits(self, edits: list[dict[str, str]], delay: float = 0.0) -> None:
        content = json.dumps({"edits": edits})
        body = {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
        self.queue.append((200, json.dumps(body), delay))

    def respond_text(self, content: str) -> None:
        body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        self.queue.append((200, json.dumps(body), 0.0))

    def respond_raw(self, status: int, payload: str) -> None:
        self.queue.append((status, payload, 0.0))


@pytest.fixture
def fake_llm() -> Iterator[FakeLLM]:
    fake = FakeLLM()
    yield fake
    fake.httpd.shutdown()
    fake.httpd.server_close()


@pytest.fixture
async def cleanup_client(serve: Serve, fake_llm: FakeLLM) -> Client:
    """The edit-list engine."""
    return await serve(
        cleanup={
            "base_url": fake_llm.url, "model": "fake-llm", "default_budget_ms": 2000,
            "engine": "edits",
        }
    )  # fmt: skip


@pytest.fixture
async def s1_client(serve: Serve, fake_llm: FakeLLM) -> Client:
    return await serve(
        cleanup={"base_url": fake_llm.url, "model": "s1-mini", "default_budget_ms": 2000}
    )


async def call(
    client: Client, **kw: Unpack[CleanupFields]
) -> tuple[pb.CleanupResponse | None, grpc.StatusCode, pb.ErrorDetail | None]:
    kw.setdefault("text", TEXT)
    kw.setdefault("protected_spans", [KUBECTL])
    return await client.unary(client.cleanup.Cleanup, pb.CleanupRequest(utterance_id="u-c", **kw))


async def test_cleanup_resolves_unique_quotes_to_byte_ranges(
    cleanup_client: Client, fake_llm: FakeLLM
):
    caps = await cleanup_client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert "hark.cleanup" in caps.features
    assert [p.id for p in caps.cleanup_profiles] == ["terminal", "chat", "prose"]

    fake_llm.respond_edits(
        [
            edit("i think", "I think", "capitalization"),
            edit("um, so", "so", "filler_removal"),
            edit("café", "Café", "capitalization"),  # occurs twice
            edit("kubectl", "Kubectl", "capitalization"),  # protected
        ]
    )
    resp, code, _ = await call(cleanup_client)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    # Byte offsets (not code points): "Grüße" has two 2-byte characters.
    um, i_think = RAW.index(b"um, so"), RAW.index(b"i think")
    assert um == TEXT.index("um, so") + 2
    assert [(e.byte_start, e.byte_end, e.find, e.replace, e.kind) for e in resp.edits] == [
        (um, um + 6, "um, so", "so", pb.EDIT_KIND_FILLER_REMOVAL),
        (i_think, i_think + 7, "i think", "I think", pb.EDIT_KIND_CAPITALIZATION),
    ]
    assert [(r.find, r.reason) for r in resp.rejections] == [
        ("café", R.EDIT_REJECTION_REASON_NOT_UNIQUE),
        ("kubectl", R.EDIT_REJECTION_REASON_PROTECTED_SPAN),
    ]
    assert resp.rejected_by_server == 2
    assert not resp.truncated
    assert resp.model == "fake-llm"

    (sent,) = fake_llm.requests
    assert sent["path"] == "/v1/chat/completions"
    body = sent["json"]
    assert dig(body, "model") == "fake-llm"
    assert dig(body, "temperature") == 0
    fmt = dig(body, "response_format")
    assert dig(fmt, "type") == "json_schema"
    assert dig(fmt, "json_schema", "name") == "hark_edits_v1"
    item = dig(fmt, "json_schema", "schema", "properties", "edits", "items")
    assert dig(item, "required") == ["find", "replace", "kind", "reason"]  # no occurrence index
    user = cast("object", json.loads(cast("str", dig(body, "messages", -1, "content"))))
    assert dig(user, "text") == TEXT
    assert dig(user, "protected") == ["kubectl"]


@pytest.mark.parametrize(
    ("proposed", "accepted", "reasons"),
    [
        ([edit("banana", "Banana", "capitalization")], [], [R.EDIT_REJECTION_REASON_NOT_FOUND]),
        ([edit("is fine", "is fine", "punctuation")], [], [R.EDIT_REJECTION_REASON_NO_OP]),
        ([edit("is fine", "is good", "grammar")], [], [R.EDIT_REJECTION_REASON_KIND_NOT_ALLOWED]),
        ([edit("is fine", "is good", "punctuation")], [], [R.EDIT_REJECTION_REASON_INVARIANT]),
        ([edit("main street", "Main Street,", "capitalization")], [], [R.EDIT_REJECTION_REASON_INVARIANT]),
        (
            [edit("i think", "I think", "capitalization"), edit("think the", "think, the", "punctuation")],
            ["i think"],
            [R.EDIT_REJECTION_REASON_OVERLAP],
        ),
        (  # more than half invalid: the valid edit is dropped too
            [edit("i think", "I think", "capitalization"), edit("banana", "x", "spacing"),
             edit("apple", "y", "spacing")],
            [],
            [R.EDIT_REJECTION_REASON_NOT_FOUND] * 2 + [R.EDIT_REJECTION_REASON_GLOBAL],
        ),
        (  # passes the punctuation invariant but removes a negation
            [edit("can't", "cant", "punctuation")], [], [R.EDIT_REJECTION_REASON_GLOBAL],
        ),
    ],
)  # fmt: skip
async def test_cleanup_rejects_invalid_edits(
    cleanup_client: Client,
    fake_llm: FakeLLM,
    proposed: list[dict[str, str]],
    accepted: list[str],
    reasons: list[pb.EditRejectionReason.ValueType],
):
    fake_llm.respond_edits(proposed)
    resp, code, _ = await call(cleanup_client)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert [e.find for e in resp.edits] == accepted
    assert [r.reason for r in resp.rejections] == reasons


@pytest.mark.parametrize(
    ("profile", "text", "protected", "proposed", "accepted", "reasons"),
    [
        (  # kept prefix + retracted words + cue + correction; retracted digits may go
            "terminal", "Run it on port 8080, sorry, I mean port 8081.", ["8080", "8081"],
            [edit("Run it on port 8080, sorry, I mean port 8081", "Run it on port 8081", "self_correction")],
            [("Run it on port 8080, sorry, I mean port 8081", "Run it on port 8081", "self_correction")],
            [],
        ),
        (  # quoting from the cue retracts nothing: "Open the config file, the env file."
            "terminal", "Open the config file, no wait, the env file.", [],
            [edit("no wait, the env file", "the env file", "self_correction")],
            [], [R.EDIT_REJECTION_REASON_INVARIANT],
        ),
        (  # "no wait" not set off by punctuation is not a cue
            "chat", "There is no wait time for the build.", [],
            [edit("There is no wait time", "time", "self_correction")],
            [], [R.EDIT_REJECTION_REASON_INVARIANT],
        ),
        (  # mislabelled kind: relabelled by the invariant it passes
            "prose", "Also, um, translate it.", [],
            [edit("Also, um, translate", "Also, translate", "punctuation")],
            [("Also, um, translate", "Also, translate", "filler_removal")],
            [],
        ),
        (  # case + punctuation in one quote over an unchanged protected span: split
            "prose", "i think kubectl works", ["kubectl"],
            [edit("i think kubectl works", "I think kubectl works.", "punctuation")],
            [("i think", "I think", "capitalization"), ("works", "works.", "punctuation")],
            [],
        ),
        (  # a rewrite that also changes a word is rejected whole
            "prose", "i think it works", [],
            [edit("i think it works", "I think it fails.", "punctuation")],
            [], [R.EDIT_REJECTION_REASON_INVARIANT],
        ),
        (  # terminal is single-line ...
            "terminal", "Thanks. Next topic.", [],
            [edit("Thanks. Next", "Thanks.\n\nNext", "spacing")],
            [], [R.EDIT_REJECTION_REASON_INVARIANT],
        ),
        (  # ... prose may start a paragraph
            "prose", "Thanks. Next topic.", [],
            [edit("Thanks. Next", "Thanks.\n\nNext", "spacing")],
            [("Thanks. Next", "Thanks.\n\nNext", "spacing")],
            [],
        ),
        (  # valid alone, but the result reads "Sure,, I'll look."
            "chat", "Sure, I'll look.", [],
            [edit("Sure", "Sure,", "punctuation")],
            [], [R.EDIT_REJECTION_REASON_GLOBAL],
        ),
        (  # echoed text (no-ops) does not count towards the >50% invalid rule
            "terminal", "um, run it", [],
            [edit("um, run", "Run", "filler_removal"), edit("it", "it", "punctuation"),
             edit("um", "um", "spacing")],
            [("um, run", "Run", "filler_removal")],
            [R.EDIT_REJECTION_REASON_NO_OP] * 2,
        ),
    ],
)  # fmt: skip
async def test_cleanup_profiles_and_validator(
    cleanup_client: Client,
    fake_llm: FakeLLM,
    profile: str,
    text: str,
    protected: list[str],
    proposed: list[dict[str, str]],
    accepted: list[tuple[str, str, str]],
    reasons: list[pb.EditRejectionReason.ValueType],
):
    fake_llm.respond_edits(proposed)
    spans = [
        pb.ProtectedSpan(byte_start=s, byte_end=e) for s, e in (span(text, p) for p in protected)
    ]
    resp, code, _ = await call(cleanup_client, text=text, profile_id=profile, protected_spans=spans)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    raw = text.encode()
    assert [(e.find, e.replace, KIND_NAMES[e.kind]) for e in resp.edits] == accepted
    assert all(raw[e.byte_start : e.byte_end] == e.find.encode() for e in resp.edits)
    assert all(text.count(e.find) == 1 for e in resp.edits)
    assert [r.reason for r in resp.rejections] == reasons


async def test_cleanup_prompt_is_static_prefix_then_request(
    cleanup_client: Client, fake_llm: FakeLLM
):
    for text in ("first, um, request", "second request"):
        fake_llm.respond_edits([])
        resp, code, _ = await call(
            cleanup_client, text=text, protected_spans=[], profile_id="terminal",
            context=pb.CleanupContext(app_category="terminal"),
        )  # fmt: skip
        assert code == grpc.StatusCode.OK
        assert resp is not None
    first, second = (
        cast("list[dict[str, str]]", dig(r["json"], "messages")) for r in fake_llm.requests
    )
    assert first[:-1] == second[:-1]  # identical prefix: the LLM server's prompt cache reuses it
    assert first[0]["role"] == "system"
    assert first[0]["content"].startswith(POLICY)  # shared by every profile, so first
    assert len(first) > 3  # few-shot turns
    request = cast("object", json.loads(second[-1]["content"]))
    assert dig(request, "text") == "second request"
    assert dig(request, "app") == "terminal"
    kinds = dig(fake_llm.requests[0]["json"], "response_format", "json_schema", "schema",
                "properties", "edits", "items", "properties", "kind", "enum")  # fmt: skip
    assert kinds == [
        "punctuation",
        "capitalization",
        "spacing",
        "filler_removal",
        "self_correction",
    ]


@pytest.mark.parametrize("spec", PROFILES, ids=[spec.id for spec in PROFILES])
def test_few_shot_examples_pass_the_validator(spec: ProfileSpec):
    """A few-shot edit the validator rejects would teach the model to fail."""
    for ex in spec.examples:
        proposed: list[ProposedEdit] = [
            {"find": f, "replace": r, "kind": k, "reason": why} for f, r, k, why in ex.edits
        ]
        edits, rejections = resolve_edits(
            ex.text, proposed, [span(ex.text, p) for p in ex.protected],
            [KINDS[k] for k in spec.kinds], single_line=spec.single_line,
        )  # fmt: skip
        assert list(rejections) == []
        assert [(e.find, e.replace, KIND_NAMES[e.kind]) for e in edits] == [
            (f, r, k) for f, r, k, _ in ex.edits
        ]


@pytest.mark.parametrize("failure", ["slow", "http_500", "malformed"])
async def test_cleanup_upstream_failures(cleanup_client: Client, fake_llm: FakeLLM, failure: str):
    if failure == "slow":
        fake_llm.respond_edits([edit("i think", "I think", "capitalization")], delay=0.5)
        resp, code, _ = await call(cleanup_client, budget_ms=100)
        assert code == grpc.StatusCode.OK
        assert resp is not None
        assert resp.truncated  # fall back to raw text
        assert not resp.edits
        assert resp.total_ms < 400
    elif failure == "http_500":
        fake_llm.respond_raw(500, '{"error": "boom"}')
        _, code, err = await call(cleanup_client)
        assert code == grpc.StatusCode.UNAVAILABLE
        assert err is not None
        assert err.code == pb.ERROR_CODE_BACKEND_FAILURE
        assert err.retryable
    else:
        body = {"choices": [{"message": {"content": "Sure! Here are the edits: ..."}}]}
        fake_llm.respond_raw(200, json.dumps(body))
        _, code, err = await call(cleanup_client)
        assert code == grpc.StatusCode.INTERNAL
        assert err is not None
        assert err.code == pb.ERROR_CODE_BACKEND_FAILURE


@pytest.mark.parametrize(
    ("kw", "code", "error"),
    [
        ({"privacy": pb.PrivacyFlags(allow_context_logging=True)},
         grpc.StatusCode.FAILED_PRECONDITION, pb.ERROR_CODE_PRIVACY_UNSATISFIABLE),
        ({"protected_spans": [pb.ProtectedSpan(byte_start=3, byte_end=5)]},  # inside "ü"
         grpc.StatusCode.INVALID_ARGUMENT, pb.ERROR_CODE_PROTOCOL_VIOLATION),
        ({"profile_id": "nope"}, grpc.StatusCode.INVALID_ARGUMENT, pb.ERROR_CODE_PROTOCOL_VIOLATION),
        ({"text": "x" * 4001}, grpc.StatusCode.INVALID_ARGUMENT, pb.ERROR_CODE_PROTOCOL_VIOLATION),
    ],
)  # fmt: skip
async def test_cleanup_rejects_requests_before_calling_llm(
    cleanup_client: Client,
    fake_llm: FakeLLM,
    kw: CleanupFields,
    code: grpc.StatusCode,
    error: pb.ErrorCode.ValueType,
):
    _, got, err = await call(cleanup_client, **kw)
    assert got == code
    assert err is not None
    assert err.code == error
    if error == pb.ERROR_CODE_PRIVACY_UNSATISFIABLE:
        assert list(err.unavailable_features) == ["privacy.allow_context_logging"]
    assert fake_llm.requests == []


async def test_s1_rewrite_is_aligned_into_validated_edits(s1_client: Client, fake_llm: FakeLLM):
    caps = await s1_client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert pb.EDIT_KIND_NUMBER_FORMAT in caps.cleanup_profiles[0].allowed_kinds
    text = "um, so i need the the report on Tuesday, no wait, on Wednesday"
    rewrite = "So I need the report on Wednesday."
    fake_llm.respond_text(rewrite)

    resp, code, _ = await call(s1_client, text=text, protected_spans=[], profile_id="prose")

    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert list(resp.rejections) == []
    assert apply_edits(text, list(resp.edits)) == rewrite
    assert {KIND_NAMES[e.kind] for e in resp.edits} >= {"filler_removal", "self_correction"}
    assert all(text.count(e.find) == 1 for e in resp.edits)
    (sent,) = fake_llm.requests
    body = sent["json"]
    assert dig(body, "messages") == [
        {"role": "system", "content": S1_SYSTEM},
        {"role": "user", "content": f"{S1_CONTROL}\n{text}"},
    ]
    assert dig(body, "chat_template_kwargs") == {"enable_thinking": False}
    assert dig(body, "temperature") == 0
    assert "response_format" not in cast("dict[str, object]", body)


INV, PROT, DEP, GLOB = (
    R.EDIT_REJECTION_REASON_INVARIANT,
    R.EDIT_REJECTION_REASON_PROTECTED_SPAN,
    R.EDIT_REJECTION_REASON_DEPENDENT,
    R.EDIT_REJECTION_REASON_GLOBAL,
)


@pytest.mark.parametrize(
    ("text", "protected", "rewrite", "result", "reasons"),
    [
        ("move the standup to ten", [], "Move the standup to 10.", "Move the standup to 10.", []),
        (  # wrong value: dropped; the sentence start two words away is kept
            "the total is twenty five", [], "The total is 52.", "The total is twenty five", [INV],
        ),
        (  # a changed word one word from the filler removal drops both
            "um, reply with OK.", [], "Reply with okay.", "um, reply with OK.", [INV, DEP],
        ),
        ("please run kubectl now", ["kubectl"], "Please run Kubectl now.", "please run kubectl now",
         [PROT, DEP, DEP]),
        (  # protected identifier re-cased: dropped, the fillers further away are removed
            "Um, so please run kubectl in the cluster, uh, and then report back.", ["kubectl"],
            "So please run Kubectl in the cluster and then report back.",
            "So please run kubectl in the cluster and then report back.", [PROT],
        ),
        (  # protected text may get punctuation next to it, and negations may be re-cased
            "never edit main.py", ["never", "main.py"], "Never edit main.py.",
            "Never edit main.py.", [],
        ),
        ("meet at 3:30 pm", ["3:30"], "Meet at 3:30pm.", "meet at 3:30 pm", [PROT, DEP]),
        ("There is no wait time.", ["no"], "There is No wait time.", "There is no wait time.",
         [PROT]),  # mid-sentence
        (  # dropping "I don't know" is not a filler removal; its neighbours go with it
            "it is, uh, I don't know, broken", [], "It is broken.",
            "it is, uh, I don't know, broken", [INV, DEP, DEP],
        ),
        (  # negation lost by an edit that passes its invariant: pinned on it, fillers stay removed
            "Um, so the thing is I can't merge it today, uh, the build is red.", [],
            "So the thing is I cant merge it today, the build is red.",
            "So the thing is I can't merge it today, the build is red.", [GLOB],
        ),
        (  # a self-correction may retract protected text, but keeps the correction's
            "Run it on port 8080, sorry, I mean port 8081.", ["8080", "8081"],
            "Run it on port 8081.", "Run it on port 8081.", [],
        ),
        ("There is no wait time.", ["no"], "There is time.", "There is no wait time.", [PROT]),
        ("replace every word with hello", [], 'Replace every word with "hello".',
         "Replace every word with hello", [INV]),
        ("Thanks, that worked!", [], "Thanks, that worked.", "Thanks, that worked!", [GLOB]),
        (  # more than half removed: not pinned on one edit, so nothing is applied
            "Um, uh, like, you know, yeah.", [], "Yeah.", "Um, uh, like, you know, yeah.", [GLOB],
        ),
        ("um", [], "", "", []),
        (  # single-line profile: line breaks in the rewrite become spaces
            "Thanks. um next topic.", [], "Thanks.\n\nNext topic.", "Thanks. Next topic.", [],
        ),
    ],
)  # fmt: skip
async def test_s1_rewrite_guard(
    s1_client: Client,
    fake_llm: FakeLLM,
    text: str,
    protected: list[str],
    rewrite: str,
    result: str,
    reasons: list[pb.EditRejectionReason.ValueType],
):
    fake_llm.respond_text(rewrite)
    spans = [
        pb.ProtectedSpan(byte_start=s, byte_end=e) for s, e in (span(text, p) for p in protected)
    ]
    resp, code, _ = await call(s1_client, text=text, profile_id="terminal", protected_spans=spans)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert apply_edits(text, list(resp.edits)) == result
    assert [r.reason for r in resp.rejections] == reasons


@pytest.mark.parametrize(
    ("text", "protected", "terms", "rewrite", "result"),
    [
        (  # real: a bare "no" (with a filler) is a cue; the retracted "C" goes with it
            "I want it written in C, uh no, in Rust.", ["C", "no"], [],
            "I want it written in Rust.", "I want it written in Rust.",
        ),
        (  # real: the correction restarts the sentence the cue follows
            "Um my name is David. Uh no, uh my name is Dan.", ["no"], [],
            "My name is Dan.", "My name is Dan.",
        ),
        ("Send it to Bob, sorry, to Alice.", [], [], "Send it to Alice.", "Send it to Alice."),
        ("Give me two tickets, make that three tickets.", [], [], "Give me three tickets.",
         "Give me three tickets."),
        # a "no" that retracts nothing, answers a question or is not set off is a negation
        ("No, I don't want C.", ["No", "don't"], [], "I don't want C.", "No, I don't want C."),
        ("Is it done? No, it is not.", ["No", "not"], [], "It is not.",
         "Is it done? No, it is not."),
        ("Ship it, no tests needed.", ["no"], [], "Ship tests needed.", "Ship it, no tests needed."),
        ("Use C not Rust.", ["not"], [], "Use Rust.", "Use C not Rust."),
        ("Do not delete it.", ["not"], [], "Do delete it.", "Do not delete it."),
        (  # bare cue without a correction of the same kind
            "It is, actually, fine.", [], [], "Fine.", "It is, actually, fine.",
        ),
        (  # real: a merge into a known term may change protected words
            "It works like Super Whisper.", ["Super", "Whisper"], ["Superwhisper"],
            "It works like Superwhisper.", "It works like Superwhisper.",
        ),
        (  # ... a protected span's text is a known term too
            "Try open pencil. OpenPencil is fast.", ["OpenPencil"], [],
            "Try OpenPencil. OpenPencil is fast.", "Try OpenPencil. OpenPencil is fast.",
        ),
        # not a known term: protected or not, the merge stays out
        ("It works like Super Whisper.", ["Super", "Whisper"], [], "It works like Superwhisper.",
         "It works like Super Whisper."),
        ("It works like Super Whisper.", [], ["Super Whisper"], "It works like Superwhisper.",
         "It works like Super Whisper."),
        (  # a known term, but the merge also changes a letter
            "It works like Super Whisperer.", [], ["Superwhisper"], "It works like Superwhisper.",
            "It works like Super Whisperer.",
        ),
    ],
)  # fmt: skip
async def test_s1_self_corrections_and_term_merges(
    s1_client: Client,
    fake_llm: FakeLLM,
    text: str,
    protected: list[str],
    terms: list[str],
    rewrite: str,
    result: str,
):
    fake_llm.respond_text(rewrite)
    spans = [
        pb.ProtectedSpan(byte_start=s, byte_end=e) for s, e in (span(text, p) for p in protected)
    ]
    resp, code, _ = await call(
        s1_client, text=text, profile_id="terminal", protected_spans=spans, known_terms=terms
    )
    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert apply_edits(text, list(resp.edits)) == result


REAL = {
    c["id"]: c["text"]
    for line in (DATA / "cleanup_eval_real.jsonl").read_text().splitlines()
    for c in [cast("dict[str, str]", json.loads(line))]
}
# S1-mini q4_k_m's rewrites of two long real dictations (llama.cpp b12284, temperature 0).
REWRITES = {
    "r-filler-1": (
        "As you can see, this further prompt was written just by the thing, but there is one"
        " small little problem, and that is that it was separated into a couple different"
        " toggles. And you can see that the text is not 100% formatted well.\n\nBefore we"
        " continue implementing and iterating over this, I would like to stress test it by"
        " doing whatever you tell me to test it with. I mean, say whatever I need to say and"
        " try out."
    ),
    "r-filler-5": (
        "I would really like Hark to do higher accuracy of prompts and conceptually the only"
        " thing I would like Hark to do is to enable me to express my words freely and then"
        " create a prompt based on my thinking and reasoning and thinking out loud without"
        " using filler words etc. My point is that I would like to reason out loud because"
        " reasoning out loud helps me solve problems and when I reason out loud I feel like"
        " that we come up to better conclusions. The point is not though for Hark to rewrite"
        " what I say, it is just to clean it up, kind of like Superwhisper does with"
        " contextual understanding of the repository and the context and the previous text on"
        " the screen and the agency's replies, etc. And I would like to be able to use it"
        " actually outside of agent terminals. I mean for whatever dictation possible, so"
        " yeah, that's kind of what I want Hark to do."
    ),
}
# What the client protects in them: negations and numbers.
_CLIENT_SPANS = re.compile(r"(?i)\b(?:not|no|never|\w+n't)\b|\d+")


@pytest.mark.parametrize(
    ("case", "accepted", "rejected"),
    [
        (
            "r-filler-1",
            [("that uh", "that"), ("mean", "mean,")],
            [("different uh I don't know like", PROT)],
        ),
        (
            "r-filler-5",
            [("and like", "and"), ("HARK", "Hark"), ("loud you know", "loud"), ("not uh", "not"),
             ("the like", "the"), ("hark", "Hark")],
            [("like if um", INV), ("could", INV), ("words etc", INV),
             ("we I don't know we we I", PROT), ("super whispered", INV), ("previous the", INV),
             ("replies, etc.", INV), ("do like", DEP)],  # "Hark could do like": next to "could"
        ),
    ],
)  # fmt: skip
async def test_s1_long_dictation_keeps_the_edits_that_pass(
    s1_client: Client,
    fake_llm: FakeLLM,
    case: str,
    accepted: list[tuple[str, str]],
    rejected: list[tuple[str, pb.EditRejectionReason.ValueType]],
):
    """All or nothing kept every filler here: one edit in each rewrite cannot be verified."""
    text = REAL[case]
    fake_llm.respond_text(REWRITES[case])
    spans = [
        pb.ProtectedSpan(
            byte_start=len(text[: m.start()].encode()), byte_end=len(text[: m.end()].encode())
        )
        for m in _CLIENT_SPANS.finditer(text)
    ]
    resp, code, _ = await call(s1_client, text=text, profile_id="terminal", protected_spans=spans)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert [(e.find, e.replace) for e in resp.edits] == accepted
    assert [(r.find, r.reason) for r in resp.rejections] == rejected


GROUPED = "so the build fails on the linker step uh can you check it"


@pytest.mark.parametrize(
    ("proposed", "result", "reasons"),
    [
        (  # adjacent: dropping "step uh" -> "stage." must not leave "step uh Can"
            [("step uh", "stage."), ("can", "Can")], GROUPED, [INV, DEP],
        ),
        (  # one word between still links; groups chain
            [("on the", "in the"), ("step uh", "step."), ("can", "Can")], GROUPED, [INV, DEP, DEP],
        ),
        (  # two words between: independent
            [("fails on", "failed on"), ("step uh", "step."), ("can", "Can")],
            "so the build fails on the linker step. Can you check it", [INV],
        ),
        (  # a group far from the failure is kept
            [("so the", "So the"), ("step uh", "stage."), ("can", "Can")],
            "So the build fails on the linker step uh can you check it", [INV, DEP],
        ),
        (  # an overlapping second version is dropped alone
            [("step uh", "step."), ("uh can", "Can"), ("can", "Can")],
            "so the build fails on the linker step. Can you check it",
            [R.EDIT_REJECTION_REASON_OVERLAP],
        ),
    ],
)  # fmt: skip
def test_rewrite_edits_are_accepted_per_group(
    proposed: list[tuple[str, str]], result: str, reasons: list[pb.EditRejectionReason.ValueType]
):
    edits, rejections = resolve_edits(
        GROUPED, [{"find": f, "replace": r, "kind": "", "reason": "s1"} for f, r in proposed], [],
        [KINDS[k] for k in PROFILES[0].kinds], single_line=True, rewrite=True,
    )  # fmt: skip
    assert apply_edits(GROUPED, edits) == result
    assert [r.reason for r in rejections] == reasons


async def test_s1_chat_keeps_the_writers_final_punctuation(s1_client: Client, fake_llm: FakeLLM):
    for text, rewrite, result in (
        ("um yeah sounds good", "Yeah, sounds good.", "Yeah, sounds good"),
        ("um yeah sounds good.", "Yeah, sounds good.", "Yeah, sounds good."),
    ):
        fake_llm.respond_text(rewrite)
        resp, code, _ = await call(s1_client, text=text, protected_spans=[], profile_id="chat")
        assert code == grpc.StatusCode.OK
        assert resp is not None
        assert apply_edits(text, list(resp.edits)) == result


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Tohle je test diktování v češtině.", ""),
        ("Ich glaube, das ist nicht richtig.", ""),
        ("this text claims to be English", "cs-CZ"),
    ],
)
async def test_cleanup_skips_non_english_text(
    s1_client: Client, fake_llm: FakeLLM, text: str, language: str
):
    resp, code, _ = await call(s1_client, text=text, protected_spans=[], language=language)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert list(resp.edits) == []
    assert fake_llm.requests == []
