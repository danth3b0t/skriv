# hark-client (POC)

Push-to-talk dictation client for a [hark.v1](../proto/hark/v1/hark.proto) server
([ADR-001](../docs/protocol/ADR-001-protocol.md)). Hold a key, speak, release: the text is pasted into the focused app.

```
key down ─┬─ context.collect() (thread, 150 ms deadline) ──► SessionStart (bias list)
          └─ Transcribe stream opens; 40 ms PCM16 chunks (+ pre-roll) queue behind SessionStart
key up ───► EndOfAudio ► FinalResult ► canonicalize ► spoken symbols (terminal) ► cleanup (optional, deadline) ► guardrails
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

Control protocol: one line in (`start|stop|toggle|cancel|status|ping|paste-last`), one line out (`ok …` or `error …`).
`hark ctl` exits 0 on `ok`, 1 on `error`, 2 if no daemon is running. Status bars and scripts can use the versioned
JSON protocol on the same socket (`hark ctl --json status`, spec: [ctl-v1](../docs/protocol/ctl-v1.md)): state,
the last dictation's status/paste verdict/latency (never its text) and whether learning is watching.

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
min_ms = 300                  # shorter presses are cancelled (short "cancel" sound, nothing pasted)
min_speech_ms = 300           # less speech energy than this is discarded too (status no_speech); 0 = off
max_ms = 120000

[hotkey]
key = "alt_r"                 # pynput Key name (alt_r = Right Option, cmd_r, f13, ...) or one character
listener = "auto"             # auto (pynput on macOS/X11) | pynput | none

[paste]
method = "auto"               # auto | macos | hyprland | wtype | x11 | clipboard | dry-run
restore_clipboard = true
check_focus = true            # focus changed since key-down => copy + notify instead of pasting
smart_spacing = true          # leading/trailing space + mid-sentence lower case from the caret (see below)
join_space = true             # older name; false also turns smart spacing off
join_window_ms = 60000        # shadow buffer (no readable caret) forgets after this long without input
verify = true                 # read the field back after pasting: landed | missing | not_text | unknown (recorded)

[learn]                       # correction learning, see below; off unless enabled
enabled = false
window_ms = 30000             # watch the pasted text this long after a paste (max 30 s)
poll_ms = 500
promote_after = 2             # a correction is used after this many occurrences (min 2)
max_terms = 20                # learned terms added to every shortlist

[feedback]
sounds = true                 # afplay (macOS), pw-play/paplay (Linux)
notify = true

[history]
dir = "~/.local/share/hark"
save_recordings = false       # opt-in dataset seeding, see below

[retry]
keep = 10                     # audio of the last N dictations for `hark retry` (0 = off), see below

[indicator]
enabled = true                # small passive overlay: recording/level, transcribing, cleaning, done/error
position = "bottom"           # bottom | top, centred on the screen with the pointer (macOS)

[profiles.terminal]           # built-ins: terminal, chat, prose (fallback); table order = match order
apps = ["com.mitchellh.ghostty", "kitty", "*terminal*"]   # globs on app id / name, case-insensitive
terminal = true               # single line, Ctrl+Shift+V on Linux, never Enter
cleanup = false
cleanup_profile = "prose"     # server CleanupProfile.id
```

A profile is chosen by the first `apps` match, else `terminal` if the context engine reports a terminal, else `prose`.

## Recording indicator

While `hark run` works, a small pill shows its state: **Listening** with the live input level, **Transcribing…**,
**Cleaning up…**, then **Pasted** / **Copied** / **No speech** / **Cancelled** / **Failed: hark retry** for a moment.
It never takes focus or clicks, so the paste still goes to the window you dictated into.

- macOS: a borderless `NSPanel` with `NSWindowStyleMaskNonactivatingPanel`, click-through, status-window level, on
  every Space; the process is an accessory app (no Dock icon) that is never activated, and the panel is shown with
  `orderFrontRegardless`, never made key. `indicator/macos.py:is_passive` checks this (the backend refuses a panel
  that fails it; `test_client_indicator.py` pins it). AppKit needs the main thread, so `hark run` pumps the AppKit
  event loop there; mic, hotkey listener, ctl socket and each dictation's finish run on their own threads.
- Linux: GTK 4 + gtk4-layer-shell via PyGObject, if importable: an overlay layer surface anchored to the bottom (or
  top) edge, `KeyboardMode.NONE`, empty input region (Hyprland, sway, KDE). Needs `gtk4 gtk4-layer-shell` plus
  PyGObject and pycairo visible to hark's Python (e.g. `uv pip install PyGObject pycairo` with the introspection
  headers). Without them, or without layer-shell (GNOME), there is no indicator. **Untested on real hardware.**

## Recovery, history and re-insertion

The daemon keeps the audio of the last `retry.keep` (10) dictations in `<history.dir>/retry/<id>/` (0700/0600; also
with `save_recordings = false`; older ones are deleted after each dictation, all of them with `keep = 0`): the WAV,
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
hark history                  # last 20: time, id, app, status, key-up->paste latency, text preview
hark history -n 100 --json    # the full records
hark history show 9f3c1a2b    # one dictation: raw, canonical + replacements, cleanup edits/dropped,
                              # gate decisions (if recorded), final, spacing decision, paste, timings
hark last                     # print the last dictated text; --copy puts it on the clipboard
hark ctl paste-last           # daemon: paste it again into the focused window (bind it to a key)
```

`paste-last` re-applies the guardrails for the window it goes to (a prose result pasted into a terminal loses its
newlines); its reply gives only the length, never the text.

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

## Paste verification and correction learning

After a paste into a non-terminal app, hark reads the focused field of the target app back (macOS: Accessibility;
Linux: AT-SPI via PyGObject if installed, **untested on real hardware**). History gets `paste_verdict`: `landed`
(the text is there), `missing` (readable, non-empty, absent twice), `not_text`, or `unknown` (could not look: no
permission, terminal, secure field, empty read-back, > 64 KiB). The verdict is only recorded for now (web/Electron
fields can expose stale values); acting on `missing` waits until its false-positive rate is measured. Secure and
password fields are never read.

With `[learn] enabled = true`, the same read-back watches the pasted text for up to `window_ms` (≤ 30 s), only while
the same window stays focused, until the next dictation, or until the text around it changes (e.g. the message was
sent). If you fix a misheard word in it (`cube control` → `kubectl`, `Kerry` → `Carrie`, `github` → `GitHub`),
the pair is counted in `<history.dir>/learned.json` (0600). Field contents are never stored, only the pairs. Style
rewrites, negation changes, corrections into ordinary English words and edits touching more than 3 places or half
the words are ignored. A pair seen `promote_after` (≥ 2) times becomes a shortlist term with the misheard words as
its spoken alias, so the recognizer is biased towards it and canonicalization rewrites it.

```sh
hark learned                  # list (* = in use)
hark learned forget kubectl   # delete by heard or written form
hark learned clear
```

Terminals expose no field to read, so after a paste into a terminal the learner replays the keys the hotkey
listener sees (the same stream as the shadow buffer; macOS and X11, not Wayland) on the pasted line
(`hark_client/keyedits.py`) until Enter, the next dictation, a key reaching another window or `window_ms`. Anything
it cannot model exactly (history, completion, Home then an edit, mouse clicks, other shortcuts) drops the watch.
Key events stay in memory; only the derived pairs are stored.

## History and recordings

`history.jsonl` gets one line per dictation (not for presses shorter than `min_ms`): status, profile, app, raw/canonical/final text, replacements, cleanup
outcome, guard events, paste result and `paste_verdict`, client timings per stage (`context_ms`, `session_start_ms`, `accepted_ms`,
`keyup_to_final_ms`, `canonicalize_ms`, `cleanup_ms`, `guard_ms`, `focus_check_ms`, `spacing_ms`, `paste_ms`,
`keyup_to_paste_ms`, `verify_ms`), the smart-spacing decision (`spacing`, see above),
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
fallback against a misbehaving TextCleanup server, config parsing, the ctl socket protocols (text + JSON v1
contract), Linux paste commands incl. the Hyprland in-compositor re-check (fake runner), the Omarchy adapter (fake
`hark`/`systemctl`), the daemon controller (pre-roll, cancel, too short), paste verdicts, correction learning
(diff rules, store, watch window, learned terms reaching the server), the AT-SPI search and the terminal key replay
(fakes), the indicator state machine (fake backend) and the macOS panel's focus safety, the retry ring
(retention, permissions, purge) and `hark retry` after a failed dictation, history formatting, `hark last` and
`paste-last`.

## Limitations

- Context: without Accessibility permission macOS gives no window title/selection; the terminal/repo/agent context still works. `hark once --cwd DIR` pins the repo for testing; with no focused terminal and no `--cwd` there are no repo bias terms.
- Untested on real hardware: Hyprland/wtype/X11 paste incl. the `hyprctl repl` re-check (commands are unit-tested
  only), the Omarchy adapter/installer, AT-SPI read-back, the macOS Cmd+V path, the AX field read-back and the
  pynput hotkey (need permissions the dev sessions did not have), long-running daemon use.
- Cleanup only via the hark `TextCleanup` service; no direct OpenAI-compatible adapter yet (ADR-001 #6).
- No VAD, no `EagerEnd`, no partials, no automatic retries/fallback providers, no `ContextUpdate`, no undo/swap-to-raw.
- Esc (cancel) is not swallowed: the focused app sees it too. The start sound can leak into the recording.
- Clipboard restore on Linux restores text only.
