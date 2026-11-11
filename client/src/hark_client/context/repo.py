"""Git repository vocabulary: files, recently touched files and their identifiers.

Results are cached per repository keyed by (HEAD, index mtime) with a short TTL, because
editing a file without staging changes neither. Every git call is bounded by a timeout and
all outputs are truncated, so a huge repository costs at most a few hundred milliseconds
once and nothing while the cache is warm.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from hark_client.context.aliases import extract_terms
from hark_client.context.rank import Candidate, file_candidates
from hark_client.context.types import TermKind, TermSource

MAX_FILES = 5000  # tracked files kept from `git ls-files`
MAX_CHANGED = 200  # changed/untracked entries considered
RECENT_COMMITS = 10
MAX_SCAN_FILES = 12  # recent files read for identifiers
MAX_SCAN_BYTES = 256 * 1024
MAX_IDENTIFIERS = 150
CACHE_TTL_S = 30.0
GIT_TIMEOUT_S = 2.0


@dataclass(frozen=True, slots=True)
class RepoInfo:
    root: str
    branch: str | None
    head: str | None
    files: tuple[str, ...]  # tracked, repo-relative (bounded)
    recent_files: tuple[str, ...]  # changed/untracked by mtime, then recent commits' files
    identifiers: tuple[str, ...]  # from recent files, most relevant first


_cache: dict[str, tuple[tuple[str | None, int], float, RepoInfo]] = {}
_cache_lock = threading.Lock()


def _git(args: list[str], cwd: str, timeout: float = GIT_TIMEOUT_S) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            # no optional locks: never rewrite the index behind the user's back
            ["git", "--no-optional-locks", "-C", cwd, *args],  # noqa: S607 - git from PATH
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode(errors="replace")


def repo_info(cwd: str) -> RepoInfo | None:
    """Repository containing ``cwd`` or None (not a repo, git missing, timeout)."""
    top = _git(["rev-parse", "--show-toplevel", "--absolute-git-dir"], cwd)
    if top is None:
        return None
    lines = top.splitlines()
    if len(lines) < 2:
        return None
    root, git_dir = lines[0], lines[1]
    head = (_git(["rev-parse", "-q", "--verify", "HEAD"], root) or "").strip() or None
    try:
        index_mtime = (Path(git_dir) / "index").stat().st_mtime_ns
    except OSError:
        index_mtime = 0
    key = (head, index_mtime)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(root)
    if hit is not None and hit[0] == key and now - hit[1] < CACHE_TTL_S:
        return hit[2]
    info = _scan(root, head)
    with _cache_lock:
        _cache[root] = (key, now, info)
    return info


def _scan(root: str, head: str | None) -> RepoInfo:
    branch = (_git(["symbolic-ref", "-q", "--short", "HEAD"], root) or "").strip() or None
    listed = _git(["ls-files", "-z"], root) or ""
    files = tuple(f for f in listed.split("\0")[:MAX_FILES] if f)
    changed = _changed_files(root)
    committed: list[str] = []
    if head is not None:
        log = _git(["log", f"-n{RECENT_COMMITS}", "--name-only", "--format="], root) or ""
        committed = [ln for ln in log.splitlines() if ln]
    recent = list(dict.fromkeys([*changed, *committed]))
    return RepoInfo(
        root=root,
        branch=branch,
        head=head,
        files=files,
        recent_files=tuple(recent),
        identifiers=_identifiers(root, recent),
    )


def _changed_files(root: str) -> list[str]:
    """Modified, staged and untracked files, most recently modified first."""
    out = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], root) or ""
    entries = out.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(entries) and len(paths) < MAX_CHANGED:
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC":
            i += 1  # the next field is the rename/copy source
        if "D" in status or path.endswith("/"):
            continue
        paths.append(path)

    def mtime(p: str) -> float:
        try:
            return (Path(root) / p).stat().st_mtime
        except OSError:
            return 0.0

    return sorted(paths, key=mtime, reverse=True)


def _identifiers(root: str, recent: list[str]) -> tuple[str, ...]:
    """Identifiers from the first readable recent files, weighted by file recency."""
    scores: dict[str, float] = {}
    scanned = 0
    for rank, rel in enumerate(recent):
        if scanned >= MAX_SCAN_FILES:
            break
        path = Path(root) / rel
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:1024]:
            continue  # binary
        scanned += 1
        file_weight = 1.0 / (1 + rank)
        for term, kind in extract_terms(data.decode(errors="replace")):
            if kind is TermKind.IDENTIFIER:
                scores[term] = scores.get(term, 0.0) + file_weight
    ranked = sorted(scores, key=lambda t: (-scores[t], t))
    return tuple(ranked[:MAX_IDENTIFIERS])


def repo_candidates(info: RepoInfo) -> list[Candidate]:
    out: list[Candidate] = [
        Candidate(Path(info.root).name, TermKind.NAME, TermSource.REPO_RECENT, 1.0)
    ]
    if info.branch:
        out.extend(
            Candidate(t, k, TermSource.REPO_RECENT, 1.0)
            for t, k in extract_terms(info.branch.replace("/", " "))
        )
    n = max(1, len(info.recent_files))
    for i, rel in enumerate(info.recent_files[:60]):
        out.extend(file_candidates(rel, TermSource.REPO_RECENT, 1.0 - i / n))
    m = max(1, len(info.identifiers))
    out.extend(
        Candidate(ident, TermKind.IDENTIFIER, TermSource.REPO_RECENT, 1.0 - i / m)
        for i, ident in enumerate(info.identifiers)
    )
    # repository-wide: shallow files first
    for rel in sorted(info.files, key=lambda f: (f.count("/"), f))[:300]:
        depth = rel.count("/")
        out.extend(file_candidates(rel, TermSource.REPO, 1.0 / (1 + depth)))
        if depth:
            top = rel.split("/", 1)[0]
            out.append(Candidate(top, TermKind.PATH, TermSource.REPO, 1.0))
    return out
