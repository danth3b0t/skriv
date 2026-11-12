"""Linux paste command construction (fake runner; nothing is executed)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from hark_client.inject import TIMED_OUT, LinuxInjector, detect_method

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeRunner:
    def __init__(
        self,
        hyprctl_reply: str = "ok",
        clipboard: str = "old",
        repl_reply: str | None = None,
        times_out: Sequence[str] = (),
    ) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.times_out = list(times_out)  # argv prefix of a command that does not finish
        self.hyprctl_reply = hyprctl_reply
        self.repl_reply = repl_reply  # None: `hyprctl repl` is unknown (hyprlang Hyprland)
        self.clipboard = clipboard

    def __call__(
        self, argv: Sequence[str], *, stdin: str | None = None, capture: bool = True
    ) -> tuple[int, str]:
        argv = list(argv)
        self.calls.append((argv, stdin))
        if self.times_out and argv[: len(self.times_out)] == self.times_out:
            return TIMED_OUT, "timed out"
        if argv[0] in ("wl-copy", "xclip") and stdin is not None:
            self.clipboard = stdin
        if argv[0] in ("wl-paste", "xclip") and stdin is None:
            return 0, self.clipboard
        if argv[:2] == ["hyprctl", "repl"]:
            return 0, "unknown request" if self.repl_reply is None else self.repl_reply
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


ADDR = "0x55d0C0a1b2c0"


def test_lua_hyprland_rechecks_focus_and_targets_the_window_inside_the_compositor():
    run = FakeRunner(hyprctl_reply="Invalid dispatcher", repl_reply='"paste_sent"')
    inj = LinuxInjector("hyprland", restore=False, run=run)
    assert inj.paste("a", terminal=True, target=ADDR).ok
    assert inj.paste("b", terminal=False, target=ADDR).ok
    calls = run.key_calls()
    assert [c[:2] for c in calls] == [["hyprctl", "repl"]] * 2  # no other attempt, no wtype
    lua = calls[0][2]
    assert "if a ~= '55d0c0a1b2c0' then return 'focus_changed:'" in lua
    assert "mods = 'CTRL + SHIFT', key = 'V', window = 'address:0x55d0c0a1b2c0'" in lua
    assert "mods = 'CTRL', key = 'V'" in calls[1][2]


def test_lua_hyprland_focus_change_keeps_the_text_in_the_clipboard():
    run = FakeRunner(clipboard="previous", repl_reply="focus_changed:abc123")
    res = LinuxInjector("hyprland", restore=True, restore_delay_s=0.0, run=run).paste(
        "dictated", terminal=False, target=ADDR
    )
    assert (res.ok, res.focus_changed) == (False, True)
    assert "0xabc123" in res.detail
    assert [c[1] for c in run.key_calls()] == ["repl"]  # nothing was sent
    time.sleep(0.05)
    assert run.clipboard == "dictated"  # not restored


def test_hyprlang_hyprland_targets_the_address_and_is_remembered():
    run = FakeRunner(hyprctl_reply="ok")  # repl unknown
    inj = LinuxInjector("hyprland", restore=False, run=run)
    inj.paste("a", terminal=True, target=ADDR)
    inj.paste("b", terminal=True, target=ADDR)
    assert run.key_calls() == [
        ["hyprctl", "repl", run.key_calls()[0][2]],
        ["hyprctl", "dispatch", "sendshortcut", "CTRL SHIFT, V, address:0x55d0c0a1b2c0"],
        ["hyprctl", "dispatch", "sendshortcut", "CTRL SHIFT, V, address:0x55d0c0a1b2c0"],
    ]


@pytest.mark.parametrize("target", ["", "0x12'); os.exit() --", "123:45"])
def test_unknown_or_malformed_target_falls_back_to_an_untargeted_shortcut(target: str):
    run = FakeRunner(hyprctl_reply="ok", repl_reply="paste_sent")
    assert (
        LinuxInjector("hyprland", restore=False, run=run)
        .paste("x", terminal=False, target=target)
        .ok
    )
    assert run.key_calls() == [["hyprctl", "dispatch", "sendshortcut", "CTRL, V,"]]


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
    ("target", "times_out"),
    [
        ("", ["hyprctl", "dispatch", "sendshortcut"]),
        (ADDR, ["hyprctl", "repl"]),
        ("", ["wtype"]),
    ],
)
def test_a_paste_command_that_timed_out_is_not_retried_and_the_text_stays(
    target: str, times_out: list[str]
):
    run = FakeRunner(clipboard="previous", repl_reply="paste_sent", times_out=times_out)
    mode = "wtype" if times_out == ["wtype"] else "hyprland"
    res = LinuxInjector(mode, restore=True, restore_delay_s=0.0, run=run).paste(
        "dictated", terminal=False, target=target
    )
    assert not res.ok
    assert "not retried" in res.detail
    assert [c[: len(times_out)] for c in run.key_calls()] == [times_out]  # one attempt only
    time.sleep(0.05)
    assert run.clipboard == "dictated"  # not restored: the user can paste it


def test_back_to_back_pastes_restore_the_users_clipboard_not_the_previous_dictation():
    run = FakeRunner(clipboard="user's")
    inj = LinuxInjector("hyprland", restore=True, restore_delay_s=0.05, run=run)
    assert inj.paste("first", terminal=False).ok
    assert inj.paste("second", terminal=False).ok  # before the first restore ran
    time.sleep(0.2)
    assert run.clipboard == "user's"


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
