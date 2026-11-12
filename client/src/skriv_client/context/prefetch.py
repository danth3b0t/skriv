"""Background prefetch: keep the slow context of the focused window warm.

A daemon thread follows the focus: it polls ``focus_token()`` every ``POLL_S`` (macOS: the
window server, ~1 ms) or, on Hyprland, wakes on ``.socket2.sock`` events (``activewindowv2``,
``windowtitlev2`` of the active window). On a focus change, and every ``REVALIDATE_S``, it
re-reads the focus (no Accessibility round trip), re-resolves the terminal (cwd, agent) and stats cheap validators:
the repository's ``HEAD``/``index``, the agent session directories and the matched session
file. When any of them changed (or the entry is older than ``WARM_TTL_S``), it re-runs the
slow providers (repository, agent session, learned vocabulary) for that window and keeps the
result as its warm entry (the last ``WARM_WINDOWS`` windows).

A second daemon thread learns vocabulary from ended agent sessions (:mod:`vocab`): a bounded
pass at start, when the focused cwd changes and every ``LEARN_INTERVAL_S``.

At key-down ``collect()`` uses the warm entry of the focused window when it was validated
within ``MAX_WARM_AGE_S`` and only reads the volatile sources itself (focus, selection, screen
text). A key-down right after a focus change, or within ``REVALIDATE_S`` of a tmux pane switch
inside the same window, gets the cold path or the previous pane's context.

:func:`start_prefetch` / :func:`stop_prefetch` are the hooks for the client core.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from skriv_client.context import agents, aliases, vocab
from skriv_client.context.agents import find_session
from skriv_client.context.focus import focus_token, get_focus
from skriv_client.context.repo import repo_info
from skriv_client.context.terminal import resolve

if TYPE_CHECKING:
    from collections.abc import Callable

    from skriv_client.context.agents import AgentSession
    from skriv_client.context.rank import Candidate
    from skriv_client.context.repo import RepoInfo
    from skriv_client.context.terminal import TerminalState

log = logging.getLogger(__name__)

POLL_S = 0.25
REVALIDATE_S = 2.0
MAX_WARM_AGE_S = 3 * REVALIDATE_S
WARM_TTL_S = 30.0  # rebuild anyway (unstaged edits change no validator)
WARM_WINDOWS = 16
LEARN_INTERVAL_S = 30 * 60.0
LEARN_BACKLOG_PAUSE_S = 5.0  # between passes while old sessions are still unread


@dataclass(frozen=True, slots=True)
class Warm:
    """The slow context of one window, as collect() would compute it."""

    window_id: str
    cwd: str | None
    terminal: TerminalState | None
    repo: RepoInfo | None
    session: AgentSession | None
    learned: tuple[Candidate, ...]
    validators: tuple[object, ...]
    built_at: float  # time.monotonic()
    validated_at: float


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _git_dir(root: str) -> Path:
    dot = Path(root) / ".git"
    if dot.is_file():  # worktree: "gitdir: <path>"
        with contextlib.suppress(OSError, ValueError):
            text = dot.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                return (Path(root) / text.removeprefix("gitdir:").strip()).resolve()
    return dot


def validators(cwd: str | None, session: AgentSession | None) -> tuple[object, ...]:
    """Cheap stats that change when the slow context may have: repository HEAD/index,
    the agent session directories (new sessions) and the matched session file."""
    if cwd is None:
        return ()
    git = _git_dir(vocab.repo_root(cwd))
    dirs = (agents.pi_session_dir(cwd), agents.claude_project_dir(cwd))
    return (
        _stat(git / "HEAD"),
        _stat(git / "index"),
        *(_stat(d) for d in dirs),
        _stat(Path(session.path)) if session else None,
    )


def build(window_id: str, terminal: TerminalState | None, cwd: str | None) -> Warm:
    """Run the slow providers for one window (no deadline; background only)."""
    repo = session = None
    learned: tuple[Candidate, ...] = ()
    if cwd is not None:
        agent = terminal.agent if terminal else None
        started = terminal.agent_started if terminal else None
        repo = repo_info(cwd)
        session = find_session(cwd, agent, started)
        learned = tuple(vocab.store().candidates(vocab.repo_root(cwd)))
    now = time.monotonic()
    checks = validators(cwd, session)
    return Warm(window_id, cwd, terminal, repo, session, learned, checks, now, now)


def hyprland_socket2() -> Path | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig or not sys.platform.startswith("linux"):
        return None
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    for base in (Path(runtime) / "hypr", Path("/tmp/hypr")):  # noqa: S108 - Hyprland < 0.40
        path = base / sig / ".socket2.sock"
        if path.exists():
            return path
    return None


def watch_hyprland(path: Path, kick: threading.Event, stop: threading.Event) -> None:
    """Set ``kick`` on focus changes and title changes of the active window until ``stop``.
    Reconnects when Hyprland restarts. Only the event socket is held open."""
    active = ""
    while not stop.is_set():
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            sock.connect(str(path))
        except OSError:
            stop.wait(2.0)
            continue
        with sock:
            buf = b""
            while not stop.is_set():
                try:
                    data = sock.recv(4096)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                *lines, buf = (buf + data).split(b"\n")
                for line in lines:
                    event, _, payload = line.decode(errors="replace").partition(">>")
                    if event == "activewindowv2":
                        active = payload
                        kick.set()
                    elif event == "windowtitlev2" and payload.split(",", 1)[0] == active:
                        kick.set()


class Prefetcher:
    """The daemon (see the module docstring). ``learn``: also learn vocabulary."""

    def __init__(self, data_dir: str | None = None, *, learn: bool = True) -> None:
        self._store = vocab.store(data_dir)
        self._learn = learn
        self._stop = threading.Event()
        self._kick = threading.Event()  # focus/title event (Hyprland)
        self._learn_kick = threading.Event()
        self._lock = threading.Lock()
        self._warm: OrderedDict[str, Warm] = OrderedDict()
        self._learn_cwd: str | None = None
        self._exclude: tuple[str, ...] = ()
        self._threads: list[threading.Thread] = []

    # -- control -----------------------------------------------------------------------

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        targets: list[tuple[str, Callable[[], None]]] = [("skriv-context-prefetch", self._run)]
        if self._learn:
            targets.append(("skriv-context-learn", self._learn_loop))
        if (sock := hyprland_socket2()) is not None:
            targets.append(
                ("skriv-context-hypr", lambda: watch_hyprland(sock, self._kick, self._stop))
            )
        for name, fn in targets:
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        self._kick.set()
        self._learn_kick.set()
        for t in self._threads:
            t.join(timeout_s)
        self._threads = []

    def lookup(self, window_id: str) -> Warm | None:
        """The warm entry of ``window_id`` if it is fresh enough for a key-down."""
        with self._lock:
            warm = self._warm.get(window_id)
        if warm is None or time.monotonic() - warm.validated_at > MAX_WARM_AGE_S:
            return None
        return warm

    # -- focus loop --------------------------------------------------------------------

    def _run(self) -> None:
        aliases.english_words()  # load the word list off the hotkey path
        events = any(t.name == "skriv-context-hypr" for t in self._threads)
        last_token: str | None = None
        next_check = 0.0
        while not self._stop.is_set():
            kicked = self._kick.wait(REVALIDATE_S if events else POLL_S)
            self._kick.clear()
            if self._stop.is_set():
                break
            try:
                token = focus_token()
                now = time.monotonic()
                if kicked or token != last_token or now >= next_check:
                    self.refresh()
                    last_token, next_check = token, now + REVALIDATE_S
            except Exception:
                log.exception("context prefetch failed")

    def refresh(self) -> Warm | None:
        """Validate (and if needed rebuild) the warm entry of the focused window."""
        focus = get_focus(accessibility=False).focus  # never competes with a key-down
        if not focus.window_id:
            return None
        terminal = resolve(focus.pid) if focus.is_terminal and focus.pid is not None else None
        cwd = os.environ.get("SKRIV_CONTEXT_CWD") or (terminal.cwd if terminal else None)
        agent = terminal.agent if terminal else None
        with self._lock:
            cur = self._warm.get(focus.window_id)
        now = time.monotonic()
        if (
            cur is not None
            and cur.cwd == cwd
            and (cur.terminal.agent if cur.terminal else None) == agent
            and now - cur.built_at < WARM_TTL_S
            and validators(cwd, cur.session) == cur.validators
        ):
            warm = replace(cur, validated_at=now)
        else:
            warm = build(focus.window_id, terminal, cwd)
            if cwd is not None and (cwd != self._learn_cwd or cur is None):
                self._learn_cwd = cwd
                self._exclude = (warm.session.path,) if warm.session else ()
                self._learn_kick.set()
        with self._lock:
            self._warm[focus.window_id] = warm
            self._warm.move_to_end(focus.window_id)
            while len(self._warm) > WARM_WINDOWS:
                self._warm.popitem(last=False)
        return warm

    # -- vocabulary --------------------------------------------------------------------

    def _learn_loop(self) -> None:
        wait = 0.0  # first pass right away
        while not self._stop.is_set():
            self._learn_kick.wait(wait)
            self._learn_kick.clear()
            if self._stop.is_set():
                break
            try:
                n = self._store.learn(
                    exclude=self._exclude, priority_cwd=self._learn_cwd, stop=self._stop
                )
            except Exception:
                log.exception("vocabulary learning failed")
                n = 0
            wait = LEARN_BACKLOG_PAUSE_S if n >= vocab.MAX_FILES_PER_PASS else LEARN_INTERVAL_S


_active: Prefetcher | None = None
_active_lock = threading.Lock()


def start_prefetch(data_dir: str | None = None, *, learn: bool = True) -> Prefetcher:
    """Start the context daemon (idempotent). ``data_dir``: where learned vocabulary lives
    (the client's ``history.dir``); ``learn=False`` only reads what was learned before."""
    global _active  # noqa: PLW0603 - one daemon per process
    with _active_lock:
        if _active is None:
            _active = Prefetcher(data_dir, learn=learn)
            _active.start()
        return _active


def stop_prefetch() -> None:
    global _active  # noqa: PLW0603
    with _active_lock:
        if _active is not None:
            _active.stop()
            _active = None


def lookup(window_id: str) -> Warm | None:
    """Fresh warm entry for ``window_id`` when the daemon runs, else None."""
    active = _active
    return active.lookup(window_id) if active is not None and window_id else None
