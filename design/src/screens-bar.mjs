// Omarchy bar mock (omarchy.bar, 26 px) and the skriv bar glyph (S2).
import { F, frame, text, icon, rect, v, skrivMark, dot } from './lib.mjs'

export const BAR_H = 26

// Bar glyph: colour = activity (red = mic live, accent = working); one 4 px red dot = look at the panel.
export function skrivBarWidget(state = 'idle') {
  const slot = (name, ...c) => frame({ name: `skriv-bar-widget/${name}`, w: 27, h: BAR_H, items: 'center', justify: 'center', flex: 'row', opacity: state === 'off' ? 0.4 : undefined }, ...c)
  if (state === 'paused') return slot('paused', icon('mdi:microphone-off', { size: 14, color: 'text-2' }))
  const color = { idle: 'fg', recording: 'live-ink', working: 'accent-ink', attention: 'fg', off: 'fg' }[state]
  return slot(state, frame({ w: 16, h: 16 }, skrivMark(14, color)),
    state === 'attention' ? frame({ name: 'attention', position: 'absolute', x: 19, y: 5, w: 4, h: 4 }, dot('live-ink', 4)) : '')
}

export function omarchyBar(w, { skriv = 'idle', clock = 'Wednesday 14:32', underline = false } = {}) {
  const ws = ['1', '2', '3', '4']
  return frame({ name: 'omarchy.bar', flex: 'row', w, h: BAR_H, px: 10, items: 'center', bg: v('bg') },
    frame({ flex: 'row', gap: 14, items: 'center', w: 260 },
      icon('mdi:hexagon-multiple-outline', { size: 14 }),
      frame({ flex: 'row', gap: 10, items: 'center' }, ws.map((n, i) => text(n, { size: F.body, weight: i === 0 ? 'bold' : undefined, color: i === 0 ? 'fg' : 'text-2' })))),
    frame({ grow: 1, flex: 'row', justify: 'center' }, text(clock, { size: F.body, weight: 'bold' })),
    frame({ flex: 'row', gap: 2, items: 'center', w: 260, justify: 'end' },
      frame({ flex: 'col', items: 'center' }, skrivBarWidget(skriv), underline ? rect({ name: 'open-underline', w: 18, h: 2, bg: v('accent'), position: 'absolute', x: 4, y: 24 }) : ''),
      ...['mdi:bluetooth', 'mdi:wifi', 'mdi:volume-high', 'mdi:battery-80'].map((n) => frame({ w: 27, h: BAR_H, items: 'center', justify: 'center', flex: 'row' }, icon(n, { size: 13 })))))
}
