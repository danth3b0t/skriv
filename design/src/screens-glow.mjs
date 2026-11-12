import { F, frame, text, rect, v, boardTitle, annotation, chip } from './lib.mjs'
import { desktop, glowLayers, win, terminal, docWindow } from './screens-desktop.mjs'
import { pill, LEVELS } from './screens-indicator.mjs'

const recPill = (levels = LEVELS.speaking, profile = 'terminal') => pill({ state: 'recording', label: 'Listening', levels, elapsed: '0:04', profile })

export function glowHero(theme) {
  return frame({ name: `export/glow-recording-${theme}`, flex: 'col', gap: 16, p: 32, w: 1344, bg: v('dark-bg') },
    boardTitle(`Edge glow · recording · ${theme}`, 'The glow traces the window that will receive the text (Hyprland address captured at key-down), not the whole screen, so “where will this land?” is answered before you speak. Pill = words; glow = place; bar icon = mic live.'),
    desktop({ name: 'desktop', hark: 'recording', glow: { e: 0.62 }, pill: recPill() }))
}

function crop(label, sub, inner, w = 300, h = 200) {
  return frame({ name: `crop/${label}`, flex: 'col', gap: 8 },
    frame({ w, h, bg: v('dark-bg'), overflow: 'hidden' }, inner),
    text(label, { size: F.bodySmall, weight: 'bold' }),
    text(sub, { size: F.caption, color: 'text-2', w, lineHeight: 14 }))
}

function cornerWindow(glowOpts, { child } = {}) {
  // a window whose top-left corner sits inside the crop
  const W = { x: 40, y: 40, w: 420, h: 300 }
  return [win({ ...W, active: true, child: child || terminal([['~/projects/hark  main', 'accent-text', 'bold'], ['❯ git commit -m "fix: …', 'fg']]) }),
    glowLayers({ ...W, ...glowOpts })].join('')
}

export function glowLevels(theme) {
  const L = [[0.1, 'quiet room'], [0.45, 'normal speech'], [0.8, 'loud'], [1, 'peak / clipping']]
  return frame({ name: `export/glow-intensity-${theme}`, flex: 'col', gap: 20, p: 32, w: 1336, bg: v('dark-bg') },
    boardTitle('Intensity follows the voice', 'e = envelope of the RMS level (−60…0 dBFS → 0…1): attack 60 ms, release 280 ms. halo opacity = 0.30 + 0.50·e, blur = 10 + 18·e px, width = 8 + 18·e px; the 2 px edge never changes. Brightness may not swing more than 0.25 within 333 ms (≤ 3 flashes/s, WCAG 2.3.1).'),
    frame({ flex: 'row', gap: 24 }, L.map(([e, label]) => crop(`e = ${e}`, `${label} · opacity ${(0.3 + 0.5 * e).toFixed(2)} · blur ${Math.round(10 + 18 * e)} · width ${Math.round(8 + 18 * e)}`, cornerWindow({ e })))))
}

export function glowStates(theme) {
  const items = [
    ['hands-free, silent', 'floor e ≥ 0.35 so a locked recording never looks off; same accent', cornerWindow({ e: 0.35 })],
    ['transcribing / cleaning', 'halo drops to e = 0.2; a 160 px bright segment travels the perimeter once per 1.2 s (linear, the only linear motion)', cornerWindow({ e: 0.2, sweep: true })],
    ['pasted', 'ok-ink: in 120 ms, out 400 ms (ease-out), then the overlay unmaps', cornerWindow({ e: 0.5, color: 'ok-ink' })],
    ['copied · focus moved', 'warn-ink stays on the ORIGINAL target 1.2 s: the text did not go there; pill says Super+V', cornerWindow({ e: 0.45, color: 'warn-ink' })],
    ['error', 'live-ink, one fade-in (120 ms), hold 800 ms, fade 400 ms — never blinks', cornerWindow({ e: 0.45, color: 'live-ink' })],
    ['reduced motion', 'static 3 px accent edge, no halo, no pulse, no sweep; states change by colour + pill words only', cornerWindow({ mode: 'static' })]
  ]
  return frame({ name: `export/glow-states-${theme}`, flex: 'col', gap: 20, p: 32, w: 1012, bg: v('dark-bg') },
    boardTitle('Glow states', 'Colours come from the theme: accent (= Hyprland active border), and ok/warn/live inks solved to ≥ 3:1 against the background. The glow is never the only signal: the pill and the bar icon carry the same state in words and shape.'),
    frame({ grid: true, columns: '300px 300px 300px', gap: 24 }, items.map(([a, b, c]) => crop(a, b, c))))
}

export function glowFocusMoved(theme) {
  return frame({ name: `export/glow-focus-moved-${theme}`, flex: 'col', gap: 16, p: 32, w: 1344, bg: v('dark-bg') },
    boardTitle('Focus moved during dictation', 'You dictated into Ghostty, then clicked Chromium before key-up. hark never pastes into a window you did not dictate to: the glow stays on Ghostty in warn colour, the text goes to the clipboard.'),
    desktop({ name: 'desktop', hark: 'idle', focus: 'right', glow: { e: 0.45, color: 'warn-ink' }, pill: pill({ state: 'copied', label: 'Copied · focus moved', keys: ['Super+V'], keysLabel: 'to paste' }) }))
}

export function glowReduced(theme) {
  return frame({ name: `export/glow-reduced-motion-${theme}`, flex: 'col', gap: 16, p: 32, w: 1344, bg: v('dark-bg') },
    boardTitle('Reduced motion', 'Active when Hyprland animations are off (`omarchy toggle animations` → Style.reduceMotion) or indicator.motion = "reduce". Static 3 px edge, the level meter steps without easing, no sweep, no fades (0 ms).'),
    desktop({ name: 'desktop', hark: 'recording', glow: { mode: 'static' }, pill: recPill(LEVELS.quiet) }))
}

export function glowFullscreen(theme) {
  return frame({ name: `export/glow-screen-fallback-${theme}`, flex: 'col', gap: 16, p: 32, w: 1344, bg: v('dark-bg') },
    boardTitle('Fallback: screen edge', 'Fullscreen window, unknown geometry (XWayland/unmapped), or target on a hidden special workspace: glow the target monitor’s edges instead. Same envelope, inset 3 px.'),
    desktop({ name: 'desktop', bar: false, fullscreen: true, glow: { e: 0.55, screen: true }, pill: recPill(LEVELS.speaking, 'prose') }))
}

export function glowTiming(theme) {
  const W = 1100
  const t0 = 160
  const sx = (s) => t0 + Math.round(s * 230) // px per second
  const lane = (label, blocks) => frame({ name: `lane/${label}`, w: W, h: 34 },
    text(label, { size: F.bodySmall, weight: 'bold' }),
    ...blocks.map(([a, b, l, c = 'fill-selected', tc = 'fg']) => frame({ position: 'absolute', x: sx(a), y: 2, w: Math.max(6, sx(b) - sx(a)), h: 26, bg: v(c), px: 6, items: 'center', flex: 'row', overflow: 'hidden' }, text(l, { size: F.caption, color: tc, maxLines: 1 }))))
  const ticks = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4]
  return frame({ name: `export/glow-timing-${theme}`, flex: 'col', gap: 10, p: 32, bg: v('dark-bg'), w: W + 64 },
    boardTitle('Timeline · hold-to-talk dictation (2.6 s of speech)', 'All durations go through Style.duration(ms) (0 when reduced motion). Entrances ease-out cubic-bezier(0.23,1,0.32,1); exits ≈ ⅔ of entrance; no ease-in, no bounce (Omarchy agents/skills/motion.md).'),
    frame({ name: 'axis', w: W, h: 20 }, ...ticks.map((s) => frame({ position: 'absolute', x: sx(s) - 6, y: 0 }, text(`${s}s`, { size: F.caption, color: 'text-2' })))),
    lane('key (Right Alt)', [[0, 2.6, 'held', 'accent-fill']]),
    lane('pill', [[0, 0.12, 'in', 'accent-fill'], [0.12, 2.6, 'Listening · level 20 Hz · 0:0x after 1 s'], [2.6, 3.15, 'Transcribing', 'accent-fill'], [3.15, 3.35, 'Clean', 'accent-fill'], [3.35, 4.15, 'Pasted · 0.8 s', 'ok-fill'], [4.15, 4.27, 'out', 'fill-hover']]),
    lane('glow', [[0, 0.18, 'in', 'accent-fill'], [0.18, 2.6, 'envelope e(t): attack 60 / release 280'], [2.6, 3.35, 'sweep 1.2 s/loop', 'accent-fill'], [3.35, 3.47, 'ok', 'ok-fill'], [3.47, 3.87, 'fade 400', 'fill-hover']]),
    lane('bar icon', [[0, 2.6, 'bar.active (red)', 'live-fill'], [2.6, 3.35, 'accent dot', 'accent-fill'], [3.35, 4.3, 'idle', 'fill-hover']]),
    lane('sound', [[0, 0.15, 'start', 'fill-selected'], [3.35, 3.5, 'done', 'fill-selected']]),
    lane('a11y', [[0, 0.75, 'announce “Listening”'], [3.35, 4.1, 'announce “Pasted”']]),
    text('pill in 120 · pill out 120 · glow in 180 · ok flash in 120 · glow out 400 · linger: pasted 0.8 s, copied 2.5 s, cancelled 1 s, error 4 s', { size: F.caption, color: 'text-2' }),
    annotation('Hands-free: identical, except the key lane is two taps (Alt+Space) and the glow keeps a floor of e = 0.35. Cancel (Esc / ctl cancel): pill “Cancelled” 1 s, glow out 160 ms, “cancel” cue.', { w: W }))
}
