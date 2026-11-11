"""Focused application/window (and selection where the platform exposes it).

- macOS: frontmost app via Accessibility/NSWorkspace; window title and selected text via
  Accessibility (needs the Accessibility permission; reported as an error otherwise).
- Linux/Hyprland: ``hyprctl -j activewindow`` (class, title, pid, address).
- Anything else: empty FocusInfo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import cast

from hark_client.context.types import FocusInfo

if sys.platform == "darwin":
    # imported eagerly: loading pyobjc takes ~100 ms, too slow for the first hotkey press
    from hark_client.context import _macos as mac
else:
    mac = None

# macOS bundle ids
TERMINAL_BUNDLE_IDS = frozenset(
    {
        "com.apple.Terminal",
        "com.googlecode.iterm2",
        "com.mitchellh.ghostty",
        "net.kovidgoyal.kitty",
        "com.github.wez.wezterm",
        "org.alacritty",
        "io.alacritty",
        "dev.warp.Warp-Stable",
        "dev.warp.Warp-Preview",
        "dev.warp.Warp",
    }
)
# Linux window classes (compared lower-case)
TERMINAL_CLASSES = frozenset(
    {
        "foot",
        "footclient",
        "kitty",
        "alacritty",
        "org.wezfurlong.wezterm",
        "wezterm",
        "com.mitchellh.ghostty",
        "ghostty",
        "dev.warp.warp",
        "warp",
        "xterm",
        "st",
        "st-256color",
        "konsole",
        "org.kde.konsole",
        "org.gnome.terminal",
        "gnome-terminal-server",
        "org.gnome.ptyxis",
        "com.raggesilver.blackbox",
    }
)
HYPRCTL_TIMEOUT_S = 0.3


@dataclass(frozen=True, slots=True)
class FocusResult:
    focus: FocusInfo
    selection: str | None = None
    errors: tuple[str, ...] = ()


def is_terminal(app_id: str) -> bool:
    return app_id in TERMINAL_BUNDLE_IDS or app_id.lower() in TERMINAL_CLASSES


def parse_hyprctl(raw: str) -> FocusInfo:
    """FocusInfo from ``hyprctl -j activewindow`` output (``{}`` when nothing is focused)."""
    data = cast("object", json.loads(raw or "{}"))
    if not isinstance(data, dict):
        return FocusInfo()
    obj = cast("dict[str, object]", data)
    cls = obj.get("class")
    title = obj.get("title")
    pid = obj.get("pid")
    address = obj.get("address")
    app_id = cls if isinstance(cls, str) else ""
    return FocusInfo(
        app=app_id,
        app_id=app_id,
        title=title if isinstance(title, str) else "",
        pid=pid if isinstance(pid, int) and pid > 0 else None,
        is_terminal=is_terminal(app_id),
        window_id=address if isinstance(address, str) else "",
    )


def _hyprland() -> FocusResult:
    exe = shutil.which("hyprctl")
    if exe is None:
        return FocusResult(FocusInfo(), errors=("focus: hyprctl not found",))
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [exe, "-j", "activewindow"],
        capture_output=True,
        text=True,
        timeout=HYPRCTL_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return FocusResult(FocusInfo(), errors=(f"focus: hyprctl exit {proc.returncode}",))
    return FocusResult(parse_hyprctl(proc.stdout))


def get_focus(ax_timeout_s: float = 0.1) -> FocusResult:
    if sys.platform == "darwin":
        return _macos_focus(ax_timeout_s)
    if sys.platform.startswith("linux") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return _hyprland()
    return FocusResult(FocusInfo())


def _macos_focus(ax_timeout_s: float) -> FocusResult:
    if mac is None:
        return FocusResult(FocusInfo())
    app = mac.front_app(ax_timeout_s)
    if app is None:
        return FocusResult(FocusInfo(), errors=("focus: no frontmost application",))
    errors: list[str] = []
    title: str | None = None
    selection: str | None = None
    if mac.ax_trusted():
        title, selection = mac.window_title_and_selection(app.pid, ax_timeout_s)
    else:
        errors.append("focus: accessibility permission not granted (no title/selection)")
    focus = FocusInfo(
        app=app.name,
        app_id=app.bundle_id,
        title=title or "",
        pid=app.pid,
        is_terminal=is_terminal(app.bundle_id),
        window_id=str(app.pid),
    )
    return FocusResult(focus, selection, tuple(errors))
