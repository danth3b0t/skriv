// History (S5): the recovery and accuracy surface. Search · list · detail (final text, heard → final).
// Opened from the panel or right-click on the bar glyph. Card = Omarchy popup chrome; no nav, no footer.
import { F, frame, text, icon, v, spacer, boardTitle } from './lib.mjs'

const dim = (s, size = F.bodySmall) => text(s, { size, color: 'text-2' })
const hint = (s) => text(s, { size: F.caption, color: 'text-2' })

function top() {
  return frame({ name: 'top', flex: 'row', w: 'fill', gap: 24, items: 'center' },
    text('History', { size: F.body, weight: 'bold' }),
    spacer(),
    frame({ name: 'search', flex: 'row', w: 260, h: 28, px: 8, gap: 8, items: 'center', bg: v('fill-normal') },
      icon('mdi:magnify', { size: 13, color: 'text-2' }), dim('search'), spacer(), hint('/')))
}

const ROWS = [
  ['ok', '14:31', 'Ghostty', 'refactor learn.py so the watch window closes when the focus moves away'],
  ['failed', '14:05', 'Ghostty', null],
  ['copied', '13:58', 'Slack', 'Carrie, the kubectl rollout finished, can you check staging?'],
  ['ok', '13:40', 'Chromium', 'The Hyprland paste now re-checks the window inside the compositor.']
]
const GLYPH = { ok: ['mdi:check', 'ok-ink'], failed: ['mdi:alert-circle-outline', 'live-ink'], copied: ['mdi:content-copy', 'warn-ink'] }

function list(selected) {
  return frame({ name: 'list', flex: 'col', w: 340, gap: 4 },
    frame({ px: 8, pb: 6 }, dim('Today')),
    ROWS.map(([kind, t, app, preview], i) => frame({ name: `row/${t}`, flex: 'row', w: 'fill', gap: 10, px: 8, py: 8, items: 'start', bg: i === selected ? v('fill-hover') : undefined },
      frame({ pt: 2 }, icon(GLYPH[kind][0], { size: 13, color: GLYPH[kind][1] })),
      frame({ flex: 'col', gap: 4, grow: 1 },
        dim(`${t} · ${app}`),
        preview ? text(preview, { size: F.body, w: 'fill', maxLines: 2, lineHeight: 17 }) : text('Failed · timeout', { size: F.body })))))
}

// heard → final, word by word: removed = strikethrough text-2, added = accent-text.
const DIFF = [['refactor'], ['learn', '-'], ['dot', '-'], ['pie', '-'], ['learn.py', '+'], ['so'], ['the'], ['um', '-'], ['watch'], ['window'], ['closes'], ['when'], ['the'], ['focus'], ['moves'], ['away']]
function diff() {
  return frame({ name: 'heard-final', flex: 'row', wrap: true, gap: 7, rowGap: 4, w: 'fill' },
    DIFF.map(([w, op]) => text(w, { size: F.body, lineHeight: 17, color: op === '-' ? 'text-2' : op === '+' ? 'accent-text' : 'fg', decoration: op === '-' ? 'strikethrough' : undefined })))
}

function detailPasted() {
  return frame({ name: 'detail', flex: 'col', gap: 24, grow: 1 },
    frame({ flex: 'col', gap: 8, w: 'fill' },
      text('refactor learn.py so the watch window closes when the focus moves away', { size: F.body, w: 'fill', lineHeight: 18 }),
      dim('14:31 · Ghostty · terminal · 412 ms')),
    hint('C copy   P paste   R retry   Del delete'),
    frame({ flex: 'col', gap: 8, w: 'fill' }, frame({ flex: 'row', w: 'fill' }, dim('Heard'), spacer(), hint('H copy heard')), diff()))
}

function detailFailed() {
  return frame({ name: 'detail', flex: 'col', gap: 24, grow: 1 },
    frame({ flex: 'col', gap: 8, w: 'fill' },
      frame({ flex: 'row', gap: 8, items: 'center' }, icon('mdi:alert-circle-outline', { size: 13, color: 'live-ink' }), text('Failed · server timeout', { size: F.body })),
      dim('14:05 · Ghostty · 4.1 s audio')),
    hint('R retry   Del delete'))
}

function windowCard(children) {
  return frame({ name: 'history-window', flex: 'col', w: 900, h: 480, p: 24, gap: 24, bg: v('bg'), stroke: v('accent'), strokeWidth: 2 }, children)
}

function board(name, title, card) {
  return frame({ name, flex: 'col', gap: 32, p: 48, bg: v('dark-bg') }, boardTitle(title), card)
}

export function historyPasted(theme) {
  return board(`export/history-${theme}`, 'History', windowCard([top(), frame({ flex: 'row', w: 'fill', gap: 32, items: 'start' }, list(0), detailPasted())]))
}
export function historyFailed(theme) {
  return board(`export/history-failed-${theme}`, 'History · failed', windowCard([top(), frame({ flex: 'row', w: 'fill', gap: 32, items: 'start' }, list(1), detailFailed())]))
}
