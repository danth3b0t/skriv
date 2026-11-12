# hark UI on Omarchy: design research

Date: 2026-11-12. Feeds `design/hark-ui.fig` and `design/SPEC.md`. Labels: **[V]** = verified in a file or page I
read (path or URL given), **[I]** = my inference, **[U]** = unknown or unverified. Omarchy references are to
`omacom/omarchy`, branch `quattro` (Omarchy 4), read on 2026-11-12. That branch moves fast, so file paths are
more reliable than line numbers.

## 0. Findings that shaped the design

1. **Omarchy 4 is a QuickShell desktop.** One long-running `omarchy-shell` Quickshell process hosts the bar,
   panels, menu, notifications, OSD, lock and polkit as plugins with a `manifest.json`. A hark UI therefore ships as
   an omarchy-shell plugin, not a standalone Quickshell config, and not as Waybar/mako/SwayOSD modules. [V
   `docs/omarchy-shell.md`, `manual/32-shell-plugins.md`, `shell/plugins/README.md`]
2. **There is a real design system to inherit.** Colours come from `colors.toml`. Surface roles, the type
   scale, spacing, control-state alphas and bar size come from `shell.toml`. Radius and gaps come from Hyprland
   (`decoration:rounding`, `general:gaps_out`). Motion goes through `Style.duration()`, which is 0 when Hyprland
   animations are off. hark needs no palette of its own. [V `shell/Commons/Color.qml`, `shell/Commons/Style.qml`,
   `default/themed/shell.toml.tpl`]
3. **Omarchy's own dim text fails contrast.** Panels draw secondary text as `Qt.darker(fg, 1.55)`, which gives 3.6:1
   on tokyo-night, 3.56:1 on gruvbox and 3.18:1 on everforest (WCAG AA needs 4.5:1). On light themes it is *darker*
   than the foreground, not dimmer. Yellow and green are below 3:1 on catppuccin-latte, rose-pine and flexoki-light.
   The `[controls]` 40 % border is below 3:1 on 16 of 18 themes. hark solves these per theme at runtime (numbers in
   `design/tokens/contrast.md`). [V computed with `design/src/tokens.py` from 18 theme files]
4. **Superwhisper's edge glow lights the window you are typing into**, not the screen: "the window you're typing
   into glows around the edges, so you know exactly where the text will show up". hark already captures the target
   window address at key-down for its in-compositor paste, so the same idea comes almost for free. hark can also
   show something Superwhisper does not: the glow stays on the original target, in warning colour, when focus moved
   and the text went to the clipboard. [V superwhisper.com/blog/omarchy; hark `client/README.md`]
5. **Superwhisper's Omarchy integration is a split design.** A QML bar plugin (hero, on/off switch, setting rows,
   last dictation, history link) uses only `qs.Commons`/`qs.Ui`. A separate GTK4 layer-shell process draws the
   pill and the glow and reads Omarchy's `colors.toml`. A GPUI app is the settings window. The daemon "holds every
   list" and the panel holds no product knowledge. [V static read of the 1.3.1 bundle in `/tmp/sw-linux`,
   `docs/research/superwhisper-linux-internals.md`]
6. **Voxtype's Omarchy panel is schema-driven.** It renders `voxtype config schema --json`, writes through
   `voxtype config set`, searches across sections, uses j/k with Enter, and marks keys that override a default
   with a reset action. That is the right model for hark's settings: one source of truth for defaults and
   validation (`config.py`). [V V9 README, below]
7. **Keeping the microphone open lights Omarchy's mic indicator all the time.** hark defaults to
   `audio.keep_open = true` (pre-roll). Omarchy's `omarchy.microphone` bar widget marks the mic "in use" whenever
   a non-meter capture stream exists. The settings must state this trade-off, and hark's own glyph must be the
   reliable "audio is being used" signal. [V `shell/plugins/bar/widgets/Microphone.qml`; hark `config.py`] [I: the
   exact interaction is untested on hardware]

## A. Omarchy's design system

### A1. Themes and theme files
- **Theme list on quattro (23):** catppuccin, catppuccin-latte, ethereal, everforest, flexoki-light, gruvbox,
  hackerman, jade-horse, kanagawa, last-horizon, lumon, lupine, matte-black, miasma, nord, retro-82, ristretto,
  rose-pine, solitude, starship, tokyo-night, vantablack, white. [V `themes/` tree]
- **`themes/<name>/colors.toml`** has `mode = "dark"|"light"`, `accent`, `selection`, `muted`, `background`,
  `dark_background`, `darker_background`, `lighter_background`, `foreground`, `dark_foreground`,
  `light_foreground`, `bright_foreground`, `red yellow orange green cyan blue magenta brown` and `bright_*`
  variants. Example: tokyo-night has accent `#7aa2f7` and bg `#1a1b26`; catppuccin-latte has accent `#1e66f5` and
  bg `#eff1f5`. [V `themes/tokyo-night/colors.toml`, `themes/catppuccin-latte/colors.toml`]
- **The current theme is at `~/.local/state/omarchy/current/theme/`.** The shell reads `colors.toml` and
  `shell.toml` from there and layers `~/.config/omarchy/shell.toml` on top (user keys win, watched live).
  `applyTheme` IPC pushes new colours on a theme switch. [V `Color.qml`]
- **App configs are generated from templates** in `default/themed/*.tpl` (alacritty, btop, chromium, foot,
  ghostty, kitty, hyprland.lua, neovim, vscode, fcitx5, obsidian, pi.json, …).
  `hyprland.lua.tpl` sets `active_border = hypr_gradient hyprland_active_border accent`, so the active window
  border *is* the accent unless a theme defines a gradient. There is no waybar/mako/walker template on quattro;
  those surfaces moved into the shell. [V `default/themed/`]
- **`Commons.Color`** exposes `foreground`, `background`, `accent`, `urgent` (= `red`, falling back to `color1`) and
  `muted`, plus per-surface roles: `bar.*`, `popups.*` (background, text, border = `hyprland.active-border`),
  `tooltip.*`, `notifications.*`, `menu.*` (scrim bg@0.5, selected bg fg@0.08, selected text accent), `polkit.*`
  (`border-error` = red), `lock.*`, `imagePicker.*`. Green, yellow and the other named colours are **not** exposed,
  so a plugin that needs them reads `colors.toml` itself, as Superwhisper's indicator does. [V `Color.qml`,
  Superwhisper indicator strings `omarchy/current/theme/colors.toml … accent background foreground`]

### A2. Typography, spacing, radius, borders
- **Font:** the fontconfig `monospace` alias, set with `omarchy font set` (default JetBrainsMono Nerd Font).
  `OMARCHY_MENU_FONT` can override the menu family. [V `Style.qml`]
- **Type scale** from `[font] base-size` (default 12, changed with `omarchy display text size`): caption 10,
  bodySmall 11, body 12, subtitle 13, title 14, heading 16, display 24, displayLarge 28; icon 14, iconLarge 18.
  [V `Style.qml`, `docs/omarchy-shell.md`]
- **Spacing:** `Style.space(px)` scales with `[spacing] scale` and the font. Tokens: xxs 2, xs 3, sm 4, md 6, lg 8,
  xl 10, xxl 12, xxxl 14, huge 18; controlHeight 28, popupRowHeight 28, rowPaddingX 12, rowGap 8, labelGap 4,
  panelGap 14, panelPadding 18, popupPadding 14, dropdownWidth 240. [V]
- **Radius:** `Style.cornerRadius` is Hyprland `decoration:rounding`: **0 by default**, 6 with the
  `corners-rounded` toggle. Popouts sit `Style.gapsOut` (= `gaps_out / 2` = 5) from the bar. Hyprland defaults:
  `gaps_in 5`, `gaps_out 10`, `border_size 2`, inactive border `rgba(595959aa)`, no shadow, no blur (the glassy
  toggle adds blur and opacity 0.75). [V `default/hypr/looknfeel.lua`, `toggles/corners-rounded.lua`,
  `toggles/window-glassy.lua`]
- **Control states (`[controls]`):** normal fill fg@0.04 with border fg@0.40 (1 px); hover = keyboard cursor fill
  fg@0.08 with border fg@0.25; selected fill fg@0.18 (no border); pressed fg@0.22; selection fg@0.35. Focus mirrors
  hover unless a theme overrides it. [V `Style.qml`, `shell.toml.tpl`]
- **Bar:** 26 px tall (`size-horizontal`), icon slot 27, icon size 12; `[bar] active` = red is meant for
  "Modules calling attention to themselves (recording, voxtype, alerts, updates)". [V `shell.toml.tpl`]

### A3. Surfaces and conventions (the Hyprland look, menu, notifications, OSD)
- **Popups and panels:** `KeyboardPanel`, a layer-shell PanelWindow. It primes exclusive keyboard focus briefly,
  then switches to OnDemand, and grows from 0.97 at the bar edge. Card: background with a 2 px popups border at the
  Hyprland radius, padding 14. Cursor model through `PanelKeyCatcher`: arrows/hjkl move, Enter activates, Esc
  closes, Tab/Shift+Tab switch to the neighbouring bar panel, letters are panel actions, Ctrl+↑/↓ reorders. Hover and
  keyboard cursor look the same. [V `shell/Ui/KeyboardPanel.qml`, `PanelKeyCatcher.qml`]
- **Panel content pattern** (Superwhisper's Panel.qml copies Omarchy's Bluetooth and Tailscale panels): a hero
  (icon at display size, title in subtitle bold, a meta line in caption bold uppercase with 1.2 tracking) with a
  `ToggleSwitch` on the trailing edge as the first cursor stop. Then `PanelSeparator`, SettingRows
  ("Label …… value ›") that drill the whole card into a choice page with a back row, and ActionRows with a glyph,
  label, caption and hint letter. [V Superwhisper `Panel.qml`; Omarchy `PanelHero.qml`, `ToggleSwitch.qml`]
- **ToggleSwitch:** track 22×42, knob 16, square at rounding 0. On = selected fill with a fg knob; off = normal fill
  with a `Qt.darker(fg, 1.25)` knob; 120 ms OutCubic. [V]
- **OSD (`omarchy.osd`, replaces SwayOSD's role):** a panel plugin on an `OverlayWindow` with
  `WlrKeyboardFocus.None` and `mask: Region {}` (click-through), namespace `omarchy-osd`. The card is bottom-centre,
  bottom margin `Style.space(67)`, background α 0.97, 2 px popups border, radius `Style.cornerRadius`, padding 16,
  icon at displayLarge, title bold message, 142 px progress bar in accent. It appears and disappears at once.
  [V `shell/plugins/osd/Osd.qml`]
- **Omarchy menu:** a `menu` plugin defined in JSONC (`default/omarchy/omarchy-menu.jsonc` plus user extensions in
  `~/.config/omarchy/extensions/omarchy-menu.jsonc`). No open/close motion. `[menu]` tokens: scrim bg@0.5,
  selected row fg@0.08 with accent text. Third parties add rows through the extension file; Voxtype adds
  *Setup › Dictation*. [V `shell/plugins/README.md`, Voxtype README]
- **Notifications** (`omarchy.notifications` service, replaces mako's role): card with a [notifications] border
  (the active-border gradient) and an accent countdown. [V `Color.qml`, plugin list]
- **Motion rules** (`agents/skills/motion.md`): every duration through `Style.duration(ms)`; entrances
  ease-out `cubic-bezier(0.23,1,0.32,1)`; no ease-in, no bounce; UI motion under 300 ms; exits about ⅔ of the
  entrance (panels 180/120 ms); things opened by one keystroke many times a day don't animate; never scale from 0;
  a looping animation needs `running: … && !Style.reduceMotion`. [V]
- **Dictation contract:** Omarchy owns *Right Alt (hold)* and *Alt+Space (toggle)*, and calls
  `omarchy-dictation-<backend> start|stop|toggle`. Superwhisper is the default backend; Voxtype and Hex are the
  alternatives. hark already ships `omarchy-dictation-hark`. [V `manual/11-text-extraction-dictation.md`,
  `default/hypr/bindings/dictation.lua`; hark `packaging/omarchy/`]

### A4. QuickShell inside Omarchy (plugin contract)
- **Manifest:** `schemaVersion: 1`, namespaced `id` (the `omarchy.` prefix is reserved), `kinds` from
  `bar-widget | bar | panel | overlay | menu | service`, and `entryPoints` per kind. Bar widgets also declare
  `barWidget` (`displayName`, `category`, `allowMultiple`, `defaultSection` = left/centerBegin/centerEnd/tray/right,
  `defaults`, `schema`). `keepLoaded: true` keeps a panel instantiated so its IPC target exists. One plugin may
  declare several kinds; the media plugin is service + bar-widget. [V `docs/omarchy-shell.md`]
- **Install and trust:** plugins live in `~/.config/omarchy/plugins/<id>/` and are managed with `omarchy plugin
  add|enable|validate`. They run **unsandboxed** in the shell; third-party plugins get capability-scoped facades
  (own service and lifecycle). Service-backed third-party widgets are fully integrated only under the built-in bar.
  [V]
- **IPC:** `omarchy-shell shell summon|hide|toggle <id> <payloadJson>`, `call <id> <method> <arg>`; per-plugin
  `ShellIpc`/`IpcHandler` targets, e.g. `omarchy-shell superwhisper page language`. [V; Superwhisper `Panel.qml`]
- **Theming in plugins:** `import qs.Commons as Commons` and use `Commons.Color.*` (Qt 6.12 added a `Color` type that
  shadows the bare name); use `Style`, `Border` and `BorderSurface` for theme gradients and per-side borders. [V]

### A5. Superwhisper on Omarchy (pill, edge glow, panel)
- **Process:** `superwhisper-indicator` (Rust, gtk4-rs, dlopened gtk4-layer-shell). Source modules `pill.rs`,
  `edge_glow.rs`, `glow.rs`, `theme.rs`, `layer.rs`, `pond.rs`. Reads `$XDG_STATE_HOME/omarchy/current/theme/
  colors.toml` (accent, background, foreground) and also `omarchy/shell.json` bar layout. D-Bus
  `com.superwhisper.Indicator` exposes `State` (s) and `Waveform` (ad). Layer namespaces `superwhisper-indicator`
  and `superwhisper-edge-glow`, with Hyprland `layer_rule no_anim` for both. It listens to
  `notify::gtk-enable-animations` (reduced motion). [V `strings` of the 1.3.1 binary]
- **Settings:** `showRecordingIndicator` (on/off) and `recordingIndicatorStyle` = `pill | edgeGlow` ("Show no visual
  recording indicator.", "Choose how dictation is styled."). [V strings]
- **Release notes:** 1.1.0, "The recording pill and edge glow no longer show a blurred box behind them with
  Omarchy's glassy windows"; 1.2.0, "The recording glow shows on an open launcher's card". [V GitHub releases API]
- **Panel plugin** `superwhisper.panel`, a bar-widget placed before `omarchy.clock`: hero with an on/off switch that
  starts/stops the user service, rows for processing/language/tone/microphone, last dictation (C copy), View
  history, Settings. It follows tray SNI frames for live state instead of polling. `PROTOCOL.md`: an NDJSON control
  socket with `status`, `models_get`, `general_get`, `audio_get`, `history_list` (offset/limit/before/query,
  newest first, failures as rows with `failed` and `error`), `history_stats` and `history_retry`. Pinned by a
  contract test against a fake daemon. [V `/tmp/sw-linux/.../omarchy-plugin/superwhisper-panel/`]

## B. QuickShell (architecture, windows, IPC) and community shells

- **Model:** a QML config rooted in `ShellRoot`, hot reload, singletons for services. Windows:
  `PanelWindow` (layer shell on Wayland; anchors, margins, `exclusionMode`, `aboveWindows`), `PopupWindow`
  (xdg-popup attached to a parent window), `FloatingWindow`. `WlrLayershell` is attached to PanelWindow: `layer`
  (Background/Bottom/Top/Overlay), `keyboardFocus` (None/Exclusive/OnDemand), `namespace` (used by compositor layer
  rules). Click-through comes from `mask: Region {}`. [V quickshell.org/docs/v0.2.1 PanelWindow, PopupWindow,
  WlrLayershell; Omarchy `OverlayWindow.qml`]
- **IPC:** `IpcHandler { target }` with typed functions (string/int/bool/real/color, at most 10 args), called with
  `qs ipc call <target> <fn>` (Omarchy wraps this as `omarchy-shell`). Sockets: `Quickshell.Io.Socket` (Unix
  socket client, `write`/`flush`, `connected`), plus `Process`, `StdioCollector`, `FileView`
  (watchChanges). Services: `Quickshell.Services.Pipewire`, `SystemTray`, `Quickshell.Hyprland` (monitors, raw
  events). [V quickshell.org IpcHandler, Socket; Omarchy `Style.qml`, `Microphone.qml`]
- **caelestia-dots/shell:** `shell.qml` composes `Background`, `Drawers`, `AreaPicker`, `Lock`, `Shortcuts`,
  `BatteryMonitor` and `IdleMonitors`; services are singletons; a C++ plugin covers heavy work; panels live in
  "drawers" that slide from screen edges. *Pattern taken:* services own state; views bind. [V github.com/
  caelestia-dots/shell `shell.qml`; deepwiki overview]
- **end-4/dots-hyprland (illogical-impulse):** a Material-style Quickshell desktop with wallpaper-derived colours,
  overview and settings panels. *Pattern rejected for hark:* its own palette generation. On Omarchy the theme is the
  source. [V github.com/end-4/dots-hyprland; summary sources]
- **noctalia-shell:** `SettingsPanel` can be "centered", "attached" (to the bar) or a "window"; tabs per area
  (General, Display, Color scheme, Plugins, …); a plugin settings popup is a modal 600-wide card at max 80 % of the
  screen height. v5 plugins are Luau with `plugin.toml`. *Pattern taken:* a centred settings card with a left
  section list, and a quick panel attached to the bar. [V noctalia `SettingsPanel.qml`, `SettingsContent.qml`,
  `NPluginSettingsPopup.qml`, docs.noctalia.dev]
- **DankMaterialShell:** everything has an IPC target (`dms ipc call <target> open|close|toggle`); widget and
  daemon plugins (`plugin.json`); settings got lazy loading, search routing and "IPC navigation" to a section.
  *Pattern taken:* `omarchy-shell hark.settings section Context` and per-section deep links. [V DankLinux-Docs
  keybinds-ipc; DMS commit 69f3dee]
- **Voxtype Omarchy panel** (`io.voxtype.settings`, `panel` kind with `keepLoaded`): search box focused on open, j/k
  cursor, Enter/Space acts, Escape in a field returns to the row cursor; switches write immediately, fields on
  commit, sliders 300 ms after release; an override dot plus a reset button per key; facts line ("what is actually
  running"); `section <name>` IPC answers `unknown section` rather than showing an empty pane. [V
  https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/omarchy-plugin/README.md]

## C. Voice-tool status/settings UX, accessibility, keyboard, motion

- **Superwhisper:** a mini recording window with waveform, mode/context indicator and stop/cancel; history search,
  playback and reprocess; failed recordings kept for retry; correction learning with "Vocabulary learned"; local
  vocabulary suggestions "ask first". [V `docs/research/competitive-voxtype-superwhisper.md` §1, §4; release notes]
- **Wispr Flow:** the Flow Bar is a small floating bubble with resting / recording / processing states, hidden at
  rest, movable to another screen edge. The waveform is white while listening and **turns blue when it detects
  voice** ("white bars that never turn blue mean the mic is picking up silence"). Ping sounds on start and stop.
  *Taken:* a separate "no input" state and colour-independent level bars. [V docs.wisprflow.ai "Move and Dock the
  Flow Bar"; vkorost/wispr-flow-field-guide ch. 2; abduzeedo.com/wispr-flow-voice-dictation-app]
- **Voxtype:** Waybar state module, tray, OSD waveform, sound feedback and media ducking; the Omarchy panel above.
  [V competitive doc]
- **macOS Control Center:** an orange dot whenever any app uses the microphone, and Control Center names which
  app. The privacy signal is system-owned and cannot be hidden. *Taken:* hark's bar glyph turns `bar.active` red
  exactly while audio is captured for a dictation, and the panel says which window will receive the text.
  [V support.apple.com/guide/mac-help/mchl50f94f8f; 9to5mac.com 2022-01-19]
- **Accessibility:**
  - WCAG 2.2 1.4.3 (text 4.5:1), 1.4.11 (non-text/UI 3:1), 1.4.1 (not colour alone).
  - WCAG 2.2 2.3.1 (≤ 3 flashes/s), 2.3.3 (motion from interactions can be disabled), 2.2.2 (pause/stop/hide
    for motion over 5 s that is not essential).
  - Qt Quick `Accessible` (role/name/description, `onPressAction`); on Linux, Qt exposes AT-SPI only when
    `org.a11y.Status.IsEnabled` and `ScreenReaderEnabled` are set (or with `QT_LINUX_ACCESSIBILITY_ALWAYS_ON`).
  [V w3.org/WAI/WCAG22/Understanding/{non-text-contrast, animation-from-interactions, pause-stop-hide};
  doc.qt.io/qt-6/accessible-qtquick.html; doc.qt.io/qt-6/qaccessible.html]
  - *Consequence:* KeyboardFocus.None overlays are invisible to a screen reader, so state changes need a second
    channel (bar-widget accessible status, sound, notification). [I]
- **Keyboard-first:** Omarchy panels are cursor-driven with single-letter actions (above). Voxtype and DMS add
  search and section deep links. Every surface in the design has a full keyboard path, and the indicator needs none
  because it is passive. [V]
- **Reduced motion:** Omarchy's switch is Hyprland `animations:enabled = false` (`omarchy toggle animations`) →
  `Style.reduceMotion`. Superwhisper listens to GTK `gtk-enable-animations`. hark follows Omarchy's switch and
  offers its own override. [V]

## D. hark facts the design depends on

- Indicator states today: `recording` (level 0..1 at ≤ 20 Hz from RMS dBFS, −60 dB floor), `transcribing`,
  `cleaning`, `done`/`error`/`cancelled` with the labels Pasted, Copied, "Focus moved: copied", Paste failed,
  Transcription failed, Nothing to paste, No speech, Too short, Cancelled. Linger: done 0.8 s, cancelled 1.0 s,
  error 3.0 s. The GTK backend uses a bottom/top margin of 72 and fixed colours (#ff453a etc.) instead of theme
  colours. [V `client/src/hark_client/indicator/__init__.py`, `gtk.py`]
- ctl JSON v1: `status` returns `state idle|recording`, `finishing`, `last {status, profile, paste_verdict,
  keyup_to_paste_ms}` and `learning {enabled, watching}`, and never any text. Commands: start, stop, toggle, cancel,
  status, ping, paste-last. One request per connection, 512 B max. [V `docs/protocol/ctl-v1.md`, `ctl.py`]
- Config keys and defaults: all of `config.py` (server, context, gate, cleanup, audio, hotkey, paste, learn,
  feedback, history, retry, indicator, profiles). Strict TOML: unknown keys are errors. [V `config.py`]
- Context sources: window title, selection, focused text / terminal text (tmux, AT-SPI), agent sessions, repo and
  recent repo, vocabulary, learned. Secure fields, password managers and credential prompts are never read.
  [V `context/types.py`, `context/screen.py`]

## Sources
- Omarchy (quattro): https://github.com/omacom/omarchy/tree/quattro — `docs/omarchy-shell.md`,
  `manual/32-shell-plugins.md`, `manual/11-text-extraction-dictation.md`, `shell/plugins/README.md`,
  `shell/Commons/{Color,Style}.qml`, `shell/Ui/{KeyboardPanel,PanelKeyCatcher,OverlayWindow,ToggleSwitch,PanelHero,
  PanelSlider,Button}.qml`, `shell/plugins/osd/Osd.qml`, `shell/plugins/bar/widgets/Microphone.qml`,
  `agents/skills/motion.md`, `default/themed/{shell.toml,hyprland.lua}.tpl`, `default/hypr/{looknfeel.lua,
  bindings/dictation.lua}`, `default/hypr/toggles/{corners-rounded,window-glassy}.lua`, `config/omarchy/shell.json`,
  `themes/*/colors.toml` (18 read).
- Omarchy manual: https://omarchy.org/manual/text-extraction-dictation/
- Superwhisper: https://superwhisper.com/blog/omarchy ; https://superwhisper.com/linux ;
  https://github.com/superultrainc/superwhisper-linux-releases (release notes); static read of
  `superwhisper-omarchy-x86_64-1.3.1` (`superwhisper-indicator` strings, `assets/omarchy-plugin/superwhisper-panel/
  {README,PROTOCOL}.md, Panel.qml, tests/qml/shell.qml`), details in `docs/research/superwhisper-linux-internals.md`.
- Voxtype Omarchy panel: https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/omarchy-plugin/README.md
- QuickShell: https://quickshell.org/docs/v0.2.1/types/Quickshell/PanelWindow/ ; …/PopupWindow/ ;
  …/Quickshell.Io/IpcHandler/ ; …/Quickshell.Io/Socket/ ; …/Quickshell.Wayland/WlrLayershell/
- caelestia: https://github.com/caelestia-dots/shell/blob/main/shell.qml ; https://deepwiki.com/caelestia-dots/shell
- end-4: https://github.com/end-4/dots-hyprland
- noctalia: https://github.com/noctalia-dev/noctalia-shell/blob/e56a9db5/Modules/Panels/Settings/SettingsPanel.qml ;
  https://docs.noctalia.dev/noctalia/plugins/
- DankMaterialShell: https://github.com/AvengeMedia/DankLinux-Docs/tree/master/docs/dankmaterialshell/keybinds-ipc.mdx ;
  https://github.com/AvengeMedia/DankMaterialShell/commit/69f3dee25a4ecfe67ee8c8c8d49be60a736921f8
- Wispr Flow: https://docs.wisprflow.ai/articles/1790396454-move-and-dock-the-flow-bar-on-desktop ;
  https://github.com/vkorost/wispr-flow-field-guide/blob/main/book/chapters/02-the-dictation-model.md ;
  https://abduzeedo.com/wispr-flow-voice-dictation-app
- macOS: https://support.apple.com/en-gb/guide/mac-help/mchl50f94f8f/mac ;
  https://9to5mac.com/2022/01/19/how-to-use-mac-privacy-features-macos-monterey/
- Accessibility: https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast ;
  https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions ;
  https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide ; https://doc.qt.io/qt-6/accessible-qtquick.html ;
  https://doc.qt.io/qt-6/qaccessible.html
- OpenPencil (tooling): https://openpencil.dev/llms.txt ; https://openpencil.dev/programmable/mcp-server ;
  https://openpencil.dev/reference/design-authoring.md ; https://openpencil.dev/reference/cli.md
- hark: `README.md`, `client/README.md`, `client/src/hark_client/{config.py,indicator/,ctl.py,context/types.py}`,
  `docs/protocol/ctl-v1.md`, `docs/decisions/ADR-002-superwhisper-ideas.md`,
  `docs/research/{competitive-voxtype-superwhisper,superwhisper-linux-internals}.md`.
