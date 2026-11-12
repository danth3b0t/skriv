"""Debug CLI: print the context snapshot as JSON.

    uv run python -m skriv_client.context --delay 3   # then focus the window to inspect

``--canonicalize TEXT`` additionally runs the canonicalizer on TEXT with the collected terms.

    uv run python -m skriv_client.context vocab [list|update|clear] [--repo DIR] [--data-dir DIR]

inspects (``list``: the learned terms served for ``--repo``, default the current directory),
refreshes or deletes the vocabulary learned from earlier agent sessions.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path
from typing import cast

from skriv_client.context import vocab
from skriv_client.context.canon import canonicalize
from skriv_client.context.engine import collect_detailed


def _jsonable(obj: object) -> object:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: _jsonable(cast("object", getattr(obj, f.name))) for f in dataclasses.fields(obj)
        }
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in cast("dict[object, object]", obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in cast("list[object]", obj)]
    return obj


class _Args(argparse.Namespace):
    delay: float = 0.0
    deadline: float = 1.0
    max_terms: int = 30
    diagnostics: bool = False
    canonicalize: str | None = None


class _VocabArgs(argparse.Namespace):
    action: str = "list"
    repo: str = "."
    data_dir: str | None = None
    limit: int = 40


def vocab_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m skriv_client.context vocab")
    ap.add_argument("action", nargs="?", default="list", choices=["list", "update", "clear"])
    ap.add_argument("--repo", default=".", help="repository (or directory) to list terms for")
    ap.add_argument("--data-dir", help="hark data dir (default: history.dir default)")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv, namespace=_VocabArgs())
    store = vocab.store(args.data_dir)
    if args.action == "clear":
        store.clear()
        print(f"deleted {store.path}")
        return 0
    if args.action == "update":
        n = store.learn(max_files=10_000)
        print(f"read {n} session file(s) into {store.path}")
        return 0
    learned = store.vocab()
    repo = str(Path(args.repo).resolve())
    out = {
        "path": str(store.path),
        "user_terms": len(learned.user),
        "scopes": {s: len(t) for s, t in sorted(learned.scopes.items())},
        "files_read": len(learned.marks),
        "top": [
            {"term": c.canonical, "origin": c.origin.value, "score": round(c.score, 3)}
            for c in store.candidates(repo)[: args.limit]
        ],
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["vocab"]:
        return vocab_main(argv[1:])
    ap = argparse.ArgumentParser(prog="python -m skriv_client.context", description=__doc__)
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before collecting")
    ap.add_argument("--deadline", type=float, default=1.0, help="collect() deadline in seconds")
    ap.add_argument("--max-terms", type=int, default=30)
    ap.add_argument(
        "--diagnostics", action="store_true", help="include terminal/session/repo detail"
    )
    ap.add_argument("--canonicalize", metavar="TEXT", help="rewrite TEXT with the collected terms")
    args = ap.parse_args(argv, namespace=_Args())
    if args.delay > 0:
        time.sleep(args.delay)
    snapshot, diag = collect_detailed(args.deadline, args.max_terms)
    out: dict[str, object] = {"snapshot": _jsonable(snapshot)}
    if args.diagnostics:
        repo = diag.repo
        out["diagnostics"] = {
            "terminal": _jsonable(diag.terminal),
            "session": _jsonable(diag.session),
            "repo": None
            if repo is None
            else {
                "root": repo.root,
                "branch": repo.branch,
                "head": repo.head,
                "files": len(repo.files),
                "recent_files": list(repo.recent_files[:20]),
                "identifiers": list(repo.identifiers[:40]),
            },
            "screen": None
            if diag.screen is None
            else {
                "origin": diag.screen.origin.value,
                "source": diag.screen.source,
                "detail": diag.screen.detail,
                "chunks": len(diag.screen.chunks),
            },
            "learned_candidates": len(diag.learned),
            "stage_ms": diag.stage_ms,
        }
    if args.canonicalize is not None:
        text, reps = canonicalize(args.canonicalize, [], snapshot.terms)
        out["canonicalized"] = {"text": text, "replacements": _jsonable(reps)}
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
