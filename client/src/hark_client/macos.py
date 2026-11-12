"""Typed wrapper around the pyobjc calls hark needs (macOS only).

pyobjc ships no type information and its modules are populated dynamically, so the
frameworks are bound as `Any` here; everything this module exports has precise
signatures.
"""
# pyright: reportAny=false, reportExplicitAny=false

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

if sys.platform != "darwin":  # imported lazily, from darwin-only code paths
    raise ImportError("hark_client.macos is macOS-only")

AppKit: Any = importlib.import_module("AppKit")
Foundation: Any = importlib.import_module("Foundation")
Quartz: Any = importlib.import_module("Quartz")
ApplicationServices: Any = importlib.import_module("ApplicationServices")
objc: Any = importlib.import_module("objc")

V_KEYCODE = 9  # kVK_ANSI_V
BACKSPACE_KEYCODE = 51  # kVK_Delete (the Backspace key)

type PasteboardSnapshot = list[dict[str, bytes]]


def change_count() -> int:
    return int(AppKit.NSPasteboard.generalPasteboard().changeCount())


def save_pasteboard() -> PasteboardSnapshot:
    """Every item with every type's raw data (text, RTF, files, images...)."""
    items: PasteboardSnapshot = []
    board_items: Any = AppKit.NSPasteboard.generalPasteboard().pasteboardItems()
    if board_items is None:
        return items
    for item in board_items:
        data: dict[str, bytes] = {}
        for t in item.types():
            d = item.dataForType_(t)
            if d is not None:
                data[str(t)] = bytes(d)
        items.append(data)
    return items


def restore_pasteboard(snapshot: PasteboardSnapshot) -> None:
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    if not snapshot:
        return
    objects: list[object] = []
    for data in snapshot:
        item = AppKit.NSPasteboardItem.alloc().init()
        for t, raw in data.items():
            item.setData_forType_(Foundation.NSData.dataWithBytes_length_(raw, len(raw)), t)
        objects.append(item)
    pb.writeObjects_(objects)


def set_clipboard_text(text: str) -> int:
    """Returns the pasteboard changeCount after the write."""
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
    return int(pb.changeCount())


def send_cmd_v() -> None:
    """Cmd+V from a private event source, so physically held keys (the hotkey) do not
    leak their modifier flags into the synthetic event."""
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(source, V_KEYCODE, down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def send_backspaces(n: int) -> None:
    """Backspace ``n`` times from a private event source with no modifier flags, so a held
    Option/Cmd cannot turn it into a word or line delete."""
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    for _ in range(n):
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(source, BACKSPACE_KEYCODE, down)
            Quartz.CGEventSetFlags(event, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def can_post_events() -> bool:
    """Accessibility permission (needed to post Cmd+V)."""
    return bool(Quartz.CGPreflightPostEventAccess())


def can_listen_events() -> bool:
    """Input Monitoring permission (needed by the global hotkey listener)."""
    return bool(Quartz.CGPreflightListenEventAccess())


def request_listen_events() -> bool:
    """Input Monitoring: shows the system prompt the first time; True if already granted."""
    return bool(Quartz.CGRequestListenEventAccess())


def accessibility_trusted(*, prompt: bool = False) -> bool:
    """Accessibility (AX) trust; ``prompt`` shows the system dialog that leads to Settings."""
    options = {ApplicationServices.kAXTrustedCheckOptionPrompt: prompt}
    return bool(ApplicationServices.AXIsProcessTrustedWithOptions(options))


MIC_STATES = ("not_determined", "restricted", "denied", "authorized")  # AVAuthorizationStatus
_AUDIO = "soun"  # AVMediaTypeAudio


def _capture_device() -> Any:  # noqa: ANN401 - pyobjc is untyped
    # AVFoundation without its pyobjc wrapper: load the framework, declare the block's signature.
    objc.loadBundle(
        "AVFoundation", {}, bundle_path="/System/Library/Frameworks/AVFoundation.framework"
    )
    objc.registerMetaDataForSelector(
        b"AVCaptureDevice", b"requestAccessForMediaType:completionHandler:",
        {"arguments": {3: {"callable": {"retval": {"type": b"v"}, "arguments": {0: {"type": b"^v"}, 1: {"type": b"Z"}}}}}},
    )  # fmt: skip
    return objc.lookUpClass("AVCaptureDevice")


def mic_authorization() -> str:
    """Microphone permission of this process's responsible app: one of MIC_STATES."""
    status = int(_capture_device().authorizationStatusForMediaType_(_AUDIO))
    return MIC_STATES[status] if 0 <= status < len(MIC_STATES) else "denied"


def request_mic(done: Callable[[bool], None]) -> None:
    """Shows the Microphone prompt if undecided; ``done(granted)`` runs on another thread."""
    _capture_device().requestAccessForMediaType_completionHandler_(_AUDIO, done)


PRIVACY_PANES = {
    "microphone": "Privacy_Microphone",
    "accessibility": "Privacy_Accessibility",
    "input_monitoring": "Privacy_ListenEvent",
}


def open_privacy_pane(which: str) -> None:
    """System Settings > Privacy & Security at the pane for ``which`` (PRIVACY_PANES key)."""
    url = f"x-apple.systempreferences:com.apple.preference.security?{PRIVACY_PANES[which]}"
    subprocess.Popen(["/usr/bin/open", url])  # noqa: S603 - fixed program and URL
