import os
import subprocess
from pathlib import Path

import pytest

from hark_client.context import repo
from hark_client.context.rank import Candidate, Origin
from hark_client.context.types import TermKind


def git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - test-controlled argv
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def work(tmp_path: Path) -> Path:
    root = tmp_path / "myrepo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "session_store.py").write_text("def load_session_store():\n    return 1\n")
    (root / "README.md").write_text("hello\n")
    git(root, "init", "-q", "-b", "feat/use-store")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    (root / "notes").mkdir()
    (root / "notes" / "newThing.ts").write_text("export const useSessionStore = makeStore();\n")
    os.utime(root / "notes" / "newThing.ts", (2_000_000_000, 2_000_000_000))
    return root


def test_repo_info_reports_root_branch_recent_files_and_identifiers(work: Path):
    info = repo.repo_info(str(work / "src"))
    assert info is not None
    assert (Path(info.root).resolve(), info.branch) == (work.resolve(), "feat/use-store")
    # untracked file first (newest mtime), then the last commit's files
    assert info.recent_files[0] == "notes/newThing.ts"
    assert {"useSessionStore", "makeStore", "load_session_store"} <= set(info.identifiers)


def test_repo_candidates_cover_recent_and_repo_wide_files(work: Path):
    info = repo.repo_info(str(work))
    assert info is not None
    cands = repo.repo_candidates(info)
    assert Candidate("newThing.ts", TermKind.PATH, Origin.REPO_RECENT, 1.0) in cands
    assert any(c.canonical == "README.md" and c.origin is Origin.REPO for c in cands)


def test_cache_is_reused_until_head_changes(work: Path):
    first = repo.repo_info(str(work))
    assert repo.repo_info(str(work)) is first
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "more")
    second = repo.repo_info(str(work))
    assert second is not first
    assert second is not None
    assert first is not None
    assert second.head != first.head


def test_not_a_repository(tmp_path: Path):
    assert repo.repo_info(str(tmp_path)) is None


def test_empty_repository_without_commits(tmp_path: Path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "fooBar.py").write_text("x_y = 1\n")
    info = repo.repo_info(str(tmp_path))
    assert info is not None
    assert (info.head, info.branch, info.recent_files) == (None, "main", ("fooBar.py",))
