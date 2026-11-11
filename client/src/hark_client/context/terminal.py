"""Working directory and agent of the focused terminal, from its process tree.

Heuristics, in order:

1. Collect the terminal's descendants and group them by controlling tty. One terminal
   process often owns many ttys (tabs, splits, foot/kitty server mode).
2. Pick the tty the user touched last: newest access time of the tty device (reading
   keyboard input updates atime; program output only updates mtime), then newest mtime.
3. On that tty take the foreground process group (``tpgid``). The group leader is the
   foreground job (shell when idle, else ``vim``/``pi``/...).
4. If the foreground job is a tmux client, ask that tmux server which pane the client shows
   (``list-clients``, matched by client tty) and repeat step 3 on the pane's tty;
   ``pane_current_path`` is the fallback cwd.
5. No tty information (e.g. GUI-spawned processes): newest descendant by start time.

The agent is detected by command line among the foreground group and its descendants.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath

import psutil

AGENT_NAMES = ("pi", "claude", "codex")
_RUNTIMES = frozenset({"node", "bun", "deno", "python", "python3"})
_TMUX_TIMEOUT_S = 0.5


@dataclass(frozen=True, slots=True)
class TerminalState:
    cwd: str | None = None
    pid: int | None = None  # foreground process the cwd was taken from
    agent: str | None = None
    agent_pid: int | None = None
    agent_started: float | None = None  # epoch seconds
    tty: str | None = None
    via_tmux: bool = False


def detect_agent(cmdline: list[str]) -> str | None:
    """'pi' | 'claude' | 'codex' when the command line runs one of the coding agents."""
    if not cmdline:
        return None
    args = cmdline[1:2] if PurePath(cmdline[0]).name in _RUNTIMES else cmdline[:1]
    for arg in args:
        path = PurePath(arg)
        name = path.name.removesuffix(".js").removesuffix(".mjs")
        for agent in AGENT_NAMES:
            if name == agent or (agent == "codex" and name.startswith("codex-")):
                return agent
        parts = set(path.parts)
        if "claude" in parts or "@anthropic-ai" in parts:
            return "claude"  # ~/.local/share/claude/versions/2.1.x, npm package
        if "@openai" in parts and "codex" in arg:
            return "codex"
        if "pi-coding-agent" in arg:
            return "pi"
    return None


def _tpgid(pid: int) -> int | None:
    """Foreground process group of the process's controlling terminal."""
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        fields = stat[stat.rfind(")") + 2 :].split()
        # fields after comm: state ppid pgrp session tty_nr tpgid
        return int(fields[5]) if len(fields) > 5 and int(fields[5]) > 0 else None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["ps", "-o", "tpgid=", "-p", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out) if out.lstrip("-").isdigit() and int(out) > 0 else None


def _tty_activity(tty: str) -> tuple[float, float]:
    try:
        st = Path(tty).stat()
    except OSError:
        return (0.0, 0.0)
    return (st.st_atime, st.st_mtime)


def _info(p: psutil.Process) -> tuple[str | None, int | None]:
    """(tty, pgid) or Nones if the process vanished/denied."""
    try:
        return p.terminal(), os.getpgid(p.pid)
    except (psutil.Error, OSError):
        return None, None


def _foreground(procs: list[psutil.Process]) -> psutil.Process | None:
    """Foreground job leader among processes sharing one tty."""
    if not procs:
        return None
    tpgid = _tpgid(procs[0].pid)
    group = [p for p in procs if _info(p)[1] == tpgid] if tpgid else []
    if not group:
        return _newest(procs)
    leader = next((p for p in group if p.pid == tpgid), None)
    return leader or _newest(group)


def _newest(procs: list[psutil.Process]) -> psutil.Process | None:
    best: tuple[float, psutil.Process] | None = None
    for p in procs:
        try:
            created = p.create_time()
        except psutil.Error:
            continue
        if best is None or created > best[0]:
            best = (created, p)
    return best[1] if best else None


def _name(p: psutil.Process) -> str:
    try:
        return p.name()
    except psutil.Error:
        return ""


def _cmdline(p: psutil.Process) -> list[str]:
    try:
        return p.cmdline()
    except psutil.Error:
        return []


def _cwd(p: psutil.Process) -> str | None:
    try:
        return p.cwd() or None
    except psutil.Error:
        return None


def _tmux_target(client: psutil.Process) -> TmuxPane | None:
    """The pane shown by the tmux client ``client``."""
    cmd = _cmdline(client)
    sock: list[str] = []
    for flag in ("-S", "-L"):
        if flag in cmd[:-1]:
            sock = [flag, cmd[cmd.index(flag) + 1]]
    client_tty, _ = _info(client)
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            [  # noqa: S607 - tmux from PATH is intended
                "tmux",
                *sock,
                "list-clients",
                "-F",
                "#{client_tty}\t#{pane_pid}\t#{pane_tty}\t#{pane_current_path}",
            ],
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_S,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_tmux_clients(out, client_tty)


@dataclass(frozen=True, slots=True)
class TmuxPane:
    pid: int
    tty: str
    path: str | None


def parse_tmux_clients(out: str, client_tty: str | None) -> TmuxPane | None:
    """Pane of the client on ``client_tty`` from ``list-clients`` output (tab-separated)."""
    for line in out.splitlines():
        row = line.split("\t")
        if len(row) != 4 or not row[1].isdigit():
            continue
        c_tty, p_pid, p_tty, p_path = row
        if c_tty == client_tty:
            return TmuxPane(int(p_pid), p_tty, p_path or None)
    return None


def _by_tty(procs: list[psutil.Process]) -> dict[str, list[psutil.Process]]:
    groups: dict[str, list[psutil.Process]] = {}
    for p in procs:
        tty, _ = _info(p)
        if tty:
            groups.setdefault(tty, []).append(p)
    return groups


def _pane_procs(pane: TmuxPane) -> list[psutil.Process]:
    """The pane's shell and its descendants that still run on the pane's tty."""
    try:
        shell = psutil.Process(pane.pid)
        procs = [shell, *shell.children(recursive=True)]
    except psutil.Error:
        return []
    return [p for p in procs if _info(p)[0] == pane.tty]


def _agent_near(
    fg: psutil.Process, peers: list[psutil.Process]
) -> tuple[str | None, psutil.Process | None]:
    try:
        kids = fg.children(recursive=True)
    except psutil.Error:
        kids = []
    for p in [fg, *peers, *kids]:
        agent = detect_agent(_cmdline(p))
        if agent:
            return agent, p
    return None, None


def resolve(terminal_pid: int) -> TerminalState:
    """Foreground cwd/agent inside the terminal emulator process ``terminal_pid``."""
    try:
        root = psutil.Process(terminal_pid)
        procs = root.children(recursive=True)
    except psutil.Error:
        return TerminalState()
    groups = _by_tty(procs)
    tty: str | None = None
    fg: psutil.Process | None
    peers: list[psutil.Process] = []
    if groups:
        tty = max(groups, key=_tty_activity)
        peers = groups[tty]
        fg = _foreground(peers)
    else:
        fg = _newest(procs)
    if fg is None:
        return TerminalState()
    via_tmux = False
    fallback_cwd: str | None = None
    if _name(fg) == "tmux" or any(_name(p) == "tmux" for p in peers if _info(p)[1] == _info(fg)[1]):
        client = fg if _name(fg) == "tmux" else next(p for p in peers if _name(p) == "tmux")
        pane = _tmux_target(client)
        if pane is not None:
            via_tmux = True
            tty = pane.tty
            fallback_cwd = pane.path
            peers = _pane_procs(pane)
            fg = _foreground(peers) or fg
    agent, agent_proc = _agent_near(fg, [p for p in peers if _info(p)[1] == _info(fg)[1]])
    source = agent_proc or fg
    cwd = _cwd(source) or _cwd(fg) or fallback_cwd
    started: float | None = None
    if agent_proc is not None:
        with contextlib.suppress(psutil.Error):
            started = agent_proc.create_time()
    return TerminalState(
        cwd=cwd,
        pid=source.pid,
        agent=agent,
        agent_pid=agent_proc.pid if agent_proc else None,
        agent_started=started,
        tty=tty,
        via_tmux=via_tmux,
    )
