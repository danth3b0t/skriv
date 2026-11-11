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

from hark_client.continuation import CLICK, KeyPress

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("hark.hotkey")

# pynput Key names of modifiers -> KeyPress.mods names
MODIFIERS = {
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
}  # fmt: skip


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
        on_input: Callable[[KeyPress], object] | None = None,
    ) -> None:
        """on_input: any other key press (modifiers folded into ``mods``) or mouse click."""
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
        self._mods: dict[object, str] = {}  # held modifier key -> mods name
        self._lock = threading.Lock()
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._mouse: _Listener | None = None
        if on_input is not None:
            from pynput import mouse  # noqa: PLC0415 - optional, platform-specific

            self._mouse = mouse.Listener(on_click=self._click)

    def _key_press(self, key: object) -> KeyPress:
        mods = frozenset(self._mods.values())
        if isinstance(key, self._keyboard.KeyCode):
            return KeyPress("char", key.char or "", mods)
        return KeyPress(str(getattr(key, "name", "")), "", mods)

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
            elif (mod := MODIFIERS.get(str(getattr(key, "name", "")))) is not None:
                self._mods[key] = mod
                return
            elif self.on_input is not None:
                press = self._key_press(key)
                on_input = self.on_input

                def action() -> object:
                    return on_input(press)
            else:
                return
        action()

    def _click(self, _x: int, _y: int, _button: object, pressed: bool) -> None:  # noqa: FBT001 - pynput callback signature
        if pressed and self.on_input is not None:
            self.on_input(CLICK)

    def _release(self, key: object) -> None:
        with self._lock:
            self._mods.pop(key, None)
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
