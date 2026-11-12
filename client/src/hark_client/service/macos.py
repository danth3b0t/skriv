"""macOS launchd user agents for the local inference stack, and Hark.app's login item.

The ``hark service`` backend on macOS (components ``server`` and ``llama``; the client is
Hark.app). ``hark service install`` writes ``~/Library/LaunchAgents/dev.hark.{llama,server}.plist`` and
bootstraps them into the GUI session: llama-server (cleanup, via scripts/llama-up) and
hark-server (ASR), the same commands scripts/poc-up starts, kept alive by launchd and logging to
``~/Library/Logs/hark/{llama,server}.log``. They run from this source checkout and its venv.

Launch at login for Hark.app uses ``SMAppService.mainApp`` (macOS 13+, works ad-hoc signed) and
falls back to a LaunchAgent that ``open``s the bundle.
"""
# pyright: reportAny=false, reportExplicitAny=false

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import platform
import plistlib
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hark_client.service import InstallOptions, ServiceError, Status

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

log = logging.getLogger("hark.service")

LLAMA = "dev.hark.llama"
SERVER = "dev.hark.server"
APP = "dev.hark.app"  # login-item fallback (LaunchAgent)
LLAMA_URL = "http://127.0.0.1:8080/v1"
PORTS = {LLAMA: 8080, SERVER: 50051}
LABELS = {"server": SERVER, "llama": LLAMA}  # `hark service` component -> agent


@dataclass(frozen=True)
class Layout:
    """Where the agents' programs and files live."""

    root: Path  # source checkout (scripts/, models/)
    venv: Path
    home: Path

    @property
    def agents_dir(self) -> Path:
        return self.home / "Library" / "LaunchAgents"

    @property
    def logs_dir(self) -> Path:
        return self.home / "Library" / "Logs" / "hark"

    def plist(self, label: str) -> Path:
        return self.agents_dir / f"{label}.plist"


def layout() -> Layout:
    """This interpreter's venv and the checkout hark_client was installed from (editable)."""
    root = Path(__file__).resolve().parents[4]
    if not (root / "scripts" / "llama-up").is_file():
        raise ServiceError(f"hark service needs a source checkout (no scripts/llama-up in {root})")
    return Layout(root=root, venv=Path(sys.prefix), home=Path.home())


def default_asr_backend() -> str:
    return "parakeet_mlx" if (sys.platform, platform.machine()) == ("darwin", "arm64") else "mock"


def default_cleanup_model(root: Path) -> str:
    """The DEFAULT= line of scripts/fetch-models (as scripts/llama-up reads it)."""
    m = re.search(r"^DEFAULT=(\S+)$", (root / "scripts" / "fetch-models").read_text(), re.MULTILINE)
    if m is None:
        raise ServiceError("no DEFAULT= in scripts/fetch-models")
    return m.group(1)


def agent_plist(
    label: str, program: list[str], lay: Layout, log: str, *, keep_alive: bool = True
) -> dict[str, object]:
    return {
        "Label": label,
        "ProgramArguments": program,
        "WorkingDirectory": str(lay.root),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "RunAtLoad": True,
        "KeepAlive": keep_alive,
        "ThrottleInterval": 10,  # seconds between restarts of a crashing server
        "ProcessType": "Interactive",  # no background throttling: dictation is latency-bound
        "StandardOutPath": str(lay.logs_dir / log),
        "StandardErrorPath": str(lay.logs_dir / log),
    }


def stack_plists(
    lay: Layout, *, backend: str, cleanup_model: str | None
) -> dict[str, dict[str, object]]:
    """label -> plist; no llama-server when ``cleanup_model`` is None."""
    server = [str(lay.venv / "bin" / "hark-server"), "--backend", backend]
    plists: dict[str, dict[str, object]] = {}
    if cleanup_model is not None:
        engine = "s1" if cleanup_model.startswith("s1-mini") else "edits"
        server += ["--cleanup-url", LLAMA_URL, "--cleanup-model", cleanup_model]
        server += ["--cleanup-engine", engine]
        llama = [str(lay.root / "scripts" / "llama-up"), cleanup_model]
        plists[LLAMA] = agent_plist(LLAMA, llama, lay, "llama.log")
    plists[SERVER] = agent_plist(SERVER, server, lay, "server.log")
    return plists


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed program, no shell
        ["/bin/launchctl", *args], capture_output=True, text=True, check=False
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def install(
    lay: Layout, *, backend: str, cleanup_model: str | None, out: Callable[[str], None] = print
) -> list[str]:
    """Write and (re)load the agents; returns their labels. Raises ServiceError."""
    if not (lay.venv / "bin" / "hark-server").is_file():
        raise ServiceError(f"{lay.venv}/bin/hark-server missing; run `uv sync`")
    if cleanup_model is not None:
        if not (lay.root / "models" / "bin" / "llama-server").is_file():
            raise ServiceError("models/bin/llama-server missing; run scripts/fetch-llama")
        if not (lay.root / "models" / f"{cleanup_model}.gguf").is_file():
            raise ServiceError(
                f"models/{cleanup_model}.gguf missing; run scripts/fetch-models {cleanup_model}"
            )
    plists = stack_plists(lay, backend=backend, cleanup_model=cleanup_model)
    lay.agents_dir.mkdir(parents=True, exist_ok=True)
    lay.logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stale = {LLAMA, SERVER} - plists.keys()
    for label in stale:  # e.g. installed with cleanup before, now --no-cleanup
        _remove(lay, label)
    for label, plist in plists.items():
        launchctl("bootout", f"{_domain()}/{label}")  # not loaded yet: fails, fine
        path = lay.plist(label)
        path.write_bytes(plistlib.dumps(plist, sort_keys=True))
        if _port_busy(PORTS[label]):
            out(
                f"warning: 127.0.0.1:{PORTS[label]} is in use (scripts/poc-up still running?);"
                f" {label} keeps restarting until it is free"
            )
        r = launchctl("bootstrap", _domain(), str(path))
        if r.returncode != 0:
            raise ServiceError(f"launchctl bootstrap {path}: {(r.stderr or r.stdout).strip()}")
        out(f"installed {label} -> {lay.logs_dir / plist_log(plist)}")
    return list(plists)


def plist_log(plist: dict[str, object]) -> str:
    return Path(str(plist["StandardOutPath"])).name


def installed(lay: Layout) -> list[str]:
    return [label for label in (LLAMA, SERVER) if lay.plist(label).is_file()]


def _remove(lay: Layout, label: str) -> bool:
    launchctl("bootout", f"{_domain()}/{label}")
    path = lay.plist(label)
    if not path.is_file():
        return False
    path.unlink()
    return True


def uninstall(
    lay: Layout, labels: Sequence[str] = (LLAMA, SERVER), out: Callable[[str], None] = print
) -> int:
    """Stop and remove the agents (logs stay); returns how many were installed."""
    n = 0
    for label in labels:
        if _remove(lay, label):
            out(f"removed {label}")
            n += 1
    return n


@dataclass(frozen=True)
class AgentState:
    label: str
    loaded: bool
    state: str  # launchd's "state" (running, not running, ...) or "not loaded"
    pid: int | None
    last_exit: str | None


def agent_states(lay: Layout) -> list[AgentState]:
    result: list[AgentState] = []
    for label in installed(lay):
        r = launchctl("print", f"{_domain()}/{label}")
        if r.returncode != 0:
            result.append(
                AgentState(label, loaded=False, state="not loaded", pid=None, last_exit=None)
            )
            continue

        def field(name: str, text: str = r.stdout) -> str | None:
            m = re.search(rf"^\t{name} = (.+)$", text, re.MULTILINE)  # top level only
            return m.group(1).strip() if m else None

        pid = field("pid")
        result.append(
            AgentState(
                label, loaded=True, state=field("state") or "?",
                pid=int(pid) if pid and pid.isdigit() else None,
                last_exit=field("last exit code"),
            )
        )  # fmt: skip
    return result


def restart(lay: Layout, labels: Sequence[str]) -> list[str]:
    for label in labels:
        r = launchctl("kickstart", "-k", f"{_domain()}/{label}")
        if r.returncode != 0:
            raise ServiceError(f"launchctl kickstart {label}: {(r.stderr or r.stdout).strip()}")
    return list(labels)


class LaunchdBackend:
    components: tuple[str, ...] = ("server", "llama")
    install_default: tuple[str, ...] = ("server", "llama")

    def install(self, components: Sequence[str], opts: InstallOptions) -> int:
        if not opts.start or opts.force or opts.dry_run:
            return _fail("--no-start, --force and --dry-run are for systemd units (Linux)", 2)
        if "server" not in components:
            return _fail("llama-server runs for hark-server: hark service install server llama", 2)
        try:
            lay = layout()
            model = (
                (opts.cleanup_model or default_cleanup_model(lay.root))
                if "llama" in components
                else None
            )
            install(lay, backend=opts.asr_backend or default_asr_backend(), cleanup_model=model)
        except ServiceError as e:
            return _fail(str(e), 1)
        print("check with `hark service status` and `hark doctor`")
        return 0

    def uninstall(self, components: Sequence[str]) -> int:
        try:
            lay = layout()
        except ServiceError as e:
            return _fail(str(e), 1)
        if not uninstall(lay, [LABELS[c] for c in components]):
            print("no hark services installed")
        return 0

    def status(self, components: Sequence[str]) -> list[Status]:
        try:
            lay = layout()
        except ServiceError as e:
            return [Status(c, LABELS[c], installed=False, enabled=False, active=False,
                           detail=str(e)) for c in components]  # fmt: skip
        states = {a.label: a for a in agent_states(lay)}
        rows: list[Status] = []
        for c in components:
            a = states.get(LABELS[c])
            if a is None:
                rows.append(Status(c, LABELS[c], installed=False, enabled=False, active=False))
                continue
            run = f"pid {a.pid}" if a.pid else f"{a.state}, last exit {a.last_exit or '-'}"
            rows.append(
                Status(
                    c, a.label, installed=True, enabled=a.loaded, active=a.state == "running",
                    detail=f"{run}, {lay.logs_dir / f'{c}.log'}",
                )
            )  # fmt: skip
        return rows

    def restart(self, components: Sequence[str]) -> int:
        try:
            labels = restart(layout(), [LABELS[c] for c in components])
        except ServiceError as e:
            return _fail(str(e), 1)
        print(f"restarted {', '.join(labels)}")
        return 0


def _fail(message: str, code: int) -> int:
    print(f"hark service: {message}", file=sys.stderr)
    return code


def backend() -> LaunchdBackend:
    return LaunchdBackend()


# --- Hark.app launch at login -------------------------------------------------------------


def _sm_app_service() -> Any | None:  # noqa: ANN401 - pyobjc is untyped
    """SMAppService.mainAppService, or None (not macOS 13+, or not running inside an app)."""
    if sys.platform != "darwin":
        return None
    objc: Any = importlib.import_module("objc")
    foundation: Any = importlib.import_module("Foundation")
    if not str(foundation.NSBundle.mainBundle().bundlePath()).endswith(".app"):
        return None
    try:
        objc.loadBundle(
            "ServiceManagement", {},
            bundle_path="/System/Library/Frameworks/ServiceManagement.framework",
        )  # fmt: skip
        cls = objc.lookUpClass("SMAppService")
    except Exception as e:  # noqa: BLE001 - objc.error: no such framework/class (macOS < 13)
        log.info("SMAppService unavailable (%s); using a LaunchAgent for launch at login", e)
        return None
    for sel in (b"registerAndReturnError:", b"unregisterAndReturnError:"):
        # pyobjc has no metadata for the NSError** out-parameter without its framework wrapper
        objc.registerMetaDataForSelector(
            b"SMAppService", sel, {"retval": {"type": b"Z"}, "arguments": {2: {"type_modifier": b"o"}}}
        )  # fmt: skip
    return cls.mainAppService()


SM_ENABLED, SM_REQUIRES_APPROVAL = 1, 2


def app_login_plist(app: Path) -> dict[str, object]:
    return {
        "Label": APP,
        "ProgramArguments": ["/usr/bin/open", "-a", str(app)],
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",
    }


def login_item_enabled(home: Path) -> bool:
    svc = _sm_app_service()
    if svc is not None:
        return int(svc.status()) in (SM_ENABLED, SM_REQUIRES_APPROVAL)
    return (home / "Library" / "LaunchAgents" / f"{APP}.plist").is_file()


def set_login_item(home: Path, app: Path, *, enabled: bool) -> str:
    """Turn launch at login on/off; returns a message for the user. Raises ServiceError."""
    svc = _sm_app_service()
    if svc is not None:
        ok, err = (svc.registerAndReturnError_ if enabled else svc.unregisterAndReturnError_)(None)
        if not ok:
            raise ServiceError(f"SMAppService: {err.localizedDescription() if err else '?'}")
        if int(svc.status()) == SM_REQUIRES_APPROVAL:
            return "Allow Hark in System Settings > General > Login Items"
        return "Hark opens at login" if enabled else "Hark no longer opens at login"
    path = home / "Library" / "LaunchAgents" / f"{APP}.plist"
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(app_login_plist(app), sort_keys=True))
        return "Hark opens at login (LaunchAgent)"
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    return "Hark no longer opens at login"
