"""Omarchy dictation-backend contract for packaging/omarchy/omarchy-dictation-hark, and the install /
remove scripts around it (fake hark, systemctl and Omarchy tools)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ADAPTER = Path(__file__).parents[2] / "packaging" / "omarchy" / "omarchy-dictation-hark"

FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
case "$(basename "$0") $1" in
  "hark ctl") echo "$HARK_REPLY"; exit "$HARK_EXIT" ;;
  "systemctl --user") [ "$2" = cat ] && exit "$UNIT_EXIT"; exit 0 ;;
esac
"""


def run_adapter(
    tmp_path: Path, *args: str, hark_exit: int = 0, unit_exit: int = 1, with_hark: bool = True
) -> tuple[int, list[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("hark", "systemctl") if with_hark else ("systemctl",):
        exe = bin_dir / name
        exe.write_text(FAKE)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FAKE_LOG": str(log),
        "HARK_EXIT": str(hark_exit),
        "HARK_REPLY": "ok recording" if hark_exit == 0 else "error boom",
        "UNIT_EXIT": str(unit_exit),
    }
    p = subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/bash", str(ADAPTER), *args], env=env, capture_output=True, text=True, check=False
    )
    return p.returncode, log.read_text().splitlines(), p.stderr


@pytest.mark.parametrize("action", ["start", "stop", "toggle"])
def test_actions_map_to_hark_ctl(tmp_path: Path, action: str):
    code, calls, _ = run_adapter(tmp_path, action)
    assert (code, calls) == (0, [f"hark ctl {action}"])


@pytest.mark.parametrize("args", [[], ["record"], [""]])
def test_bad_usage_fails(tmp_path: Path, args: list[str]):
    code, calls, err = run_adapter(tmp_path, *args)
    assert code == 1
    assert calls == []
    assert "Usage" in err


def test_stop_succeeds_without_a_daemon_or_without_hark(tmp_path: Path):
    assert run_adapter(tmp_path, "stop", hark_exit=2)[0] == 0
    assert run_adapter(tmp_path, "stop", with_hark=False)[0] == 0


def test_start_without_daemon_starts_the_unit_and_fails(tmp_path: Path):
    code, calls, err = run_adapter(tmp_path, "start", hark_exit=2, unit_exit=0)
    assert code == 1
    assert calls == [
        "hark ctl start",
        "systemctl --user cat hark.service",
        "systemctl --user start --no-block hark.service",
    ]
    assert "starting hark.service" in err
    code, calls, err = run_adapter(
        tmp_path, "toggle", hark_exit=2, unit_exit=1
    )  # no unit installed
    assert code == 1
    assert "hark run" in err
    assert not any("start --no-block" in c for c in calls)


def test_daemon_error_reply_fails(tmp_path: Path):
    code, _, err = run_adapter(tmp_path, "start", hark_exit=1)
    assert code == 1
    assert "error boom" in err


INSTALL = ADAPTER.with_name("omarchy-install-dictation-hark")
REMOVE = ADAPTER.with_name("omarchy-remove-dictation-hark")
FAKE_TOOL = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
exit "${FAKE_EXIT:-0}"
"""


OMARCHY_4 = (  # Omarchy 4's shell: the bar widget and the panel key get installed
    "omarchy-shell", "omarchy-plugin-enable", "omarchy-plugin-disable", "omarchy-restart-hyprctl",
)  # fmt: skip
BIND = 'o.bind("SUPER + ALT + D", "Dictation panel", "hark-panel")'


def run_script(
    tmp_path: Path, script: Path, fake_exit: int = 0, *, omarchy4: bool = False
) -> tuple[int, list[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("hark", "omarchy-dictation-use", "systemctl", *(OMARCHY_4 if omarchy4 else ())):
        exe = bin_dir / name
        exe.write_text(FAKE_TOOL)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log"
    log.write_text("")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "FAKE_LOG": str(log),
        "FAKE_EXIT": str(fake_exit),
        "TMPDIR": str(tmp_path),
    }
    p = subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/bash", str(script)], env=env, capture_output=True, text=True, check=False
    )
    return p.returncode, log.read_text().splitlines(), p.stderr


def test_install_from_a_checkout_links_the_adapter_and_installs_the_service(tmp_path: Path):
    code, calls, err = run_script(tmp_path, INSTALL)  # no Omarchy 4 shell: no bar widget
    assert (code, calls) == (
        0, ["hark service install client", "omarchy-dictation-use hark", "hark doctor"],
    )  # fmt: skip
    assert "skipped hark's bar widget" in err
    link = tmp_path / "home" / ".local" / "bin" / "omarchy-dictation-hark"
    assert link.resolve() == ADAPTER.resolve()


def test_install_stops_when_the_service_cannot_be_installed(tmp_path: Path):
    code, calls, _ = run_script(tmp_path, INSTALL, fake_exit=1)
    assert code == 1
    assert calls == ["hark service install client"]  # hark is not made the backend


def test_remove_uninstalls_the_service_and_unselects_hark(tmp_path: Path):
    run_script(tmp_path, INSTALL)
    backend = tmp_path / "config" / "omarchy" / "defaults" / "dictation"
    backend.parent.mkdir(parents=True)
    backend.write_text("hark")
    code, calls, _ = run_script(tmp_path, REMOVE)
    assert code == 0
    assert calls[-1] == "hark service uninstall client"
    assert not backend.exists()
    assert not (tmp_path / "home" / ".local" / "bin" / "omarchy-dictation-hark").is_symlink()


def test_on_omarchy_4_install_adds_the_bar_widget_and_the_panel_key_once_and_remove_undoes_it(
    tmp_path: Path,
):
    bindings = tmp_path / "config" / "hypr" / "bindings.lua"
    bindings.parent.mkdir(parents=True)
    mine = '-- my keys\no.bind("SUPER + SHIFT + R", "SSH", "alacritty -e ssh box")\n'
    bindings.write_text(mine)
    code, calls, err = run_script(tmp_path, INSTALL, omarchy4=True)
    assert code == 0, err
    assert calls == [
        "hark service install client",
        "omarchy-dictation-use hark",
        "omarchy-shell shell rescanPlugins",
        "omarchy-plugin-enable hark.shell",
        "omarchy-restart-hyprctl ",
        "hark doctor",
    ]
    code, calls, err = run_script(tmp_path, INSTALL, omarchy4=True)
    assert code == 0, err
    assert "omarchy-restart-hyprctl " not in calls  # the key is bound already
    plugin = tmp_path / "config" / "omarchy" / "plugins" / "hark.shell"
    assert sorted(p.name for p in plugin.iterdir()) == ["Model.js", "Widget.qml", "manifest.json"]
    assert not any(p.is_symlink() for p in plugin.iterdir())  # the shell refuses symlinks
    assert json.loads((plugin / "manifest.json").read_text())["id"] == "hark.shell"
    assert not list(tmp_path.glob("hark.shell.*"))  # the staging folder moved in
    assert bindings.read_text().startswith(mine)
    assert bindings.read_text().count(BIND) == 1
    panel = tmp_path / "home" / ".local" / "bin" / "hark-panel"
    assert panel.resolve() == ADAPTER.with_name("hark-panel").resolve()

    code, calls, err = run_script(tmp_path, REMOVE, omarchy4=True)
    assert code == 0, err
    assert "omarchy-plugin-disable hark.shell" in calls
    assert not plugin.exists()
    assert bindings.read_text() == mine
    assert not panel.is_symlink()


def test_remove_leaves_a_plugin_folder_that_is_not_hark(tmp_path: Path):
    plugin = tmp_path / "config" / "omarchy" / "plugins" / "hark.shell"
    plugin.mkdir(parents=True)
    (plugin / "manifest.json").write_text('{"id": "someone.else"}')
    code, _, err = run_script(tmp_path, REMOVE, omarchy4=True)
    assert code == 0, err
    assert plugin.exists()
