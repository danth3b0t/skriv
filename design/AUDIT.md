# hark UI audit: what should exist

> **Historical.** This audit fed the first redesign (2026-11-12). [SPEC.md](SPEC.md) is authoritative; where they
> differ (target outline, pill states, panel toggles, History, macOS), follow SPEC.md.

Date: 2026-11-12. Input: SPEC.md, all exports, `client/` (config, daemon, menubar, onboarding, doctor, history, ctl,
indicator, pipeline, inject). Scope: *what* exists, not how it looks. Audience: 3–4 technical users who are fine
editing `~/.config/hark/config.toml`.

Verdicts: **KEEP** (in UI) · **MERGE →** (folded into another surface) · **CONFIG** (TOML only, no UI) ·
**CLI** (`hark doctor` / `history` / `learned` / `retry`) · **CUT** (gone, including the proposed backend work).

Rule used: if you'd change it less than about once a month, it goes in the config file. Explanatory copy goes to the
README. The UI is for *state* and *recovery*, not for teaching.

---

## 1. Default path (what you see every day)

| moment | what shows | nothing else |
|---|---|---|
| idle | bar glyph, monochrome | no pill, no glow |
| key down | pill: red dot + level meter. A thin static outline on the **target window**. Bar glyph turns red | no words, timer, profile chip or key hints |
| key up | pill: one indeterminate sweep (transcribing and cleanup are the same state) | no "4.1 s audio", "≤ 1 s", or profile |
| pasted | ✓ for ~0.5 s, then gone. "done" sound | no app name and no ms (they go in the tooltip/history) |

Short words appear only when something needs your attention. When something goes wrong, the pill **stays longer
and offers one action**, the bar glyph keeps an attention dot until you look, and the last text is always one
gesture away (middle-click the glyph = paste last; panel = Copy/Retry). That covers trust and recovery. Everything
else is optional.

---

## 2. Verdicts per surface

| # | Surface (today) | Verdict | Reason |
|---|---|---|---|
| S1 | Indicator pill | **KEEP** | the one daily surface; shrink it to glyph + meter + ≤ 3 words |
| S2 | Edge glow | **KEEP-lite → part of S1** | "where will it land" is a real trust signal. Keep a static outline of the target only. Cut the audio envelope, sweep, flashes and the 3 settings |
| S3 | Bar widget | **KEEP** | always-on status and the entry point; 5 states, not 8 |
| S4 | Quick panel (4 variants) | **KEEP, 1 page** | becomes the only "app" surface: status, last dictation, 2 toggles, links |
| S5a | Settings window (8 sections, search, nav, schema rows) | **CUT** | ~45 controls, almost all changed once per machine. Replace with "Config" (opens `$EDITOR`) plus 2 toggles in the panel |
| S5b | History (list/detail/failed/search) | **KEEP, slim** | the recovery and accuracy-debugging surface; becomes its own small window |
| S5c | Learned-words review | **MERGE → History ("Words" list)** | data, not a setting; you only need Forget |
| S5d | Diagnostics page | **CLI** | `hark doctor` already exists. The panel shows the first failing check inline and has a "Doctor" link that opens a terminal |
| S6 | Onboarding, 6 steps | **CUT** | the installer runs `hark doctor` at the end; the first real dictation is the practice |
| — | Motion/a11y spec | **KEEP rules, CUT knobs** | follow `Style.reduceMotion`; no own motion setting. Keep accessible names on the bar item (AGENTS.md: never drop a11y) |
| — | Tokens | **KEEP, trimmed** | keep "derive everything from Omarchy" and the contrast solver. Only fg, text‑2, accent, live, ok, warn are needed |
| — | macOS menu bar (exists) | **KEEP** | same content as the quick panel (§4). Already close |
| — | macOS Setup & Permissions (exists) | **KEEP** | OS permissions need prompts; this is the one onboarding that is required |

---

## 3. Verdicts per control / block

### S1 Pill states
| state | verdict | content after audit |
|---|---|---|
| recording · hold | KEEP | red dot + meter. Show m:ss only after 60 s. At ≥ 100 s, count down to `audio.max_ms` |
| recording · hands-free | KEEP | lock glyph + meter. "Alt+Space stop" hint CUT |
| recording · no input (P) | KEEP, **add** | mic-off glyph, "No input" (the meter is flat anyway) |
| profile chip | KEEP only on override | pin + profile name; auto-resolved profile is never shown |
| transcribing / cleaning | MERGE → "working" | one sweep, no words. The stage split is internal |
| pasted | KEEP | ✓ only, ~0.5 s |
| copied · focus moved | KEEP | "Copied — focus moved", 2.5 s |
| paste failed | KEEP | "Paste failed — in clipboard" (inject leaves it there), 4 s |
| error | KEEP | "Failed — retry" + 2-word reason (`server timeout`, `server down`), 4 s |
| cancelled / no speech / too short / empty | MERGE → one quiet × state | single word ("Cancelled" / "No speech"), 0.6 s, no detail line |
| warming (P) | KEEP if cheap | "Loading model". Rare, but it explains a 2 s gap |
| info notice ("Profile: chat") | KEEP | already exists, 1.5 s |
| detail lines (app · ms, "nothing pasted", "<n> s audio") | CUT | tooltip/history only |
| `indicator.style` 4 tiles | CONFIG | `pill` / `pill+outline` / `off` |
| `indicator.position` | CONFIG | once |

### S2 Glow
| item | verdict | reason |
|---|---|---|
| static outline on target window, recording only | KEEP | answers "which window"; cheap |
| screen-edge fallback (fullscreen/unknown) | KEEP | same thing |
| RMS envelope, halo blur, 30 Hz MultiEffect | CUT | CPU cost on the laptop and visual noise; the meter already shows level |
| processing sweep on the perimeter | CUT | the pill already sweeps |
| ok/warn/error outcome flashes | CUT | the pill carries outcome words; colour-only flashes add nothing |
| `glow_level`, `glow_strength`, `motion` (P) | CUT | knobs for a removed effect |

### S3 Bar widget
| item | verdict | reason |
|---|---|---|
| idle / recording / working / off(dim) | KEEP | core |
| attention dot (error, unreachable, mic lost, paused) | KEEP, MERGE 3 badges → 1 | one "look at me" signal; the panel says what it is |
| hands-free lock badge | MERGE → recording | the pill already says it |
| learning "watching" eye badge | CUT | learning is opt-in and passive; nothing to act on |
| tooltip | KEEP, 1 line | "hark · ready · desktop 38 ms" or the problem. Click legend CUT |
| left = panel, middle = paste last | KEEP | the fastest recovery there is |
| right = settings | CHANGE → History | settings no longer exists |
| barWidget `showLevel` option | CUT | the pill shows level |

### S4 Quick panel
| row | verdict | note |
|---|---|---|
| header: state + active server + rtt | KEEP | shows "fallback: laptop-cpu" or "server unreachable" when relevant |
| power switch | KEEP as **Pause** | `Controller.set_paused` exists (releases mic). systemctl start/stop → only in the "not running" state, as one "Start" button |
| problem line (only when present) | **ADD** | the first failing condition + one action (Restart · Retry · Doctor) |
| last dictation: 2-line preview + Copy · Paste again · Retry (if failed) | KEEP | main recovery block |
| Profile › (Auto → now: X, terminal, chat, prose) | KEEP | real feature (`ctl profile`, macOS menu). "Pin for" segmented CUT → `profile_switch.sticky` CONFIG |
| Cleanup toggle | KEEP (moved from Settings) | the A/B accuracy lever |
| Sounds toggle | KEEP (moved from Settings) | calls/meetings |
| Microphone › | CUT | the system default source (omarchy.audio) owns this; mic name only appears in a problem line |
| Server › page | CUT | status is in the header; editing goes in the config |
| Cleanup › "3 profiles" | CUT | per-profile setting → CONFIG |
| History › "214 · 2 failed" | KEEP as link | count of failed only, if > 0 |
| Settings , | CHANGE → "Config" | opens `$EDITOR`; the panel then offers "Restart to apply" |
| Diagnostics D "all 7 passed" | CHANGE → "Doctor" link | runs `hark doctor` in a terminal |
| while recording: live strip, Stop/Cancel buttons | CUT | the keys do this; the pill shows it |
| learning watch row + `learn_stop` (P) | CUT | passive and opt-in |
| daemon-off warn box with paragraph | CUT copy | header "Not running" + [Start] |

### S5 Settings: every row (all CUT from UI unless noted)
| section | item | verdict |
|---|---|---|
| all | intro paragraphs, footnotes, key-hint footer, search, nav, modified dots, "saved · atomic" | CUT |
| all | `config_schema/get/set/unset`, comment-preserving TOML writer (P) | CUT; the risk disappears with them |
| General | PTT / hands-free key display | CUT (Omarchy owns the binds; README) |
| General | Cancel bind "Bind…" writer (P) | CUT |
| General | `hotkey.tap_to_lock` (P) | CUT; `hotkey.tap_ms` already exists → CONFIG |
| General | profile rules table, reorder, add rule | CONFIG (`[profiles.*]`) |
| General | Test focused window (P `profile_test`) | CLI (`hark doctor` already prints focus → profile) |
| Recognition | server list + order + chips + status | status → panel header. Editing → CONFIG. `[[providers]]` **already exists**; the SPEC wrongly marks it proposed |
| Recognition | `model_id`, `language`, `final_timeout_ms`, TLS×4, `finalize_budget_ms` | CONFIG |
| Recognition | `gate.*` | CONFIG |
| Recognition | "address changed, apply now" banner | MERGE → generic "Restart to apply" in panel |
| Context | 5 source toggles, never-read list, `exclude_apps` (all P) | CUT (privacy hardening is not a priority) |
| Context | `deadline_ms`, `max_terms` | CONFIG |
| Context | `learn.enabled` | CONFIG (set once) |
| Context | learned table | MERGE → History "Words": heard → written, ×n, Forget |
| Context | Approve/Dismiss, `learn.require_approval` (P) | CUT (`promote_after ≥ 2` already guards) |
| Context | Clear all | CLI (`hark learned clear`) |
| Cleanup | `cleanup.enabled` | KEEP → panel toggle |
| Cleanup | strength Off/Light/Standard (P) | CUT (overlaps per-profile `cleanup_profile`) |
| Cleanup | `deadline_ms`, `resolve_corrections`, per-profile cards | CONFIG |
| Cleanup | last-dictation diff preview | MERGE → History detail (heard vs final) |
| Cleanup | edit chips, "Try another sentence" (P `cleanup_preview`) | CUT |
| Output | `paste.method`, `check_focus`, `smart_spacing`, `restore_clipboard`, `restore_delay_ms`, `verify`, `join_window_ms` | CONFIG |
| Output | AT-SPI missing warning | CLI (doctor) |
| Output | terminal-rules chips | CUT (README) |
| Indicator | style, position | CONFIG |
| Indicator | glow level/strength, motion (P) | CUT |
| Indicator | `feedback.sounds` | KEEP → panel toggle. Preview CUT |
| Indicator | `feedback.notify`, `start_cue`, `cue_mask_ms` | CONFIG |
| Privacy | "what leaves this computer" card | CUT (README) |
| Privacy | retention Off/7/30/Forever (P) | CUT; `history.enabled` → CONFIG |
| Privacy | `retry.keep`, `audio.keep_open`/`preroll_ms`, `save_recordings` | CONFIG |
| Privacy | storage sizes, Delete history/audio, Clear words, Open folder | CUT (rm / CLI) |
| Diagnostics | Run checks, check rows, fix commands | CLI (`hark doctor`; add `fix:` lines there) |
| Diagnostics | Copy redacted report | CUT |
| Diagnostics | last-dictation timing bar | CLI (`hark history show`). History detail keeps one latency number |

### S5b History
| item | verdict | note |
|---|---|---|
| search (raw + final + app) | KEEP | the only filter. Add `failed` as a query word |
| filter segments All/Failed/Copied/Terminal | CUT | search covers it |
| day groups, row: glyph · time · app · 2-line preview | KEEP | |
| row latency, profile | CUT from row | in detail |
| detail: final text (selectable) | KEEP | |
| Copy · Paste again · Retry · Delete | KEEP (keys C/P/R/Del) | Retry only when audio is kept; otherwise hidden, not disabled with an explanation |
| **heard vs final** (raw → final as an inline diff, replaced terms marked) | KEEP, **promote** | this is the "cleanup never changed my sentence" proof. Show only when they differ. Add **Copy heard** |
| cleanup outcome line | KEEP only if partial/rejected/skipped/timeout | one line: "cleanup skipped: timeout" |
| playback + waveform + speed (P `history_play`) | CUT | Retry covers recovery; the WAV path is in `hark history show` |
| paste method, spacing decision, bias counts per source, gate | CLI (`hark history show`) | debugging detail |
| failed: reason card + explanation paragraph | MERGE → status line "Failed · server timeout" + Retry | |
| "Retry and copy" ⇧R | CUT | retry already copies when the target is not focused |
| "what happened" rows, "add a fallback server" pointer | CUT / CLI | |
| copied: "You were in Chromium…" warn card | MERGE → status line "Copied · focus moved" + Paste now | |
| footer key hints, "kept 30 days · audio last 10" | CUT | |
| Words list (from S5c) | KEEP | heard → written · ×n · Forget |

### S6 Onboarding
| step | verdict | replacement |
|---|---|---|
| 1 Welcome (three promises) | CUT | README |
| 2 Server choice + Test + TLS note | CUT | installer prompts for an address (default 127.0.0.1) and writes `[server]`; doctor tests it |
| 3 Mic meter + test sentence | CUT | doctor level check; runtime "No input" pill |
| 4 Keys + practice field | CUT | the first real dictation; Omarchy docs the keys |
| 5 Privacy toggles + retention | CUT | defaults; CONFIG |
| 6 Done + doctor summary | MERGE → installer | `omarchy-install-dictation-hark` ends with `hark doctor` |
| macOS Setup window (permissions, level, hotkey test, servers) | KEEP | required by macOS. Could drop "Servers" (doctor) |

---

## 4. Resulting surface map

| surface | essential contents | trigger |
|---|---|---|
| **Pill + target outline** (overlay) | glyph · meter · ≤ 3 words · override chip. States: recording, hands-free, no-input, working, ✓, copied, failed, quiet ×, notice, loading | dictation activity |
| **Bar glyph** | idle / recording / working / attention / off · 1-line tooltip · L panel · M paste last · R history | always |
| **Quick panel** (1 page + profile list) | 1 header (state · server · rtt) + Pause · 2 problem line (conditional) · 3 last dictation + Copy/Paste/Retry · 4 Profile › · 5 Cleanup ◻ Sounds ◻ · 6 History · Config · Doctor | click glyph / IPC |
| **History** window | search · list · detail (text, heard-vs-final, actions) · Words list | panel link / right-click glyph |
| macOS menu bar | same 6 blocks as the panel as an NSMenu (exists: add Paste last and the Cleanup/Sounds toggles; keep Setup, Launch at Login, Quit) | menu bar |
| macOS Setup window | permissions + hotkey test (exists) | first launch / menu |

**Count.** Omarchy screens today: about 23 (pill, glow, bar, 4 panel variants, 8 settings sections, learned
review, history, 6 onboarding). After the audit: **4 surfaces, 5 screens** (pill, bar, panel, panel/profile,
history). UI controls today: about 45 settings rows plus 6 wizard steps. After: **3 toggles + 1 picker** (Pause,
Cleanup, Sounds, Profile).

Backend work that is no longer needed (from SPEC §7/§10): `config_schema/get/set/unset`, comment-preserving TOML
writes, `doctor` JSON, `devices`, `mic_test`, `server_test`, `profile_test`, `cleanup_preview`,
`history_play/stop`, `learned_approve/dismiss`, `learn_stop`, `context.sources.*`, `exclude_apps`,
`require_approval`, `cleanup.strength`, `retention_days`, `tap_to_lock`, the `glow_*`/`motion` keys, and
`indicator.style` beyond 3 values.
Still needed: `subscribe` (state/level/result/server/mic), the extended `status`, `retry`, `copy_last`,
`set_profile`, `set_paused`, `history_list/get/delete`, `learned_list/forget`, and a generic `config_set` only for
`cleanup.enabled` and `feedback.sounds` (or keep those two as runtime overrides).

---

## 5. Missing states that matter more than the cut text

| condition | today | should show |
|---|---|---|
| **Paused + key press** | `start` returns `error paused`; nothing visible | pill "Paused" 1 s; bar glyph mic-slash |
| **Server unreachable at key-down** (no healthy provider) | router uses the first one anyway; you find out only after you finish speaking | pill shows ⚠ next to the meter *while recording* ("server down · audio kept"), so you know a retry is coming; bar attention dot |
| **Fallback provider served it** (CPU: no bias, no cleanup) | only in history `route` | ✓ plus a small "via laptop" tag; header says "fallback" |
| **Cleanup skipped/timeout/rejected → raw text pasted** | history only | ✓ with a faint "raw" mark; history line. Never silent when cleanup was expected |
| **Paste failed** | pill "Paste failed" | add "in clipboard" (true per `inject.py`) + middle-click paste hint |
| **Paste verdict `missing`** (read-back says it didn't land) | recorded only | later, once the false-positive rate is known: "May not have landed — paste again?". Until then: history glyph only |
| **Mic silent / muted** during recording (P) | nothing | "No input" in the pill |
| **Mic lost** (device unplugged, stream error) | daemon start fails outright; mid-session not handled | bar attention dot + problem line "Mic lost · Restart"; pill on the next press |
| **Daemon not running** | bar dim | the same, plus the first key press shows pill "Starting…" (the Omarchy contract starts the service) |
| **Config edited, not applied** (no hot reload; menubar says "restart") | a notification on macOS only | panel problem line "Config changed · Restart" (compare file mtime with daemon start) |
| **Retry in progress** | notification after the fact | pill "working" → normal outcome, same as a dictation |
| **Max length hit** (`audio.max_ms`) | stops silently | countdown from 100 s; result line "stopped at 2:00" |
| **Model cold** (P) | nothing | "Loading model" while still recording |
| **Context empty / failed** | silent | **not shown** (silent degrade is fine); "0 terms" in history detail; doctor reports errors |
| **Undo/swap-raw on Wayland** | always refused with a notification | don't surface undo/swap in Omarchy UI; "Copy heard" in History covers swap |

Notifications: keep them only for failed and copied/focus-moved outcomes (they outlive the pill). Cut "Learned:
x → y" and "Restart Hark after editing the config" (both move to the panel).

---

## 6. Copy rules for the designer

- The pill has at most 3 words. The panel has no sentences: a label and a value on each row.
- No config keys, no "proposed", no explanations of mechanism in any surface. The README owns explanations.
- Every failure state has exactly one next action, named with a verb (Retry, Paste, Restart, Start).
- Words, a glyph, and colour stay together for each state; never colour alone. The accessible name on the bar
  item carries the full sentence, so the visual can stay terse.

## 7. Doc drift noticed (fix outside this audit)

- README `[context] max_terms = 60` vs code default 30; README `cleanup.deadline_ms = 600` vs code 1000.
- SPEC §4.4 calls a server list "proposed"; `[[providers]]` with priority, `send_context` and health already ships.
- SPEC says "Esc is not swallowed" (Omarchy, true), but `hotkey.swallow_esc` exists for macOS/X11. The difference is
  per platform, so it belongs in the README, not the UI.
