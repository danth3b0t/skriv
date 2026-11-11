# hark-client (POC)

Push-to-talk dictation client for a [hark.v1](../proto/hark/v1/hark.proto) server
([ADR-001](../docs/protocol/ADR-001-protocol.md)). Hold a key, speak, release: the text is pasted into the focused app.

```
key down ─┬─ context.collect() (thread, 150 ms deadline) ──► SessionStart (bias list)
          └─ Transcribe stream opens; 40 ms PCM16 chunks (+ pre-roll) queue behind SessionStart
key up ───► EndOfAudio ► FinalResult ► canonicalize ► cleanup (optional, deadline) ► guardrails
          ► focus re-check ► paste (or clipboard + notification) ► history.jsonl
```

## Quickstart

Everything runs from the repo root (uv workspace, Python 3.12).

```sh
uv sync
uv run hark-server --backend mock &            # or parakeet_mlx on a Mac, see server/README.md
uv run hark doctor                             # server, mic, permissions, paste tools, daemon, context
uv run hark once --wav some.wav --dry-run      # whole pipeline on a file, every stage printed
uv run hark once --wav some.wav --dry-run --profile terminal
```

`hark once` prints each stage's output and timing (context, SessionStart, ASR, canonicalize, cleanup, guard, paste, history).
Without `--dry-run` it really pastes into the focused window. Any PCM WAV works (resampled to 16 kHz mono with soxr).

### macOS

```sh
uv run hark run          # hold Right Option to dictate, release to paste, Esc while holding cancels
```

The app that runs hark (your terminal) needs, in System Settings → Privacy & Security:
**Microphone**, **Input Monitoring** (global hotkey via pynput) and **Accessibility** (posting Cmd+V).
`hark doctor` checks the latter two. Paste = NSPasteboard (saved and restored afterwards, including non-text items)
+ a CGEvent Cmd+V from a private event source (key code 9), so the held hotkey's modifiers do not leak into it.

### Linux / Hyprland (Wayland)

Wayland gives clients no global key grabs, so the daemon listens on a control socket
(`$XDG_RUNTIME_DIR/hark/ctl.sock`) and the compositor binds keys to `hark ctl`:

```sh
sudo pacman -S portaudio wl-clipboard wtype libnotify   # PortAudio for sounddevice; wtype = fallback
uv run hark run
```

Hyprland ≤ 0.54 (hyprlang), push-to-talk on F13 (`bindr` fires on release):

```ini
bind  = , F13, exec, hark ctl start
bindr = , F13, exec, hark ctl stop
bind  = SUPER, Escape, exec, hark ctl cancel
```

Hyprland ≥ 0.55 (Lua config):

```lua
hl.bind("F13", hl.dsp.exec_cmd("hark ctl start"))
hl.bind("F13", hl.dsp.exec_cmd("hark ctl stop"), { release = true })
```

`hark` must be on `PATH` for the compositor (e.g. `uv tool install --editable ./client`, or use the full path to
`.venv/bin/hark`). `hark ctl` takes ~50 ms (stdlib-only imports); the 200 ms pre-roll buffer (mic kept open) covers the first
words. Zero-cost alternative: `echo start | nc -U "$XDG_RUNTIME_DIR/hark/ctl.sock"`. `hark ctl toggle` works for a single
tap-to-start/tap-to-stop key.

Paste: `wl-copy`, then `hyprctl dispatch sendshortcut` (old and Lua dispatcher syntax are both tried), else `wtype`.
Terminals get Ctrl+Shift+V. On X11: `xclip` + `xdotool`, and `hotkey.listener = "pynput"` works with the `x11` extra.

Control protocol: one line in (`start|stop|toggle|cancel|status|ping`), one line out (`ok …` or `error …`).
`hark ctl` exits 0 on `ok`, 1 on `error`, 2 if no daemon is running.

## Configuration

`~/.config/hark/config.toml` (or `--config FILE`; `--server` overrides the address). All keys are optional; unknown
keys and wrong types are errors.

```toml
[server]
address = "127.0.0.1:50051"   # or "desktop.tailnet:50051", "unix:/run/hark.sock"
tls_ca = ""                   # set => TLS; tls_cert + tls_key => mTLS
model_id = ""                 # "" => server default
language = ""                 # BCP-47; "" => detect
finalize_budget_ms = 0        # 0 => server default
final_timeout_ms = 5000

[context]
deadline_ms = 150             # SessionStart waits at most this long for context
max_terms = 60

[cleanup]
enabled = true                # profiles opt in individually
deadline_ms = 600             # timeout/failure => uncleaned text

[audio]
device = ""                   # PortAudio name or index
preroll_ms = 200              # needs keep_open
keep_open = true              # mic stream stays open between dictations
min_ms = 300                  # shorter presses are cancelled
max_ms = 120000

[hotkey]
key = "alt_r"                 # pynput Key name (alt_r = Right Option, cmd_r, f13, ...) or one character
listener = "auto"             # auto (pynput on macOS/X11) | pynput | none

[paste]
method = "auto"               # auto | macos | hyprland | wtype | x11 | clipboard | dry-run
restore_clipboard = true
check_focus = true            # focus changed since key-down => copy + notify instead of pasting

[feedback]
sounds = true                 # afplay (macOS), pw-play/paplay (Linux)
notify = true

[history]
dir = "~/.local/share/hark"
save_recordings = false       # opt-in dataset seeding, see below

[profiles.terminal]           # built-ins: terminal, chat, prose (fallback); table order = match order
apps = ["com.mitchellh.ghostty", "kitty", "*terminal*"]   # globs on app id / name, case-insensitive
terminal = true               # single line, Ctrl+Shift+V on Linux, never Enter
cleanup = false
cleanup_profile = "prose"     # server CleanupProfile.id
```

A profile is chosen by the first `apps` match, else `terminal` if the context engine reports a terminal, else `prose`.

## Guardrails

- All profiles: escape sequences (CSI incl. the bracketed-paste markers `ESC[200~`/`ESC[201~`, OSC, 8-bit CSI), C0/C1
  control characters and bidi overrides are removed.
- Terminal profile: every whitespace run, newlines included, becomes one space; no trailing newline. Nothing ever sends Enter.
- Cleanup: protected spans (shortlist identifiers, code-like tokens, numbers, negations) are sent with the request.
  The client re-validates every returned edit (unique quote, byte range, protected spans, per-kind invariant, negation
  and digit preservation). One bad edit discards the whole set; timeouts and RPC errors fall back to the uncleaned text.
- Focus: if the focused window changed between key-down and paste, the text goes to the clipboard and a notification
  says so.

## History and recordings

`history.jsonl` gets one line per dictation: status, profile, app, raw/canonical/final text, replacements, cleanup
outcome, guard events, paste result, client timings per stage (`context_ms`, `session_start_ms`, `accepted_ms`,
`keyup_to_final_ms`, `canonicalize_ms`, `cleanup_ms`, `guard_ms`, `focus_check_ms`, `paste_ms`, `keyup_to_paste_ms`),
wall-clock marks `client_unix_us` (`keyup`, `end_enqueued`, `end_dequeued` by the gRPC request iterator, `end_sent`,
`final_received`; comparable with the server's `server_end_received_unix_us` / `server_final_sent_unix_us`)
and the server's `Timings`. Files are created 0600 in a 0700 directory.

With `save_recordings = true`, each dictation also writes `recordings/YYYY/MM/<utterance_id>/`:
`audio.wav` (exactly the 16 kHz stream sent, pre-roll included), `context.json` (the context snapshot) and
`result.json` (history record + SessionStart + FinalResult). These are raw, unscrubbed inputs for the dataset
pipeline in [docs/dataset/spec.md](../docs/dataset/spec.md).

## Tests

```sh
uv run pytest client/tests -q     # ~60 tests, ~2 s
```

`hark once` runs end to end against an in-process hark server (mock backend, on its own event-loop thread) with a
fake OpenAI-compatible cleanup endpoint behind it, and a scripted context engine: biasing, cleanup, terminal profile,
cleanup timeout fallback, late context, focus change, unreachable server. Plus guardrail tables, the protected-span
fallback against a misbehaving TextCleanup server, config parsing, the ctl socket protocol, Linux paste commands
(fake runner) and the daemon controller (pre-roll, cancel, too short).

## Limitations

- Context: without Accessibility permission macOS gives no window title/selection; the terminal/repo/agent context still works. `hark once --cwd DIR` pins the repo for testing; with no focused terminal and no `--cwd` there are no repo bias terms.
- Untested on real hardware: Hyprland/wtype/X11 paste (commands are unit-tested only), the macOS Cmd+V path and the
  pynput hotkey (needs permissions this session did not have), long-running daemon use.
- Cleanup only via the hark `TextCleanup` service; no direct OpenAI-compatible adapter yet (ADR-001 #6).
- No VAD, no `EagerEnd`, no partials, no retries/fallback providers, no `ContextUpdate`, no undo/swap-to-raw.
- Esc (cancel) is not swallowed: the focused app sees it too. The start sound can leak into the recording.
- Clipboard restore on Linux restores text only.
