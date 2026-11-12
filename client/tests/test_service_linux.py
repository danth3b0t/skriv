"""`hark service` on Linux: unit generation, install/uninstall/status/restart against a fake systemctl,
and the daemon's sd_notify watchdog."""

from __future__ import annotations

import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from skriv_client import service
from skriv_client.service import linux

if TYPE_CHECKING:
    from collections.abc import Sequence

PACKAGING = Path(__file__).parents[2] / "packaging" / "systemd"
DEV = linux.Commands(
    skriv=("/home/u/my repo/.venv/bin/skriv",),
    server=("/home/u/my repo/.venv/bin/skriv-server",),
    llama_up=("/home/u/100%/scripts/llama-up",),
)
SESSION = {
    "HOME": "/home/u",
    "WAYLAND_DISPLAY": "wayland-1",
    "HYPRLAND_INSTANCE_SIGNATURE": "abc_123",
    "XDG_CURRENT_DESKTOP": "Hyprland",
    "SSH_AUTH_SOCK": "/run/user/1000/ssh",  # not a session variable: never exported
}


class FakeSystemctl:
    """Records argv; answers `is-active`, `loginctl` and `show` like systemd would."""

    def __init__(self, *, session: str = "active", fail: str = "", show: str = "") -> None:
        self.calls: list[list[str]] = []
        self.session, self.fail, self.show = session, fail, show

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        out, code, err = "", 0, ""
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            out = self.session + "\n"
        elif argv[0] == "loginctl":
            out = "no\n"
        elif argv[2:3] == ["show"]:
            out = self.show
        if self.fail and argv[2:3] == [self.fail]:
            code, err = 1, "Failed to connect to bus"
        return subprocess.CompletedProcess(argv, code, out, err)

    def systemctl(self) -> list[list[str]]:
        return [c[2:] for c in self.calls if c[0] == "systemctl" and c[2] != "is-active"]


def make(
    tmp_path: Path,
    fake: FakeSystemctl,
    *,
    packaged: bool = False,
    env: dict[str, str] | None = None,
) -> linux.SystemdBackend:
    return linux.SystemdBackend(
        run=fake,
        env={**(SESSION if env is None else env), "XDG_CONFIG_HOME": str(tmp_path / "config")},
        commands=linux.PACKAGED if packaged else DEV,
        packaged=packaged,
        vendor_dir=tmp_path / "vendor",
    )


def exec_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("Exec")]


def test_packaged_units_are_the_rendered_ones():
    for component, unit in linux.UNITS.items():
        assert (PACKAGING / unit).read_text() == linux.render(component, linux.PACKAGED), (
            f"stale {unit}: python -m skriv_client.service.linux packaging/systemd"
        )


def test_unit_commands_are_quoted_for_systemd():
    assert exec_lines(linux.render("client", DEV)) == [
        """ExecCondition=/bin/sh -c 'test -n "$${WAYLAND_DISPLAY}$${DISPLAY}"'""",
        'ExecStart="/home/u/my repo/.venv/bin/skriv" run',
    ]
    assert exec_lines(linux.render("server", DEV)) == [
        'ExecStart="/home/u/my repo/.venv/bin/skriv-server" --config %E/hark/server.toml'
    ]
    assert exec_lines(linux.render("llama", DEV))[0] == "ExecStart=/home/u/100%%/scripts/llama-up"
    assert linux.quote('a"b\\c $X') == '"a\\"b\\\\c $$X"'


def test_server_and_llama_need_their_programs():
    missing = linux.Commands(skriv=DEV.skriv, server=None, llama_up=None)
    with pytest.raises(linux.ServiceError, match="skriv-server"):
        linux.render("server", missing)
    with pytest.raises(linux.ServiceError, match="llama-up"):
        linux.render("llama", missing)


def test_install_writes_units_imports_the_session_and_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    fake = FakeSystemctl(session="inactive")
    assert (
        make(tmp_path, fake).install(
            ["client"], service.InstallOptions(start=True, force=False, dry_run=False)
        )
        == 0
    )
    unit = tmp_path / "config" / "systemd" / "user" / "hark.service"
    assert unit.read_text() == linux.render("client", DEV)
    assert unit.stat().st_mode & 0o777 == 0o644
    assert fake.systemctl() == [
        ["import-environment", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE",
         "XDG_CURRENT_DESKTOP"],
        ["daemon-reload"],
        ["enable", "--now", "hark.service"],
    ]  # fmt: skip
    out = capsys.readouterr().out
    assert "exec-once = systemctl --user import-environment WAYLAND_DISPLAY" in out


def test_install_outside_a_session_imports_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    fake = FakeSystemctl()
    backend = make(tmp_path, fake, env={"HOME": "/home/u"})
    assert (
        backend.install(["client"], service.InstallOptions(start=False, force=False, dry_run=False))
        == 0
    )
    assert fake.systemctl() == [["daemon-reload"], ["enable", "hark.service"]]
    assert "no WAYLAND_DISPLAY/DISPLAY" in capsys.readouterr().err


def test_install_keeps_a_unit_it_did_not_write_unless_forced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    unit = tmp_path / "config" / "systemd" / "user" / "hark.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/opt/my-hark run\n")
    fake = FakeSystemctl()
    assert (
        make(tmp_path, fake).install(
            ["client"], service.InstallOptions(start=True, force=False, dry_run=False)
        )
        == 1
    )
    assert "not written by hark" in capsys.readouterr().err
    assert unit.read_text() == "[Service]\nExecStart=/opt/my-hark run\n"
    assert fake.calls == []
    assert (
        make(tmp_path, fake).install(
            ["client"], service.InstallOptions(start=True, force=True, dry_run=False)
        )
        == 0
    )
    assert unit.read_text() == linux.render("client", DEV)


def test_packaged_install_enables_the_package_units(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    fake = FakeSystemctl()
    backend = make(tmp_path, fake, packaged=True)
    assert (
        backend.install(
            ["server", "llama"], service.InstallOptions(start=True, force=False, dry_run=False)
        )
        == 1
    )
    assert "reinstall hark-git" in capsys.readouterr().err  # the package's unit is missing
    (tmp_path / "vendor").mkdir()
    for unit in ("hark-server.service", "hark-llama.service"):
        (tmp_path / "vendor" / unit).write_text("")
    assert (
        backend.install(
            ["server", "llama"], service.InstallOptions(start=True, force=False, dry_run=False)
        )
        == 0
    )
    assert not (tmp_path / "config" / "systemd").exists()
    assert fake.systemctl() == [
        ["daemon-reload"],
        ["enable", "--now", "hark-server.service", "hark-llama.service"],
    ]
    out = capsys.readouterr().out
    assert "server.toml, which does not exist" in out
    assert "loginctl enable-linger" in out


def test_dry_run_prints_and_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fake = FakeSystemctl()
    assert (
        make(tmp_path, fake).install(
            ["client"], service.InstallOptions(start=True, force=False, dry_run=True)
        )
        == 0
    )
    assert fake.calls == []
    assert not (tmp_path / "config").exists()
    out = capsys.readouterr().out
    assert linux.render("client", DEV) in out
    assert "systemctl --user enable --now hark.service" in out


def test_systemctl_failure_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fake = FakeSystemctl(fail="enable")
    assert (
        make(tmp_path, fake).install(
            ["client"], service.InstallOptions(start=True, force=False, dry_run=False)
        )
        == 1
    )
    assert "Failed to connect to bus" in capsys.readouterr().err


def test_uninstall_removes_only_generated_units(tmp_path: Path):
    fake = FakeSystemctl()
    backend = make(tmp_path, fake)
    backend.install(
        ["client", "server"], service.InstallOptions(start=True, force=False, dry_run=False)
    )
    user_units = tmp_path / "config" / "systemd" / "user"
    (user_units / "hark-server.service").write_text("[Service]\nExecStart=/opt/mine\n")
    fake.calls.clear()
    assert backend.uninstall(["client", "server"]) == 0
    assert not (user_units / "hark.service").exists()
    assert (user_units / "hark-server.service").exists()
    assert fake.systemctl() == [
        ["disable", "--now", "hark.service", "hark-server.service"],
        ["daemon-reload"],
    ]


SHOW = """\
LoadState=loaded
ActiveState=active
SubState=running
UnitFileState=enabled
NRestarts=2
FragmentPath=/home/u/.config/systemd/user/hark.service

LoadState=not-found
ActiveState=inactive
SubState=dead
UnitFileState=
NRestarts=0
FragmentPath=
"""


def test_status_reads_systemd_and_exits_3_unless_all_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    fake = FakeSystemctl(show=SHOW)
    backend = make(tmp_path, fake)
    monkeypatch.setattr(service, "backend", lambda: backend)
    code = service.main("status", ["client", "llama"], no_start=False, force=False, dry_run=False)
    assert code == service.EXIT_NOT_RUNNING
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "client", "hark.service", "running,", "enabled", "(running,", "2", "restarts,",
        "/home/u/.config/systemd/user/hark.service)",
    ]  # fmt: skip
    assert lines[1].split() == ["llama", "hark-llama.service", "not", "installed"]
    show = fake.systemctl()[0]
    assert show[0] == "show"
    assert show[2:] == ["hark.service", "hark-llama.service"]
    fake.show = SHOW.split("\n\n", maxsplit=1)[0]
    assert service.main("status", ["client"], no_start=False, force=False, dry_run=False) == 0


def test_restart_and_default_status_cover_only_installed_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    client, missing = SHOW.split("\n\n", maxsplit=1)
    fake = FakeSystemctl(show=f"{client}\n\n{missing}\n{missing}")  # client, server, llama
    monkeypatch.setattr(service, "backend", lambda: make(tmp_path, fake))
    assert service.main("restart", []) == 0
    assert fake.systemctl()[-1] == ["restart", "hark.service"]
    assert service.main("status", []) == 0  # server and llama are not installed: not counted
    assert capsys.readouterr().out.splitlines()[-1].split()[:2] == ["client", "hark.service"]
    fake.show = f"{missing}\n{missing}\n{missing}"
    assert service.main("restart", []) == 1
    assert "no hark services installed" in capsys.readouterr().out
    fake.calls.clear()
    assert service.main("install", [], asr_backend="mock") == 2  # a macOS-only flag
    assert fake.calls == []


def test_unknown_component_is_a_usage_error(capsys: pytest.CaptureFixture[str]):
    assert service.main("install", ["gpu"], no_start=False, force=False, dry_run=False) == 2
    assert "unknown component 'gpu'" in capsys.readouterr().err


def test_watchdog_is_fed_only_while_healthy(monkeypatch: pytest.MonkeyPatch):
    with tempfile.TemporaryDirectory(dir="/tmp") as d:  # short AF_UNIX path
        path = str(Path(d, "notify"))
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(path)
        sock.settimeout(0.5)
        monkeypatch.setenv("NOTIFY_SOCKET", path)
        monkeypatch.setenv("WATCHDOG_USEC", "40000")  # every 20 ms
        monkeypatch.delenv("WATCHDOG_PID", raising=False)
        healthy = [True]

        def check() -> bool:
            if not healthy[0]:
                raise OSError("no daemon")
            return True

        stop = threading.Event()
        assert linux.notify("READY=1")
        thread = linux.start_watchdog(check, stop)
        assert thread is not None
        assert [sock.recv(64) for _ in range(3)] == [b"READY=1", b"WATCHDOG=1", b"WATCHDOG=1"]
        healthy[0] = False
        time.sleep(0.05)  # let an in-flight ping land
        sock.setblocking(False)
        while True:
            try:
                sock.recv(64)
            except BlockingIOError:
                break
        sock.settimeout(0.2)
        with pytest.raises(TimeoutError):
            sock.recv(64)
        stop.set()
        thread.join(1)
        assert not thread.is_alive()
        sock.close()


def test_no_watchdog_without_watchdog_sec(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert linux.start_watchdog(lambda: True, threading.Event()) is None
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert linux.notify("READY=1") is False
