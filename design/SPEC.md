# hark on Omarchy: UI spec

**Authoritative.** This file is the source of truth for the hark UI. [AUDIT.md](AUDIT.md) is historical (it set the
first scope); background is in [RESEARCH.md](RESEARCH.md). The daemon facts used here are defined in
[docs/protocol/ctl-v1.md](../docs/protocol/ctl-v1.md); labels, priority and linger durations are the UI's.

Design file [hark-ui.fig](hark-ui.fig) (OpenPencil, 6 pages). Renders in [exports/](exports/) are **illustrative**
(18 PNGs at 2×). Source in [src/](src/): `node design/src/build.mjs` rebuilds the `.fig` in a running OpenPencil
(MCP, eval on) and re-exports every PNG; `--only <page>` builds one page to `/tmp/hark-scratch/`.

The UI is for **state and recovery**. Anything changed less than about once a month lives in
`~/.config/hark/config.toml`; explanations live in the README.

## 1. Principles

1. **Shape and motion first; words only when you must act.** The pill has at most 3 words, plus the recovery key
   on failures (§5). Panel rows are a label and a value. Every problem has exactly one verb.
2. **One accent.** Accent marks the keyboard cursor and the panel edge. Red means mic live or failed; green and
   yellow appear only on glyphs.
3. **Type and space, not boxes.** No chips, keycaps, inner borders, separators or uppercase meta lines. The only
   fill is the cursor row.
4. **Quiet until needed.** Idle shows one monochrome glyph. A problem adds a dot to the glyph and one line to the
   panel. Silence is never an error: people pause to think.
5. **Honest.** ✓ means *sent*, never *confirmed*. Confirmation exists only in History.
6. **Native.** Every colour, size, radius and duration comes from the running Omarchy theme.

Copy: sentence case, no full stops, no config keys, no mechanism. `·` separates a state from its reason. Keys are
plain text (`P paste`, `Super Alt D`). Words, glyph and colour always appear together; the bar item's accessible
name carries the full sentence.

## 2. Surfaces and build order

| | Surface | Drawn by | Data |
|---|---|---|---|
| S1 | **Pill** | the daemon's own indicator: GTK4 layer-shell overlay (`hark-indicator`, no keyboard, empty input region), bottom-centre of the output, 24 px up | in-process (it has the input level) |
| S2 | **Bar glyph** | `hark.shell` bar-widget, section `right` | `subscribe` |
| S3 | **Quick panel** | `hark.shell` `KeyboardPanel` from S2: the **only control surface** | `subscribe` + the last history record |
| S4 | **History** (optional) | `hark.shell` `panel` card | `hark history --json` |

Build in this order and stop when it is good enough:

1. pill
2. bar glyph + quick panel
3. last-result Copy / Retry in the panel
4. History
5. (deferred) target marker

There is no settings window, onboarding or diagnostics page. `Config` opens `$EDITOR`, `Doctor` runs `hark
doctor` in a terminal, and `omarchy-install-dictation-hark` ends with `hark doctor`.

**Recovery bind.** The installer adds `o.bind("SUPER + ALT + D", "Dictation panel", "hark-panel")` (free in
Omarchy's defaults). `hark-panel` opens the quick panel with the cursor on the last dictation (`omarchy-shell shell
toggle hark.shell '{"cursor":"last"}'`). Failure pills name this key, because the pill itself is click-through.

**macOS: frozen scope.** The existing indicator, menu bar and Setup window stay and get correctness fixes only.
There is no parity promise with this UI.

## 3. Visual system — `exports/system-*.png`, `theme-matrix.png`

**Colour.** hark defines no colours. `Theme.qml` reads `colors.toml` (re-read on `applyTheme`) and derives:

| token | source | use |
|---|---|---|
| `bg`, `fg` | `[popups] background / text` | surfaces, primary text |
| `text-2` | `mix(fg, bg)` solved to ≥ 4.5:1 (Omarchy's `Qt.darker(fg,1.55)` fails on tokyo-night 3.6:1) | secondary text, key hints |
| `accent` | `accent` | panel edge, open-panel underline |
| `accent-ink` / `accent-text` | accent toward fg until ≥ 3:1 / ≥ 4.5:1 | working dots, profile check / diff insertions |
| `live-ink`, `ok-ink`, `warn-ink` | red / green / yellow toward fg until ≥ 3:1 | mic live + failed / done / attention glyphs |
| `fill-hover`, `fill-normal` | fg @ `[controls]` 0.08 / 0.04 | cursor row; search field |
| `border-hover` | fg @ 0.25 | pill hairline |

The formulas are in `src/tokens.py`; per-theme numbers are in `tokens/contrast.md`.

**Type** (`Style.font`, `monospace`): `body 12` primary, `bodySmall 11 text-2` secondary, `caption 10 text-2` key
hints. Bold only for the surface title and the selected option.

**Space** (`Style.space`): 4 / 8 / 12 / 18. Rows are 28 px tall. Pill padding is 12. The quick panel has 18 px
padding and gaps. The History card has 24 px padding and gaps, and a 32 px gap between list and detail.

**Motion** (`Style.duration`, `Style.easeOut`):

| what | how | reduced motion |
|---|---|---|
| voice | meter bars set per 50 ms sample, no tween | same |
| working | 3 dots breathe in turn, 900 ms loop | middle dot lit |
| pill in / out | fade 120 / 120 ms; content swaps instantly | instant |
| panel | `KeyboardPanel` grow 180 / 120 ms | 0 |

There is no glow, outline, sweep, flash, scale or blink.

## 4. Pill (S1) — `exports/indicator-states-*.png`

28 px tall, gap 8, `bg` with a 1 px `border-hover` hairline. The newest dictation always takes over. A recording
interrupts any linger.

| state | fact | pill | linger |
|---|---|---|---|
| recording | `phase: recording` | red dot · meter (8 × 2 px bars, max 12) | — |
| long | elapsed > 60 s | + `1:12` | — |
| near limit | elapsed ≥ `audio.max_ms` − 20 s | + countdown in `warn-ink` | — |
| hands-free | started by `toggle` | lock (`live-ink`) · meter | — |
| pinned profile | `profile_override` set | + pin · `chat` | — |
| server down | `server.state: down` while recording | + ⚠ Server down · saved | while true |
| working | `phase: finishing`, or `start` refused as busy | three breathing dots | — |
| sent | `last.delivery: sent \| confirmed` | ✓ | 0.5 s |
| fallback | `last.fallback` | ✓ via *provider* | 0.8 s |
| cleanup skipped | cleanup expected for the profile, `last.cleanup` ≠ `applied` | ✓ cleanup skipped | 0.8 s |
| mic dropped out | `last.mic_interrupted` | ✓ mic dropped | 1.5 s |
| length limit | `last.stop_reason: limit` | ✓ Stopped at 2:00 | 1.5 s |
| cancelled | `cancelled`, `no_speech`, `too_short`, `empty` | × Cancelled / No speech / Too short | 0.6 s |
| copied | `status: copied` (clipboard mode) | copy glyph · Copied | 1 s |
| focus moved | `status: focus_changed`, `delivery: copied` | copy glyph (`warn`) · Copied · focus moved | 2.5 s |
| paste failed | `status: paste_failed` | copy glyph · Paste failed · copied · `Super Alt D` (no "copied" if `delivery: failed`) | 4 s |
| failed | `status: error` | ⓘ (`live`) · Failed · `Super Alt D` | 4 s |
| mic lost | `mic.status: lost` | mic-off (`live`) · Mic lost · `Super Alt D` | 4 s, again on each key press while lost |
| paused | `start` refused while `paused` | pause · Paused | 1 s |

✓ tags are exclusive. If several apply, show the first of: mic dropped, cleanup skipped, via *provider*.

**Delivery.** ✓ means the text was sent. hark never claims confirmation in the pill, because terminals and many
fields cannot be read back, and it never warns after an ordinary terminal paste. `paste_verdict: missing` shows
nothing for now.

**Mic.** Only `mic.status: lost` (the stream stopped delivering audio) is shown, in red. Quiet input is never
shown: there is no "No input" state.

**Deferred: target marker.** No window outline or glow ships. Trial a target marker only if target confusion
proves common. It must not look like Hyprland's active border: use corner marks or a short target label, never an
accent outline.

## 5. Bar glyph (S2) — `exports/bar-widget-states-*.png`

| state | fact | glyph |
|---|---|---|
| off | no socket / no `subscribe` line for > 35 s | mark at 0.4 opacity |
| paused | `paused` | mic-off, `text-2` |
| recording | `phase: recording` | mark, `live-ink` (`[bar] active`) |
| working | `phase: finishing` | mark, `accent-ink` |
| attention | `server.state: down`, `mic.status: lost`, `config.changed` or `config.restart_needed`, or an unseen `last.status: error \| paste_failed` | mark + 4 px `live-ink` dot, cleared when the panel opens |
| idle | otherwise | mark, `fg` |

Tooltip: one line, `hark · ready · desktop 38 ms`, or the problem. Left click opens the panel; middle click runs
`paste-last`; right click opens History once it exists. Accessible role `StatusBar`; its name is the full state
sentence, updated as a live region.

## 6. Quick panel (S3) — `exports/quick-panel-*.png`

Card: `Style.space(300)` content width, 2 px `accent` border. The cursor (arrows/hjkl) starts on the problem line if
there is one, otherwise on the last dictation.

1. **Header:** mark, **hark**, `server.name · rtt_ms` in `text-2`. The status shows `laptop · fallback` in
   `warn-ink` when `server.fallback`, the rtt in `warn-ink` when `slow`, and `paused` when paused. On the right is
   a labelled text control, **Pause**; while paused it reads **Resume** in `fg`. Enter toggles it.
2. **Problem line** (only when present; first in this order):

   | condition | line | verb |
   |---|---|---|
   | no socket | Not running | `↵ Start` (`systemctl --user start hark.service`) |
   | `mic.status: lost` | Mic lost | `↵ Restart` (`hark service restart`) |
   | `server.state: down` | Server down | `↵ Doctor` |
   | `config.restart_needed` non-empty | Restart needed | `↵ Restart` |
   | `config.changed` | Config changed | `↵ Reload` (`reload`) |

3. **Last dictation:** a 2-line preview and `app · age`, plus a tag when it applies: `· cleanup skipped`, `· mic
   dropped`, `· stopped at 2:00`. Failed shows `Failed · reason`; paste failed shows `Paste failed · in clipboard`.
4. **Profile** `auto ›`: a choice page (auto showing the resolved profile, terminal, chat, prose), via `profile`.
5. **History · Config · Doctor** (History only once it exists).

**Keys on the last dictation.** `↵` = **Copy** (the default, `hark last --copy`). `P` = **Paste**: the panel closes
first, then `paste-last` pastes into the window that had focus before the panel opened. `R` = **Retry** (`hark
retry last`), shown only when `last.retry_available`; on a failed dictation `↵` is Retry. Opening the panel during a
recording never changes the target, which is frozen at key-down.

Preview text, app and failure reason come from the newest record of `hark history -n 1 --json`, read when the
panel opens. That read is text-bearing and local, and is never logged. `status` itself carries no text.

## 7. History (S4, optional, later) — `exports/history-*.png`

A 900 × 480 card with search (`/`; matches both texts and the app; `failed` works as a query word).

- **List:** day groups; each row has a glyph, `time · app`, and a 2-line preview (or `Failed · reason`).
- **Detail:** two full texts. **Final** (`C copy`) is what was delivered. **Recognized** (`⇧C copy`) is the
  recognizer output, already biased by context. `D` switches both to one inline diff (removed words struck through
  in `text-2`, added in `accent-text`) and back. The diff shows what changed; it does not prove cleanup kept the
  meaning. The meta line reads `time · app · profile · delivery · ms`, where delivery is **Confirmed / Sent /
  Copied / Failed** from the record. Then `P paste  R retry  Del delete`; Retry appears only while audio is kept.
  A cleanup outcome line appears only when cleanup did not apply.

Pipeline internals stay in `hark history show`. There is no words UI; spellings live in `[[words]]` in the config.

## 8. Behaviour and accessibility

- Nothing hark draws during a dictation takes focus or input. The overlay is mapped only while it has content.
- `status` / `subscribe` never carry dictated text, titles or field content. The shell may log them; it never logs
  the history read.
- Contrast: text ≥ 4.5:1 and glyphs ≥ 3:1 on every theme (solved at runtime). The diff uses strikethrough and
  colour together.
- Keyboard: `Super Alt D`; panel (arrows/hjkl, Enter, ←, Esc, Tab to the neighbouring panel, P, R); history (`/`,
  C, ⇧C, D, P, R, Del).
- Sizes scale with `omarchy display text size`; durations are 0 when Hyprland animations are off.
- Notifications only for failed and focus-moved outcomes (they outlive the pill).

## 9. Contract gaps

The design uses only ctl v1 facts and existing commands (`paste-last`, `profile`, `reload`, `subscribe`) plus the
local CLI (`hark last --copy`, `hark retry`, `hark history --json`, `hark service restart`). One gap remains:

- **Pause / Resume** needs a ctl command. `status.paused` exists, and the daemon's `set_paused` is used only by the
  macOS menu bar.

Cleanup and Sounds toggles are not in the panel: they are config keys (edit, then `↵ Reload`).

## 10. Open

- `keep_open` keeps Omarchy's mic indicator lit; check on hardware.
- If Superwhisper is also installed: dim `hark.shell` when hark is not `omarchy-default-dictation`.

## 11. Exports

`cover-tokyo-night` · `system-{…}` · `theme-matrix` · `indicator-states-{…}` · `bar-widget-states-{…}` ·
`quick-panel-{…}`, `quick-panel-{states,attention,profile,off}-tokyo-night` · `history-{…}`,
`history-{diff,failed}-tokyo-night`. `{…}` = tokyo-night and catppuccin-latte.
