# skriv-client

Push-to-talk dictation client for a [skriv.v1](../proto/skriv/v1/skriv.proto) server
([ADR-001](../docs/protocol/ADR-001-protocol.md)). Hold a key, speak, release: the text is pasted into the focused app.
Tap the key instead to dictate hands-free until the next tap.

```
key down ─┬─ context.collect() (thread, 150 ms deadline) ──► SessionStart (bias list, if the provider may see it)
          └─ provider router picks a server (cached health) ──► Transcribe stream; 40 ms PCM16 chunks (+ pre-roll)
key up ───► EndOfAudio ► FinalResult ► degenerate-output check ► anchor gate ► canonicalize ► spoken symbols (terminal)
          ► self-corrections ► cleanup (deadline) + fidelity check ► guardrails
          ► focus re-check ► smart spacing ► paste (or clipboard + notification) ► history.jsonl (+ audio kept for `skriv retry`)
```

Module by module: [docs/architecture.md](../docs/architecture.md).

## Quickstart

Everything runs from the repo root (uv workspace, Python 3.12).

```sh
uv sync
uv run skriv-server --backend mock &            # or parakeet_mlx on a Mac, see server/README.md
uv run skriv doctor                             # server, mic, permissions, paste tools, daemon, context
uv run skriv once --wav some.wav --dry-run      # whole pipeline on a file, every stage printed
uv run skriv once --wav some.wav --dry-run --profile terminal
```

`skriv once` prints each stage's output and timing (context, SessionStart, ASR, canonicalize, cleanup, guard, paste, history).
Without `--dry-run` it really pastes into the focused window. Any PCM WAV works (resampled to 16 kHz mono with soxr).

### macOS

```sh
uv run skriv run          # hold Right Option to dictate, release to paste; or tap it, speak, tap again; Esc cancels
```

The app that runs skriv (your terminal) needs, in System Settings → Privacy & Security:
**Microphone**, **Input Monitoring** (global hotkey via pynput) and **Accessibility** (posting Cmd+V).
`skriv doctor` checks all three. Or use **Skriv.app** (`scripts/build-macos-app`, see
[packaging/macos](../packaging/macos/README.md)): it runs `skriv app` = `skriv run` plus a menu-bar item and a setup
window, and the permissions belong to Skriv instead of the terminal. Paste = NSPasteboard (saved and restored afterwards, including non-text items)
+ a CGEvent Cmd+V from a private event source (key code 9), so the held hotkey's modifiers do not leak into it.

### Linux / Hyprland (Wayland)

Wayland gives clients no global key grabs, so the daemon listens on a control socket
(`$XDG_RUNTIME_DIR/skriv/ctl.sock`) and the compositor binds keys to `skriv ctl`:

```sh
sudo pacman -S portaudio wl-clipboard wtype libnotify   # PortAudio for sounddevice; wtype = fallback
uv run skriv run
```

Installed from the `skriv-git` package, `skriv service install` runs the daemon as a systemd user service
(`skriv service status`, `skriv service uninstall`); see [docs/linux.md](../docs/linux.md).

Hyprland ≤ 0.54 (hyprlang), push-to-talk on F13 (`bindr` fires on release):

```ini
bind  = , F13, exec, skriv ctl start
bindr = , F13, exec, skriv ctl stop
bind  = SUPER, Escape, exec, skriv ctl cancel
```

Plain Esc as cancel, swallowed only while recording (skriv cannot grab keys on Wayland; a submap entered for the
recording can; **untested**):

```ini
bind   = , F13, exec, skriv ctl start
bind   = , F13, submap, skriv
submap = skriv
bindr  = , F13, exec, skriv ctl stop
bindr  = , F13, submap, reset
bind   = , Escape, exec, skriv ctl cancel
bind   = , Escape, submap, reset
submap = reset
```

Hyprland ≥ 0.55 (Lua config):

```lua
hl.bind("F13", hl.dsp.exec_cmd("skriv ctl start"))
hl.bind("F13", hl.dsp.exec_cmd("skriv ctl stop"), { release = true })
```

Omarchy: skriv implements Omarchy's dictation-backend contract, so Omarchy's own keys (hold Right Alt, Alt+Space
toggle) drive it with no Hyprland edits:

```sh
uv tool install --editable ./client                    # puts `skriv` on PATH
packaging/omarchy/omarchy-install-dictation-skriv       # links omarchy-dictation-skriv into ~/.local/bin,
                                                       # installs + starts skriv.service, selects skriv
omarchy dictation toggle                               # = omarchy-dictation-skriv toggle = skriv ctl toggle
```

`omarchy-dictation-skriv stop` succeeds when skriv is idle or not running; `start` with no daemon starts
`skriv.service` and fails so you press again. `packaging/omarchy/omarchy-remove-dictation-skriv` undoes the install
(config, history and learned vocabulary stay). Untested on a real Omarchy install.

`skriv` must be on `PATH` for the compositor (e.g. `uv tool install --editable ./client`, or use the full path to
`.venv/bin/skriv`). `skriv ctl` takes ~50 ms (stdlib-only imports); the 200 ms pre-roll buffer (mic kept open) covers the first
words. Zero-cost alternative: `echo start | nc -U "$XDG_RUNTIME_DIR/skriv/ctl.sock"`. `skriv ctl toggle` works for a single
tap-to-start/tap-to-stop key.

Paste: `wl-copy`, then a Ctrl+V sent to the window that was focused at key-down. With a Lua Hyprland config
(≥ 0.55) one `hyprctl repl` chunk re-checks the active window's address *inside the compositor* and sends
`send_shortcut{…, window = "address:…"}` only if it still matches (otherwise: status `focus_changed`, text left in
the clipboard, notification); with hyprlang (≤ 0.54) `dispatch sendshortcut CTRL, V, address:…`. The syntax that
worked is remembered. Unknown target: an untargeted shortcut; neither syntax works: `wtype`.
Terminals get Ctrl+Shift+V. On X11: `xclip` + `xdotool`, and `hotkey.listener = "pynput"` works with the `x11` extra.

`SKRIV_CTL_SOCKET` overrides the socket path (e.g. a second, test daemon). Control protocol: one line in
(`start|stop|toggle|cancel|status|ping|paste-last|pause|resume|profile …|reload`), one line out (`ok …` or `error …`).
`skriv ctl` exits 0 on `ok`, 1 on `error`, 2 if no daemon is running. Status bars and scripts can use the versioned
JSON protocol on the same socket (`skriv ctl --json status`, spec: [ctl-v1](../docs/protocol/ctl-v1.md)): state,
the provider in use, config changes, the last dictation's status/delivery/latency (never its text) and the profile
override; a JSON `subscribe` keeps the connection open and sends a status line on every change.

- `pause` refuses dictations and releases the mic until `resume` (both idempotent).
- `reload` re-reads the config file for the next dictation: `[[words]]`, `[context]`, `[gate]`, `[cleanup]`,
  `[feedback]`, `[profiles]`, `[retry]` and the paste keys except `method`, `restore_clipboard`,
  `restore_delay_ms` and `join_window_ms`. Everything else (`[[providers]]`, `[server]`, `[audio]`, `[hotkey]`, …)
  is named in the reply and in status as needing a restart; a config that does not load is refused and the old one
  stays.

## Configuration

`~/.config/skriv/config.toml` (or `--config FILE`; `--server ADDRESS` = one provider at that address instead of
`[[providers]]`). All keys are optional;
unknown keys and wrong types are errors.

```toml
log_level = "INFO"            # -v / --verbose: DEBUG

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
resolve_corrections = true    # apply spoken self-corrections ("in C, no, in Rust") before the model, every profile

[audio]
device = ""                   # PortAudio name or index
preroll_ms = 200              # needs keep_open
keep_open = true              # mic stream stays open between dictations
min_ms = 300                  # shorter presses are cancelled (short "cancel" sound, nothing pasted)
min_speech_ms = 300           # less speech energy than this is discarded too (status no_speech); 0 = off
max_ms = 300000               # 5 min, pre-roll included (the server's limit); the pill counts down the last 30 s

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
dir = "~/.local/share/skriv"  # $XDG_DATA_HOME/skriv
enabled = true                # false: no history.jsonl

[retry]
keep = 10                     # audio of the last N dictations for `skriv retry` (0 = off), see below

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

### Coming from hark (the old name)

The first `skriv` command moves `~/.config/hark` to `~/.config/skriv` and the data dir (`$XDG_DATA_HOME/hark`,
`~/.local/share/hark`) to `.../skriv`, once, and never over an existing directory. By hand:

- Commands: `hark` → `skriv`, `hark-server` → `skriv-server`, `hark-probe` → `skriv-probe`; environment
  variables `HARK_*` → `SKRIV_*` (e.g. `SKRIV_CTL_SOCKET`); a repository's `.hark/words.toml` → `.skriv/words.toml`.
- Linux services: with the old package still installed, `hark service uninstall` (or `systemctl --user disable --now
  hark hark-server hark-llama hark-cpu-server` and delete `~/.config/systemd/user/hark*.service`), then
  `skriv service install`. Omarchy: `omarchy-remove-dictation-hark`, then `omarchy-install-dictation-skriv`.
- macOS: `hark service uninstall` (or `launchctl bootout gui/$UID/dev.hark.server` and `.../dev.hark.llama`), then
  `skriv service install`. **Skriv.app** has a new bundle id (`dev.skriv.Skriv`): grant Microphone, Accessibility and
  Input Monitoring again, and remove Hark.app from those lists.

### Migrating an older config

These keys were removed; a config that still has them fails to load (`skriv: config error: unknown config key …`,
exit 2). Delete them:

| Key | Now |
|---|---|
| `[server]` `address`, `tls_ca`, `tls_cert`, `tls_key`, `tls_server_name`, `model_id` | the same keys in a `[[providers]]` entry; `[server]` keeps `language`, `finalize_budget_ms`, `final_timeout_ms` |
| `[learn]` (correction learning) | gone; add a spelling with `skriv words add` |
| `[profile_switch]` (`sticky`) | gone; `skriv ctl profile set NAME` is for the next dictation only |
| `hotkey.undo_key`, `hotkey.swap_key`, `hotkey.profile_key` | gone, with `skriv ctl undo`, `swap-raw` and `profile next`; `skriv last --raw --copy` gives the uncleaned text |
| `paste.verify` | gone (no read-back after the paste) |
| `history.save_recordings` | gone; the retry ring (`retry.keep`) is the only audio kept, `skriv eval add` copies a dictation into an eval set |
| `feedback.cue_mask_ms` | gone; the start sound plays on the first recorded audio and masks nothing |

The built-in `chat` profile is gone too. A `[profiles.chat]` table still loads, but as a new profile with every
field at its default (no cleanup): give it `apps`, `cleanup = true` and `cleanup_profile = "chat"` (the server still
has that cleanup profile) or delete it.

## Hands-free, cancel and profiles

- **Tap or hold** (listener: macOS/X11): a press released within `tap_ms` (250 ms) keeps recording hands-free (the
  indicator says **Hands-free**); the next press stops at once. Held longer it is push-to-talk. A press while a
  recording started by `skriv ctl` runs stops it. On Wayland bind `skriv ctl toggle` (tap) or start/stop (hold).
- **Esc** cancels any recording (hotkey, hands-free or `skriv ctl start`), and a stopped dictation until its paste
  starts (`skriv ctl cancel` too; on Wayland the submap ends at release, so use the always-bound `SUPER+Escape`).
  One dictation at a time: pressing the hotkey while the last one is still being transcribed records nothing. With `swallow_esc` the focused app never
  sees that Esc keystroke (down, auto-repeat, up); every other key, and Esc while idle, passes untouched. macOS:
  the pynput event tap becomes a filter (`darwin_intercept`), which needs **Accessibility** (without it skriv logs a
  warning and stays listen-only); its callbacks only update the key state, the actions run on a worker thread so
  typing is never held up. X11: a passive grab of Esc exists only while recording or finishing (**untested on
  real hardware**).
  Wayland: a compositor submap, see the Hyprland example above.
- **Start sound**: off by default; the indicator (Listening) is the signal. With `start_cue = "always"` (or
  `"auto"` and no indicator) it plays when the recording's first audio arrives, so anything said after it is
  captured. The sound is recorded like everything else; no audio is ever replaced or dropped.
- **Profile**: `skriv ctl profile set NAME` picks the profile of the next dictation only; `skriv ctl profile auto`
  goes back to choosing by app. The indicator shows **Profile: prose** briefly (never over a recording) and the
  recording as **Listening · prose**; `skriv ctl profile` and `skriv ctl --json status` (`profile_override`) report
  it. Skriv.app's menu-bar Profile menu sets the same override (Automatic = `profile auto`).

## Providers and the offline fallback

Without `[[providers]]` there is one provider, `127.0.0.1:50051` (`address` also takes `unix:/path`). With them,
every dictation goes to the first healthy server in `priority` order (lower first, then file order), e.g. the desktop GPU over Tailscale, then the laptop's own CPU server:

```toml
[[providers]]
name = "desktop"
address = "desktop.tailnet:50051"
tls_ca = "~/.config/skriv/ca.pem"     # set => TLS; tls_cert + tls_key => mTLS
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

- **Health**: a gRPC health check of `skriv.v1.Asr` bounded by `max_latency_ms`: SERVING in time is *ok*, no answer in
  time is *slow* (also what a blackholed host looks like), refused/NOT_SERVING/TLS errors are *down*. Results are
  cached (ok 30 s, slow/down 5 s) and `skriv run` re-checks every 10 s in the background, so key-down normally waits
  for nothing; a stale cache costs at most `max_latency_ms` per skipped provider, spent while the context is collected
  and with the audio buffered.
- **Per dictation**, at session start: the first *ok* provider takes it. If none is ok, the first is used anyway and
  its error reaches you (`skriv retry` re-runs the audio). A dictation that gets no answer at all (stream error,
  timeout) marks its provider down, so the next one moves on. There is no switch mid-dictation.
- **Privacy**: `send_context = false` sends audio only; the shortlist stays on this machine and is still used to
  canonicalize the result. Cleanup on such a provider gets the transcript and which spans of it to protect.
- The CPU server has no bias input (bias `NONE`) and no TextCleanup, so dictations it serves are canonicalized
  client-side and not cleaned. Latency: [docs/bench/2026-11-12-transcribe-cpp-cpu.md](../docs/bench/2026-11-12-transcribe-cpp-cpu.md).
- `skriv doctor` lists every provider (health, latency, models, bias mechanism, cleanup) and which one the next
  dictation would use; `skriv history show ID` and `history.jsonl` (`provider`, `route`, `timings_ms.route_ms`) record
  which one served and why.

Running the CPU server (Linux laptop; also macOS for testing):

```sh
uv sync && scripts/fetch-transcribe          # builds transcribe.cpp 0.4.2 into build/, fetches the GGUF into models/
scripts/cpu-server-up                        # 127.0.0.1:50052, CPU; --device metal|vulkan|cuda, --threads N
# as a systemd user service:
sed "s|@REPO@|$PWD|g" packaging/systemd/skriv-cpu-server.service > ~/.config/systemd/user/skriv-cpu-server.service
systemctl --user daemon-reload && systemctl --user enable --now skriv-cpu-server
```

## Recording indicator

While `skriv run` works, a small pill shows its state (design/SPEC.md §4): a red dot (a lock when hands-free, plus a
hand-picked profile) with the live input level and, after a minute, the elapsed time (**0:25 left** in the last 30 s
before `audio.max_ms`), three dots while working, then the outcome for a moment: ✓ (sent,
with at most one tag: *mic dropped*, *cleanup skipped*, *via laptop*; or *Stopped at 5:00*), **Copied**, **Paste
failed · copied**, **Failed · server timeout**, **No speech**, **Cancelled**. **No speech** names the input it
listened to and `skriv doctor`; it is not an error (a press with nothing said, a pause to think: silence inside a
dictation never discards it, only too little speech in all of it). When every sample was the same value (a muted or
dead input) it says **No audio from mic** instead, and a notification says which device and to run `skriv doctor`
(which flags a flat signal too); history records `input_device` and `flat_signal`. A press that starts nothing says why:
**Paused**, **Mic lost**, or the working dots while the previous dictation finishes. On Omarchy, failure pills name
the key that opens skriv's panel (**Super Alt D**); elsewhere a failure names `skriv retry` when its audio is kept.
It never takes focus or clicks, so the paste still goes to the window you dictated into.

- macOS: a borderless `NSPanel` with `NSWindowStyleMaskNonactivatingPanel`, click-through, status-window level, on
  every Space; the process is an accessory app (no Dock icon) that is never activated, and the panel is shown with
  `orderFrontRegardless`, never made key. `indicator/macos.py:is_passive` checks this (the backend refuses a panel
  that fails it; `test_client_indicator.py` pins it). AppKit needs the main thread, so `skriv run` pumps the AppKit
  event loop there; mic, hotkey listener, ctl socket and each dictation's finish run on their own threads.
- Linux: GTK 4 + gtk4-layer-shell via PyGObject, if importable: an overlay layer surface anchored to the bottom (or
  top) edge, 24 px in, `KeyboardMode.NONE`, empty input region (Hyprland, sway, KDE). Colours and text size come
  from the running Omarchy theme (`~/.local/state/omarchy/current/theme/colors.toml`, re-read when it changes; inks
  solved to WCAG contrast); glyphs need a Nerd Font as `monospace` (Omarchy's default). Needs `gtk4 gtk4-layer-shell` plus
  PyGObject and pycairo in skriv's Python: the `skriv-git` package has them; from a checkout `uv sync --extra gi`
  (builds them: `gobject-introspection cairo pkgconf base-devel`). Without them, or without layer-shell (GNOME), there is no indicator. **Untested on real hardware.**

## Recovery, history and re-insertion

The daemon keeps the audio of the last `retry.keep` (10) dictations in `<history.dir>/retry/<id>/` (0700/0600; the
only audio skriv keeps; older ones are deleted after each dictation, all of them with `keep = 0`): the WAV,
the key-down context without selection, window title or agent reply, and the outcome. After a server error or
timeout the notification says `skriv retry`:

```sh
skriv retry                    # re-run the newest kept dictation against the server
skriv retry 9f3c1a2b --copy    # by id (from `skriv history`); copy the result instead of pasting
skriv retry last --dry-run     # print only
```

A retry uses the original context (bias terms, profile, target window) and pastes only if that window is focused
again; otherwise the text goes to the clipboard (also when the window is unknown or `paste.check_focus = false`).
It is a new history entry; the kept audio stays.

```sh
skriv history                  # last 20: time, id, app, status, release->paste latency, text preview
skriv history -n 100 --json    # the full records
skriv history show 9f3c1a2b    # one dictation: raw, canonical + replacements, cleanup status/reason,
                              # gate decisions (if recorded), final, spacing decision, paste,
                              # latency (key release -> text sent), timings
skriv last                     # print the last dictated text; --copy puts it on the clipboard
skriv last --raw --copy        # its text before cleanup (spoken symbols, self-corrections and guardrails
                              # applied, no LLM rewrite), e.g. when the cleanup changed too much
skriv ctl paste-last           # daemon: paste it again into the focused window (bind it to a key)
```

`paste-last` re-applies the guardrails for the window it goes to (a prose result pasted into a terminal loses its
newlines); its reply gives only the length, never the text.

Accuracy regressions: `skriv eval add` puts a kept dictation into a private set of real clips, `skriv eval run`
re-runs them all and fails on a lost identifier, a false substitution or a lost negation
([docs/eval.md](../docs/eval.md)). It ends with the median and max release->paste latency; `--report` has every
clip's `timings_ms`.

## Guardrails

- All profiles: escape sequences (CSI incl. the bracketed-paste markers `ESC[200~`/`ESC[201~`, OSC, 8-bit CSI), C0/C1
  control characters and bidi overrides are removed.
- Terminal profile: every whitespace run, newlines included, becomes one space; no trailing newline. Nothing ever sends Enter.
- Terminal profile, before cleanup: spoken symbols are written as symbols, conservatively (`skriv_client/symbols.py`):
  "canon dot py" → `canon.py`, "commit dash m fix colon" → `commit -m fix:`, "dash dash verbose" → `--verbose`,
  "src slash main dot rs" → `src/main.rs`, "cd slash tmp" → `cd /tmp`. A single "slash" between ordinary words
  ("the slash command") and "dash" after a non-command stay words.
- Cleanup: the server returns a rewrite of the whole text (S1-mini: fillers out, casing, punctuation, light
  rewording, spoken numbers as digits). Only the text is sent; the shortlist stays here. The rewrite replaces the text
  only if it passes one fidelity check (`skriv_client/fidelity.py`, also used by `skriv eval`): shortlist identifiers and
  code-like tokens keep their spelling and count (a re-cased one is put back first), the same number of negations and
  numbers (`seven` = `7`), no content words added or dropped beyond about one in eight, no new line break in a
  terminal profile. A spoken self-correction the rewrite resolved ("3 seconds, sorry, 5 seconds") is checked
  against the corrected text. Status `applied`, `no_edits` (the rewrite equals the text) or `rejected` (the text
  stays; `reason` names the failed rule, `rewrite` keeps the rejected text in the history). Timeouts and RPC errors
  fall back to the uncleaned text. `server.language` other than `en*`, or text the server finds non-English, is not
  cleaned: status `skipped`.
- Focus: if the focused window changed between key-down and paste, the text goes to the clipboard and a notification
  says so.

## Smart continuation (spacing)

Right before pasting (after the focus check), skriv looks at the text around the caret
(`skriv_client/context/caret.py`, `skriv_client/continuation.py`), like Spokenly's smart spacing:

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
4. Shadow buffer (daemon only): skriv's last paste into that window plus the keys the hotkey listener saw since.
   Characters append, Backspace pops (past the pasted text: unknown), Option/Ctrl+Backspace pops a word,
   Cmd+Backspace, Ctrl+U and (Shift+)Enter mean "line start"; arrows, Home/End, Page Up/Down, Tab, Esc, other
   Cmd/Ctrl shortcuts, mouse clicks, keys reaching another window and `join_window_ms` without input mean unknown.
   skriv's own paste chord is ignored for 0.5 s after it pastes.

The history's `spacing` field records the decision: `before_repr` (last ≤16 chars), `after_repr`, `source`
(`ax`, `atspi`, `tmux`, `shadow`, `none`), `detail` (why a source was missing/untrusted), `leading`, `trailing`,
`lowercased`; `joined` = `leading`.

Limitations: Wayland gives clients no global key events, so there the caret comes only from AT-SPI or tmux (no
shadow buffer). The shadow buffer cannot see autocomplete, IME composition, mouse selection or edits made by the
app itself; the AX caret in a terminal is the terminal cursor, which a TUI may park elsewhere. Wide/combined
characters in tmux rows are mapped to columns approximately.

## Dictionary

For a handful of spellings no context will supply (`Hyprland` for "hyper land"), list them as `[[words]]`
(`skriv_client/context/words.py`): every dictation gets them, ahead of the context terms, through the same gate and
canonicalization rules. One command fixes a spelling:

```sh
skriv words add "hyper land" Hyprland        # [SPOKEN] WORD into your config; a running daemon reloads
skriv words add "pie" pi --repo              # this git repository only: <repo>/.skriv/words.toml
skriv words                                  # your list, and this repository's
skriv words remove Hyprland                  # (--repo: from the repository's list)
```

Adding a word that is already listed adds the spoken form to it. The file is edited as text (the new `[[words]]`
table goes at the end, a removed one goes with its blank line before it; your comments and layout stay), then
re-read: if it no longer loads it is put back as it was. A repository's list (only `[[words]]` tables) is read at
each dictation whose context engine reports that repository, after yours. Yours and the repository's together are
capped at a third of `context.max_terms` (10); `skriv words add` refuses past it and says so.

## Anchor gate

With bias terms the server returns the biased decode and an unbiased anchor of the same audio
(`skriv_client/gate.py`). Where they differ around a bias hit, the biased words survive only if the anchor heard
something close to the term (`max_distance`) without being sure of its own word (`confident_anchor`), and the
change adds/removes no negation or number, is not just an inflection and drops no function word; otherwise that
region goes back to the anchor's words. A difference outside every hit keeps the biased words (the bias did not
act there). Every biased word also gets the anchor's confidence, which canonicalization reads. `history.jsonl`
records each region as `gate.decisions[] = {decision: kept|reverted, reason, biased, anchor, term, distance}`.
Measurements: [acoustic gate](../docs/bench/2026-11-12-acoustic-gate.md) (thresholds) and
[gate and self-corrections](../docs/bench/2026-11-13-acc-gate-corrections.md) (current behaviour).

## History

`history.jsonl` gets one line per dictation (not for presses shorter than `min_ms`): status, profile, app, raw/canonical/final text, replacements, cleanup
outcome (with a rejected `rewrite`), `uncleaned_text`, guard events, paste result and `delivery`, the anchor gate's decisions (`gate`, see below), client timings per stage (`context_ms`, `session_start_ms`, `accepted_ms`,
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
uv run pytest client/tests -q     # ~650 tests
```

`skriv once` runs end to end against an in-process skriv server (mock backend, on its own event-loop thread) with a
fake OpenAI-compatible cleanup endpoint behind it, and a scripted context engine: biasing, cleanup, terminal profile,
cleanup timeout fallback, late context, focus change, unreachable server. Plus guardrail tables, the fidelity check
against unfaithful rewrites, self-corrections, spoken symbols, canonicalization traps, config parsing and
`skriv words`, `skriv eval`, the ctl socket protocols (text + JSON v1
contract), Linux paste commands incl. the Hyprland in-compositor re-check (fake runner), the Omarchy adapter (fake
`skriv`/`systemctl`), the daemon controller (pre-roll, cancel, too short), dictionary terms reaching the server, the
AT-SPI search (fakes), the indicator state machine (fake backend) and the macOS panel's focus safety, the retry ring
(retention, permissions, purge) and `skriv retry` after a failed dictation, history formatting, `skriv last` and
`paste-last`; the hotkey state machine (tap/hold, Esc swallowing via the tap filter) with fake key
events, the Omarchy bar plugin's rules (`Model.js` under Node, skipped without `node`), the start cue on live audio, release->paste latency, the profile override, pause, the menu-bar state icon, Skriv.app's Info.plist, and `skriv service`
(launchd plists, install/status/restart/uninstall against a fake launchctl, the login-item LaunchAgent fallback).

## Limitations

- Context: without Accessibility permission macOS gives no window title/selection; the terminal/repo/agent context still works. `skriv once --cwd DIR` pins the repo for testing; with no focused terminal and no `--cwd` there are no repo bias terms.
- Untested on real hardware: Hyprland/wtype/X11 paste incl. the `hyprctl repl` re-check (commands are unit-tested
  only), the Omarchy adapter/installer, the macOS Cmd+V path and the
  pynput hotkey (need permissions the dev sessions did not have), long-running daemon use.
- Cleanup only via the skriv `TextCleanup` service; no direct OpenAI-compatible adapter yet (ADR-001 #6).
- No VAD, no `EagerEnd`, no partials, no automatic retry of a failed dictation on the next provider (the next
  dictation moves on; `skriv retry` re-runs the failed one), no `ContextUpdate`.
- Untested on real hardware: Esc swallowing (macOS tap filter, X11 grab), the Hyprland submap.
- Clipboard restore on Linux restores text only.
