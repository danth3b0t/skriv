# ADR-002: Which Superwhisper-for-Linux ideas hark adopts

Status: accepted (2026-11-12). Input: `docs/research/superwhisper-linux-internals.md` (§9 ranked ideas, §10 things to
avoid), ADR-001, `docs/00-context.md`. Daily driver: Linux laptop without GPU (Hyprland/Omarchy) + RTX 3060 desktop;
the Mac is the dev box. The client is still the Python POC; "C" below means the client, wherever it ends up (the
Rust client in `00-context.md` inherits these decisions).

Scoring: value and effort are relative (L/M/H); risk is the chance of harming correctness, privacy or the latency
target. "Fit" says where it lands in hark's split (client / server / protocol / packaging).

## Decisions

| # | Idea | Value | Effort | Risk | Decision |
|---|---|---|---|---|---|
| X | Correction learning from post-paste edits (privacy-scoped) | H | M | M (privacy) | **adopt now** |
| 5 | Omarchy `omarchy-dictation-hark` contract | H | L | L | **adopt now** |
| 4 | Hyprland-internal paste targeted at the captured window, with in-compositor re-check | H | L | L | **adopt now** |
| 8 | Paste verification (`landed / missing / not_text / unknown`) | M | L | L | **adopt now** (macOS AX; Linux AT-SPI untested) |
| 8b | AX/AT-SPI cursor context (char before/after the caret for join spacing) | M | L | L | adopt later (reuses the field reader below) |
| 9 | Versioned JSON control protocol + contract tests | M | L | L | **adopt now** |
| 6 | Work during speech / prewarm | H | M | L | partly done; cleanup prefill **adopt later** |
| 7 | Warm mic via WirePlumber `fast-capture-start` | L | L | L | adopt later (docs only, for `keep_open = false`) |
| 1 | Phoneme-based acoustic vocabulary verification | H | H | M | adopt later (design below) |
| 2 | transcribe.cpp Parakeet CPU fallback | H | M | L | implemented (`feat/m1-cpu`, see §2) |
| 3 | fcitx5 commit-based insertion | M | M | M | adopt later (design below) |
| 12 | Signed update manifest, versioned installs, rollback | M | M | L | adopt later, at packaging time (principles fixed now) |

### X. Correction learning from post-paste edits — adopt now
- **Value.** hark's biggest accuracy lever after context is *the user's own corrections*: the same name misheard
  twice is the clearest signal there is. Superwhisper learns from post-paste edits; hark already has
  `TermSource.LEARNED` (weight 1.0) and `ALIAS_KIND_MISRECOGNITION` in the protocol with nothing feeding them.
- **Privacy scope (the coordinator's conditions, all enforced in code):** opt-in (`[learn] enabled = false` by
  default); local only; active only for `window_ms` (≤ 30 s, clamped) after a hark paste and only while the *same
  focus target* (window id) stays focused; stops when a new dictation starts. Field text is held in memory only
  during that window. Secure/password fields are never read. Nothing raw is persisted — only derived
  `heard -> written` pairs of ≤ 3 words each, which pass a secret scrubber. Pairs feed a learned-vocabulary store
  (`learned.json`, 0600) and are promoted to `TermSource.LEARNED` terms only after ≥ 2 occurrences
  (`promote_after`). `hark learned list|forget|clear` inspects and deletes.
- **Mechanism.** Preferred: read back the focused field (macOS AX `AXValue`, Linux AT-SPI `Text`) right after the
  paste, anchor the pasted span by the text before and after it, poll while the window lasts, and word-diff the
  pasted text against what the span became. Only *replacements* count (appends, deletions and pure re-casing of
  ordinary words are ignored); more than 3 replaced runs or > 50 % of the words changed is a rewrite, not a
  correction. Keystroke observation is the fallback for terminals only (Linux), and never persists raw keys: a
  keysym-based line-editing reconstructor (layout-independent; aborts on anything it cannot model: history,
  completion, mouse) turns the events into the edited text, which then goes through the same diff.
- **Fit.** Client-only; learned terms join the shortlist before `SessionStart`, so they bias decoding (the research
  rule: inject context into ASR) and canonicalization; no protocol change.
- **Risk.** Wrong pairs (style edits) are bounded by the similarity filter + the ≥ 2 promotion rule + `forget`.
  macOS terminals are excluded (AX of a terminal is its whole scrollback).
- **Status.** macOS AX path implemented and wired; Linux AT-SPI reader and keystroke reconstructor implemented and
  unit-tested with fakes, **untested on real hardware**. Since the merge with smart continuation the reconstructor is
  fed by the daemon's pynput hotkey listener (macOS, X11), the same key stream as the shadow buffer; the Hyprland
  key-event source is designed only (below).

### 5. Omarchy dictation-backend contract — adopt now
`omarchy-dictation-hark start|stop|toggle` maps to `hark ctl`; returns promptly; `stop` succeeds when idle or when
no daemon runs; `start` asks systemd to start `hark.service` if the daemon is down and fails (nonzero) so Omarchy
can tell the user. `omarchy-install-dictation-hark` links the adapter and the systemd user unit, checks that `hark`
is on `PATH` (Omarchy's `o.dictation_installed()` requires the backend's own program) and calls
`omarchy-dictation-use hark`. Omarchy then owns Right Alt (hold) and Alt+Space (toggle) — zero Hyprland edits.
Opt-in per integration (§10): the installer only touches `~/.local/bin` and `~/.config/systemd/user`.

### 4. Hyprland-internal paste — adopt now
Today hark checks focus with `hyprctl activewindow` and then sends a shortcut to *whatever* is focused, a TOCTOU
window of a few ms plus the clipboard write. New: with the window address captured at key-down,
(a) Lua Hyprland (≥ 0.55): one `hyprctl repl` IIFE re-checks `hl.get_active_window().address` and sends
`send_shortcut{…, window = "address:…"}` inside the compositor; verdicts `paste_sent | focus_changed |
dispatch_refused`; (b) hyprlang (≤ 0.54): `dispatch sendshortcut MODS, V, address:…` (no in-compositor check, but the
keys can only reach the target). The working syntax is cached. `focus_changed` keeps the text in the clipboard and
notifies, like the existing pre-paste check. Not adopted: the layer-shell "panel has keyboard" check (needs
`hl.get_layers()` fields we cannot verify without hardware) and session-lock detection — later.

### 8. Paste verification — adopt now; cursor context — later
After the paste, read the focused field once or twice (150 ms, 500 ms): pasted text present (normalised spaces,
quotes, dashes) → `landed`; readable, non-empty but absent → `missing`; unreadable / empty / no text field /
terminal → `unknown` or `not_text`. *Absence of evidence is never "failed".* Stored in history as
`paste_verdict`, recorded only: web/Electron fields can expose stale AX values, so re-copying the text on `missing`
waits until history shows the verdict's false-positive rate. The field reader is shared with X.
Cursor context (char before the caret → join spacing/capitalisation) reuses the same reader and replaces
`join_prefix`'s heuristic; small, next.

### 9. Versioned JSON control protocol — adopt now
`ctl.sock` keeps the one-word text protocol (compositor binds, `nc -U`). A request line starting with `{` is JSON:
`{"version": 1, "command": "status"}` → one JSON line `{"version": 1, "ok": true, …}`. `status` returns
`state`, `finishing`, `last` (status, paste verdict, timing; never text) and `learning`. Unknown versions/commands
are typed errors. Spec in `docs/protocol/ctl-v1.md`, pinned by a contract test against the real controller.
`hark ctl --json <cmd>` prints the JSON reply (Waybar/Quickshell modules).

### 6. Work during speech / prewarm — partly done, rest later
Done already: audio streams while the key is held; `parakeet_mlx` decodes sentence segments while long dictations
stream; models are warmed at server start; context collection overlaps the first 150 ms. Missing: **cleanup
prefill** at key-down (system prompt + profile, so llama-server's prompt cache is hot). Superwhisper's `prepare`
does this. It needs a server change in `cleanup.py` (owned by another agent this week), so: later. Design:
`TextCleanup.Prepare(profile_id)` unary RPC (or a `prepare_only` flag on `CleanupRequest`) that sends the static
prefix with `max_tokens = 0`/`n_predict = 0` and `cache_prompt = true`; client fires it from `Utterance.__init__`
when the profile has cleanup on; measure with `cleanup_ms` before/after. Draft-guided prompt-lookup decoding
(llama-server `--spec-type ngram`/lookup) for S1-mini's copy-heavy rewrite is a cheap server-side experiment.
Model warm on hotkey-down: only matters if a server unloads models (`MODEL_STATE_COLD`); add a `Warmup` call at
key-down when the cached capabilities say COLD.

### 7. Warm mic via WirePlumber — later (docs)
hark keeps the PortAudio stream open (`audio.keep_open = true`), so the mic is never suspended and the 200 ms
pre-roll works. `fast-capture-start` only helps users who set `keep_open = false` (mic indicator off between
dictations); document the WirePlumber ≥ 0.5.19 setting then.

### 1. Acoustic vocabulary verification (phonetic gate) — later
High value for backends without decode-time biasing (transcribe.cpp, Riva/NeMo-Speech.cpp Parakeet) and for
measuring false substitutions, but a second model, a G2P and a CTC keyword spotter are a multi-day project.
**Design.** Server-side optional capability `hark.acoustic_verify`: (1) G2P for shortlist canonicals + aliases
(espeak-ng phonemes; cache per term); (2) phoneme CTC posteriors of the utterance from
`facebook/wav2vec2-xlsr-53-espeak-cv-ft` (or a distilled int8 ONNX), computed on the same audio while it streams;
(3) CTC keyword scoring of each needle with a phoneme substitution-cost matrix → hits `{term_id, t0, t1, score,
acoustic_sim, text_sim, posterior_deficit}`; (4) transcript fusion: replace an ASR span with a term only when the
acoustic hit and the text similarity agree, never over a common-word list. Protocol: `BiasHit` gains
`acoustic_score`; `REJECTED` hits are reported for diagnostics. **Tasks:** (a) offline notebook: score hit
precision with distractor terms on the existing boost clips (`docs/bench/parakeet-mlx-boost`); (b) server module
`acoustic.py` + capability + per-stage timing `acoustic_vocab_ms`; (c) fusion in the server, gated by the client's
existing confidence rule; (d) CPU budget on the laptop (target ≤ 80 ms for 10 s audio).

### 2. transcribe.cpp CPU fallback — implemented
**Status (`feat/m1-cpu`).** Backend `transcribe_cpp` (worker child over JSON lines through transcribe.cpp's ctypes
binding; crash → that decode fails and the next restarts it; hang → killed at `decode_timeout_s`), BUFFERED, bias
`NONE` (transcribe.cpp 0.4 has no vocabulary input for Parakeet), anchor == primary. `scripts/fetch-transcribe` builds
the pinned 0.4.2 from source (tagged 2026-11-11 with a Parakeet mel-frontend fix, no release artifacts or PyPI wheels
yet; 0.4.1 has prebuilt `transcribe-native-*` archives incl. linux-x86_64-cpu-vulkan, a possible no-compiler option
for the laptop later) and fetches the pinned GGUF; `scripts/cpu-server-up` + `packaging/systemd/hark-cpu-server.service` run it. Client: `[[providers]]` with
cached health checks, priority order, `send_context`, `max_latency_ms`; shown in `hark doctor` and history. Bench:
`docs/bench/2026-11-12-transcribe-cpp-cpu.md`. Not done: the laptop bench itself, incremental decoding on CPU, a
mid-dictation switch to the next provider.

Original design:
The laptop needs an offline path. **Design:** a `transcribe_cpp` server backend (Parakeet TDT 0.6B v3 Q4_K_M GGUF,
485 MB, MIT runtime) run as a long-lived worker child over JSON lines (crash isolation, Superwhisper's pattern);
BUFFERED streaming first, word timings from the token timestamps; no biasing → pair with #1. The client's provider
registry (ADR-001) routes to `127.0.0.1` when the desktop is unreachable. **Tasks:** (a) `scripts/fetch-transcribe`
(pinned release + sha256, x86_64 AVX2 and baseline); (b) backend + capability tests with a tiny fixture model or a
fake worker; (c) bench RTF on the laptop CPU; (d) client fallback routing with health checks.

### 3. fcitx5 commit insertion — later
No clipboard damage, layout-independent, ~4× faster than paste; Omarchy runs fcitx5. Needs a C++ addon (`.so`)
built against the user's fcitx5, a unix socket (`VERSION`, `INSERT <len>\n<text>`; refuse password fields) and an
fcitx5 restart on install — impossible to build or test on the Mac. **Tasks:** (a) addon skeleton (CMake,
`Fcitx5Core`), socket in `$XDG_RUNTIME_DIR/hark-fcitx5.sock` 0600; (b) `Fcitx5Injector` in the client, first in
the Hyprland route, multi-line text into terminals still pasted; (c) installer opt-in + uninstall; (d) test on the
laptop.

### 12. Signed updates / versioned installs / rollback — later
hark is installed from git with uv today. When there is a release artifact: minisign-signed per-channel manifest
with size + sha256 (signature mandatory, unlike their bootstrap), per-version dirs + atomic `current` swap +
post-start health check with rollback, systemd user unit with `UMask=0077`, a journal of every system change for
uninstall. The systemd unit shipped now already follows the unit-level parts.

## Implemented in this ADR's branch (`feat/adopt-sw-ideas`)

| Idea | Files | Tests | Real-hardware status |
|---|---|---|---|
| X correction learning | `client/src/hark_client/learn.py`, `fields.py`, `context/_macos.py` (`focused_field`), `atspi.py`, `keyedits.py`; wiring in `pipeline.py`, `daemon.py`, `config.py` (`[learn]`), `__main__.py` (`hark learned`) | `test_client_learn.py`, `test_client_fields.py`, `test_client_keyedits.py`, daemon + `hark once` integration | macOS AX path not exercised (the dev session had no Accessibility permission); AT-SPI and key replay: fakes only; key-event source: pynput listener (shared with the shadow buffer), Hyprland design only |
| 5 Omarchy contract | `packaging/omarchy/omarchy-{dictation,install-dictation,remove-dictation}-hark`, `packaging/systemd/hark.service` | `test_omarchy_adapter.py` (fake `hark`/`systemctl`) | not run on Omarchy |
| 4 Hyprland paste | `inject.py` (`hypr_lua_paste`, `LinuxInjector._hypr_targeted`), `pipeline.py` | `test_client_inject.py` (fake runner) | not run on Hyprland; `hyprctl repl` output format assumed (quotes stripped) |
| 8 paste verification | `fields.py`, `pipeline.py` (`_read_back`), history `paste_verdict` | `test_client_fields.py`, daemon tests | as X |
| 9 ctl JSON v1 | `ctl.py`, `daemon.py` (`status_json`), `hark ctl --json`, `docs/protocol/ctl-v1.md` | `test_client_ctl.py::test_json_v1_contract` (real controller) | n/a |
| doctor | `doctor.py` (`check_read_back`) | — | — |

Needs the user: grant Accessibility to the terminal running `hark run` on macOS and set `[learn] enabled = true` to
try learning; on Omarchy run `uv tool install --editable ./client` then
`packaging/omarchy/omarchy-install-dictation-hark`; for AT-SPI read-back install PyGObject into hark's environment.

## Rejected / avoided (from §10)
- A global keystroke stream to the daemon: the terminal fallback above is armed only for the learning window and the
  target window, keeps events in memory, persists derived pairs only.
- Turning on accessibility globally (Chromium/Qt flags) without asking: hark reads what the toolkit exposes; `hark
  doctor` may *suggest* flags later.
- Writing into other tools' config homes unasked (agent skills, shell rc files).
- Cloud-first defaults and a default-on telemetry DSN.

## Linux key-event source for terminal learning (design, not implemented)
Hyprland ≥ 0.55 Lua: an optional `~/.config/hark/shortcuts.lua` (loaded by Omarchy's `dictation-backend.lua`)
registers `hl.on("input.keyboard.key", fn)` once. `fn` forwards **only** while a global `hark_learn` table is armed
(`{address, until}`) and the active window's address matches, writing `{"version":1,"command":"key","keysym":…,
"mods":…,"state":…}` lines to `ctl.sock`. The daemon arms it after a terminal paste with
`hyprctl eval 'hark_learn = {address = "0x…", until = <monotonic + window>}'` and disarms on finish, focus change or
the next dictation. Events feed `keyedits.reconstruct()` in memory. Unverified: the event payload field names of
`input.keyboard.key`; needs a session on the laptop.
