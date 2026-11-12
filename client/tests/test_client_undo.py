"""`hark ctl undo` / `swap-raw`: delete or replace hark's newest paste, never a guess."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from hark_client.context import CaretContext
from hark_client.continuation import CLICK, backspaces

from .conftest import RecordingFeedback, pcm, snapshot, wait_idle
from .test_client_daemon import dictate, typed

if TYPE_CHECKING:
    from hark_client.daemon import Controller
    from hark_client.inject import DryRunInjector

    from .conftest import FakeContext, FakeLLM


@pytest.mark.parametrize(
    ("text", "presses"),
    [
        ("please run", 10),
        ("café", 4),  # precomposed é: one character
        ("a\nb", 3),
        ("日本語 👍", 5),  # one code point each, astral or not
        ("cafe\u0301", None),  # e + combining acute: one or two presses, depending on the app
        ("👍🏽", None),  # emoji + skin tone
        ("👩\u200d💻", None),  # ZWJ sequence
        ("🇩🇪", None),  # flag = two regional indicators
        ("\u1112\u1161\u11ab", None),  # conjoining Hangul jamo
        ("a\r\nb", None),
    ],
)
def test_backspace_count(text: str, presses: int | None):
    assert backspaces(text) == presses


def test_undo_deletes_exactly_the_newest_paste(controller: tuple[Controller, DryRunInjector]):
    c, injector = controller
    c.keys_observed = True
    dictate(c)
    dictate(c)  # joined: " please run"
    assert c.handle("undo") == "ok removed 11 chars"
    assert injector.backspaced == [11]
    typed(c, *["backspace"] * 11)  # the listener sees hark's own Backspaces: not the user's
    assert c.handle("undo").startswith("error no dictation to undo")  # once only
    dictate(c)  # the shadow still knows "please run" before the caret
    assert [text for text, _ in injector.pasted] == ["please run", " please run", " please run"]
    assert injector.backspaced == [11]


def test_undo_refuses_unless_the_caret_is_known_to_follow_the_paste(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, injector = controller
    feedback = c.svc.feedback
    assert isinstance(feedback, RecordingFeedback)
    dictate(c)
    assert c.handle("undo").startswith("error needs the hotkey listener")  # no key stream
    c.keys_observed = True
    assert c.handle("swap-raw").startswith("error no other version")  # cleanup changed nothing
    typed(c, "x")
    assert c.handle("undo").startswith("error the caret may have moved")
    dictate(c)
    c.user_input(CLICK)
    assert c.handle("undo").startswith("error the caret may have moved")
    dictate(c)
    fake_context.caret = CaretContext("echo please", "", "ax", trusted=True)
    assert c.handle("undo") == "error the text before the caret is not hark's last paste"
    fake_context.caret = CaretContext()
    fake_context.snapshots = [snapshot("kitty", "w2")]  # another window has focus now
    assert c.handle("undo").startswith("error the caret may have moved")
    c.handle("start")
    assert c.handle("undo") == "error a dictation is in progress"
    c.handle("cancel")
    assert c.handle("undo").startswith("error no dictation")  # a newer dictation pasted nothing
    assert injector.backspaced == []
    assert len([n for n in feedback.notifications if n.startswith("Nothing")]) == 8


def test_swap_raw_replaces_the_cleaned_text_and_back(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext, fake_llm: FakeLLM
):
    c, injector = controller
    c.keys_observed = True
    fake_context.snapshots = [snapshot("com.apple.Notes")]
    c.svc.caps.refresh()  # learns the server's cleanup profiles
    fake_llm.respond("Please run kubectl get")
    c.handle("start")
    c.on_pcm(pcm(1.0))
    c.handle("stop")
    wait_idle(c)
    assert c.handle("swap-raw") == "ok swapped 22 for 22 chars"
    typed(c, *["backspace"] * 22, "v")  # hark's Backspaces and its paste chord (flags only)
    assert c.handle("swap-raw") == "ok swapped 22 for 22 chars"
    assert injector.backspaced == [22, 22]
    assert [text for text, _ in injector.pasted] == [
        "Please run kubectl get",
        "please run kubectl get",  # before cleanup
        "Please run kubectl get",
    ]
