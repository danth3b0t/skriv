import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from hark_client.context.focus import parse_hyprctl
from hark_client.context.terminal import TmuxPane, detect_agent, parse_tmux_clients, resolve


@pytest.mark.parametrize(
    ("cmdline", "agent"),
    [
        (["pi"], "pi"),
        (["node", "/usr/lib/node_modules/@earendil-works/pi-coding-agent/dist/cli.js"], "pi"),
        (["/home/u/.local/share/claude/versions/2.1.321", "--resume", "x.jsonl"], "claude"),
        (["claude"], "claude"),
        (["codex"], "codex"),
        (["node", "/usr/lib/node_modules/@openai/codex/bin/codex.js"], "codex"),
        (["-zsh"], None),
        (["nvim", "pi.md"], None),
        ([], None),
    ],
)
def test_detect_agent(cmdline: list[str], agent: str | None):
    assert detect_agent(cmdline) == agent


def test_parse_tmux_clients_picks_the_focused_client():
    out = (
        "/dev/ttys007\t86174\t/dev/ttys016\t/w/kairo\n/dev/ttys009\t99898\t/dev/ttys008\t/w/hark\n"
    )
    assert parse_tmux_clients(out, "/dev/ttys009") == TmuxPane(99898, "/dev/ttys008", "/w/hark")
    assert parse_tmux_clients(out, "/dev/ttys001") is None


def test_parse_hyprctl_terminal_window():
    raw = '{"address": "0x55d", "class": "kitty", "title": "nvim store.ts", "pid": 4242}'
    focus = parse_hyprctl(raw)
    assert (focus.app_id, focus.title, focus.pid, focus.is_terminal, focus.window_id) == (
        "kitty",
        "nvim store.ts",
        4242,
        True,
        "0x55d",
    )


def test_parse_hyprctl_nothing_focused():
    assert parse_hyprctl("{}").pid is None


def test_resolve_finds_agent_and_cwd_under_a_terminal_process(tmp_path: Path):
    # "terminal" -> sh -> process whose argv[0] is "pi", running in tmp_path. A program file
    # named pi, not `exec -a pi`: binfmt emulation (an amd64 container on arm64) replaces
    # argv[0] with the path it executed.
    sleep = shutil.which("sleep")
    assert sleep is not None
    (tmp_path / "pi").symlink_to(sleep)
    script = f"cd {tmp_path} && exec ./pi 30"
    term = subprocess.Popen(  # noqa: S603 - test-controlled argv
        [sys.executable, "-c", f"import subprocess; subprocess.run(['bash', '-c', {script!r}])"]
    )
    try:
        deadline = time.monotonic() + 5
        state = resolve(term.pid)
        while state.agent is None and time.monotonic() < deadline:
            time.sleep(0.05)
            state = resolve(term.pid)
        assert state.agent == "pi"
        assert state.cwd is not None
        assert Path(state.cwd).resolve() == tmp_path.resolve()
    finally:
        for child in psutil.Process(term.pid).children(recursive=True):
            child.kill()
        term.kill()
        term.wait()


def test_resolve_vanished_process():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert resolve(proc.pid).cwd is None
