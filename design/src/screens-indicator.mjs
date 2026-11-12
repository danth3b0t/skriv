// Indicator pill (S1). Glyph + meter + at most three words. Click-through; recovery is the skriv-panel key.
import { F, frame, text, icon, rect, ellipse, v, boardTitle } from './lib.mjs'

export const LEVELS = {
  speaking: [0.3, 0.55, 0.85, 0.62, 0.95, 0.7, 0.45, 0.8],
  quiet: [0.12, 0.2, 0.15, 0.28, 0.18, 0.12, 0.22, 0.16]
}

// Meter: 8 bars × 2 px, gap 2, max 12 px, newest on the right. Silent bars sit at 2 px in text-2.
export function meter(levels, { h = 12, color = 'fg' } = {}) {
  return frame({ name: 'meter', flex: 'row', gap: 2, items: 'center', h },
    levels.map((l) => rect({ w: 2, h: Math.max(2, Math.round(l * h)), bg: v(l < 0.08 ? 'text-2' : color) })))
}

// Working: three 4 px dots breathing in turn (900 ms cycle). Static (middle dot lit) under reduced motion.
export function breathe(phase = 1) {
  return frame({ name: 'working', flex: 'row', gap: 4, items: 'center', h: 12 },
    [0, 1, 2].map((i) => ellipse({ w: 4, h: 4, bg: v('accent-ink'), opacity: i === phase ? 1 : 0.35 })))
}

const live = () => ellipse({ name: 'live', w: 6, h: 6, bg: v('live-ink') })
const g = (name, color, size = 12) => icon(name, { size, color })
const w = (s, color = 'fg') => text(s, { size: F.bodySmall, color })
const dim = (s) => text(s, { size: F.caption, color: 'text-2' })
const ok = () => g('mdi:check', 'ok-ink', 14)
// Recovery bind (skriv-panel): opens the quick panel on the last dictation. Appended to failure pills only;
// it is a key hint, not a word, so it may run past the three-word limit.
export const RECOVER_KEY = 'Super Alt D'
const key = () => dim(RECOVER_KEY)

// Every pill state. Words ≤ 3 (+ the recovery key on failures); "·" separates a state from its reason.
// The pill is drawn by the daemon's own indicator, which has the input level in-process.
const STATES = () => ({
  recording: [live(), meter(LEVELS.speaking)],
  long: [live(), meter(LEVELS.speaking), dim('1:12')],
  limit: [live(), meter(LEVELS.speaking), text('0:14', { size: F.caption, color: 'warn-ink' })],
  handsfree: [g('mdi:lock', 'live-ink'), meter(LEVELS.quiet)],
  override: [live(), meter(LEVELS.speaking), g('mdi:pin-outline', 'text-2', 11), dim('chat')],
  serverdown: [live(), meter(LEVELS.speaking), g('mdi:alert-outline', 'warn-ink'), w('Server down · saved')],
  working: [breathe()],
  sent: [ok()],
  fallback: [ok(), dim('via laptop')],
  uncleaned: [ok(), dim('cleanup skipped')],
  micdrop: [ok(), dim('mic dropped')],
  stopped: [ok(), w('Stopped at 2:00')],
  cancelled: [g('mdi:close', 'text-2'), w('Cancelled', 'text-2')],
  copied: [g('mdi:content-copy', 'warn-ink'), w('Copied · focus moved')],
  pastefailed: [g('mdi:content-copy', 'warn-ink'), w('Paste failed · copied'), key()],
  failed: [g('mdi:alert-circle-outline', 'live-ink', 13), w('Failed'), key()],
  miclost: [g('mdi:microphone-off', 'live-ink'), w('Mic lost'), key()],
  paused: [g('mdi:pause', 'text-2'), w('Paused')]
})

// Pill: 28 px, padding 12, gap 8, [popups] background, 1 px border-hover hairline, radius Style.cornerRadius.
export function pill(state) {
  return frame({ name: `pill/${state}`, flex: 'row', h: 28, px: 12, gap: 8, items: 'center', bg: v('bg'), stroke: v('border-hover'), strokeWidth: 1 }, STATES()[state])
}

const GROUPS = [
  ['Recording', [['hold', 'recording'], ['after 60 s', 'long'], ['near limit', 'limit'], ['hands-free', 'handsfree'], ['pinned profile', 'override'], ['server down', 'serverdown']]],
  ['Needs you', [['focus moved', 'copied'], ['paste failed', 'pastefailed'], ['failed', 'failed'], ['mic lost', 'miclost'], ['paused', 'paused']]],
  ['After', [['working', 'working'], ['sent', 'sent'], ['fallback', 'fallback'], ['cleanup skipped', 'uncleaned'], ['mic dropped out', 'micdrop'], ['length limit', 'stopped'], ['cancelled', 'cancelled']]]
]

export function pillBoard(theme) {
  return frame({ name: `export/indicator-states-${theme}`, flex: 'col', gap: 40, p: 48, w: 1120, bg: v('dark-bg') },
    boardTitle('Indicator'),
    frame({ grid: true, columns: '1fr 1fr', gap: 48, w: 'fill' },
      GROUPS.map(([title, rows]) => frame({ flex: 'col', gap: 16, w: 'fill' },
        text(title, { size: F.bodySmall, color: 'text-2' }),
        frame({ grid: true, columns: '140px 1fr', gap: 14, w: 'fill', items: 'center' },
          rows.flatMap(([label, s]) => [frame({ h: 28, flex: 'row', items: 'center' }, text(label, { size: F.bodySmall, color: 'text-2' })), frame({ flex: 'row' }, pill(s))]))))))
}
