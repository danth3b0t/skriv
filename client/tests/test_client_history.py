"""`hark history` (table, show, --json) and `hark last`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from hark_client import history, historyview
from hark_client.__main__ import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from hark_client.history import Record

ID_A = "0199aaaa-0000-7000-8000-0000000000a1"
ID_B = "0199aaaa-0000-7000-8000-0000000000b2"


def record(uid: str, ts: str, **kw: object) -> Record:
    return {"utterance_id": uid, "ts": ts, "app": "Ghostty", "status": "pasted", **kw}


def local(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).astimezone()


def test_table_previews_one_line_per_dictation():
    today, older = "2026-11-12T10:00:00Z", "2026-11-10T08:30:00Z"
    rows = [
        record(
            ID_A,
            older,
            final_text="first line\nsecond line " + "x" * 60,
            timings_ms={"keyup_to_final_ms": 300.4, "keyup_to_paste_ms": 412.6},
        ),
        record(
            ID_B,
            today,
            app="",
            app_id="com.example.VeryLongAppName",
            status="error",
            error="asr: no FinalResult within 5.0 s",
        ),
    ]
    lines = historyview.table(rows, kept={ID_B}, now=local(today)).splitlines()
    assert lines[1] == (
        f"{local(older).strftime('%m-%d %H:%M')} 000000a1  Ghostty      pasted          413 ms  "
        "first line second line xxxxxxxxxxxxxxxxxxxxxxxx…"
    )
    assert lines[2] == (
        f"{local(today).strftime('%H:%M:%S')}    000000b2* com.example… error                -  "
        "[asr: no FinalResult within 5.0 s]"
    )
    assert lines[3] == "* = audio kept: `hark retry ID` re-runs it"


def test_show_lists_every_stage():
    rec = record(
        ID_A,
        "2026-11-10T08:30:00Z",
        raw_text="cube control get pods",
        canonical_text="kubectl get pods",
        final_text="kubectl get pods",
        replacements=[{"before": "cube control", "after": "kubectl", "reason": "alias"}],
        cleanup={
            "status": "partial",
            "reason": "1 of 2",
            "ms": 120.0,
            "edits": [{"find": "a", "replace": "b"}],
            "dropped": [{"find": "pods", "replace": "Pods", "reason": "protected", "by": "client"}],
        },
        gate={"decision": "accept"},
        spacing={"source": "ax", "leading": True},
        timings_ms={"keyup_to_paste_ms": 400.0},
        provider={"name": "laptop-cpu", "address": "127.0.0.1:50052", "send_context": False},
        route=[{"name": "desktop", "state": "down"}, {"name": "laptop-cpu", "state": "ok"}],
    )
    out = historyview.show(rec, kept=True).splitlines()
    assert out[0] == f"id         {ID_A}  (audio kept: hark retry 000000a1)"
    for expected in (
        "raw        'cube control get pods'",
        "             'cube control' -> 'kubectl' (alias)",
        "cleanup    partial [1 of 2] (120 ms)",
        "             edit 'a' -> 'b'",
        "             dropped 'pods' -> 'Pods' (protected, by client)",
        'gate       {"decision": "accept"}',
        "spacing    source=ax, leading=True",
        "status     pasted",
        "timings    keyup_to_paste=400.0",
        "provider   laptop-cpu (127.0.0.1:50052, audio only)  route desktop:down -> laptop-cpu:ok",
    ):
        assert expected in out


def test_history_and_last_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = tmp_path / "config.toml"
    config.write_text(f'[history]\ndir = "{tmp_path}"\n[paste]\nmethod = "dry-run"\n')
    base = ["--config", str(config)]
    assert main([*base, "last"]) == 1  # no history yet
    assert main([*base, "history"]) == 0
    assert "no history" in capsys.readouterr().out
    history.append(tmp_path, record(ID_A, "2026-11-10T08:30:00Z", final_text="hello there"))
    history.append(tmp_path, record(ID_B, "2026-11-10T08:31:00Z", status="error", final_text=""))

    assert main([*base, "history", "-n", "1", "--json"]) == 0
    listed = cast("list[Record]", json.loads(capsys.readouterr().out))
    assert [r["utterance_id"] for r in listed] == [ID_B]
    assert main([*base, "history", "show", "b2"]) == 0
    assert ID_B in capsys.readouterr().out
    assert main([*base, "history", "show", "0000000"]) == 1  # both match
    assert "2 dictations match" in capsys.readouterr().err
    assert main([*base, "history", "show"]) == 2

    assert main([*base, "last"]) == 0  # newest record with text, skipping the failed one
    assert capsys.readouterr().out == "hello there\n"
    assert main([*base, "last", "--copy"]) == 0
    assert "[dry-run] would copy: 'hello there'" in capsys.readouterr().out
