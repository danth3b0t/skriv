# hark (working name)

Context-aware, local-first dictation: hold a key, speak, and the text is pasted into the focused app.
Before decoding, hark looks at what you are working on (focused app, terminal working directory,
git repo vocabulary, the coding agent's recent turns read from its session files) and biases the
speech recognizer toward those identifiers and names; a small local model ("S1-mini" by "Superwhisper") then
tidies the text under strict guardrails. **This is a proof of concept.**

## Try the POC on macOS (Apple Silicon)

```sh
uv sync
scripts/poc-up          # downloads llama.cpp + S1-mini on first run, starts llama-server + hark-server
# in another terminal (the one you grant permissions to):
uv run hark doctor      # checks server, mic, permissions, paste, context
uv run hark run         # hold Right Option to dictate, release to paste; Esc while holding cancels
```

Grant the terminal app that runs `hark run` **Microphone**, **Accessibility** and **Input Monitoring**
(System Settings → Privacy & Security). Without Accessibility the hotkey and Cmd+V paste don't work.

### Hark.app: always there, with its own permissions

```sh
scripts/build-macos-app     # dist/Hark.app: menu-bar app running this checkout's .venv in-process
open dist/Hark.app          # setup window: Microphone, Accessibility, Input Monitoring, mic level, hotkey, servers
uv run hark service install  # llama-server + hark-server as launchd agents (stop scripts/poc-up first)
```

The permissions then belong to Hark (bundle id `dev.hark.Hark`), not to your terminal. The menu-bar item shows the
state and offers the last dictation, Copy/Retry last, Pause, a profile override, history, settings, doctor and
Launch at Login. Python edits need no rebuild. Signing (rebuilds keep the grants), TCC attribution and future
notarization: [packaging/macos/README.md](packaging/macos/README.md).

Try it without a mic: `uv run hark once --wav some.wav --dry-run --profile terminal --cwd ~/some/repo`.

Linux (Arch/Omarchy): [docs/linux.md](docs/linux.md): the `hark-git` AUR package, always-on systemd user services
(`hark service install`), Omarchy/Hyprland keys, and a GPU desktop running `hark-server` for a laptop over
Tailscale with mTLS. Client details: [client/README.md](client/README.md) (`hark ctl start|stop` bound with
`bind`/`bindr`). On Linux without a Mac-class GPU, run `hark-server` on another machine and list it in
`[[providers]]`, with the laptop's own CPU server as the offline fallback: `scripts/fetch-transcribe &&
scripts/cpu-server-up` (transcribe.cpp, Parakeet v3 Q4_K_M; ~0.5 s for 10 s of speech on 4 cores of an M5 Pro, see
[the bench](docs/bench/2026-11-12-transcribe-cpp-cpu.md)). Each dictation goes to the first healthy provider; see
[client/README.md](client/README.md#providers-and-the-offline-fallback).

## Layout

| Path | What |
|---|---|
| `proto/hark/v1/hark.proto` | client ↔ inference-server protocol (gRPC), see `docs/protocol/ADR-001-protocol.md` |
| `server/` | `hark-server`: ASR backends (mock, parakeet_mlx with decode-time boosting, transcribe_cpp CPU/Metal/Vulkan, NeMo GPU-PB stub) + TextCleanup proxy |
| `client/` | `hark`: hotkey, audio, context engine, streaming, canonicalization, cleanup, guardrails, paste |
| `proto-py/` | generated Python stubs (`proto-py/gen-proto`) |
| `scripts/` | `poc-up`, `fetch-llama`, `fetch-models`, `llama-up`, `eval-cleanup`, `fetch-transcribe`, `cpu-server-up`, `build-macos-app`, `hark-certs` (mTLS) |
| `packaging/` | `macos/` (Hark.app launcher), AUR `hark-git`, `systemd/` user units, `omarchy/` dictation adapter |
| `docs/` | context, research-driven decisions, protocol, dataset spec, benchmarks |

## Development

uv workspace; hooks via lefthook (`uv run lefthook install`): ruff, basedpyright (strict), ty, Conventional
Commits (commitizen), pytest on push. Never bypass the hooks.
