"""`hark doctor`: server, mic, permissions, paste tools, control socket, context."""

from __future__ import annotations

import shutil
import sys
import time
from typing import TYPE_CHECKING, cast

import grpc
from grpc_health.v1 import health_pb2

from hark.v1 import hark_pb2 as pb
from hark_client import asr, ctl
from hark_client.feedback import sound_player
from hark_client.inject import detect_method

if TYPE_CHECKING:
    from hark_client.config import Config

SERVING_STATUS = {0: "UNKNOWN", 1: "SERVING", 2: "NOT_SERVING", 3: "SERVICE_UNKNOWN"}
TOOLS = {
    "hyprland": ["wl-copy", "wl-paste", "hyprctl"],
    "wtype": ["wl-copy", "wl-paste", "wtype"],
    "x11": ["xclip", "xdotool"],
}


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, what: str, detail: str = "") -> None:
        print(f"  ok    {what}{': ' + detail if detail else ''}")

    def warn(self, what: str, detail: str) -> None:
        print(f"  warn  {what}: {detail}")

    def fail(self, what: str, detail: str) -> None:
        self.failures += 1
        print(f"  FAIL  {what}: {detail}")


def _health(channel: grpc.Channel, service: str) -> str:
    check = channel.unary_unary(
        "/grpc.health.v1.Health/Check",
        request_serializer=health_pb2.HealthCheckRequest.SerializeToString,
        response_deserializer=health_pb2.HealthCheckResponse.FromString,
    )
    resp = check(health_pb2.HealthCheckRequest(service=service), timeout=2.0)
    status = cast("int", resp.status)  # the generated stub types the enum as Any
    return SERVING_STATUS.get(status, str(status))


def check_server(r: Report, cfg: Config) -> None:
    print(f"server {cfg.server.address} ({'TLS' if cfg.server.tls_ca else 'plaintext'})")
    try:
        channel = asr.open_channel(cfg.server)
    except OSError as e:
        r.fail("TLS files", str(e))
        return
    with channel:
        try:
            r.ok("health", _health(channel, "hark.v1.Asr"))
        except grpc.RpcError as e:
            r.fail("health", asr.rpc_error_text(e))
            return
        caps = asr.Capabilities(channel).refresh()
        if caps is None:
            r.fail("capabilities", "GetCapabilities failed")
            return
        r.ok(
            "server",
            f"{caps.server.name} ({caps.server.implementation}, {caps.server.accelerator or '?'})",
        )
        for m in caps.models:
            state = pb.ModelState.Name(m.state).removeprefix("MODEL_STATE_")
            bias = pb.BiasMechanism.Name(m.bias.mechanism).removeprefix("BIAS_MECHANISM_")
            line = f"{m.model_id}{' (default)' if m.is_default else ''}: {state}, bias {bias}"
            (r.ok if state == "READY" else r.warn)("model", line)
        r.ok("features", ", ".join(caps.features) or "none")
        if caps.cleanup_profiles:
            r.ok("cleanup profiles", ", ".join(p.id for p in caps.cleanup_profiles))
        else:
            r.warn("cleanup", "server has no TextCleanup; cleanup will be skipped")


def check_mic(r: Report, cfg: Config) -> None:
    print("microphone")
    try:
        from hark_client import mic  # noqa: PLC0415 - PortAudio may be missing

        name, rate = mic.describe_input(cfg.audio.device)
        r.ok("input device", f"{name} @ {rate} Hz")
        from hark_client.audio import rms_dbfs  # noqa: PLC0415

        level = rms_dbfs(mic.record(0.5, cfg.audio.device))
    except (
        OSError,
        ValueError,
    ) as e:  # PortAudioError subclasses Exception; OSError covers lib load
        r.fail("input device", str(e))
        return
    except Exception as e:  # noqa: BLE001 - report any PortAudio failure, do not crash doctor
        r.fail("input device", f"{type(e).__name__}: {e}")
        return
    if level < -80:
        hint = (
            "grant Microphone access to your terminal (System Settings > Privacy & Security)"
            if sys.platform == "darwin"
            else "check the input source/mute (pavucontrol, wpctl)"
        )
        r.warn("level", f"{level:.0f} dBFS over 0.5 s: digital silence; {hint}")
    else:
        r.ok("level", f"{level:.0f} dBFS over 0.5 s")


def check_permissions(r: Report) -> None:
    if sys.platform != "darwin":
        return
    print("macOS permissions (for the app that runs hark, e.g. your terminal)")
    from hark_client import macos  # noqa: PLC0415

    if macos.can_listen_events():
        r.ok("Input Monitoring", "push-to-talk hotkey can see keys")
    else:
        r.fail(
            "Input Monitoring", "missing: System Settings > Privacy & Security > Input Monitoring"
        )
    if macos.can_post_events():
        r.ok("Accessibility", "can post Cmd+V")
    else:
        r.fail("Accessibility", "missing: System Settings > Privacy & Security > Accessibility")


def check_paste(r: Report, cfg: Config) -> None:
    method = cfg.paste.method if cfg.paste.method != "auto" else detect_method()
    print(f"paste ({method}{', auto-detected' if cfg.paste.method == 'auto' else ''})")
    if method == "dry-run" and cfg.paste.method == "auto":
        r.warn("display", "no Wayland/X11 display found; paste would be a dry run")
    for tool in TOOLS.get(method, []):
        if shutil.which(tool):
            r.ok(tool)
        elif tool == "hyprctl" and shutil.which("wtype"):
            r.warn(tool, "missing; falling back to wtype")
        else:
            r.fail(tool, "not found in PATH")
    if method == "hyprland" and not shutil.which("wtype"):
        r.warn("wtype", "not found; no fallback if hyprctl sendshortcut fails")
    player = sound_player()
    (r.ok if player else r.warn)(
        "sounds", player[0] if player else "no afplay/pw-play/paplay; silent"
    )


def check_daemon(r: Report) -> None:
    path = ctl.socket_path()
    print(f"daemon ({path})")
    try:
        r.ok("hark run", ctl.send("status", path, timeout=1.0))
    except OSError:
        r.warn("hark run", "not running")


def check_context(r: Report, cfg: Config) -> None:
    print("context engine")
    from hark_client import context  # noqa: PLC0415

    t = time.perf_counter()
    snap = context.collect(cfg.context.deadline_ms / 1000, cfg.context.max_terms)
    ms = (time.perf_counter() - t) * 1000
    f = snap.focus
    r.ok(
        "focus",
        f"app={f.app or '?'} app_id={f.app_id or '?'} terminal={f.is_terminal} -> profile {cfg.profile_for(f)}",
    )
    detail = f"{len(snap.terms)} terms in {ms:.0f} ms"
    if snap.errors:
        r.warn("collect", f"{detail}; errors: {'; '.join(snap.errors)}")
    else:
        r.ok("collect", detail)


def run(cfg: Config, config_path: str) -> int:
    print(f"config {config_path}")
    r = Report()
    check_server(r, cfg)
    check_mic(r, cfg)
    check_permissions(r)
    check_paste(r, cfg)
    check_daemon(r)
    check_context(r, cfg)
    print("all good" if r.failures == 0 else f"{r.failures} problem(s)")
    return 0 if r.failures == 0 else 1
