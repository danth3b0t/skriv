"""`hark doctor`: ASR providers, mic, permissions, paste tools, control socket, context."""

from __future__ import annotations

import shutil
import sys
import time
from typing import TYPE_CHECKING

from skriv.v1 import skriv_pb2 as pb
from skriv_client import ctl
from skriv_client.feedback import sound_player
from skriv_client.inject import detect_method
from skriv_client.providers import Router

if TYPE_CHECKING:
    from skriv_client.config import Config

TOOLS = {
    "hyprland": ["wl-copy", "wl-paste", "hyprctl"],
    "wtype": ["wl-copy", "wl-paste", "wtype"],
    "x11": ["xclip", "xdotool"],
}


class Report:
    def __init__(self) -> None:
        self.failures: int = 0

    def ok(self, what: str, detail: str = "") -> None:
        print(f"  ok    {what}{': ' + detail if detail else ''}")

    def warn(self, what: str, detail: str) -> None:
        print(f"  warn  {what}: {detail}")

    def fail(self, what: str, detail: str) -> None:
        self.failures += 1
        print(f"  FAIL  {what}: {detail}")


def check_providers(r: Report, cfg: Config) -> None:
    """Every provider's health and capabilities, and which one a dictation would use now."""
    try:
        router = Router(cfg.providers)
    except OSError as e:
        print("providers")
        r.fail("TLS files", str(e))
        return
    try:
        router.refresh()
        for p in router.providers:
            c = p.cfg
            tls = "TLS" if c.tls_ca else "plaintext"
            context = "sends context" if c.send_context else "audio only (send_context = false)"
            print(f"provider {p.name}: {c.address} ({tls}, priority {c.priority}, {context})")
            h = p.health
            if h.state == "ok":
                r.ok("health", f"SERVING in {h.rtt_ms:.0f} ms (max {c.max_latency_ms} ms)")
            else:
                r.warn("health", f"{h.state}: {h.detail}")
                continue
            caps = p.caps.get()
            if caps is None:
                r.warn("capabilities", "GetCapabilities failed")
                continue
            _describe(r, caps)
        print("routing")
        chosen, route = router.pick()
        path = " -> ".join(f"{x['name']}:{x['state']}" for x in route)
        if chosen.health.state == "ok":
            r.ok("next dictation", f"{chosen.name} (route {path})")
        else:
            r.fail("next dictation", f"no provider is healthy (route {path})")
    finally:
        router.close()


def _describe(r: Report, caps: pb.Capabilities) -> None:
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
        from skriv_client import mic  # noqa: PLC0415 - PortAudio may be missing

        name, rate = mic.describe_input(cfg.audio.device)
        r.ok("input device", f"{name} @ {rate} Hz")
        from skriv_client.audio import flat, rms_dbfs  # noqa: PLC0415

        pcm = mic.record(0.5, cfg.audio.device)
        level, constant = rms_dbfs(pcm), flat(pcm)
    except (
        OSError,
        ValueError,
    ) as e:  # PortAudioError subclasses Exception; OSError covers lib load
        r.fail("input device", str(e))
        return
    except Exception as e:  # noqa: BLE001 - report any PortAudio failure, do not crash doctor
        r.fail("input device", f"{type(e).__name__}: {e}")
        return
    if level < -80 or constant:
        hint = (
            "grant Microphone access to Hark.app or your terminal (System Settings > Privacy & Security)"
            if sys.platform == "darwin"
            else "check the input source/mute (pavucontrol, wpctl)"
        )
        what = "digital silence" if level < -80 else "a flat signal (every sample the same)"
        r.warn("level", f"{level:.0f} dBFS over 0.5 s: {what}; {hint}")
    else:
        r.ok("level", f"{level:.0f} dBFS over 0.5 s")


def check_permissions(r: Report) -> None:
    if sys.platform != "darwin":
        return
    print("macOS permissions (of Hark.app, or of the terminal that runs `hark run`)")
    from skriv_client import macos  # noqa: PLC0415

    mic = macos.mic_authorization()
    if mic == "authorized":
        r.ok("Microphone", "allowed")
    elif mic == "not_determined":
        r.warn("Microphone", "not asked yet (Hark.app: Setup & Permissions)")
    else:
        r.fail("Microphone", f"{mic}: System Settings > Privacy & Security > Microphone")
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
    from skriv_client import context  # noqa: PLC0415

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
    check_providers(r, cfg)
    check_mic(r, cfg)
    check_permissions(r)
    check_paste(r, cfg)
    check_daemon(r)
    check_context(r, cfg)
    print("all good" if r.failures == 0 else f"{r.failures} problem(s)")
    return 0 if r.failures == 0 else 1
