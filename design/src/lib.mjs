// hark design source: OpenPencil design-JSX helpers that mirror Omarchy shell (qs.Commons / qs.Ui)
// geometry at the default scale ([font] base-size 12, [spacing] scale 1, Hyprland rounding 0).
// Colours are bound to the "hark · Omarchy theme" variable collection (one mode per Omarchy theme),
// so every frame can be switched between themes inside OpenPencil.
import { readFileSync } from 'node:fs'

export const TOKENS = JSON.parse(readFileSync(new URL('../tokens/omarchy-hark-tokens.json', import.meta.url)))
export const MODES = ['tokyo-night', 'catppuccin-latte', 'catppuccin', 'gruvbox', 'rose-pine', 'flexoki-light', 'matte-black', 'nord']

// variable name -> [token key in omarchy-hark-tokens.json, Omarchy source / formula]
export const VARS = {
  bg: ['bg', 'colors.toml background = [popups] background'],
  fg: ['fg', 'colors.toml foreground = [popups] text'],
  accent: ['accent', 'colors.toml accent = Hyprland active border: the target window and the cursor'],
  'dark-bg': ['dark_bg', 'colors.toml dark_background (canvas behind mocks)'],
  'lighter-bg': ['lighter_bg', 'colors.toml lighter_background (mock windows)'],
  'text-2': ['text_secondary', 'mix(fg, bg) solved to >= 4.5:1 on bg'],
  'fill-normal': ['fill_normal', 'fg @ [controls] normal-fill-alpha 0.04'],
  'fill-hover': ['fill_hover', 'fg @ [controls] hover-cursor-fill-alpha 0.08: the cursor row'],
  'fill-selected': ['fill_selected', 'fg @ [controls] selected-fill-alpha 0.18 (toggle on)'],
  'border-hover': ['border_hover', 'fg @ [controls] hover-cursor-border-alpha 0.25: pill hairline'],
  'border-control': ['border_control', 'mix(fg, border-normal) solved to >= 3:1 on bg (toggle off)'],
  'accent-ink': ['accent_ink', 'accent toward fg until >= 3:1 on bg'],
  'accent-text': ['accent_text', 'accent toward fg until >= 4.5:1 on bg'],
  'live-ink': ['live_ink', 'red toward fg until >= 3:1: mic live, failed'],
  'ok-ink': ['ok_ink', 'green toward fg until >= 3:1: done'],
  'warn-ink': ['warn_ink', 'yellow toward fg until >= 3:1: attention']
}

let VAR_IDS = {}
export function setVarIds(ids) { VAR_IDS = ids }
export function v(name) {
  const id = VAR_IDS[name]
  if (!id) throw new Error('unknown var ' + name)
  return `{designVar('${id}')}`
}

// --- Omarchy Style tokens (default scale) ---
export const FONT = 'JetBrains Mono' // fontconfig `monospace` alias on Omarchy (JetBrainsMono Nerd Font)
export const F = { caption: 10, bodySmall: 11, body: 12, subtitle: 13, title: 14, heading: 16, display: 24, displayLarge: 28, icon: 14, iconLarge: 18 }

const q = (s) => JSON.stringify(String(s))

function attrs(o) {
  return Object.entries(o)
    .filter(([, val]) => val !== undefined && val !== null && val !== false)
    .map(([k, val]) => {
      if (val === true) return k
      if (typeof val === 'number') return `${k}={${val}}`
      if (typeof val === 'string' && val.startsWith('{')) return `${k}=${val}`
      return `${k}=${q(val)}`
    })
    .join(' ')
}

// A Frame without auto-layout and without a height defaults to 100 px tall in OpenPencil; frames that are
// not explicit absolute containers (they always pass h) therefore hug their content as a column.
export function frame(props, ...children) {
  if (!props.flex && !props.grid && props.h === undefined) props = { flex: 'col', ...props }
  return `<Frame ${attrs(props)}>${children.flat(Infinity).filter(Boolean).join('')}</Frame>`
}
export function text(str, { size = F.body, color = 'fg', weight, upper, track, name, w, align, maxLines, lineHeight, opacity, italic, decoration } = {}) {
  const p = {
    name, size, font: FONT, weight, color: color.startsWith('#') ? color : v(color), w, textAlign: align,
    letterSpacing: track, maxLines, truncate: maxLines ? true : undefined, lineHeight, opacity, italic,
    textDecoration: decoration
  }
  if (upper) str = String(str).toUpperCase()
  return `<Text ${attrs(p)}>{${q(str)}}</Text>`
}
// Icons: Iconify `mdi:` = the Nerd Font `nf-md` glyphs Omarchy draws. Icon colours cannot take a
// variable in this OpenPencil build, so the icon is wrapped in a frame named `icon:<var>` and build.mjs
// binds every vector fill inside it to that variable after rendering.
export function icon(nameStr, { size = F.icon, color = 'fg' } = {}) {
  const hex = color.startsWith('#') ? color : TOKENS['tokyo-night'][VARS[color][0]]
  const inner = `<Icon ${attrs({ name: nameStr, size, color: hex })} />`
  return color.startsWith('#') ? inner : `<Frame ${attrs({ name: 'icon:' + color, w: size, h: size })}>${inner}</Frame>`
}
export function rect(props) { return `<Rectangle ${attrs(props)} />` }
export function ellipse(props) { return `<Ellipse ${attrs(props)} />` }
export function spacer() { return frame({ name: 'spacer', grow: 1, h: 1 }) }

// ---------------------------------------------------------------- atoms
export function dot(color = 'live-ink', size = 8) { return ellipse({ name: 'dot', w: size, h: size, bg: v(color) }) }

export function harkMark(size = 16, color = 'fg') {
  // three level bars: the hark glyph (drawn in the bar at Style.bar.iconSize)
  const u = size / 16
  const bars = [[1, 7], [6.25, 14], [11.5, 9.5]] // x, height; centred: a three-sample waveform
  return frame({ name: 'hark-mark', w: size, h: size },
    bars.map(([x, h]) => rect({ x: Math.round(x * u), y: Math.round(((16 - h) / 2) * u), w: Math.max(2, Math.round(3.5 * u)), h: Math.round(h * u), bg: v(color) })))
}

export function toggle(on, { hasCursor = false } = {}) {
  // Omarchy ToggleSwitch: track 42x22, knob 16, square at rounding 0; cursor ring pad 6.
  const track = frame({ name: on ? 'toggle/on' : 'toggle/off', w: 42, h: 22, bg: v(on ? 'fill-selected' : 'fill-normal'), stroke: v(on ? 'fg' : 'border-control'), strokeWidth: 1 },
    rect({ name: 'knob', x: on ? 23 : 3, y: 3, w: 16, h: 16, bg: v(on ? 'fg' : 'text-2') }))
  if (!hasCursor) return track
  return frame({ name: 'toggle-cursor', p: 5, stroke: v('border-hover'), strokeWidth: 1, bg: v('fill-hover') }, track)
}

// Neutral canvas label used on presentation boards (not part of the product UI)
export function boardTitle(title, sub) {
  return frame({ name: 'board-title', flex: 'col', gap: 4 },
    text(title, { size: F.title, color: 'text-2' }),
    sub ? text(sub, { size: F.bodySmall, color: 'text-2' }) : '')
}
