"""Control socket: lets compositor key binds and status UIs drive the daemon.

Two protocols on the same socket, one request line and one reply line per connection:

- Text (key binds): ``start``, ``stop``, ``toggle``, ``cancel``, ``status``, ``ping`` ->
  ``ok[ <detail>]`` or ``error <message>``. Example: ``echo toggle | nc -U "$sock"``.
- JSON v1 (UIs, scripts), any line starting with ``{``: ``{"version": 1, "command": "status"}`` ->
  ``{"version": 1, "ok": true, "command": "status", ...}``; errors are
  ``{"version": 1, "ok": false, "error": {"code": ..., "message": ...}}``.
  Spec: docs/protocol/ctl-v1.md (pinned by client/tests/test_client_ctl.py).

Socket: $XDG_RUNTIME_DIR/hark/ctl.sock on Linux, ~/Library/Caches/hark/ctl.sock on
macOS, else $TMPDIR/hark-<uid>/ctl.sock. The directory is 0700, the socket 0600.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("hark.ctl")

COMMANDS = ("start", "stop", "toggle", "cancel", "status", "ping")
MAX_LINE = 512
PROTOCOL_VERSION = 1

type JsonObject = dict[str, object]


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and sys.platform != "darwin":
        return Path(runtime) / "hark" / "ctl.sock"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "hark" / "ctl.sock"
    return Path(tempfile.gettempdir()) / f"hark-{os.getuid()}" / "ctl.sock"


class DaemonRunningError(RuntimeError):
    pass


def error_reply(code: str, message: str) -> JsonObject:
    return {"version": PROTOCOL_VERSION, "ok": False, "error": {"code": code, "message": message}}


def text_to_json(command: str, reply: str) -> JsonObject:
    """A text-protocol reply (``ok <detail>`` / ``error <message>``) as a JSON v1 reply."""
    if reply == "ok" or reply.startswith("ok "):
        return {"version": PROTOCOL_VERSION, "ok": True, "command": command, "result": reply[3:]}
    return error_reply("failed", reply.removeprefix("error").strip())


def _json_request(
    line: bytes, json_handler: Callable[[str], JsonObject], handler: Callable[[str], str]
) -> JsonObject:
    try:
        req = cast("object", json.loads(line))
    except ValueError as e:
        return error_reply("bad_json", str(e))
    if not isinstance(req, dict):  # unreachable for a line starting with "{"; narrows the type
        return error_reply("bad_json", "expected a JSON object")
    obj = cast("JsonObject", req)
    version = obj.get("version")
    if version != PROTOCOL_VERSION:
        return error_reply("unsupported_version", f"version {version!r}; this daemon speaks 1")
    command = obj.get("command")
    if not isinstance(command, str) or command not in COMMANDS:
        return error_reply("unknown_command", f"command {command!r} (use: {', '.join(COMMANDS)})")
    try:
        return json_handler(command) if command == "status" else text_to_json(
            command, handler(command)
        )  # fmt: skip
    except Exception as e:
        log.exception("ctl %s failed", command)
        return error_reply("internal", f"{type(e).__name__}: {e}")


class CtlServer:
    """Unix-socket server; handlers run on a per-connection thread.

    ``handler(command) -> "ok ..."`` serves both protocols; ``status_json() -> dict`` answers a
    JSON ``status`` (default: the text reply wrapped by :func:`text_to_json`).
    """

    def __init__(
        self,
        path: Path,
        handler: Callable[[str], str],
        status_json: Callable[[], JsonObject] | None = None,
    ) -> None:
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

        def json_handler(command: str) -> JsonObject:
            if status_json is not None:
                return {
                    "version": PROTOCOL_VERSION,
                    "ok": True,
                    "command": command,
                    **status_json(),
                }
            return text_to_json(command, handler(command))

        class Handler(socketserver.StreamRequestHandler):
            @override
            def handle(self) -> None:
                line = self.rfile.readline(MAX_LINE + 1).strip()
                if line.startswith(b"{"):
                    obj = (
                        error_reply("too_long", f"request over {MAX_LINE} bytes")
                        if len(line) > MAX_LINE
                        else _json_request(line, json_handler, handler)
                    )
                    self.wfile.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
                    return
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
    """Send one request line; returns the reply line. Raises OSError if no daemon listens."""
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


def send_json(command: str, path: Path | None = None, timeout: float = 2.0) -> JsonObject:
    """JSON v1 request; returns the decoded reply object. Raises OSError, ValueError, TypeError."""
    reply = send(json.dumps({"version": PROTOCOL_VERSION, "command": command}), path, timeout)
    obj = cast("object", json.loads(reply))
    if not isinstance(obj, dict):
        raise TypeError(f"not a JSON object: {reply[:80]!r}")
    return cast("JsonObject", obj)
