"""The hark.shell plugin (packaging/omarchy/hark.shell): its Model.js under Node, fed by the real
daemon's subscribe lines and history records. The QML itself needs Omarchy's shell and is
untested here; the install/remove scripts are in test_omarchy_adapter.py."""
# pyright: reportAny=false, reportExplicitAny=false

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from hark_client import ctl, history

from .conftest import pcm, snapshot, wait_idle

if TYPE_CHECKING:
    from collections.abc import Iterator

    from hark_client.daemon import Controller
    from hark_client.inject import DryRunInjector

    from .conftest import FakeContext

OMARCHY = Path(__file__).parents[2] / "packaging" / "omarchy"
MODEL = OMARCHY / "hark.shell" / "Model.js"
TOKENS = Path(__file__).parents[2] / "design" / "tokens" / "omarchy-hark-tokens.json"
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="Node runs the shell model")


def model(expr: str, **data: object) -> Any:
    """Evaluate ``expr`` with ``M`` = Model.js and ``d`` = ``data``; its JSON result."""
    script = f"const M = require({json.dumps(str(MODEL))})\n"
    script += "const d = JSON.parse(require('fs').readFileSync(0, 'utf8'))\n"
    script += f"process.stdout.write(JSON.stringify({expr}))\n"
    p = subprocess.run(  # noqa: S603 - fixed argv
        [cast("str", NODE), "-e", script], input=json.dumps(data), capture_output=True,
        text=True, check=True,
    )  # fmt: skip
    return json.loads(p.stdout)


def status(**over: object) -> dict[str, object]:
    """A JSON v1 status reply: idle, healthy, nothing dictated yet; ``over`` replaces fields."""
    s: dict[str, object] = {
        "version": 1, "ok": True, "command": "status", "state": "idle", "phase": "idle",
        "finishing": 0, "paused": False, "mic": {"status": "ok"},
        "server": {"name": "desktop", "state": "ok", "rtt_ms": 38.2, "fallback": False},
        "config": {"changed": False, "restart_needed": []}, "last": None,
        "learning": {"enabled": False, "watching": False}, "profile_override": None,
    }  # fmt: skip
    s.update(over)
    return s


@pytest.fixture
def ctl_socket(controller: tuple[Controller, DryRunInjector]) -> Iterator[Path]:
    """The real control socket of ``controller`` (a short path: AF_UNIX limits its length)."""
    c, _ = controller
    path = Path(tempfile.mkdtemp(prefix="hark-", dir="/tmp")) / "ctl.sock"
    server = ctl.CtlServer(path, c.handle, c.status_json, c.wait_change)
    server.start()
    yield path
    server.close()
    shutil.rmtree(path.parent, ignore_errors=True)


def subscribe_line(path: Path, request: str) -> str:
    """The first line the daemon answers to the shell's subscribe ``request``."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(str(path))
        s.sendall(request.encode())
        data = b""
        while not data.endswith(b"\n"):
            data += s.recv(4096)
    return data.decode()


@needs_node
def test_the_shell_reads_the_real_daemons_lines_and_history(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext, ctl_socket: Path
):
    c, _ = controller
    fake_context.snapshots = [snapshot("com.mitchellh.ghostty")]
    assert model("M.socketPath(d)", XDG_RUNTIME_DIR="/run/user/1000") == (
        "/run/user/1000/hark/ctl.sock"
    )
    # Node runs only while no dictation is in flight: forking during a gRPC call can stall it
    request = model("M.subscribeRequest()")
    lines = [subscribe_line(ctl_socket, request)]
    c.handle("start")
    lines.append(subscribe_line(ctl_socket, request))
    c.on_pcm(pcm(0.6))
    c.handle("stop")
    wait_idle(c)
    lines.append(subscribe_line(ctl_socket, request))
    records = history.recent(Path(c.svc.cfg.history.dir), 1)  # = `hark history -n 1 --json`
    out = model(
        "d.l.map(M.parseStatus).map(s => [M.glyphState(s, true), M.lastId(s),"
        " M.sentence(s, true, M.lastId(s))])",
        l=lines,
    )
    assert [o[0] for o in out] == ["idle", "recording", "idle"]
    assert out[2][1] == cast("dict[str, object]", c.status_json()["last"])["utterance_id"]
    assert out[2][2].startswith("hark · ready · ")
    last = model(
        "M.lastView(M.parseHistory(d.h), M.parseStatus(d.l), d.now)",
        h=json.dumps(records), l=lines[2], now=time.time() * 1000,
    )  # fmt: skip
    assert last["id"] == out[2][1]
    assert last["preview"] == records[-1]["final_text"]
    # the test server's cleanup profiles were never fetched: the terminal profile's cleanup
    # did not apply
    assert last["meta"] == "ghostty · now · cleanup skipped"
    assert (last["enterVerb"], last["canPaste"], last["failed"]) == ("copy", True, False)


@needs_node
def test_status_lines_the_shell_does_not_understand_are_dropped():
    lines = ["", "not json", '{"version": 1, "ok": false, "error": {"code": "internal"}}',
             json.dumps(status(version=2)), json.dumps({**status(), "command": "ping"})]  # fmt: skip
    assert model("d.lines.map(M.parseStatus)", lines=lines) == [None] * len(lines)


@needs_node
@pytest.mark.parametrize(
    ("over", "online", "glyph", "problem", "sentence"),
    [
        ({}, True, "idle", None, "hark · ready · desktop 38 ms"),
        ({}, False, "off", "Not running", "hark · Not running"),
        ({"paused": True, "mic": {"status": "off"}}, True, "paused", None, "hark · paused"),
        ({"phase": "recording", "state": "recording"}, True, "recording", None,
         "hark · recording"),
        ({"phase": "finishing", "finishing": 1}, True, "working", None, "hark · working"),
        # problems: the first that applies
        ({"mic": {"status": "lost"}, "server": {"name": "desktop", "state": "down"}}, True,
         "idle", "Mic lost", "hark · Mic lost"),
        ({"server": {"name": "desktop", "state": "down", "rtt_ms": None},
          "config": {"changed": True, "restart_needed": ["audio"]}}, True,
         "idle", "Server down", "hark · Server down"),
        ({"config": {"changed": True, "restart_needed": ["audio"]}}, True, "idle",
         "Restart needed", "hark · Restart needed"),
        ({"config": {"changed": True, "restart_needed": []}}, True, "idle", "Config changed",
         "hark · Config changed"),
    ],
)  # fmt: skip
def test_bar_glyph_problem_line_and_sentence(
    over: dict[str, object], online: bool, glyph: str, problem: str | None, sentence: str
):
    out = model(
        "[M.glyphState(d.s, d.on), M.problem(d.s, d.on), M.sentence(d.s, d.on, ''),"
        " M.attention(d.s, d.on, '')]",
        s=status(**over), on=online,
    )  # fmt: skip
    assert out[0] == glyph
    assert (out[1]["label"] if out[1] else None) == problem
    assert out[2] == sentence
    assert out[3] == (online and problem is not None)  # off is its own glyph, not a dot


@needs_node
def test_problem_verbs_run_the_right_command():
    kinds = ["off", "mic", "server", "restart", "config"]
    assert model("d.k.map(M.problemCommand)", k=kinds) == [
        ["systemctl", "--user", "start", "hark.service"],
        ["hark", "service", "restart"],
        None,  # Doctor: a terminal
        ["hark", "service", "restart"],
        ["hark", "ctl", "reload"],
    ]


@needs_node
def test_a_failure_draws_attention_until_the_panel_has_shown_it():
    s = status(last={"utterance_id": "u1", "status": "error", "retry_available": True})
    out = model("[M.attention(d.s, true, ''), M.attention(d.s, true, 'u1'), M.sentence(d.s, true, '')]", s=s)  # fmt: skip
    assert out == [True, False, "hark · last dictation failed"]
    assert model("M.isOnline(d.s, 0, d.now)", s=s, now=35_001) is False
    assert model("M.isOnline(d.s, 0, d.now)", s=s, now=35_000) is True


@needs_node
@pytest.mark.parametrize(
    ("server", "paused", "parts"),
    [
        ({"name": "desktop", "state": "ok", "rtt_ms": 38.4, "fallback": False}, False,
         [["desktop", False], ["38 ms", False]]),
        ({"name": "desktop", "state": "slow", "rtt_ms": 412.0, "fallback": False}, False,
         [["desktop", False], ["412 ms", True]]),
        ({"name": "laptop", "state": "ok", "rtt_ms": 9.0, "fallback": True}, False,
         [["laptop", True], ["fallback", True]]),
        ({"name": "desktop", "state": "down", "rtt_ms": None, "fallback": False}, False,
         [["desktop", False]]),
        ({"name": "desktop", "state": "ok", "rtt_ms": 38.4, "fallback": False}, True,
         [["paused", False]]),
    ],
)  # fmt: skip
def test_the_header_names_the_server_and_flags_fallback_and_slowness(
    server: dict[str, object], paused: bool, parts: list[list[object]]
):
    out = model("M.serverParts(d.s, true).map(p => [p.text, p.warn])", s=status(server=server, paused=paused))  # fmt: skip
    assert out == parts


def record(**over: object) -> dict[str, object]:
    r: dict[str, object] = {
        "ts": "2025-11-12T12:18:00Z", "utterance_id": "u1", "status": "pasted", "error": "",
        "app": "Ghostty", "final_text": "refactor learn.py", "delivery": "sent",
        "cleanup": {"status": "applied", "reason": ""}, "stop_reason": "key_up",
        "mic_interrupted": False, "timings_ms": {"held_ms": 3000.0},
    }  # fmt: skip
    r.update(over)
    return r


@needs_node
@pytest.mark.parametrize(
    ("over", "retry", "headline", "meta", "enter", "can_retry"),
    [
        ({}, False, "", "Ghostty · 2m", "copy", False),
        ({"cleanup": {"status": "timeout", "reason": "deadline"}}, False, "",
         "Ghostty · 2m · cleanup skipped", "copy", False),
        ({"cleanup": {"status": "skipped", "reason": "off for this profile"}}, False, "",
         "Ghostty · 2m", "copy", False),
        ({"stop_reason": "limit", "timings_ms": {"held_ms": 120_030.0}}, False, "",
         "Ghostty · 2m · stopped at 2:00", "copy", False),
        ({"mic_interrupted": True, "stop_reason": "limit"}, False, "",
         "Ghostty · 2m · mic dropped", "copy", False),
        ({"status": "paste_failed", "delivery": "copied"}, True, "Paste failed · in clipboard",
         "Ghostty · 2m", "copy", True),
        ({"status": "error", "error": "asr: no FinalResult within 4.0 s", "final_text": ""}, True,
         "Failed · server timeout", "Ghostty · 2m", "retry", True),
        ({"status": "error", "error": "asr: boom", "final_text": ""}, False, "Failed",
         "Ghostty · 2m", "", False),
    ],
)  # fmt: skip
def test_the_last_dictation_line(
    over: dict[str, object], retry: bool, headline: str, meta: str, enter: str, can_retry: bool
):
    s = status(last={"utterance_id": "u1", "status": "pasted", "retry_available": retry})
    out = model(
        "M.lastView(M.parseHistory(d.h), d.s, Date.parse('2025-11-12T12:20:00Z'))",
        h=json.dumps([record(ts="2025-11-12T11:00:00Z"), record(**over)]), s=s,
    )  # fmt: skip
    assert (out["headline"], out["meta"], out["enterVerb"], out["canRetry"]) == (
        headline, meta, enter, can_retry,
    )  # fmt: skip


@needs_node
def test_ages_and_unreadable_history():
    at = "Date.parse('2025-11-12T12:00:00Z')"
    ages = model(f"d.ts.map(t => M.age(t, {at}))", ts=[
        "2025-11-12T11:59:30Z", "2025-11-12T11:15:00Z", "2025-11-12T07:00:00Z",
        "2025-11-09T12:00:00Z", "garbage",
    ])  # fmt: skip
    assert ages == ["now", "45m", "5h", "3d", ""]
    assert model("['', 'x', '[]', '{}'].map(M.parseHistory)") == [None, None, None, None]


@needs_node
def test_the_cursor_starts_on_the_problem_else_on_the_last_dictation():
    out = model(
        "[M.rows(true, true, true), M.initialRow(M.rows(true, true, true), ''),"
        " M.initialRow(M.rows(true, true, true), 'last'), M.initialRow(M.rows(true, false, true), ''),"
        " M.rows(false, true, false), M.initialRow(M.rows(false, true, false), 'last'),"
        " M.moveRow(M.rows(true, false, true), 'last', 1), M.moveRow(M.rows(true, false, true), 'doctor', 1)]"
    )  # fmt: skip
    assert out == [
        ["pause", "problem", "last", "config", "doctor"],
        "problem", "last", "last",
        ["problem", "config", "doctor"],
        "problem", "config", "doctor",
    ]  # fmt: skip


@needs_node
@pytest.mark.parametrize("name", ["tokyo-night", "catppuccin-latte", "rose-pine"])
def test_shell_tokens_match_the_design(name: str):
    tok: dict[str, str] = json.loads(TOKENS.read_text())[name]
    toml = "\n".join(
        f'{k} = "{tok[v]}"'
        for k, v in (("background", "bg"), ("foreground", "fg"), ("accent", "accent"),
                     ("red", "urgent"), ("green", "green"), ("yellow", "yellow"))
    ) + '\nmode = "dark"\n'  # fmt: skip
    out = model("M.tokens(M.parseColors(d.t))", t=toml)
    assert out == {
        "text2": tok["text_secondary"], "accentInk": tok["accent_ink"], "live": tok["live_ink"],
        "ok": tok["ok_ink"], "warn": tok["warn_ink"],
    }  # fmt: skip


@needs_node
def test_glyphs_are_nerd_font_codepoints():
    assert model(
        "['alert-circle', 'copy', 'mic-off', 'nope'].map(g => [...M.glyphText(g)].map(c => c.codePointAt(0)))"
    ) == [
        [0xF05D6],
        [0xF018F],
        [0xF036D],
        [],
    ]
