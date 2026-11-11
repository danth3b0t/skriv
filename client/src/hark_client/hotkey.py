"""Push-to-talk via a global pynput listener (macOS; Linux X11 with the `x11` extra).

Hold the key to dictate, release to paste; Esc while holding cancels. On macOS the
process running hark (i.e. your terminal app) needs Input Monitoring (to see keys) and
Accessibility (to post Cmd+V) in System Settings > Privacy & Security. Wayland has no
global key grabs for clients: bind `hark ctl start/stop` in the compositor instead.

pynput ships no type information, so the unknown-type rules are relaxed for this module
only; the exported class has precise signatures.
"""
# pyright: reportMissingTypeStubs=false, reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportAny=false

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("hark.hotkey")


class _Listener(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class PushToTalk:
    def __init__(
        self,
        key: str,
        on_down: Callable[[], object],
        on_up: Callable[[], object],
        on_cancel: Callable[[], object],
        on_input: Callable[[], object] | None = None,
    ) -> None:
        """on_input: any other key press or mouse click (any time)."""
        from pynput import keyboard  # noqa: PLC0415 - optional, platform-specific

        self._keyboard = keyboard
        self.target = (
            keyboard.KeyCode.from_char(key) if len(key) == 1 else getattr(keyboard.Key, key, None)
        )
        if self.target is None:
            raise ValueError(
                f"unknown hotkey {key!r}; use a pynput Key name like alt_r, cmd_r, f13"
            )
        self.on_down, self.on_up, self.on_cancel = on_down, on_up, on_cancel
        self.on_input = on_input
        self._held = False
        self._lock = threading.Lock()
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._mouse: _Listener | None = None
        if on_input is not None:
            from pynput import mouse  # noqa: PLC0415 - optional, platform-specific

            self._mouse = mouse.Listener(on_click=self._click)

    def _press(self, key: object) -> None:
        with self._lock:
            if key == self.target:
                if self._held:
                    return  # auto-repeat
                self._held = True
                action = self.on_down
            elif key == self._keyboard.Key.esc and self._held:
                self._held = False
                action = self.on_cancel
            elif self.on_input is not None and not self._held:
                action = self.on_input
            else:
                return
        action()

    def _click(self, _x: int, _y: int, _button: object, pressed: bool) -> None:  # noqa: FBT001 - pynput callback signature
        if pressed and self.on_input is not None:
            self.on_input()

    def _release(self, key: object) -> None:
        with self._lock:
            if key != self.target or not self._held:
                return
            self._held = False
        self.on_up()

    def start(self) -> None:
        self._listener.start()
        if self._mouse is not None:
            self._mouse.start()
        log.info("push-to-talk: hold %s (Esc cancels)", self.target)

    def stop(self) -> None:
        self._listener.stop()
        if self._mouse is not None:
            self._mouse.stop()
