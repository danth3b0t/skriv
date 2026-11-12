# Hark.app (macOS)

`scripts/build-macos-app` builds `dist/Hark.app`, a menu-bar app (`LSUIElement`, bundle id `dev.hark.Hark`) that
runs `hark app`: the `hark run` daemon plus a status item and a first-run setup window. Its point is that the
Microphone, Accessibility and Input Monitoring grants belong to **Hark**, not to the terminal you started hark from.

```sh
uv sync
scripts/build-macos-app            # -> dist/Hark.app (ad-hoc signed)
open dist/Hark.app                 # or copy it to /Applications first, then open it there
uv run hark service install        # optional: llama-server + hark-server as launchd agents
```

## How the bundle works

```
Hark.app/Contents/
  Info.plist        CFBundleIdentifier dev.hark.Hark, LSUIElement, NSMicrophoneUsageDescription,
                    NSAppleEventsUsageDescription, HarkVenv = /abs/path/to/checkout/.venv
  MacOS/Hark        native launcher (launcher.swift, ~85 KB)
```

The launcher reads `HarkVenv`, loads the venv's base interpreter (`libpython3.12.dylib` of the uv-managed CPython,
found through `pyvenv.cfg`) and calls `Py_BytesMain` with `argv[0]` = the venv's `bin/python`, so `python -m
hark_client app` runs **in the bundle's own process** (`sys.prefix` = the venv, `sys.executable` = its python).
Python code is not copied: editing it needs no rebuild and never changes the signature. Without a terminal, stdout
and stderr go to `~/Library/Logs/hark/app.log`. `HARK_VENV` overrides the venv.

**TCC attribution.** macOS attributes protected access to the *responsible process*. An app launched by
LaunchServices (Finder, `open`, a login item; parent = launchd) is its own responsible process, and its children
inherit that (so `hark retry` / `hark doctor` started from the menu run with Hark's grants). A process started from a
shell inherits the *terminal's* responsibility, so when the launcher's parent is not launchd it re-spawns itself
with `responsibility_spawnattrs_setdisclaim` (private SPI, as Qt Creator and others use) and waits for that child.
Verified on macOS 26.5: launched with `open`, ppid 1 and `responsibility_get_pid_responsible_for_pid(pid) == pid`;
started from a shell, the re-spawned child is its own responsible process.

**Why not py2app / Briefcase / a child-process launcher.**

| | verdict |
|---|---|
| py2app | Copies the interpreter and site-packages into the bundle: every rebuild (every Python edit) is a new signature, so ad-hoc grants are lost; awkward with a uv workspace of editable packages. The right shape only for a distributable build. |
| Briefcase | Its own project layout, templates and Python support package; heavy for the dev loop, same re-signing problem. |
| Launcher that spawns `python` as a child (e.g. tcc-venv) | Grants do land on the launcher, but Python is a second process whose main bundle is not Hark.app: `SMAppService.mainApp`, the app's name/icon in LaunchServices and Info.plist usage strings do not apply to the process that calls AppKit. |
| **Embed libpython in the launcher (chosen)** | One process, Hark.app's identity, tiny reproducible binary; Python stays in the venv. |

## Signing and keeping the grants

TCC stores a grant with the app's *designated requirement*. For an ad-hoc signature (`codesign -s -`, the
default) that is the cdhash, a hash of the launcher and Info.plist. The build is reproducible: same launcher source,
same compiler and same `HarkVenv` path give the same cdhash, so plain rebuilds keep the grants. Changing the launcher,
the plist (e.g. building from another checkout path) or the Swift compiler creates a new identity: grant again, and
remove the stale "Hark" entry in System Settings.

For a stable identity across launcher changes, sign with a self-signed code-signing certificate; the requirement
then is `identifier "dev.hark.Hark" and certificate leaf = H"…"`:

1. Keychain Access → Certificate Assistant → Create a Certificate…: name `Hark Dev`, Identity Type *Self Signed
   Root*, Certificate Type *Code Signing*.
2. `HARK_CODESIGN_IDENTITY="Hark Dev" scripts/build-macos-app` (allow codesign to use the key when asked).

Reset grants: `tccutil reset Microphone dev.hark.Hark` (also `Accessibility`, `ListenEvent`). Input Monitoring and
a Microphone grant given while Hark runs take effect after a restart (setup window: *Restart Hark*).

No hardened runtime: the launcher loads the uv-managed libpython and the venv's extension modules from outside
the bundle, which library validation refuses.

### Developer ID and notarization (later, for distribution)

Notarization needs a Developer ID Application certificate, the hardened runtime and every executable inside the
bundle, signed. Steps:

1. Make the bundle self-contained: copy a uv-managed CPython into `Contents/Frameworks/python` and install the
   locked, non-editable dependencies into `Contents/Resources/venv` (`uv export --frozen --no-dev --no-editable`
   + `uv pip install --target`, or a `uv venv --relocatable`); point the launcher at bundle-relative paths instead
   of `HarkVenv`.
2. Sign inside-out with a timestamp and the hardened runtime: every `.so`/`.dylib`, then `libpython`, then the app:
   `codesign --force --timestamp --options runtime --entitlements hark.entitlements --sign "Developer ID
   Application: … (TEAMID)" …`. Entitlements: `com.apple.security.device.audio-input` (the hardened runtime blocks
   the mic without it) and `com.apple.security.automation.apple-events`; `com.apple.security.cs.disable-library-
   validation` only if some library cannot be re-signed with the team's identity.
3. `ditto -c -k --keepParent Hark.app Hark.zip`, `xcrun notarytool submit Hark.zip --keychain-profile hark --wait`,
   `xcrun stapler staple Hark.app`, check with `spctl -a -vv Hark.app`.

## Menu, setup window, services

Menu: state (icon: `mic` ready, `mic.fill` listening, `waveform` transcribing, `mic.slash` paused, a warning
triangle for 10 s after a failed dictation), the last dictation, Copy / Retry Last Dictation, Pause / Resume
(releases an always-open mic), Profile (Automatic or a forced profile: the same override as `hark ctl profile set NAME|auto`, for the next
dictation unless `[profile_switch] sticky = true`), Open History (Terminal, `hark history`),
Settings… (opens the config file in the default text editor; restart to apply), Setup & Permissions…, Run Doctor
(runs `hark doctor` with Hark's permissions, opens the report), Launch at Login (`SMAppService.mainApp`; a
`~/Library/LaunchAgents/dev.hark.app.plist` that `open`s the app where that is unavailable), Quit.

The setup window opens at launch while a permission is missing (`hark app --no-onboarding` skips it). It asks
for each permission with the system prompt first (`AVCaptureDevice requestAccess`, `AXIsProcessTrustedWithOptions`,
`CGRequestListenEventAccess`), then opens the matching System Settings pane; the mic level and server rows run
`hark doctor`'s checks, and the hotkey row turns green once a dictation starts (dictate into its text box). Unlike
the recording indicator it takes focus; closing it hands focus back.

`hark service install|uninstall|status|restart` manages `~/Library/LaunchAgents/dev.hark.llama.plist` (scripts/llama-up)
and `dev.hark.server.plist` (`.venv/bin/hark-server`, same flags as scripts/poc-up), `RunAtLoad` + `KeepAlive`,
logs in `~/Library/Logs/hark/{llama,server}.log`. Options: `--backend`, `--cleanup-model KEY`, `--no-cleanup`.
Stop `scripts/poc-up` first: the agents need ports 8080 and 50051 (install warns if they are taken).

## Running a second instance for testing

The control socket and config can be redirected, so a test instance does not collide with a running hark:

```sh
open -n -g --env HARK_CTL_SOCKET=/tmp/hark-test/ctl.sock --env XDG_CONFIG_HOME=/tmp/hark-test/config \
  dist/Hark.app --args --no-onboarding
HARK_CTL_SOCKET=/tmp/hark-test/ctl.sock hark ctl --json status
kill -TERM "$(pgrep -f Hark.app/Contents/MacOS/Hark)"
```

Give it a config with `hotkey.listener = "none"` (two listeners would both react to the key) and
`audio.keep_open = false`.
