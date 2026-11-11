"""Terminal-profile spoken symbols: what rewrites and what must stay prose."""

from __future__ import annotations

import pytest

from hark_client import symbols


@pytest.mark.parametrize(
    ("spoken", "written"),
    [
        # from real dictations (stress history)
        (
            "Rename it in canon dot py and update the tests.",
            "Rename it in canon.py and update the tests.",
        ),
        ("Rename it in canon.by and update.", "Rename it in canon.py and update."),
        (
            "Git commit dash m fix colon handle empty audio.",
            "git commit -m fix: handle empty audio.",
        ),
        ("Git commit-m fix colon handle empty audio.", "git commit -m fix: handle empty audio."),
        # extensions
        ("update the config dot jason file", "update the config.json file"),
        ("Open Safari dot app", "Open Safari.app"),
        ("cat dot env", "cat .env"),
        # flags
        ("Run pytest dash dash verbose.", "Run pytest --verbose."),
        ("cargo build dash dash release", "cargo build --release"),
        ("ls dash l dash a", "ls -l -a"),
        # paths
        ("Open src slash hark slash main dot rs please.", "Open src/hark/main.rs please."),
        ("cd slash etc slash hosts", "cd /etc/hosts"),
        ("cd slash tmp", "cd /tmp"),
        ("slash compact", "/compact"),
        ("cd dot dot", "cd .."),
        ("dot slash run dot sh", "./run.sh"),
        ("dot dot slash scripts slash poc-up", "../scripts/poc-up"),
        # stays prose
        ("Use the slash command to see help.", "Use the slash command to see help."),
        ("Look at docs slash bench.", "Look at docs slash bench."),
        ("I want a dash of salt.", "I want a dash of salt."),
        ("Add a dash m here", "Add a dash m here"),  # "a" is not a command
        ("The dot is red.", "The dot is red."),
        ("canon, dot py", "canon, dot py"),  # punctuation separates
        ("what time is it dash 5", "what time is it dash 5"),
        ("colon", "colon"),
        ("", ""),
    ],
)
def test_spoken_symbols(spoken: str, written: str):
    assert symbols.apply(spoken)[0] == written
