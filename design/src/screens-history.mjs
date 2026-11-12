// History: a section of hark.settings (also `omarchy-shell hark.settings section History`).
// Master list (search, filters, day groups) + detail pane (text, actions, playback, pipeline details).
import { F, frame, text, icon, rect, v, chip, keys, button, field, separator, spacer, dot, sectionHeader, segmented } from './lib.mjs'
import { settingsWindow, spans } from './screens-settings.mjs'

const ENTRIES = [
  { t: '14:31', app: 'Ghostty', prof: 'terminal', st: 'pasted', ms: 412, text: 'refactor learn.py so the watch window closes when the focus moves away' },
  { t: '14:22', app: 'Slack', prof: 'chat', st: 'copied', ms: 388, text: 'Carrie, the kubectl rollout finished, can you check staging?' },
  { t: '14:05', app: 'Ghostty', prof: 'terminal', st: 'error', ms: null, text: '' , err: 'server timeout · audio kept' },
  { t: '13:58', app: 'Chromium', prof: 'prose', st: 'pasted', ms: 455, text: 'The Hyprland paste now re-checks the window inside the compositor.' },
  { t: '13:40', app: 'Ghostty', prof: 'terminal', st: 'cancelled', ms: null, text: '', err: 'cancelled · nothing pasted' },
  { t: 'Yesterday', app: 'Obsidian', prof: 'prose', st: 'pasted', ms: 502, text: 'Decide whether the edge glow should follow the target window or the screen.' }
]

function statusGlyph(st) {
  return {
    pasted: ['mdi:check', 'ok-ink'], copied: ['mdi:clipboard-text-outline', 'warn-ink'], error: ['mdi:alert-circle-outline', 'live-ink'], cancelled: ['mdi:close', 'text-2']
  }[st]
}

function entryRow(e, { selected = false, cursor = false, highlight, theme } = {}) {
  const [g, c] = statusGlyph(e.st)
  const body = e.text
    ? (highlight ? spans(theme, e.text.split(new RegExp(`(${highlight})`, 'i')).filter(Boolean).map((part) => [part, part.toLowerCase() === highlight.toLowerCase() ? 'hit' : 'keep']), { size: F.bodySmall, lineHeight: 15 }) : text(e.text, { size: F.bodySmall, w: 'fill', maxLines: 2, lineHeight: 15 }))
    : text(e.err, { size: F.bodySmall, color: e.st === 'error' ? 'fg' : 'text-2', w: 'fill' })
  return frame({ name: `entry/${e.t}`, flex: 'row', w: 'fill', gap: 10, px: 10, py: 8, items: 'start', bg: selected ? v('fill-selected') : cursor ? v('fill-hover') : undefined, stroke: cursor ? v('border-hover') : undefined, strokeWidth: cursor ? 1 : undefined },
    frame({ pt: 1 }, icon(g, { size: 14, color: c })),
    frame({ flex: 'col', gap: 3, grow: 1 },
      frame({ flex: 'row', gap: 8, w: 'fill', items: 'center' }, text(e.t, { size: F.caption, weight: 'bold', color: 'text-2' }), text(`${e.app} · ${e.prof}`, { size: F.caption, color: 'text-2' }), spacer(), e.ms ? text(`${e.ms} ms`, { size: F.caption, color: 'text-2' }) : ''),
      body))
}

function list({ selected = 0, query = '', filter = 'All', entries = ENTRIES, theme }) {
  return frame({ name: 'history-list', flex: 'col', w: 300, h: 'fill', gap: 8 },
    field(query || 'Search text, app or profile', { w: 300, placeholder: !query, iconName: 'mdi:magnify', focused: !!query, suffix: query ? '3 matches' : '/' }),
    segmented(['All', 'Failed', 'Copied', 'Terminal'], filter),
    frame({ name: 'rows', flex: 'col', gap: 2, w: 'fill', overflow: 'hidden', grow: 1 },
      text('TODAY', { size: F.caption, weight: 'bold', color: 'text-2', track: 1.2 }),
      entries.map((e, i) => entryRow(e, { selected: i === selected, highlight: query || undefined, theme }))))
}

function playback({ failed = false } = {}) {
  const bars = Array.from({ length: 40 }, (_, i) => 0.15 + 0.85 * Math.abs(Math.sin(i * 0.7) * Math.cos(i * 0.23)))
  return frame({ name: 'playback', flex: 'row', w: 'fill', gap: 10, px: 10, py: 8, items: 'center', bg: v('fill-normal'), stroke: v('border-control'), strokeWidth: 1 },
    frame({ w: 26, h: 26, items: 'center', justify: 'center', flex: 'row', stroke: v('fg'), strokeWidth: 1 }, icon('mdi:play', { size: 16 })),
    frame({ name: 'waveform', flex: 'row', gap: 1, items: 'center', h: 26, w: 160 }, bars.map((b, i) => rect({ w: 3, h: Math.max(2, Math.round(b * 22)), bg: v(i < 14 ? 'fg' : 'border-hover') }))),
    text('0:01.4 / 0:04.1', { size: F.caption, color: 'text-2' }),
    text('1×', { size: F.caption, weight: 'bold' }))
}

function stageRow(label, value, note) {
  return frame({ flex: 'row', w: 'fill', gap: 10, items: 'start' },
    frame({ w: 92 }, text(label, { size: F.caption, weight: 'bold', color: 'text-2', upper: true, track: 1 })),
    frame({ flex: 'col', gap: 2, grow: 1 }, value, note ? text(note, { size: F.caption, color: 'text-2', w: 'fill' }) : ''))
}

function detailPasted() {
  return frame({ name: 'detail', flex: 'col', grow: 1, h: 'fill', gap: 12, pl: 16, overflow: 'hidden' },
    frame({ flex: 'row', w: 'fill', gap: 8, items: 'center' },
      icon('mdi:check', { size: 14, color: 'ok-ink' }), text('Pasted into Ghostty', { size: F.title, weight: 'bold' }), chip('terminal'), spacer(), text('14:31', { size: F.caption, color: 'text-2' })),
    frame({ name: 'final-text', w: 'fill', p: 12, bg: v('fill-normal'), stroke: v('border-control'), strokeWidth: 1 },
      text('refactor learn.py so the watch window closes when the focus moves away', { size: F.body, w: 'fill', lineHeight: 18 })),
    frame({ name: 'actions', flex: 'row', gap: 8, wrap: true, rowGap: 6, w: 'fill' },
      button('Copy', { iconName: 'mdi:content-copy', hint: 'C' }), button('Paste again', { iconName: 'mdi:clipboard-arrow-down-outline', hint: 'P' }), button('Retry', { iconName: 'mdi:replay', hint: 'R' }), button('Delete', { kind: 'ghost', iconName: 'mdi:delete-outline', hint: 'Del' })),
    playback(),
    sectionHeader('Pipeline'),
    stageRow('heard', text('refactor learn dot pie so the watch window closes when the focus moves away', { size: F.bodySmall, color: 'text-2', w: 'fill' }), 'raw ASR · 268 ms · 31 bias terms (repo 12, agent 9, window 4, learned 6)'),
    stageRow('canonical', frame({ flex: 'row', gap: 6, wrap: true }, text('learn dot pie →', { size: F.bodySmall, color: 'text-2' }), chip('learn.py', { tone: 'accent' })), 'spoken symbols (terminal) · gate kept 1 of 1 biased span'),
    stageRow('cleanup', text('applied · 0 edits', { size: F.bodySmall }), 'terminal profile · 104 ms'),
    stageRow('paste', text('hyprland in-compositor · landed', { size: F.bodySmall }), 'spacing: leading “ ” (caret after “> ”) · source tmux'))
}

function detailCopied() {
  return frame({ name: 'detail', flex: 'col', grow: 1, h: 'fill', gap: 12, pl: 16 },
    frame({ flex: 'row', w: 'fill', gap: 8, items: 'center' },
      icon('mdi:clipboard-text-outline', { size: 14, color: 'warn-ink' }), text('Copied · focus moved', { size: F.title, weight: 'bold' }), chip('chat'), spacer(), text('14:22', { size: F.caption, color: 'text-2' })),
    frame({ name: 'why', flex: 'row', gap: 8, w: 'fill', p: 10, bg: v('warn-fill'), stroke: v('warn-ink'), strokeWidth: 1 },
      text('You were in Chromium when the text was ready, not Slack. It went to the clipboard and was pasted nowhere.', { size: F.bodySmall, w: 'fill', lineHeight: 15 })),
    frame({ name: 'final-text', w: 'fill', p: 12, bg: v('fill-normal'), stroke: v('border-control'), strokeWidth: 1 },
      text('Carrie, the kubectl rollout finished, can you check staging?', { size: F.body, w: 'fill', lineHeight: 18 })),
    frame({ name: 'actions', flex: 'row', gap: 8, wrap: true, rowGap: 6, w: 'fill' },
      button('Paste now', { kind: 'primary', iconName: 'mdi:clipboard-arrow-down-outline', hint: 'P' }), button('Copy', { iconName: 'mdi:content-copy', hint: 'C' })),
    sectionHeader('Pipeline'),
    stageRow('canonical', frame({ flex: 'row', gap: 6, wrap: true }, text('cube control →', { size: F.bodySmall, color: 'text-2' }), chip('kubectl', { tone: 'accent' })), 'learned word (3×) · gate kept'),
    stageRow('paste', text('focus_changed · clipboard', { size: F.bodySmall }), 'target Slack 0x55d…; focused Chromium at paste time'))
}

function detailFailed() {
  return frame({ name: 'detail', flex: 'col', grow: 1, h: 'fill', gap: 12, pl: 16 },
    frame({ flex: 'row', w: 'fill', gap: 8, items: 'center' },
      icon('mdi:alert-circle-outline', { size: 14, color: 'live-ink' }), text('Failed · server timeout', { size: F.title, weight: 'bold' }), chip('terminal'), spacer(), text('14:05', { size: F.caption, color: 'text-2' })),
    frame({ name: 'failure', flex: 'col', gap: 6, w: 'fill', p: 12, bg: v('live-fill'), stroke: v('live-ink'), strokeWidth: 1 },
      text('No final text after 5000 ms from desktop.tailnet:50051.', { size: F.body, w: 'fill' }),
      text('The recording and its key-down context are kept, so a retry uses the same profile, words and target window.', { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 15 })),
    frame({ name: 'actions', flex: 'row', gap: 8, wrap: true, rowGap: 6, w: 'fill' },
      button('Retry now', { kind: 'primary', iconName: 'mdi:replay', hint: 'R' }), button('Retry and copy', { iconName: 'mdi:content-copy', hint: '⇧R' }), button('Delete', { kind: 'ghost', iconName: 'mdi:delete-outline' })),
    playback({ failed: true }),
    text('Retry pastes only if Ghostty is focused again; otherwise the text goes to the clipboard.', { size: F.caption, color: 'text-2', w: 'fill' }),
    sectionHeader('What happened'),
    stageRow('context', text('ok · 140 ms · 28 terms', { size: F.bodySmall }), ''),
    stageRow('audio', text('4.1 s streamed', { size: F.bodySmall }), ''),
    stageRow('final', text('timeout after 5000 ms', { size: F.bodySmall }), 'server.final_timeout_ms'),
    stageRow('fallback', text('none configured', { size: F.bodySmall }), 'add a fallback server in Recognition'))
}

export function historyPasted(theme) {
  return settingsWindow({ section: 'history', title: 'History', intro: null, footer: historyFooter(),
    body: [frame({ name: 'history', flex: 'row', w: 'fill', h: 480 }, list({ selected: 0 }), rect({ w: 1, h: 'fill', bg: v('border-hover') }), detailPasted())] })
}
export function historyFailed(theme) {
  return settingsWindow({ section: 'history', title: 'History', intro: null, footer: historyFooter(),
    body: [frame({ name: 'history', flex: 'row', w: 'fill', h: 480 }, list({ selected: 2, filter: 'All' }), rect({ w: 1, h: 'fill', bg: v('border-hover') }), detailFailed())] })
}
export function historySearch(theme) {
  const hits = [ENTRIES[1], { ...ENTRIES[0], t: 'Mon 09:12', text: 'why does kubectl apply hang on the rollout status check', ms: 431 }, { ...ENTRIES[3], t: 'Sun 17:40', text: 'ask Carrie about the kubectl context for staging', app: 'Slack', prof: 'chat', ms: 377 }]
  return settingsWindow({ section: 'history', title: 'History', intro: null, footer: historyFooter(), search: '',
    body: [frame({ name: 'history', flex: 'row', w: 'fill', h: 480 },
      list({ selected: 0, query: 'kubectl', entries: hits, theme }),
      rect({ w: 1, h: 'fill', bg: v('border-hover') }),
      detailCopied())] })
}

function historyFooter() {
  return [
    frame({ flex: 'row', gap: 5, items: 'center' }, keys('/'), text('search', { size: F.caption, color: 'text-2' })),
    frame({ flex: 'row', gap: 5, items: 'center' }, keys('C'), text('copy', { size: F.caption, color: 'text-2' })),
    frame({ flex: 'row', gap: 5, items: 'center' }, keys('P'), text('paste again', { size: F.caption, color: 'text-2' })),
    frame({ flex: 'row', gap: 5, items: 'center' }, keys('R'), text('retry', { size: F.caption, color: 'text-2' })),
    frame({ flex: 'row', gap: 5, items: 'center' }, keys('Space'), text('play', { size: F.caption, color: 'text-2' })),
    spacer(),
    text('kept 30 days · audio for the last 10', { size: F.caption, color: 'text-2' }),
    frame({ flex: 'row', gap: 5, items: 'center' }, keys('Esc'), text('close', { size: F.caption, color: 'text-2' }))
  ]
}
