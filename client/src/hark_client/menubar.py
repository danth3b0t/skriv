"""Hark.app's menu-bar status item (`hark app`, macOS).

An ``NSStatusItem`` whose SF Symbol shows the daemon state (ready, listening, transcribing,
paused, last dictation failed) and a menu: last dictation, Copy / Retry last, Pause / Resume,
Profile override, Open History, Settings, Setup & Permissions, Run Doctor, Launch at Login,
Quit. It lives on the main thread in the same AppKit loop as the recording indicator
(``indicator.macos.pump``); the daemon calls :meth:`MenuBar.tick` from that loop. Opening the
menu does not activate Hark, so the dictation target keeps focus.

Commands that already exist in the CLI (`hark retry`, `hark doctor`) run as child processes of
Hark, so they run with Hark's permissions; `hark history` opens in Terminal.
"""
# pyright: reportAny=false, reportExplicitAny=false, reportUntypedBaseClass=false
# ruff: noqa: ANN401, N802 - pyobjc is untyped, takes positional booleans, selector names

from __future__ import annotations

import importlib
import logging
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hark_client import history, service
from hark_client.config import expand
from hark_client.indicator.macos import pump, shared_app

if sys.platform != "darwin":
    raise ImportError("hark_client.menubar is macOS-only")

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.daemon import Controller
    from hark_client.onboarding import Setup

AppKit: Any = importlib.import_module("AppKit")
Foundation: Any = importlib.import_module("Foundation")

log = logging.getLogger("hark.menubar")

SYMBOLS = {
    "idle": "mic",
    "recording": "mic.fill",
    "finishing": "waveform",
    "paused": "mic.slash",
    "error": "exclamationmark.triangle",
}
LABELS = {
    "idle": "Ready",
    "recording": "Listening…",
    "finishing": "Transcribing…",
    "paused": "Paused",
    "error": "Last dictation failed",
}
FAILED = frozenset({"error", "paste_failed"})
ERROR_SHOW_S = 10.0  # the warning icon stays this long after a failed dictation
PREVIEW_CHARS = 40
LOOP_POLL_S = 0.25
CACHE_DIR = Path.home() / "Library" / "Caches" / "hark"
LOG_DIR = Path.home() / "Library" / "Logs" / "hark"
CONFIG_STUB = "# hark client config; every key is optional. See client/README.md#configuration\n"


def menu_state(c: Controller, now: float) -> str:
    """A SYMBOLS key for the daemon's current state."""
    if c.paused:
        return "paused"
    st = c.status_json()
    if st["state"] == "recording":
        return "recording"
    if st["finishing"]:
        return "finishing"
    last = c.last_result
    if last is not None and last[1].status in FAILED and now - last[0] < ERROR_SHOW_S:
        return "error"
    return "idle"


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


class Actions(Foundation.NSObject):
    """Objective-C target for menu items, buttons and delegates: ``tag`` -> Python callable.
    Exceptions are logged, never raised into AppKit."""

    def act_(self, sender: Any) -> None:
        _call(self.handlers.get(int(sender.tag())))

    def menuNeedsUpdate_(self, menu: Any) -> None:
        _call(self.on_menu_open)

    def windowWillClose_(self, notification: Any) -> None:
        _call(self.on_close)


def _call(fn: Callable[[], None] | None) -> None:
    # Outside Actions: pyobjc would make any method of it an Objective-C selector.
    if fn is None:
        return
    try:
        fn()
    except Exception:
        log.exception("menu action failed")


def make_actions() -> Any:
    actions = Actions.alloc().init()
    actions.handlers = {}
    actions.on_menu_open = None
    actions.on_close = None
    return actions


def bind(actions: Any, control: Any, fn: Callable[[], None]) -> None:
    """Make ``control`` (NSMenuItem / NSButton) call ``fn``."""
    tag = len(actions.handlers) + 1
    actions.handlers[tag] = fn
    control.setTag_(tag)
    control.setTarget_(actions)
    control.setAction_("act:")


def symbol(name: str, description: str) -> Any:
    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, description)
    if image is not None:
        image.setTemplate_(True)  # follows the menu bar's light/dark appearance
    return image


def bundle_path() -> Path | None:
    """The running Hark.app, or None when `hark app` runs outside a bundle."""
    path = Path(str(AppKit.NSBundle.mainBundle().bundlePath()))
    return path if path.suffix == ".app" else None


def open_in_terminal(argv: list[str], name: str) -> None:
    """Run ``argv`` in a new Terminal window (a .command file; no Apple Events needed)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    script = CACHE_DIR / f"{name}.command"
    script.write_text(f"#!/bin/sh\nclear\n{shlex.join(argv)}\n")
    script.chmod(0o700)
    subprocess.Popen(["/usr/bin/open", "-a", "Terminal", str(script)])  # noqa: S603


class MenuBar:
    """The status item and its menu. Main thread only (built, ticked and clicked there)."""

    def __init__(
        self,
        controller: Controller,
        stop: threading.Event,
        *,
        config_path: Path,
        cli: list[str],
        onboarding: bool,
    ) -> None:
        self.c = controller
        self.stop = stop
        self.config_path = config_path
        self.cli = cli  # global options for child `hark` commands (--config, --server)
        self.app = shared_app()
        self.actions = make_actions()
        self.actions.on_menu_open = self.refresh
        self.setup: Setup | None = None
        self.item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        self.menu = AppKit.NSMenu.alloc().initWithTitle_("Hark")
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self.actions)
        self.item.setMenu_(self.menu)
        self._build()
        self._state = ""
        self.tick()
        self.refresh()
        log.info("menu-bar item ready (%s)", bundle_path() or "no app bundle")
        if onboarding:
            from hark_client import onboarding as setup  # noqa: PLC0415

            if setup.permissions_missing():
                self.open_setup()

    # --- construction -----------------------------------------------------------------

    def _add(self, title: str, fn: Callable[[], None] | None = None, menu: Any = None) -> Any:
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        if fn is None:
            item.setEnabled_(False)
        else:
            bind(self.actions, item, fn)
        (menu or self.menu).addItem_(item)
        return item

    def _separator(self) -> None:
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())

    def _build(self) -> None:
        self.status_line = self._add("Hark")
        self.last_line = self._add("No dictation yet")
        self.copy_item = self._add("Copy Last Dictation", self.copy_last)
        self.retry_item = self._add("Retry Last Dictation", self.retry_last)
        self._separator()
        self.pause_item = self._add("Pause Dictation", self.toggle_pause)
        profiles = AppKit.NSMenu.alloc().initWithTitle_("Profile")
        profiles.setAutoenablesItems_(False)
        self.profile_items: dict[str | None, Any] = {
            None: self._add("Automatic (by app)", lambda: self.set_profile(None), profiles)
        }
        for name in self.c.svc.cfg.profiles:
            self.profile_items[name] = self._add(
                name, lambda name=name: self.set_profile(name), profiles
            )
        parent = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Profile", None, "")
        parent.setSubmenu_(profiles)
        self.menu.addItem_(parent)
        self._separator()
        self._add("Open History", self.open_history)
        self._add("Settings…", self.open_settings)
        self._add("Setup & Permissions…", self.open_setup)
        self._add("Run Doctor", self.run_doctor)
        self.login_item = self._add("Launch at Login", self.toggle_login) if bundle_path() else None
        self._separator()
        self._add("Quit Hark", self.stop.set)

    # --- loop ---------------------------------------------------------------------------

    def tick(self) -> None:
        state = menu_state(self.c, time.time())
        if state != self._state:
            self._state = state
            button = self.item.button()
            button.setImage_(symbol(SYMBOLS[state], f"Hark: {LABELS[state]}"))
            button.setToolTip_(f"Hark: {LABELS[state]}")
        if self.setup is not None:
            self.setup.tick()

    def run(self, stop: threading.Event) -> None:
        pump(stop, self.tick, lambda: LOOP_POLL_S)

    def refresh(self) -> None:
        """Menu about to open: current titles, checkmarks and enabled states."""
        on, off = AppKit.NSControlStateValueOn, AppKit.NSControlStateValueOff
        self.status_line.setTitle_(f"Hark: {LABELS[menu_state(self.c, time.time())]}")
        text = self.last_text()
        self.last_line.setTitle_(f"Last: “{preview(text)}”" if text else "No dictation yet")
        self.copy_item.setEnabled_(bool(text))
        self.retry_item.setEnabled_(self.c.svc.cfg.retry.keep > 0)
        self.pause_item.setTitle_("Resume Dictation" if self.c.paused else "Pause Dictation")
        for name, item in self.profile_items.items():
            item.setState_(on if name == self.c.profile_override else off)
        if self.login_item is not None:
            enabled = service.login_item_enabled(Path.home())
            self.login_item.setState_(on if enabled else off)

    # --- actions ------------------------------------------------------------------------

    def last_text(self) -> str:
        cfg = self.c.svc.cfg
        if self.c.last_text or not cfg.history.enabled:
            return self.c.last_text
        return history.last_text(expand(cfg.history.dir)) or ""

    def copy_last(self) -> None:
        text = self.last_text()
        if text and not self.c.svc.injector.copy(text):
            self.c.svc.feedback.notify("Could not write the clipboard")

    def hark(self, *args: str) -> list[str]:
        """argv for a child `hark` command run by this interpreter."""
        return [sys.executable, "-m", "hark_client", *self.cli, *args]

    def retry_last(self) -> None:
        def work() -> None:
            proc = subprocess.run(  # noqa: S603 - our own CLI
                self.hark("retry", "last"), capture_output=True, text=True, timeout=120, check=False
            )
            lines = (proc.stdout + proc.stderr).strip().splitlines()
            self.c.svc.feedback.notify(
                lines[-1] if lines else f"hark retry: exit {proc.returncode}"
            )

        threading.Thread(target=work, daemon=True, name="hark-retry").start()

    def toggle_pause(self) -> None:
        self.c.set_paused(not self.c.paused)
        self.tick()

    def set_profile(self, name: str | None) -> None:
        self.c.set_profile_override(name)

    def open_history(self) -> None:
        open_in_terminal(self.hark("history"), "history")

    def open_settings(self) -> None:
        path = self.config_path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(CONFIG_STUB)
        subprocess.Popen(["/usr/bin/open", "-t", str(path)])  # noqa: S603
        self.c.svc.feedback.notify("Restart Hark after editing the config")

    def run_doctor(self) -> None:
        def work() -> None:
            LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            report = LOG_DIR / "doctor.txt"
            with report.open("w") as out:
                subprocess.run(  # noqa: S603 - our own CLI, with Hark's permissions
                    self.hark("doctor"), stdout=out, stderr=subprocess.STDOUT, timeout=60,
                    check=False,
                )  # fmt: skip
            subprocess.Popen(["/usr/bin/open", "-t", str(report)])  # noqa: S603

        threading.Thread(target=work, daemon=True, name="hark-doctor").start()

    def open_setup(self) -> None:
        if self.setup is None:
            from hark_client.onboarding import Setup  # noqa: PLC0415

            self.setup = Setup(self.c, self.relaunch)
        self.setup.show()

    def toggle_login(self) -> None:
        app = bundle_path()
        if app is None:
            return
        enable = not service.login_item_enabled(Path.home())
        try:
            message = service.set_login_item(Path.home(), app, enabled=enable)
        except service.ServiceError as e:
            message = f"Launch at login: {e}"
        self.c.svc.feedback.notify(message)

    def relaunch(self) -> None:
        """Quit and open this bundle again once this process has exited (new permissions are
        picked up only by a new process)."""
        app = bundle_path()
        if app is None:
            self.c.svc.feedback.notify("Restart `hark app` to apply the new permissions")
            return
        pid = Foundation.NSProcessInfo.processInfo().processIdentifier()
        script = f"while kill -0 {pid} 2>/dev/null; do sleep 0.2; done; /usr/bin/open {shlex.quote(str(app))}"
        subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True)  # noqa: S603
        self.stop.set()
