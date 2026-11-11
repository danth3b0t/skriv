"""hark CLI: `run` (daemon), `ctl` (control the daemon), `once` (pipeline on a WAV), `doctor`,
`learned` (inspect/delete the learned vocabulary).

`hark ctl` is meant to be bound to compositor keys, so this module imports only the
stdlib at top level; everything else is imported by the subcommand that needs it.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hark_client.config import Config
    from hark_client.pipeline import Services


class Args(argparse.Namespace):
    # Annotations only: argparse fills in the values.
    command: str
    config: str | None
    server: str | None
    verbose: bool
    action: str
    wav: str
    dry_run: bool
    profile: str | None
    cwd: str | None
    realtime: bool
    no_history: bool
    json: bool
    learned_action: str
    text: str | None


def parse_args(argv: list[str] | None = None) -> Args:
    p = argparse.ArgumentParser(prog="hark", description="context-aware dictation (POC client)")
    p.add_argument("--config", help="config TOML (default: ~/.config/hark/config.toml)")
    p.add_argument("--server", help="override server.address (host:port or unix:/path)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="daemon: mic + hotkey / control socket -> paste")
    c = sub.add_parser("ctl", help="control a running daemon (bind these to keys)")
    c.add_argument("action", choices=["start", "stop", "toggle", "cancel", "status", "ping"])
    c.add_argument(
        "--json", action="store_true", help="JSON v1 request/reply (docs/protocol/ctl-v1.md)"
    )
    o = sub.add_parser("once", help="run the whole pipeline on a WAV file and report each stage")
    o.add_argument(
        "--wav", required=True, help="WAV file (any rate/channels; resampled to 16 kHz mono)"
    )
    o.add_argument("--dry-run", action="store_true", help="print instead of pasting")
    o.add_argument("--profile", help="force a profile (e.g. terminal, chat, prose)")
    o.add_argument(
        "--realtime",
        action="store_true",
        help="stream at real-time pace (default: as fast as possible)",
    )
    o.add_argument("--no-history", action="store_true", help="do not append to history")
    o.add_argument(
        "--cwd", help="pretend the focused terminal is in this directory (repo/agent context)"
    )
    sub.add_parser("doctor", help="check server, mic, permissions, paste tools")
    lp = sub.add_parser("learned", help="corrections learned from your edits (learn.enabled)")
    lp.add_argument(
        "learned_action", nargs="?", default="list", choices=["list", "forget", "clear"]
    )
    lp.add_argument("text", nargs="?", help="forget: the heard or written form to delete")
    return p.parse_args(argv, namespace=Args())


def cmd_ctl(args: Args) -> int:
    import json  # noqa: PLC0415

    from hark_client import ctl  # noqa: PLC0415

    try:
        if args.json:
            obj = ctl.send_json(args.action)
            print(json.dumps(obj))
            return 0 if obj.get("ok") is True else 1
        reply = ctl.send(args.action)
    except (ValueError, TypeError) as e:
        print(f"hark ctl: bad reply ({e})", file=sys.stderr)
        return 1
    except OSError as e:
        print(
            f"hark ctl: no daemon on {ctl.socket_path()} ({e}); start one with `hark run`",
            file=sys.stderr,
        )
        return 2
    print(reply)
    return 0 if reply.startswith("ok") else 1


def cmd_once(args: Args, services: Services) -> int:
    from hark_client.asr import CHUNK_SAMPLES, SAMPLE_RATE  # noqa: PLC0415
    from hark_client.audio import read_wav  # noqa: PLC0415
    from hark_client.pipeline import Utterance  # noqa: PLC0415

    if args.profile:
        services.cfg.profile(args.profile)  # raises ConfigError on unknown names
    if args.cwd:
        os.environ["HARK_CONTEXT_CWD"] = str(Path(args.cwd).resolve())
    pcm = read_wav(Path(args.wav))
    print(f"{'wav':<9} {args.wav}: {len(pcm) // 2 / SAMPLE_RATE:.2f} s at 16 kHz mono")
    utt = Utterance(services, profile=args.profile)
    step = CHUNK_SAMPLES * 2
    t0 = time.perf_counter()
    for i in range(0, len(pcm), step):
        if args.realtime:
            time.sleep(max(0.0, t0 + (i + step) / 2 / SAMPLE_RATE - time.perf_counter()))
        utt.feed(pcm[i : i + step])
    res = utt.finish()
    print(f"{'result':<9} {res.status}{': ' + res.error if res.error else ''}")
    return 0 if res.status in ("pasted", "focus_changed") else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "ctl":
        return cmd_ctl(args)

    from hark_client import config  # noqa: PLC0415

    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        print(f"hark: config error: {e}", file=sys.stderr)
        return 2
    if args.server:
        cfg.server.address = args.server
    logging.basicConfig(
        level=logging.DEBUG
        if args.verbose
        else getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config_path = args.config or str(config.default_config_path())

    if args.command == "doctor":
        from hark_client import doctor  # noqa: PLC0415

        return doctor.run(
            cfg, config_path if Path(config_path).exists() else f"{config_path} (absent; defaults)"
        )
    if args.command == "learned":
        return cmd_learned(args, cfg)

    return cmd_pipeline(args, cfg)


def cmd_learned(args: Args, cfg: Config) -> int:
    from hark_client.config import expand  # noqa: PLC0415
    from hark_client.learn import LearnedStore, store_path  # noqa: PLC0415

    store = LearnedStore(store_path(expand(cfg.history.dir)), cfg.learn.promote_after)
    if args.learned_action == "forget":
        if not args.text:
            print("hark learned forget: give the heard or written form", file=sys.stderr)
            return 2
        n = store.forget(args.text)
        print(f"forgot {n} entr{'y' if n == 1 else 'ies'}")
        return 0 if n else 1
    if args.learned_action == "clear":
        n = store.clear()
        print(f"cleared {n} entr{'y' if n == 1 else 'ies'} from {store.path}")
        return 0
    entries = store.entries()
    state = "on" if cfg.learn.enabled else "off (learn.enabled = false)"
    print(f"{store.path}: {len(entries)} entries; learning {state}")
    for e in entries:
        mark = "*" if e.count >= store.promote_after else " "
        print(f"{mark} {e.count:>3}  {e.heard!r} -> {e.written!r}  (last {e.last})")
    if entries:
        print(f"* = used for recognition (seen >= {store.promote_after} times)")
    return 0


def cmd_pipeline(args: Args, cfg: Config) -> int:
    from hark_client import asr, ctl, daemon, fields  # noqa: PLC0415
    from hark_client.config import ConfigError, FeedbackConfig, expand  # noqa: PLC0415
    from hark_client.feedback import Feedback  # noqa: PLC0415
    from hark_client.inject import make_injector  # noqa: PLC0415
    from hark_client.learn import LearnedStore, Learner, store_path  # noqa: PLC0415
    from hark_client.pipeline import Services  # noqa: PLC0415

    channel = asr.open_channel(cfg.server)
    caps = asr.Capabilities(channel)
    learned = LearnedStore(
        store_path(expand(cfg.history.dir)), cfg.learn.promote_after, cfg.learn.max_terms
    )
    try:
        if args.command == "once":
            if args.no_history:
                cfg.history.enabled = False
            caps.refresh()
            svc = Services(
                cfg, channel, caps,
                injector=make_injector(cfg.paste, dry_run=args.dry_run),
                feedback=Feedback(FeedbackConfig(sounds=False, notify=False)),
                out=print,
                learned=learned,
            )  # fmt: skip
            try:
                return cmd_once(args, svc)
            except ConfigError as e:
                print(f"hark: {e}", file=sys.stderr)
                return 2
        feedback = Feedback(cfg.feedback)
        reader = fields.make_reader() if cfg.paste.verify or cfg.learn.enabled else None
        learner = None
        if cfg.learn.enabled:
            learner = Learner(
                learned, reader,
                window_s=cfg.learn.window_ms / 1000, poll_s=cfg.learn.poll_ms / 1000,
                on_promoted=lambda e: feedback.notify(f"Learned: {e.heard!r} -> {e.written!r}"),
            )  # fmt: skip
            if reader is None:
                logging.getLogger("hark").warning(
                    "learn.enabled, but this platform offers no field read-back (see `hark doctor`);"
                    " learning only from key replay in terminals"
                )
        svc = Services(
            cfg, channel, caps, injector=make_injector(cfg.paste), feedback=feedback,
            field_reader=reader, learned=learned, learner=learner,
        )  # fmt: skip
        try:
            return daemon.run(svc)
        except ctl.DaemonRunningError as e:
            print(f"hark: {e}", file=sys.stderr)
            return 1
    finally:
        channel.close()


if __name__ == "__main__":
    raise SystemExit(main())
