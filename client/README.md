# hark-client (POC)

Push-to-talk dictation client for a [hark.v1](../proto/hark/v1/hark.proto) server
([ADR-001](../docs/protocol/ADR-001-protocol.md)). Hold a key, speak, release: the text is pasted into the focused app.
Tap the key instead to dictate hands-free until the next tap.

```
key down ─┬─ context.collect() (thread, 150 ms deadline) ──► SessionStart (bias list, if the provider may see it)
          └─ provider router picks a server (cached health) ──► Transcribe stream; 40 ms PCM16 chunks (+ pre-roll)
key up ───► EndOfAudio ► FinalResult ► anchor gate ► canonicalize ► spoken symbols (terminal) ► cleanup (optional, deadline) ► guardrails
          ► focus re-check ► paste (or clipboard + notification) ► history.jsonl (+ audio kept for `hark retry`)
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
uv run hark run          # hold Right Option to dictate, release to paste; or tap it, speak, tap again; Esc cancels
```

The app that runs hark (your terminal) needs, in System Settings → Privacy & Security:
**Microphone**, **Input Monitoring** (global hotkey via pynput) and **Accessibility** (posting Cmd+V).
`hark doctor` checks all three. Or use **Hark.app** (`scripts/build-macos-app`, see
[packaging/macos](../packaging/macos/README.md)): it runs `hark app` = `hark run` plus a menu-bar item and a setup
window, and the permissions belong to Hark instead of the terminal. Paste = NSPasteboard (saved and restored afterwards, including non-text items)
+ a CGEvent Cmd+V from a private event source (key code 9), so the held hotkey's modifiers do not leak into it.

### Linux / Hyprland (Wayland)

Wayland gives clients no global key grabs, so the daemon listens on a control socket
(`$XDG_RUNTIME_DIR/hark/ctl.sock`) and the compositor binds keys to `hark ctl`:

```sh
sudo pacman -S portaudio wl-clipboard wtype libnotify   # PortAudio for sounddevice; wtype = fallback
uv run hark run
```

Installed from the `hark-git` package, `hark service install` runs the daemon as a systemd user service
(`hark service status`, `hark service uninstall`); see [docs/linux.md](../docs/linux.md).

Hyprland ≤ 0.54 (hyprlang), push-to-talk on F13 (`bindr` fires on release):

```ini
bind  = , F13, exec, hark ctl start
bindr = , F13, exec, hark ctl stop
bind  = SUPER, Escape, exec, hark ctl cancel
```

Plain Esc as cancel, swallowed only while recording (hark cannot grab keys on Wayland; a submap entered for the
recording can; **untested**):

```ini
bind   = , F13, exec, hark ctl start
bind   = , F13, submap, hark
submap = hark
bindr  = , F13, exec, hark ctl stop
bindr  = , F13, submap, reset
bind   = , Escape, exec, hark ctl cancel
bind   = , Escape, submap, reset
submap = reset
```

Hyprland ≥ 0.55 (Lua config):

```lua
hl.bind("F13", hl.dsp.exec_cmd("hark ctl start"))
hl.bind("F13", hl.dsp.exec_cmd("hark ctl stop"), { release = true })
```

Omarchy: hark implements Omarchy's dictation-backend contract, so Omarchy's own keys (hold Right Alt, Alt+Space
toggle) drive it with no Hyprland edits:

```sh
uv tool install --editable ./client                    # puts `hark` on PATH
packaging/omarchy/omarchy-install-dictation-hark       # links omarchy-dictation-hark into ~/.local/bin,
                                                       # installs + starts hark.service, selects hark
omarchy dictation toggle                               # = omarchy-dictation-hark toggle = hark ctl toggle
```

`omarchy-dictation-hark stop` succeeds when hark is idle or not running; `start` with no daemon starts
`hark.service` and fails so you press again. `packaging/omarchy/omarchy-remove-dictation-hark` undoes the install
(config, history and learned vocabulary stay). Untested on a real Omarchy install.

`hark` must be on `PATH` for the compositor (e.g. `uv tool install --editable ./client`, or use the full path to
`.venv/bin/hark`). `hark ctl` takes ~50 ms (stdlib-only imports); the 200 ms pre-roll buffer (mic kept open) covers the first
words. Zero-cost alternative: `echo start | nc -U "$XDG_RUNTIME_DIR/hark/ctl.sock"`. `hark ctl toggle` works for a single
tap-to-start/tap-to-stop key.

Paste: `wl-copy`, then a Ctrl+V sent to the window that was focused at key-down. With a Lua Hyprland config
(≥ 0.55) one `hyprctl repl` chunk re-checks the active window's address *inside the compositor* and sends
`send_shortcut{…, window = "address:…"}` only if it still matches (otherwise: status `focus_changed`, text left in
the clipboard, notification); with hyprlang (≤ 0.54) `dispatch sendshortcut CTRL, V, address:…`. The syntax that
worked is remembered. Unknown target: an untargeted shortcut; neither syntax works: `wtype`.
Terminals get Ctrl+Shift+V. On X11: `xclip` + `xdotool`, and `hotkey.listener = "pynput"` works with the `x11` extra.

`HARK_CTL_SOCKET` overrides the socket path (e.g. a second, test daemon). Control protocol: one line in
(`start|stop|toggle|cancel|status|ping|paste-last|pause|resume|profile …|reload`), one line out (`ok …` or `error …`).
`hark ctl` exits 0 on `ok`, 1 on `error`, 2 if no daemon is running. Status bars and scripts can use the versioned
JSON protocol on the same socket (`hark ctl --json status`, spec: [ctl-v1](../docs/protocol/ctl-v1.md)): state,
the last dictation's status/delivery/latency (never its text) and the profile override.

## Configuration

`~/.config/hark/config.toml` (or `--config FILE`; `--server ADDRESS` = one provider at that address instead of
`[[providers]]`). All keys are optional;
unknown keys and wrong types are errors.

```toml
[server]                      # per dictation, whichever provider serves it; servers: [[providers]] (below)
language = ""                 # BCP-47; "" => detect
finalize_budget_ms = 0        # 0 => server default
final_timeout_ms = 5000

[context]
deadline_ms = 150             # SessionStart waits at most this long for context
max_terms = 30

[gate]                        # anchor gate: biased words the unbiased decode does not support are reverted
enabled = true
max_distance = 0.5            # anchor must have heard something this close to the term (0..1)
confident_anchor = 0.97       # ...and been less sure than this of its own word (> 1: rule off)

[cleanup]
enabled = true                # profiles opt in individually
deadline_ms = 1000            # timeout/failure => uncleaned text

[audio]
device = ""                   # PortAudio name or index
preroll_ms = 200              # needs keep_open
keep_open = true              # mic stream stays open between dictations
min_ms = 300                  # shorter presses are cancelled (short "cancel" sound, nothing pasted)
min_speech_ms = 300           # less speech energy than this is discarded too (status no_speech); 0 = off
max_ms = 120000

[hotkey]
key = "alt_r"                 # pynput Key name (alt_r = Right Option, cmd_r, f13, ...) or one character
listener = "auto"             # auto (pynput on macOS/X11) | pynput | none
tap_ms = 250                  # released sooner: a tap, recording goes on hands-free until the next press (0 = hold only)
swallow_esc = true            # the Esc that cancels a recording does not reach the app (never when idle)

[paste]
method = "auto"               # auto | macos | hyprland | wtype | x11 | clipboard | dry-run
restore_clipboard = true
restore_delay_ms = 800         # then the clipboard is restored (a slow app pastes the old text if too short)
check_focus = true            # focus changed since key-down => copy + notify instead of pasting
smart_spacing = true          # leading/trailing space + mid-sentence lower case from the caret (see below)
join_space = true             # older name; false also turns smart spacing off
join_window_ms = 60000        # shadow buffer (no readable caret) forgets after this long without input

[[words]]                     # spellings the recognizer needs help with; in every shortlist, first
                              # (at most context.max_terms / 3 entries)
word = "Hyprland"
spoken = ["hyper land"]       # optional; derived spoken forms are added anyway

[feedback]
sounds = true                 # afplay (macOS), pw-play/paplay (Linux)
notify = true
start_cue = "never"           # start sound: never | auto (only without the indicator) | always (see below)

[history]
dir = "~/.local/share/hark"

[retry]
keep = 10                     # audio of the last N dictations for `hark retry` (0 = off), see below

[indicator]
enabled = true                # small passive overlay: recording/level, transcribing, cleaning, done/error
position = "bottom"           # bottom | top, centred on the screen with the pointer (macOS)

[profiles.terminal]           # built-ins: terminal, prose (fallback); table order = match order
apps = ["com.mitchellh.ghostty", "kitty", "*terminal*"]   # globs on app id / name, case-insensitive
terminal = true               # single line, Ctrl+Shift+V on Linux, never Enter
cleanup = true
cleanup_profile = "terminal"  # server CleanupProfile.id
```

A profile is chosen by the first `apps` match, else `terminal` if the context engine reports a terminal, else `prose`,
unless one was picked by hand (below).

## Hands-free, cancel and profiles

- **Tap or hold** (listener: macOS/X11): a press released within `tap_ms` (250 ms) keeps recording hands-free (the
  indicator says **Hands-free**); the next press stops at once. Held longer it is push-to-talk. A press while a
  recording started by `hark ctl` runs stops it. On Wayland bind `hark ctl toggle` (tap) or start/stop (hold).
- **Esc** cancels any recording (hotkey, hands-free or `hark ctl start`), and a stopped dictation until its paste
  starts (`hark ctl cancel` too; on Wayland the submap ends at release, so use the always-bound `SUPER+Escape`).
  One dictation at a time: pressing the hotkey while the last one is still being transcribed records nothing. With `swallow_esc` the focused app never
  sees that Esc keystroke (down, auto-repeat, up); every other key, and Esc while idle, passes untouched. macOS:
  the pynput event tap becomes a filter (`darwin_intercept`), which needs **Accessibility** (without it hark logs a
  warning and stays listen-only); its callbacks only update the key state, the actions run on a worker thread so
  typing is never held up. X11: a passive grab of Esc exists only while recording or finishing (**untested on
  real hardware**).
  Wayland: a compositor submap, see the Hyprland example above.
- **Start sound**: off by default; the indicator (Listening) is the signal. With `start_cue = "always"` (or
  `"auto"` and no indicator) it plays when the recording's first audio arrives, so anything said after it is
  captured. The sound is recorded like everything else; no audio is ever replaced or dropped.
- **Profile**: `hark ctl profile set NAME` picks the profile of the next dictation only; `hark ctl profile auto`
  goes back to choosing by app. The indicator shows **Profile: prose** briefly (never over a recording) and the
  recording as **Listening · prose**; `hark ctl profile` and `hark ctl --json status` (`profile_override`) report
  it. Hark.app's menu-bar Profile menu sets the same override (Automatic = `profile auto`).

## Providers and the offline fallback

Without `[[providers]]` there is one provider, `127.0.0.1:50051` (`address` also takes `unix:/path`). With them,
every dictation goes to the first healthy server in `priority` order (lower first, then file order), e.g. the desktop GPU over Tailscale, then the laptop's own CPU server:

```toml
[[providers]]
name = "desktop"
address = "desktop.tailnet:50051"
tls_ca = "~/.config/hark/ca.pem"     # set => TLS; tls_cert + tls_key => mTLS
tls_server_name = ""                 # override the expected server name
model_id = ""                        # "" => server default
priority = 0
max_latency_ms = 300                 # health check slower than this: skipped for this dictation
send_context = true                  # send the context shortlist (bias terms)

[[providers]]
name = "laptop-cpu"                  # scripts/cpu-server-up (transcribe.cpp, Parakeet v3 Q4_K_M)
address = "127.0.0.1:50052"
priority = 10
```

- **Health**: a gRPC health check of `hark.v1.Asr` bounded by `max_latency_ms`: SERVING in time is *ok*, no answer in
  time is *slow* (also what a blackholed host looks like), refused/NOT_SERVING/TLS errors are *down*. Results are
  cached (ok 30 s, slow/down 5 s) and `hark run` re-checks every 10 s in the background, so key-down normally waits
  for nothing; a stale cache costs at most `max_latency_ms` per skipped provider, spent while the context is collected
  and with the audio buffered.
- **Per dictation**, at session start: the first *ok* provider takes it. If none is ok, the first is used anyway and
  its error reaches you (`hark retry` re-runs the audio). A dictation that gets no answer at all (stream error,
  timeout) marks its provider down, so the next one moves on. There is no switch mid-dictation.
- **Privacy**: `send_context = false` sends audio only; the shortlist stays on this machine and is still used to
  canonicalize the result. Cleanup on such a provider gets the transcript and which spans of it to protect.
- The CPU server has no bias input (bias `NONE`) and no TextCleanup, so dictations it serves are canonicalized
  client-side and not cleaned. Latency: [docs/bench/2026-11-12-transcribe-cpp-cpu.md](../docs/bench/2026-11-12-transcribe-cpp-cpu.md).
- `hark doctor` lists every provider (health, latency, models, bias mechanism, cleanup) and which one the next
  dictation would use; `hark history show ID` and `history.jsonl` (`provider`, `route`, `timings_ms.route_ms`) record
  which one served and why.

Running the CPU server (Linux laptop; also macOS for testing):

```sh
uv sync && scripts/fetch-transcribe          # builds transcribe.cpp 0.4.2 into build/, fetches the GGUF into models/
scripts/cpu-server-up                        # 127.0.0.1:50052, CPU; --device metal|vulkan|cuda, --threads N
# as a systemd user service:
sed "s|@REPO@|$PWD|g" packaging/systemd/hark-cpu-server.service > ~/.config/systemd/user/hark-cpu-server.service
systemctl --user daemon-reload && systemctl --user enable --now hark-cpu-server
```

## Recording indicator

While `hark run` works, a small pill shows its state (design/SPEC.md §4): a red dot (a lock when hands-free, plus a
hand-picked profile) with the live input level, three dots while working, then the outcome for a moment: ✓ (sent,
with at most one tag: *mic dropped*, *cleanup skipped*, *via laptop*; or *Stopped at 2:00*), **Copied**, **Paste
failed · copied**, **Failed · server timeout**, **No speech**, **Cancelled**. A press that starts nothing says why:
**Paused**, **Mic lost**, or the working dots while the previous dictation finishes. On Omarchy, failure pills name
the key that opens hark's panel (**Super Alt D**); elsewhere a failure names `hark retry` when its audio is kept.
It never takes focus or clicks, so the paste still goes to the window you dictated into.

- macOS: a borderless `NSPanel` with `NSWindowStyleMaskNonactivatingPanel`, click-through, status-window level, on
  every Space; the process is an accessory app (no Dock icon) that is never activated, and the panel is shown with
  `orderFrontRegardless`, never made key. `indicator/macos.py:is_passive` checks this (the backend refuses a panel
  that fails it; `test_client_indicator.py` pins it). AppKit needs the main thread, so `hark run` pumps the AppKit
  event loop there; mic, hotkey listener, ctl socket and each dictation's finish run on their own threads.
- Linux: GTK 4 + gtk4-layer-shell via PyGObject, if importable: an overlay layer surface anchored to the bottom (or
  top) edge, 24 px in, `KeyboardMode.NONE`, empty input region (Hyprland, sway, KDE). Colours and text size come
  from the running Omarchy theme (`~/.local/state/omarchy/current/theme/colors.toml`, re-read when it changes; inks
  solved to WCAG contrast); glyphs need a Nerd Font as `monospace` (Omarchy's default). Needs `gtk4 gtk4-layer-shell` plus
  PyGObject and pycairo in hark's Python: the `hark-git` package has them; from a checkout `uv sync --extra gi`
  (builds them: `gobject-introspection cairo pkgconf base-devel`). Without them, or without layer-shell (GNOME), there is no indicator. **Untested on real hardware.**

## Recovery, history and re-insertion

The daemon keeps the audio of the last `retry.keep` (10) dictations in `<history.dir>/retry/<id>/` (0700/0600; the
only audio hark keeps; older ones are deleted after each dictation, all of them with `keep = 0`): the WAV,
the key-down context without selection, window title or agent reply, and the outcome. After a server error or
timeout the notification says `hark retry`:

```sh
hark retry                    # re-run the newest kept dictation against the server
hark retry 9f3c1a2b --copy    # by id (from `hark history`); copy the result instead of pasting
hark retry last --dry-run     # print only
```

A retry uses the original context (bias terms, profile, target window) and pastes only if that window is focused
again; otherwise the text goes to the clipboard (also when the window is unknown or `paste.check_focus = false`).
It is a new history entry; the kept audio stays.

```sh
hark history                  # last 20: time, id, app, status, release->paste latency, text preview
hark history -n 100 --json    # the full records
hark history show 9f3c1a2b    # one dictation: raw, canonical + replacements, cleanup edits/dropped,
                              # gate decisions (if recorded), final, spacing decision, paste,
                              # latency (key release -> text sent), timings
hark last                     # print the last dictated text; --copy puts it on the clipboard
hark ctl paste-last           # daemon: paste it again into the focused window (bind it to a key)
```

`paste-last` re-applies the guardrails for the window it goes to (a prose result pasted into a terminal loses its
newlines); its reply gives only the length, never the text.

Accuracy regressions: `hark eval add` puts a kept dictation into a private set of real clips, `hark eval run`
re-runs them all and fails on a lost identifier, a false substitution or a lost negation
([docs/eval.md](../docs/eval.md)). It ends with the median and max release->paste latency; `--report` has every
clip's `timings_ms`.

## Guardrails

- All profiles: escape sequences (CSI incl. the bracketed-paste markers `ESC[200~`/`ESC[201~`, OSC, 8-bit CSI), C0/C1
  control characters and bidi overrides are removed.
- Terminal profile: every whitespace run, newlines included, becomes one space; no trailing newline. Nothing ever sends Enter.
- Terminal profile, before cleanup: spoken symbols are written as symbols, conservatively (`hark_client/symbols.py`):
  "canon dot py" → `canon.py`, "commit dash m fix colon" → `commit -m fix:`, "dash dash verbose" → `--verbose`,
  "src slash main dot rs" → `src/main.rs`, "cd slash tmp" → `cd /tmp`. A single "slash" between ordinary words
  ("the slash command") and "dash" after a non-command stay words.
- Cleanup: protected spans (shortlist identifiers, code-like tokens, numbers, negations) are sent with the request.
  The client re-validates every returned edit (unique quote, byte range, protected spans, per-kind invariant incl.
  self-corrections and spoken numbers, negation and digit preservation, at most half the words removed, no lost `!`).
  A bad edit is dropped with its group (edits at most one word apart), and a negation, digit or `!` change only
  with the edits that make it; any other failed whole-text rule keeps the text. Status `applied` (every edit),
  `partial` (some dropped; the reason gives the counts) or `rejected` (none); the history's `cleanup.dropped` lists
  each dropped edit with its reason and who dropped it (`server` or `client`). Timeouts and RPC errors fall back
  to the uncleaned text. Text that looks
  non-English (or `server.language` other than `en*`) is not sent: status `skipped`, reason `non_english`.
- Focus: if the focused window changed between key-down and paste, the text goes to the clipboard and a notification
  says so.

## Smart continuation (spacing)

Right before pasting (after the focus check), hark looks at the text around the caret
(`hark_client/context/caret.py`, `hark_client/continuation.py`), like Spokenly's smart spacing:

- **Leading space** iff the character before the caret is known, is not whitespace, not an opening character
  (`(` `[` `{` `"` `'` `` ` `` `/` `-` and curly opening quotes) and the text does not start with closing punctuation (`. , ; : ! ? ) ] } %`).
- **Lower-case first word** iff the caret is mid-sentence (after a letter/digit or `, ; :`, spaces ignored) and the
  word is a common function word (And, But, So, The, When, It, ...). Never `I`, acronyms, identifiers or names.
- **Trailing space** iff the character after the caret is a letter or digit.
- Unknown context counts as the start of a field: no leading space, nothing lower-cased.

Where the caret comes from, first trusted source wins:

1. macOS Accessibility: focused element of the focused app (system-wide focused element as a fallback),
   `AXSelectedTextRange` + `AXStringForRange`, 50 ms per round trip, 200 ms overall. Secure text fields are never
   read. Terminal.app reports a real caret. Ghostty (1.4) exposes its text but the caret is always (0, 0): untrusted.
2. Linux AT-SPI (optional PyGObject with the Atspi typelib; caret offset + text of the focused accessible).
   Unit-tested with fakes only, **untested on real hardware**.
3. tmux: in a terminal whose active tab runs a tmux client, the cursor of that client's pane and its screen row.
   TUIs that hide the cursor and draw their own (coding agents, editors) or copy mode make it untrusted.
4. Shadow buffer (daemon only): hark's last paste into that window plus the keys the hotkey listener saw since.
   Characters append, Backspace pops (past the pasted text: unknown), Option/Ctrl+Backspace pops a word,
   Cmd+Backspace, Ctrl+U and (Shift+)Enter mean "line start"; arrows, Home/End, Page Up/Down, Tab, Esc, other
   Cmd/Ctrl shortcuts, mouse clicks, keys reaching another window and `join_window_ms` without input mean unknown.
   hark's own paste chord is ignored for 0.5 s after it pastes.

The history's `spacing` field records the decision: `before_repr` (last ≤16 chars), `after_repr`, `source`
(`ax`, `atspi`, `tmux`, `shadow`, `none`), `detail` (why a source was missing/untrusted), `leading`, `trailing`,
`lowercased`; `joined` = `leading`.

Limitations: Wayland gives clients no global key events, so there the caret comes only from AT-SPI or tmux (no
shadow buffer). The shadow buffer cannot see autocomplete, IME composition, mouse selection or edits made by the
app itself; the AX caret in a terminal is the terminal cursor, which a TUI may park elsewhere. Wide/combined
characters in tmux rows are mapped to columns approximately.

## Dictionary

For a handful of spellings no context will supply (`Hyprland` for "hyper land"), list them as `[[words]]`
(`hark_client/context/words.py`): every dictation gets them, ahead of the context terms, through the same gate and
canonicalization rules.

## Anchor gate

With bias terms the server returns the biased decode and an unbiased anchor of the same audio
(`hark_client/gate.py`). Where they differ, the biased words survive only if they are a bias hit, the anchor heard
something close to the term (`max_distance`) without being sure of its own word (`confident_anchor`), and the
change adds/removes no negation or number, is not just an inflection and drops no function word. Everything else,
including any change outside a hit, goes back to the anchor's words. `history.jsonl` records each region as
`gate.decisions[] = {decision: kept|reverted, reason, biased, anchor, term, distance}`. Measurements:
[docs/bench/2026-11-12-acoustic-gate.md](../docs/bench/2026-11-12-acoustic-gate.md).

## History

`history.jsonl` gets one line per dictation (not for presses shorter than `min_ms`): status, profile, app, raw/canonical/final text, replacements, cleanup
outcome, guard events, paste result and `delivery`, the anchor gate's decisions (`gate`, see below), client timings per stage (`context_ms`, `session_start_ms`, `accepted_ms`,
`keyup_to_final_ms`, `gate_ms`, `canonicalize_ms`, `cleanup_ms`, `guard_ms`, `focus_check_ms`, `spacing_ms`, `paste_ms`,
`keyup_to_paste_ms`, and `release_to_paste_ms`: from the key release, monotonic clock, to the text sent; this is
what you wait, including the recovery copy and the wait for the key-down context that the other stage timings
start after), the smart-spacing decision (`spacing`, see above),
wall-clock marks `client_unix_us` (`keyup`, `end_enqueued`, `end_dequeued` by the gRPC request iterator, `end_sent`,
`final_received`; comparable with the server's `server_end_received_unix_us` / `server_final_sent_unix_us`)
the server's `Timings`, and which provider served it (`provider`: name, address, send_context) with the `route`
(each provider tried: state, rtt_ms, cached, detail). Files are created 0600 in a 0700 directory.

## Tests

```sh
uv run pytest client/tests -q     # ~60 tests, ~2 s
```

`hark once` runs end to end against an in-process hark server (mock backend, on its own event-loop thread) with a
fake OpenAI-compatible cleanup endpoint behind it, and a scripted context engine: biasing, cleanup, terminal profile,
cleanup timeout fallback, late context, focus change, unreachable server. Plus guardrail tables, the protected-span
fallback against a misbehaving TextCleanup server, config parsing, the ctl socket protocols (text + JSON v1
contract), Linux paste commands incl. the Hyprland in-compositor re-check (fake runner), the Omarchy adapter (fake
`hark`/`systemctl`), the daemon controller (pre-roll, cancel, too short), dictionary terms reaching the server, the
AT-SPI search (fakes), the indicator state machine (fake backend) and the macOS panel's focus safety, the retry ring
(retention, permissions, purge) and `hark retry` after a failed dictation, history formatting, `hark last` and
`paste-last`; the hotkey state machine (tap/hold, Esc swallowing via the tap filter, extra keys) with fake key
events, the start cue on live audio, release->paste latency, the profile override, pause, the menu-bar state icon, Hark.app's Info.plist, and `hark service`
(launchd plists, install/status/restart/uninstall against a fake launchctl, the login-item LaunchAgent fallback).

## Limitations

- Context: without Accessibility permission macOS gives no window title/selection; the terminal/repo/agent context still works. `hark once --cwd DIR` pins the repo for testing; with no focused terminal and no `--cwd` there are no repo bias terms.
- Untested on real hardware: Hyprland/wtype/X11 paste incl. the `hyprctl repl` re-check (commands are unit-tested
  only), the Omarchy adapter/installer, the macOS Cmd+V path and the
  pynput hotkey (need permissions the dev sessions did not have), long-running daemon use.
- Cleanup only via the hark `TextCleanup` service; no direct OpenAI-compatible adapter yet (ADR-001 #6).
- No VAD, no `EagerEnd`, no partials, no automatic retry of a failed dictation on the next provider (the next
  dictation moves on; `hark retry` re-runs the failed one), no `ContextUpdate`.
- Untested on real hardware: Esc swallowing (macOS tap filter, X11 grab), the Hyprland submap.
- Clipboard restore on Linux restores text only.
