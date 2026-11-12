# hark on Omarchy: UI spec

Status: design, ready for implementation review (2026-11-12). Scope from [AUDIT.md](AUDIT.md); background in
[RESEARCH.md](RESEARCH.md). Design file [hark-ui.fig](hark-ui.fig) (OpenPencil, 7 pages), renders in
[exports/](exports/) (21 PNGs at 2×). Source: [src/](src/). `node design/src/build.mjs` rebuilds the `.fig` in a
running OpenPencil (MCP, eval on) and re-exports every PNG; `--only <page>` builds one page to `/tmp/hark-scratch/`.

The UI is for **state and recovery**. Everything set less than about once a month lives in
`~/.config/hark/config.toml`, and explanations live in the README.

## 1. Principles

1. **Shape and motion first; words only when you must act.** At most 3 words in the pill. Panel rows are a label
   and a value. Every failure has exactly one verb (Retry, Paste, Restart, Start, Doctor).
2. **One accent.** Accent marks the target window and the keyboard cursor. Red means mic live or failed. Green and
   yellow appear only on glyphs.
3. **Type and space, not boxes.** No chips, keycaps, inner borders, separators, or uppercase meta lines. Groups are
   separated by 18 px; the only fill is the cursor row.
4. **Quiet until needed.** Idle shows one monochrome glyph. Problems add a red dot to the glyph and one line to the
   panel.
5. **Native.** Every colour, size, radius and duration comes from the running Omarchy theme.

Copy: sentence case, no full stops, no config keys, no mechanism. `·` separates a state from its reason. Keys are
plain text (`C copy`). Words, glyph and colour always appear together; the bar item's accessible name carries the
full sentence.

## 2. Surfaces

| | Surface | Omarchy mechanism | Shown |
|---|---|---|---|
| S1 | **Pill + target outline** | `OverlayWindow`, layer Overlay, namespace `hark-indicator`, `KeyboardFocus.None`, `mask: Region {}`, `layer_rule no_anim` | during and just after a dictation |
| S2 | **Bar glyph** | bar-widget `BarIconButton`, section `right` | always |
| S3 | **Quick panel** | `KeyboardPanel` from S2 (`omarchy-shell hark.shell toggle`) | on click; the **only control surface** |
| S4 | **History** | `panel` entry, a centred card | panel link, right-click on S2 |

Plugin `hark.shell` (kinds `service`, `bar-widget`, `panel`, `keepLoaded: true`), linked and enabled by
`omarchy-install-dictation-hark`, which ends by running `hark doctor`. There are no settings window, onboarding or
diagnostics page; `Config` opens `$EDITOR` and `Doctor` runs `hark doctor` in a terminal. Outside Omarchy, the GTK
indicator stays and adopts the same state table. The macOS menu bar mirrors S3, and the macOS Setup window
(permissions) stays.

## 3. Visual system — `exports/system-*.png`, `theme-matrix.png`

**Colour.** hark defines no colours. `Theme.qml` reads `colors.toml` (`FileView`, re-read on `applyTheme`) and
derives the variables in the OpenPencil collection *hark · Omarchy theme* (8 modes):

| token | source | use |
|---|---|---|
| `bg`, `fg` | `[popups] background / text` | surfaces, primary text |
| `text-2` | `mix(fg, bg)` solved to ≥ 4.5:1 (Omarchy's `Qt.darker(fg,1.55)` fails on tokyo-night 3.6:1) | secondary text, key hints |
| `accent` | `accent` = Hyprland active border | target outline, panel border, open-panel underline |
| `accent-ink` / `accent-text` | accent toward fg until ≥ 3:1 / ≥ 4.5:1 | working dots, profile check / diff insertions |
| `live-ink`, `ok-ink`, `warn-ink` | red / green / yellow toward fg until ≥ 3:1 | mic live + failed / done / attention glyphs |
| `fill-hover`, `fill-normal`, `fill-selected` | fg @ `[controls]` 0.08 / 0.04 / 0.18 | cursor row; search field; toggle |
| `border-hover`, `border-control` | fg @ 0.25; solved to ≥ 3:1 | pill hairline; toggle off |

The formulas are in `src/tokens.py`; per-theme numbers are in `tokens/contrast.md`.

**Type** (`Style.font`, family `monospace`): `body 12` for primary text, `bodySmall 11 text-2` for secondary text,
`caption 10 text-2` for key hints. Bold is used only for the surface title and the selected option.
**Space** (`Style.space`): 4 / 8 / 12 / 18. Rows are 28 px tall; panels have 18 px padding and 18 px gaps.
Radius is `Style.cornerRadius`.
**Motion** (`Style.duration`, ease-out `Style.easeOut`, Omarchy `motion.md`):

| what | how | reduced motion |
|---|---|---|
| voice | meter bars set per 50 ms sample, no tween | same |
| working | 3 dots breathe in turn, 900 ms loop, `running: visible && !Style.reduceMotion` | middle dot lit |
| pill in / out | fade 120 / 120 ms; content swaps instantly | instant |
| panel | `KeyboardPanel` grow 180 / 120 ms | 0 |

There is no glow, sweep, flash, scale or blink.

## 4. Pill + target outline (S1) — `exports/indicator-states-*.png`, `outline-*.png`

The pill is 28 px tall, padding 12, gap 8, `bg` with a 1 px `border-hover` hairline. It sits bottom-centre on the
**target window**, 24 px up (bottom of the output for the screen fallback). The newest dictation always takes over.

| state | trigger | pill | linger |
|---|---|---|---|
| recording | `phase: recording` | red dot · meter (8 × 2 px bars, max 12) | — |
| long | > 60 s | + `1:12` (`text-2`) | — |
| near limit | ≥ 100 s | + countdown to `audio.max_ms`, `warn-ink` | — |
| hands-free | `mode: handsfree` | lock (`live-ink`) · meter | — |
| pinned profile | `profile_source: override` | + pin · `chat` | — |
| no input | `mic: silent` > 1.5 s | mic-off (`warn`) · flat meter · No input | while true |
| server down | no healthy provider at key-down | dot · meter · ⚠ Server down · saved | while recording |
| cold model | `server.state: cold` | dot · meter · Loading model | until ready |
| working | `transcribing` or `cleaning` (one state) | three breathing dots (`accent-ink`) | — |
| pasted | `pasted` | ✓ | 0.5 s |
| fallback | pasted via a fallback provider | ✓ via laptop | 0.8 s |
| cleanup skipped | cleanup timeout / rejected / skipped | ✓ raw | 0.8 s |
| length limit | stopped at `audio.max_ms` | ✓ Stopped at 2:00 | 1.5 s |
| focus moved | `copied`, `focus_changed` | copy glyph (`warn`) · Copied · focus moved | 2.5 s |
| paste failed | `paste_failed` (text stays in clipboard) | copy glyph · Paste failed · copied | 4 s |
| failed | `error` | ⓘ (`live`) · Failed · *2-word reason* | 4 s |
| mic lost | stream error / device gone | mic-off (`live`) · Mic lost | 4 s |
| paused | key press while paused | pause · Paused | 1 s |
| not running | key press, daemon starting | dots · Starting | until up |
| cancelled | `cancelled`, `no_speech`, `too_short`, `empty` | × · Cancelled / No speech | 0.6 s |

**Target outline.** A static 2 px `accent` line drawn 2 px outside the target window's border (inside the 5 px
gap), shown only while recording. It stays on the target if focus moves, so it never follows the active border.
Geometry comes from the Quickshell Hyprland toplevel by address (fallback `hyprctl -j clients`), refreshed on move,
resize, fullscreen and close events. Fullscreen, unknown or hidden targets get a screen-edge outline inset 6 px.
`indicator.style = "pill" | "pill+outline" | "off"` (config only).

## 5. Bar glyph (S2) — `exports/bar-widget-states-*.png`

| state | glyph |
|---|---|
| idle | waveform mark, `fg` |
| recording | mark, `live-ink` (`[bar] active`); hands-free included |
| working | mark, `accent-ink` |
| attention | mark + 4 px `live-ink` dot until the panel is opened (failed, server down, mic lost, config changed) |
| paused | mic-off, `text-2` |
| off | mark at 0.4 opacity |

Tooltip: one line, `hark · ready · desktop 38 ms` or the problem. Left click opens the panel, middle click pastes the
last dictation (`paste-last`), right click opens History. Accessible role `StatusBar`; its name is the full state
sentence, updated as a live region.

## 6. Quick panel (S3) — `exports/quick-panel-*.png`

Card: `Style.space(300)` content, 18 px padding, 18 px gaps, 2 px `accent` border. Cursor (arrows/hjkl) starts on
the problem line if there is one, otherwise on the last dictation.

1. **Header:** mark, **hark**, `server · rtt` in `text-2` (`laptop · fallback` in `warn-ink` when a fallback
   serves; `paused` when paused), and a `ToggleSwitch` = Pause (`set_paused`, releases the mic).
2. **Problem line** (only when present, first failing condition): Server down `↵ Doctor` · Mic lost `↵ Restart` ·
   Config changed `↵ Restart` (config mtime newer than daemon start) · Not running `↵ Start` (the header switch is
   hidden).
3. **Last dictation:** 2-line preview, `app · age` (+ `· raw`, `· stopped at 2:00`), `C copy  P paste`. A failed
   dictation shows `Failed · reason` with `R retry`; a failed paste shows `Paste failed · in clipboard` with `P
   paste`.
4. **Profile** `auto ›`: a choice page (auto showing the resolved profile, terminal, chat, prose); `←` goes back.
5. **Cleanup** on/off · **Sounds** on/off (Enter toggles).
6. **History · Config · Doctor**.

Opening the panel during a recording never changes the target, which is frozen at key-down.

## 7. History (S4) — `exports/history-*.png`

A 900 × 480 card with a search field (`/`; matches raw and final text and app; `failed`
works as a query word).

- **List:** day groups; each row has a glyph, `time · app`, and a 2-line preview (or `Failed · reason`).
- **Detail:** final text (selectable), `time · app · profile · ms`, `C copy  P paste  R retry  Del delete`. Retry
  is shown only while audio is kept. **Heard** shows raw → final as one inline diff: removed words struck through in
  `text-2`, added words in `accent-text`, `H copy heard`. It appears only when the two differ. A cleanup outcome
  line appears only when cleanup was skipped, partial or rejected.
Deleting all history and showing pipeline internals stay in the CLI (`hark history show`). Niche spellings
(`Hyprland` for "hyper land") are a short hand-written `[[words]]` list in the config file, opened from the panel's
Config link; there is no words UI.

## 8. Behaviour and accessibility

- Nothing drawn during a dictation takes focus or input. Overlays are mapped only while they have content.
- Status and events never carry dictated text, window titles or field content. `history_*` is the only
  text-bearing path and is never logged by the shell.
- Contrast: text ≥ 4.5:1 and glyphs/borders ≥ 3:1 on every theme (solved at runtime). The toggle state is the
  knob position; the diff uses strikethrough and colour together.
- Keyboard: panel (arrows/hjkl, Enter, ←, Esc, Tab to the neighbouring panel, C/P/R); history (`/`, C/P/R/H, Del).
- Sizes scale with `omarchy display text size`; durations are 0 when Hyprland animations are off.
- Notifications only for failed and focus-moved outcomes (they outlive the pill).

## 9. Daemon contract (ctl JSON v1, additive)

Needed: `subscribe` (events `state`, `level` ≤ 20 Hz, `result`, `server`, `mic`, `config`; bounded queue, `level`
dropped first), the extended `status` (`phase`, `mode`, `profile_source`, `target{app,window,output}`,
`server{name,state,rtt_ms,fallback}`, `mic{status}`, `paused`, `last{status,app,at,retry_available,route,cleanup}`,
`restart_required`), `retry`, `copy_last`, `set_profile`, `set_paused`, `history_list/get/delete`, and `config_set` limited to `cleanup.enabled` and `feedback.sounds`. Result statuses add
`paste_failed` (text left in clipboard) and `stopped_at_limit`; `mic` adds `lost`. `[[providers]]` with priority and
health already ships.

Not needed (cut with the settings window and onboarding): `config_schema/get/unset`, the comment-preserving TOML
writer, `doctor` JSON, `devices`, `mic_test`, `server_test`, `profile_test`, `cleanup_preview`, `history_play`,
`learned_list/forget/approve/dismiss`, `learn_stop`, `context.sources.*`, `exclude_apps`, `require_approval`, `cleanup.strength`,
`retention_days`, `tap_to_lock`, and the `glow_*` / `motion` keys.

## 10. Open

- `service` plugins may not be allowed to open windows; if so, the overlay moves to a `hark.indicator` panel
  plugin with its own subscription.
- Toplevel geometry in the Quickshell version Omarchy ships (fallback `hyprctl`).
- `keep_open` keeps Omarchy's mic indicator lit; check on hardware.
- Paste verdict `missing`: show nothing until its false-positive rate is known (history glyph only).
- If Superwhisper is also installed: dim `hark.shell` when hark is not `omarchy-default-dictation`.

## 11. Exports

`cover-tokyo-night` · `system-{tokyo-night,catppuccin-latte}` · `theme-matrix` ·
`indicator-states-{…}` · `outline-recording-{…}`, `outline-focus-moved-tokyo-night` ·
`bar-widget-states-{…}` · `quick-panel-{…}`, `quick-panel-{states,attention,profile,off}-tokyo-night` ·
`history-{…}`, `history-failed-tokyo-night`. `{…}` = tokyo-night and catppuccin-latte.
