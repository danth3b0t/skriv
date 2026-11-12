// Omarchy desktop mock: omarchy.bar (top, 26 px), Hyprland tiling with gaps_out 10 / gaps_in 5, border_size 2,
// active border = accent, inactive rgba(595959aa), rounding 0.
import { F, frame, text, icon, rect, v, harkMark, dot } from './lib.mjs'

export const BAR_H = 26

// Bar glyph: colour = activity (red = mic live, accent = working); one 4 px red dot = look at the panel.
export function harkBarWidget(state = 'idle') {
  const slot = (name, ...c) => frame({ name: `hark-bar-widget/${name}`, w: 27, h: BAR_H, items: 'center', justify: 'center', flex: 'row', opacity: state === 'off' ? 0.4 : undefined }, ...c)
  if (state === 'paused') return slot('paused', icon('mdi:microphone-off', { size: 14, color: 'text-2' }))
  const color = { idle: 'fg', recording: 'live-ink', working: 'accent-ink', attention: 'fg', off: 'fg' }[state]
  return slot(state, frame({ w: 16, h: 16 }, harkMark(14, color)),
    state === 'attention' ? frame({ name: 'attention', position: 'absolute', x: 19, y: 5, w: 4, h: 4 }, dot('live-ink', 4)) : '')
}

export function omarchyBar(w, { hark = 'idle', clock = 'Wednesday 14:32', underline = false } = {}) {
  const ws = ['1', '2', '3', '4']
  return frame({ name: 'omarchy.bar', flex: 'row', w, h: BAR_H, px: 10, items: 'center', bg: v('bg') },
    frame({ flex: 'row', gap: 14, items: 'center', w: 260 },
      icon('mdi:hexagon-multiple-outline', { size: 14 }),
      frame({ flex: 'row', gap: 10, items: 'center' }, ws.map((n, i) => text(n, { size: F.body, weight: i === 0 ? 'bold' : undefined, color: i === 0 ? 'fg' : 'text-2' })))),
    frame({ grow: 1, flex: 'row', justify: 'center' }, text(clock, { size: F.body, weight: 'bold' })),
    frame({ flex: 'row', gap: 2, items: 'center', w: 260, justify: 'end' },
      frame({ flex: 'col', items: 'center' }, harkBarWidget(hark), underline ? rect({ name: 'open-underline', w: 18, h: 2, bg: v('accent'), position: 'absolute', x: 4, y: 24 }) : ''),
      ...['mdi:bluetooth', 'mdi:wifi', 'mdi:volume-high', 'mdi:battery-80'].map((n) => frame({ w: 27, h: BAR_H, items: 'center', justify: 'center', flex: 'row' }, icon(n, { size: 13 })))))
}

export function terminal(lines, { title } = {}) {
  return frame({ name: 'terminal', flex: 'col', gap: 4, p: 14, w: 'fill', h: 'fill', bg: v('bg') },
    lines.map(([s, c = 'fg', weight]) => text(s, { size: F.body, color: c, weight })))
}

export function docWindow({ heading = 'Release notes — Notion', bars = 9 } = {}) {
  return frame({ name: 'browser', flex: 'col', gap: 10, p: 18, w: 'fill', h: 'fill', bg: v('lighter-bg') },
    text(heading, { size: F.title, weight: 'bold' }),
    ...Array.from({ length: bars }, (_, i) => rect({ w: [420, 380, 440, 300, 410, 360, 220, 400, 330][i % 9], h: 8, bg: v('border-hover') })))
}

export function win({ x, y, w, h, active = false, child, name = 'window' }) {
  return frame({ name, position: 'absolute', x, y, w, h, stroke: active ? v('accent') : '#595959aa', strokeWidth: 2, p: 2, flex: 'col', overflow: 'hidden' }, child)
}

// Target outline (overlay layer): a static 2 px accent line in the gap, 2 px outside the window border.
// Shown only while recording; it stays on the target if focus (and the active border) moves.
export function outline({ x, y, w, h }) {
  return rect({ name: 'outline', x: x - 4, y: y - 4, w: w + 8, h: h + 8, stroke: v('accent'), strokeWidth: 2, strokeAlign: 'inside' })
}

// A 1280×800 desktop with two tiled windows; left = dictation target.
export function desktop({ name, w = 1280, h = 800, hark = 'recording', target = true, focus = 'left', pill = '', fullscreen = false }) {
  const top = fullscreen ? 0 : BAR_H
  const gap = 10
  const L = fullscreen ? { x: 0, y: 0, w, h } : { x: gap, y: top + gap, w: Math.round((w - gap * 3) / 2), h: h - top - gap * 2 }
  const R = { x: L.x + L.w + gap, y: L.y, w: w - (L.x + L.w + gap) - gap, h: L.h }
  const lines = [
    ['~/projects/hark  main', 'accent-text', 'bold'],
    ['❯ pi', 'fg'],
    [' '],
    ['  › refactor learn.py so the watch window', 'fg'],
    ['    closes when the focus moves away', 'fg'],
    [' '],
    ['  > █', 'fg', 'bold']
  ]
  return frame({ name, w, h, bg: v('dark-bg') },
    fullscreen ? '' : frame({ position: 'absolute', x: 0, y: 0 }, omarchyBar(w, { hark })),
    fullscreen
      ? win({ ...L, active: true, child: docWindow({ heading: 'Slides — fullscreen', bars: 14 }), name: 'window/fullscreen' })
      : [win({ ...L, active: focus === 'left', child: terminal(lines), name: 'window/ghostty (target)' }),
          win({ ...R, active: focus === 'right', child: docWindow(), name: 'window/chromium' })].join(''),
    target ? frame({ name: 'overlay hark-indicator (input region empty)', position: 'absolute', x: 0, y: 0, w, h },
      outline(fullscreen ? { x: 6, y: 6, w: w - 12, h: h - 12 } : L)) : '',
    pill ? frame({ name: 'pill-anchor (target window, bottom-centre, 24 up)', position: 'absolute', x: L.x, y: L.y + L.h - 24 - 28, w: L.w, flex: 'row', justify: 'center' }, pill) : '')
}
