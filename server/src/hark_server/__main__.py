"""hark-server CLI: `uv run hark-server --config hark-server.toml [flags]`."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import TYPE_CHECKING

from .backends import BackendUnavailable
from .config import ConfigError, parse_args
from .server import start

if TYPE_CHECKING:
    from .config import Config


async def _serve(cfg: Config) -> None:
    running = await start(cfg)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    logging.getLogger("hark_server").info("shutting down")
    await running.stop(2.0)


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = parse_args(argv)
    except (ConfigError, OSError) as e:
        print(f"hark-server: config error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_serve(cfg))
    except BackendUnavailable as e:
        print(f"hark-server: backend unavailable: {e}", file=sys.stderr)
        return 3
    except ValueError as e:  # invalid backend kind/options
        print(f"hark-server: config error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
