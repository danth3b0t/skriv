# hark on Linux (Arch / Omarchy): install, always-on services, remote GPU server

Topology this is written for: a **laptop without a GPU** (Hyprland/Omarchy) runs the hark client; a **desktop
with an RTX 3060** runs `hark-server` + llama-server; they talk over **Tailscale with mTLS**. The laptop keeps a
local CPU engine as the fallback when the desktop is off.

```
laptop (Omarchy)                                   desktop (RTX 3060)
 hark.service ── gRPC + mTLS over Tailscale ──────► hark-server.service :50051 ──► hark-llama.service :8080
  (hark run)                                         (ASR + TextCleanup proxy)     (llama-server, cleanup)
  └─ fallback: hark-server.service on 127.0.0.1 (CPU engine, feat/m1-cpu)
```

## 1. Install the package

Until the repository is public the AUR recipe is built from a local clone (see
[packaging/aur/README.md](../packaging/aur/README.md) for why it bundles its own Python):

```sh
cd packaging/aur/hark-git
HARK_REPO="file://$HOME/projects/hark#branch=main" makepkg -si
# later, from the AUR: paru -S hark-git
```

It installs `/usr/bin/hark`, `/usr/bin/hark-server`, `/usr/bin/hark-certs`, the Omarchy adapter
(`/usr/bin/omarchy-*-dictation-hark`, `/usr/bin/hark-panel`, and the bar plugin in `/usr/share/hark/omarchy/hark.shell`), the systemd **user** units `hark.service`, `hark-server.service` and
`hark-llama.service` (in `/usr/lib/systemd/user`, inert until enabled) and a private CPython 3.12 + venv in
`/usr/lib/hark`. Optional packages, by role:

| Machine | Packages |
|---|---|
| both | `libnotify` (notifications), `pipewire` (`pw-play` sounds), `tailscale` |
| laptop / any client | `gtk4-layer-shell` (recording indicator), `at-spi2-core` (caret), `wtype` (paste fallback outside Hyprland) |
| desktop (cleanup on the GPU) | `llama-cpp` + `ggml-cuda` (NVIDIA) or `ggml-vulkan` |

From a checkout instead of the package: `uv tool install --editable ./client` (and `uv sync --extra gi` for the
indicator/AT-SPI, which builds PyGObject: needs `gobject-introspection cairo pkgconf base-devel`). `hark service`
then writes units pointing at that checkout's executables.

## 2. The client as a service

```sh
hark service install            # = client: enables + starts hark.service
hark service status             # installed units (or name them): running/stopped, enabled, restarts, unit file
hark service restart            # the installed units (or name them: restart server)
journalctl --user -u hark -f
```

`hark.service` is `Type=notify`: it is *started* once the control socket answers, and its watchdog is fed only
while `hark ctl ping` gets a reply, so a wedged daemon is restarted (`Restart=on-failure`; a config error, exit 2,
is not retried). It is `PartOf=graphical-session.target` and needs `WAYLAND_DISPLAY` (and on Hyprland
`HYPRLAND_INSTANCE_SIGNATURE`) in the user manager's environment:

- **Omarchy / uwsm**: uwsm exports them before `graphical-session.target`; nothing to do.
- **Hyprland without uwsm**: `graphical-session.target` never starts, so add to `hyprland.conf`
  (`hark service install` prints this line when it detects the case):
  ```ini
  exec-once = systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_CURRENT_DESKTOP && systemctl --user start hark.service
  ```

Without a display variable the unit's `ExecCondition` skips the start instead of running a daemon that cannot
paste. `hark service install` run from a terminal inside the session imports the variables itself.
Customise units with drop-ins (`systemctl --user edit hark.service`), not by editing them.

### Keys

Omarchy owns its dictation keys (hold **Right Alt**, **Alt+Space** toggle); hand them to hark with:

```sh
omarchy-install-dictation-hark      # hark service install client + omarchy-dictation-use hark (+ bar, Super Alt D)
omarchy-remove-dictation-hark       # undo (config, history and learned vocabulary stay)
```

From a checkout run `packaging/omarchy/omarchy-install-dictation-hark`; it links the scripts into `~/.local/bin`.

### Omarchy: pill, bar glyph and quick panel

Design: [design/SPEC.md](../design/SPEC.md). Omarchy 4 (quattro) draws its whole desktop in one QuickShell process,
`omarchy-shell`: Waybar, Walker and mako are gone, the bar and its panels are shell plugins, keybindings are Lua
(`~/.config/hypr/bindings.lua`, `o.bind`), and the theme is `~/.local/state/omarchy/current/theme/colors.toml`
(checked against `basecamp/omarchy@quattro`, November 2026). On Omarchy 4, `omarchy-install-dictation-hark`
also:

- copies the **`hark.shell`** bar-widget plugin into `~/.config/omarchy/plugins/hark.shell/` (copies: the shell
  refuses symlinks inside a plugin), then `omarchy-shell shell rescanPlugins` and `omarchy plugin enable hark.shell`
  (it lands in the right section; move it with `omarchy bar move hark.shell`);
- appends two marked lines to `~/.config/hypr/bindings.lua` and reloads Hyprland:
  ```lua
  -- hark: dictation panel (omarchy-remove-dictation-hark removes this line and the next)
  o.bind("SUPER + ALT + D", "Dictation panel", "hark-panel")
  ```
- ends with `hark doctor`.

Without `omarchy-shell` (Omarchy 3, plain Hyprland) these steps are skipped. `omarchy-remove-dictation-hark`
disables and deletes the plugin (only if its manifest says `hark.shell`) and removes the two lines.

**Pill** (the daemon's own GTK layer-shell overlay, bottom centre): red dot and level meter while recording (a
lock when hands-free, `1:12` after a minute, the pinned profile), three dots while working, then ✓ for a moment
(*sent*; one tag at most: mic dropped, Stopped at 2:00, cleanup skipped, via laptop),
Copied, Copied · focus moved, Paste failed · copied, Failed · server timeout, Mic lost, Paused, Cancelled / No
speech. Failures name **Super Alt D**, because the pill is click-through. Colours and text size follow the theme
(re-read on change); glyphs come from the Nerd Font Omarchy sets as `monospace`.

**Bar glyph**: three bars; red while recording, accent while working, a mic-off glyph when paused, dimmed when
hark is off (no socket, or no status line for 35 s), and a red dot when something needs you (mic lost, server
down, config changed or a restart needed, or a failed dictation you have not looked at). The tooltip and the
accessible name read `hark · ready · desktop 38 ms`, or the problem. Left click opens the panel, middle click
pastes the last dictation again.

**Quick panel** (left click or **Super Alt D**; arrows or hjkl, Enter, Esc, Tab to the next bar panel):

| row | Enter | other keys |
|---|---|---|
| hark · server · rtt, **Pause** / **Resume** | `hark ctl pause` / `hark ctl resume` | |
| problem line (Not running, Mic lost, Server down, Restart needed, Config changed) | `systemctl --user start hark.service`, `hark service restart`, `hark doctor`, `hark ctl reload` | |
| last dictation: preview, app · age · tag | copy (`hark last --copy`); on a failed one, retry | **P** closes the panel, then `hark ctl paste-last` into the window that had focus; **R** `hark retry last` (when its audio is kept) |
| Config · Doctor | `$EDITOR` via `omarchy-launch-editor ~/.config/hark/config.toml`; `hark doctor` in a floating terminal | |

The plugin keeps one `subscribe` connection to the control socket (`$XDG_RUNTIME_DIR/hark/ctl.sock`), which never
carries dictated text; the last dictation's text comes from `hark history -n 1 --json`, read when the panel opens
and never logged. `hark-panel` calls the widget's own IPC target (`omarchy-shell hark.shell toggleAt last`):
Omarchy drops summon payloads for bar widgets, so `omarchy-shell shell toggle hark.shell '{"cursor":"last"}'`
from the SPEC would not reach it.

**Untested on hardware.** None of this has run on Omarchy yet: the QML was written against the quattro shell
sources (`shell/Ui/{Panel,KeyboardPanel,PanelKeyCatcher,CursorSurface,BarIconButton}.qml`) and only
syntax-checked (`qmllint`) and validated (`omarchy-plugin-validate`) on macOS; its rules (`Model.js`) run under
Node in the test suite against the real daemon's status lines; the GTK pill and the scripts' Omarchy calls
(`omarchy-shell`, `omarchy plugin enable`, `omarchy-restart-hyprctl`) are exercised with fakes. Known gaps: the
Profile row and History (SPEC §6.4, §7), the pill's near-limit countdown and *Server down · saved* while
recording, the theme's corner radius (the pill is square, Omarchy's default), and whether the panel's
paste lands in the right window (250 ms after it closes). `pause` / `resume` are the ctl names this UI expects
from the daemon. If something misbehaves: `journalctl --user -t omarchy-shell` (QML errors), `omarchy plugin
list`, `omarchy-shell hark.shell status`.

Plain Hyprland, push-to-talk on F13 (`bindr` fires on release), hyprlang (≤ 0.54):

```ini
bind  = , F13, exec, hark ctl start
bindr = , F13, exec, hark ctl stop
bind  = SUPER, Escape, exec, hark ctl cancel
bind  = SUPER, V, exec, hark ctl paste-last
```

Lua config (≥ 0.55):

```lua
hl.bind("F13", hl.dsp.exec_cmd("hark ctl start"))
hl.bind("F13", hl.dsp.exec_cmd("hark ctl stop"), { release = true })
```

`hark ctl toggle` suits a single tap key. Details (paste path, focus re-check): [client/README.md](../client/README.md).

## 3. The desktop: GPU server + cleanup LLM

```sh
sudo pacman -S llama-cpp ggml-cuda
/usr/lib/hark/scripts/fetch-models          # S1-mini cleanup model -> ~/.local/share/hark/models
hark-certs ca                               # private CA in ~/.config/hark/pki (section 4)
hark-certs server desktop desktop.tail1234.ts.net 100.101.102.103 127.0.0.1
$EDITOR ~/.config/hark/server.toml          # below
hark service install server llama
loginctl enable-linger                      # keep them running without a login session
```

`~/.config/hark/server.toml` (absolute paths: the server does not expand `~`; all keys in
[hark-server.example.toml](../server/hark-server.example.toml)):

```toml
[server]
listen = "100.101.102.103:50051"     # the desktop's Tailscale IP (tailscale ip -4); or 0.0.0.0:50051
name = "desktop"
accelerator = "RTX 3060"

[server.tls]
cert = "/home/me/.config/hark/pki/desktop.crt"
key = "/home/me/.config/hark/pki/desktop.key"
client_ca = "/home/me/.config/hark/pki/ca.crt"   # mTLS: only certs from this CA get in

[backend]
kind = "mock"        # until a Linux GPU backend lands: nemo_gpupb is a stub (server/README.md)

[cleanup]
base_url = "http://127.0.0.1:8080/v1"
model = "s1-mini-q4_k_m"
engine = "s1"
```

- `hark-server.service` is `Type=notify`: *started* means the model is loaded and warm (health SERVING; the first
  start may download it, hence `TimeoutStartSec=15min`). Its watchdog is fed from the event loop. Restarts back off
  from 5 s to 2 min, so a listen address that is not up yet at boot (Tailscale) is retried; exit 2 (config) and 3
  (backend unavailable) are not.
- `hark-llama.service` runs `llama-up` (llama-server on 127.0.0.1:8080, `-ngl 99`, flags in
  [scripts/llama-up](../scripts/llama-up)) and counts as started when `/health` answers 200. It uses the pinned
  binary from `fetch-llama` if present in the models dir, else `llama-server` from `llama-cpp`. If the repo's
  llama.cpp rejects a flag, `/usr/lib/hark/scripts/fetch-llama cuda` fetches the pinned build.
- Plaintext is refused on non-loopback addresses unless you set `allow_insecure_non_local` (don't).

## 4. Tailscale + mTLS

Tailscale already encrypts and authenticates devices; mTLS on top means only machines holding a hark client
certificate can use the server, not every device or shared node on the tailnet. `hark-certs` (OpenSSL, EC P-256,
keys 0600, never overwrites):

```sh
# on the desktop (keeps ca.key)
hark-certs ca
hark-certs server desktop desktop.tail1234.ts.net 100.101.102.103 127.0.0.1   # every name/IP clients dial
hark-certs client laptop
scp ~/.config/hark/pki/{ca.crt,laptop.crt,laptop.key} laptop:.config/hark/pki/
rm ~/.config/hark/pki/laptop.key      # the laptop's key now lives only on the laptop
```

On the laptop, `~/.config/hark/config.toml`:

```toml
[[providers]]
name = "desktop"
address = "desktop.tail1234.ts.net:50051"     # or "desktop:50051" with MagicDNS
tls_ca = "~/.config/hark/pki/ca.crt"
tls_cert = "~/.config/hark/pki/laptop.crt"
tls_key = "~/.config/hark/pki/laptop.key"
# tls_server_name = "desktop"                 # only if you dial a name/IP that is not in the server cert
```

Check with `hark doctor` (server line says `TLS`, health SERVING). Optionally restrict port 50051 to your own
devices in the Tailscale ACL. Limits: any certificate from the CA is accepted (no per-client allowlist), and
there is no revocation: to shut a client out, make a new CA and re-issue the others. The CA is valid 10 years,
leaf certificates 825 days (`--days N`).

## 5. Laptop fallback: local CPU engine

When the desktop is off, the laptop runs its own `hark-server` on loopback with the CPU ASR engine (being built
in `feat/m1-cpu`; see that branch's docs for the backend name and model download):

```sh
# ~/.config/hark/server.toml on the laptop: loopback, so plaintext is fine
#   [server]  listen = "127.0.0.1:50051"
#   [backend] kind = "<cpu engine from feat/m1-cpu>"
hark service install server
```

How the client chooses between the desktop and the local engine (health-checked fallback) is part of the CPU
engine work; until it lands, switch by hand: `hark --server 127.0.0.1:50051 run` (one provider at that address), or
list the local server as a second `[[providers]]` entry (client/README.md, "Providers and the offline fallback").

## Troubleshooting

| Symptom | Look at |
|---|---|
| keys do nothing | `hark service status`; `hark ctl status` (exit 2 = no daemon); `omarchy dictation toggle` |
| `hark.service` "condition failed" | no `WAYLAND_DISPLAY` in `systemctl --user show-environment`: section 2 |
| pastes nowhere / dry-run | `hark doctor` (paste section: wl-copy, hyprctl, wtype) |
| server unreachable | `hark doctor`; `journalctl --user -u hark-server`; `tailscale ping desktop`; cert names (`openssl x509 -in desktop.crt -noout -ext subjectAltName`) |
| cleanup skipped | `journalctl --user -u hark-llama`; `curl 127.0.0.1:8080/health` on the desktop |
| no indicator | `gtk4-layer-shell` installed? GNOME has no layer-shell |
| no hark in the bar / Super Alt D does nothing | `omarchy plugin list` (hark.shell enabled?); `omarchy-shell hark.shell status`; `journalctl --user -t omarchy-shell` |
