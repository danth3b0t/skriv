"""Hark.app Info.plist (scripts/build-macos-app)."""

from __future__ import annotations

import plistlib
from typing import TYPE_CHECKING, cast

import pytest

from hark_client import bundle

if TYPE_CHECKING:
    from pathlib import Path


def test_info_plist_is_a_menu_bar_app_that_runs_the_venv(tmp_path: Path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /x/bin\nversion_info = 3.12.15\n")
    out = tmp_path / "Info.plist"
    assert bundle.main([str(out), "--venv", str(venv)]) == 0
    info = cast("dict[str, object]", plistlib.loads(out.read_bytes()))
    assert info["CFBundleIdentifier"] == "dev.hark.Hark"  # TCC grants hang on this + signature
    assert info["CFBundleExecutable"] == "Hark"
    assert info["CFBundlePackageType"] == "APPL"
    assert info["LSUIElement"] is True
    assert "voice" in str(info["NSMicrophoneUsageDescription"])
    assert info["NSAppleEventsUsageDescription"]
    assert info["HarkVenv"] == str(venv)
    assert info["CFBundleShortVersionString"] == info["CFBundleVersion"] != ""


def test_info_plist_refuses_a_relative_or_missing_venv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    with pytest.raises(ValueError, match="absolute"):
        bundle.info_plist(tmp_path.relative_to(tmp_path.parent), "1")
    with pytest.raises(SystemExit):
        bundle.main([str(tmp_path / "Info.plist"), "--venv", str(tmp_path)])
    assert "no pyvenv.cfg" in capsys.readouterr().err
    assert not (tmp_path / "Info.plist").exists()
