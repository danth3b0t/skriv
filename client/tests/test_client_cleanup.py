"""Protected spans and the client-side (authoritative) edit validator."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import grpc
import pytest

from hark.v1 import hark_pb2 as pb
from hark.v1 import hark_pb2_grpc as pb_grpc
from hark_client import cleanup

from .conftest import KUBECTL

if TYPE_CHECKING:
    from collections.abc import Iterator

K = pb.EditKind
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
    with pytest.raises(ValueError, match=error):
        cleanup.validate_edits(TEXT, [edit], cleanup.protected_spans(TEXT, [KUBECTL]))


def test_validator_applies_valid_edits_and_checks_negations():
    text = "so um I think it is not done"
    spans = cleanup.protected_spans(text, [])
    ok = [
        make_edit("so um I", "so I", K.EDIT_KIND_FILLER_REMOVAL, text),
        make_edit("done", "done.", K.EDIT_KIND_PUNCTUATION, text),
    ]
    assert cleanup.validate_edits(text, ok, spans) == "so I think it is not done."
    # Without protection a punctuation edit could still drop a negation: caught globally.
    with pytest.raises(ValueError, match="negation"):
        cleanup.validate_edits(
            "I can't go", [make_edit("can't", "cant", K.EDIT_KIND_PUNCTUATION, "I can't go")], []
        )


class BadCleanup(pb_grpc.TextCleanupServicer):
    """A server whose validator let an edit of a protected identifier through."""

    def Cleanup(
        self, request: pb.CleanupRequest, context: grpc.ServicerContext
    ) -> pb.CleanupResponse:
        return pb.CleanupResponse(
            edits=[make_edit("kubectl", "Kubectl", K.EDIT_KIND_CAPITALIZATION, request.text)]
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


def test_protected_span_violation_falls_back_to_input(bad_cleanup_channel: grpc.Channel):
    out = cleanup.run(
        bad_cleanup_channel, text="please run kubectl get", terms=[KUBECTL], utterance_id="u1",
        profile_id="prose", app_category="prose", language="", deadline_ms=2000,
    )  # fmt: skip
    assert (out.text, out.status) == ("please run kubectl get", "rejected")
    assert "protected span" in out.reason
