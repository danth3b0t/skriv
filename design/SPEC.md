# hark status & settings UI for Omarchy: design spec

Status: design, ready for implementation review (2026-11-12). Research: [RESEARCH.md](RESEARCH.md).
Design file: [hark-ui.fig](hark-ui.fig) (OpenPencil 0.16, 10 pages). Exports: [exports/](exports/) (57 PNGs at 2×).
Tokens: [tokens/omarchy-hark-tokens.json](tokens/omarchy-hark-tokens.json) and [tokens/contrast.md](tokens/contrast.md).
Design source: [src/](src/). `node design/src/build.mjs` rebuilds the `.fig` in a running OpenPencil (MCP, eval
enabled) and re-exports every PNG headlessly with `@open-pencil/core`.

Nothing here changes product code. Items marked **(proposed)** need a client, config or protocol change, listed in
§7 and §10.

---

## 1. Surfaces

| # | Surface | Omarchy mechanism | Shown when | Takes focus? |
|---|---|---|---|---|
| S1 | **Indicator pill** | `OverlayWindow` (layer Overlay, namespace `hark-indicator`, `mask: Region {}`) | a dictation is active, then for the linger time | never |
| S2 | **Edge glow** | same overlay surface, drawn around the target window | while recording / processing (style setting) | never |
| S3 | **Bar widget** | `bar-widget` entry, `BarIconButton` in section `right` | always (dimmed when the daemon is off) | no |
| S4 | **Quick panel** | `KeyboardPanel` hung from S3 | click on S3, or `omarchy-shell hark.shell toggle` | yes (OnDemand) |
| S5 | **Settings + History** | `panel` entry: a centred card on a scrim, `keepLoaded: true` | right-click S3, Omarchy menu *Setup › Dictation › hark*, IPC | yes |
| S6 | **Onboarding** | S5 with `{"onboarding": true}` | once, from `omarchy-install-dictation-hark` | yes |

Plugin: **`hark.shell`**, installed as a symlink in `~/.config/omarchy/plugins/hark.shell/` by
`omarchy-install-dictation-hark`, then `omarchy plugin enable hark.shell` (placed before `omarchy.audio` in
`right`). One manifest, three kinds:

```json
{ "schemaVersion": 1, "id": "hark.shell", "name": "hark", "version": "0.1.0", "author": "hark",
  "description": "hark dictation: recording indicator, edge glow, bar status, settings and history.",
  "kinds": ["service", "bar-widget", "panel"], "keepLoaded": true,
  "entryPoints": { "service": "Service.qml", "barWidget": "BarWidget.qml", "panel": "Settings.qml" },
  "barWidget": { "displayName": "hark", "category": "Status", "allowMultiple": false, "defaultSection": "right",
                 "defaults": { "showLevel": false }, "schema": [ { "key": "showLevel", "type": "boolean",
                 "label": "Level meter in the bar while recording" } ] } }
```

Fallback outside Omarchy: the existing GTK layer-shell indicator (`client/.../indicator/gtk.py`) stays as it is. It
should adopt the same labels (Model.js table, §4.2) and read `colors.toml` when present. While a QuickShell
subscriber has declared `"indicator": true` (§7.3), the daemon does not draw its own indicator, so the two never
show at once.

---

## 2. Tokens: Omarchy theme variables → hark tokens

Rule: **hark defines no colours.** Each token is either an Omarchy role or a formula over Omarchy roles, computed
at runtime by `Theme.qml`. `Theme.qml` reads `~/.local/state/omarchy/current/theme/colors.toml` through `FileView`
for the roles `Commons.Color` does not expose (green, yellow, dark_background, lighter_background, selection),
re-runs on the shell's `applyTheme`, and falls back as listed. The OpenPencil file binds every frame to the variable
collection **“hark · Omarchy theme”**, with one mode per theme: tokyo-night, catppuccin-latte, catppuccin, gruvbox,
rose-pine, flexoki-light, matte-black, nord.

| hark token | Omarchy source / formula | QML | Fallback |
|---|---|---|---|
| `bg` | colors.toml `background` = `[popups] background` | `Commons.Color.popups.background` | `Commons.Color.background` |
| `fg` | `foreground` = `[popups] text` | `Commons.Color.popups.text` | — |
| `accent` | `accent`; `[popups] border` = `hyprland.active-border` | `Commons.Color.accent`; borders via `Border.surfaceSpec("popups","border",…)` | `color4` |
| `urgent` | `red` = `Commons.Color.urgent` = `[bar] active` | `Commons.Color.urgent` | `color1` |
| `green`, `yellow` | colors.toml `green`, `yellow` | `Theme.green/yellow` | accent / fg |
| `dark-bg`, `lighter-bg`, `selection` | colors.toml | `Theme.*` | bg / bg / fg@0.18 |
| `fill-normal/hover/selected/pressed` | fg @ `[controls] *-fill-alpha` (0.04 / 0.08 / 0.18 / 0.22) | `Style.normalFill`… | — |
| `border-hover` | fg @ `hover-cursor-border-alpha` 0.25 | `Style.hoverBorderColor` | — |
| `border-normal` | fg @ `normal-border-alpha` 0.40, **decorative only** | `Style.normalBorderColor` | — |
| `text-2` | `mix(fg, bg, a)`, smallest `a` such that contrast ≥ 4.5:1 on bg | `Theme.solve(fg, bg, 4.5)` | fg |
| `border-control` | `mix(fg, border-normal)` solved to ≥ 3:1 on bg | `Theme.solve(…, 3.0)` | fg |
| `accent-ink` / `accent-text` | accent blended toward fg until ≥ 3:1 / ≥ 4.5:1 | `Theme.ensure(accent, 3 \| 4.5)` | fg |
| `live-ink`, `ok-ink`, `warn-ink` | red / green / yellow blended toward fg until ≥ 3:1 | `Theme.ensure(c, 3)` | fg |
| `accent-fill` | accent @ 0.18 over bg (`Style.selectedAccentFill`) | `Style.selectedAccentFill` | — |
| `live-fill`, `ok-fill`, `warn-fill` | red / green / yellow @ 0.16 over bg | `Util.alpha(c, .16)` | — |
| `on-accent` | `background` | `Commons.Color.background` | — |

The formulas are implemented in `design/src/tokens.py`, which produced the numbers below (18 themes; full table in
`tokens/contrast.md`):

- `fg/bg` ≥ 6.66 everywhere. `text-2/bg` lands at 4.50–4.64 (pass). Omarchy's `Qt.darker(fg,1.55)` gives
  **3.60 (tokyo-night), 3.56 (gruvbox), 3.18 (everforest), 3.82 (miasma)**, so it is not used for text.
- yellow/bg: **2.31 (catppuccin-latte), 2.05 (rose-pine), 2.31 (flexoki-light), 2.90 (matte-black)**. `warn-ink`
  brings all of them to ≥ 3.0.
- `border-normal/bg` is 1.87–2.99 on 16 themes; `border-control` is ≥ 3.0 on all 18.
- accent/bg: 3.14 on rose-pine, so accent is fine for strokes but never for small text; `accent-text` is used for
  that.

Non-colour tokens come straight from `Style`, never hard-coded: `Style.font.{caption 10, bodySmall 11, body 12,
subtitle 13, title 14, heading 16, display 24}`, `Style.spacing.*` / `Style.space(px)`, `Style.cornerRadius`
(Hyprland rounding: 0, or 6 with corners-rounded), `Style.gapsOut`, `Style.bar.*`, `Style.duration()`,
`Style.easeOut`, `Style.reduceMotion`. Font family: `Style.font.family` (`monospace`). The mocks use JetBrains Mono
and Iconify `mdi:` icons, which are the Nerd Font `nf-md` glyphs Omarchy draws.

---

## 3. Component inventory

| Component | Base | Anatomy / sizes (base-size 12) | States | Accessible |
|---|---|---|---|---|
| **Pill** | `BorderSurface` | row padding 12×8, gap 10, height 36; glyph slot 16; label body bold; detail bodySmall `text-2`; card bg α 0.97, 2 px popups border, radius `cornerRadius` | §4.1 table (12 states) | role `StatusBar`; name = label + detail; `Accessible.ignored` on decoration |
| **LevelMeter** | `Row` of `Rectangle` | 12 bars × 3 px, gap 2, max 16 px, 50 ms per sample, newest right; < 8 % drawn as `border-hover` floor | speaking / quiet / silent | ignored (the label carries the state) |
| **Glow** | `Rectangle` border + `MultiEffect` blur | edge 2 px (3 px static), halo width 8 + 18·e, blur 10 + 18·e, opacity 0.30 + 0.50·e | recording, hands-free (floor e = 0.35), processing sweep, ok/warn/live flash, static | ignored |
| **HarkGlyph** | `BarIconButton` iconComponent | three-sample waveform (bars 7/14/9.5 of 16, width 3.5) at `Style.bar.iconSize`; badge 8 px at top-right | idle, recording (`bar.active`), hands-free (lock), processing (accent dot), watching (eye), error (red dot), unreachable (warn dot), off (dimmed) | role `Button`, name “hark, <state>”, description = last result; live region (§9) |
| **QuickPanel** | `KeyboardPanel` + `PanelKeyCatcher` | content width `Style.space(360)`, padding 14, column gap 12 | idle, recording, choice page, daemon off | panel `Pane`; rows `Button` |
| **Hero** | `PanelHero` layout | icon 24, title subtitle bold, meta caption bold upper tracking 1.2 `text-2`, live-state box, `ToggleSwitch` | on / starting / off | switch `CheckBox` “Run hark” |
| **SettingRow** | `CursorSurface` | padding 12×9; label body; description bodySmall `text-2` line-height 15; config key caption `text-2`; control right; modified dot 6 px `accent-ink` | normal, cursor (fill-hover + 1 px border-hover, label bold), modified, disabled (0.55), warn line | name = label, description = desc + key; control carries the role |
| **ToggleSwitch** | `qs.Ui ToggleSwitch` | 42×22 track, 16 knob; off border `border-control` (a11y override); cursor ring pad 6 | off, on, busy, cursor | `CheckBox`, checked |
| **Segmented** | `ButtonGroup` | items bodySmall, padding 10×5, 1 px `border-control`; selected `fill-selected` + bold | — | `RadioButton` group |
| **Dropdown / TextField / NumberField** | `qs.Ui` | h 28 (`controlHeight`), 1 px `border-control`; focus 2 px `accent-ink` | normal, hover, focus, invalid (`live-ink` border + message) | `ComboBox` / `EditableText` |
| **Slider** | `PanelSlider` | track 4 `fill-selected`, fill fg, knob 14 with 2 px bg ring | — | `Slider` |
| **Button** | `qs.Ui Button` | h 28, padding 10, icon 13; primary = accent fill + `on-accent` (one per view); danger = `live-ink` text + border; ghost | normal, hover, pressed (0.97 scale, 160 ms), focus | `Button`; shortcut in `Accessible.description` |
| **Chip** | `BorderSurface` | caption bold upper, padding 6×2, 1 px border | normal, accent (pin), ok, warn, live | `StaticText` |
| **KeyHint** | `BorderSurface` | caption bold, padding 5×1, `fill-normal` + `border-control` | — | ignored (shortcuts are in descriptions) |
| **ProviderRow** | `CursorSurface` | drag handle, glyph, name bold + primary/fallback chip, sub-line, capability chips, status dot + text | ready, cold, unreachable | `ListItem` |
| **SourceRow** | `CursorSurface` | glyph, label, what-it-reads, config key, toggle with “sends: terms only” | on / off | `CheckBox` |
| **LearnedTable** | `ListView` | heard (strikethrough `text-2`), written (bold), seen, where, status chip, action | in use, waiting (n of 2), approve? | rows `ListItem`, actions `Button` |
| **CheckRow** (doctor) | `Row` | 16 px glyph, label 150, detail, optional `fix:` command chip + C copy, status word | ok, warn (`warn-fill`), fail (`live-fill`) | `ListItem`, name = label + status |
| **TimingBar** | `Row` | segments proportional to ms, labels caption | — | text equivalent below it |
| **HistoryRow / HistoryDetail / Playback** | `CursorSurface` | §4.6 | pasted, copied, failed, cancelled, selected | `ListItem`; playback `Slider` + `Button` |
| **Stepper** | `Row` | 6 segments, 3 px bars (`fg` done / accent current / `fill-selected` next), caption labels | — | `ProgressBar` “step n of 6” |

Interactive state precedence is Omarchy's: focus > pressed > hover/cursor > selected > normal (`Style.control*`
helpers).

---

## 4. Screens

### 4.1 Indicator pill (S1) — `exports/indicator-states-*.png`, `indicator-anatomy-tokyo-night.png`

Placement: an `OverlayWindow` on the output that holds the **target window** (focused output if unknown),
bottom-centre, margin `Style.space(24)`. That keeps it clear of the Omarchy OSD card, which sits at 67. With
`indicator.position = "top"`: top-centre, `Style.bar.sizeHorizontal + Style.gapsOut + Style.space(8)` from the top
edge. Hyprland `layer_rule` no_anim for namespace `hark-indicator`, so QML owns the motion. The namespace is not in
Omarchy's glassy blur list, so there is no blurred box behind it.

| state | source (ctl event, §7) | glyph (colour) | label | detail | border | linger | sound |
|---|---|---|---|---|---|---|---|
| hidden | `phase: idle` | — | — | — | — | — | — |
| recording · hold | `phase: recording, mode: hold` | dot `live-ink` in a `live-fill` halo (scale 1–1.35 with e) | Listening | LevelMeter, m:ss after 1 s, profile chip | accent | — | start |
| recording · hands-free | `mode: handsfree` (ctl `toggle`) | `mdi:lock` `live-ink` | Hands-free | LevelMeter, m:ss, KeyHint “Alt+Space stop”, chip | accent | — | start |
| recording · no input **(proposed)** | `mic: silent` (> 1.5 s under −55 dBFS) | `mdi:microphone-off` `warn-ink` | Listening | “No input from <device>” | accent | while true | — |
| transcribing | `phase: transcribing` | `mdi:loading` `accent-ink` + 2 px sweep at the bottom | Transcribing | “<n> s audio”, chip | accent | — | — |
| cleaning | `phase: cleaning` | `mdi:auto-fix` `accent-ink` + sweep | Cleaning up | “<profile> · ≤ <deadline>” | accent | — | — |
| pasted | result `pasted` | `mdi:check-bold` `ok-ink` | Pasted | “<app> · <ms> ms” | accent | 0.8 s | done |
| copied only | `copied`, `focus_changed` | `mdi:clipboard-text-outline` `warn-ink` | Copied · focus moved (or “Copied”) | KeyHint “Super+V to paste” | accent | **2.5 s** | copied |
| error | `error`, `paste_failed` | `mdi:alert-circle-outline` `live-ink` | Transcription failed / Paste failed | “<reason> · hark retry” | **`live-ink`** (polkit `border-error` precedent) | **4 s** | error |
| cancelled | `cancelled`, `no_speech`, `too_short`, `empty` | `mdi:close` `text-2` | Cancelled / No speech / Too short / Nothing to paste | “nothing pasted” | accent | 1 s | cancel |
| profile override | `profile_source: override` | as recording | Listening | chip in `accent-fill` + `accent-ink` + `mdi:pin` | accent | — | — |
| warming **(proposed)** | `server.state: cold` at key-down | `mdi:timer-sand` `text-2` | Loading model | “recording continues” | accent | until ready | — |

Rules: the newest dictation always takes over (same as `Indicator._may_update`). A recording interrupts a linger.
Labels come from one table in `Model.js`, shared by the pill, the bar tooltip and the announcements. Linger times
change from today's code: copied 0.8 → 2.5 s and error 3 → 4 s, so there is time to read the action.

### 4.2 Edge glow (S2) — `exports/glow-*.png`

- **What it traces:** the target window captured at key-down (`target.window`, a Hyprland address). Geometry comes
  from Quickshell's Hyprland module (toplevel by address; `at`/`size` from `hyprctl -j clients` as fallback),
  refreshed on Hyprland raw events `movewindowv2`, `resizewindowv2`/config reload, `fullscreen`, `closewindowv2` and
  `changefloatingmode`, then converted to output-local logical pixels. It is drawn **outside** the window's 2 px
  border, so the halo spills into the 5 px gap.
- **Fallback to the screen edge** (inset 3 px, same envelope): fullscreen target, unknown address, the window on a
  hidden special workspace, or a non-Hyprland compositor.
- **Colour:** `accent` (= the Hyprland active border, i.e. “this window”). Outcome flash `ok-ink`; copied/focus moved
  `warn-ink`, held on the original target; error `live-ink`. The glow never uses `urgent` while recording, because
  the red is reserved for the mic-live dot and the bar glyph.
- **Intensity:** `e` = one-pole envelope of the RMS level (dBFS −60…0 → 0…1), attack 60 ms, release 280 ms,
  sampled at the 20 Hz level events and rendered at the display refresh rate. Halo opacity 0.30 + 0.50·e, blur
  10 + 18·e px, width 8 + 18·e px. Hands-free floor e ≥ 0.35. `indicator.glow_strength` **(proposed)** scales the
  0.50 term: 0.25 subtle, 0.50 normal, 0.75 strong. `indicator.glow_level = false` **(proposed)** fixes e = 0.5.
- **Flash safety:** clamp |Δopacity| ≤ 0.25 per 333 ms (WCAG 2.3.1); error is a single fade and never blinks.
- **Processing:** halo at e = 0.2 plus a 160 px bright segment travelling the perimeter at 1.2 s per loop
  (`Easing.Linear`, `running: visible && !Style.reduceMotion`).
- **Reduced motion** (`Style.reduceMotion` or `indicator.motion = "reduce"` **(proposed)**): static 3 px `accent`
  edge, no halo, no sweep; colour swaps without a fade; all durations 0.
- **Performance:** one overlay surface per output that has a target. The blur is `MultiEffect` on a single
  `Rectangle`, `layer.enabled` only while visible, updates capped at 30 Hz. With Omarchy's software-rendering
  toggle or `QT_QUICK_BACKEND=software`, use the static style.
- **Style setting:** `indicator.style = "pill" | "pill+glow" | "glow" | "off"` **(proposed; replaces
  `indicator.enabled`)**. The Omarchy default is `pill+glow`. With `glow`, the pill still appears for
  copied/error/cancelled results, because those states need words. With `off`, only sounds, notifications and the
  bar glyph remain.

Timeline (hold, 2.6 s of speech) is in `exports/glow-timing-tokyo-night.png` and §8.

### 4.3 Bar widget and quick panel (S3, S4) — `exports/bar-widget-states-*.png`, `quick-panel-*.png`

- Glyph states as listed in §3 HarkGlyph. Tooltip uses the `[tooltip]` tokens: “hark · <state>”, last result
  without text (“pasted into Ghostty · 412 ms · 2m ago”), and “click: panel · right-click: settings · middle:
  paste last”.
- Mouse: left = panel, right = settings, middle = `paste-last`, wheel = nothing (mic gain belongs to
  `omarchy.audio`).
- **Panel rows** (cursor order): power switch → Profile › → Microphone › → Server (status dot, RTT) › → Cleanup
  › → Last dictation (C copy, P paste again) → History › → Settings (`,`) → Diagnostics (`D`).
- **While recording**, add a live strip (meter, “→ <app>”, profile chip), Stop (Enter) / Cancel (Esc) buttons, and
  the **learning watch row** whenever `learning.watching` (eye glyph, “Watching your edit in <app> · n s left ·
  keeps only word pairs”, S stops it) **(proposed: `learn_stop`)**.
- **Choice pages** drill the whole card with a back row (Omarchy pattern). Profile page: Auto (shows the current
  resolution), terminal, chat, prose, plus a segmented “Pin for: Next dictation | Until changed” **(proposed)**.
- **Daemon off:** hero meta “OFF · DAEMON NOT RUNNING”, a warn strip explaining that Right Alt restarts it, and
  only Settings/Diagnostics. The switch runs `systemctl --user start|stop hark.service` and waits for a snapshot to
  confirm, ignoring stale snapshots (the Superwhisper `powerBusy` logic).

### 4.4 Settings (S5) — `exports/settings-<section>-<theme>.png`

Card `Style.space(960)` wide, at least 640 tall. The shell sizes it to the output; content scrolls in a Flickable
while header, nav and footer stay fixed. The mocks show each section at full content height. Layout: header (glyph,
“hark settings”, status chip, search field `/`, “Edit config” opens `$EDITOR` in a terminal), nav 208 px
(`fill-normal`, active item `fill-selected` with a 2 px accent bar, badges), content (title heading, intro
bodySmall `text-2`, section headers, rows), footer (key hints, save status).

Rows are schema-driven: the daemon's `config_schema` (§7.4) supplies key, type, default, enum, range, description
and `restart`. Writes: switches and segments immediately; fields on Enter or blur; sliders 300 ms after release.
Saved atomically with comments preserved; validation errors show inline under the row in `live-ink` with
`config.ConfigError`'s message. A modified dot marks values that differ from the default; Backspace on the row
resets it (`config_unset`).

| Section | Rows (config key; **P** = proposed) |
|---|---|
| General | push-to-talk / hands-free keys (read-only, Omarchy owns them, with file pointer); Cancel `Super+Esc` with Bind… (writes a Hyprland bind to `~/.config/hypr/bindings.lua` after confirmation, **P**); `hotkey.tap_to_lock` **P**; profile rules table (`profiles.*`: apps globs, terminal, cleanup, cleanup_profile; reorder with Ctrl+↑/↓; prose fallback cannot be removed); “Test focused window” (`profile_test`, **P**) |
| Recognition | servers list with primary/fallback order, capability chips (STREAMING/BUFFERED, BIAS, ANCHOR, WORD CONF) and live status (today `[server]` holds one address; a list is **P**, per ADR-001's provider registry); `server.model_id` (from Capabilities); `server.language`; `server.final_timeout_ms`; TLS (`tls_ca`, `tls_cert`, `tls_key`, `tls_server_name`) under a disclosure; anchor gate `gate.enabled` (+ `max_distance`, `confident_anchor` under advanced); restart banner when the address changes |
| Context | intro: “only a ranked list of up to `context.max_terms` words is sent”; source rows `context.sources.{window_title, repo, agent_session, focused_text, selection}` **P** (today all on, not configurable), each with what it reads and “sends: terms only”; never-read list; excluded apps `context.exclude_apps` **P**; budget `context.deadline_ms`, `context.max_terms`; **learned words**: `learn.enabled` switch, table (heard → written, seen ×, where, status), Approve / Dismiss / Forget, Clear; `learn.require_approval` **P** (suggestions wait for Approve instead of auto-promoting at `promote_after`) |
| Cleanup | `cleanup.enabled`; strength Off/Light/Standard **P** (maps to per-profile edit-kind allowlists on the server); `cleanup.deadline_ms`; **preview** of the last dictation as a diff (deleted strikethrough `text-2`, inserted `accent-text` bold) with chips (fillers removed, self-correction applied, negation kept, identifier protected); “Try another sentence” (`cleanup_preview`, **P**); per-profile cards (`profiles.<p>.cleanup`, `cleanup_profile`) |
| Output | `paste.method` with the detected method; `paste.check_focus`; `paste.smart_spacing`; `paste.restore_clipboard` + `restore_delay_ms`; `paste.verify` with a doctor warning when AT-SPI is missing; terminal rules as read-only chips (one line, Ctrl+Shift+V, never Enter, escapes removed, spoken symbols) |
| Indicator & sounds | `indicator.style` **P** (4 picture tiles); `indicator.position`; `indicator.glow_level` **P**; `indicator.glow_strength` **P**; `indicator.motion` **P**; `feedback.sounds` (+ Preview); `feedback.notify` |
| Privacy & history | “what leaves this computer” card (audio to your servers; ≤ N words; text to clean up; nothing else); history retention Off/7/30 days/Forever (`history.enabled` + `history.retention_days` **P**); `retry.keep` slider; `audio.keep_open` (states the mic-indicator trade-off); `history.save_recordings` with warning; storage rows with sizes and Delete… (confirm dialog, `qs.Ui ConfirmDialog`) |
| History | §4.6 |
| Diagnostics | Run checks (R), Copy report (redacted: no text, titles or paths), one CheckRow per `doctor.py` check with fix commands, timing bar of the last dictation from `last`/history timings |

Search (`/`) filters across every section's labels, descriptions and keys. Results are grouped by section; Enter
jumps to the row. `omarchy-shell hark.shell section <Name>` opens a section and answers `unknown section: <x>`
otherwise (Voxtype pattern).

### 4.5 Learned-words review (inside Context)
Each row shows the heard form (strikethrough), the written form, the count, the last app (never field text) and a
status chip. Pending rows (`approve?`) get Approve (`A`, primary) and Dismiss (`X`); in-use rows get Forget (`F`).
Learning on/off is in the section header. The quick panel shows the watch while it runs (§4.3).

### 4.6 History — `exports/history-*.png`
List 300 px: search (searches raw and final text plus app), filter segments All / Failed / Copied / Terminal, day
groups, rows (status glyph, time, app · profile, latency, two-line preview, or the failure reason). Detail pane:

- **pasted:** final text (selectable), Copy C / Paste again P / Retry R (needs kept audio) / Delete; playback
  (Space; waveform scrubber, speed); pipeline section — heard (raw), canonical with replaced terms as chips, cleanup
  outcome (applied/partial/rejected with dropped edits), paste method + verdict + spacing decision, bias-term counts
  per source (never the context text).
- **failed:** reason card in `live-fill`/`live-ink`, “Retry now” (primary, R) / “Retry and copy” (⇧R), the note
  that retry pastes only if the original window is focused, “what happened” rows, a pointer to add a fallback server.
- **copied (focus moved):** warn card explaining where focus was, Paste now (P) / Copy.

Retention and audio availability are shown in the footer. A row whose audio aged out shows Retry disabled with
“audio removed after 10 dictations”.

### 4.7 Onboarding — `exports/onboarding-*.png`
Six steps in a 720 px card, each skippable; Esc finishes later without losing anything already saved:
1. Welcome (three promises).
2. Server (this computer vs another machine, Test, TLS note).
3. Microphone (device, live meter, test sentence, verdict with fix copy).
4. Keys (Right Alt / Alt+Space / Super+Esc, and a practice field that only advances after a real dictation landed;
   AltGr hint).
5. Privacy (sources, learning opt-in default off, retention).
6. Done (doctor summary, tip).

---

## 5. QuickShell component tree

```
hark.shell/
  manifest.json
  Service.qml            HarkService: Connection (subscribe socket) + Requests (one-shot sockets) + Store
    ├─ Connection        Quickshell.Io.Socket → $XDG_RUNTIME_DIR/hark/ctl.sock; writes {"command":"subscribe",…};
    │                    parses NDJSON; reconnect with 0.5 → 8 s backoff; `connected`, `daemonRunning`
    ├─ Store             snapshot (status) + reducers for events: phase, mode, profile, target, level ring buffer
    │                    (12 samples), last result, learning, server, mic, configChanged
    ├─ Requests          request(cmd, args) → Promise; one Socket per request (the protocol closes after one reply)
    ├─ Theme.qml         tokens of §2 (FileView colors.toml, Commons.Color, Style), solve()/ensure()
    └─ Indicator.qml     per target output: OverlayWindow (layer Overlay, KeyboardFocus.None, mask Region{},
                         namespace "hark-indicator") { Glow { target rect } Pill { state } }
  BarWidget.qml          BarIconButton { HarkGlyph } + KeyboardPanel { PanelKeyCatcher { QuickPanel } }
  QuickPanel.qml         Hero, LiveStrip, SettingRow×4, LastDictation, WatchRow, ActionRow×3, ChoicePage
  Settings.qml           panel entry; OverlayWindow (KeyboardFocus Exclusive while open) { Scrim, Card { Header,
                         Nav, SectionLoader, Footer } }; ShellIpc target "hark.shell":
                         open/close/toggle/section(name)/onboarding()/history(id)
  sections/              General, Recognition, Context, Cleanup, Output, Indicator, Privacy, History, Diagnostics,
                         Onboarding — each a ColumnLayout of schema rows + custom blocks
  components/            Pill, LevelMeter, Glow, HarkGlyph, SettingRow, SourceRow, ProviderRow, LearnedTable,
                         HistoryRow, HistoryDetail, Playback, CheckRow, TimingBar, Chip, KeyHint, Stepper
  Model.js               state → {glyph, colour token, label, detail, linger, sound, announcement}; formatting
  tests/                 fake-daemon.py (serves §7 from fixtures), contract test, qml_logic offscreen checks
```

Data flow: the daemon is the only source of truth. Views bind to `Store`; actions go through `Requests`; nothing
writes `config.toml` except the daemon (`config_set`). If the socket is gone, the bar shows *off* and settings show
a “Start hark” banner. The overlay is only mapped while it has something to draw.

**Open (needs a check against the shell):** `docs/omarchy-shell.md` calls `service` a “headless singleton”. If the
registry refuses windows from a service entry, `Indicator.qml` moves to a second plugin `hark.indicator` (kind
`panel`, `keepLoaded: true`, like `omarchy.osd`) that opens its own subscription.

---

## 6. Behaviour rules
- Nothing hark draws during a dictation takes keyboard focus or pointer input. Opening the quick panel during a
  recording does not change the target (it was frozen at key-down).
- Every state is communicated by words (pill / tooltip / announcement) plus shape (glyph) plus colour; never colour
  alone.
- The glow shows *where*; the pill shows *what*; the bar glyph shows *mic live*. The red recording colour
  (`[bar] active`) appears only while audio is being captured for a dictation, not while the pre-roll buffer is
  merely open.
- Status and events never contain dictated text, window titles or field content (ctl v1 rule). Text-bearing
  commands (`history_*`, `cleanup_preview`) are separate, explicit, and never logged by the shell.
- Destructive actions (delete history, clear learned words, delete audio) confirm with `ConfirmDialog`; Forget on
  a single learned word does not.

---

## 7. IPC contract with the daemon (ctl JSON v1 + proposed extensions)

All additions are **additive within v1** (`docs/protocol/ctl-v1.md` §Compatibility): new commands, new optional
`args`, new reply fields. Old daemons answer `unknown_command`; the UI feature-detects (§7.1) and hides what is
missing. The text protocol is unchanged. Socket 0600 in a 0700 directory as today. JSON request lines may be up to
**16 KiB** (proposed; the 512-byte limit stays for text lines).

### 7.1 Feature detection — `ping` (extended reply)
```json
{"version":1,"ok":true,"command":"ping","result":"pong",
 "daemon":{"version":"0.1.0","pid":4242,"service_unit":"hark.service","config_path":"/home/u/.config/hark/config.toml"},
 "features":["subscribe","status2","history","learned","config","doctor","profile_override","retry","mic_test","server_test","cleanup_preview"]}
```

### 7.2 `status` (new optional fields)
```json
{"version":1,"ok":true,"command":"status","state":"recording","finishing":0,
 "phase":"recording","mode":"hold","started_at":1762950000.12,
 "utterance_id":"0199c3e2…","profile":"terminal","profile_source":"rule",
 "profile_override":null,
 "target":{"app":"Ghostty","app_id":"com.mitchellh.ghostty","window":"0x55d3c1a0","output":"eDP-1"},
 "server":{"address":"desktop.tailnet:50051","state":"ready","rtt_ms":38,"model_id":"parakeet-tdt-0.6b-v3",
           "capabilities":["streaming","bias","anchor","word_confidence"]},
 "mic":{"device":"Built-in Mic","status":"ok","keep_open":true},
 "history":{"enabled":true,"count":214,"failed":2,"retention_days":30},
 "last":{"at":1762949000.1,"utterance_id":"…","status":"pasted","profile":"terminal","paste_verdict":"landed",
         "keyup_to_paste_ms":412.5,"app":"Ghostty","retry_available":true},
 "learning":{"enabled":true,"watching":false,"pending":1}}
```
- `phase`: `idle | recording | transcribing | cleaning | pasting`. `state` keeps its v1 meaning
  (`idle | recording`).
- `mode`: `hold` (started by `start`) or `handsfree` (started by `toggle`, or tap-to-lock).
- `profile_source`: `override | rule | terminal | fallback`.
- `target.window` is the opaque Hyprland address the client already captures; `app`/`app_id` identify the app (not
  content). The window title is never included.
- `server.state`: `ready | cold | unreachable | unknown`.
- `mic.status`: `ok | silent | muted | disconnected`.

### 7.3 `subscribe` — the one long-lived request
Request: `{"version":1,"command":"subscribe","args":{"topics":["state","level","result","learning","server","mic","config"],"indicator":true}}`

The reply is the `status` object (above) as the first line; then the daemon keeps the connection open and writes
one JSON event per line until the client closes:

```json
{"event":"state","seq":41,"utterance_id":"…","phase":"recording","mode":"hold","profile":"terminal","profile_source":"rule","target":{…},"at":1762950000.12}
{"event":"level","seq":42,"utterance_id":"…","rms":0.62,"peak":0.81}
{"event":"state","seq":63,"utterance_id":"…","phase":"transcribing","audio_ms":4100}
{"event":"state","seq":64,"utterance_id":"…","phase":"cleaning","profile":"prose","deadline_ms":1000}
{"event":"result","seq":65,"utterance_id":"…","status":"pasted","paste_verdict":"landed","keyup_to_paste_ms":412.5,"app":"Ghostty","retry_available":true}
{"event":"result","seq":70,"utterance_id":"…","status":"error","error":{"code":"server_timeout","message":"no final text after 5000 ms"},"retry_available":true}
{"event":"learning","seq":71,"watching":true,"app":"Slack","ends_at":1762950031.0}
{"event":"server","seq":80,"address":"desktop.tailnet:50051","state":"unreachable"}
{"event":"mic","seq":81,"status":"silent","device":"Built-in Mic"}
{"event":"config","seq":90,"changed":["paste.method"],"restart_required":false}
```
- `level` only while recording, at most **20 Hz** (today's `LEVEL_INTERVAL_S`). `rms` uses the same 0..1 mapping
  as `level_of` (−60 dBFS floor). Latest wins: a slow subscriber gets dropped samples, never a blocked audio
  thread. Each subscriber has a bounded queue of 64 events; overflow drops `level` first, then closes the
  connection with `{"event":"overflow"}`.
- `seq` is per daemon and monotonic, so the client can tell it missed events and call `status`.
- `indicator: true` → while at least one such subscriber is connected, the daemon's own GTK/NSPanel indicator does
  not draw.
- At most 8 subscribers; more get `error {code:"busy"}`.
- `cancelled`, `no_speech` and `too_short` arrive as `result` events with those statuses (today `too_short` is not
  in history; the event still fires).

### 7.4 Request/reply commands (one line in, one line out)
| command | args | reply (fields) | notes |
|---|---|---|---|
| `start` | `profile?`, `profile_scope?: "next"\|"sticky"` | `result` as v1 | unknown profile → `invalid_args` (never a silent fallback) |
| `set_profile` | `profile: string\|null`, `scope` | `profile_override` | null clears it |
| `retry` | `id: "last"\|uuid`, `deliver: "paste"\|"copy"` | `result: "queued"` + later `result` event | wraps `hark retry`; `unavailable` if no audio |
| `copy_last` | — | `chars` | via wl-copy; never echoes text |
| `learn_stop` | — | `watching:false` | ends the current watch window |
| `history_list` | `limit≤200`, `before?`, `query?`, `filter?: failed\|copied\|terminal` | `items[] {id, at, app, profile, status, keyup_to_paste_ms, preview≤280, failed, error?, audio}`, `more`, `matches?` | **text-bearing** |
| `history_get` | `id` | full record: raw, canonical, final, replacements, cleanup {status, edits, dropped}, gate, spacing, paste, timings, `context_summary {terms_by_source}` | **text-bearing**; never the context text |
| `history_delete` | `id` \| `all:true` \| `before` | `deleted` | confirm in UI |
| `history_play` / `history_stop` | `id`, `from_s?`, `speed?` | `playing` | pw-play in the daemon (Superwhisper pattern) |
| `learned_list` | — | `items[] {heard, written, count, status: in_use\|pending\|suggested, app, last_seen}` | pairs only |
| `learned_approve` / `learned_dismiss` / `learned_forget` / `learned_clear` | `written` | `ok` | `hark learned …` |
| `config_schema` | — | `keys[] {key, type, default, enum?, min?, max?, description, restart, section}` | generated from the `config.py` dataclasses; single source of truth |
| `config_get` | `keys?` | `values {key: {value, source: default\|file}}`, `path` | |
| `config_set` / `config_unset` | `key`, `value` | `ok` + `config` event, or `invalid_value {message}` | validated with `config.validate`; atomic write that keeps comments |
| `doctor` | — | `checks[] {id, label, status: ok\|warn\|fail, detail, fix?}`, `ms` | reuse `doctor.py` checks |
| `devices` | — | `inputs[] {name, index, default}` | PortAudio |
| `mic_test` | `action: start\|stop`, `device?` | `ok`; level events with `utterance_id:null` | onboarding; nothing recorded or sent |
| `server_test` | `address`, `tls_ca?` | `{state, rtt_ms, model_id, capabilities}` | onboarding / Recognition |
| `profile_test` | — | `{app, app_id, profile, matched_rule}` | “Test focused window” |
| `cleanup_preview` | `text` (≤ 2000), `profile` | `{text, edits[], dropped[], status}` | **text-bearing**; nothing saved |

New error codes (additive): `invalid_args`, `invalid_value`, `not_found`, `unavailable`, `busy`. Contract tests
extend `test_json_v1_contract`. `tests/fake-daemon.py` in the plugin serves the same shapes for QML development.

---

## 8. Motion spec
All durations go through `Style.duration(ms)`. Curves: entrances `Easing.Bezier` with `Style.easeOut`
(0.23, 1, 0.32, 1); fades `Easing.OutCubic`; colour changes `Easing.InOutQuad`; the processing sweep is the only
`Easing.Linear`.

| element | in | out | detail | reduced motion |
|---|---|---|---|---|
| pill appear/hide | 120 ms fade + scale 0.97→1 | 120 ms fade | never from 0; MouseArea irrelevant (click-through) | instant |
| pill content change | 0 | — | width animates 140 ms OutCubic; text swaps at once | width jumps |
| level meter | 50 ms per sample | — | no tween | same |
| glow | 180 ms fade | 400 ms fade | envelope attack 60 / release 280 | static 3 px edge |
| processing sweep | 1.2 s loop | — | `running: visible && !Style.reduceMotion` | none |
| outcome flash | 120 ms | 400 ms | colour cross-fade, single, no blink | colour swap |
| quick panel | 180 ms (KeyboardPanel grow from 0.97) | 120 ms | Omarchy default | 0 |
| settings card | none | none | summoned by keystroke and often: appears at once (motion.md) | — |
| toggle knob | 120 ms OutCubic | — | `ToggleSwitch` | 0 |
| button press | 160 ms scale 0.97 | — | `qs.Ui Button` | 0 |

Linger after results: pasted 0.8 s, copied 2.5 s, cancelled 1 s, error 4 s. A new recording interrupts at once.

---

## 9. Accessibility
- **Contrast:** text ≥ 4.5:1 (`fg`, `text-2`, `accent-text`); glyphs, strokes, focus rings and control borders
  ≥ 3:1 (`*-ink`, `border-control`). Verified per theme by `design/src/tokens.py` (CI idea: run it over every theme
  in `themes/` on each Omarchy update).
- **Not colour alone:** every state has a distinct glyph and word; the toggle state is the knob position; the
  learned words' heard/written use strikethrough vs bold; the diff uses strikethrough vs bold.
- **Screen readers:** overlays with `KeyboardFocus.None` cannot be focused, so state changes are also exposed by
  the bar widget item: `Accessible.role: StatusBar`, `Accessible.name` updated to “hark, Listening into Ghostty” /
  “hark, Pasted” / “hark, Copied, focus moved, press Super V to paste”, which Orca reads as a live object; plus the
  sound cue and, for copied/failed, an Omarchy notification. Every control sets `Accessible.role/name/description`
  and `onPressAction` = Enter. Qt exposes AT-SPI only when the a11y bus says a screen reader is on. hark never
  forces `QT_LINUX_ACCESSIBILITY_ALWAYS_ON` globally (cf. Superwhisper, research §10).
- **Keyboard:** every surface is fully operable: quick panel (arrows/hjkl, Enter/→, ←, Esc, Tab, C, P, S, `,`, D),
  settings (Tab/Shift+Tab sections, ↑↓ rows, Enter/Space change, Backspace reset, `/` search, Esc in a field returns
  to the row cursor, Esc closes), history (`/`, C, P, R, ⇧R, Space, Del), learned words (A, X, F), onboarding (Enter,
  Shift+Tab back, S skip, Esc later). Focus ring = Omarchy focus tokens; inputs get 2 px `accent-ink`.
- **Motion:** respects `Style.reduceMotion` (Hyprland animations off) and its own override; nothing loops while
  reduced; flash limit as in §4.2.
- **Text size:** all metrics come from `Style.font`/`Style.space`, so `omarchy display text size` scales every
  surface. Truncation always uses elide with the full text in the tooltip or accessible description.
- **Sound:** cues are distinct for start, done, copied, error and cancel, and can be switched off; never the only
  signal.

---

## 10. Proposed changes outside the UI (for the implementation plan)
1. Daemon: `subscribe` with events and level, extended `status`/`ping`, the §7.4 commands, 16 KiB JSON lines,
   and suppressing the built-in indicator while an `indicator` subscriber is connected.
2. Config: `indicator.style` (replaces `enabled`), `indicator.glow_level`, `indicator.glow_strength`,
   `indicator.motion`; `context.sources.*`, `context.exclude_apps`; `learn.require_approval`; `cleanup.strength`;
   `history.retention_days`; `hotkey.tap_to_lock`; a server list for fallback. Each needs `validate()` rules and
   tests.
3. Mic health (silent/muted/disconnected) as a status and event; an optional mic test mode.
4. Linger times: copied 2.5 s, error 4 s (`LINGER_S`).
5. Packaging: `omarchy-install-dictation-hark` links and enables `hark.shell`, adds a Hyprland `layer_rule`
   no_anim for `hark-indicator`, and adds an Omarchy menu extension row *Setup › Dictation › hark settings*; the
   remove script undoes all of it.

## 11. Risks and open questions
- **Service windows** (§5). **Toplevel geometry** in the Quickshell version Omarchy ships (fallback:
  `hyprctl -j clients`) [U].
- **Blur cost** on CPU-only laptops (the daily driver): measure `MultiEffect` at 30 Hz, and switch to the static
  style automatically above a frame-time budget [U].
- **`keep_open` and Omarchy's mic indicator:** confirm on hardware that the widget stays lit with hark's open
  stream. If so, the settings copy stays, and consider `keep_open = false` with WirePlumber `fast-capture-start`
  as the Omarchy default (ADR-002 #7) [U].
- **Comment-preserving TOML writes** need a library or a careful line patcher; until then `config_set` may refuse
  and the UI falls back to “Edit config”.
- **Glyph/announcement wording** needs a quick check with Orca on Omarchy.
- **Superwhisper co-installed:** Omarchy runs one dictation backend at a time, but both bar widgets could be
  enabled. Since the dictation keys go only to the selected backend, `hark.shell` should check
  `omarchy-default-dictation` and dim itself when hark is not the selected backend.

## 12. Export index (`design/exports/`)
- Cover / system: `cover-tokyo-night`, `tokens-{tokyo-night,catppuccin-latte}`,
  `components-{tokyo-night,catppuccin-latte}`, `theme-matrix` (8 themes), `motion-a11y-{tokyo-night,catppuccin-latte}`
- Indicator: `indicator-states-{tokyo-night,catppuccin-latte}`, `indicator-anatomy-tokyo-night`
- Edge glow: `glow-recording-{tokyo-night,catppuccin-latte}`, `glow-intensity-tokyo-night`,
  `glow-states-tokyo-night`, `glow-focus-moved-tokyo-night`, `glow-reduced-motion-catppuccin-latte`,
  `glow-screen-fallback-tokyo-night`, `glow-timing-tokyo-night`
- Bar & panel: `bar-widget-states-{…}`, `quick-panel-{tokyo-night,catppuccin-latte}`,
  `quick-panel-recording-tokyo-night`, `quick-panel-profile-{…}`, `quick-panel-off-tokyo-night`
- Settings: `settings-{general,recognition,context,cleanup,output,indicator,privacy,diagnostics}-{tokyo-night,catppuccin-latte}`
- History: `history-detail-{…}`, `history-failed-{…}`, `history-search-tokyo-night`
- Onboarding: `onboarding-{1-welcome,2-server,3-microphone,4-keys,5-privacy,6-done}-tokyo-night`,
  `onboarding-{1-welcome,4-keys,5-privacy}-catppuccin-latte`

Known mock simplifications: the desktop mocks use a placeholder Omarchy logo glyph; the bar crop shows only the
right side; the settings card is shown at full content height rather than scrolled.
