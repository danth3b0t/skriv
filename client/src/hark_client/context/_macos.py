"""Typed wrapper around the few pyobjc calls the context engine needs (macOS only).

pyobjc ships no type information, so the frameworks are loaded with importlib and cast to
small Protocols describing exactly the calls used here; nothing untyped leaves this module.

The frontmost window comes from the window server (``CGWindowListCopyWindowInfo``): it works
from any thread without a run loop or permission and never waits on the focused app, unlike
``NSWorkspace.frontmostApplication`` (stale without an AppKit run loop) and the Accessibility
API (an IPC round trip to the focused app, which can stall for seconds in Chromium/Electron
apps). Window titles need the Screen Recording permission there; Accessibility fills them in.
"""
# ruff: noqa: N802 - Protocol methods mirror Apple's API names

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from hark_client.fields import FieldText

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_AX_SUCCESS = 0
_AX_VALUE_CF_RANGE = 4  # kAXValueCFRangeType
# AX attribute names (values of the kAX...Attribute constants)
_AX_FOCUSED_WINDOW = "AXFocusedWindow"
_AX_FOCUSED_UI_ELEMENT = "AXFocusedUIElement"
_AX_TITLE = "AXTitle"
_AX_SELECTED_TEXT = "AXSelectedText"
_AX_VALUE = "AXValue"
_AX_ROLE = "AXRole"
_AX_SUBROLE = "AXSubrole"
_AX_NUMBER_OF_CHARACTERS = "AXNumberOfCharacters"
_AX_SECURE = "AXSecureTextField"
# CGWindowListOption values and kCGNullWindowID
_ON_SCREEN_ONLY = 1 << 0
_EXCLUDE_DESKTOP = 1 << 4
_NULL_WINDOW = 0
_MIN_WINDOW_SIDE = 40  # smaller layer-0 windows are helpers/overlays, not the focused window


class _ApplicationServices(Protocol):
    def AXIsProcessTrusted(self) -> bool: ...
    def AXUIElementCreateApplication(self, pid: int, /) -> object: ...
    def AXUIElementCreateSystemWide(self) -> object: ...
    def AXUIElementGetPid(self, element: object, out: None, /) -> tuple[int, int]: ...
    def AXUIElementCopyParameterizedAttributeValue(
        self, element: object, attribute: str, parameter: object, out: None, /
    ) -> tuple[int, object]: ...
    def AXValueCreate(self, kind: int, value: tuple[int, int], /) -> object: ...
    def AXValueGetValue(self, value: object, kind: int, out: None, /) -> tuple[bool, object]: ...
    def AXUIElementSetMessagingTimeout(self, element: object, seconds: float, /) -> int: ...
    def AXUIElementCopyAttributeValue(
        self, element: object, attribute: str, out: None, /
    ) -> tuple[int, object]: ...


class _Quartz(Protocol):
    def CGWindowListCopyWindowInfo(
        self, option: int, relative_to: int, /
    ) -> Sequence[Mapping[str, object]] | None: ...


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
_QUARTZ = cast("_Quartz", importlib.import_module("Quartz"))


@dataclass(frozen=True, slots=True)
class FrontApp:
    name: str
    bundle_id: str
    pid: int


@dataclass(frozen=True, slots=True)
class FrontWindow:
    pid: int
    number: int
    title: str  # "" without the Screen Recording permission


def ax_trusted() -> bool:
    """Whether this process has the Accessibility permission."""
    return bool(_AS.AXIsProcessTrusted())


def _num(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def front_window() -> FrontWindow | None:
    """Front-most normal (layer 0), visible window on screen; ~1 ms."""
    windows = _QUARTZ.CGWindowListCopyWindowInfo(_ON_SCREEN_ONLY | _EXCLUDE_DESKTOP, _NULL_WINDOW)
    for w in windows or ():
        if _num(w.get("kCGWindowLayer")) != 0 or _num(w.get("kCGWindowAlpha")) <= 0:
            continue
        bounds = cast("Mapping[str, object] | None", w.get("kCGWindowBounds"))
        if bounds is None or min(_num(bounds.get("Width")), _num(bounds.get("Height"))) < (
            _MIN_WINDOW_SIDE
        ):
            continue
        name = w.get("kCGWindowName")
        return FrontWindow(
            pid=int(_num(w.get("kCGWindowOwnerPID"))),
            number=int(_num(w.get("kCGWindowNumber"))),
            title=str(name) if name else "",
        )
    return None


def front_app(pid: int | None) -> FrontApp | None:
    """Application owning ``pid``, or NSWorkspace's (possibly stale) frontmost one if None."""
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


def _ax_attr(element: object, attribute: str, timeout_s: float) -> object | None:
    _AS.AXUIElementSetMessagingTimeout(element, timeout_s)
    err, value = _AS.AXUIElementCopyAttributeValue(element, attribute, None)
    return value if err == _AX_SUCCESS else None


# Caret primitives (``hark_client.context.caret`` holds the logic). Errors are AXError codes.


def ax_focused_element(pid: int | None, timeout_s: float) -> tuple[int, object | None]:
    """Focused UI element of the application ``pid``, or system-wide if None."""
    root = _AS.AXUIElementCreateApplication(pid) if pid is not None else (
        _AS.AXUIElementCreateSystemWide()
    )  # fmt: skip
    _AS.AXUIElementSetMessagingTimeout(root, timeout_s)  # system-wide: the global default
    err, value = _AS.AXUIElementCopyAttributeValue(root, _AX_FOCUSED_UI_ELEMENT, None)
    return err, value if err == _AX_SUCCESS else None


def ax_element_pid(element: object) -> int | None:
    err, pid = _AS.AXUIElementGetPid(element, None)
    return int(pid) if err == _AX_SUCCESS else None


def ax_attribute(element: object, name: str, timeout_s: float) -> tuple[int, object | None]:
    _AS.AXUIElementSetMessagingTimeout(element, timeout_s)
    err, value = _AS.AXUIElementCopyAttributeValue(element, name, None)
    return err, value if err == _AX_SUCCESS else None


def ax_range_value(value: object) -> tuple[int, int] | None:
    """(location, length) of an AXValue holding a CFRange."""
    ok, rng = _AS.AXValueGetValue(value, _AX_VALUE_CF_RANGE, None)
    if not ok:
        return None
    if isinstance(rng, tuple):
        loc, length = cast("tuple[int, int]", rng)
        return int(loc), int(length)
    return int(_num(getattr(rng, "location", 0))), int(_num(getattr(rng, "length", 0)))


def ax_string_for_range(
    element: object, location: int, length: int, timeout_s: float
) -> tuple[int, str | None]:
    """``AXStringForRange`` (UTF-16 units)."""
    _AS.AXUIElementSetMessagingTimeout(element, timeout_s)
    param = _AS.AXValueCreate(_AX_VALUE_CF_RANGE, (location, length))
    err, value = _AS.AXUIElementCopyParameterizedAttributeValue(
        element, "AXStringForRange", param, None
    )
    return err, str(value) if err == _AX_SUCCESS and value is not None else None


# Field read-back (``hark_client.fields``), on the same primitives as the caret lookup.


def focused_field(pid: int, timeout_s: float, max_chars: int) -> FieldText:
    """Value of the focused element of ``pid``; secure fields are never read."""
    _, focused = ax_focused_element(pid, timeout_s)
    if focused is None:
        return FieldText("unavailable")
    _, role = ax_attribute(focused, _AX_ROLE, timeout_s)
    _, subrole = ax_attribute(focused, _AX_SUBROLE, timeout_s)
    if _AX_SECURE in (role, subrole):
        return FieldText("secure")
    _, count = ax_attribute(focused, _AX_NUMBER_OF_CHARACTERS, timeout_s)
    if isinstance(count, int) and count > max_chars:
        return FieldText("too_long")
    _, value = ax_attribute(focused, _AX_VALUE, timeout_s)
    if not isinstance(value, str):
        return FieldText("not_text")
    if len(value) > max_chars:
        return FieldText("too_long")
    return FieldText("text", str(value))


def window_title_and_selection(pid: int, timeout_s: float) -> tuple[str | None, str | None]:
    """(focused window title, selected text in the focused element) via Accessibility.

    Every element gets the messaging timeout (it is per element, not inherited).
    """
    app_el = _AS.AXUIElementCreateApplication(pid)
    title: str | None = None
    window = _ax_attr(app_el, _AX_FOCUSED_WINDOW, timeout_s)
    if window is not None:
        value = _ax_attr(window, _AX_TITLE, timeout_s)
        title = str(value) if value is not None else None
    selection: str | None = None
    focused = _ax_attr(app_el, _AX_FOCUSED_UI_ELEMENT, timeout_s)
    if focused is not None:
        value = _ax_attr(focused, _AX_SELECTED_TEXT, timeout_s)
        selection = str(value) if value else None
    return title, selection
