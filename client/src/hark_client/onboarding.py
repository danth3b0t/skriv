"""Hark.app first-run setup window (macOS): permissions, mic level, hotkey and server checks.

Shown at launch while a permission is missing (and from the menu: Setup & Permissions…). Unlike
the indicator this is an ordinary window that takes focus; closing it deactivates Hark so the
previous app gets focus back. Each permission row asks with the system prompt first
(AVCaptureDevice, AXIsProcessTrustedWithOptions, CGRequestListenEventAccess) and opens the
System Settings pane after that. The mic level and server rows reuse ``hark doctor``'s checks.
Input Monitoring granted after launch needs a restart: the hotkey listener is created at start.
"""
# pyright: reportAny=false, reportExplicitAny=false
# ruff: noqa: ANN401, FBT003 - pyobjc is untyped and takes positional booleans

from __future__ import annotations

import importlib
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, override

from hark_client import doctor, macos
from hark_client.indicator.macos import shared_app
from hark_client.menubar import bind, make_actions

if sys.platform != "darwin":
    raise ImportError("hark_client.onboarding is macOS-only")

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.daemon import Controller

AppKit: Any = importlib.import_module("AppKit")
Foundation: Any = importlib.import_module("Foundation")

WIDTH, ROW_H, MARGIN, BUTTON_W = 580.0, 54.0, 20.0, 130.0
REFRESH_S = 1.0
KEY_NAMES = {
    "alt_r": "Right Option",
    "alt_l": "Left Option",
    "alt": "Option",
    "cmd_r": "Right Command",
    "ctrl_r": "Right Control",
    "shift_r": "Right Shift",
}


def permissions_missing() -> bool:
    return not (
        macos.mic_authorization() == "authorized"
        and macos.accessibility_trusted()
        and macos.can_listen_events()
    )


def key_name(key: str) -> str:
    return KEY_NAMES.get(key, key.upper() if key.startswith("f") else key)


class Collect(doctor.Report):
    """`hark doctor` checks, collected instead of printed."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[tuple[str, str, str]] = []

    @override
    def ok(self, what: str, detail: str = "") -> None:
        self.entries.append(("ok", what, detail))

    @override
    def warn(self, what: str, detail: str) -> None:
        self.entries.append(("warn", what, detail))

    @override
    def fail(self, what: str, detail: str) -> None:
        self.failures += 1
        self.entries.append(("FAIL", what, detail))

    def summary(self) -> str:
        bad = [e for e in self.entries if e[0] != "ok"]
        shown = bad or self.entries
        return "; ".join(f"{what}: {detail}" if detail else what for _, what, detail in shown[:3])


class Row:
    def __init__(self, title: Any, detail: Any, button: Any | None) -> None:
        self.title, self.detail, self.button = title, detail, button

    def button_title(self) -> str | None:
        return None if self.button is None else str(self.button.title())

    def show(self, text: str, button: str | None = None, *, good: bool = False) -> None:
        self.detail.setStringValue_(text)
        self.detail.setTextColor_(
            AppKit.NSColor.systemGreenColor() if good else AppKit.NSColor.secondaryLabelColor()
        )
        if self.button is not None:
            self.button.setHidden_(button is None)
            if button is not None:
                self.button.setTitle_(button)


class Setup:
    """The setup window. Main thread only; :meth:`tick` is called from the AppKit loop."""

    def __init__(self, controller: Controller, relaunch: Callable[[], None]) -> None:
        self.c = controller
        self.relaunch = relaunch
        self.app = shared_app()
        self.actions = make_actions()
        self.actions.on_close = self._closed
        self.window: Any = None
        self.rows: dict[str, Row] = {}
        # granted after launch: the hotkey listener / mic stream were created without it
        self.listen_at_start = macos.can_listen_events()
        self.mic_at_start = macos.mic_authorization() == "authorized"
        self.asked: set[str] = set()
        self.started_at_show = 0
        self._next_refresh = 0.0
        self._results: dict[str, tuple[str, bool]] = {}  # row -> (text, good), from threads
        self._lock = threading.Lock()

    # --- window -------------------------------------------------------------------------

    def _build(self) -> Any:
        rows = ["mic", "ax", "listen", "level", "hotkey", "servers"]
        height = MARGIN + len(rows) * ROW_H + 40 + 60
        style = AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            Foundation.NSMakeRect(0, 0, WIDTH, height), style, AppKit.NSBackingStoreBuffered, False
        )
        win.setTitle_("Set up Hark")
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self.actions)
        content = win.contentView()
        y = height - MARGIN
        spec: dict[str, tuple[str, Callable[[], None] | None]] = {
            "mic": ("Microphone", self._ask_mic),
            "ax": ("Accessibility", self._ask_ax),
            "listen": ("Input Monitoring", self._ask_listen),
            "level": ("Microphone level", self._test_level),
            "hotkey": (f"Push-to-talk: {key_name(self.c.svc.cfg.hotkey.key)}", None),
            "servers": ("Servers", self._check_servers),
        }
        for key in rows:
            title, action = spec[key]
            y -= ROW_H
            if key == "servers":  # the dictation test box goes between hotkey and servers
                self.try_box = AppKit.NSTextField.alloc().initWithFrame_(
                    Foundation.NSMakeRect(MARGIN, y + 14, WIDTH - 2 * MARGIN, 24)
                )
                self.try_box.setPlaceholderString_("Dictated text lands here")
                content.addSubview_(self.try_box)
                y -= 40
            label = AppKit.NSTextField.labelWithString_(title)
            label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(13))
            label.setFrame_(Foundation.NSMakeRect(MARGIN, y + 28, 400, 18))
            detail = AppKit.NSTextField.wrappingLabelWithString_("")
            detail.setFont_(AppKit.NSFont.systemFontOfSize_(11))
            detail.setMaximumNumberOfLines_(2)
            detail.cell().setTruncatesLastVisibleLine_(True)
            detail.setFrame_(Foundation.NSMakeRect(MARGIN, y, WIDTH - 3 * MARGIN - BUTTON_W, 28))
            content.addSubview_(label)
            content.addSubview_(detail)
            button = None
            if action is not None:
                button = AppKit.NSButton.buttonWithTitle_target_action_("", None, None)
                button.setFrame_(
                    Foundation.NSMakeRect(WIDTH - MARGIN - BUTTON_W, y + 10, BUTTON_W, 30)
                )
                bind(self.actions, button, action)
                content.addSubview_(button)
            self.rows[key] = Row(label, detail, button)
        restart = AppKit.NSButton.buttonWithTitle_target_action_("Restart Hark", None, None)
        restart.setFrame_(Foundation.NSMakeRect(MARGIN, 16, BUTTON_W, 30))
        bind(self.actions, restart, self.relaunch)
        done = AppKit.NSButton.buttonWithTitle_target_action_("Done", None, None)
        done.setFrame_(Foundation.NSMakeRect(WIDTH - MARGIN - BUTTON_W, 16, BUTTON_W, 30))
        done.setKeyEquivalent_("\r")
        bind(self.actions, done, lambda: win.performClose_(None))
        content.addSubview_(restart)
        content.addSubview_(done)
        self.rows["level"].show("Records half a second and shows the input level", "Test")
        self.rows["servers"].show("Checking…", "Check again")
        win.center()
        self.window = win
        return win

    def show(self) -> None:
        win = self.window if self.window is not None else self._build()
        self.started_at_show = self.c.started
        self._next_refresh = 0.0
        self.tick()
        self._check_servers()
        if self.app.respondsToSelector_("activate"):
            self.app.activate()
        else:
            self.app.activateIgnoringOtherApps_(True)
        win.makeKeyAndOrderFront_(None)

    def _closed(self) -> None:
        self.app.deactivate()  # hand focus back to the app the user was in

    # --- state --------------------------------------------------------------------------

    def tick(self) -> None:
        if self.window is None or not self.window.isVisible():
            return
        with self._lock:
            results, self._results = self._results, {}
        for key, (text, good) in results.items():
            row = self.rows[key]
            row.show(text, row.button_title(), good=good)
        now = time.monotonic()
        if now < self._next_refresh:
            return
        self._next_refresh = now + REFRESH_S
        self._refresh_permissions()
        hotkey = self.rows["hotkey"]
        if self.c.started > self.started_at_show:
            hotkey.show("Works: a dictation started. Its text should appear in the box.", good=True)
        elif not macos.can_listen_events():
            hotkey.show("Needs Input Monitoring (above)")
        else:
            hotkey.show(
                f"Click the box below, hold {key_name(self.c.svc.cfg.hotkey.key)}, say a few"
                " words and release."
            )

    def _refresh_permissions(self) -> None:
        mic = macos.mic_authorization()
        if mic == "authorized":
            restart = "" if self.mic_at_start else ": click Restart Hark to use it"
            self.rows["mic"].show(f"Allowed{restart}", good=True)
        elif mic == "not_determined":
            self.rows["mic"].show("Hark asks when you click Allow", "Allow…")
        else:
            self.rows["mic"].show("Denied: turn Hark on in System Settings", "Open Settings")
        if macos.accessibility_trusted():
            self.rows["ax"].show("Allowed: Hark can paste and read the text field", good=True)
        else:
            self.rows["ax"].show(
                "Needed to paste (Cmd+V) and to read the text around the caret",
                "Open Settings" if "ax" in self.asked else "Allow…",
            )
        if not macos.can_listen_events():
            self.rows["listen"].show(
                "Needed for the push-to-talk key",
                "Open Settings" if "listen" in self.asked else "Allow…",
            )
        elif self.listen_at_start:
            self.rows["listen"].show("Allowed", good=True)
        else:
            self.rows["listen"].show("Allowed: click Restart Hark to start the hotkey", good=True)

    def _post(self, key: str, text: str, *, good: bool) -> None:
        with self._lock:
            self._results[key] = (text, good)

    # --- actions ------------------------------------------------------------------------

    def _ask_mic(self) -> None:
        if macos.mic_authorization() == "not_determined":
            macos.request_mic(lambda _granted: None)  # the next refresh shows the answer
        else:
            macos.open_privacy_pane("microphone")

    def _ask_ax(self) -> None:
        if "ax" in self.asked:
            macos.open_privacy_pane("accessibility")
        else:
            self.asked.add("ax")
            macos.accessibility_trusted(prompt=True)
        self._next_refresh = 0.0

    def _ask_listen(self) -> None:
        if "listen" in self.asked:
            macos.open_privacy_pane("input_monitoring")
        else:
            self.asked.add("listen")
            macos.request_listen_events()
        self._next_refresh = 0.0

    def _in_thread(self, key: str, check: Callable[[Collect], None]) -> None:
        self.rows[key].show("Checking…", self.rows[key].button_title())

        def work() -> None:
            r = Collect()
            try:
                check(r)
            except Exception as e:  # noqa: BLE001 - shown in the window
                r.fail(key, f"{type(e).__name__}: {e}")
            self._post(key, r.summary(), good=r.failures == 0)

        threading.Thread(target=work, daemon=True, name=f"hark-setup-{key}").start()

    def _test_level(self) -> None:
        self._in_thread("level", lambda r: doctor.check_mic(r, self.c.svc.cfg))

    def _check_servers(self) -> None:
        self._in_thread("servers", lambda r: doctor.check_providers(r, self.c.svc.cfg))
