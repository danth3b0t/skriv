"""Skriv.app's Info.plist (used by scripts/build-macos-app).

The bundle holds only the native launcher (packaging/macos/launcher.swift) and this plist; the
launcher runs the virtualenv named by ``SkrivVenv`` in its own process. TCC keys its grants on
the bundle's code signature, so everything that changes the signature (this plist, the launcher)
should change rarely: Python edits in the venv do not.

    python -m skriv_client.bundle OUT.plist --venv /abs/path/.venv
"""

from __future__ import annotations

import argparse
import plistlib
from importlib import metadata
from pathlib import Path

BUNDLE_ID = "dev.skriv.Skriv"
EXECUTABLE = "Skriv"
MIN_MACOS = "13.0"  # SMAppService (launch at login)

MIC_USAGE = (
    "Skriv records your voice only while you hold the dictation key and sends it to your skriv "
    "server for transcription."
)
APPLE_EVENTS_USAGE = (
    "Skriv uses AppleScript to show notifications and may ask Terminal to show its history."
)


def info_plist(venv: Path, version: str) -> dict[str, object]:
    """Info.plist for a bundle that runs ``venv`` (absolute, with a pyvenv.cfg)."""
    if not venv.is_absolute():
        raise ValueError(f"venv must be an absolute path, got {venv}")
    if not (venv / "pyvenv.cfg").is_file():
        raise ValueError(f"{venv} is not a virtualenv (no pyvenv.cfg); run `uv sync` first")
    return {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "Skriv",
        "CFBundleDisplayName": "Skriv",
        "CFBundleExecutable": EXECUTABLE,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "LSUIElement": True,  # menu-bar app: no Dock icon, no app menu
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": MIC_USAGE,
        "NSAppleEventsUsageDescription": APPLE_EVENTS_USAGE,
        "SkrivVenv": str(venv),
    }


class _Args(argparse.Namespace):
    out: Path
    venv: Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m skriv_client.bundle", description=__doc__)
    p.add_argument("out", type=Path, help="Info.plist to write")
    p.add_argument("--venv", type=Path, required=True, help="virtualenv the app runs (absolute)")
    args = p.parse_args(argv, namespace=_Args())
    try:
        info = info_plist(args.venv, metadata.version("skriv-client"))
    except ValueError as e:
        p.error(str(e))
    args.out.write_bytes(plistlib.dumps(info, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
