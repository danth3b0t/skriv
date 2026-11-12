"""hark CLI: `run` (daemon), `app` (the daemon with a macOS menu-bar item; Hark.app runs it),
`ctl` (control the daemon), `once` (pipeline on a WAV), `retry`
(pipeline on a kept recording), `history`, `last`, `doctor`, `learned` (inspect/delete the
learned vocabulary), `service` (macOS launchd agents for the servers).

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
    import threading
    from collections.abc import Callable

    from hark_client.config import Config
    from hark_client.context import ContextSnapshot
    from hark_client.daemon import AppUi, Controller
    from hark_client.pipeline import Services
    from hark_client.service import Layout


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
    id: str | None
    copy: bool
    history_action: str
    n: int
    service_action: str
    backend: str | None
    cleanup_model: str | None
    no_cleanup: bool
    no_onboarding: bool


def parse_args(argv: list[str] | None = None) -> Args:
    p = argparse.ArgumentParser(prog="hark", description="context-aware dictation (POC client)")
    p.add_argument("--config", help="config TOML (default: ~/.config/hark/config.toml)")
    p.add_argument("--server", help="override server.address (host:port or unix:/path)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="daemon: mic + hotkey / control socket -> paste")
    ap = sub.add_parser("app", help="macOS: `run` plus a menu-bar item (what Hark.app runs)")
    ap.add_argument(
        "--no-onboarding", action="store_true", help="no setup window, even if permissions miss"
    )
    c = sub.add_parser("ctl", help="control a running daemon (bind these to keys)")
    c.add_argument(
        "action",
        choices=["start", "stop", "toggle", "cancel", "status", "ping", "paste-last"],
        help="paste-last: paste the last dictated text again into the focused window",
    )
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
    r = sub.add_parser(
        "retry", help="re-run the pipeline on a kept recording (after a server error or timeout)"
    )
    r.add_argument(
        "id", nargs="?", default="last", help="last (default) or an id from `hark history`"
    )
    r.add_argument("--dry-run", action="store_true", help="print instead of pasting")
    r.add_argument("--copy", action="store_true", help="copy the result instead of pasting it")
    h = sub.add_parser("history", help="recent dictations; `history show ID` for every stage")
    h.add_argument("history_action", nargs="?", default="list", choices=["list", "show"])
    h.add_argument("id", nargs="?", help="show: an id (or its last 8 characters)")
    h.add_argument("-n", type=int, default=20, help="list: how many (default 20)")
    h.add_argument("--json", action="store_true", help="list: the full records as a JSON array")
    la = sub.add_parser("last", help="print the last dictated text")
    la.add_argument("--copy", action="store_true", help="copy it to the clipboard instead")
    sub.add_parser("doctor", help="check server, mic, permissions, paste tools")
    lp = sub.add_parser("learned", help="corrections learned from your edits (learn.enabled)")
    lp.add_argument(
        "learned_action", nargs="?", default="list", choices=["list", "forget", "clear"]
    )
    lp.add_argument("text", nargs="?", help="forget: the heard or written form to delete")
    sv = sub.add_parser(
        "service", help="macOS: llama-server + hark-server as launchd agents (start at login)"
    )
    sv.add_argument("service_action", choices=["install", "uninstall", "status", "restart"])
    sv.add_argument(
        "--backend", help="install: ASR backend (default parakeet_mlx on Apple Silicon)"
    )
    sv.add_argument(
        "--cleanup-model", help="install: a scripts/fetch-models key (default: its DEFAULT)"
    )
    sv.add_argument("--no-cleanup", action="store_true", help="install: no llama-server")
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


def cmd_service(args: Args) -> int:
    from hark_client import service  # noqa: PLC0415

    if sys.platform != "darwin":
        print("hark service: macOS only (Linux: packaging/systemd/hark.service)", file=sys.stderr)
        return 2
    try:
        lay = service.layout()
        if args.service_action == "install":
            model = None if args.no_cleanup else (
                args.cleanup_model or service.default_cleanup_model(lay.root)
            )  # fmt: skip
            backend = args.backend or service.default_backend()
            service.install(lay, backend=backend, cleanup_model=model)
            print("check with `hark service status` and `hark doctor`")
            return 0
        if args.service_action == "uninstall":
            if not service.uninstall(lay):
                print("no hark services installed")
            return 0
        if args.service_action == "restart":
            labels = service.restart(lay)
            print(f"restarted {', '.join(labels)}" if labels else "no hark services installed")
            return 0 if labels else 1
    except service.ServiceError as e:
        print(f"hark service: {e}", file=sys.stderr)
        return 1
    return _service_status(lay)


def _service_status(lay: Layout) -> int:
    from hark_client import service  # noqa: PLC0415

    states = service.status(lay)
    if not states:
        print("no hark services installed (`hark service install`)")
        return 1
    for st in states:
        detail = f"pid {st.pid}" if st.pid else f"last exit {st.last_exit or '-'}"
        log = lay.logs_dir / ("llama.log" if st.label == service.LLAMA else "server.log")
        print(f"{st.label:<16} {st.state:<12} {detail:<14} {log}")
    return 0 if all(st.state == "running" for st in states) else 1


def run_pcm(
    services: Services,
    pcm: bytes,
    *,
    profile: str | None = None,
    realtime: bool = False,
    replay: ContextSnapshot | None = None,
) -> int:
    """Stream 16 kHz PCM16 through one Utterance (as fast as possible unless ``realtime``)."""
    from hark_client.asr import CHUNK_SAMPLES, SAMPLE_RATE  # noqa: PLC0415
    from hark_client.pipeline import Utterance  # noqa: PLC0415

    utt = Utterance(services, profile=profile, replay=replay)
    step = CHUNK_SAMPLES * 2
    t0 = time.perf_counter()
    for i in range(0, len(pcm), step):
        if realtime:
            time.sleep(max(0.0, t0 + (i + step) / 2 / SAMPLE_RATE - time.perf_counter()))
        utt.feed(pcm[i : i + step])
    res = utt.finish()
    print(f"{'result':<9} {res.status}{': ' + res.error if res.error else ''}")
    return 0 if res.status in ("pasted", "focus_changed") else 1


def cmd_once(args: Args, services: Services) -> int:
    from hark_client.asr import SAMPLE_RATE  # noqa: PLC0415
    from hark_client.audio import read_wav  # noqa: PLC0415

    if args.profile:
        services.cfg.profile(args.profile)  # raises ConfigError on unknown names
    if args.cwd:
        os.environ["HARK_CONTEXT_CWD"] = str(Path(args.cwd).resolve())
    pcm = read_wav(Path(args.wav))
    print(f"{'wav':<9} {args.wav}: {len(pcm) // 2 / SAMPLE_RATE:.2f} s at 16 kHz mono")
    return run_pcm(services, pcm, profile=args.profile, realtime=args.realtime)


def cmd_retry(args: Args, services: Services) -> int:
    """Re-run a kept recording with the context captured at its key-down: biased like the
    original and pasted only if that window is focused again (else copied)."""
    from hark_client import retry  # noqa: PLC0415
    from hark_client.audio import read_wav  # noqa: PLC0415
    from hark_client.config import expand  # noqa: PLC0415
    from hark_client.inject import ClipboardOnlyInjector  # noqa: PLC0415

    try:
        entry = retry.find(expand(services.cfg.history.dir), args.id or "last")
    except LookupError as e:
        print(f"hark retry: {e}", file=sys.stderr)
        return 1
    snap = retry.load_snapshot(entry)
    meta = entry.meta
    error = f": {meta['error']}" if meta.get("error") else ""
    app = snap.focus.app or snap.focus.app_id or "?"
    print(f"{'retry':<9} {entry.id} (was {meta.get('status', '?')}{error}; app {app!r})")
    if services.injector.name not in ("dry-run", "clipboard") and not (
        snap.focus.window_id and services.cfg.paste.check_focus
    ):
        # cannot tell whether the original window is focused: never paste into this one
        services.injector = ClipboardOnlyInjector(services.injector)
        print(f"{'retry':<9} original window unknown: the result is copied, not pasted")
    return run_pcm(services, read_wav(entry.audio), replay=snap)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "ctl":
        return cmd_ctl(args)
    if args.command == "service":
        return cmd_service(args)
    if args.command == "app" and sys.platform != "darwin":
        print("hark app: macOS only; use `hark run`", file=sys.stderr)
        return 2

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
    offline = {"learned": cmd_learned, "history": cmd_history, "last": cmd_last}
    return offline.get(args.command, cmd_pipeline)(args, cfg)


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


def cmd_history(args: Args, cfg: Config) -> int:
    import json  # noqa: PLC0415

    from hark_client import history, historyview, retry  # noqa: PLC0415
    from hark_client.config import expand  # noqa: PLC0415

    base = expand(cfg.history.dir)
    kept = {e.id for e in retry.entries(base)}
    if args.history_action == "show":
        if not args.id:
            print("hark history show: give an id (see `hark history`)", file=sys.stderr)
            return 2
        found = history.find(base, args.id)
        ids = {str(r.get("utterance_id")) for r in found}
        if len(ids) != 1:
            what = "no dictation" if not ids else f"{len(ids)} dictations"
            print(f"hark history show: {what} match {args.id!r}", file=sys.stderr)
            return 1
        uid = ids.pop()
        print(historyview.show(found[-1], kept=uid in kept))
        return 0
    if args.n < 1:
        print("hark history: -n must be >= 1", file=sys.stderr)
        return 2
    records = history.recent(base, args.n)
    if args.json:
        print(json.dumps(records, ensure_ascii=False))
    elif not records:
        print(f"no history in {base}")
    else:
        print(historyview.table(records, kept))
    return 0


def cmd_last(args: Args, cfg: Config) -> int:
    from hark_client import history  # noqa: PLC0415
    from hark_client.config import expand  # noqa: PLC0415

    text = history.last_text(expand(cfg.history.dir))
    if not text:
        print("hark last: no dictated text in the history", file=sys.stderr)
        return 1
    if not args.copy:
        print(text)
        return 0
    from hark_client.inject import make_injector  # noqa: PLC0415

    if not make_injector(cfg.paste).copy(text):
        print("hark last: could not write the clipboard", file=sys.stderr)
        return 1
    print(f"copied {len(text)} characters")
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
        if args.command in ("once", "retry"):
            if args.command == "once":
                cfg.retry.keep = 0  # nothing kept for `hark retry`: the WAV is the recording
                if args.no_history:
                    cfg.history.enabled = False
            elif args.copy:
                cfg.paste.method = "clipboard"
            caps.refresh()
            svc = Services(
                cfg, channel, caps,
                injector=make_injector(cfg.paste, dry_run=args.dry_run),
                feedback=Feedback(FeedbackConfig(sounds=False, notify=False)),
                out=print,
                learned=learned,
            )  # fmt: skip
            try:
                return cmd_once(args, svc) if args.command == "once" else cmd_retry(args, svc)
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
            return daemon.run(svc, _menu_bar(args) if args.command == "app" else None)
        except ctl.DaemonRunningError as e:
            print(f"hark: {e}", file=sys.stderr)
            return 1
    finally:
        channel.close()


def _menu_bar(args: Args) -> Callable[[Controller, threading.Event], AppUi]:
    """`hark app`: builds the menu-bar item on the main thread once the daemon is up."""
    from hark_client.config import default_config_path  # noqa: PLC0415

    cli = [*(["--config", args.config] if args.config else [])]
    cli += ["--server", args.server] if args.server else []

    def make(controller: Controller, stop: threading.Event) -> AppUi:
        from hark_client.menubar import MenuBar  # noqa: PLC0415 - AppKit

        return MenuBar(
            controller, stop,
            config_path=Path(args.config) if args.config else default_config_path(),
            cli=cli, onboarding=not args.no_onboarding,
        )  # fmt: skip

    return make


if __name__ == "__main__":
    raise SystemExit(main())
