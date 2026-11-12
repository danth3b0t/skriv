// System boards: cover, the visual system (colour roles, type, space, motion), theme matrix.
import { F, MODES, frame, text, rect, v, harkMark, spacer, boardTitle } from './lib.mjs'
import { pill } from './screens-indicator.mjs'

export function cover(theme) {
  const principles = [
    'Shape and motion first; words only to act',
    'One accent: the cursor. Red: live or failed',
    'Type and space, not boxes',
    'Quiet until something needs you',
    'Every colour and size from the Omarchy theme'
  ]
  return frame({ name: `export/cover-${theme}`, flex: 'col', gap: 48, p: 64, w: 1200, bg: v('bg') },
    frame({ flex: 'row', gap: 14, items: 'center' }, harkMark(24), text('hark', { size: F.display, weight: 'bold' })),
    frame({ flex: 'col', gap: 10 }, principles.map((p) => text(p, { size: F.body, color: 'text-2' }))),
    frame({ flex: 'row', gap: 16, items: 'center' }, pill('recording'), pill('working'), pill('sent'), pill('copied'), pill('failed')))
}

export function systemBoard(theme) {
  const roles = [
    ['bg', 'surface'], ['fg', 'text'], ['text-2', 'secondary'], ['accent', 'cursor · panel edge'],
    ['live-ink', 'live · failed'], ['ok-ink', 'done'], ['warn-ink', 'attention'], ['fill-hover', 'cursor row']
  ]
  const swatch = ([name, role]) => frame({ name: `token/${name}`, flex: 'row', gap: 12, items: 'center' },
    rect({ w: 24, h: 24, bg: v(name), stroke: v('border-hover'), strokeWidth: 1 }),
    frame({ flex: 'col', gap: 2 }, text(name, { size: F.body }), text(role, { size: F.bodySmall, color: 'text-2' })))
  const type = [['body 12', F.body, 'fg', 'refactor learn.py'], ['bodySmall 11', F.bodySmall, 'text-2', 'Ghostty · 2m'], ['caption 10', F.caption, 'text-2', 'C copy   P paste']]
  const space = [4, 8, 12, 18]
  const motion = [['voice', 'meter follows the level'], ['working', 'breathe · 900 ms'], ['outcome', 'one fade · 120 / 400 ms'], ['panel', 'grow · 180 / 120 ms'], ['reduced', 'nothing moves']]
  const col = (title, ...children) => frame({ flex: 'col', gap: 16, w: 'fill' }, text(title, { size: F.bodySmall, color: 'text-2' }), ...children)
  return frame({ name: `export/system-${theme}`, flex: 'col', gap: 40, p: 48, w: 1100, bg: v('bg') },
    boardTitle(`System · ${theme}`),
    frame({ grid: true, columns: '1fr 1fr', gap: 48, w: 'fill' },
      col('Colour', frame({ grid: true, columns: '1fr 1fr', gap: 16, w: 'fill' }, roles.map(swatch))),
      col('Type',
        ...type.map(([n, s, c, sample]) => frame({ flex: 'row', gap: 16, items: 'center' }, frame({ w: 110 }, text(n, { size: F.caption, color: 'text-2' })), text(sample, { size: s, color: c }))),
        text('Space', { size: F.bodySmall, color: 'text-2' }),
        frame({ flex: 'row', gap: 24, items: 'end' }, space.map((s) => frame({ flex: 'col', gap: 6, items: 'center' }, rect({ w: s, h: s, bg: v('accent') }), text(String(s), { size: F.caption, color: 'text-2' }))))),
      col('Motion', ...motion.map(([a, b]) => frame({ flex: 'row', gap: 16 }, frame({ w: 110 }, text(a, { size: F.body })), text(b, { size: F.body, color: 'text-2' })))),
      col('Controls', frame({ flex: 'row', gap: 24, items: 'center' }, text('Pause', { size: F.body, color: 'text-2' }), frame({ flex: 'row', h: 28, px: 8, items: 'center', bg: v('fill-hover') }, text('Resume', { size: F.body }))),
        frame({ flex: 'row', w: 300, h: 28, px: 8, items: 'center', bg: v('fill-hover') }, text('Profile', { size: F.body }), spacer(), text('auto ›', { size: F.body, color: 'text-2' })))))
}

export function themeMatrix() {
  const col = (m) => frame({ name: `theme=${m}`, flex: 'col', gap: 14, p: 20, w: 'fill', bg: v('bg') },
    text(m, { size: F.bodySmall, color: 'text-2' }),
    pill('recording'), pill('copied'), pill('pastefailed'),
    frame({ flex: 'col', gap: 12, w: 'fill', p: 12, stroke: v('accent'), strokeWidth: 2 },
      frame({ flex: 'row', gap: 8, items: 'center', w: 'fill', h: 28 }, harkMark(14), text('hark', { size: F.body, weight: 'bold' }), spacer(), text('Pause', { size: F.body, color: 'text-2' })),
      frame({ flex: 'row', w: 'fill', h: 28, px: 8, items: 'center', bg: v('fill-hover') }, text('Profile', { size: F.body }), spacer(), text('auto', { size: F.body, color: 'text-2' }))))
  return frame({ name: 'export/theme-matrix', flex: 'col', gap: 24, p: 48, w: 1400, bg: '#202020' },
    text('Theme matrix', { size: F.title, color: '#a0a0a0' }),
    frame({ grid: true, columns: 'repeat(4, 1fr)', gap: 12, w: 'fill' }, MODES.map(col)))
}
