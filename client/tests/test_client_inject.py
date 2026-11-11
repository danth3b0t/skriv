"""Linux paste command construction (fake runner; nothing is executed)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from hark_client.inject import LinuxInjector, detect_method

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeRunner:
    def __init__(self, hyprctl_reply: str = "ok", clipboard: str = "old") -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.hyprctl_reply = hyprctl_reply
        self.clipboard = clipboard

    def __call__(
        self, argv: Sequence[str], *, stdin: str | None = None, capture: bool = True
    ) -> tuple[int, str]:
        argv = list(argv)
        self.calls.append((argv, stdin))
        if argv[0] in ("wl-copy", "xclip") and stdin is not None:
            self.clipboard = stdin
        if argv[0] in ("wl-paste", "xclip") and stdin is None:
            return 0, self.clipboard
        if argv[0] == "hyprctl":
            return 0, self.hyprctl_reply
        return 0, ""

    def key_calls(self) -> list[list[str]]:
        return [a for a, _ in self.calls if a[0] in ("hyprctl", "wtype", "xdotool")]


@pytest.mark.parametrize(
    ("mode", "terminal", "hyprctl_reply", "expected_keys"),
    [
        ("hyprland", True, "ok", [["hyprctl", "dispatch", "sendshortcut", "CTRL SHIFT, V,"]]),
        ("hyprland", False, "ok", [["hyprctl", "dispatch", "sendshortcut", "CTRL, V,"]]),
        (  # neither dispatcher syntax works (e.g. unknown Hyprland version): wtype fallback
            "hyprland", True, "Invalid dispatcher",
            [
                ["hyprctl", "dispatch", "sendshortcut", "CTRL SHIFT, V,"],
                ["hyprctl", "dispatch", 'hl.dsp.send_shortcut({ mods = "CTRL + SHIFT", key = "V" })'],
                ["wtype", "-M", "ctrl", "-M", "shift", "-k", "v", "-m", "shift", "-m", "ctrl"],
            ],
        ),
        ("wtype", False, "", [["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"]]),
        ("x11", True, "", [["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"]]),
    ],
)  # fmt: skip
def test_paste_keys(mode: str, terminal: bool, hyprctl_reply: str, expected_keys: list[list[str]]):
    run = FakeRunner(hyprctl_reply)
    result = LinuxInjector(mode, restore=False, run=run).paste("hello", terminal=terminal)
    assert result.ok
    assert run.clipboard == "hello"
    assert run.key_calls() == expected_keys
    assert not any(
        "\n" in a or "Return" in a for argv in run.key_calls() for a in argv
    )  # never Enter


def test_previous_clipboard_text_is_restored():
    run = FakeRunner(clipboard="previous")
    LinuxInjector("hyprland", restore=True, restore_delay_s=0.0, run=run).paste(
        "dictated", terminal=False
    )
    for _ in range(100):
        if run.clipboard == "previous":
            break
        time.sleep(0.01)
    assert run.clipboard == "previous"


@pytest.mark.parametrize(
    ("env", "platform", "method"),
    [
        ({"WAYLAND_DISPLAY": "wayland-1", "HYPRLAND_INSTANCE_SIGNATURE": "x"}, "linux", "hyprland"),
        ({"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}, "linux", "wtype"),
        ({"DISPLAY": ":0"}, "linux", "x11"),
        ({}, "linux", "dry-run"),
        ({}, "darwin", "macos"),
    ],
)
def test_detect_method(env: dict[str, str], platform: str, method: str):
    assert detect_method(env, platform) == method
