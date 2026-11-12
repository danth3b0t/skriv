"""`hark service install|uninstall|status|restart`: run hark's processes as always-on user services.

Components: ``client`` (the `hark run` daemon), ``server`` (`skriv-server`) and ``llama``
(llama-server for text cleanup). Each platform has a backend with the same four operations:
systemd user units on Linux (:mod:`skriv_client.service.linux`), launchd agents on macOS
(:mod:`skriv_client.service.macos`; there the client is Hark.app, which opens at login from its
menu, so only ``server`` and ``llama`` are services).
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Sequence

COMPONENTS = ("client", "server", "llama")
EXIT_NOT_RUNNING = 3  # `status`: some component is not running (systemctl/LSB convention)
NONE_INSTALLED = "no hark services installed (`hark service install`)"


class ServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Status:
    component: str
    name: str  # unit file / launchd label
    installed: bool
    enabled: bool  # starts with the session / at boot
    active: bool  # running (and, where the platform reports it, ready)
    detail: str = ""


@dataclass(frozen=True)
class InstallOptions:
    start: bool = True  # Linux: enable --now (else enable only)
    force: bool = False  # Linux: replace a unit hark did not write
    dry_run: bool = False  # Linux: print the units and systemctl calls only
    asr_backend: str | None = None  # macOS: skriv-server --backend (default by machine)
    cleanup_model: str | None = None  # macOS: the scripts/fetch-models key llama-server runs


class Backend(Protocol):
    @property
    def components(self) -> tuple[str, ...]:
        """The components this platform runs as services."""
        ...

    @property
    def install_default(self) -> tuple[str, ...]:
        """What install/uninstall act on when no component is named."""
        ...

    def install(self, components: Sequence[str], opts: InstallOptions) -> int: ...
    def uninstall(self, components: Sequence[str]) -> int: ...
    def status(self, components: Sequence[str]) -> list[Status]: ...
    def restart(self, components: Sequence[str]) -> int: ...


def backend() -> Backend:
    """The backend for this platform; ``NotImplementedError`` where there is none."""
    name = {"linux": "linux", "darwin": "macos"}.get(sys.platform)
    try:
        module = importlib.import_module(f"skriv_client.service.{name}") if name else None
    except ImportError:
        module = None
    if module is None:
        raise NotImplementedError(f"hark service is not available on {sys.platform}")
    return cast("Callable[[], Backend]", vars(module)["backend"])()


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # pyright: ignore[reportPrivateUsage]
    p = sub.add_parser(
        "service",
        help="always-on services: systemd user units (Linux), launchd agents (macOS)",
    )
    p.add_argument("service_action", choices=["install", "uninstall", "status", "restart"])
    p.add_argument(
        "components",
        nargs="*",
        metavar="COMPONENT",
        help=f"{', '.join(COMPONENTS)} (macOS: server, llama; the client is Hark.app). Default:"
        " install/uninstall: client (Linux), server and llama (macOS); status/restart: the"
        " installed ones",
    )
    p.add_argument("--no-start", action="store_true", help="install (Linux): enable, do not start")
    p.add_argument(
        "--force", action="store_true", help="install (Linux): replace a unit you edited"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="install (Linux): print what would be written and run",
    )
    p.add_argument(
        "--backend",
        dest="asr_backend",
        help="install (macOS): ASR backend (default parakeet_mlx on Apple Silicon)",
    )
    p.add_argument(
        "--cleanup-model",
        help="install (macOS): a scripts/fetch-models key (default: its DEFAULT)",
    )
    p.add_argument("--no-cleanup", action="store_true", help="install: without llama-server")


def main(  # noqa: PLR0911, PLR0913 - one exit per usage error; keyword-only CLI flags
    action: str,
    components: Sequence[str],
    *,
    no_start: bool = False,
    force: bool = False,
    dry_run: bool = False,
    asr_backend: str | None = None,
    cleanup_model: str | None = None,
    no_cleanup: bool = False,
) -> int:
    unknown = [c for c in components if c not in COMPONENTS]
    if unknown:
        return _usage(f"unknown component {unknown[0]!r} (use: {', '.join(COMPONENTS)})")
    try:
        b = backend()
    except NotImplementedError as e:
        return _usage(str(e))
    foreign = [c for c in components if c not in b.components]
    if foreign:
        hint = (
            "; the client is Hark.app: turn on Launch at Login in its menu"
            if foreign[0] == "client" and sys.platform == "darwin"
            else ""
        )
        return _usage(f"no {foreign[0]} service on this platform{hint}")
    selected = list(dict.fromkeys(components))
    if action == "install":
        if no_cleanup and "llama" in selected:
            return _usage("--no-cleanup and llama contradict each other")
        selected = [c for c in selected or b.install_default if not (no_cleanup and c == "llama")]
        opts = InstallOptions(
            start=not no_start, force=force, dry_run=dry_run,
            asr_backend=asr_backend, cleanup_model=cleanup_model,
        )  # fmt: skip
        return b.install(selected, opts)
    if action == "uninstall":
        return b.uninstall(selected or list(b.install_default))
    if selected:
        rows = b.status(selected)
    else:
        rows = b.status(list(b.components))
        rows = [r for r in rows if r.installed] or rows
    if action == "restart":
        targets = [r.component for r in rows if r.installed]
        if not targets:
            print(NONE_INSTALLED)
            return 1
        return b.restart(targets)
    print(format_status(rows))
    if not any(r.installed for r in rows):
        print(NONE_INSTALLED)
    return 0 if all(r.active for r in rows) else EXIT_NOT_RUNNING


def _usage(message: str) -> int:
    print(f"hark service: {message}", file=sys.stderr)
    return 2


def format_status(rows: Sequence[Status]) -> str:
    lines: list[str] = []
    for r in rows:
        state = (
            "not installed"
            if not r.installed
            else f"{'running' if r.active else 'stopped'}, {'enabled' if r.enabled else 'disabled'}"
        )
        detail = f"  ({r.detail})" if r.detail else ""
        lines.append(f"{r.component:<7} {r.name:<22} {state}{detail}")
    return "\n".join(lines)
