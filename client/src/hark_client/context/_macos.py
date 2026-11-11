"""Typed wrapper around the few pyobjc calls the context engine needs (macOS only).

pyobjc ships no type information, so the frameworks are loaded with importlib and cast to
small Protocols describing exactly the calls used here; nothing untyped leaves this module.
"""
# ruff: noqa: N802 - Protocol methods mirror Apple's API names

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Protocol, cast

_AX_SUCCESS = 0
# AX attribute names (values of the kAX...Attribute constants)
_AX_FOCUSED_APPLICATION = "AXFocusedApplication"
_AX_FOCUSED_WINDOW = "AXFocusedWindow"
_AX_FOCUSED_UI_ELEMENT = "AXFocusedUIElement"
_AX_TITLE = "AXTitle"
_AX_SELECTED_TEXT = "AXSelectedText"


class _ApplicationServices(Protocol):
    def AXIsProcessTrusted(self) -> bool: ...
    def AXUIElementCreateSystemWide(self) -> object: ...
    def AXUIElementCreateApplication(self, pid: int, /) -> object: ...
    def AXUIElementSetMessagingTimeout(self, element: object, seconds: float, /) -> int: ...
    def AXUIElementCopyAttributeValue(
        self, element: object, attribute: str, out: None, /
    ) -> tuple[int, object]: ...
    def AXUIElementGetPid(self, element: object, out: None, /) -> tuple[int, int]: ...


class _RunningApplication(Protocol):
    def localizedName(self) -> str | None: ...
    def bundleIdentifier(self) -> str | None: ...
    def processIdentifier(self) -> int: ...


class _Workspace(Protocol):
    def frontmostApplication(self) -> _RunningApplication | None: ...


class _WorkspaceClass(Protocol):
    def sharedWorkspace(self) -> _Workspace: ...


class _RunningApplicationClass(Protocol):
    def runningApplicationWithProcessIdentifier_(
        self, pid: int, /
    ) -> _RunningApplication | None: ...


class _AppKit(Protocol):
    NSWorkspace: _WorkspaceClass
    NSRunningApplication: _RunningApplicationClass


_AS = cast("_ApplicationServices", importlib.import_module("ApplicationServices"))
_APPKIT = cast("_AppKit", importlib.import_module("AppKit"))


@dataclass(frozen=True, slots=True)
class FrontApp:
    name: str
    bundle_id: str
    pid: int


def ax_trusted() -> bool:
    """Whether this process has the Accessibility permission."""
    return bool(_AS.AXIsProcessTrusted())


def _ax_attr(element: object, attribute: str) -> object | None:
    err, value = _AS.AXUIElementCopyAttributeValue(element, attribute, None)
    return value if err == _AX_SUCCESS else None


def front_app(timeout_s: float) -> FrontApp | None:
    """Frontmost application.

    Prefers the Accessibility API (always current) and falls back to NSWorkspace, whose
    value only refreshes while the process runs an AppKit run loop.
    """
    pid: int | None = None
    if ax_trusted():
        system = _AS.AXUIElementCreateSystemWide()
        _AS.AXUIElementSetMessagingTimeout(system, timeout_s)
        app_el = _ax_attr(system, _AX_FOCUSED_APPLICATION)
        if app_el is not None:
            err, ax_pid = _AS.AXUIElementGetPid(app_el, None)
            if err == _AX_SUCCESS:
                pid = int(ax_pid)
    app = (
        _APPKIT.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if pid is not None
        else _APPKIT.NSWorkspace.sharedWorkspace().frontmostApplication()
    )
    if app is None:
        return None
    return FrontApp(
        name=str(app.localizedName() or ""),
        bundle_id=str(app.bundleIdentifier() or ""),
        pid=int(app.processIdentifier()),
    )


def window_title_and_selection(pid: int, timeout_s: float) -> tuple[str | None, str | None]:
    """(focused window title, selected text in the focused element) via Accessibility."""
    app_el = _AS.AXUIElementCreateApplication(pid)
    _AS.AXUIElementSetMessagingTimeout(app_el, timeout_s)
    title: str | None = None
    window = _ax_attr(app_el, _AX_FOCUSED_WINDOW)
    if window is not None:
        value = _ax_attr(window, _AX_TITLE)
        title = str(value) if value is not None else None
    selection: str | None = None
    focused = _ax_attr(app_el, _AX_FOCUSED_UI_ELEMENT)
    if focused is not None:
        value = _ax_attr(focused, _AX_SELECTED_TEXT)
        selection = str(value) if value else None
    return title, selection
