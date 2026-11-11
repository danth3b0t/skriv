"""TextCleanup proxy against a fake OpenAI-compatible /v1/chat/completions."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, TypedDict, Unpack, cast

import grpc
import pytest
from conftest import dig

from hark.v1 import hark_pb2 as pb

if TYPE_CHECKING:
    from collections.abc import Iterator

    from conftest import Client, Serve

R = pb.EditRejectionReason
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
    return await serve(
        cleanup={"base_url": fake_llm.url, "model": "fake-llm", "default_budget_ms": 2000}
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
    assert [p.id for p in caps.cleanup_profiles] == ["prose"]

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
