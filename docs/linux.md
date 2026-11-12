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
(`/usr/bin/omarchy-*-dictation-hark`), the systemd **user** units `hark.service`, `hark-server.service` and
`hark-llama.service` (in `/usr/lib/systemd/user`, inert until enabled) and a private CPython 3.12 + venv in
`/usr/lib/hark`. Optional packages, by role:

| Machine | Packages |
|---|---|
| both | `libnotify` (notifications), `pipewire` (`pw-play` sounds), `tailscale` |
| laptop / any client | `gtk4-layer-shell` (recording indicator), `at-spi2-core` (caret + paste verification), `wtype` (paste fallback outside Hyprland) |
| desktop (cleanup on the GPU) | `llama-cpp` + `ggml-cuda` (NVIDIA) or `ggml-vulkan` |

From a checkout instead of the package: `uv tool install --editable ./client` (and `uv sync --extra gi` for the
indicator/AT-SPI, which builds PyGObject: needs `gobject-introspection cairo pkgconf base-devel`). `hark service`
then writes units pointing at that checkout's executables.

## 2. The client as a service

```sh
hark service install            # = client: enables + starts hark.service
hark service status             # client, server, llama: running/stopped, enabled, restarts, unit file
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
omarchy-install-dictation-hark      # hark service install client + omarchy-dictation-use hark
omarchy-remove-dictation-hark       # undo (config, history and learned vocabulary stay)
```

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
[server]
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
engine work; until it lands, switch by hand: `hark --server 127.0.0.1:50051 run`, or point `server.address` at
`127.0.0.1:50051` and drop the `tls_*` keys.

## Troubleshooting

| Symptom | Look at |
|---|---|
| keys do nothing | `hark service status`; `hark ctl status` (exit 2 = no daemon); `omarchy dictation toggle` |
| `hark.service` "condition failed" | no `WAYLAND_DISPLAY` in `systemctl --user show-environment`: section 2 |
| pastes nowhere / dry-run | `hark doctor` (paste section: wl-copy, hyprctl, wtype) |
| server unreachable | `hark doctor`; `journalctl --user -u hark-server`; `tailscale ping desktop`; cert names (`openssl x509 -in desktop.crt -noout -ext subjectAltName`) |
| cleanup skipped | `journalctl --user -u hark-llama`; `curl 127.0.0.1:8080/health` on the desktop |
| no indicator | `gtk4-layer-shell` installed? GNOME has no layer-shell |
