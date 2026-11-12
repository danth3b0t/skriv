"""systemd service notifications (sd_notify(3)), stdlib only.

Under a ``Type=notify`` unit, ``READY=1`` is sent once the model is warm and health says SERVING,
and ``WATCHDOG=1`` every half ``WatchdogSec`` while the event loop runs, so systemd restarts a
hung server. Outside systemd (no ``$NOTIFY_SOCKET``) everything is a no-op.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

log = logging.getLogger("hark_server.sdnotify")


def notify(state: str) -> bool:
    """Send ``state`` (e.g. ``"READY=1"``) to the service manager; False if there is none."""
    addr = os.environ.get("NOTIFY_SOCKET", "")
    if addr.startswith("@"):  # abstract namespace
        addr = "\0" + addr[1:]
    elif not addr.startswith("/"):
        return False  # unset, or a transport we do not speak (vsock)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode())
    except OSError as e:
        log.warning("sd_notify %r failed: %s", state.split("=", 1)[0], e)
        return False
    return True


def watchdog_interval() -> float | None:
    """Seconds between ``WATCHDOG=1`` pings (half of ``WatchdogSec``), None if not enabled."""
    pid = os.environ.get("WATCHDOG_PID", "")
    if pid and pid != str(os.getpid()):
        return None
    try:
        usec = int(os.environ.get("WATCHDOG_USEC", ""))
    except ValueError:
        return None
    return usec / 2e6 if usec > 0 else None


async def watchdog(interval: float) -> None:
    """Ping the watchdog from the event loop until cancelled: a blocked loop stops the pings."""
    while True:
        notify("WATCHDOG=1")
        await asyncio.sleep(interval)
