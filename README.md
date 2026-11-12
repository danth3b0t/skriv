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

Try it without a mic: `uv run hark once --wav some.wav --dry-run --profile terminal --cwd ~/some/repo`.

Linux (Arch/Omarchy): [docs/linux.md](docs/linux.md): the `hark-git` AUR package, always-on systemd user services
(`hark service install`), Omarchy/Hyprland keys, and a GPU desktop running `hark-server` for a laptop over
Tailscale with mTLS. Client details: [client/README.md](client/README.md).

## Layout

| Path | What |
|---|---|
| `proto/hark/v1/hark.proto` | client ↔ inference-server protocol (gRPC), see `docs/protocol/ADR-001-protocol.md` |
| `server/` | `hark-server`: ASR backends (mock, parakeet_mlx with decode-time boosting, NeMo GPU-PB stub) + TextCleanup proxy |
| `client/` | `hark`: hotkey, audio, context engine, streaming, canonicalization, cleanup, guardrails, paste |
| `proto-py/` | generated Python stubs (`proto-py/gen-proto`) |
| `scripts/` | `poc-up`, `fetch-llama`, `fetch-models`, `llama-up`, `eval-cleanup`, `hark-certs` (mTLS) |
| `packaging/` | AUR `hark-git`, systemd user units, Omarchy dictation adapter |
| `docs/` | context, research-driven decisions, protocol, dataset spec, benchmarks |

## Development

uv workspace; hooks via lefthook (`uv run lefthook install`): ruff, basedpyright (strict), ty, Conventional
Commits (commitizen), pytest on push. Never bypass the hooks.
