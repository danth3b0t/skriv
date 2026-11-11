"""``collect()``: run the providers under one deadline and rank their terms.

Pipeline (each stage only starts when its input is known):

    focus (app, title, selection) -> terminal (cwd, agent process) -> repo || agent session

Every provider runs in a worker thread with its own timeout, clipped to the overall
deadline. A provider that misses its deadline is reported in ``errors`` and keeps running in
the background (git/tmux calls have their own short timeouts), so its cache is warm for the
next dictation. Nothing here raises.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from hark_client.context.agents import CONFIDENT, AgentSession, find_session
from hark_client.context.aliases import extract_terms
from hark_client.context.focus import FocusResult, get_focus
from hark_client.context.rank import Candidate, file_candidates, ranked
from hark_client.context.repo import RepoInfo, repo_candidates, repo_info
from hark_client.context.terminal import TerminalState, resolve
from hark_client.context.types import ContextSnapshot, FocusInfo, TermSource

if TYPE_CHECKING:
    from collections.abc import Callable

PROVIDER_TIMEOUT_S: dict[str, float] = {
    "focus": 0.15,
    "terminal": 0.2,
    "repo": 0.5,
    "agent": 0.3,
}
MAX_SELECTION_CHARS = 2000

_SECRET_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"|[A-Za-z0-9+/_-]{40,})\b"
)


def scrub(text: str) -> str:
    """Redact API keys/tokens and other long opaque strings."""
    return _SECRET_RE.sub("[redacted]", text)


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


def collect_detailed(deadline_s: float, max_terms: int = 60) -> tuple[ContextSnapshot, Diagnostics]:
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
        cwd = diag.terminal.cwd if diag.terminal else None
        if cwd is not None:
            agent_hint = diag.terminal.agent if diag.terminal else None
            started = diag.terminal.agent_started if diag.terminal else None
            repo_job = run.start("repo", lambda: repo_info(cwd))
            agent_job = run.start("agent", lambda: find_session(cwd, agent_hint, started))
            diag.repo = run.result(repo_job)
            diag.session = run.result(agent_job)
        snapshot = _snapshot(focus_res, diag, max_terms)
    except Exception as exc:  # noqa: BLE001 - collect() must never raise
        run.errors.append(f"collect: {type(exc).__name__}: {exc}")
        snapshot = ContextSnapshot()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    errors = [*(focus_res.errors if focus_res else ()), *run.errors]
    diag.stage_ms = run.stage_ms
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    return replace(snapshot, collected_ms=elapsed_ms, errors=tuple(errors)), diag


def _snapshot(focus_res: FocusResult | None, diag: Diagnostics, max_terms: int) -> ContextSnapshot:
    focus = focus_res.focus if focus_res else FocusInfo()
    selection = (
        focus_res.selection[:MAX_SELECTION_CHARS] if focus_res and focus_res.selection else None
    )
    cands: list[Candidate] = []
    if selection:
        cands.extend(
            Candidate(t, k, TermSource.SELECTION) for t, k in extract_terms(selection, prose=True)
        )
    session = diag.session if diag.session and diag.session.confidence >= CONFIDENT else None
    reply = scrub(session.last_reply) if session else None
    if session and reply:
        cands.extend(
            Candidate(t, k, TermSource.AGENT_SESSION) for t, k in extract_terms(reply, prose=True)
        )
        n = max(1, len(session.files))
        for i, path in enumerate(session.files[:30]):
            cands.extend(file_candidates(path, TermSource.AGENT_SESSION, 1.0 - i / n))
    if focus.title:
        cands.extend(
            Candidate(t, k, TermSource.WINDOW_TITLE)
            for t, k in extract_terms(focus.title, prose=True)
        )
    if diag.repo is not None:
        cands.extend(repo_candidates(diag.repo))
    return ContextSnapshot(
        focus=focus,
        cwd=diag.terminal.cwd if diag.terminal else None,
        repo_root=diag.repo.root if diag.repo else None,
        branch=diag.repo.branch if diag.repo else None,
        agent=session.agent if session else None,
        agent_last_reply=reply,
        selection=selection,
        terms=ranked(cands, max_terms),
    )


def collect(deadline_s: float, max_terms: int = 60) -> ContextSnapshot:
    return collect_detailed(deadline_s, max_terms)[0]
