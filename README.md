# hark

Dictation for people who talk to coding agents. Hold a key, speak, let go: the text is pasted into the focused
terminal or chat box. hark is built for prompts to Claude Code, pi and Codex, in terminals and IDE chats, on
Omarchy (Hyprland) first and macOS second.

## How it differs

- **It knows what you are working on.** At key-down hark reads the focused terminal's git repository (files,
  branch, recent changes), the coding agent's session for that directory (your prompts, its replies, the paths
  its tools touched) and the text on screen, and gives the recognizer a short list of those identifiers before
  it decodes. `parse_config`, `kubectl`, `Hyprland` come out spelled right. An unbiased decode of the same audio
  keeps the list honest: a biased word the audio does not support is reverted.
- **Cleanup that does not change what you said.** A small local model (S1-mini) removes fillers, applies
  "no wait, Wednesday" self-corrections and punctuates. The client keeps its rewrite only if every identifier,
  number and negation survives and only a few content words changed; otherwise you get the uncleaned text.
- **Terminal-aware delivery.** In a terminal the text is one line, spoken symbols are written
  ("dash dash verbose" → `--verbose`), and Enter is never sent.
- **Local.** Recognition (Parakeet) and cleanup run on your machines: the laptop, or a GPU desktop over
  Tailscale with mTLS, with the laptop's CPU as the fallback.

## Quickstart: Omarchy

```sh
git clone <this repository> ~/projects/hark
cd ~/projects/hark/packaging/aur/hark-git
HARK_REPO="file://$HOME/projects/hark#branch=main" makepkg -si
omarchy-install-dictation-hark     # hark.service, Omarchy's dictation keys, bar glyph, panel on Super Alt D
```

hark needs a recognition server. On a GPU desktop: [docs/linux.md §3–4](docs/linux.md#3-the-desktop-gpu-server--cleanup-llm).
On the laptop's CPU (no cleanup there): [§5](docs/linux.md#5-laptop-fallback-local-cpu-engine), then list it in
`~/.config/hark/config.toml`:

```toml
[[providers]]
address = "127.0.0.1:50052"
```

`hark doctor` checks server, mic, paste tools and the daemon. Hold **Right Alt** to talk, or **Alt+Space** to
start and stop.

## Quickstart: macOS (Apple Silicon)

```sh
uv sync
scripts/poc-up                                 # fetches llama.cpp + S1-mini once; starts both servers
uv run hark doctor                             # (in another terminal)
uv run hark run                                # hold Right Option to talk; tap it for hands-free; Esc cancels
```

The terminal running `hark run` needs Microphone, Accessibility and Input Monitoring. Or build **Hark.app**
(`scripts/build-macos-app`): a menu-bar app that holds those permissions itself
([packaging/macos](packaging/macos/README.md)).

`uv run hark service install` keeps both servers running as launchd agents instead.

## The daily loop

**Dictate.** Hold the key, speak, release. A small pill at the bottom of the screen shows the level while you
talk, then ✓ once the text is sent. Speak your corrections ("three seconds, sorry, five seconds"); they are applied.
A dictation can be up to 5 minutes.

**Recover.** If the pill says *Copied*, *Paste failed* or *Failed*, nothing is lost:

| | |
|---|---|
| **Super Alt D** (Omarchy) | the panel: copy the last dictation, paste it again, retry it |
| the menu-bar item (Hark.app) | copy or retry the last dictation |
| `hark last` | print the last text; `--copy` puts it on the clipboard |
| `hark last --raw --copy` | the text before cleanup, when the cleanup changed too much |
| `hark retry` | re-run the last recording through the server (kept for the last 10 dictations) |
| `hark ctl paste-last` | paste the last text again into the focused window (bind it to a key) |

**Fix a word.** When a name keeps coming out wrong and no context will supply it:

```sh
hark words add "hyper land" Hyprland     # how you say it, how it is spelled; the daemon reloads
hark words add pie pi --repo             # only in this git repository (.hark/words.toml)
```

**Check.** `hark history` lists recent dictations with their latency; `hark history show ID` shows every stage
(recognized, corrected, cleaned, pasted). `hark ctl pause` releases the mic until `hark ctl resume`.

## Read more

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | the pipeline, which module owns each step, the daemon/UI contract, tests and eval |
| [client/README.md](client/README.md) | every command, config key and behaviour of the `hark` client |
| [server/README.md](server/README.md) | `hark-server`: backends, cleanup, configuration |
| [docs/linux.md](docs/linux.md) | Arch/Omarchy install, services, the Omarchy pill, bar and panel, GPU desktop over Tailscale + mTLS |
| [docs/context-engine.md](docs/context-engine.md) | what context is read and how terms are ranked |
| [docs/eval.md](docs/eval.md) | `hark eval` and the shared set `eval/dev-v1` |
| [docs/protocol/](docs/protocol/) | the gRPC protocol (ADR-001, CHANGES) and the control socket ([ctl-v1](docs/protocol/ctl-v1.md)) |
| [design/SPEC.md](design/SPEC.md) | the Omarchy UI specification |
| [docs/bench/](docs/bench/) | measurements; [docs/archive/](docs/archive/README.md) holds superseded ones and the research |

## Development

A uv workspace (Python 3.12): `client/` (`hark`), `server/` (`hark-server`), `proto/` and `proto-py/` (the
gRPC protocol and its generated stubs), `scripts/`, `packaging/`, `eval/`. Hooks via lefthook
(`uv run lefthook install`): ruff, basedpyright (strict), ty, Conventional Commits, pytest on push.

```sh
uv sync
uv run pytest -q
```
