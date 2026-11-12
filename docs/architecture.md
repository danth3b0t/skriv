# Architecture

One dictation is four steps: **record → recognize with context → clean → deliver**. The client (`hark`, in
`client/src/hark_client/`) owns all four; the server (`hark-server`, in `server/src/hark_server/`) decodes audio and
runs the cleanup model. They speak gRPC (`proto/hark/v1/hark.proto`). UIs never touch the pipeline: they read facts
from the daemon's control socket and decide what to show.

```
key down ──► record ─────────────► recognize with context ──────────► clean ──────────────► deliver
             mic, pre-roll          context.collect() ─► SessionStart   spoken symbols         guardrails
             hotkey / hark ctl      40 ms PCM chunks ─► Transcribe      self-corrections       focus re-check
             one at a time          FinalResult ─► anchor gate          S1-mini rewrite        smart spacing
                                    ─► canonicalize                     ─► fidelity check      paste / copy, history
```

`pipeline.py` (`Utterance`) runs the steps for one dictation, the same code for `hark run`, `hark once`, `hark retry`
and `hark eval`. Its `_finish` reads top to bottom in this order.

## Record

| Module | Owns |
|---|---|
| `daemon.py` | `hark run`: the `Controller` (one dictation at a time, cancel until the paste, pause/resume, `audio.max_ms`), the threads, `reload` |
| `hotkey.py` | the pynput listener on macOS/X11: hold to talk, tap for hands-free, Esc cancels (and is swallowed) |
| `ctl.py` | the control socket: `hark ctl start/stop/toggle/…` from compositor binds (Wayland has no global keys) |
| `mic.py`, `audio.py` | the PortAudio stream (kept open, watchdog reopens a lost mic), 16 kHz mono PCM, pre-roll, speech and flat-signal measures, WAV I/O |
| `feedback.py` | sounds (start cue off by default) and notifications |

## Recognize with context

| Module | Owns |
|---|---|
| `context/` | `collect()` at key-down under `context.deadline_ms`: focus, terminal cwd, repo, agent sessions, screen text; ranked to `context.max_terms` ([context-engine.md](context-engine.md)). `prefetch.py` keeps the slow parts warm per window. |
| `context/words.py` | `[[words]]` (config and `<repo>/.hark/words.toml`) ahead of the ranked terms; base shell commands for the terminal profile |
| `providers.py` | which server takes the dictation: first healthy `[[providers]]` entry, cached health checks |
| `asr.py` | the `Transcribe` stream: `SessionStart` with the bias list (unless `send_context = false`), audio, `FinalResult` |
| `gate.py` | anchor gate: the server returns a biased and an unbiased decode; biased words the audio does not support are reverted |
| `context/canon.py`, `context/aliases.py` | canonicalization: spoken forms of shortlist terms rewritten to their spelling, with everyday-word protection |
| `guardrails.degenerate` | refuses `<unk>` runs and repeated patterns as a failed transcription (kept for `hark retry`) |
| server `session.py`, `backends/` | model routing by language, audio placement, finalize budget, bias (decode-time boosting on `parakeet_mlx`; none on `transcribe_cpp`), anchor, bias hits (`hits.py`), language fallback (`language.py`) |

## Clean

| Module | Owns |
|---|---|
| `symbols.py` | terminal profile only: "dash dash verbose" → `--verbose` |
| `corrections.py` | explicit self-corrections ("three seconds, sorry, five seconds") resolved before the model |
| `cleanup.py` | the `Cleanup` RPC under `cleanup.deadline_ms`; any failure keeps the uncleaned text |
| server `cleanup.py` | S1-mini through an OpenAI-compatible endpoint (llama-server), small per-profile touches; returns the rewrite, judges nothing |
| `fidelity.py` | the one definition of a faithful rewrite: identifiers, numbers and negations kept, few content words changed. A rewrite that fails it is discarded (`rejected`). `hark eval` scores with the same module. |

## Deliver

| Module | Owns |
|---|---|
| `guardrails.py` | strips escape and control characters; terminal profile: one line, never Enter |
| `continuation.py`, `context/caret.py` | smart spacing and mid-sentence lower case from the text around the caret (AX, AT-SPI, tmux, shadow buffer) |
| `inject.py` | paste: macOS Cmd+V, Hyprland shortcut sent to the window captured at key-down, wtype, X11; focus moved → clipboard + notification; clipboard restored |
| `history.py`, `historyview.py` | `history.jsonl` (every stage, timings, no audio); `hark history`, `hark last` |
| `retry.py` | the only audio store: the last `retry.keep` recordings with their key-down context, for `hark retry` and `hark eval add` |

## The daemon/UI contract

The daemon reports **facts**, never labels and never dictated text: its state (recording, finishing, paused), the
provider the next dictation would use, whether the config changed or needs a restart, and the last result's
status, delivery and latency. `status.py` builds the JSON v1 `status` reply and the `subscribe` stream on
`ctl.sock`; the spec is [protocol/ctl-v1.md](protocol/ctl-v1.md), pinned by `client/tests/test_client_ctl.py`.
Each UI turns facts into words:

| UI | Where | Reads |
|---|---|---|
| the pill | `indicator/` (GTK layer-shell on Linux, NSPanel on macOS), in the daemon | the controller's events directly; passive, never takes focus |
| bar glyph and quick panel (Omarchy) | `packaging/omarchy/hark.shell` (QML, rules in `Model.js`), `hark-panel` | one `subscribe` connection; the last text from `hark history -n 1 --json` when the panel opens |
| menu bar and setup window (macOS) | `menubar.py`, `onboarding.py` (`hark app`, Hark.app) | the controller in-process |

The UI design is [design/SPEC.md](../design/SPEC.md). Commands go the other way through the same socket
(`pause`, `resume`, `paste-last`, `reload`, …) or the CLI (`hark retry`, `hark last --copy`).

## Around the pipeline

`__main__.py` is the CLI (`hark run|app|ctl|once|retry|history|last|doctor|words|eval|service`), `config.py` the
TOML config, `doctor.py` the checks, `service/` the systemd units and launchd agents. The server's entry points are
`server/src/hark_server/__main__.py` and `config.py`.

## Tests and eval

| What | Where | Run |
|---|---|---|
| client unit and end-to-end tests (`hark once` against an in-process server with the mock backend and a fake cleanup endpoint) | `client/tests/` | `uv run pytest client/tests -q` |
| server tests over real gRPC on localhost | `server/tests/` | `uv run pytest server/tests -q` |
| Omarchy shell rules (`Model.js` under Node against real daemon status lines) | `client/tests/test_omarchy_shell.py` | with the client tests |
| accuracy regression, every stage scored | `eval/dev-v1/` (shared, synthetic + real speech), your own set in `<history.dir>/eval` ([eval.md](eval.md)) | `uv run hark eval run --set eval/dev-v1` |
| cleanup model regression through the real RPC | `server/tests/data/cleanup_eval*.jsonl` | `scripts/eval-cleanup` |
| measurements | [bench/](bench/) (eval configs and reports in `bench/eval-baseline/`) | |
| a server by hand | `tools/hark-probe` | `tools/hark-probe/hark-probe some.wav --words` |
