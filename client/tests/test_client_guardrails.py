import pytest

from skriv_client import guardrails


@pytest.mark.parametrize(
    ("text", "terminal", "expected"),
    [
        # Bracketed-paste breakout: the end marker and the newline must not survive.
        ("ls -la\x1b[201~\nrm -rf ~\n", True, "ls -la rm -rf ~"),
        ("git status\r\n", True, "git status"),  # never a trailing Enter
        ("one\n\ntwo\tthree\u2028four", True, "one two three four"),
        ("echo \x1b]0;title\x07hi\x1b[31m!\x1b[0m", True, "echo hi!"),
        ("say \x9b201~hi\x00\x07", True, "say hi"),
        ("pay \u202eadmin\u202c now", True, "pay admin now"),  # bidi override
        ("Dear team,\r\nthanks \x1b[1m!\x1b[0m\x08  \n\n", False, "Dear team,\nthanks !"),
        ("a\tb\nc  ", False, "a\tb\nc"),
        ("\x1b[200~\x1b[201~", False, ""),
    ],
)
def test_guardrails(text: str, terminal: bool, expected: str):
    out, _ = guardrails.apply(text, terminal=terminal)
    assert out == expected


def test_guardrail_events_name_what_changed():
    _, events = guardrails.apply("a\x1b[201~\nb\x07", terminal=True)
    assert events == ["stripped_escape_sequences", "stripped_control_chars", "collapsed_newlines"]
    assert guardrails.apply("plain text", terminal=True) == ("plain text", [])


@pytest.mark.parametrize(
    ("text", "refused"),
    [
        ("<unk>" * 60, True),
        ("<unk> <unk> <unk>.", True),
        ("\u00cc" * 23, True),  # baseline syn-005, boosted decode
        ("ha " * 12, True),
        ("ab" * 12 + ".", True),
        ("Get status.", False),
        ("Get <unk> status now.", False),  # one unknown token inside real text
        ("no no no no", False),
        ("\u00cc" * 11, False),
        ("set the mask to ffffffff", False),
        ("ffffffff", False),
        ("Yes.", False),
        ("", False),
    ],
)
def test_degenerate_recognizer_output(text: str, refused: bool):
    assert bool(guardrails.degenerate(text)) is refused
