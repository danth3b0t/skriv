import { F, frame, text, icon, rect, ellipse, v, chip, keys, levelBars, annotation, boardTitle, spacer, card } from './lib.mjs'

export const LEVELS = {
  speaking: [0.05, 0.12, 0.3, 0.55, 0.8, 0.62, 0.9, 0.7, 0.42, 0.66, 0.85, 0.5],
  quiet: [0.04, 0.05, 0.1, 0.16, 0.12, 0.08, 0.14, 0.2, 0.12, 0.09, 0.15, 0.1],
  silent: Array(12).fill(0.02),
  peak: [0.6, 0.8, 1, 0.95, 1, 0.9, 1, 0.85, 1, 0.97, 1, 0.9]
}

// The pill. Omarchy OSD anatomy: [popups] background @0.97, 2px popups border (accent), Style.cornerRadius,
// content row padded 12/8; glyph slot 16; label body bold; detail caption text-2.
export function pill(st) {
  const glyphs = {
    recording: frame({ name: 'glyph', w: 16, h: 16, items: 'center', justify: 'center' },
      ellipse({ name: 'halo', x: 0, y: 0, w: 16, h: 16, bg: v('live-fill') }),
      ellipse({ name: 'live-dot', x: 4, y: 4, w: 8, h: 8, bg: v('live-ink') })),
    handsfree: icon('mdi:lock', { size: 15, color: 'live-ink' }),
    transcribing: icon('mdi:loading', { size: 16, color: 'accent-ink' }),
    cleaning: icon('mdi:auto-fix', { size: 16, color: 'accent-ink' }),
    pasted: icon('mdi:check-bold', { size: 16, color: 'ok-ink' }),
    copied: icon('mdi:clipboard-text-outline', { size: 16, color: 'warn-ink' }),
    error: icon('mdi:alert-circle-outline', { size: 16, color: 'live-ink' }),
    cancelled: icon('mdi:close', { size: 16, color: 'text-2' }),
    warming: icon('mdi:timer-sand', { size: 16, color: 'text-2' }),
    nosignal: icon('mdi:microphone-off', { size: 16, color: 'warn-ink' })
  }
  const working = st.state === 'transcribing' || st.state === 'cleaning'
  const border = st.state === 'error' ? 'live-ink' : 'accent'
  const row = frame({ name: 'content', flex: 'row', gap: 10, px: 12, py: 8, items: 'center' },
    glyphs[st.state],
    text(st.label, { size: F.body, weight: 'bold' }),
    st.levels ? levelBars(st.levels) : '',
    st.elapsed ? text(st.elapsed, { size: F.bodySmall, color: 'text-2' }) : '',
    st.detail ? text(st.detail, { size: F.bodySmall, color: 'text-2' }) : '',
    st.warn ? frame({ flex: 'row', gap: 4, items: 'center' }, icon('mdi:alert-outline', { size: 12, color: 'warn-ink' }), text(st.warn, { size: F.bodySmall })) : '',
    st.keys ? frame({ flex: 'row', gap: 5, items: 'center' }, keys(...st.keys), st.keysLabel ? text(st.keysLabel, { size: F.bodySmall, color: 'text-2' }) : '') : '',
    st.profile ? chip(st.profile, st.override ? { tone: 'accent', iconName: 'mdi:pin' } : {}) : '')
  return frame({ name: `pill/${st.state}`, flex: 'col', bg: v('bg'), stroke: v(border), strokeWidth: 2 },
    row,
    working ? frame({ name: 'progress', w: 'fill', h: 2, bg: v('fill-selected') },
      rect({ name: 'sweep', x: 40, y: 0, w: 70, h: 2, bg: v('accent-ink') })) : '')
}

export const PILL_STATES = [
  { id: 'hidden (idle)', when: 'daemon idle; no layer surface is mapped', st: null },
  { id: 'recording · hold', when: 'key down (ctl start). Level = rolling RMS, 20 Hz; elapsed after 1 s', st: { state: 'recording', label: 'Listening', levels: LEVELS.speaking, elapsed: '0:03', profile: 'terminal' } },
  { id: 'recording · hands-free', when: 'ctl toggle (Alt+Space) or tap-to-lock; shows how to stop', st: { state: 'handsfree', label: 'Hands-free', levels: LEVELS.quiet, elapsed: '0:42', keys: ['Alt+Space'], keysLabel: 'stop', profile: 'prose' } },
  { id: 'recording · no input', when: '> 1.5 s below -55 dBFS while recording (proposed mic-health hint)', st: { state: 'nosignal', label: 'Listening', levels: LEVELS.silent, warn: 'No input from “Built-in Mic”', profile: 'prose' } },
  { id: 'transcribing', when: 'key up → FinalResult. Indeterminate sweep, never a fake %', st: { state: 'transcribing', label: 'Transcribing', detail: '4.1 s audio', profile: 'terminal' } },
  { id: 'cleaning', when: 'cleanup request sent (profile opts in)', st: { state: 'cleaning', label: 'Cleaning up', detail: 'prose · ≤ 1 s', profile: 'prose' } },
  { id: 'pasted', when: 'status pasted; linger 0.8 s then hide', st: { state: 'pasted', label: 'Pasted', detail: 'Ghostty · 412 ms' } },
  { id: 'copied only', when: 'focus_changed / paste.method clipboard; linger 2.5 s', st: { state: 'copied', label: 'Copied · focus moved', keys: ['Super', 'V'], keysLabel: 'to paste' } },
  { id: 'error', when: 'error / paste_failed; linger 4 s; audio kept for retry', st: { state: 'error', label: 'Transcription failed', detail: 'server timeout · hark retry' } },
  { id: 'cancelled', when: 'Esc / ctl cancel; also no_speech, too_short, empty; linger 1 s', st: { state: 'cancelled', label: 'Cancelled', detail: 'nothing pasted' } },
  { id: 'profile override badge', when: 'ctl start --profile chat (proposed). Pin glyph + accent chip', st: { state: 'recording', label: 'Listening', levels: LEVELS.speaking, elapsed: '0:02', profile: 'chat', override: true } },
  { id: 'warming (proposed)', when: 'server reports MODEL_STATE_COLD at key-down; audio is still captured', st: { state: 'warming', label: 'Loading model', detail: 'recording continues', profile: 'prose' } }
]

export function pillBoard(themeName) {
  const cells = PILL_STATES.map((p) => frame({ name: `state/${p.id}`, flex: 'col', gap: 8, w: 'fill' },
    frame({ flex: 'row', gap: 8, items: 'center', w: 'fill' }, text(p.id, { size: F.bodySmall, weight: 'bold' })),
    text(p.when, { size: F.caption, color: 'text-2', w: 'fill', lineHeight: 14 }),
    p.st ? pill(p.st) : frame({ name: 'pill/hidden', flex: 'row', px: 12, py: 9, gap: 8, items: 'center', stroke: v('border-normal'), strokeWidth: 1, strokeDash: [4, 4] }, text('no surface', { size: F.bodySmall, color: 'text-2' }))))
  return frame({ name: `export/indicator-states-${themeName}`, flex: 'col', gap: 24, p: 32, w: 1240, bg: v('dark-bg') },
    boardTitle(`Recording indicator · all states · ${themeName}`, 'Layer surface `hark-indicator` (overlay, no keyboard, empty input region). Anchored bottom-centre, margin 24 under the Omarchy OSD slot. Border = [popups] border (accent); error borrows polkit `border-error`. Every state = glyph + words, never colour alone.'),
    frame({ name: 'grid', grid: true, columns: '1fr 1fr', gap: 28, w: 'fill' }, cells))
}

// Anatomy with measurements
export function pillAnatomy(themeName) {
  const p = pill({ state: 'recording', label: 'Listening', levels: LEVELS.speaking, elapsed: '0:03', profile: 'terminal' })
  const rows = [
    ['glyph slot', '16×16. recording: 8 px dot live-ink in a 16 px live-fill halo whose scale follows level (1.0–1.35)'],
    ['label', 'Style.font.body (12), bold, fg. Fixed words per state; read by Orca via Accessible.name'],
    ['level', '12 bars × 3 px, gap 2, max 16 px; newest right; < 8 % drawn as border-hover floor'],
    ['elapsed', 'bodySmall, text-2, m:ss; appears after 1 s so short presses stay quiet'],
    ['profile chip', 'caption bold upper, 1 px border-control; override = accent-fill + accent-ink + pin glyph'],
    ['card', '[popups] background (OSD uses α 0.97), 2 px [popups] border, radius Style.cornerRadius (0; 6 with corners-rounded), padding 12 × 8, gap 10; height 36 at base-size 12'],
    ['placement', 'OverlayWindow on the focused output, bottom-centre, margin Style.space(24) (the OSD card sits at 67); `indicator.position = top` mirrors to the top under the bar']
  ]
  return frame({ name: `export/indicator-anatomy-${themeName}`, flex: 'col', gap: 20, p: 32, w: 980, bg: v('dark-bg') },
    boardTitle('Pill anatomy', 'Measurements at Omarchy defaults ([font] base-size 12, [spacing] scale 1). All sizes go through Style.space()/Style.font so `omarchy display text size` scales the pill.'),
    frame({ flex: 'row', gap: 32, items: 'start', w: 'fill' },
      frame({ flex: 'col', gap: 10, items: 'start' },
        p,
        frame({ name: 'rounded-variant', flex: 'col', gap: 6, pt: 12 },
          text('corners-rounded toggle (rounding 6)', { size: F.caption, color: 'text-2' }),
          frame({ flex: 'row', gap: 10, px: 12, py: 8, items: 'center', bg: v('bg'), stroke: v('accent'), strokeWidth: 2, rounded: 6 },
            ellipse({ w: 8, h: 8, bg: v('live-ink') }), text('Listening', { size: F.body, weight: 'bold' }), levelBars(LEVELS.speaking), text('0:03', { size: F.bodySmall, color: 'text-2' })))),
      frame({ name: 'spec', flex: 'col', gap: 10, w: 400 },
        rows.map(([a, b]) => frame({ flex: 'col', gap: 2, w: 400 }, text(a, { size: F.bodySmall, weight: 'bold' }), text(b, { size: F.bodySmall, color: 'text-2', w: 400, lineHeight: 15 }))))))
}
