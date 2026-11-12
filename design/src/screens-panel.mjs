// Bar glyph (S2) and quick panel (S3): the only control surface. One page + the profile list.
// Blocks: header (server · rtt, Pause/Resume) · problem line (only when present) · last dictation · Profile ·
// History / Config / Doctor. Labels and values only; one verb per problem.
import { F, frame, text, icon, v, skrivMark, spacer, boardTitle } from './lib.mjs'
import { omarchyBar, skrivBarWidget, BAR_H } from './screens-bar.mjs'

const W = 300 // content width: Style.space(300)
const cur = (on) => (on ? { bg: v('fill-hover') } : {})
const dim = (s, size = F.bodySmall) => text(s, { size, color: 'text-2' })
const hint = (s) => text(s, { size: F.caption, color: 'text-2' })

// Pause is a labelled text control: "Pause" while running, "Resume" (fg) while paused. Enter toggles.
function pauseControl(paused, cursor) {
  return frame({ name: paused ? 'control/resume' : 'control/pause', flex: 'row', h: 28, px: 8, items: 'center', ...cur(cursor) },
    text(paused ? 'Resume' : 'Pause', { size: F.body, color: paused ? 'fg' : 'text-2' }))
}

function header({ status = 'desktop · 38 ms', paused = false, statusColor = 'text-2', cursor = false, running = true } = {}) {
  return frame({ name: 'header', flex: 'row', w: 'fill', gap: 10, items: 'center', h: 28 },
    frame({ opacity: running && !paused ? 1 : 0.4 }, skrivMark(14)),
    text('Skriv', { size: F.body, weight: 'bold' }),
    text(status, { size: F.bodySmall, color: statusColor }), spacer(),
    running ? pauseControl(paused, cursor) : '')
}

const PROBLEMS = {
  server: ['mdi:alert-outline', 'warn-ink', 'Server down', 'Doctor'],
  mic: ['mdi:microphone-off', 'live-ink', 'Mic lost', 'Restart'],
  config: ['mdi:refresh', 'warn-ink', 'Config changed', 'Reload'],
  restart: ['mdi:refresh', 'warn-ink', 'Restart needed', 'Restart'],
  off: ['mdi:power', 'text-2', 'Not running', 'Start']
}
function problem(kind, { cursor = true } = {}) {
  const [g, color, label, verb] = PROBLEMS[kind]
  return frame({ name: `problem/${kind}`, flex: 'row', w: 'fill', h: 28, px: 8, gap: 10, items: 'center', ...cur(cursor) },
    icon(g, { size: 13, color }), text(label, { size: F.body }), spacer(), hint(`↵ ${verb}`))
}

// Enter = copy (the default); P closes the panel, then pastes into the window focused before it opened.
const PREVIEW = 'refactor learn.py so the watch window closes when the focus moves away'
const LAST = {
  sent: { meta: 'Ghostty · 2m', keys: '↵ copy  P paste' },
  uncleaned: { meta: 'Ghostty · 2m · cleanup skipped', keys: '↵ copy  P paste' },
  stopped: { meta: 'Ghostty · stopped at 2:00', keys: '↵ copy  P paste' },
  micdrop: { meta: 'Ghostty · 2m · mic dropped', keys: '↵ copy  P paste' },
  pastefailed: { head: ['mdi:content-copy', 'warn-ink', 'Paste failed · in clipboard'], meta: 'Ghostty · now', keys: '↵ copy  P paste' },
  failed: { head: ['mdi:alert-circle-outline', 'live-ink', 'Failed · server timeout'], meta: 'Ghostty · now', keys: '↵ retry' }
}
function last(kind = 'sent', { cursor = true } = {}) {
  const L = LAST[kind]
  return frame({ name: `last/${kind}`, flex: 'col', w: 'fill', gap: 6, px: 8, py: 8, ...cur(cursor) },
    L.head ? frame({ flex: 'row', gap: 8, items: 'center' }, icon(L.head[0], { size: 13, color: L.head[1] }), text(L.head[2], { size: F.body })) : '',
    kind === 'failed' ? '' : text(PREVIEW, { size: F.body, w: 'fill', maxLines: 2, lineHeight: 17, color: L.head ? 'text-2' : 'fg' }),
    frame({ flex: 'row', w: 'fill', items: 'center' }, dim(L.meta), spacer(), cursor ? hint(L.keys) : ''))
}

const profileRow = () => frame({ name: 'row/Profile', flex: 'row', w: 'fill', h: 28, px: 8, gap: 8, items: 'center' },
  text('Profile', { size: F.body }), spacer(), dim('auto', F.body), dim('›', F.body))
const links = (items = ['History', 'Config', 'Doctor']) => frame({ name: 'links', flex: 'row', w: 'fill', gap: 24, px: 8 }, items.map((s) => dim(s, F.body)))

export function panelCard(children) {
  return frame({ name: 'KeyboardPanel', flex: 'col', w: W + 36, p: 18, gap: 18, bg: v('bg'), stroke: v('accent'), strokeWidth: 2 }, children)
}

function withBar(name, title, panel, { skriv = 'idle' } = {}) {
  const w = 720
  return frame({ name, flex: 'col', gap: 32, p: 48, bg: v('dark-bg') },
    boardTitle(title),
    frame({ name: 'composition', w, flex: 'col' },
      frame({ name: 'bar-crop', w, h: BAR_H, overflow: 'hidden' }, frame({ position: 'absolute', x: w - 1280, y: 0 }, omarchyBar(1280, { skriv, underline: true }))),
      frame({ flex: 'row', w, justify: 'end', pr: 96, pt: 5 }, panel)))
}

export function quickPanelIdle(theme) {
  return withBar(`export/quick-panel-${theme}`, 'Quick panel', panelCard([header(), last(), profileRow(), links()]))
}

export function quickPanelAttention(theme) {
  return withBar(`export/quick-panel-attention-${theme}`, 'Quick panel · needs you',
    panelCard([header({ status: 'desktop' }), problem('server'), last('failed', { cursor: false }), profileRow(), links()]), { skriv: 'attention' })
}

export function quickPanelOff(theme) {
  return withBar(`export/quick-panel-off-${theme}`, 'Quick panel · not running',
    panelCard([header({ status: '', running: false }), problem('off'), links(['Config', 'Doctor'])]), { skriv: 'off' })
}

export function quickPanelProfile(theme) {
  const opt = (label, { on = false, cursor = false, value } = {}) => frame({ name: `option/${label}`, flex: 'row', w: 'fill', h: 28, px: 8, gap: 10, items: 'center', ...cur(cursor) },
    frame({ w: 12, h: 12 }, on ? icon('mdi:check', { size: 12, color: 'accent-ink' }) : ''),
    text(label, { size: F.body, weight: on ? 'bold' : undefined }), spacer(), value ? dim(value, F.body) : '')
  return withBar(`export/quick-panel-profile-${theme}`, 'Quick panel · profile', panelCard([
    frame({ flex: 'row', w: 'fill', gap: 8, items: 'center', px: 8, h: 28 }, dim('‹', F.body), text('Profile', { size: F.body, weight: 'bold' })),
    frame({ flex: 'col', w: 'fill' }, opt('auto', { on: true, value: 'terminal' }), opt('terminal', { cursor: true }), opt('chat'), opt('prose'))]))
}

// Every non-default state the panel can show, by slot. Same components as the panel.
export function quickPanelStates(theme) {
  const slot = (title, ...items) => frame({ flex: 'col', gap: 14, w: W + 16 }, text(title, { size: F.bodySmall, color: 'text-2' }), ...items)
  const tag = (s, child) => frame({ flex: 'col', gap: 6, w: 'fill' }, hint(s), child)
  return frame({ name: `export/quick-panel-states-${theme}`, flex: 'col', gap: 32, p: 48, bg: v('dark-bg') },
    boardTitle('Quick panel · states'),
    frame({ flex: 'row', gap: 48, items: 'start', p: 18, bg: v('bg') },
      slot('Header',
        tag('ready', header()),
        tag('fallback', header({ status: 'laptop · fallback', statusColor: 'warn-ink' })),
        tag('paused', header({ status: 'paused', paused: true, cursor: true }))),
      slot('Problem line',
        tag('server down', problem('server', { cursor: false })),
        tag('mic lost', problem('mic', { cursor: false })),
        tag('config changed', problem('config', { cursor: false })),
        tag('restart needed', problem('restart', { cursor: false })),
        tag('not running', problem('off', { cursor: false }))),
      slot('Last dictation',
        tag('cleanup skipped', last('uncleaned', { cursor: false })),
        tag('length limit', last('stopped', { cursor: false })),
        tag('mic dropped out', last('micdrop', { cursor: false })),
        tag('paste failed', last('pastefailed')),
        tag('failed', last('failed')))))
}

export function barWidgetStates(theme) {
  const states = ['idle', 'recording', 'working', 'attention', 'paused', 'off']
  return frame({ name: `export/bar-widget-states-${theme}`, flex: 'col', gap: 32, p: 48, bg: v('dark-bg') },
    boardTitle('Bar glyph'),
    frame({ flex: 'row', gap: 32, items: 'start' }, states.map((s) => frame({ name: `state/${s}`, flex: 'col', gap: 12, items: 'center' },
      frame({ flex: 'row', h: BAR_H, px: 10, items: 'center', bg: v('bg') }, skrivBarWidget(s)), dim(s)))),
    frame({ flex: 'row', gap: 16, items: 'center' },
      frame({ name: 'tooltip', flex: 'row', px: 10, py: 6, bg: v('bg'), stroke: v('border-hover'), strokeWidth: 1 }, text('Skriv · ready · desktop 38 ms', { size: F.bodySmall })),
      dim('left panel · middle paste last · Super Alt D panel on last')))
}
