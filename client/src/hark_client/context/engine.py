"""``collect()``: run the providers under one deadline and rank their terms.

Pipeline (each stage only starts when its input is known):

    focus (app, title, selection) -> terminal (cwd, agent process) -> repo || agent session

Every provider runs in a worker thread with its own timeout, clipped to the overall
deadline. A provider that misses its deadline is reported in ``errors`` and keeps running in
the background (git/tmux calls have their own short timeouts), so its cache is warm for the
next dictation. Nothing here raises.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from hark_client.context.agents import CONFIDENT, AgentSession, find_session
from hark_client.context.aliases import extract_terms, scrub
from hark_client.context.focus import FocusResult, get_focus
from hark_client.context.rank import MAX_TERMS, Candidate, Origin, file_candidates, rank
from hark_client.context.repo import RepoInfo, repo_candidates, repo_info
from hark_client.context.terminal import TerminalState, resolve
from hark_client.context.types import ContextSnapshot, FocusInfo, TermKind

if TYPE_CHECKING:
    from collections.abc import Callable

PROVIDER_TIMEOUT_S: dict[str, float] = {
    "focus": 0.15,
    "terminal": 0.2,
    "repo": 0.5,
    "agent": 0.3,
}
MAX_SELECTION_CHARS = 2000


@dataclass(slots=True)
class Diagnostics:
    """Extra detail for debugging (not part of the snapshot contract)."""

    terminal: TerminalState | None = None
    session: AgentSession | None = None
    repo: RepoInfo | None = None
    stage_ms: dict[str, float] = field(default_factory=dict[str, float])


class _Runner:
    def __init__(self, pool: ThreadPoolExecutor, end: float) -> None:
        self.pool = pool
        self.end = end
        self.errors: list[str] = []
        self.stage_ms: dict[str, float] = {}

    def start[T](self, name: str, fn: Callable[[], T]) -> tuple[str, float, Future[T]]:
        return name, time.monotonic(), self.pool.submit(fn)

    def result[T](self, job: tuple[str, float, Future[T]]) -> T | None:
        name, started, fut = job
        limit = min(self.end, started + PROVIDER_TIMEOUT_S[name])
        try:
            return fut.result(timeout=max(0.0, limit - time.monotonic()))
        except FutureTimeout:
            self.errors.append(
                f"{name}: timeout after {(time.monotonic() - started) * 1000:.0f} ms"
            )
        except Exception as exc:  # noqa: BLE001 - providers must never break dictation
            self.errors.append(f"{name}: {type(exc).__name__}: {exc}")
        finally:
            self.stage_ms[name] = round((time.monotonic() - started) * 1000, 1)
        return None


def collect_detailed(
    deadline_s: float, max_terms: int = MAX_TERMS
) -> tuple[ContextSnapshot, Diagnostics]:
    t0 = time.monotonic()
    diag = Diagnostics()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hark-context")
    run = _Runner(pool, t0 + max(0.0, deadline_s))
    focus_res: FocusResult | None = None
    try:
        focus_res = run.result(run.start("focus", get_focus))
        focus = focus_res.focus if focus_res else FocusInfo()
        term_pid = focus.pid
        if focus.is_terminal and term_pid is not None:
            diag.terminal = run.result(run.start("terminal", lambda: resolve(term_pid)))
        # HARK_CONTEXT_CWD pins the working directory (testing, `hark once --cwd`).
        cwd = os.environ.get("HARK_CONTEXT_CWD") or (diag.terminal.cwd if diag.terminal else None)
        if cwd is not None:
            agent_hint = diag.terminal.agent if diag.terminal else None
            started = diag.terminal.agent_started if diag.terminal else None
            repo_job = run.start("repo", lambda: repo_info(cwd))
            agent_job = run.start("agent", lambda: find_session(cwd, agent_hint, started))
            diag.repo = run.result(repo_job)
            diag.session = run.result(agent_job)
        snapshot = _snapshot(focus_res, diag, cwd, max_terms)
    except Exception as exc:  # noqa: BLE001 - collect() must never raise
        run.errors.append(f"collect: {type(exc).__name__}: {exc}")
        snapshot = ContextSnapshot()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    errors = [*(focus_res.errors if focus_res else ()), *run.errors]
    diag.stage_ms = run.stage_ms
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    return replace(snapshot, collected_ms=elapsed_ms, errors=tuple(errors)), diag


def session_candidates(session: AgentSession) -> list[Candidate]:
    """Terms of each prompt/response group at its age: the user's prompt, the response and
    the tool-call paths (full path, file name and stem)."""
    out: list[Candidate] = []
    for g in session.groups:
        for text, origin in ((g.user, Origin.USER), (g.assistant, Origin.ASSISTANT)):
            out.extend(
                Candidate(t, k, origin, age=g.age)
                for t, k in extract_terms(scrub(text), prose=True)
            )
        for path in g.files:
            if scrub(path) != path:
                continue
            if "/" in path:
                out.append(Candidate(path, TermKind.PATH, Origin.TOOLS, age=g.age))
            out.extend(file_candidates(path, Origin.TOOLS, 1.0, g.age))
    return out


def text_candidates(text: str, origin: Origin, score: float = 1.0) -> list[Candidate]:
    return [Candidate(t, k, origin, score) for t, k in extract_terms(scrub(text), prose=True)]


def _snapshot(
    focus_res: FocusResult | None, diag: Diagnostics, cwd: str | None, max_terms: int
) -> ContextSnapshot:
    focus = focus_res.focus if focus_res else FocusInfo()
    selection = (
        focus_res.selection[:MAX_SELECTION_CHARS] if focus_res and focus_res.selection else None
    )
    cands: list[Candidate] = []
    if selection:
        cands.extend(text_candidates(selection, Origin.SELECTION))
    session = diag.session if diag.session and diag.session.confidence >= CONFIDENT else None
    if session:
        cands.extend(session_candidates(session))
    if focus.title:
        cands.extend(text_candidates(focus.title, Origin.WINDOW_TITLE))
    if diag.repo is not None:
        cands.extend(repo_candidates(diag.repo))
    ranking = rank(cands, max_terms)
    return ContextSnapshot(
        focus=focus,
        cwd=cwd,
        repo_root=diag.repo.root if diag.repo else None,
        branch=diag.repo.branch if diag.repo else None,
        agent=session.agent if session else None,
        agent_last_reply=scrub(session.last_reply) if session else None,
        selection=selection,
        terms=ranking.terms,
        provenance=ranking.trace,
    )


def collect(deadline_s: float, max_terms: int = MAX_TERMS) -> ContextSnapshot:
    return collect_detailed(deadline_s, max_terms)[0]
