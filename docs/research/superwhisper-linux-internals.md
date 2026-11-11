# Superwhisper for Linux 1.3.1: internals (static analysis)

Date: 2026-11-12. Analyst role: reverse engineering by **static inspection only**. Nothing was executed, nothing was installed and no sudo was used.
Complements `competitive-voxtype-superwhisper.md`, which covers product and UX. This document covers *how it is built*.

**Evidence labels.** **[V]** means verified in a file I read: script text, JSON, `strings`/`nm`/`objdump -p` output, release notes or Omarchy source. **[I]** means an inference from symbol or string fragments and is plausible but unconfirmed. **[U]** means unknown. String evidence shows that code *exists*, not that it runs by default.

## 0. Artifacts inspected

| Artifact | Source | Notes |
|---|---|---|
| `install.sh` (bootstrap, 180 lines) | `https://tux.superwhisper.com/install.sh` | read in full |
| `stable.json`, `beta.json` | `https://tux.superwhisper.com/{stable,beta}.json` | both point at 1.3.1 (`schema: 1`, published 2026-11-11T13:06:09Z) |
| `superwhisper-omarchy-x86_64-1.3.1.tar.zst` (51,969,150 B) | `https://tux.superwhisper.com/x86_64/v1.3.1/…` | sha256 `464a732c…f0e4`, matches manifest **[V]** |
| `superwhisper-omarchy-aarch64-1.3.1.tar.zst` (39,255,818 B) | `https://tux.superwhisper.com/aarch64/v1.3.1/…` | sha256 `a440c85e…f06f`, matches manifest **[V]** |
| `.minisig` for both | same paths + `.minisig` | key ID matches the embedded public key `BC524820AE37B966`. Not cryptographically checked because minisign is not installed and I was not allowed to install it. Trusted comment: `superwhisper-linux 1.3.1 1f35e195…` |
| Release notes v0.1.0-beta.13 … v1.3.1 (17 releases) | GitHub API `superultrainc/superwhisper-linux-releases` | the source repo `superultrainc/superwhisper-linux` is private (API returns 404) |
| Omarchy dictation plumbing | `omacom/omarchy` branch `quattro` (`bin/omarchy-*dictation*`, `default/hypr/*.lua`, manual) | |
| Vendor/upstream pages | superwhisper.com/blog/omarchy, /blog/cohere; github.com/handy-computer/transcribe.cpp README | |

Everything was extracted under `/tmp/sw-linux/`. `BUILD_INFO.json`: `profile: portable-vulkan`, `target: x86_64-unknown-linux-gnu`, `modelsIncluded: false`, git `1f35e195`. **[V]**

---

## 1. TL;DR

- **Language and shape [V].** The core is one stripped **Rust** binary (`superwhisper`, rustc 1.96.1). It contains the daemon, the CLI, a ratatui TUI and the shortcut bridge. It drives **C++ worker processes** over JSON-lines stdio: `asr-worker` on **transcribe.cpp/ggml + Vulkan**, and `cleanup-worker` on **llama.cpp**. **Python + PyGObject helper scripts** handle AT-SPI, portals, MPRIS and GNOME/KDE glue. A separate **GTK4 + gtk4-layer-shell** process draws the recording pill and edge glow. The Settings window is **GPUI**, Zed's Rust UI framework, rendering through wgpu.
- **Models [V].** "Offline Lite" is **Parakeet TDT 0.6B v3 Q4_K_M GGUF** (485 MB). "Offline" is **Cohere Transcribe 03-2026 Q4_K_M GGUF** (1.56 GB). Both use **S1 Mini** cleanup (`s1-mini-v080-iq4_xs.gguf`, 368 MB, Qwen3 chat template with thinking disabled), **Silero VAD** (ONNX) and **"supervocab"**. Supervocab is an *acoustic* vocabulary matcher: a wav2vec2-XLSR espeak phoneme CTC model, a G2P encoder-decoder and a phoneme substitution-cost matrix, run through ONNX Runtime 1.24.2 or as a GGUF.
- **Vocabulary is not decode-time boosting [I].** No hotword or bias API appears in `asr-worker`. Vocabulary terms are applied *after* ASR by phonetic keyword spotting and transcript fusion. That is an acoustically gated replacement, unlike text-only LLM correction. Cloud requests send an `asr_prompt` plus `vocab.bin` and `enable_audio_vocab`.
- **Insertion has many routes [V].** On Hyprland/Omarchy, an **fcitx5 addon** commits text as input-method text, with no clipboard and no keystroke. The fallback is `wl-copy` plus a **Hyprland Lua `send_key_state`** to the exact window address, after focus, pid, layer and lock checks. Other routes: `zwp_virtual_keyboard_v1`, `wtype`, `ydotool`, the **RemoteDesktop portal** (KDE/GNOME), and a **GNOME Shell extension** D-Bus `SendChord`. Afterwards it checks over AT-SPI whether the paste "landed".
- **Context on Linux is thin at dictation time [V/I].** It captures focused-window identity (Hyprland IPC/KWin/AT-SPI), the **cursor context** (characters before and after the caret, used for spacing) and the terminal/app class (used to pick the paste chord). I found **no screenshot or OCR** path and no evidence that screen text is sent to the LLM. The heavy context work goes into **vocabulary**: opt-in *suggestions* mined locally from agent sessions (Claude Code, Codex, **pi**, Copilot, Cursor, Grok), notes, git and the app launcher, plus **correction learning**. Correction learning watches the target field over AT-SPI after a paste. In terminals it reconstructs edits from Hyprland key events.
- **Cloud [V].** `api.superwhisper.com` handles `/v2/inference/regions`, `/v2/inference/key` and `/license/validate`. The region host is `us.aws.superwhisper.com`. Audio goes up as Ogg/Opus encoded by ffmpeg, in a multipart request with `asr_prompt`, `llm_prompt` (`{{SW_USER_TRANSCRIPT}}`), `vocab.bin` and `S1-Language`. Offline is used automatically when the network is down (v1.2.0). Crash and paste reports go to **Sentry** (DSN embedded), with scrubbed panics and bucketed fields.
- **Packaging [V].** A signed per-arch manifest. Per-version directories with an atomic `current` symlink swap, then a health check that rolls back on failure. systemd **user** service with `UMask=0077`. A 6-hourly update timer. An uninstall that undoes changes from a journal. No udev, uinput or `input` group changes. Omarchy ships it as the **default dictation backend** through a public adapter contract (`omarchy-dictation-<backend>`).

---

## 2. Architecture (ASCII)

```
                       ┌────────────────────── compositor / desktop ───────────────────────┐
 Hyprland (Lua cfg) ── │ shortcuts.lua / managed block: hl.bind + hl.on('input.keyboard.key')│
                       │   io.popen pipe → NDJSON {"version":1,"command":"shortcut_event",…} │
 niri ──────────────── │ superwhisper.kdl (binds{} included from config.kdl)                │
 KDE / GNOME ≥48 ───── │ xdg-desktop-portal GlobalShortcuts (desktop_portal.py)             │
 GNOME <48 ─────────── │ gsettings custom keybindings + Shell ext com.superwhisper.Hold     │
                       └───────────────┬────────────────────────────────────────────────────┘
                                       │ $XDG_RUNTIME_DIR/superwhisper/control.sock (NDJSON v1)
  CLI `superwhisper …` ───────────────►│◄──── Omarchy panel (Quickshell QML + status.py)
  agent skill (SKILL.md → CLI)         │◄──── GPUI settings (superwhisper-settings)
                                       ▼
 ┌──────────────────────── superwhisper daemon (Rust, tokio; systemd --user) ─────────────────────┐
 │ audio: `pw-record` subprocess, pactl subscribe/volume/mute, mic health; WirePlumber keep-ready │
 │ VAD (Silero ONNX via ort) ─► ASR route: offline worker │ cloud (reqwest+rustls) │ fallback   │
 │ supervocab: G2P → phoneme "needles"; wav2vec2 CTC posteriors → keyword_spotter → fusion       │
 │ cleanup route: S1 Mini worker │ S1 cloud │ rules-based tone (Offline Lite)                     │
 │ insertion router: fcitx5 │ Hyprland Lua paste │ virtual-keyboard │ wtype │ ydotool │ portal  │
 │ history: ~/…/recordings/<ts>/meta.json + audio, SQLite stats (WAL); Sentry reports            │
 │ media: MPRIS pause (media-bridge.py) │ "Muffle" LADSPA filter-chain │ "Lower" volume journal │
 └──┬──────────────┬──────────────┬───────────────┬───────────────┬─────────────┬─────────────────┘
    │stdio JSON    │stdio JSON    │stdio JSON     │stdio JSON     │unix socket  │D-Bus
    ▼              ▼              ▼               ▼               ▼             ▼
 asr-worker     cleanup-worker  desktop_portal.py capture-field.py fcitx5 addon superwhisper-indicator
 (transcribe.   (llama.cpp,     (portals, KWin,   confirm-paste.py (libsuper-  (GTK4 + layer-shell,
  cpp/ggml,      CPU or Vulkan;  AT-SPI focus,    insertion-ctx.py  whisper-     com.superwhisper.Indicator
  Vulkan/CPU;    prepare/        RemoteDesktop    (AT-SPI)          fcitx5.so,   State/Waveform, SNI tray,
  --wav2vec2)    tone_control)   paste+clipboard)                   INSERT)      edge glow, pill)
                                       │
                         cloud: api.superwhisper.com (/v2/inference/regions|key, /license/validate)
                                → region host (us.aws.superwhisper.com): multipart ogg/opus + prompts
```

---

## 3. Installation and distribution (task 1)

### 3.1 Bootstrap `install.sh` [V]
- Wraps everything in `main()` called on the last line, so a truncated download runs nothing. POSIX `sh`. Refuses to run as root, on non-Linux, or on an arch other than `x86_64`/`aarch64`. It only *warns* when there is no Wayland session.
- Options: `--channel stable|beta` (or `SUPERWHISPER_CHANNEL`), `--dry-run`, `--help`. `SUPERWHISPER_BASE_URL` exists "for tests only". When set it relaxes the protocol to http and skips the URL allow-list.
- Requires `curl tar zstd sha256sum bash` and prints distro-specific install hints, but does not install anything itself.
- Fetches `$base/$channel.json` with `curl --proto =https --tlsv1.2 --retry 2`. It flattens the JSON with `tr` and `sed` (no jq) and validates `schema==1`, `channel`, the version charset, 64-hex `sha256` and a numeric `size`. URLs must be on the allow-list: `https://tux.superwhisper.com/*`, `github.com`, `*.github.com`, `*.githubusercontent.com`.
- Checks the download in order: **size**, then **SHA-256** (both mandatory), then the **minisign** signature, which is *skipped with a message if minisign is absent*. The public key is embedded: `RWRmuTeu…zhzATB`.
- Archive safety: `tar -t`, then a single top-level directory with no absolute or `..` paths, and `VERSION` must equal the manifest version. It then runs the inner `./install.sh` with `</dev/tty` so sudo can prompt.

### 3.2 Inner `install.sh` + `dependencies.sh` [V]
- `sha256sum -c MANIFEST.sha256` runs over the whole bundle first.
- **Packages** per manager (pacman/apt/dnf/zypper; `omarchy pkg add` on Omarchy): python + gobject + cairo, gtk4, gtk4-layer-shell (optional on Ubuntu 24.04), at-spi2-core, pipewire tools, pulseaudio-utils (`pactl`), libnotify, **wl-clipboard**, **ffmpeg**, vulkan loader + Mesa ICD (chosen by DRM PCI vendor: Intel `0x8086`, AMD `0x1002`), **minisign**, librsvg, libxkbcommon(-x11), wayland. Adds `foot` on Omarchy, and **wtype** outside Hyprland/Omarchy. Escape hatch: `SW_SKIP_PACKAGES=1`.
- **Layout:** `~/.local/share/superwhisper/app/<version>/` is staged as `.install-<v>-$$`, and `run.sh doctor` runs *before* the switch. Then `current` is swapped atomically (`ln -sfn current.new && mv -Tf`). The launcher `~/.local/bin/superwhisper` is a two-line `exec …/current/run.sh "$@"`.
- **PATH edits:** `~/.config/environment.d/60-superwhisper-path.conf`, plus marked blocks in `~/.bashrc`, `~/.zshrc` and fish `conf.d`. Skipped when run by the updater, detected via `INVOCATION_ID` with no TTY.
- **systemd user units:**
  - `superwhisper.service`: `ExecStart=… daemon`, `ExecStopPost=… playback-restore` and `… desktop-release`, `PartOf=graphical-session.target`, `After=pipewire.service`, `Restart=on-failure`, `KillMode=control-group`, **`UMask=0077`**, `Conflicts=superwhisper-dev.service`.
  - `superwhisper-update.service` (oneshot `update apply`).
  - `superwhisper-update.timer` (`OnBootSec=10m`, `OnUnitActiveSec=6h`, `RandomizedDelaySec=30m`).
  - Muffle runs as a transient unit (`superwhisper-muffle.service` via `systemd-run`) **[V strings]**.
- **Desktop entry:** `com.superwhisper.Superwhisper.desktop`, reverse-DNS because "GNOME refuses global shortcuts for a dotless one". Icons go in hicolor. Deeplinks (`superwhisper://`, via `register-deeplinks.py`) and the TUI glyph font are installed too.
- **Agent skill:** symlinks `assets/agent-skill/superwhisper` into `~/.agents/skills`, plus `~/.claude/skills`, `~/.codex/skills` and **`~/.pi/agent/skills`** when those agent homes exist. No prompt.
- **GNOME:** symlinks and enables the Shell extension `superwhisper@superwhisper.com` (shell versions 46–51) on first link only.
- **fcitx5:** writes `~/.local/share/fcitx5/addon/superwhisper.conf` pointing at `current/lib/fcitx5/libsuperwhisper-fcitx5.so`. It asks the running addon for `VERSION` over `$XDG_RUNTIME_DIR/superwhisper-fcitx5.sock`. If the version is stale and fcitx5 runs under Hyprland, it restarts fcitx5 via its systemd unit or a `systemd-run --user --scope` clone of its env and argv.
- **Omarchy:** links the bar plugin `superwhisper.panel` (placed before `omarchy.clock`), migrates an old plugin id inside `shell.json` (backup kept), adds Omarchy menu rows, hides Omarchy's own dictation indicator, and on *first* install runs `omarchy-default-dictation superwhisper`.
- **Start and health:** `systemctl --user enable/restart`. Polls `superwhisper status` 50×100 ms. **On failure it rolls back** to the previous `current` and restores the replaced directory. Finally it runs `superwhisper shortcuts apply`.
- **No** udev rules, no `/dev/uinput`, no `input` group, and no system-wide files beyond the distro packages. **[V]**

### 3.3 Update and uninstall [V]
- `scripts/update.py`: picks the beta channel for prerelease versions, else stable. It reads the manifest, validates URLs and SemVer, takes `flock` on `.update.lock`, downloads, and verifies the **digest and a mandatory minisign signature** (unlike the bootstrap, it calls the `minisign` binary). It then runs the bundle's `install.sh`, which handles the rollback. Nix sets `SUPERWHISPER_UPDATE_DISABLED=1`.
- `uninstall.sh`: disables units, stops Muffle, and removes the fcitx5 addon conf (reverting KDE `InputMethod` if Superwhisper set it). It resets GNOME global-shortcut dconf, the niri `superwhisper.kdl`, the Omarchy plugin, menu rows and default dictation, the GNOME extension, agent-skill links and WirePlumber links. It replays an **`accessibility-changes` journal** to undo only what it turned on: gsettings `toolkit-accessibility`, the `systemctl --user` env, Chromium and Obsidian flags. It also removes the PATH blocks by marker and the app directory, and keeps user data (preferences, vocabulary, history, models).

### 3.4 Release channels and arches [V]
- Channels: `stable` and `beta`. Archives are named `superwhisper-omarchy-{x86_64,aarch64}-<v>.tar.zst`; v1.2.0 alone used `superwhisper-linux-…`. Assets per release: `.tar.zst`, `.minisig`, `.sha256`.
- aarch64 has the same layout without `asr-worker-baseline` or `cleanup-worker-baseline`. Offline needs ARMv8.2 dot-product; Cloud works anywhere.
- x86_64 ships `*-baseline` workers for CPUs without AVX2 (v1.3.1: "Offline works on CPUs without AVX2"). `SW_HARDWARE_PROFILE` and the probes of `/proc/meminfo`, `/sys/class/drm` and `cpu_atom` suggest automatic variant selection **[I]**.
- NixOS gets a flake plus a Home Manager module (`github:superultrainc/superwhisper-linux`, per README).

---

## 4. Archive layout and binaries (task 2)

| Path | What | Evidence |
|---|---|---|
| `app/bin/superwhisper` (17.6 MB, stripped) | **Rust** multi-role binary. Roles string: `daemon ui worker shortcut-bridge cli`. Crates: tokio 1.53, reqwest 0.12 + rustls 0.23 + ring, rusqlite 0.40, **ort 2.0.0-rc.13** (ONNX Runtime via `ORT_DYLIB_PATH`), sentry 0.42, ratatui 0.29 + crossterm (TUI), wayland-client + **wayland-protocols-misc** (`zwp_virtual_keyboard_manager_v1`), toml_edit, csv, hound (WAV), **uroman** (romanization), unicode_names2 (emoji names), regex, aho-corasick. Own modules include `src/vad.rs`, `shortcuts.rs`, `shortcut_bridge.rs`, `s1_draft.rs`, `text_commit_offer.rs`, `vocabulary.rs`, `waveform.rs`, `playback.rs`, `settings_window.rs`, and `vendor/supervocab/src/{audio/{wav2vec2,g2p,ctc_scoring,forced_aligner,keyword_spotter,substitution_costs,token_matching,token_stream,spell_checking}.rs, text/{metaphone,string_distance,tokenizer,transcript_fusion,vocabulary}.rs}` | `strings` panic paths **[V]** |
| `app/bin/asr-worker` (54.6 MB, **not stripped**) | C++ on **transcribe.cpp** (vendored ggml + Vulkan, nlohmann::json). Namespaces: `transcribe::{parakeet, cohere, whisper, qwen3_asr, voxtral(_realtime), canary(_qwen), moonshine(_streaming), granite(*), gigaam, sensevoice, funasr_nano, medasr, moss, sortformer}`, plus `w2v::Model`. Usage: `asr-worker MODEL cpu\|cuda\|vulkan \| --wav2vec2 MODEL [--backend vulkan\|cpu]`. Links only `libvulkan.so.1` and libc/libstdc++ | `nm -C`, `objdump -p` **[V]** |
| `app/bin/asr-worker-baseline` | non-AVX2 build of the above | **[V]** file / **[I]** purpose (release note) |
| `app/bin/cleanup-worker{,-baseline,-vulkan}` | C++ on **llama.cpp** (`/src/.deps/llama.cpp`), with grammar/JSON-schema and speculative/n-gram draft code linked. Keys: `command`, `prepare`/`prepared`/`prepare_ms`, `cleanup_ms`, `tone_control`, `gpu_layers_offloaded`, `cpu_threads`, tones `casual…formal`. Chat template from `SW_CHAT_TEMPLATE` (`templates/qwen3.jinja`, which emits an empty `<think>` block, so thinking is off) | **[V]** |
| `app/bin/superwhisper-indicator` (1.2 MB) | Rust **gtk4-rs 0.11.5**. **dlopens `libgtk4-layer-shell.so.0`** (with an LD_PRELOAD fallback dance: `SW_LAYER_SHELL_PRELOADED`). Exposes D-Bus `com.superwhisper.Indicator` at `/com/superwhisper/Indicator` with props `State` (s) and `Waveform` (ad), plus a **StatusNotifierItem** tray. Draws the pill and **edge glow**. Reads Omarchy `theme/colors.toml`, niri `NIRI_SOCKET` and the Pond theme | **[V]** |
| `app/bin/superwhisper-settings` (31 MB) | Rust **GPUI** (fork `gpui-omarchy`), wgpu 29, cosmic-text, ashpd (portals), zbus 5, wayland-protocols-wlr/-plasma, calloop. Software-render fallback in VMs (v1.3.1) | **[V]** |
| `app/lib/libonnxruntime.so.1.24.2` | ONNX Runtime CPU build (VAD and supervocab ONNX graphs) | **[V]** |
| `app/lib/libsuperwhisper_muffle.so` | Rust **LADSPA** plugin ("Superwhisper Muffle"), loaded into a PipeWire filter-chain to muffle playback while dictating | **[V]** |
| `app/lib/fcitx5/libsuperwhisper-fcitx5.so` (31 KB) | fcitx5 addon (links libFcitx5Core/Utils). Unix socket `superwhisper-fcitx5.sock`, line commands `VERSION`, `INSERT`, `bad_request`, password-field refusal | `strings` **[V]**; exact grammar **[I]** |
| `app/scripts/*.py` | `desktop_portal.py` (66 KB: GlobalShortcuts, RemoteDesktop + Clipboard portals, KWin scripting focus, kglobalaccel migration, GNOME Shell paste), `capture-field.py` (correction capture), `confirm-paste.py`, `insertion-context.py`, `media-bridge.py` (MPRIS), `doctor.py`, `update.py`, `gnome_keys.py`, Omarchy helpers | read **[V]** |
| `app/assets/` | `models.json` (catalogue), `cohere-mlx.json` (Mac/MLX dev variant), `cloud-dialects.json` (per-language ASR prompt prefixes), `languages.json`, `menu-options.json` (offline languages, tones), `paste-keys.json` (paste chord per app class), `prompts/message.txt` (cleanup system prompt, 586 words), `self-check.wav` + `.phonemes` (GPU correctness probe), sounds, WirePlumber conf/lua, GNOME extension, Omarchy panel plugin (QML + `PROTOCOL.md` + contract tests + fake daemon), agent skill | **[V]** |
| `licenses/` | llama.cpp (MIT), onnxruntime (MIT), transcribe.cpp (MIT), Parakeet CC-BY-4.0 notice, "Super Ultra, Inc. All rights reserved" for the rest | **[V]** |

**UI toolkits [V].** GTK4 for the overlay, GPUI for settings, ratatui for the TUI, Quickshell QML on Omarchy, GJS on GNOME. **Not** Electron, Tauri, WebKit, Flutter or Qt widgets.
**ML runtimes [V].** ggml/transcribe.cpp (Vulkan + CPU) for ASR and the wav2vec2 GGUF; llama.cpp (CPU or Vulkan) for cleanup; ONNX Runtime (CPU) for Silero and supervocab ONNX. No CTranslate2, no MLX in the Linux build (`SW_EXPERIMENTAL_MLX` and `cohere-mlx.json` are dev/Mac leftovers **[I]**), and no CUDA libraries shipped. A dev path references ORT `gpu_cuda13` and cuDNN in `setup-acoustic.py`/`run.sh`.

### 4.1 Model catalogue (`assets/models.json`) [V]

| id | title | file | size | host |
|---|---|---|---|---|
| `transcription` | Parakeet TDT 0.6B v3 ("Offline Lite", 25 EU languages) | `parakeet-tdt-0.6b-v3-Q4_K_M.gguf` | 485 MB | Hugging Face `handy-computer/…` @ pinned revision |
| `cohere-transcription` | "Offline" (Cohere Transcribe) | `cohere-transcribe-03-2026-Q4_K_M.gguf` | 1.56 GB | `models.superwhisper.com` |
| `cleanup` | S1 Mini | `s1-mini-v080-iq4_xs.gguf` | 368 MB | `models.superwhisper.com` |
| `vocabulary` | Acoustic vocabulary | `supervocab/wav2vec2-xlsr-espeak-distill.int8.onnx` (123 MB) + optional `.f16.gguf` (190 MB), `g2p-encoder.int8.onnx`, `g2p-decoder.int8.onnx`, `phoneme_cost.u8`, `vocab.json`, `common-en.txt` | ~145 MB | `models.superwhisper.com/supervocab/…` |
| `voice-detection` | Silero VAD | `vad-v2.onnx` | 2.8 MB | `models.superwhisper.com` |

Every file carries a sha256 and a byte size. Leftover strings mention `parakeet-tdt_ctc-110m-Q4_K_M.gguf` and `s1-mini-v076-q4_k_m.gguf` (older or alternative) **[V strings]**. S1 Mini is **Qwen3-family** **[I]**, inferred from the Qwen3 template and the iq4_xs size (≈0.6B params).

---

## 5. Pipeline stages on Linux

### 5.1 Hotkeys [V]
- **Hyprland (Lua config only).** The daemon writes a managed block (`-- Superwhisper managed shortcuts BEGIN/END`; `SUPERWHISPER_HYPR_BINDINGS`), or on Omarchy `~/.config/superwhisper/shortcuts.lua`, which Omarchy's `dictation-backend.lua` `dofile`s. The generated Lua:
  - `io.popen`s a pipe into the CLI's shortcut-bridge and writes NDJSON: `shortcut_event` with id, edge (`down|up|interrupt`) and serial token; also `submit`, `push_to_talk_key`, and `auto_vocabulary_key`/`auto_vocabulary_submit`.
  - Uses `hl.bind(..., {non_consuming, ignore_mods, transparent, release})` so keys are **observed, not consumed**. The one exception is a lone Alt hold: it is consumed because otherwise Electron and Firefox focus their menu bar. Mouse4/5 releases are tracked through transparent release binds.
  - Takes over Omarchy's `o.dictation_keys` (F9, Right Alt, Super+Ctrl+X) instead of double-firing. Unbinds each key once because unbinding press and release one at a time "crashes Hyprland 0.56".
  - Sets `hl.layer_rule` no_anim for the `superwhisper-edge-glow` and `superwhisper-indicator` layers.
  - Supports an opt-in "Send" key while holding PTT (Space → stop and press Return).
  - If `~/.config/hypr/hyprland.lua` is not the entry point, it tells the user to migrate to Lua.
- **niri:** writes `superwhisper.kdl` (a `binds {}` block) included from `config.kdl`. **KDE:** GlobalShortcuts portal, migrating old kglobalaccel components. **GNOME ≥48:** GlobalShortcuts portal (shortcuts persisted in dconf under the app id). **GNOME <48:** custom keybindings that run `superwhisper record`, and hold-to-talk through the Shell extension's D-Bus `com.superwhisper.Hold` (`Bind`, `SetCancel`, signals `Activated/Deactivated/Cancel`).
- Defaults outside Hyprland: **hold F9**, **Esc cancel** (only grabbed while recording). On Omarchy, Omarchy owns the keys (Right Alt hold, Alt+Space toggle on fresh installs) and calls `superwhisper start|stop|record`.

### 5.2 Audio capture and mic handling [V]
- Capture uses a **`pw-record` subprocess** (`--latency --rate --channels 1 s16 --properties …`). It is not cpal or libpipewire. `pactl subscribe` watches source and server events ("Audio server disconnected; reconnecting"). `pactl set-source-volume/-mute` fixes mic levels. History recordings are 48 kHz (v1.3.0), and VAD and supervocab need 16 kHz mono **[V strings]**, so capture is presumably 48 kHz and resampled **[I]**.
- **Mic warm start.** A WirePlumber component replaces the stock suspend hook. ALSA sources get `node.slow-resume=true`, and the client sets `session.fast-capture-start=true`, so the mic stays *idle rather than suspended* ("Waking a suspended ALSA microphone can take half a second"). This was upstreamed as WirePlumber MR 921; on 0.5.19+ the stock hook is used (v1.1.3). Related knobs: `SW_PREWARM_MIC` and `SW_KEEP_MIC_READY` / `keepMicrophoneReady`.
- **Mic health (v1.3.1):** notifications for muted, silent, lost, too quiet, and constant-signal ("broken") microphones.
- **Media while dictating:** pause via MPRIS (`media-bridge.py`; "Rust owns suppression, identity and restoration"), "Lower" (per-stream volume with a journal), or "Muffle" (LADSPA filter-chain in a transient unit). All three have a restore path in `ExecStopPost=… playback-restore`.

### 5.3 VAD [V]
Silero VAD v2 ONNX via `ort` (`silero-cpu`, `vad-worker`, `enableSilenceRemoval`, `silenceRemovalMs`, `vad_wall_ms`). It removes silence before ASR. It does not appear to be endpointing; recording is key-driven **[I]**.

### 5.4 ASR [V/I]
- `asr-worker` is a long-lived child (`ready`, `load_ms`, `warm_ms`) with JSON commands. `prepare`/`prepared`/`prepare_ms`, `begin_frame`, `prefix_text` and `reused_audio_sec` suggest **the audio prefix is encoded or decoded while the user is still speaking**, so after key-up only the tail is processed **[I]**. Output includes word-level data (`transcribe_batch_get_word`).
- GPU selection uses DRM vendor and device IDs, with special-casing for an Intel device (`0x8086`/`0xb090`, `GGML_VK_FORCE_MMVQ`). The **GPU self-check** decodes `self-check.wav` ("ask not what your country can do for you") and compares against expected phonemes and text (`ask ask ask ask` / `the the the the` look like garbage sentinels). If the driver returns wrong results it falls back to CPU, and `doctor` reports "GPU results correct" **[V]**.
- Routing: Cloud by default (10k free words per the blog), Offline or Offline Lite by choice. **On Cloud with no connection it uses offline models immediately**. When free words run out it offers to switch (v1.0.1, v1.2.0).

### 5.5 Vocabulary: "supervocab" (the interesting part) [V files / I mechanism]
- **Data model** (`settings/settings.json`, shared with the Mac and Windows apps): `terms`, `replacements` (`original` → `with`) and `pronunciations` (`word` + `sounds_like[]`). Up to 1,000 entries, a 1 MiB file limit, CSV import/export, revision-checked edits ("Vocabulary changed elsewhere"). It ships built-in Omarchy mishearings ("Oh Ma Chi" …).
- **Offline mechanism [I]:**
  1. A **G2P** encoder-decoder (ONNX) turns each term into phonemes. These are the "needles"; while they are being prepared, "using text correction for this recording".
  2. The **wav2vec2-XLSR espeak phoneme model** produces CTC posteriors for the recording, through ONNX or through `asr-worker --wav2vec2` on Vulkan.
  3. `keyword_spotter.rs` and `ctc_scoring.rs` search the posteriors for each needle using a **phoneme substitution-cost matrix** (`phoneme_cost.u8`).
  4. Each hit carries `start_seconds`, `end_seconds`, `score`, `text_similarity`, `acoustic_similarity` and **`posterior_deficit`**.
  5. `forced_aligner.rs` time-aligns the ASR words. `metaphone.rs` and `string_distance.rs` compare spellings. `transcript_fusion.rs` replaces ASR spans with vocabulary terms where acoustics and text agree. `common-en.txt` keeps common words from being overwritten.
  6. Diagnostics: `words=…; eligibility=…; hits=…; near_misses=…` (`SW_VOCAB_DIAGNOSTICS`).
  7. **uroman** romanizes non-Latin scripts for matching.
- **Cloud:** multipart fields `asr_prompt` (a per-language preamble from `cloud-dialects.json`, e.g. "Hey, here are some of the names and words that might be used", followed by the terms **[I]**), `enable_audio_vocab` and `vocab.bin` (`application/octet-stream`; a serialized vocabulary or phoneme set **[I]**), and `enable_word_timestamps`.
- **Suggestions (`vocab suggest`, v1.3.0)** mine local sources (labels: "Git repos", "Teammates", "Installed apps", "your messages", "your notes", "the names in your app launcher"): `git user.name`, `/etc/passwd`, desktop entries, Obsidian vaults, Omawrite, and agent sessions found through `CLAUDE_CONFIG_DIR` (+`archived_sessions`), `CODEX_HOME`, `GROK_HOME`, `PI_CODING_AGENT_DIR`/`PI_CODING_AGENT_SESSION_DIR`, `COPILOT_HOME` (`session-state`), `CURSOR_DATA_DIR` (`agent-transcripts`). The release note says "It asks first, and nothing leaves your computer."
- **Correction learning (`autoVocabCaptureEnabled`):**
  - **GUI apps:** after a paste, `capture-field.py` watches the focused editable field over AT-SPI for up to 30 s. It stops on focus loss, on submit (SIGUSR1) or on timeout, never reads `PASSWORD_TEXT`, caps reads at 64 KiB, and has special cases for Chromium/Obsidian. The daemon diffs the field against the pasted text to learn corrections **[I diff step]**.
  - **Terminals:** the Hyprland Lua hook streams *every key event* (code, modifiers, window address) to the daemon, which reconstructs edits assuming a US keyboard (`auto-vocabulary-terminal`; inventory notes "US-keyboard reconstruction").
  - **Prerequisite:** to make apps expose AT-SPI, it enables accessibility globally: `environment.d/90-superwhisper-accessibility.conf` with `NO_AT_BRIDGE=0`, `QT_ACCESSIBILITY=1` and `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`; GNOME `toolkit-accessibility`; Chromium `--force-renderer-accessibility` in `chromium-flags.conf`; Obsidian `user-flags.conf`. Every change is journaled for uninstall.
  - A "Vocabulary learned" notification is shown.

### 5.6 Cleanup and LLM [V/I]
- **Offline:** the S1 Mini worker uses the system prompt `prompts/message.txt`, summarized here: smallest possible edits; punctuation, capitalization and homophones; spoken numbers and dates; remove fillers and self-corrections; paragraph breaks; spoken emoji names → emoji; never rephrase, never soften profanity, never drop clauses. Tone has five levels via `tone_control`.
- **Latency tricks:** `prepare` is called during recording **[I]** to pre-fill the system prompt's KV cache. **"Draft-guided PLD for S1 Mini"** (PR #115 in v1.1.0-beta.1; `s1_draft.rs`) means a rules-based draft of the cleaned text feeds **prompt-lookup / n-gram speculative decoding** in llama.cpp, so the mostly-copied output costs few full forward passes **[V title + symbols; I details]**.
- **Offline Lite** "applies tones with lighter formatting rules instead of S1 Mini", i.e. rules only.
- **Cloud:** `llm_prompt` (with `{{SW_USER_TRANSCRIPT}}`) is sent alongside the audio in a single request (`cloud_combined`, `parallel_decode`, `llm_duration`, `S1-Language` header).
- Metrics recorded per dictation: `release_to_text_ms`, `pipeline_start_ms`, `vad_wall_ms`, `acousticVocabularyMs`, `asr_ms`, `cleanup_ms`, `prepare_ms`, `audio_seconds`.

### 5.7 Context capture on Linux [V/I]
- **Focus target at paste time:**
  - Hyprland: IPC on `.socket.sock` directly (not `hyprctl`), reading the active window `address`, `pid`, `class` and `title`, plus `hyprctl -j locked`.
  - KDE: KWin scripting via D-Bus (`org.kde.kwin.Scripting`).
  - Elsewhere: AT-SPI (`desktop_portal.py` `Focus`).
  - niri: `NIRI_SOCKET` `FocusedWindow`.
  - Terminal detection uses a class list (`foot, kitty, ghostty, wezterm, alacritty, konsole, ptyxis, …, herdr`).
- **Cursor context:** `insertion-context.py` reads only the *focused editable* field of the target pid. It returns `before`, `after`, the last non-whitespace character, newline-before, and the field length. Reads are bounded to 4,096 preceding characters, with a 200 ms deadline and 500 nodes, and KDE menus are skipped. This drives **spacing and capitalization at the join**, not LLM prompting.
- **Not found:** no screenshot portal, `grim`, OCR or Hyprland `clients` scraping. The `applicationContext`, `promptContext` and `applicationContextEnabled` keys appear in `meta.json`, which is a schema shared with macOS; whether Linux fills them is **[U]**. Clipboard and selection context like the Mac app's: **[U]**. The only strings seen concern pasting the primary selection.

### 5.8 Insertion [V]
- **Router** (`paste_route`, `routes_tried`, `SW_PASTE_FORCE_ROUTE`): `fcitx5`, Hyprland Lua dispatch, `virtual-keyboard` (`zwp_virtual_keyboard_v1`; errors like "another virtual keyboard's keymap is active"), `wtype`, `ydotool` (`YDOTOOL_SOCKET`), `portal` (RemoteDesktop `NotifyKeyboardKeysym`, after first sending modifier releases), and GNOME Shell (`com.superwhisper.Paste.SendChord/SetClipboard`, which avoids a RemoteDesktop session). Combinations such as `portal+wtype` exist.
- **fcitx5 direct commit (Hyprland, and KDE by offer):** no clipboard, layout-independent, "about four times faster". Multi-line text going into terminals is still pasted, so lines don't run.
- **Clipboard paste on Hyprland:**
  1. `wl-copy --type text/plain;charset=utf-8 --sensitive`; the `x-kde-passwordManagerHint` MIME type keeps clipboard managers from storing it **[I]**.
  2. Wait until the clipboard really holds the new text (beta.13 fix).
  3. Run a generated Lua chunk inside Hyprland that re-checks `hl.get_active_window()` address and pid against the recorded target. It aborts with `focus_changed`, or `panel_has_keyboard` if any layer holds the keyboard.
  4. `hl.dispatch(hl.dsp.send_key_state{…, window='address:…'})` for down, then up 50 ms later.
  5. Report `paste_sent`, `key_not_found` or `dispatch_refused`.
  - Text pastes "where you are when you stop dictating" (beta.16). The session lock (`login1 LockedHint`, `hyprctl locked`) blocks pasting, and text is then only copied.
- **Chord per app** (`paste-keys.json`): terminals use Ctrl+Shift+V, the default is Ctrl+V, and emacs, gvim, neovide, xterm, urxvt and st use Shift+Insert.
- **Submit:** the opt-in Send key presses Return *in the same window*. It special-cases X/Twitter titles to use Super+Return.
- **Verification:** `confirm-paste.py` returns `landed | missing | not_text | unknown`. It normalizes spacing, quotes and dashes, handles text split across nodes, and also accepts field growth of about the pasted length. *Absent evidence is `unknown`, never "failed"*. Reports carry only bucketed facts (`pasted_size <1000/1500+`, toolkit, role, app class from a fixed allow-list), "never the text itself".

### 5.9 UI, IPC and state [V]
- **Control socket:** `$XDG_RUNTIME_DIR/superwhisper/control.sock`, newline-delimited JSON `{"version":1,"command":…}` → one JSON line. Commands include `status`, `models_get`, `general_get`, `audio_get`, `history_list` (paging by `before` timestamp, `query`), `history_stats`, `history_retry` and `shortcut_event`. It is documented in the panel's `PROTOCOL.md` and **pinned by `tests/status_contract.py` against `tools/fake-daemon.py`**.
- **CLI** (JSON output; same verbs as the Mac and Windows CLIs): `status`, `record|start|stop|read`, `transcribe FILE [--insert]`, `models select`, `general show|set KEY JSON`, `vocab …`, `history list|search|play|retry|delete`, `shortcuts show|set|apply`, `license status|activate|validate|remove`, `update check|apply`, `doctor`, `settings [--tui]`.
- **Files:**
  - `~/.config/superwhisper/preferences.json` + `settings/settings.json` (JSON with migrations and a `settings.lock`; "Change settings through the CLI, never by editing files").
  - `license.json` (must be an owned regular file with mode 0600) and `cloud-credentials.json`.
  - `recordings/<ts>/meta.json` + audio, with failures stored as an empty `result` + `linux.error`.
  - `database/superwhisper.sqlite` (WAL; `recording_stat_lines` survives history deletion).
  - Models under `$XDG_DATA_HOME/superwhisper/models`.
  - The runtime dir is `$XDG_RUNTIME_DIR/superwhisper`.
- **D-Bus names:** `com.superwhisper.Indicator`, `com.superwhisper.ShellOverlay` (`/com/superwhisper/Hold`), `com.superwhisper.settings`, plus `org.kde.StatusNotifierItem` and freedesktop Notifications.
- **Agent skill** (`SKILL.md`): lets coding agents configure Superwhisper only through the CLI.

### 5.10 Network: auth, licensing, telemetry, updates [V]

| Endpoint | Purpose |
|---|---|
| `https://api.superwhisper.com/v2/inference/regions` | S1 cloud region discovery (allow-listed hosts: "Untrusted S1 region host") |
| `https://api.superwhisper.com/v2/inference/key` | short-lived inference credential (`cloud-credentials.json`, `cloud-device-id`) |
| `https://api.superwhisper.com/license/validate` (…) | Pro license activation and validation; device linking |
| `https://us.aws.superwhisper.com` | region host (multipart audio and prompts) |
| `https://o4505500361752576.ingest.us.sentry.io/4512142945681408` | Sentry DSN (override `SW_SENTRY_DSN`; toggle `errorLoggingEnabled`/`SW_ERROR_LOGGING`; "Rust panic (message removed)"). Default on or off: **[U]** |
| `https://tux.superwhisper.com/{stable,beta}.json` | update manifests (systemd timer every 6 h) |
| `https://models.superwhisper.com/…`, `huggingface.co/handy-computer/…` | model downloads (sha256-pinned) |
| `https://superwhisper.com/checkout?utm_source=linux-app` | upsell link |

No PostHog, Mixpanel or Amplitude strings were found. Test endpoints (`SW_API_TEST_URL`, `SW_CLOUD_TEST_URL`) must be HTTP **loopback**.

---

## 6. Local vs cloud

| Stage | Offline / Offline Lite | Cloud |
|---|---|---|
| Hotkey, capture, VAD, history, insertion, paste check | local | local |
| ASR | Cohere Transcribe Q4 / Parakeet v3 Q4 (Vulkan or CPU) | S1 cloud (audio as Ogg/Opus) |
| Vocabulary | supervocab acoustic KWS + fusion (local) | `asr_prompt` + `vocab.bin` + `enable_audio_vocab` sent |
| Cleanup | S1 Mini (llama.cpp, CPU/Vulkan) / rules (Lite) | S1 via `llm_prompt` in the same request |
| Vocabulary suggestions and correction learning | local only ("nothing leaves your computer") | same |
| Licensing, usage, crash/paste reports, updates | network | network |

---

## 7. Omarchy integration (task 3) [V]

- On Omarchy branch `quattro`, `config/omarchy/defaults/dictation` contains `superwhisper`. Fresh installs get the preinstalled `superwhisper-bin` package from Omarchy's own repo (OPR; not in the AUR). Per-user setup runs on first login via `install/user/first-run/superwhisper.sh` → `omarchy-install-dictation-superwhisper --background`.
- **Backend contract, documented in the manual and open to third parties:**
  - `omarchy-dictation-<backend> start|stop|toggle` must return promptly and exit nonzero on failure; `stop` must succeed when idle.
  - `omarchy-install-dictation-<backend>` does setup and then calls `omarchy-dictation-use <backend>`, which stops the previous backend and saves the choice atomically under `flock`.
  - `~/.config/<backend>/shortcuts.lua` is optional desktop integration loaded by Hyprland's `dictation-backend.lua`.
  - Omarchy owns the keys: **Right Alt hold** (`o.bind_hold`, `ignore_mods`) and **Alt+Space toggle** (older installs: F9 / Super+Ctrl+X).
  - Backends: superwhisper, voxtype, hex.
- `omarchy-dictation-superwhisper` maps `start` → `superwhisper start`, `stop` → `superwhisper stop` (no-op when the service is inactive), and `toggle` → `superwhisper record`.
- The Omarchy installer forces Superwhisper's native toggle to `Ctrl+Alt+Space` and its native hold to `none`, so the two never collide with Omarchy's keys. The blog says it "starts in the cloud" with 10,000 free words.

---

## 8. Notable engineering choices

1. **Process isolation by capability.** The Rust daemon owns the state machine. Each heavy runtime (ggml ASR, llama.cpp, ORT) and each fragile desktop API (AT-SPI aborts the process when the bus is missing; portals) lives in a child process with a JSON-lines protocol and its own deadline. A crash in a toolkit binding cannot take the daemon down.
2. **A compositor-native paste transaction.** On Hyprland the focus check and the keystroke run *inside the compositor*, in one Lua chunk keyed by window address and pid. That narrows the time-of-check/time-of-use window to near zero.
3. **Insertion without the clipboard** via an input-method addon, with clipboard paste only as a fallback.
4. **Explicit "unknown" outcomes.** Paste verification separates "could not observe" from "failed", and telemetry is bucketed.
5. **Latency engineering everywhere.** Mic kept idle rather than suspended (upstreamed to WirePlumber), workers kept warm, GPU shaders prepared at model load, ASR prefix and cleanup prefill during recording **[I]**, draft-guided PLD, Opus upload, and a `release_to_text_ms` metric.
6. **Correctness guards.** A GPU self-check against a known clip; a baseline non-AVX2 build; the DRM vendor decides the Vulkan ICD package.
7. **Vocabulary as an acoustic verifier.** Phoneme-level evidence gates text replacement, and it is backend-agnostic. It works on top of Parakeet and Cohere, neither of which offers biasing in transcribe.cpp.
8. **Reversible system changes.** Marked blocks, symlinks into `current`, a journal of accessibility changes, "only when first linked" for GNOME and Omarchy so updates never re-enable something the user disabled, and an atomic `current` swap with health-check rollback.
9. **A UI-agnostic daemon contract** with a fake daemon and contract tests, so the Omarchy panel, GPUI, TUI, GNOME and CLI clients share one protocol and the daemon holds every list ("holds no product knowledge of its own").

---

## 9. Ideas hark should adopt (ranked)

Fit legend: **C** = Rust client, **A** = adapters (desktop/provider), **P** = server protocol (`proto/hark/v1/hark.proto`), **K** = packaging.

| # | Idea | Why for hark | Where |
|---|---|---|---|
| 1 | **Acoustic vocabulary verifier ("phonetic gate")**: G2P on the shortlist terms and spoken aliases → phoneme needles; phoneme-CTC posteriors of the utterance; CTC keyword scoring with a phoneme confusion matrix → hits with `{t0,t1,score,acoustic_sim,text_sim,posterior_deficit}`; fuse only when acoustic and text evidence agree; protect a common-word list; record near-miss diagnostics | It is exactly the "acoustic gating" hark's research calls for, and it works with *any* ASR, including ones without biasing and the offline laptop fallback. It gives an evidence-based alternative to text-only LLM correction, and near-miss logs feed the distractor/false-substitution evaluation | Server: new optional `AcousticVerify` capability (or fields on `FinalResult`) in **P**. CPU path in **C** for offline. Diagnostics into history |
| 2 | **transcribe.cpp as the local CPU/Vulkan fallback**: Parakeet TDT 0.6B v3 Q4_K_M (485 MB), MIT, with Rust bindings, streaming and token timestamps, plus a separate baseline build for CPUs without AVX2 | hark needs an offline laptop path without a GPU. Superwhisper shows Q4 Parakeet on CPU is good enough to ship. Pair it with #1, because transcribe.cpp exposes no hotword biasing | **C** (in-process or worker child) or a tiny local `hark-server` backend in **K** |
| 3 | **fcitx5 commit addon** (tiny .so + unix socket, `VERSION`/`INSERT`, refuse password fields). Omarchy already runs fcitx5 | No clipboard damage, layout-independent, ~4× faster. Fall back to paste for multi-line text going into terminals | **A** (Linux injector) + **K** |
| 4 | **Atomic Hyprland paste in Lua**: re-check active window `address`+`pid`, reject if a layer holds the keyboard or the session is locked, then `send_key_state` to `window='address:…'`; return a structured verdict | hark currently uses `hyprctl dispatch sendshortcut`, so this closes the focus race the README already guards against | **A** |
| 5 | **Implement Omarchy's dictation-backend contract**: `omarchy-dictation-hark start|stop|toggle` (→ `hark ctl`), `omarchy-install-dictation-hark`, optional `~/.config/hark/shortcuts.lua` | Takes over Right Alt / Alt+Space natively on the user's daily driver with zero Hyprland edits | **K** + **A** |
| 6 | **Work during recording**: (a) server pre-encodes and pre-decodes the streamed prefix so only the tail is processed after `EndOfAudio`; (b) a cleanup **prefill** at key-down (system prompt + context shortlist) in the KV cache; (c) **prompt-lookup / draft-guided speculative decoding** for cleanup whenever the output is full text | Directly attacks the ≤1 s p95 key-up→paste target. PLD matters less if the cleanup stays an edit list. Prefill matters either way | **P** (`SessionStart` → a `CleanupPrepare` hint; per-stage timings), server |
| 7 | **Mic warm start via WirePlumber**: set `session.fast-capture-start=true` on the capture stream (built into WirePlumber ≥0.5.19 with `node.slow-resume`) | Saves up to ~0.5 s of mic wake time. hark's 200 ms pre-roll currently has to cover it | **C** (stream props), doc note in **K** |
| 8 | **AT-SPI cursor context and paste verification**: read only the focused editable field of the *target pid* (bounded chars, deadline, skip `PASSWORD_TEXT`); use char-before/after for spacing and capitalization; after the paste, return `landed|missing|not_text|unknown` | Cheap, privacy-bounded join correctness, plus honest outcome logging that never treats invisible as failed | **C** context engine + history |
| 9 | **Versioned NDJSON control protocol + fake daemon + contract test** for UI clients (Waybar/Quickshell/TUI); the daemon owns every list | hark's `ctl.sock` is line-based text, and a status UI is ranked #2 in the competitive doc | **C** |
| 10 | **GPU/ASR self-check** at load with a bundled known clip and expected output; CPU fallback; show in `hark doctor` | Catches broken Vulkan/driver results before the user dictates | server + **C** doctor |
| 11 | **Vocabulary suggestions from local sources, asked first**: env-var discovery for agent session stores (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR`, `COPILOT_HOME`, `CURSOR_DATA_DIR`, `GROK_HOME`), git authors, desktop entry names, with an explicit accept/dismiss flow | hark already reads agent sessions per dictation. A persistent, user-approved lexicon complements the per-dictation shortlist | **C** context engine |
| 12 | **Packaging pattern**: minisign-signed manifest per channel/arch, per-version dirs + atomic `current` swap + post-start health check with rollback, systemd user unit (`UMask=0077`, `PartOf=graphical-session.target`, `ExecStopPost` restore hooks), `--dry-run`, a journal of every system change for uninstall | Safe self-update for a daemon that holds mic and keyboard power | **K** |
| 13 | **Wayland virtual-keyboard and portal fallbacks** (`zwp_virtual_keyboard_v1`; RemoteDesktop `NotifyKeyboardKeysym` with modifier release first; consume a lone Alt hotkey so Electron menus don't steal focus) | Wider desktop coverage beyond Hyprland | **A** |
| 14 | **Per-stage timing names in history** (`release_to_text_ms`, `vad_wall_ms`, `asr_ms`, `cleanup_ms`, `prepare_ms`, `acoustic_vocab_ms`) | Directly comparable latency benchmarks against Superwhisper | **C** history, **P** timings |
| 15 | Media handling (MPRIS pause or per-stream duck) restored from a journal on stop or crash (`ExecStopPost`) | Nice to have, and the restore guarantee is the important part | **C** + **K** |

## 10. Things to avoid (or do differently)

- **A global keystroke stream for learning.** Their Hyprland hook sends *every* key event with the window address to the daemon to reconstruct terminal edits. For hark, which handles terminals, code and secrets, prefer explicit correction signals (undo/swap-to-raw, re-dictation), or a narrowly scoped AT-SPI diff of the target field with a time limit. Never log keys globally.
- **Turning on accessibility globally without consent.** Forced `--force-renderer-accessibility`, `QT_ACCESSIBILITY`, and GNOME toolkit accessibility have performance and privacy side effects (cf. their "Chromium tabs crash on NVIDIA…" note). If needed, ask first and keep the undo journal.
- **Writing into other tools' config homes unasked.** They symlink their agent skill into `~/.claude`, `~/.codex` and **`~/.pi/agent/skills`**, edit shell rc files and `environment.d`, restart fcitx5, replace a WirePlumber hook, and rewrite Omarchy's `shell.json`. The reversibility is good, but hark should be opt-in per integration.
- **`curl | sh` with signature verification optional.** Their bootstrap skips minisign when it is absent. If hark ships a bootstrap, require the signature, or pin the key and verify with a bundled verifier.
- **A Python + PyGObject runtime dependency** for core paths (AT-SPI, portals). It adds a distro-dependent dependency set. In Rust, `atspi` and `zbus` plus `ashpd` cover these, as their own settings binary already shows by linking ashpd and zbus.
- **Embedding a telemetry DSN with unclear default consent.** If hark adds error reporting, make it off by default and keep their good part: bucketed fields, no text, no titles.
- **Assuming a US layout** for keystroke reconstruction or chord synthesis. Use keysyms (portal) or IME commit.
- **Cloud-first defaults.** hark is local-first, and context must only go to providers whose privacy policy allows it (already in hark's provider registry design).

## 11. Unknowns / next steps
- **[U]** Exact `asr-worker` and `cleanup-worker` JSON schemas, and whether `prepare` really decodes a streaming prefix. To confirm, read the protocol strings in context with `objdump -d`, or run it in a sandbox (out of scope here).
- **[U]** The `vocab.bin` format and how the cloud uses it. The fusion thresholds.
- **[U]** Whether Linux sends any application, selection or clipboard context to S1 cloud.
- **[U]** Sentry default consent state. Hosting of `models.superwhisper.com`.
- To validate idea #1 cheaply: run `wav2vec2-xlsr-espeak` phoneme CTC (public upstream checkpoints exist) plus a public G2P over hark's existing context-ranking dataset, and measure hit precision with distractor terms.

## Sources
- Bootstrap and release: https://tux.superwhisper.com/install.sh ; https://tux.superwhisper.com/stable.json ; https://tux.superwhisper.com/beta.json ; https://tux.superwhisper.com/x86_64/v1.3.1/superwhisper-omarchy-x86_64-1.3.1.tar.zst (+ aarch64, .minisig)
- Releases and notes: https://github.com/superultrainc/superwhisper-linux-releases (API `releases?per_page=60`, 17 releases v0.1.0-beta.13 → v1.3.1)
- Omarchy: https://github.com/omacom/omarchy/tree/quattro (`bin/omarchy-dictation*`, `bin/omarchy-*-dictation-superwhisper`, `default/hypr/bindings/dictation.lua`, `default/hypr/dictation-backend.lua`, `install/user/first-run/superwhisper.sh`, `manual/11-text-extraction-dictation.md`)
- Vendor: https://superwhisper.com/blog/omarchy ; https://superwhisper.com/blog/cohere
- Runtime: https://github.com/handy-computer/transcribe.cpp (MIT; 16 families; Vulkan/CUDA/Metal/HIP; Rust bindings) ; Cohere Transcribe 03-2026 (2B, Apache-2.0, 14 languages): https://docs.cohere.com/docs/transcribe
- WirePlumber fast-capture-start: https://gitlab.freedesktop.org/pipewire/wireplumber/-/merge_requests/921 (cited in the shipped `suspend-node.lua`)
