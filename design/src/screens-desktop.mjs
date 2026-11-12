// Omarchy desktop mock: omarchy.bar (top, 26 px, opaque), Hyprland dwindle tiling with gaps_out 10 /
// gaps_in 5, border_size 2, active border = accent (theme template), inactive rgba(595959aa), rounding 0.
import { F, frame, text, icon, rect, v, harkMark, dot } from './lib.mjs'

export const BAR_H = 26

export function harkBarWidget(state = 'idle') {
  const color = { idle: 'fg', recording: 'live-ink', handsfree: 'live-ink', processing: 'accent-ink', watching: 'fg', error: 'fg', off: 'text-2', unreachable: 'fg' }[state]
  const badge = {
    handsfree: icon('mdi:lock', { size: 8, color: 'live-ink' }),
    processing: dot('accent-ink', 5),
    watching: icon('mdi:eye', { size: 8, color: 'accent-ink' }),
    error: dot('live-ink', 5),
    unreachable: dot('warn-ink', 5)
  }[state]
  return frame({ name: `hark-bar-widget/${state}`, w: 27, h: BAR_H, items: 'center', justify: 'center', flex: 'row' },
    frame({ w: 16, h: 16 }, harkMark(14, color)),
    badge ? frame({ name: 'badge', position: 'absolute', x: 18, y: 4, w: 8, h: 8 }, badge) : '')
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
  return frame({ name, position: 'absolute', x, y, w, h, stroke: active ? v('accent') : '#595959aa', strokeWidth: 2, flex: 'col', overflow: 'hidden' }, child)
}

// Edge glow around a window rectangle (absolute, drawn on the overlay layer). e = smoothed level 0..1.
export function glowLayers({ x, y, w, h, e = 0.6, color = 'accent', mode = 'live', sweep = false }) {
  const op = 0.3 + 0.5 * e
  const blur = Math.round(10 + 18 * e)
  const width = Math.round(8 + 18 * e)
  const L = []
  if (mode !== 'static') {
    L.push(rect({ name: `glow/halo e=${e}`, x: x - 2, y: y - 2, w: w + 4, h: h + 4, stroke: v(color), strokeWidth: width, strokeAlign: 'outside', blur, opacity: op }))
    L.push(rect({ name: 'glow/core', x: x - 1, y: y - 1, w: w + 2, h: h + 2, stroke: v(color), strokeWidth: 3, strokeAlign: 'outside', blur: 3, opacity: Math.min(1, op + 0.2) }))
  }
  L.push(rect({ name: 'glow/edge', x, y, w, h, stroke: v(color), strokeWidth: mode === 'static' ? 3 : 2, strokeAlign: 'inside' }))
  if (sweep) {
    // bright travelling segment along the top edge (transcribing / cleaning)
    L.push(rect({ name: 'glow/sweep', x: x + Math.round(w * 0.42), y: y - 3, w: 160, h: 6, bg: v(color), blur: 6 }))
    L.push(rect({ name: 'glow/sweep-core', x: x + Math.round(w * 0.42) + 20, y: y - 1, w: 120, h: 3, bg: v(color) }))
  }
  return L.join('')
}

// A 1280×800 desktop with two tiled windows; left = dictation target.
export function desktop({ name, w = 1280, h = 800, hark = 'recording', glow = { e: 0.6 }, focus = 'left', pill = '', termLines, extra = '', bar = true, fullscreen = false }) {
  const top = bar ? BAR_H : 0
  const gap = 10
  const L = fullscreen ? { x: 0, y: 0, w, h } : { x: gap, y: top + gap, w: Math.round((w - gap * 2 - gap) / 2), h: h - top - gap * 2 }
  const R = { x: L.x + L.w + gap, y: L.y, w: w - (L.x + L.w + gap) - gap, h: L.h }
  const lines = termLines || [
    ['~/projects/hark  main', 'accent-text', 'bold'],
    ['❯ pi', 'fg'],
    [' '],
    ['  pi · claude-sonnet · ~/projects/hark', 'text-2'],
    ['  ─────────────────────────────────────', 'text-2'],
    ['  › refactor learn.py so the watch window', 'fg'],
    ['    closes when the focus moves away', 'fg'],
    [' '],
    ['  ● I will change `LearnWatch.poll` to compare', 'text-2'],
    ['    the window id before reading the field…', 'text-2'],
    [' '],
    ['  > █', 'fg', 'bold']
  ]
  return frame({ name, w, h, bg: v('dark-bg') },
    bar ? frame({ position: 'absolute', x: 0, y: 0 }, omarchyBar(w, { hark })) : '',
    fullscreen
      ? win({ ...L, active: true, child: docWindow({ heading: 'Presentation — fullscreen', bars: 14 }), name: 'window/fullscreen' })
      : [win({ ...L, active: focus === 'left', child: terminal(lines), name: 'window/ghostty (target)' }),
          win({ ...R, active: focus === 'right', child: docWindow(), name: 'window/chromium' })].join(''),
    glow ? frame({ name: 'overlay hark-glow (layer: overlay, input region empty)', position: 'absolute', x: 0, y: 0, w, h },
      glowLayers({ ...(glow.screen ? { x: 3, y: 3, w: w - 6, h: h - 6 } : (glow.target === 'right' ? R : L)), ...glow })) : '',
    pill ? frame({ name: 'pill-anchor', position: 'absolute', x: 0, y: h - 24 - 40, w, flex: 'row', justify: 'center' }, pill) : '',
    extra)
}
