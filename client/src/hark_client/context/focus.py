"""Focused application/window (and selection where the platform exposes it).

- macOS: front-most window (owner pid, window number, title if permitted) from the window
  server, app name/bundle id via NSRunningApplication (~1 ms, any thread); window title and
  selected text via Accessibility (needs the permission; reported as an error otherwise) in
  a separate thread with a hard wait, so a slow app costs only the title/selection.
  ``window_id`` = ``"<pid>:<window number>"``.
- Linux/Hyprland: ``hyprctl -j activewindow`` (class, title, pid, address).
- Anything else: empty FocusInfo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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


def _on_hyprland() -> bool:
    return sys.platform.startswith("linux") and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


def get_focus(ax_timeout_s: float = 0.05) -> FocusResult:
    if sys.platform == "darwin":
        return _macos_focus(ax_timeout_s)
    if _on_hyprland():
        return _hyprland()
    return FocusResult(FocusInfo())


def focus_token() -> str:
    """Cheap id of the focused window, equal to ``FocusInfo.window_id`` while focus stays.

    Meant for re-checking focus right before pasting (~1 ms on macOS, one ``hyprctl`` call
    on Hyprland). "" when unknown; never raises.
    """
    try:
        if sys.platform == "darwin" and mac is not None:
            win = mac.front_window()
            return f"{win.pid}:{win.number}" if win is not None else ""
        if _on_hyprland():
            return _hyprland().focus.window_id
    except Exception:  # noqa: BLE001 - a failed check must not block pasting
        return ""
    return ""


class AxWorker:
    """One Accessibility query at a time; a hung app makes later calls skip, not pile up."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hark-ax")
        self._lock = threading.Lock()
        self._job: Future[tuple[str | None, str | None]] | None = None

    def title_and_selection(
        self, pid: int, timeout_s: float
    ) -> tuple[tuple[str | None, str | None] | None, str | None]:
        """((title, selection) or None, error)."""
        if mac is None:
            return None, None
        with self._lock:
            if self._job is not None and not self._job.done():
                return None, "focus: accessibility busy with a previous query (skipped)"
            self._job = job = self._pool.submit(mac.window_title_and_selection, pid, timeout_s)
        # four AX round trips at most; wait for about two of them
        try:
            return job.result(timeout=2 * timeout_s), None
        except FutureTimeout:
            return None, f"focus: accessibility timeout after {2 * timeout_s * 1000:.0f} ms"


_AX = AxWorker()


def _macos_focus(ax_timeout_s: float) -> FocusResult:
    if mac is None:
        return FocusResult(FocusInfo())
    win = mac.front_window()
    app = mac.front_app(win.pid if win is not None else None)
    if app is None:
        return FocusResult(FocusInfo(), errors=("focus: no frontmost application",))
    errors: list[str] = []
    title = win.title if win is not None else ""
    selection: str | None = None
    if mac.ax_trusted():
        ax, error = _AX.title_and_selection(app.pid, ax_timeout_s)
        if ax is not None:
            title, selection = ax[0] or title, ax[1]
        if error is not None:
            errors.append(error)
    else:
        errors.append("focus: accessibility permission not granted (no selection)")
    focus = FocusInfo(
        app=app.name,
        app_id=app.bundle_id,
        title=title,
        pid=app.pid,
        is_terminal=is_terminal(app.bundle_id),
        window_id=f"{win.pid}:{win.number}" if win is not None else str(app.pid),
    )
    return FocusResult(focus, selection, tuple(errors))
