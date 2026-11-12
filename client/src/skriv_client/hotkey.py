"""Push-to-talk via a global pynput listener (macOS; Linux X11 with the `x11` extra).

Hold the key to dictate, release to paste. A short tap (under ``hotkey.tap_ms``) instead keeps
recording hands-free until the next tap. Esc while recording cancels. On macOS the process
running skriv (i.e. your terminal app) needs Input Monitoring (to see keys) and Accessibility (to
post Cmd+V) in System Settings > Privacy & Security. Wayland has no global key grabs for
clients: bind `skriv ctl start/stop/toggle` in the compositor instead.

Listener callbacks only update the key state machine; the actions (start, stop, the shadow
buffer's key tracking...) run in order on one worker thread, so a slow action never holds up
the listener.

pynput ships no type information, so the unknown-type rules are relaxed for this module
only; the exported class has precise signatures.
"""
# pyright: reportMissingTypeStubs=false, reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportAny=false

from __future__ import annotations

import importlib
import logging
import queue
import sys
import threading
import time
from typing import TYPE_CHECKING, Literal, Protocol

from skriv_client.continuation import CLICK, KeyPress

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("skriv.hotkey")

# pynput Key names of modifiers -> KeyPress.mods names
MODIFIERS = {
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
}  # fmt: skip

type Action = Callable[[], object]
type Mode = Literal["idle", "hold", "hands_free"]


class _Listener(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class _Serial:
    """Runs actions one after another on a daemon thread, in submission order."""

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[Action | None] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="skriv-hotkey")

    def __call__(self, action: Action) -> None:
        self._queue.put(action)

    def _run(self) -> None:
        while (action := self._queue.get()) is not None:
            try:
                action()
            except Exception:
                log.exception("hotkey action failed")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)


class PushToTalk:
    """Hotkey state machine: idle -> hold (key down: ``on_down``) -> on release either ``on_up``
    (held at least ``tap_s``) or hands_free (a tap: ``on_tap``; recording continues) -> the next
    press ``on_up``. ``recording()`` tells it about recordings started/stopped elsewhere (ctl,
    max duration), so a press while one runs stops it and Esc cancels it."""

    def __init__(  # noqa: PLR0913 - one callback per event
        self,
        key: str,
        on_down: Action,
        on_up: Action,
        on_cancel: Action,
        on_input: Callable[[KeyPress], object] | None = None,
        *,
        on_tap: Action | None = None,
        tap_s: float = 0.0,
        swallow_esc: bool = False,
        clock: Callable[[], float] = time.monotonic,
        dispatch: Callable[[Action], None] | None = None,
    ) -> None:
        """on_input: any other key press (modifiers folded into ``mods``) or mouse click.
        tap_s: releases sooner than this after the press are taps (0: hold only).
        swallow_esc: the Esc that cancels a recording does not reach the focused app (macOS:
        an active event tap, which needs Accessibility; X11: a grab of Esc while recording).
        dispatch: runs actions (default: in order on a worker thread)."""
        from pynput import keyboard  # noqa: PLC0415 - optional, platform-specific

        self._keyboard = keyboard
        self.target = self._resolve(key)
        self.on_down, self.on_up, self.on_cancel = on_down, on_up, on_cancel
        self.on_input = on_input
        self.on_tap = on_tap
        self.tap_s = tap_s if on_tap is not None else 0.0
        self._clock = clock
        self._serial: _Serial | None = None
        if dispatch is None:
            dispatch = self._serial = _Serial()
        self._dispatch = dispatch
        self._down = False  # the hotkey is physically down
        self._down_at = 0.0
        self._mode: Mode = "idle"
        self._external = False  # a recording is running (from any source)
        self._finishing = False  # a dictation is being transcribed/pasted: Esc cancels it too
        self._mods: dict[object, str] = {}  # held modifier key -> mods name
        self._lock = threading.Lock()
        self.swallow_esc = swallow_esc
        self._swallowing = False  # an Esc keystroke that cancelled: until its release
        self._drop = False  # suppress the event the listener just reported (macOS)
        self._grab: _XEscGrab | None = None
        if swallow_esc and sys.platform.startswith("linux"):
            self._grab = _XEscGrab.open()
        intercept = self._intercept if swallow_esc and sys.platform == "darwin" else None
        # an intercept turns the macOS tap from listen-only into a filter (needs Accessibility)
        self._listener = (
            keyboard.Listener(
                on_press=self._press, on_release=self._release, darwin_intercept=intercept
            )
            if intercept is not None
            else keyboard.Listener(on_press=self._press, on_release=self._release)
        )
        self._mouse: _Listener | None = None
        if on_input is not None:
            from pynput import mouse  # noqa: PLC0415 - optional, platform-specific

            self._mouse = mouse.Listener(on_click=self._click)

    def _resolve(self, key: str) -> object:
        keyboard = self._keyboard
        target = (
            keyboard.KeyCode.from_char(key) if len(key) == 1 else getattr(keyboard.Key, key, None)
        )
        if target is None:
            raise ValueError(f"unknown key {key!r}; use a pynput Key name like alt_r, cmd_r, f13")
        return target

    @property
    def mode(self) -> Mode:
        return self._mode

    def recording(self, on: bool) -> None:  # noqa: FBT001 - a state flag, called by the daemon
        """The daemon started (True) or ended (False) a recording, whatever started it."""
        with self._lock:
            self._external = on
            if not on:
                self._mode = "idle"  # a later release of a held key does nothing
            hold = on or self._finishing
        self._hold_esc(hold=hold)

    def finishing(self, on: bool) -> None:  # noqa: FBT001 - a state flag, called by the daemon
        """A dictation started (True) or stopped (False) being transcribed and pasted: until
        it is pasted, Esc cancels it."""
        with self._lock:
            self._finishing = on
            hold = on or self._external
        self._hold_esc(hold=hold)

    def _hold_esc(self, *, hold: bool) -> None:
        grab = self._grab
        if grab is not None:  # in order with the actions, off the caller's (locked) path
            self._dispatch(lambda: grab.hold(on=hold))

    def _intercept(self, event_type: object, event: object) -> object | None:
        """macOS event tap filter, right after the listener reported ``event``: None drops it.
        Drops only the Esc keystroke that cancelled a recording (down, repeats, up)."""
        with self._lock:
            drop, self._drop = self._drop, False
        return None if drop else event

    def _key_press(self, key: object) -> KeyPress:
        mods = frozenset(self._mods.values())
        if isinstance(key, self._keyboard.KeyCode):
            return KeyPress("char", key.char or "", mods)
        return KeyPress(str(getattr(key, "name", "")), "", mods)

    def _press(self, key: object, injected: bool = False) -> None:  # noqa: FBT001, FBT002 - pynput callback signature
        with self._lock:
            action = self._on_press(key)
        if action is not None:
            self._dispatch(action)

    def _on_press(self, key: object) -> Action | None:  # noqa: PLR0911 - one rule per key class
        # caller holds the lock
        if key == self.target:
            if self._down:
                return None  # auto-repeat
            self._down = True
            if self._mode == "hands_free" or (self._mode == "idle" and self._external):
                self._mode, self._external = "idle", False
                return self.on_up
            self._mode, self._down_at = "hold", self._clock()
            return self.on_down
        if key == self._keyboard.Key.esc and self._swallowing:
            self._drop = True  # auto-repeat of the Esc that cancelled
            return None
        if key == self._keyboard.Key.esc and (
            self._mode != "idle" or self._external or self._finishing
        ):
            self._mode, self._external = "idle", False
            self._swallowing = self._drop = self.swallow_esc
            return self.on_cancel
        if (mod := MODIFIERS.get(str(getattr(key, "name", "")))) is not None:
            self._mods[key] = mod
            return None
        on_input = self.on_input
        if on_input is None:
            return None
        press = self._key_press(key)
        return lambda: on_input(press)

    def _click(self, _x: int, _y: int, _button: object, pressed: bool) -> None:  # noqa: FBT001 - pynput callback signature
        on_input = self.on_input
        if pressed and on_input is not None:
            self._dispatch(lambda: on_input(CLICK))

    def _release(self, key: object, injected: bool = False) -> None:  # noqa: FBT001, FBT002 - pynput callback signature
        with self._lock:
            self._mods.pop(key, None)
            if key == self._keyboard.Key.esc and self._swallowing:
                self._swallowing, self._drop = False, True
                return
            if key != self.target or not self._down:
                return
            self._down = False
            if self._mode != "hold":
                return  # cancelled, stopped elsewhere, or the press that stopped hands-free
            if self.on_tap is not None and self._clock() - self._down_at < self.tap_s:
                self._mode = "hands_free"
                action = self.on_tap
            else:
                self._mode, self._external = "idle", False
                action = self.on_up
        self._dispatch(action)

    def start(self) -> None:
        if self._serial is not None:
            self._serial.start()
        self._listener.start()
        if self._mouse is not None:
            self._mouse.start()
        tap = f", tap for hands-free (< {self.tap_s * 1000:.0f} ms)" if self.tap_s else ""
        log.info("push-to-talk: hold %s%s; Esc cancels", self.target, tap)

    def stop(self) -> None:
        self._listener.stop()
        if self._mouse is not None:
            self._mouse.stop()
        if self._grab is not None:
            self._dispatch(self._grab.close)  # after any pending grab change
        if self._serial is not None:
            self._serial.stop()


class _XEscGrab:
    """X11: a passive grab of Esc (any modifiers) on the root window while recording, so Esc goes
    to skriv instead of the focused window; the listener (RECORD extension) still sees it and
    cancels, which releases the grab. **Untested on real hardware.**"""

    def __init__(self, display) -> None:  # noqa: ANN001 - an untyped Xlib Display
        xk = importlib.import_module("Xlib.XK")  # python-xlib comes with pynput on Linux
        self._display = display
        self._root = display.screen().root
        self._keycode = display.keysym_to_keycode(xk.string_to_keysym("Escape"))
        self._held = False

    @classmethod
    def open(cls) -> _XEscGrab | None:
        try:
            return cls(importlib.import_module("Xlib.display").Display())
        except Exception as e:  # noqa: BLE001 - no X display / no python-xlib: Esc is not swallowed
            log.warning(
                "cannot grab Esc (%s: %s); it will reach the focused app", type(e).__name__, e
            )
            return None

    def hold(self, *, on: bool) -> None:
        """Grab (on) or release Esc. Runs on the hotkey worker thread only."""
        x = importlib.import_module("Xlib.X")
        error = importlib.import_module("Xlib.error")
        if on == self._held:
            return
        if on:
            catch = error.CatchError(error.BadAccess)
            self._root.grab_key(
                self._keycode,
                x.AnyModifier,
                True,  # noqa: FBT003 - Xlib's owner_events
                x.GrabModeAsync,
                x.GrabModeAsync,
                onerror=catch,
            )
            self._display.sync()
            if catch.get_error() is not None:
                log.warning("another client grabs Esc; it will reach the focused app")
                return
        else:
            self._root.ungrab_key(self._keycode, x.AnyModifier)
            self._display.sync()
        self._held = on
        while self._display.pending_events():  # grabbed key events are not used: drain them
            self._display.next_event()

    def close(self) -> None:
        self._display.close()
