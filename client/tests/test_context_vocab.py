"""Vocabulary learned from earlier sessions, against small pi / Claude Code / Codex files."""

import json
import math
import os
import stat
import time
from pathlib import Path

import pytest

from hark_client.context import agents, vocab
from hark_client.context.rank import Origin
from hark_client.context.vocab import VocabStore

NOW = time.time()
ENDED = NOW - 2 * 86400  # two days ago
REPO = "/home/u/projects/app"


@pytest.fixture(autouse=True)
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    return tmp_path


@pytest.fixture
def store(tmp_path: Path) -> VocabStore:
    return VocabStore(tmp_path / "data" / vocab.STORE_NAME)


def write(path: Path, entries: list[dict[str, object]], mtime: float, mode: str = "w") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode) as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in entries))
    os.utime(path, (mtime, mtime))
    return path


def pi_user(eid: str, text: str) -> dict[str, object]:
    return {"type": "message", "id": eid, "message": {"role": "user", "content": text}}


def pi_file(name: str, cwd: str, prompts: list[str], mtime: float = ENDED) -> Path:
    entries: list[dict[str, object]] = [{"type": "session", "id": name, "cwd": cwd}]
    entries += [pi_user(f"{name}{i}", p) for i, p in enumerate(prompts)]
    reply = {"role": "assistant", "content": [{"type": "text", "text": "use `assistantOnly`"}]}
    entries.append({"type": "message", "id": f"{name}a", "message": reply})
    return write(agents.pi_session_dir(cwd) / f"{name}.jsonl", entries, mtime)


def scores(cands: list[vocab.Candidate], origin: Origin) -> dict[str, float]:
    return {c.canonical: round(c.score, 4) for c in cands if c.origin is origin}


def test_learns_terms_of_ended_user_prompts_per_repository_and_user(store: VocabStore, homes: Path):
    pi_file("a", REPO, ["fix `parseConfig` in Hyprland", "and `sharedTerm`"])
    pi_file("b", f"{REPO}/sub", ["again `sharedTerm`"])
    pi_file("c", "/elsewhere", ["`otherRepoTerm` here"])
    pi_file("live", REPO, ["`stillTyping`"], mtime=NOW - 60)  # not ended yet
    pi_file("matched", REPO, ["`currentSession`"])  # excluded by the caller
    claude = homes / "claude/projects/x/s.jsonl"
    meta = {"type": "user", "uuid": "m", "cwd": REPO, "isMeta": True}
    write(claude, [{**meta, "message": {"role": "user", "content": "`injectedTerm`"}}], ENDED)
    codex = homes / "codex/sessions/2026/11/10/rollout-sub.jsonl"
    head: dict[str, object] = {
        "type": "session_meta",
        "payload": {"cwd": REPO, "parent_thread_id": "t"},
    }
    prompt = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "`subTerm`"}],
    }
    write(codex, [head, {"type": "response_item", "payload": prompt}], ENDED)

    exclude = [str(agents.pi_session_dir(REPO) / "matched.jsonl")]
    assert store.learn(now=NOW, exclude=exclude) == 5  # a, b, c, claude, codex subagent
    cands = store.candidates(REPO, now=NOW)
    one = 0.5 + 0.5 * math.log1p(1) / math.log(6)
    decay = 2 ** (-2 / 30)  # two days, 30-day half-life
    two = (0.5 + 0.5 * math.log1p(2) / math.log(6)) * decay
    assert scores(cands, Origin.PERSIST_REPO) == {
        "sharedTerm": round(two, 4),  # two sessions, one in a subdirectory
        "parseConfig": round(one * decay, 4),
        "Hyprland": round(one * decay, 4),
    }
    assert set(scores(cands, Origin.PERSIST_USER)) == {
        "sharedTerm",
        "parseConfig",
        "Hyprland",
        "otherRepoTerm",
    }


def test_growing_session_is_read_incrementally_without_counting_twice(store: VocabStore):
    path = pi_file("a", REPO, ["`firstTerm`"])
    assert store.learn(now=NOW) == 1
    assert store.learn(now=NOW) == 0  # nothing new
    write(path, [pi_user("a9", "`firstTerm` and `laterTerm`")], ENDED + 60, mode="a")
    assert store.learn(now=NOW) == 1
    entries = store.vocab().scopes[REPO]
    assert (entries["firstTerm"].sessions, entries["laterTerm"].sessions) == (1, 1)
    assert entries["firstTerm"].last_end == pytest.approx(ENDED + 60)


def test_store_is_owner_only_capped_tolerant_and_clearable(
    store: VocabStore, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(vocab, "MAX_SCOPE_TERMS", 2)
    pi_file("a", REPO, ["`termOne` `termTwo` `termThree`"])
    store.learn(now=NOW)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert len(store.vocab().scopes[REPO]) == 2
    store.path.write_text("{not json")
    assert store.candidates(REPO, now=NOW) == []
    store.clear()
    assert not store.path.exists()
