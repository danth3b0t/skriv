"""macOS indicator: a borderless, non-activating, click-through NSPanel.

Focus safety: the process is an accessory app (no Dock icon, never activated), the panel has
``NSWindowStyleMaskNonactivatingPanel``, ignores mouse events, cannot become key or main and
is shown with ``orderFrontRegardless`` (never ``makeKeyAndOrderFront``). :func:`is_passive`
checks this and the backend refuses to show a panel that fails it.

AppKit is main-thread only: :meth:`PanelBackend.run` pumps the AppKit event loop on the main
thread; :meth:`PanelBackend.render` (any thread) stores the latest view and wakes the loop with
an application-defined event (``postEvent:atStart:`` is safe from other threads).

pyobjc ships no type information, so its objects are ``Any`` here (as in ``hark_client.macos``).
"""
# pyright: reportAny=false, reportExplicitAny=false
# ruff: noqa: ANN401, FBT003 - pyobjc is untyped and takes positional booleans

from __future__ import annotations

import importlib
import sys
import threading
from typing import TYPE_CHECKING, Any

if sys.platform != "darwin":
    raise ImportError("hark_client.indicator.macos is macOS-only")

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.indicator import State, View

AppKit: Any = importlib.import_module("AppKit")
Foundation: Any = importlib.import_module("Foundation")
objc: Any = importlib.import_module("objc")
importlib.import_module("Quartz")  # registers CGColorRef, so CGColor() is not a raw pointer

WIDTH, HEIGHT = 260.0, 34.0  # room for "Hands-free · terminal"
MARGIN = 72.0  # from the bottom (or top) of the visible frame: clear of the Dock / menu bar
METER_W = 54.0
VISIBLE_POLL_S = 0.05
IDLE_POLL_S = 0.5

_DOT_RGB: dict[State, tuple[float, float, float]] = {
    "recording": (1.0, 0.27, 0.23),
    "transcribing": (1.0, 0.75, 0.2),
    "cleaning": (0.55, 0.6, 1.0),
    "done": (0.3, 0.85, 0.4),
    "error": (1.0, 0.27, 0.23),
    "cancelled": (0.6, 0.6, 0.6),
    "info": (0.4, 0.7, 1.0),
}


def make_panel() -> Any:
    """The indicator panel (not shown). Needs the main thread."""
    style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
    panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        Foundation.NSMakeRect(0, 0, WIDTH, HEIGHT), style, AppKit.NSBackingStoreBuffered, False
    )
    panel.setLevel_(AppKit.NSStatusWindowLevel)
    panel.setIgnoresMouseEvents_(True)
    panel.setHidesOnDeactivate_(False)  # the app is never active
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setFloatingPanel_(True)
    panel.setReleasedWhenClosed_(False)
    panel.setOpaque_(False)
    panel.setHasShadow_(True)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setAnimationBehavior_(AppKit.NSWindowAnimationBehaviorNone)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorStationary
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        | AppKit.NSWindowCollectionBehaviorIgnoresCycle
    )
    content = panel.contentView()
    content.setWantsLayer_(True)
    content.layer().setCornerRadius_(HEIGHT / 2)
    content.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.08, 0.88).CGColor())
    return panel


def is_passive(panel: Any) -> bool:
    """True if showing ``panel`` cannot take focus from the app the user is dictating into."""
    return bool(
        panel.styleMask() & AppKit.NSWindowStyleMaskNonactivatingPanel
        and panel.ignoresMouseEvents()
        and not panel.canBecomeKeyWindow()
        and not panel.canBecomeMainWindow()
    )


class PanelBackend:
    def __init__(self, position: str) -> None:
        if not Foundation.NSThread.isMainThread():
            raise RuntimeError("the macOS indicator must be created on the main thread")
        self.position = position
        self.app = AppKit.NSApplication.sharedApplication()
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.app.finishLaunching()
        self.panel = make_panel()
        if not is_passive(self.panel):
            raise RuntimeError("indicator panel could take focus; not using it")
        content = self.panel.contentView()
        self.dot = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSMakeRect(14, (HEIGHT - 10) / 2, 10, 10)
        )
        self.dot.setWantsLayer_(True)
        self.dot.layer().setCornerRadius_(5)
        content.addSubview_(self.dot)
        self.label = AppKit.NSTextField.labelWithString_("")
        self.label.setFrame_(
            Foundation.NSMakeRect(32, (HEIGHT - 18) / 2, WIDTH - 32 - METER_W - 20, 18)
        )
        self.label.setTextColor_(AppKit.NSColor.whiteColor())
        self.label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightMedium))
        self.label.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        content.addSubview_(self.label)
        self.meter = AppKit.NSLevelIndicator.alloc().initWithFrame_(
            Foundation.NSMakeRect(WIDTH - METER_W - 14, (HEIGHT - 8) / 2, METER_W, 8)
        )
        self.meter.setLevelIndicatorStyle_(AppKit.NSLevelIndicatorStyleContinuousCapacity)
        self.meter.setMinValue_(0.0)
        self.meter.setMaxValue_(1.0)
        self.meter.setWarningValue_(2.0)  # never turns yellow/red
        self.meter.setCriticalValue_(2.0)
        content.addSubview_(self.meter)
        self._lock = threading.Lock()
        self._pending: View | None = None
        self._dirty = False
        self._shown: View | None = None

    def render(self, view: View | None) -> None:
        with self._lock:
            wake = (view is None) != (self._pending is None) or (
                view is not None and self._pending is not None and view.state != self._pending.state
            )
            self._pending, self._dirty = view, True
        if wake:  # level-only changes are picked up by the visible poll
            event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                AppKit.NSEventTypeApplicationDefined,
                Foundation.NSMakePoint(0, 0),
                0,
                0.0,
                0,
                None,
                0,
                0,
                0,
            )
            self.app.postEvent_atStart_(event, False)

    def run(self, stop: threading.Event, tick: Callable[[], None]) -> None:
        mode = Foundation.NSDefaultRunLoopMode
        while not stop.is_set():
            with objc.autorelease_pool():
                wait = VISIBLE_POLL_S if self._shown is not None else IDLE_POLL_S
                event = self.app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                    AppKit.NSEventMaskAny,
                    Foundation.NSDate.dateWithTimeIntervalSinceNow_(wait),
                    mode,
                    True,
                )
                if event is not None and event.type() != AppKit.NSEventTypeApplicationDefined:
                    self.app.sendEvent_(event)
                tick()
                self._apply()
        self.panel.orderOut_(None)

    def _apply(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            view, self._dirty = self._pending, False
        if view is None:
            if self._shown is not None:
                self.panel.orderOut_(None)
            self._shown = None
            return
        if self._shown is None or self._shown.state != view.state:
            r, g, b = _DOT_RGB[view.state]
            self.dot.layer().setBackgroundColor_(
                AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0).CGColor()
            )
            self.meter.setHidden_(view.state != "recording")
        if self._shown is None or self._shown.label != view.label:
            self.label.setStringValue_(view.label)
        self.meter.setDoubleValue_(view.level)
        if self._shown is None:
            self._place()
            self.panel.orderFrontRegardless()  # shows without activating or becoming key
        self._shown = view

    def _place(self) -> None:
        """Bottom (or top) centre of the screen with the pointer."""
        mouse = AppKit.NSEvent.mouseLocation()
        screens = AppKit.NSScreen.screens()
        screen = next(
            (s for s in screens if Foundation.NSMouseInRect(mouse, s.frame(), False)),
            AppKit.NSScreen.mainScreen(),
        )
        if screen is None:
            return
        f = screen.visibleFrame()
        x = f.origin.x + (f.size.width - WIDTH) / 2
        y = (
            f.origin.y + MARGIN
            if self.position == "bottom"
            else f.origin.y + f.size.height - HEIGHT - MARGIN
        )
        self.panel.setFrameOrigin_(Foundation.NSMakePoint(x, y))
