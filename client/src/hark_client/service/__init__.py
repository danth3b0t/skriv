"""`hark service install|uninstall|status`: run hark's processes as always-on user services.

Components: ``client`` (the `hark run` daemon), ``server`` (`hark-server`) and ``llama``
(llama-server for text cleanup). Each platform has a backend with the same three operations:
systemd user units on Linux (:mod:`hark_client.service.linux`), launchd on macOS.
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


@dataclass(frozen=True)
class Status:
    component: str
    name: str  # unit file / launchd label
    installed: bool
    enabled: bool  # starts with the session / at boot
    active: bool  # running (and, where the platform reports it, ready)
    detail: str = ""


class Backend(Protocol):
    def install(
        self, components: Sequence[str], *, start: bool, force: bool, dry_run: bool
    ) -> int: ...
    def uninstall(self, components: Sequence[str]) -> int: ...
    def status(self, components: Sequence[str]) -> list[Status]: ...


def backend() -> Backend:
    """The backend for this platform; ``NotImplementedError`` where there is none."""
    name = {"linux": "linux", "darwin": "macos"}.get(sys.platform)
    try:
        module = importlib.import_module(f"hark_client.service.{name}") if name else None
    except ImportError:
        module = None
    if module is None:
        raise NotImplementedError(f"hark service is not available on {sys.platform}")
    return cast("Callable[[], Backend]", vars(module)["backend"])()


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # pyright: ignore[reportPrivateUsage]
    p = sub.add_parser("service", help="install/inspect hark as always-on user services")
    p.add_argument("service_action", choices=["install", "uninstall", "status"])
    p.add_argument(
        "components",
        nargs="*",
        metavar="COMPONENT",
        help=f"{', '.join(COMPONENTS)} (install/uninstall default: client; status: all)",
    )
    p.add_argument("--no-start", action="store_true", help="install: enable but do not start")
    p.add_argument("--force", action="store_true", help="install: replace a unit you edited")
    p.add_argument(
        "--dry-run", action="store_true", help="install: print what would be written and run"
    )


def main(
    action: str, components: Sequence[str], *, no_start: bool, force: bool, dry_run: bool
) -> int:
    unknown = [c for c in components if c not in COMPONENTS]
    if unknown:
        print(
            f"hark service: unknown component {unknown[0]!r} (use: {', '.join(COMPONENTS)})",
            file=sys.stderr,
        )
        return 2
    selected = list(dict.fromkeys(components)) or (
        list(COMPONENTS) if action == "status" else ["client"]
    )
    try:
        b = backend()
    except NotImplementedError as e:
        print(f"hark service: {e}", file=sys.stderr)
        return 2
    if action == "install":
        return b.install(selected, start=not no_start, force=force, dry_run=dry_run)
    if action == "uninstall":
        return b.uninstall(selected)
    rows = b.status(selected)
    print(format_status(rows))
    return 0 if all(r.active for r in rows) else EXIT_NOT_RUNNING


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
