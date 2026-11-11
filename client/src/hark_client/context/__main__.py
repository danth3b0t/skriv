"""Debug CLI: print the context snapshot as JSON.

    uv run python -m hark_client.context --delay 3   # then focus the window to inspect

``--canonicalize TEXT`` additionally runs the canonicalizer on TEXT with the collected terms.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from typing import cast

from hark_client.context.canon import canonicalize
from hark_client.context.engine import collect_detailed


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
    max_terms: int = 60
    diagnostics: bool = False
    canonicalize: str | None = None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hark_client.context", description=__doc__)
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before collecting")
    ap.add_argument("--deadline", type=float, default=1.0, help="collect() deadline in seconds")
    ap.add_argument("--max-terms", type=int, default=60)
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
