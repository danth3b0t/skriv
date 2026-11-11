"""Correction learning: what counts as a correction, the store, and the post-paste watch."""

from __future__ import annotations

import json
import stat
import threading
from typing import TYPE_CHECKING, cast

import pytest

from hark_client.__main__ import main
from hark_client.context import FocusInfo, Term, TermKind, TermSource
from hark_client.fields import FieldText
from hark_client.learn import Anchor, LearnedStore, Learner, corrections, merge_terms

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("pasted", "edited", "pairs"),
    [
        ("run cube control get pods", "run kubectl get pods", [("cube control", "kubectl")]),
        ("open it in pie charm.", "open it in PyCharm.", [("pie charm", "PyCharm")]),
        ("tell Kerry about it", "tell Carrie about it", [("Kerry", "Carrie")]),
        ("push it to github now", "push it to GitHub now", [("github", "GitHub")]),
        ("I use hyper land daily", "I use Hyprland daily and more", [("hyper land", "Hyprland")]),
        # not corrections:
        ("The meeting is at noon", "the meeting is at noon", []),  # sentence case
        ("a big change", "a large change", []),  # style rewrite: not alike
        ("we can ship it", "we cannot ship it", []),  # negation
        ("put it their", "put it there", []),  # into ordinary English
        ("send it", "send it to Bob tomorrow", []),  # appended
        ("one two three four", "five six seven eight", []),  # rewrite
        ("deploy with token", "deploy with sk-abcdefghijklmnopqrstu", []),  # secret
        ("ok", "", []),
    ],
)
def test_corrections(pasted: str, edited: str, pairs: list[tuple[str, str]]):
    assert corrections(pasted, edited) == pairs


def test_anchor_tracks_the_pasted_span_until_the_text_around_it_changes():
    anchor = Anchor.find("Hi Ann, cube control rocks. Bye", " cube control rocks.")
    assert anchor is not None
    assert anchor.span("Hi Ann, kubectl rocks. Bye") == "kubectl rocks."
    assert anchor.span("Hi Ann, cube control rocks. Bye") == "cube control rocks."
    assert anchor.span("") is None  # sent / cleared
    assert anchor.span("Hello Ann, kubectl rocks. Bye") is None  # edited outside the span
    assert Anchor.find("go go", "go") is None  # ambiguous
    assert Anchor.find("nothing here", "go") is None


def test_store_counts_promotes_after_two_and_persists_only_pairs(tmp_path: Path):
    store = LearnedStore(tmp_path / "hark" / "learned.json", promote_after=2)
    assert store.terms() == ()
    assert store.record([("cube control", "kubectl")]) == []
    assert store.terms() == ()  # seen once: not used yet
    promoted = store.record([("Cube Control", "kubectl"), ("pie charm", "PyCharm")])
    assert [(e.heard, e.written, e.count) for e in promoted] == [("cube control", "kubectl", 2)]
    (term,) = store.terms()
    assert (term.canonical, term.source, term.weight) == ("kubectl", TermSource.LEARNED, 1.0)
    assert term.spoken[0] == "cube control"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    data = cast("dict[str, list[dict[str, object]]]", json.loads(store.path.read_text()))
    assert data["version"] == 1
    assert {tuple(sorted(e)) for e in data["entries"]} == {
        ("count", "first", "heard", "last", "written")
    }
    # a fresh store (another process) sees the same file
    assert LearnedStore(store.path).terms() == (term,)
    assert store.forget("KUBECTL") == 1
    assert store.terms() == ()
    assert store.forget("nothing") == 0
    assert store.clear() == 1
    assert not store.path.exists()


def test_store_ignores_a_corrupt_file(tmp_path: Path):
    path = tmp_path / "learned.json"
    path.write_text('{"version": 1, "entries": [{"heard": 3}, "x", {"heard": "a b"}]')
    assert LearnedStore(path).entries() == []
    path.write_text('{"version": 1, "entries": [{"heard": 3}, "x"]}')
    assert LearnedStore(path).entries() == []


def test_learned_terms_lead_the_shortlist_and_share_aliases():
    ctx = (
        Term("kubectl", ("kube control",), 0.7, TermKind.COMMAND, TermSource.TERMINAL_TEXT),
        Term("pods", ("pods",), 0.5, TermKind.WORD, TermSource.TERMINAL_TEXT),
    )
    learned = (Term("kubectl", ("cube control",), 1.0, TermKind.WORD, TermSource.LEARNED),)
    merged = merge_terms(ctx, learned, max_terms=2)
    assert [t.canonical for t in merged] == ["kubectl", "pods"]
    assert merged[0].spoken == ("cube control", "kube control")
    assert merged[0].source == TermSource.LEARNED
    assert merge_terms(ctx, learned, max_terms=1) == merged[:1]
    assert merge_terms(ctx, (), max_terms=5) == ctx


class Field:
    """A text field the 'user' edits while the learner polls it."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.kind: str = "text"
        self.reads = 0
        self.polled = threading.Event()

    def read(self, focus: FocusInfo) -> FieldText:
        self.reads += 1
        self.polled.set()
        return FieldText("text", self.value) if self.kind == "text" else FieldText("secure")


TARGET = FocusInfo(app="Notes", pid=1, window_id="1:7")


def make_learner(tmp_path: Path, field: Field, window_s: float = 5.0) -> Learner:
    store = LearnedStore(tmp_path / "learned.json")
    return Learner(store, field, window_s=window_s, poll_s=0.01)


def edit_then(field: Field, value: str) -> None:
    field.polled.clear()
    field.polled.wait(2)
    field.value = value
    field.polled.clear()
    field.polled.wait(2)
    field.polled.clear()
    field.polled.wait(2)


def test_watch_learns_from_an_edit_in_the_same_window(tmp_path: Path):
    field = Field("Note: cube control get pods")
    learner = make_learner(tmp_path, field)
    assert learner.after_paste(TARGET, "cube control get pods", field.value, lambda: "1:7")
    edit_then(field, "Note: kubectl get pods")
    learner.cancel()  # next dictation
    learner.join(2)
    assert [(e.heard, e.written) for e in learner.store.entries()] == [("cube control", "kubectl")]


@pytest.mark.parametrize("ends", ["focus", "secure", "sent", "window"])
def test_watch_ends_without_learning(tmp_path: Path, ends: str):
    field = Field("cube control get pods")
    focus = {"window": "1:7"}
    learner = make_learner(tmp_path, field, window_s=0.0 if ends == "window" else 5.0)
    learner.after_paste(TARGET, "cube control get pods", field.value, lambda: focus["window"])
    if ends == "focus":
        focus["window"] = "9:9"  # the user switched windows, then edits elsewhere
    elif ends == "secure":
        field.kind = "secure"
    elif ends == "sent":
        field.value = ""
    learner.join(2)
    assert not learner.watching
    field.value = "kubectl get pods"
    assert learner.store.entries() == []
    assert not learner.store.path.exists()


def test_watch_needs_an_unambiguous_span_and_a_window_id(tmp_path: Path):
    field = Field("go go")
    learner = make_learner(tmp_path, field)
    assert not learner.after_paste(TARGET, "go", field.value, lambda: "1:7")
    assert not learner.after_paste(FocusInfo(pid=1), "go go", field.value, lambda: "")
    assert field.reads == 0


def test_hark_learned_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[history]\ndir = "{tmp_path}"\n')
    store = LearnedStore(tmp_path / "learned.json")
    store.record([("cube control", "kubectl"), ("pie charm", "PyCharm")])
    store.record([("cube control", "kubectl")])
    assert main(["--config", str(cfg), "learned"]) == 0
    out = capsys.readouterr().out
    assert "*   2  'cube control' -> 'kubectl'" in out
    assert "    1  'pie charm' -> 'PyCharm'" in out
    assert main(["--config", str(cfg), "learned", "forget"]) == 2
    assert main(["--config", str(cfg), "learned", "forget", "nope"]) == 1
    assert main(["--config", str(cfg), "learned", "forget", "pie charm"]) == 0
    assert main(["--config", str(cfg), "learned", "clear"]) == 0
    assert store.entries() == []
