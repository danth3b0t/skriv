"""`skriv service` on macOS: launchd agent plists and the install/status/restart/uninstall CLI (fake
launchctl, temporary home), and Skriv.app's LaunchAgent login-item fallback."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from skriv_client.__main__ import main
from skriv_client.service import EXIT_NOT_RUNNING
from skriv_client.service import macos as service
from skriv_server import config as server_config

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS-only")

PRINT_RUNNING = """gui/501/dev.skriv.server = {
\tactive count = 1
\tstate = running
\tpid = 4242
\tendpoints = {
\t\t"x" = {
\t\t\tstate = active
\t\t}
\t}
\tlast exit code = (never exited)
}
"""


class FakeLaunchctl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.loaded: set[str] = set()

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        rc, out = 0, ""
        if args[0] == "bootstrap":
            self.loaded.add(Path(args[2]).stem)
        elif args[0] in ("bootout", "print", "kickstart"):
            label = args[-1].rsplit("/", 1)[1]
            rc = 0 if label in self.loaded else 113
            if args[0] == "bootout":
                self.loaded.discard(label)
            elif args[0] == "print" and rc == 0:
                out = PRINT_RUNNING
        return subprocess.CompletedProcess(["launchctl", *args], rc, out, "")


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[service.Layout, FakeLaunchctl]:
    root, venv, home = tmp_path / "repo", tmp_path / "repo" / ".venv", tmp_path / "home"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "fetch-models").write_text("set -e\nDEFAULT=s1-mini-q4_k_m\nKEYS=()\n")
    (root / "scripts" / "llama-up").write_text("#!/bin/sh\n")
    (root / "models" / "bin").mkdir(parents=True)
    (root / "models" / "bin" / "llama-server").write_text("")
    (root / "models" / "s1-mini-q4_k_m.gguf").write_text("")
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "skriv-server").write_text("")
    lay = service.Layout(root=root, venv=venv, home=home)
    fake = FakeLaunchctl()
    monkeypatch.setattr(service, "layout", lambda: lay)
    monkeypatch.setattr(service, "launchctl", fake)
    monkeypatch.setattr(service, "_port_busy", (-1).__eq__)  # never busy
    return lay, fake


def read(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", plistlib.loads(path.read_bytes()))


def test_install_writes_kept_alive_agents_for_both_servers_and_loads_them(
    stack: tuple[service.Layout, FakeLaunchctl], capsys: pytest.CaptureFixture[str]
):
    lay, fake = stack
    assert main(["service", "install", "--backend", "mock"]) == 0
    home = lay.home
    llama = read(home / "Library/LaunchAgents/dev.skriv.llama.plist")
    server = read(home / "Library/LaunchAgents/dev.skriv.server.plist")
    assert llama["ProgramArguments"] == [str(lay.root / "scripts/llama-up"), "s1-mini-q4_k_m"]
    assert server["ProgramArguments"] == [
        str(lay.venv / "bin/skriv-server"), "--backend", "mock",
        "--cleanup-url", "http://127.0.0.1:8080/v1", "--cleanup-model", "s1-mini-q4_k_m",
    ]  # fmt: skip
    # the server accepts exactly these arguments
    cfg = server_config.parse_args(cast("list[str]", server["ProgramArguments"])[1:])
    assert (cfg.backend.kind, cfg.cleanup.model) == ("mock", "s1-mini-q4_k_m")
    for plist, log in ((llama, "llama.log"), (server, "server.log")):
        assert plist["KeepAlive"] is True
        assert plist["RunAtLoad"] is True
        assert plist["StandardOutPath"] == plist["StandardErrorPath"]
        assert plist["StandardErrorPath"] == str(home / "Library/Logs/skriv" / log)
        assert plist["WorkingDirectory"] == str(lay.root)
    uid = os.getuid()
    assert (
        "bootstrap",
        f"gui/{uid}",
        str(home / "Library/LaunchAgents/dev.skriv.llama.plist"),
    ) in fake.calls
    assert fake.loaded == {"dev.skriv.llama", "dev.skriv.server"}
    capsys.readouterr()

    assert main(["service", "status"]) == 0
    out = capsys.readouterr().out
    assert "dev.skriv.server" in out
    assert "running" in out
    assert "pid 4242" in out
    assert main(["service", "restart"]) == 0
    assert ("kickstart", "-k", f"gui/{uid}/dev.skriv.server") in fake.calls

    # reinstall without cleanup: the llama agent goes, the server loses its cleanup flags
    assert main(["service", "install", "--no-cleanup", "--backend", "mock"]) == 0
    assert not (home / "Library/LaunchAgents/dev.skriv.llama.plist").exists()
    assert read(home / "Library/LaunchAgents/dev.skriv.server.plist")["ProgramArguments"] == [
        str(lay.venv / "bin/skriv-server"), "--backend", "mock",
    ]  # fmt: skip
    assert fake.loaded == {"dev.skriv.server"}

    assert main(["service", "uninstall"]) == 0
    assert not list((home / "Library/LaunchAgents").iterdir())
    assert fake.loaded == set()
    assert main(["service", "status"]) == EXIT_NOT_RUNNING
    assert "no skriv services installed" in capsys.readouterr().out
    assert main(["service", "restart"]) == 1


def test_install_refuses_missing_models_and_warns_about_busy_ports(
    stack: tuple[service.Layout, FakeLaunchctl],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    lay, fake = stack
    assert main(["service", "install", "--cleanup-model", "qwen3-0.6b-q8_0"]) == 1
    assert "scripts/fetch-models qwen3-0.6b-q8_0" in capsys.readouterr().err
    assert not lay.agents_dir.exists()
    assert fake.calls == []

    monkeypatch.setattr(service, "_port_busy", (50051).__eq__)
    assert main(["service", "install", "--no-cleanup", "--backend", "mock"]) == 0
    assert "127.0.0.1:50051 is in use" in capsys.readouterr().out


def test_status_reports_an_installed_agent_launchd_does_not_know(
    stack: tuple[service.Layout, FakeLaunchctl], capsys: pytest.CaptureFixture[str]
):
    lay, _ = stack
    lay.agents_dir.mkdir(parents=True)
    lay.plist(service.SERVER).write_bytes(b"")
    assert main(["service", "status"]) == EXIT_NOT_RUNNING
    out = capsys.readouterr().out
    assert "not loaded" in out
    assert "llama" not in out  # only installed agents unless named


def test_the_client_is_not_a_launchd_service(
    stack: tuple[service.Layout, FakeLaunchctl], capsys: pytest.CaptureFixture[str]
):
    _, fake = stack
    assert main(["service", "install", "client"]) == 2
    assert "Skriv.app" in capsys.readouterr().err
    assert main(["service", "install", "--dry-run"]) == 2  # a Linux-only flag
    assert fake.calls == []


def test_login_item_falls_back_to_a_launch_agent_outside_an_app_bundle(tmp_path: Path):
    app = tmp_path / "Skriv.app"
    assert not service.login_item_enabled(tmp_path)
    service.set_login_item(tmp_path, app, enabled=True)
    assert service.login_item_enabled(tmp_path)
    plist = read(tmp_path / "Library/LaunchAgents/dev.skriv.app.plist")
    assert plist["ProgramArguments"] == ["/usr/bin/open", "-a", str(app)]
    assert plist["RunAtLoad"] is True
    service.set_login_item(tmp_path, app, enabled=False)
    assert not service.login_item_enabled(tmp_path)
    service.set_login_item(tmp_path, app, enabled=False)  # already off: no error
