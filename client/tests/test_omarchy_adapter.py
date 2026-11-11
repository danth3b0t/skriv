"""Omarchy dictation-backend contract for packaging/omarchy/omarchy-dictation-hark (fake hark/systemctl)."""

from __future__ import annotations

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
