"""TextCleanup (S1-mini or instruct-model rewrite) against a fake OpenAI-compatible
/v1/chat/completions."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, TypedDict, Unpack, cast

import grpc
import pytest
from conftest import dig

from skriv.v1 import skriv_pb2 as pb
from skriv_server import instruct
from skriv_server.cleanup import S1_CONTROL, S1_SYSTEM

if TYPE_CHECKING:
    from collections.abc import Iterator

    from conftest import Client, Serve

TEXT = "um, so i think the build is not green"


class Recorded(TypedDict):
    path: str
    json: object


class CleanupFields(TypedDict, total=False):
    """CleanupRequest fields the tests set."""

    text: str
    profile_id: str
    budget_ms: int
    privacy: pb.PrivacyFlags
    language: str
    terms: list[pb.BiasTerm]
    prime: bool


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

    def respond_text(self, content: str, delay: float = 0.0, finish: str = "stop") -> None:
        body = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
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
async def client(serve: Serve, fake_llm: FakeLLM) -> Client:
    return await serve(
        cleanup={"base_url": fake_llm.url, "model": "s1-mini", "default_budget_ms": 2000}
    )


async def call(
    client: Client, **kw: Unpack[CleanupFields]
) -> tuple[pb.CleanupResponse | None, grpc.StatusCode, pb.ErrorDetail | None]:
    kw.setdefault("text", TEXT)
    return await client.unary(client.cleanup.Cleanup, pb.CleanupRequest(utterance_id="u-c", **kw))


async def test_cleanup_returns_the_s1_rewrite(client: Client, fake_llm: FakeLLM):
    caps = await client.asr.GetCapabilities(pb.GetCapabilitiesRequest())
    assert "skriv.cleanup" in caps.features
    assert [p.id for p in caps.cleanup_profiles] == ["terminal", "chat", "prose"]
    fake_llm.respond_text("So I think the build is not green.")

    resp, code, _ = await call(client, profile_id="prose")

    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert resp.HasField("text")
    assert (resp.text, resp.model, resp.truncated) == (
        "So I think the build is not green.", "s1-mini", False,
    )  # fmt: skip
    (sent,) = fake_llm.requests
    assert sent["path"] == "/v1/chat/completions"
    body = sent["json"]
    assert dig(body, "messages") == [  # the exact training format
        {"role": "system", "content": S1_SYSTEM},
        {"role": "user", "content": f"{S1_CONTROL}\n{TEXT}"},
    ]
    assert dig(body, "chat_template_kwargs") == {"enable_thinking": False}
    assert dig(body, "temperature") == 0


@pytest.mark.parametrize(
    ("profile", "text", "rewrite", "result"),
    [
        # leftover hesitations and a "you know" set off by commas go, with their commas
        ("prose", "In the loop, eh, move it, you know, before the pass.",
         "In the loop, eh, move it, you know, before the pass.",
         "In the loop, move it, before the pass."),
        ("prose", "Uh, add a test.", "uh, add a test.", "Add a test."),
        ("prose", "Cut it to 5 mm please", "Cut it to 5 mm, please.", "Cut it to 5 mm, please."),
        ("prose", "do you know why", "Do you know why?", "Do you know why?"),
        # one line in terminal and chat; prose keeps paragraphs
        ("terminal", "first this then that", "First this.\nThen that.", "First this. Then that"),
        ("prose", "Hi Tom thanks", "Hi Tom,\n\nThanks.", "Hi Tom,\n\nThanks."),
        # no final period the input did not have, outside prose
        ("terminal", "git commit --amend", "git commit --amend.", "git commit --amend"),
        ("chat", "sounds good.", "Sounds good.", "Sounds good."),
        ("chat", "can you check", "Can you check?", "Can you check?"),
        # a flattened "!" comes back
        ("chat", "that worked!", "That worked.", "That worked!"),
    ],
)  # fmt: skip
async def test_profiles_finish_the_rewrite(
    client: Client, fake_llm: FakeLLM, profile: str, text: str, rewrite: str, result: str
):
    fake_llm.respond_text(rewrite)
    resp, code, _ = await call(client, text=text, profile_id=profile)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert resp.text == result


@pytest.mark.parametrize("failure", ["slow", "max_tokens", "http_500", "malformed"])
async def test_cleanup_upstream_failures(client: Client, fake_llm: FakeLLM, failure: str):
    if failure in ("slow", "max_tokens"):
        slow = failure == "slow"
        fake_llm.respond_text("So.", delay=0.5 if slow else 0, finish="stop" if slow else "length")
        resp, code, _ = await call(client, budget_ms=100 if slow else 2000)
        assert code == grpc.StatusCode.OK
        assert resp is not None
        assert not resp.HasField("text")  # the client keeps its text
        assert (resp.truncated, resp.skipped) == ((True, "") if slow else (False, "max_tokens"))
        assert resp.total_ms < 400
    elif failure == "http_500":
        fake_llm.respond_raw(500, '{"error": "boom"}')
        _, code, err = await call(client)
        assert code == grpc.StatusCode.UNAVAILABLE
        assert err is not None
        assert err.code == pb.ERROR_CODE_BACKEND_FAILURE
        assert err.retryable
    else:
        fake_llm.respond_raw(200, json.dumps({"choices": [{"message": {"content": None}}]}))
        _, code, err = await call(client)
        assert code == grpc.StatusCode.INTERNAL
        assert err is not None
        assert err.code == pb.ERROR_CODE_BACKEND_FAILURE


@pytest.mark.parametrize(
    ("kw", "code", "error"),
    [
        ({"privacy": pb.PrivacyFlags(allow_context_logging=True)},
         grpc.StatusCode.FAILED_PRECONDITION, pb.ERROR_CODE_PRIVACY_UNSATISFIABLE),
        ({"profile_id": "nope"}, grpc.StatusCode.INVALID_ARGUMENT, pb.ERROR_CODE_PROTOCOL_VIOLATION),
        ({"text": "x" * 4001}, grpc.StatusCode.INVALID_ARGUMENT, pb.ERROR_CODE_PROTOCOL_VIOLATION),
    ],
)  # fmt: skip
async def test_cleanup_rejects_requests_before_calling_llm(
    client: Client,
    fake_llm: FakeLLM,
    kw: CleanupFields,
    code: grpc.StatusCode,
    error: pb.ErrorCode.ValueType,
):
    _, got, err = await call(client, **kw)
    assert got == code
    assert err is not None
    assert err.code == error
    if error == pb.ERROR_CODE_PRIVACY_UNSATISFIABLE:
        assert list(err.unavailable_features) == ["privacy.allow_context_logging"]
    assert fake_llm.requests == []


@pytest.mark.parametrize(
    ("text", "language", "skipped"),
    [
        ("Tohle je test diktování v češtině.", "", "not_english"),
        ("Ich glaube, das ist nicht richtig.", "", "not_english"),
        ("this text claims to be English", "cs-CZ", "not_english"),
        ("   ", "", "empty"),
    ],
)
async def test_cleanup_skips_without_calling_llm(
    client: Client, fake_llm: FakeLLM, text: str, language: str, skipped: str
):
    resp, code, _ = await call(client, text=text, language=language)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert (resp.HasField("text"), resp.skipped) == (False, skipped)
    assert fake_llm.requests == []


async def test_english_text_with_accents_is_cleaned(client: Client, fake_llm: FakeLLM):
    fake_llm.respond_text("Grüße! I think the café on Main Street is fine.")
    resp, _, _ = await call(client, text="Grüße! I think the café on main street is fine")
    assert resp is not None
    assert resp.text == "Grüße! I think the café on Main Street is fine."


async def test_s1_needs_no_priming(client: Client, fake_llm: FakeLLM):
    resp, code, _ = await call(client, text="", prime=True)
    assert code == grpc.StatusCode.OK
    assert resp is not None
    assert (resp.HasField("text"), resp.skipped) == (False, "primed")
    assert fake_llm.requests == []


async def test_instruct_model_gets_the_shortlist_and_is_primed_with_the_same_prefix(
    serve: Serve, fake_llm: FakeLLM
):
    client = await serve(
        cleanup={"base_url": fake_llm.url, "model": "qwen3-4b-instruct-2507-q4_k_m"}
    )
    terms = [
        pb.BiasTerm(id="t0", canonical="kubectl", aliases=[
            pb.SpokenAlias(text="cube control"), pb.SpokenAlias(text="kubectl"),
            pb.SpokenAlias(text="kubectl get"), pb.SpokenAlias(text="kube ctl"),
            pb.SpokenAlias(text="cube cuttle"),
        ]),
        pb.BiasTerm(id="t1", canonical="README.md"),
    ]  # fmt: skip
    fake_llm.respond_text(".")  # the priming answer, discarded
    fake_llm.respond_text("Run kubectl get pods.")

    primed, _, _ = await call(client, text="", profile_id="terminal", terms=terms, prime=True)
    resp, _, _ = await call(
        client, text="um run cube control get pods", profile_id="terminal", terms=terms
    )

    assert primed is not None
    assert (primed.HasField("text"), primed.skipped) == (False, "primed")
    assert resp is not None
    assert resp.text == "Run kubectl get pods"  # the terminal profile's finish
    prime, rewrite = (cast("list[dict[str, str]]", dig(r["json"], "messages")) for r in
                      fake_llm.requests)  # fmt: skip
    assert prime[0] == {"role": "system", "content": instruct.SYSTEM}
    assert len(prime) == len(rewrite) == 2 + 2 * len(instruct.FEWSHOTS)
    assert prime[:-1] == rewrite[:-1]
    # Spoken forms with spaces only, at most three; the transcript comes last.
    assert rewrite[-1]["content"] == (
        "App: terminal (coding-agent prompt)\n"
        "Shortlist:\n- kubectl (said: cube control | kubectl get | kube ctl)\n- README.md\n"
        "Transcript: um run cube control get pods"
    )
    assert rewrite[-1]["content"].startswith(prime[-1]["content"])
    assert dig(fake_llm.requests[0]["json"], "max_tokens") == 1
