// Bar widget (`hark.shell`, kind bar-widget, defaultSection right) and its KeyboardPanel popup.
import { F, frame, text, icon, rect, v, harkMark, toggle, chip, keys, separator, listRow, segmented, boardTitle, annotation, spacer, dot } from './lib.mjs'
import { omarchyBar, harkBarWidget, BAR_H } from './screens-desktop.mjs'
import { levelBars } from './lib.mjs'
import { LEVELS } from './screens-indicator.mjs'

const W = 360 // contentWidth: Style.space(360)

function hero({ on = true, meta = 'READY · LOCAL SERVER · 12 MS', live, cursor = false }) {
  return frame({ name: 'hero', flex: 'row', w: 'fill', gap: 14, items: 'center' },
    frame({ w: 24, h: 24, opacity: on ? 1 : 0.5 }, harkMark(24, 'fg')),
    frame({ flex: 'col', gap: 2, grow: 1 },
      text('hark', { size: F.subtitle, weight: 'bold' }),
      text(meta, { size: F.caption, weight: 'bold', color: 'text-2', track: 1.2, maxLines: 1, w: 'fill' })),
    live ? frame({ name: 'live-state', flex: 'row', gap: 6, px: 6, py: 2, items: 'center', stroke: v('border-control'), strokeWidth: 1 }, dot('live-ink', 6), text(live, { size: F.caption, weight: 'bold', color: 'text-2' })) : '',
    toggle(on, { hasCursor: cursor }))
}

function settingRow(label, value, { cursor = false, warn = false, valueIcon } = {}) {
  return frame({ name: `setting/${label}`, flex: 'row', w: 'fill', gap: 8, px: 10, py: 8, items: 'center', bg: cursor ? v('fill-hover') : undefined, stroke: cursor ? v('border-hover') : undefined, strokeWidth: cursor ? 1 : undefined },
    text(label, { size: F.body }), spacer(),
    valueIcon || '',
    text(value, { size: F.body, color: warn ? 'fg' : 'text-2', maxLines: 1 }),
    text('›', { size: F.body }))
}

function actionRow(glyph, label, { caption, hint, chevron = false, cursor = false } = {}) {
  return frame({ name: `action/${label}`, flex: 'row', w: 'fill', gap: 8, px: 10, py: 7, items: 'center', bg: cursor ? v('fill-hover') : undefined, stroke: cursor ? v('border-hover') : undefined, strokeWidth: cursor ? 1 : undefined },
    icon(glyph, { size: F.icon }),
    frame({ flex: 'col', gap: 1, grow: 1 }, text(label, { size: F.body }), caption ? text(caption, { size: F.caption, color: 'text-2' }) : ''),
    hint ? text(hint, { size: F.caption, weight: 'bold', color: 'text-2' }) : '',
    chevron ? text('›', { size: F.body }) : '')
}

function lastDictation({ cursor = false } = {}) {
  return frame({ name: 'last-dictation', flex: 'col', w: 'fill', gap: 4, px: 10, py: 10, bg: cursor ? v('fill-hover') : undefined, stroke: cursor ? v('border-hover') : undefined, strokeWidth: cursor ? 1 : undefined },
    text('refactor learn.py so the watch window closes when the focus moves away', { size: F.body, w: 'fill', maxLines: 3, lineHeight: 16 }),
    frame({ flex: 'row', w: 'fill', gap: 8, items: 'center' },
      icon('mdi:check', { size: 11, color: 'ok-ink' }),
      text('pasted · Ghostty · 412 ms · 2m ago', { size: F.caption, color: 'text-2', maxLines: 1 }), spacer(),
      cursor ? frame({ flex: 'row', gap: 8 }, text('C copy', { size: F.caption, color: 'fg' }), text('P paste', { size: F.caption, color: 'fg' })) : ''))
}

function watchRow() {
  return frame({ name: 'learning-watch', flex: 'row', w: 'fill', gap: 8, px: 10, py: 7, items: 'center', stroke: v('accent-ink'), strokeWidth: 1, bg: v('accent-fill') },
    icon('mdi:eye-outline', { size: F.icon, color: 'accent-ink' }),
    frame({ flex: 'col', gap: 1, grow: 1 }, text('Watching your edit in Slack', { size: F.body }), text('22 s left · keeps only word pairs', { size: F.caption, color: 'text-2' })),
    text('S stop', { size: F.caption, weight: 'bold', color: 'fg' }))
}

function panelCard(children) {
  return frame({ name: 'KeyboardPanel', flex: 'col', w: W + 28, p: 14, gap: 12, bg: v('bg'), stroke: v('accent'), strokeWidth: 2 }, children)
}

function withBar(name, panel, { hark = 'idle', w = 760 } = {}) {
  // bar crop (right part) + panel hanging under the hark icon, gap = Style.gapsOut (5)
  return frame({ name, w, h: 'hug', flex: 'col', bg: v('dark-bg') },
    frame({ name: 'bar-crop (right of a 1280 px bar)', w, h: BAR_H, overflow: 'hidden' }, frame({ position: 'absolute', x: w - 1280, y: 0 }, omarchyBar(1280, { hark, underline: true }))),
    frame({ flex: 'row', w, justify: 'end', pr: 120, pt: 5, pb: 40 }, panel))
}

export function quickPanelIdle(theme) {
  const p = panelCard([
    hero({}),
    separator(),
    frame({ flex: 'col', gap: 6, w: 'fill' },
      settingRow('Profile', 'Auto · prose', { cursor: true }),
      settingRow('Microphone', 'Built-in Mic'),
      settingRow('Server', '127.0.0.1 · 12 ms', { valueIcon: dot('ok-ink', 6) }),
      settingRow('Cleanup', 'On · 3 profiles')),
    separator(),
    frame({ flex: 'col', gap: 6, w: 'fill' }, lastDictation(), actionRow('mdi:history', 'History', { chevron: true, caption: '214 dictations · 2 failed' })),
    separator(),
    frame({ flex: 'col', gap: 6, w: 'fill' }, actionRow('mdi:cog-outline', 'Settings', { hint: ',' }), actionRow('mdi:stethoscope', 'Diagnostics', { caption: 'all 7 checks passed · 1h ago', hint: 'D' }))
  ])
  return frame({ name: `export/quick-panel-${theme}`, flex: 'col', gap: 12, p: 24, w: 808, bg: v('dark-bg') },
    boardTitle(`Bar widget → quick panel · ${theme}`, 'Click the hark glyph (or `omarchy-shell hark.shell toggle`). KeyboardPanel under the icon; arrows/hjkl move the cursor, Enter/→ opens, ← back, Esc closes, Tab switches to the neighbouring bar panel. Right-click opens Settings.'),
    withBar('composition', p))
}

export function quickPanelRecording(theme) {
  const p = panelCard([
    hero({ meta: 'RECORDING', live: 'LISTENING 0:04' }),
    frame({ name: 'live-level', flex: 'row', w: 'fill', gap: 10, px: 10, py: 8, items: 'center', bg: v('fill-normal') },
      levelBars(LEVELS.speaking.concat(LEVELS.speaking).slice(0, 18)), spacer(), text('→ Ghostty', { size: F.bodySmall, color: 'text-2' }), chip('terminal')),
    frame({ flex: 'row', gap: 8, w: 'fill' }, buttonLike('Stop', 'mdi:stop', 'Enter'), buttonLike('Cancel', 'mdi:close', 'Esc')),
    separator(),
    frame({ flex: 'col', gap: 6, w: 'fill' }, watchRow(), settingRow('Profile', 'terminal (Ghostty)'), settingRow('Microphone', 'Built-in Mic')),
    separator(),
    frame({ flex: 'col', gap: 6, w: 'fill' }, lastDictation({ cursor: true }))
  ])
  return frame({ name: `export/quick-panel-recording-${theme}`, flex: 'col', gap: 12, p: 24, w: 808, bg: v('dark-bg') },
    boardTitle(`Quick panel while recording · ${theme}`, 'Opening the panel never steals the paste target: the target was frozen at key-down. Stop/Cancel act like `hark ctl stop|cancel`. The learning watch is shown whenever it is reading a field, with a one-key stop.'),
    withBar('composition', p, { hark: 'recording' }))
}

function buttonLike(label, glyph, k) {
  return frame({ flex: 'row', w: 176, gap: 6, px: 10, h: 28, items: 'center', bg: v('fill-normal'), stroke: v('border-control'), strokeWidth: 1 },
    icon(glyph, { size: 13 }), text(label, { size: F.bodySmall }), spacer(), text(k, { size: F.caption, color: 'text-2' }))
}

export function quickPanelProfile(theme) {
  const opt = (label, caption, selected, cursor) => frame({ name: `option/${label}`, flex: 'row', w: 'fill', gap: 8, px: 10, py: 7, items: 'center', bg: cursor ? v('fill-hover') : undefined, stroke: cursor ? v('border-hover') : undefined, strokeWidth: cursor ? 1 : undefined },
    frame({ w: 14, h: 14 }, selected ? icon('mdi:check', { size: 12 }) : ''),
    frame({ flex: 'col', gap: 1, grow: 1 }, text(label, { size: F.body, weight: selected ? 'bold' : undefined }), text(caption, { size: F.caption, color: 'text-2', w: 'fill' })))
  const p = panelCard([
    hero({}),
    separator(),
    frame({ flex: 'col', gap: 6, w: 'fill' },
      frame({ name: 'back', flex: 'row', w: 'fill', gap: 8, px: 10, py: 7, items: 'center' }, text('‹', { size: F.body }), text('Profile', { size: F.body, weight: 'bold' })),
      frame({ flex: 'col', gap: 2, w: 'fill' },
        opt('Auto', 'first matching app rule · now: prose (Chromium)', true, false),
        opt('terminal', 'one line · Ctrl+Shift+V · never Enter · cleanup “terminal”', false, true),
        opt('chat', 'Slack, Discord, Signal… · cleanup “chat”', false, false),
        opt('prose', 'fallback · cleanup “prose”', false, false))),
    separator(),
    frame({ flex: 'col', gap: 8, w: 'fill', px: 10 },
      text('PIN A PROFILE FOR', { size: F.caption, weight: 'bold', color: 'text-2', track: 1.2 }),
      segmented(['Next dictation', 'Until changed'], 'Next dictation'),
      annotation('Pinned profiles show as an accent chip with a pin in the pill. Same as `hark ctl start --profile terminal` (proposed).', { w: 330 }))
  ])
  return frame({ name: `export/quick-panel-profile-${theme}`, flex: 'col', gap: 12, p: 24, w: 808, bg: v('dark-bg') },
    boardTitle(`Quick panel · profile choice page · ${theme}`, 'Choice pages drill the whole card (Omarchy pattern): ← or the back row returns to the row it came from.'),
    withBar('composition', p))
}

export function quickPanelOff(theme) {
  const p = panelCard([
    hero({ on: false, meta: 'OFF · DAEMON NOT RUNNING', cursor: true }),
    frame({ name: 'off-message', flex: 'row', w: 'fill', gap: 8, px: 10, py: 8, items: 'start', bg: v('warn-fill'), stroke: v('warn-ink'), strokeWidth: 1 },
      icon('mdi:alert-outline', { size: 14, color: 'warn-ink' }),
      frame({ flex: 'col', gap: 2, grow: 1 },
        text('hark.service is inactive', { size: F.body }),
        text('Right Alt starts it on the next press (Omarchy dictation contract); or switch it on here.', { size: F.caption, color: 'text-2', w: 'fill', lineHeight: 14 }))),
    separator(),
    frame({ flex: 'col', gap: 6, w: 'fill' }, actionRow('mdi:cog-outline', 'Settings', { hint: ',' }), actionRow('mdi:stethoscope', 'Diagnostics', { caption: 'last run: server unreachable', hint: 'D' }))
  ])
  return frame({ name: `export/quick-panel-off-${theme}`, flex: 'col', gap: 12, p: 24, w: 808, bg: v('dark-bg') },
    boardTitle(`Quick panel · daemon off · ${theme}`, 'The switch starts/stops hark.service for this session (systemctl --user), like Omarchy’s Bluetooth/Tailscale panels; the bar glyph dims while off.'),
    withBar('composition', p, { hark: 'off' }))
}

export function barWidgetStates(theme) {
  const states = [
    ['idle', 'ready; mic pre-roll buffer open'],
    ['recording', 'bar.active (red) = mic captured'],
    ['handsfree', 'lock badge: stop with Alt+Space'],
    ['processing', 'accent dot: transcribing / cleaning'],
    ['watching', 'eye badge: learning watch open'],
    ['error', 'red dot until the panel is opened'],
    ['unreachable', 'warn dot: server down; dictation queues for retry'],
    ['off', 'dimmed (Qt.darker 1.55): daemon off']
  ]
  return frame({ name: `export/bar-widget-states-${theme}`, flex: 'col', gap: 18, p: 24, w: 1060, bg: v('dark-bg') },
    boardTitle(`Bar widget states · ${theme}`, 'Style.bar.iconSlot 27 × bar height 26; glyph = three-sample waveform at Style.bar.iconSize. Tooltip names the state and the last result. Middle-click = paste last (`hark ctl paste-last`).'),
    frame({ grid: true, columns: 'repeat(4, 1fr)', gap: 16, w: 'fill' }, states.map(([s, d]) => frame({ name: `state/${s}`, flex: 'col', gap: 6 },
      frame({ flex: 'row', h: BAR_H, px: 8, items: 'center', bg: v('bg'), gap: 4 }, icon('mdi:bluetooth', { size: 13 }), harkBarWidget(s), icon('mdi:wifi', { size: 13 })),
      text(s, { size: F.bodySmall, weight: 'bold' }),
      text(d, { size: F.caption, color: 'text-2', w: 'fill', lineHeight: 14 })))),
    frame({ name: 'tooltip-example', flex: 'row', gap: 16, items: 'start' },
      frame({ flex: 'col', gap: 2, px: 10, py: 6, bg: v('bg'), stroke: v('fg'), strokeWidth: 1 },
        text('hark · ready', { size: F.bodySmall, weight: 'bold' }),
        text('last: pasted into Ghostty · 412 ms · 2m ago', { size: F.caption, color: 'text-2' }),
        text('click: panel · right-click: settings · middle: paste last', { size: F.caption, color: 'text-2' })),
      annotation('Tooltip = [tooltip] tokens (bg α .97, 1 px fg border). Never shows dictated text (ctl status has none).', { w: 300 })))
}
