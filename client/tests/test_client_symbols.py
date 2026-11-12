"""Terminal-profile spoken symbols: what rewrites and what must stay prose."""

from __future__ import annotations

import pytest

from skriv_client import symbols


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
        # recognizer-joined flags, flags after a command said earlier in the sentence
        ("Docker compose up-d, then go.", "docker compose up -d, then go."),
        ("docker compose up dash d", "docker compose up -d"),
        ("uv run pytest-k gate", "uv run pytest -k gate"),
        ("Run ruff check with dash-fix.", "Run ruff check with --fix."),
        ("Run rough check with dash-dash fix", "Run rough check with --fix"),
        # host:port, leading-slash paths, chmod, underscores
        ("curl localhost colon 8000 slash health", "curl localhost:8000/health"),
        ("curl localhost:8000 slash health", "curl localhost:8000/health"),
        ("set it to slash data slash models", "set it to /data/models"),
        ("chmod plus x run dot sh", "chmod +x run.sh"),
        ("index user underscore id now", "index user_id now"),
        # numbers
        ("Close issue four twenty-one and PR four thirty seven.", "Close issue 421 and PR 437."),
        ("open port eight zero eight zero", "open port 8080"),
        ("on port fifty thousand fifty one", "on port 50051"),
        ("set it to zero comma one", "set it to 0,1"),
        ("set it to 0, comma one", "set it to 0,1"),
        # a command line starts in lower case
        ("Git status.", "git status."),
        ("Hark history", "hark history"),
        # the recognizer's "Get" for git, at the start of a command line
        ("Get status.", "git status."),
        ("Get push dash dash force, but not main.", "git push --force, but not main."),
        ("get pull origin main", "git pull origin main"),
        ("Get commit messages from last week.", "Get commit messages from last week."),
        ("Get status of the build.", "Get status of the build."),
        ("Then get status.", "Then get status."),  # not at the start
        ("Get the status.", "Get the status."),
        # stays prose
        ("Use the slash command to see help.", "Use the slash command to see help."),
        ("Look at docs slash bench.", "Look at docs slash bench."),
        ("I want a dash of salt.", "I want a dash of salt."),
        ("Add a dash m here", "Add a dash m here"),  # "a" is not a command
        ("The dot is red.", "The dot is red."),
        ("canon, dot py", "canon, dot py"),  # punctuation separates
        ("what time is it dash 5", "what time is it dash 5"),
        ("colon", "colon"),
        ("Make sure it works.", "Make sure it works."),
        ("Docker is a T-shirt company.", "Docker is a T-shirt company."),
        ("write it with an underscore.", "write it with an underscore."),
        ("the underscore key", "the underscore key"),
        ("go to slash tmp", "go to slash tmp"),
        ("add plus x", "add plus x"),
        ("one issue at a time", "one issue at a time"),
        ("add a test with dash-bos and up-d", "add a test with dash-bos and up-d"),  # "add": a word
        ("", ""),
    ],
)
def test_spoken_symbols(spoken: str, written: str):
    assert symbols.apply(spoken)[0] == written


@pytest.mark.parametrize(
    ("spoken", "written"),
    [
        ("Run Rough Check with dash-fix, then go.", "Run Rough Check with --fix, then go."),
        ("Pass dash-v here.", "Pass -v here."),
        ("I want a dash-free text.", "I want a dash-free text."),  # not on the shortlist
    ],
)
def test_joined_dash_takes_a_shortlist_flag_without_a_command(spoken: str, written: str):
    assert symbols.apply(spoken, ["--fix", "-v"])[0] == written
