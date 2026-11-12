import { F, FONT, TOKENS, VARS, MODES, frame, text, icon, rect, ellipse, v, harkMark, toggle, segmented, dropdown, field, slider, button, chip, keys, sectionHeader, separator, settingRow, listRow, spacer, dot, levelBars, annotation, boardTitle } from './lib.mjs'
import { pill, LEVELS } from './screens-indicator.mjs'

export function cover(theme) {
  const pages = [
    ['01', 'Tokens & components', 'Omarchy theme roles → hark tokens, type, spacing, controls'],
    ['02', 'Indicator pill', '12 states, anatomy, light/dark'],
    ['03', 'Edge glow', 'target-window glow, intensity, states, reduced motion, timing'],
    ['04', 'Bar widget & quick panel', 'bar states, popup, profile page, daemon off'],
    ['05', 'Settings', '8 sections × tokyo-night / catppuccin-latte'],
    ['06', 'History', 'list + detail, failed + retry, search'],
    ['07', 'Onboarding', 'six first-run steps'],
    ['08', 'Theme matrix', 'the same components in 8 Omarchy themes'],
    ['09', 'Motion & accessibility', 'motion tokens, keyboard map, focus, contrast']
  ]
  const principles = [
    ['Native, not themed', 'Every colour, size and radius comes from the running Omarchy theme (colors.toml, shell.toml, Hyprland rounding). No hark palette.'],
    ['Passive while you talk', 'Nothing hark draws during a dictation takes focus or clicks: layer surfaces with an empty input region.'],
    ['Words, place, signal', 'Pill = what is happening (words). Glow = where the text will land. Bar glyph = mic live. Never colour alone.'],
    ['Honest outcomes', '“Copied · focus moved” and “Failed · kept for retry” are first-class states, not toasts that vanish.'],
    ['Keyboard first', 'Omarchy panel conventions: hjkl/arrows cursor, Enter, ← back, Tab across panels, / search, one-letter actions.'],
    ['Privacy you can see', 'Every context source says what it reads and what leaves the machine; learning shows while it watches.']
  ]
  return frame({ name: `export/cover-${theme}`, flex: 'col', gap: 28, p: 48, w: 1440, bg: v('bg') },
    frame({ flex: 'row', gap: 16, items: 'center' }, harkMark(40), frame({ flex: 'col', gap: 4 },
      text('hark · status & settings UI for Omarchy', { size: F.displayLarge, weight: 'bold' }),
      text('QuickShell plugins for omarchy-shell (Omarchy 4 “quattro”) · design file hark-ui.fig · 2026-11-12', { size: F.body, color: 'text-2' }))),
    frame({ flex: 'row', gap: 40, w: 'fill', items: 'start' },
      frame({ flex: 'col', gap: 10, w: 560 }, sectionHeader('Pages'),
        pages.map(([n, a, b]) => frame({ flex: 'row', gap: 12, w: 'fill', items: 'start' }, text(n, { size: F.body, weight: 'bold', color: 'accent-text' }), frame({ flex: 'col', gap: 2, grow: 1 }, text(a, { size: F.body, weight: 'bold' }), text(b, { size: F.bodySmall, color: 'text-2' }))))),
      frame({ flex: 'col', gap: 10, grow: 1 }, sectionHeader('Principles'),
        principles.map(([a, b]) => frame({ flex: 'col', gap: 2, w: 'fill' }, text(a, { size: F.body, weight: 'bold' }), text(b, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 16 }))))),
    frame({ flex: 'row', gap: 16, items: 'center', w: 'fill' },
      pill({ state: 'recording', label: 'Listening', levels: LEVELS.speaking, elapsed: '0:03', profile: 'terminal' }),
      pill({ state: 'pasted', label: 'Pasted', detail: 'Ghostty · 412 ms' }),
      pill({ state: 'copied', label: 'Copied · focus moved', keys: ['Super+V'], keysLabel: 'to paste' })),
    annotation('Every fill, stroke and text colour is bound to the variable collection “hark · Omarchy theme” (one mode per Omarchy theme). Select any frame and switch its variable mode to preview another theme.', { w: 900 }))
}

export function tokensBoard(theme) {
  const t = TOKENS[theme]
  const sw = (name) => {
    const [key, desc] = VARS[name]
    return frame({ name: `token/${name}`, flex: 'row', gap: 10, w: 'fill', items: 'center' },
      rect({ w: 28, h: 28, bg: v(name), stroke: v('border-hover'), strokeWidth: 1 }),
      frame({ flex: 'col', gap: 1, w: 150 }, text(name, { size: F.bodySmall, weight: 'bold' }), text(t[key], { size: F.caption, color: 'text-2' })),
      frame({ flex: 'col', grow: 1 }, text(desc, { size: F.caption, color: 'text-2', w: 'fill', lineHeight: 13 })))
  }
  const groups = [
    ['Omarchy palette (colors.toml)', ['bg', 'fg', 'accent', 'urgent', 'green', 'yellow', 'dark-bg', 'lighter-bg', 'selection']],
    ['[controls] state fills (shell.toml)', ['fill-normal', 'fill-hover', 'fill-selected', 'fill-pressed', 'border-hover', 'border-normal']],
    ['hark accessibility-solved', ['text-2', 'border-control', 'accent-ink', 'accent-text', 'live-ink', 'ok-ink', 'warn-ink']],
    ['hark tints', ['accent-fill', 'live-fill', 'ok-fill', 'warn-fill', 'on-accent', 'glow-soft']]
  ]
  const c = t.contrast
  return frame({ name: `export/tokens-${theme}`, flex: 'col', gap: 18, p: 32, w: 1180, bg: v('bg') },
    boardTitle(`Tokens · ${theme} (${t.mode})`, 'Variables in the collection “hark · Omarchy theme”. Formulas are evaluated at runtime by hark’s Theme.qml from ~/.local/state/omarchy/current/theme/colors.toml and the merged shell.toml, so any Omarchy theme works without a hark-specific palette.'),
    frame({ grid: true, columns: '1fr 1fr', gap: 24, w: 'fill' },
      groups.map(([g, names]) => frame({ flex: 'col', gap: 8, w: 'fill' }, sectionHeader(g), names.map(sw)))),
    frame({ name: 'contrast', flex: 'row', gap: 8, wrap: true, w: 'fill' },
      [['fg/bg', c['fg/bg'], 4.5], ['text-2/bg', c['text_secondary/bg'], 4.5], ['Omarchy dim/bg', c['text_dim_omarchy/bg'], 4.5], ['accent/bg', c['accent/bg'], 3], ['border-control/bg', c['border_control/bg'], 3], ['warn-ink/bg', c['warn_ink/bg'], 3], ['ok-ink/bg', c['ok_ink/bg'], 3], ['live-ink/bg', c['live_ink/bg'], 3]]
        .map(([l, r, min]) => chip(`${l} ${r}:1 ${r >= min ? '✓' : '✗ < ' + min}`, { tone: r >= min ? 'normal' : 'warn' }))))
}

export function componentsBoard(theme) {
  const item = (label, inner, note) => frame({ name: `component/${label}`, flex: 'col', gap: 8, w: 'fill' }, text(label, { size: F.bodySmall, weight: 'bold' }), inner, note ? text(note, { size: F.caption, color: 'text-2', w: 'fill', lineHeight: 14 }) : '')
  const typeScale = [['caption', F.caption], ['bodySmall', F.bodySmall], ['body', F.body], ['subtitle', F.subtitle], ['title', F.title], ['heading', F.heading], ['display', F.display]]
  return frame({ name: `export/components-${theme}`, flex: 'col', gap: 22, p: 32, w: 1180, bg: v('bg') },
    boardTitle(`Components · ${theme}`, 'Built from qs.Ui where Omarchy has one (ToggleSwitch, CursorSurface, ButtonGroup, Dropdown, TextField, PanelSlider, PanelSectionHeader, BorderSurface); hark adds Pill, LevelMeter, Glow, ProfileChip, KeyHint, StatusRow.'),
    frame({ grid: true, columns: '1fr 1fr 1fr', gap: 24, w: 'fill' },
      item('Type scale (Style.font, base 12)', frame({ flex: 'col', gap: 4 }, typeScale.map(([n, s]) => text(`${n} ${s}`, { size: s, maxLines: 1 }))), 'JetBrains Mono here; real family = fontconfig `monospace` (`omarchy font set`)'),
      item('Spacing (Style.spacing)', frame({ flex: 'col', gap: 4 }, [['xs', 3], ['sm', 4], ['md', 6], ['lg', 8], ['xl', 10], ['xxl', 12], ['panelGap', 14], ['panelPadding', 18], ['controlHeight', 28]].map(([n, s]) => frame({ flex: 'row', gap: 8, items: 'center' }, rect({ w: s * 3, h: 6, bg: v('accent-ink') }), text(`${n} ${s}`, { size: F.caption, color: 'text-2' })))), 'all through Style.space(px): scales with [spacing] scale and font size'),
      item('Toggle (ToggleSwitch)', frame({ flex: 'row', gap: 12, items: 'center' }, toggle(false), toggle(true), toggle(true, { hasCursor: true })), 'off · on · keyboard cursor ring. Knob position is the state cue; off border uses border-control (≥ 3:1)'),
      item('Segmented (ButtonGroup)', segmented(['Off', 'Light', 'Standard'], 'Light'), 'selected = fill-selected + bold'),
      item('Dropdown / field / number', frame({ flex: 'col', gap: 6 }, dropdown('Auto · Hyprland in-compositor'), field('desktop.tailnet:50051'), field('1000', { w: 120, suffix: 'ms' }), field('desktop.tail', { focused: true })), 'focus = 2 px accent-ink border (Qt activeFocus)'),
      item('Slider (PanelSlider)', slider(0.35, { label: '10 recordings' }), 'track fill-selected, fill fg, knob fg with 2 px bg ring'),
      item('Buttons', frame({ flex: 'row', gap: 8, wrap: true }, button('Retry now', { kind: 'primary', hint: 'R' }), button('Copy', { hint: 'C' }), button('Hover', { hasCursor: true }), button('Delete…', { kind: 'danger' }), button('Bind…', { kind: 'ghost' })), 'primary only for the one forward action of a view'),
      item('Chips & key hints', frame({ flex: 'row', gap: 6, wrap: true, items: 'center' }, chip('terminal'), chip('chat', { tone: 'accent', iconName: 'mdi:pin' }), chip('in use', { tone: 'ok' }), chip('approve?', { tone: 'accent' }), chip('1 of 2'), keys('Right Alt'), keys('Alt', 'Space')), 'chips: caption bold upper, 1 px border'),
      item('Rows (CursorSurface)', frame({ flex: 'col', gap: 4, w: 'fill' }, listRow({ iconName: 'mdi:history', label: 'History', value: '214', hasCursor: false }), listRow({ iconName: 'mdi:cog-outline', label: 'Settings', value: '', hasCursor: true })), 'hover = keyboard cursor: fill-hover + 1 px border-hover'),
      item('Level meter', frame({ flex: 'col', gap: 6 }, levelBars(LEVELS.speaking), levelBars(LEVELS.quiet), levelBars(LEVELS.silent)), 'rolling 12 × 50 ms RMS; floor < 8 % drawn as border-hover'),
      item('Status dots', frame({ flex: 'row', gap: 10, items: 'center' }, dot('live-ink'), dot('accent-ink'), dot('ok-ink'), dot('warn-ink'), dot('text-2')), 'always paired with a word or glyph'),
      item('Bar glyph', frame({ flex: 'row', gap: 12, items: 'center' }, harkMark(16), harkMark(16, 'live-ink'), harkMark(16, 'accent-ink'), harkMark(16, 'text-2'), harkMark(24)), 'three-sample waveform; idle · recording · processing · off')))
}

export function themeMatrix() {
  const col = (m) => frame({ name: `theme=${m}`, flex: 'col', gap: 10, p: 14, w: 'fill', bg: v('bg') },
    text(m, { size: F.bodySmall, weight: 'bold' }),
    text(TOKENS[m].mode, { size: F.caption, color: 'text-2' }),
    pill({ state: 'recording', label: 'Listening', levels: LEVELS.speaking.slice(0, 8), profile: 'terminal' }),
    pill({ state: 'copied', label: 'Copied', keys: ['Super+V'] }),
    pill({ state: 'error', label: 'Failed', detail: 'hark retry' }),
    frame({ flex: 'col', gap: 6, w: 'fill', p: 10, stroke: v('accent'), strokeWidth: 2 },
      frame({ flex: 'row', gap: 8, items: 'center', w: 'fill' }, harkMark(16), text('hark', { size: F.body, weight: 'bold' }), spacer(), toggle(true)),
      listRow({ label: 'Profile', value: 'Auto', hasCursor: true }),
      listRow({ label: 'Server', value: '12 ms' }),
      frame({ flex: 'row', gap: 6, wrap: true }, chip('in use', { tone: 'ok' }), chip('approve?', { tone: 'accent' }), chip('kept', { tone: 'warn' }))))
  return frame({ name: 'export/theme-matrix', flex: 'col', gap: 16, p: 32, w: 1720, bg: '#202020' },
    text('Theme matrix · same components, eight Omarchy themes', { size: F.heading, weight: 'bold', color: '#e0e0e0' }),
    text('Each column is the same JSX in a different variable mode. Check: legibility of text-2, chip borders, warn/ok inks on light themes (catppuccin-latte, rose-pine, flexoki-light).', { size: F.bodySmall, color: '#b0b0b0' }),
    frame({ grid: true, columns: 'repeat(4, 1fr)', gap: 12, w: 'fill' }, MODES.map(col)))
}

export function motionA11y(theme) {
  const row = (cells, head = false) => frame({ flex: 'row', w: 'fill', gap: 12, px: 10, py: 6, bg: head ? v('fill-normal') : undefined },
    cells.map(([c, w]) => frame({ w }, text(c, { size: head ? F.caption : F.bodySmall, weight: head ? 'bold' : undefined, color: head ? 'text-2' : 'fg', w, lineHeight: 15 }))))
  const motion = [
    ['pill in / out', '120 / 120 ms', 'fade + 0.97→1 scale, Style.easeOut', 'instant'],
    ['pill state change', '0 ms', 'content swaps; width animates 140 ms OutCubic', 'width jumps'],
    ['level meter', '50 ms tick', 'no tween; bars set per sample', 'same'],
    ['glow in / out', '180 / 400 ms', 'opacity, Style.easeOut', 'static border, no fade'],
    ['glow envelope', 'attack 60 / release 280', 'one-pole smoothing of RMS', 'fixed e = 0.5'],
    ['processing sweep', '1.2 s loop', 'Easing.Linear (constant motion)', 'none; pill words only'],
    ['outcome flash', '120 in · 400 out', 'colour cross-fade', 'colour swap, no fade'],
    ['panel open / close', '180 / 120 ms', 'KeyboardPanel grow from 0.97 + fade', '0 ms'],
    ['toggle knob', '120 ms', 'OutCubic (ToggleSwitch)', '0 ms']
  ]
  const kb = [
    ['anywhere', 'Right Alt (hold) · Alt+Space · Super+Esc', 'record · hands-free · cancel (Omarchy binds)'],
    ['bar glyph', 'click · right-click · middle-click', 'quick panel · settings · paste last'],
    ['quick panel', '↑↓/jk · ↵/→ · ← · Esc · Tab · C · P · S', 'move · open · back · close · next panel · copy · paste · stop watch'],
    ['settings', 'Tab/Shift+Tab · ↑↓ · ↵/Space · Bksp · / · Esc', 'section · row · change · reset · search · close'],
    ['history', '/ · C · P · R · Space · Del', 'search · copy · paste again · retry · play · delete'],
    ['learned words', 'A · X · F', 'approve · dismiss · forget']
  ]
  return frame({ name: `export/motion-a11y-${theme}`, flex: 'col', gap: 18, p: 32, w: 1180, bg: v('bg') },
    boardTitle('Motion & accessibility', 'Motion follows Omarchy agents/skills/motion.md: every duration via Style.duration(ms) (0 when Hyprland animations are off), ease-out entrances, exits ≈ ⅔, nothing over 300 ms except the constant sweep.'),
    sectionHeader('Motion tokens'),
    frame({ name: 'motion-table', flex: 'col', w: 'fill', stroke: v('border-control'), strokeWidth: 1 },
      row([['element', 200], ['duration', 170], ['curve / method', 400], ['reduced motion', 200]], true),
      motion.map((m) => row([[m[0], 200], [m[1], 170], [m[2], 400], [m[3], 200]]))),
    sectionHeader('Keyboard map'),
    frame({ name: 'keyboard-table', flex: 'col', w: 'fill', stroke: v('border-control'), strokeWidth: 1 },
      row([['surface', 160], ['keys', 420], ['does', 440]], true),
      kb.map((k) => row([[k[0], 160], [k[1], 420], [k[2], 440]]))),
    sectionHeader('Focus & screen readers'),
    frame({ flex: 'row', gap: 20, w: 'fill', items: 'start' },
      frame({ flex: 'col', gap: 8, w: 360 }, listRow({ iconName: 'mdi:cog-outline', label: 'Keyboard cursor', value: 'fill-hover + border', hasCursor: true }), field('Focused field', { focused: true, w: 360 }), toggle(true, { hasCursor: true })),
      frame({ flex: 'col', gap: 8, w: 736 },
        ...[
          'Pill/glow surfaces are layer-shell overlays with KeyboardFocus.None — Orca cannot focus them. Each state change is therefore also (a) an AT-SPI announcement from the bar widget item (Accessible.role StatusBar, name = state words, live region), (b) a sound cue, (c) a notification for copied/failed.',
          'Every interactive item sets Accessible.role/name/description and Accessible.onPressAction = the same action as Enter.',
          'Contrast: text ≥ 4.5:1 (text-2 solved per theme), glyphs/borders ≥ 3:1 (inks solved per theme). Omarchy’s own dim text (Qt.darker 1.55) fails on tokyo-night 3.6:1, gruvbox 3.56:1, everforest 3.18:1 — hark does not use it.',
          'Flashing: the glow never changes brightness by more than 0.25 within 333 ms (≤ 3 flashes/s, WCAG 2.3.1); error never blinks.',
          'Text size: everything scales with `omarchy display text size` ([font] base-size) because all metrics go through Style.font/Style.space.'
        ].map((s) => frame({ flex: 'row', gap: 8, w: 736 }, text('•', { size: F.bodySmall }), text(s, { size: F.bodySmall, w: 716, lineHeight: 16 }))))))
}
