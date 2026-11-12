"""hark CLI: `run` (daemon), `app` (the daemon with a macOS menu-bar item; Hark.app runs it),
`ctl` (control the daemon), `once` (pipeline on a WAV), `retry`
(pipeline on a kept recording), `history`, `last`, `doctor`, `eval`, `service` (always-on
services: systemd user units on Linux, launchd agents on macOS).

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

from skriv_client import service

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from skriv_client.config import Config, WordConfig
    from skriv_client.context import ContextSnapshot
    from skriv_client.daemon import AppUi, Controller
    from skriv_client.pipeline import Services


class Args(argparse.Namespace):
    # Annotations only: argparse fills in the values.
    command: str
    config: str | None
    server: str | None
    verbose: bool
    action: str
    args: list[str]
    wav: str
    dry_run: bool
    profile: str | None
    cwd: str | None
    realtime: bool
    no_history: bool
    json: bool
    id: str | None
    copy: bool
    raw: bool
    history_action: str
    n: int
    service_action: str
    components: list[str]
    no_start: bool
    force: bool
    asr_backend: str | None
    cleanup_model: str | None
    no_cleanup: bool
    no_onboarding: bool
    eval_action: str
    set_dir: str | None
    report: str | None
    words_action: str
    terms: list[str]
    repo: bool


def parse_args(argv: list[str] | None = None) -> Args:
    p = argparse.ArgumentParser(prog="skriv", description="context-aware dictation (POC client)")
    p.add_argument("--config", help="config TOML (default: ~/.config/hark/config.toml)")
    p.add_argument(
        "--server", help="one provider at this address (host:port or unix:/path), not [[providers]]"
    )
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
        choices=[
            "start", "stop", "toggle", "cancel", "status", "ping", "paste-last", "pause",
            "resume", "profile", "reload",
        ],
        help="paste-last: paste the last dictated text again into the focused window; "
        "pause / resume: refuse dictations and release the mic, or not; "
        "profile auto|set NAME: the profile for the next dictation; "
        "reload: re-read the config file (the next dictation uses it)",
    )  # fmt: skip
    c.add_argument("args", nargs="*", help="profile: auto | set NAME")
    c.add_argument(
        "--json", action="store_true", help="JSON v1 request/reply (docs/protocol/ctl-v1.md)"
    )
    o = sub.add_parser("once", help="run the whole pipeline on a WAV file and report each stage")
    o.add_argument(
        "--wav", required=True, help="WAV file (any rate/channels; resampled to 16 kHz mono)"
    )
    o.add_argument("--dry-run", action="store_true", help="print instead of pasting")
    o.add_argument("--profile", help="force a profile (e.g. terminal, prose)")
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
    la.add_argument(
        "--raw", action="store_true", help="the text before cleanup (what was said, uncleaned)"
    )
    sub.add_parser("doctor", help="check server, mic, permissions, paste tools")
    w = sub.add_parser("words", help="spellings the recognizer needs help with ([[words]])")
    w.add_argument("words_action", nargs="?", default="list", choices=["list", "add", "remove"])
    w.add_argument(
        "terms", nargs="*", help='add: [SPOKEN] WORD, e.g. "hyper land" Hyprland; remove: WORD'
    )
    w.add_argument(
        "--repo", action="store_true",
        help="this git repository's list (<repo>/.hark/words.toml), not your config",
    )  # fmt: skip
    ev = sub.add_parser("eval", help="regression set of real dictations (docs/eval.md)")
    ev.add_argument("eval_action", choices=["add", "run", "accept"])
    ev.add_argument(
        "id", nargs="?", default="last", help="add: a kept dictation (default last); accept: a clip"
    )
    ev.add_argument(
        "--set", dest="set_dir", help="the set's directory (default <history.dir>/eval)"
    )
    ev.add_argument("--report", help="run: also write every clip's stages and score as JSON here")
    service.add_parser(sub)
    return p.parse_args(argv, namespace=Args())


def cmd_ctl(args: Args) -> int:
    import json  # noqa: PLC0415

    from skriv_client import ctl  # noqa: PLC0415

    try:
        if args.json:
            obj = ctl.send_json(args.action, args=args.args)
            print(json.dumps(obj))
            return 0 if obj.get("ok") is True else 1
        reply = ctl.send(" ".join([args.action, *args.args]))
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


def run_pcm(
    services: Services,
    pcm: bytes,
    *,
    profile: str | None = None,
    realtime: bool = False,
    replay: ContextSnapshot | None = None,
) -> int:
    """Stream 16 kHz PCM16 through one Utterance (as fast as possible unless ``realtime``)."""
    from skriv_client.asr import CHUNK_SAMPLES, SAMPLE_RATE  # noqa: PLC0415
    from skriv_client.pipeline import Utterance  # noqa: PLC0415

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
    from skriv_client.asr import SAMPLE_RATE  # noqa: PLC0415
    from skriv_client.audio import read_wav  # noqa: PLC0415

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
    from skriv_client import retry  # noqa: PLC0415
    from skriv_client.audio import read_wav  # noqa: PLC0415
    from skriv_client.config import expand  # noqa: PLC0415
    from skriv_client.inject import ClipboardOnlyInjector  # noqa: PLC0415

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
        return service.main(
            args.service_action, args.components,
            no_start=args.no_start, force=args.force, dry_run=args.dry_run,
            asr_backend=args.asr_backend, cleanup_model=args.cleanup_model,
            no_cleanup=args.no_cleanup,
        )  # fmt: skip
    if args.command == "app" and sys.platform != "darwin":
        print("hark app: macOS only; use `hark run`", file=sys.stderr)
        return 2

    from skriv_client import config  # noqa: PLC0415

    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        print(f"hark: config error: {e}", file=sys.stderr)
        return 2
    if args.server:
        cfg.providers = [config.ProviderConfig(address=args.server)]
    logging.basicConfig(
        level=logging.DEBUG
        if args.verbose
        else getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config_path = args.config or str(config.default_config_path())

    if args.command == "doctor":
        from skriv_client import doctor  # noqa: PLC0415

        return doctor.run(
            cfg, config_path if Path(config_path).exists() else f"{config_path} (absent; defaults)"
        )
    offline = {"history": cmd_history, "last": cmd_last, "eval": cmd_eval, "words": cmd_words}
    return offline.get(args.command, cmd_pipeline)(args, cfg)


def cmd_eval(args: Args, cfg: Config) -> int:
    from skriv_client import evalset  # noqa: PLC0415
    from skriv_client.config import expand  # noqa: PLC0415

    base = expand(cfg.history.dir)
    set_dir = Path(args.set_dir).expanduser() if args.set_dir else base / "eval"
    try:
        if args.eval_action == "add":
            out = evalset.add(base, set_dir, args.id or "last")
            print(f"added {out}; edit {out / evalset.LABEL} (said, critical, forbidden)")
            return 0
        if args.eval_action == "accept":
            print(f"accepted: {evalset.accept(set_dir, args.id or '')!r}")
            return 0
    except (LookupError, FileExistsError) as e:
        print(f"hark eval: {e}", file=sys.stderr)
        return 1
    return _eval_run(cfg, set_dir, Path(args.report) if args.report else None)


def _eval_run(cfg: Config, set_dir: Path, report: Path | None) -> int:
    from skriv_client import evalset  # noqa: PLC0415
    from skriv_client.config import FeedbackConfig  # noqa: PLC0415
    from skriv_client.feedback import Feedback  # noqa: PLC0415
    from skriv_client.inject import DryRunInjector  # noqa: PLC0415
    from skriv_client.pipeline import Services  # noqa: PLC0415
    from skriv_client.providers import Router  # noqa: PLC0415

    found = evalset.clips(set_dir)
    if not found:
        print(
            f"hark eval: no clips in {set_dir} (`hark eval add` after a dictation)", file=sys.stderr
        )
        return 1
    cfg.history.enabled = False
    cfg.retry.keep = 0
    # nothing is pasted: the window and caret of today are not the clip's
    cfg.paste.check_focus = cfg.paste.smart_spacing = False
    router = Router(cfg.providers)
    try:
        router.refresh()
        svc = Services(
            cfg, router, DryRunInjector(out=lambda _: None),
            Feedback(FeedbackConfig(sounds=False, notify=False)),
        )  # fmt: skip
        scores: list[evalset.Score] = []
        records: list[dict[str, object]] = []
        latency: list[float] = []  # release_to_paste_ms of the clips that reached the paste
        for clip in found:
            res = evalset.run_clip(svc, clip)
            s = evalset.score(
                clip.name, evalset.load_label(clip), res.raw_text, res.canonical_text,
                res.final_text,
            )  # fmt: skip
            scores.append(s)
            records.append(evalset.report(s, res))
            if "release_to_paste_ms" in res.timings:
                latency.append(res.timings["release_to_paste_ms"])
            print(evalset.line(s, res.status, res.final_text))
    finally:
        router.close()
    if report is not None:
        import json  # noqa: PLC0415

        report.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n")
    failed = sum(s.failed for s in scores)
    unreviewed = sum(not s.accepted for s in scores)
    wers = [s.wer for s in scores if s.wer is not None]
    mean = f"{sum(wers) / len(wers):.1%}" if wers else "n/a"
    print(f"{len(scores)} clips: {failed} failed, {unreviewed} unreviewed, recognition WER {mean}")
    if latency:
        import statistics  # noqa: PLC0415

        print(
            f"release->paste median {statistics.median(latency):.0f} ms, max {max(latency):.0f} ms"
            f" ({len(latency)} clips)"
        )
    return 1 if failed else 0


def cmd_history(args: Args, cfg: Config) -> int:
    import json  # noqa: PLC0415

    from skriv_client import history, historyview, retry  # noqa: PLC0415
    from skriv_client.config import expand  # noqa: PLC0415

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
    from skriv_client import history  # noqa: PLC0415
    from skriv_client.config import expand  # noqa: PLC0415

    text = history.last_text(
        expand(cfg.history.dir), key="uncleaned_text" if args.raw else "final_text"
    )
    if not text:
        what = "uncleaned text" if args.raw else "dictated text"
        print(f"hark last: no {what} in the history", file=sys.stderr)
        return 1
    if not args.copy:
        print(text)
        return 0
    from skriv_client.inject import make_injector  # noqa: PLC0415

    if not make_injector(cfg.paste).copy(text):
        print("hark last: could not write the clipboard", file=sys.stderr)
        return 1
    print(f"copied {len(text)} characters")
    return 0


def _git_root() -> Path | None:
    """The git repository around the working directory (what the context engine reports)."""
    import subprocess  # noqa: PLC0415

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607 - git from PATH
            capture_output=True, text=True, timeout=5, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def _show_words(words: list[WordConfig], where: Path) -> None:
    print(f"{where} ({len(words)}):")
    for w in words:
        spoken = f"  <- {', '.join(repr(s) for s in w.spoken)}" if w.spoken else ""
        print(f"  {w.word}{spoken}")


def cmd_words(args: Args, cfg: Config) -> int:  # noqa: PLR0911 - one return per outcome
    """List, add or remove a `[[words]]` entry: your config, or with --repo the repository's
    list. Files are edited as text (comments stay), then re-read; the daemon reloads."""
    from skriv_client import config  # noqa: PLC0415

    config_path = Path(args.config) if args.config else config.default_config_path()
    root = _git_root()
    if args.repo and root is None:
        print("hark words --repo: not inside a git repository", file=sys.stderr)
        return 2
    limit = config.words_limit(cfg.context.max_terms)
    try:
        repo = config.load_words(root / config.REPO_WORDS, cfg.context.max_terms) if root else []
    except config.ConfigError as e:
        print(f"hark words: {e}", file=sys.stderr)
        return 2
    if args.words_action == "list":
        _show_words(cfg.words, config_path)
        if root is not None:
            _show_words(repo, root / config.REPO_WORDS)
        print(f"at most {limit} reach a dictation (a third of context.max_terms)")
        return 0
    path = root / config.REPO_WORDS if args.repo and root else config_path
    current = repo if args.repo else cfg.words
    terms = args.terms
    if args.words_action == "remove":
        if len(terms) != 1:
            print("hark words remove: give the WORD", file=sys.stderr)
            return 2
        text, found = config.remove_word(_read(path), terms[0])
        if not found:
            print(f"hark words: {terms[0]!r} is not in {path}", file=sys.stderr)
            return 1
        return _save_words(path, text, cfg, f"removed {terms[0]!r} from {path}", repo=args.repo)
    if len(terms) not in (1, 2) or not all(t.strip() for t in terms):
        print('hark words add: give [SPOKEN] WORD, e.g. "hyper land" Hyprland', file=sys.stderr)
        return 2
    word, spoken = terms[-1].strip(), [t.strip() for t in terms[:-1]]
    old = next((w for w in current if w.word == word), None)
    if old is not None:
        merged = list(dict.fromkeys([*old.spoken, *spoken]))
        if merged == old.spoken:
            print(f"{word!r} is already in {path}")
            return 0
        spoken = merged
    taken = {w.word for w in (*cfg.words, *repo)} if args.repo else {w.word for w in cfg.words}
    if old is None and len(taken) >= limit:
        print(
            f"hark words: the list is full ({len(taken)} of {limit}, a third of"
            " context.max_terms, yours and this repository's together); `hark words remove"
            " WORD` one that context now supplies, then add again",
            file=sys.stderr,
        )
        return 1
    text, _ = config.remove_word(_read(path), word)
    text = config.append_block(text, config.word_block(word, spoken))
    return _save_words(path, text, cfg, f"added {word!r} to {path}", repo=args.repo)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def _save_words(path: Path, text: str, cfg: Config, done: str, *, repo: bool) -> int:
    """Write ``text``, re-read the file (restored if it no longer loads), reload the daemon."""
    from skriv_client import config, ctl  # noqa: PLC0415

    before = path.read_text() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    try:
        if repo:
            config.load_words(path, cfg.context.max_terms)
        else:
            config.load(path)
    except config.ConfigError as e:
        if before is None:
            path.unlink()
        else:
            path.write_text(before)
        print(f"hark words: not changed: {e}", file=sys.stderr)
        return 1
    print(done)
    if repo:
        print("used from the next dictation in this repository")
        return 0
    try:
        reply = ctl.send("reload")
    except OSError:
        print("no daemon running; `hark run` reads it")
        return 0
    print(f"daemon: {reply}")
    return 0 if reply.startswith("ok") else 1


def cmd_pipeline(args: Args, cfg: Config) -> int:
    from skriv_client import ctl, daemon  # noqa: PLC0415
    from skriv_client.config import (  # noqa: PLC0415
        ConfigError,
        FeedbackConfig,
        default_config_path,
    )
    from skriv_client.feedback import Feedback  # noqa: PLC0415
    from skriv_client.inject import make_injector  # noqa: PLC0415
    from skriv_client.pipeline import Services  # noqa: PLC0415
    from skriv_client.providers import Router  # noqa: PLC0415

    router = Router(cfg.providers)
    try:
        if args.command in ("once", "retry"):
            if args.command == "once":
                cfg.retry.keep = 0  # nothing kept for `hark retry`: the WAV is the recording
                if args.no_history:
                    cfg.history.enabled = False
            elif args.copy:
                cfg.paste.method = "clipboard"
            router.refresh()
            svc = Services(
                cfg, router,
                injector=make_injector(cfg.paste, dry_run=args.dry_run),
                feedback=Feedback(FeedbackConfig(sounds=False, notify=False)),
                out=print,
            )  # fmt: skip
            try:
                return cmd_once(args, svc) if args.command == "once" else cmd_retry(args, svc)
            except ConfigError as e:
                print(f"hark: {e}", file=sys.stderr)
                return 2
        feedback = Feedback(cfg.feedback)
        svc = Services(cfg, router, injector=make_injector(cfg.paste), feedback=feedback)
        try:
            config_path = Path(args.config) if args.config else default_config_path()
            return daemon.run(
                svc,
                _menu_bar(args) if args.command == "app" else None,
                config_path if config_path.exists() else None,
            )
        except ctl.DaemonRunningError as e:
            print(f"hark: {e}", file=sys.stderr)
            return 1
    finally:
        router.close()


def _menu_bar(args: Args) -> Callable[[Controller, threading.Event], AppUi]:
    """`hark app`: builds the menu-bar item on the main thread once the daemon is up."""
    from skriv_client.config import default_config_path  # noqa: PLC0415

    cli = [*(["--config", args.config] if args.config else [])]
    cli += ["--server", args.server] if args.server else []

    def make(controller: Controller, stop: threading.Event) -> AppUi:
        from skriv_client.menubar import MenuBar  # noqa: PLC0415 - AppKit

        return MenuBar(
            controller, stop,
            config_path=Path(args.config) if args.config else default_config_path(),
            cli=cli, onboarding=not args.no_onboarding,
        )  # fmt: skip

    return make


if __name__ == "__main__":
    raise SystemExit(main())
