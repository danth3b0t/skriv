"""Control socket: lets compositor key binds drive the daemon (`hark ctl start|stop|...`).

Protocol: the client connects, sends one command line (``start``, ``stop``, ``toggle``,
``cancel``, ``status``, ``ping``) and reads one reply line: ``ok[ <detail>]`` or
``error <message>``. Example without Python: ``echo toggle | nc -U "$sock"``.

Socket: $XDG_RUNTIME_DIR/hark/ctl.sock on Linux, ~/Library/Caches/hark/ctl.sock on
macOS, else $TMPDIR/hark-<uid>/ctl.sock. The directory is 0700, the socket 0600.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("hark.ctl")

COMMANDS = ("start", "stop", "toggle", "cancel", "status", "ping")
MAX_LINE = 64


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and sys.platform != "darwin":
        return Path(runtime) / "hark" / "ctl.sock"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "hark" / "ctl.sock"
    return Path(tempfile.gettempdir()) / f"hark-{os.getuid()}" / "ctl.sock"


class DaemonRunningError(RuntimeError):
    pass


class CtlServer:
    """Unix-socket server; `handler(command) -> reply` runs on a per-connection thread."""

    def __init__(self, path: Path, handler: Callable[[str], str]) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        if path.exists() or path.is_symlink():
            try:
                send("ping", path, timeout=0.5)
            except OSError:
                path.unlink()  # stale socket from a crashed daemon
            else:
                raise DaemonRunningError(f"another hark daemon is listening on {path}")

        class Handler(socketserver.StreamRequestHandler):
            @override
            def handle(self) -> None:
                line = self.rfile.readline(MAX_LINE + 1)
                if len(line) > MAX_LINE:
                    reply = "error command too long"
                else:
                    cmd = line.decode(errors="replace").strip()
                    if cmd not in COMMANDS:
                        reply = f"error unknown command {cmd!r} (use: {', '.join(COMMANDS)})"
                    else:
                        try:
                            reply = handler(cmd)
                        except Exception as e:
                            log.exception("ctl %s failed", cmd)
                            reply = f"error {type(e).__name__}: {e}"
                self.wfile.write(reply.encode() + b"\n")

        old_umask = os.umask(0o177)
        try:
            self._server = socketserver.ThreadingUnixStreamServer(str(path), Handler)
        finally:
            os.umask(old_umask)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True,
            name="hark-ctl",
        )  # fmt: skip

    def start(self) -> None:
        self._thread.start()
        log.info("control socket: %s", self.path)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()


def send(command: str, path: Path | None = None, timeout: float = 2.0) -> str:
    """Send one command; returns the reply line. Raises OSError if no daemon listens."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path or socket_path()))
        s.sendall(command.encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            part = s.recv(4096)
            if not part:
                break
            data += part
    return data.decode(errors="replace").strip()
