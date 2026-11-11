"""Typed wrapper around the pyobjc calls hark needs (macOS only).

pyobjc ships no type information and its modules are populated dynamically, so the
frameworks are bound as `Any` here; everything this module exports has precise
signatures.
"""
# pyright: reportAny=false, reportExplicitAny=false

from __future__ import annotations

import importlib
import sys
from typing import Any

if sys.platform != "darwin":  # imported lazily, from darwin-only code paths
    raise ImportError("hark_client.macos is macOS-only")

AppKit: Any = importlib.import_module("AppKit")
Foundation: Any = importlib.import_module("Foundation")
Quartz: Any = importlib.import_module("Quartz")

V_KEYCODE = 9  # kVK_ANSI_V

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


def can_post_events() -> bool:
    """Accessibility permission (needed to post Cmd+V)."""
    return bool(Quartz.CGPreflightPostEventAccess())


def can_listen_events() -> bool:
    """Input Monitoring permission (needed by the global hotkey listener)."""
    return bool(Quartz.CGPreflightListenEventAccess())
