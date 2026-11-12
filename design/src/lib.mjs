// hark design source: OpenPencil design-JSX helpers that mirror Omarchy shell (qs.Commons / qs.Ui)
// geometry at the default scale ([font] base-size 12, [spacing] scale 1, Hyprland rounding 0).
// Colours are bound to the "hark · Omarchy theme" variable collection (one mode per Omarchy theme),
// so every frame can be switched between themes inside OpenPencil.
import { readFileSync } from 'node:fs'

export const TOKENS = JSON.parse(readFileSync(new URL('../tokens/omarchy-hark-tokens.json', import.meta.url)))
export const MODES = ['tokyo-night', 'catppuccin-latte', 'catppuccin', 'gruvbox', 'rose-pine', 'flexoki-light', 'matte-black', 'nord']

// variable name -> [token key in omarchy-hark-tokens.json, Omarchy source / formula]
export const VARS = {
  bg: ['bg', 'colors.toml background = Commons.Color.background = [popups] background'],
  fg: ['fg', 'colors.toml foreground = Commons.Color.foreground = [popups] text'],
  accent: ['accent', 'colors.toml accent = Commons.Color.accent; [popups] border falls back to it (hyprland.active-border)'],
  urgent: ['urgent', 'colors.toml red = Commons.Color.urgent = [bar] active (recording/attention)'],
  green: ['green', 'colors.toml green (read by hark Theme.qml; fallback accent)'],
  yellow: ['yellow', 'colors.toml yellow (read by hark Theme.qml; fallback fg)'],
  'dark-bg': ['dark_bg', 'colors.toml dark_background (fallback background)'],
  'lighter-bg': ['lighter_bg', 'colors.toml lighter_background (fallback background)'],
  selection: ['selection', 'colors.toml selection'],
  'text-2': ['text_secondary', 'mix(fg, bg, a) with the smallest a giving >= 4.5:1 on bg (replaces Omarchy Qt.darker(fg,1.55), which fails AA on several themes)'],
  'fill-normal': ['fill_normal', 'fg @ [controls] normal-fill-alpha 0.04'],
  'fill-hover': ['fill_hover', 'fg @ [controls] hover-cursor-fill-alpha 0.08 (hover and keyboard cursor)'],
  'fill-selected': ['fill_selected', 'fg @ [controls] selected-fill-alpha 0.18'],
  'fill-pressed': ['fill_pressed', 'fg @ [controls] pressed-fill-alpha 0.22'],
  'border-hover': ['border_hover', 'fg @ [controls] hover-cursor-border-alpha 0.25'],
  'border-normal': ['border_normal', 'fg @ [controls] normal-border-alpha 0.40 (decorative only: < 3:1 on most themes)'],
  'border-control': ['border_control', 'mix(fg, border-normal) solved to >= 3:1 on bg (WCAG 1.4.11 for control boundaries)'],
  'accent-ink': ['accent_ink', 'accent, blended toward fg until >= 3:1 on bg'],
  'accent-text': ['accent_text', 'accent, blended toward fg until >= 4.5:1 on bg (small text)'],
  'live-ink': ['live_ink', 'red, blended toward fg until >= 3:1 on bg (mic live / error glyphs)'],
  'ok-ink': ['ok_ink', 'green, blended toward fg until >= 3:1 on bg'],
  'warn-ink': ['warn_ink', 'yellow, blended toward fg until >= 3:1 on bg'],
  'accent-fill': ['accent_fill', 'accent @ 0.18 over bg (Style.selectedAccentFill)'],
  'live-fill': ['urgent_fill', 'red @ 0.16 over bg'],
  'ok-fill': ['green_fill', 'green @ 0.16 over bg'],
  'warn-fill': ['yellow_fill', 'yellow @ 0.16 over bg'],
  'on-accent': ['on_accent', 'background (text on an accent fill)'],
  'glow-soft': ['glow_soft', 'accent @ 0.18 over bg (glow halo base)']
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
export const S = { xxs: 2, xs: 3, sm: 4, md: 6, lg: 8, xl: 10, xxl: 12, xxxl: 14, huge: 18, controlHeight: 28, rowPaddingX: 12, panelGap: 14, panelPadding: 18, popupPadding: 14, controlPaddingX: 10, rowGap: 8 }

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

export function chip(label, { tone = 'normal', iconName, name = 'chip' } = {}) {
  const tones = {
    normal: { stroke: 'border-control', color: 'text-2', bg: undefined },
    accent: { stroke: 'accent-ink', color: 'fg', bg: 'accent-fill' },
    live: { stroke: 'live-ink', color: 'fg', bg: 'live-fill' },
    ok: { stroke: 'ok-ink', color: 'fg', bg: 'ok-fill' },
    warn: { stroke: 'warn-ink', color: 'fg', bg: 'warn-fill' }
  }[tone]
  return frame({ name, flex: 'row', gap: 4, px: 6, py: 2, items: 'center', stroke: v(tones.stroke), strokeWidth: 1, bg: tones.bg ? v(tones.bg) : undefined },
    iconName ? icon(iconName, { size: 10, color: tones.stroke }) : '',
    text(label, { size: F.caption, weight: 'bold', color: tones.color, upper: true }))
}

export function keycap(k) {
  return frame({ name: 'key', px: 5, py: 1, stroke: v('border-control'), strokeWidth: 1, bg: v('fill-normal') }, text(k, { size: F.caption, color: 'fg', weight: 'bold' }))
}
export function keys(...ks) { return frame({ name: 'keys', flex: 'row', gap: 3, items: 'center' }, ks.map(keycap)) }

export function toggle(on, { hasCursor = false } = {}) {
  // Omarchy ToggleSwitch: track 42x22, knob 16, square at rounding 0; cursor ring pad 6.
  const track = frame({ name: on ? 'toggle/on' : 'toggle/off', w: 42, h: 22, bg: v(on ? 'fill-selected' : 'fill-normal'), stroke: v(on ? 'fg' : 'border-control'), strokeWidth: 1 },
    rect({ name: 'knob', x: on ? 23 : 3, y: 3, w: 16, h: 16, bg: v(on ? 'fg' : 'text-2') }))
  if (!hasCursor) return track
  return frame({ name: 'toggle-cursor', p: 5, stroke: v('border-hover'), strokeWidth: 1, bg: v('fill-hover') }, track)
}

export function segmented(options, selected, { name = 'segmented' } = {}) {
  return frame({ name, flex: 'row', gap: 0, stroke: v('border-control'), strokeWidth: 1 },
    options.map((o) => frame({ name: 'seg', px: 10, py: 5, bg: o === selected ? v('fill-selected') : undefined, items: 'center' },
      text(o, { size: F.bodySmall, weight: o === selected ? 'bold' : undefined, color: o === selected ? 'fg' : 'text-2' }))))
}

export function dropdown(value, { w = 220, hint } = {}) {
  return frame({ name: 'dropdown', flex: 'row', w, h: 28, px: 10, items: 'center', gap: 6, stroke: v('border-control'), strokeWidth: 1, bg: v('fill-normal') },
    frame({ flex: 'col', grow: 1 }, text(value, { size: F.bodySmall, maxLines: 1, w: 'fill' })),
    hint ? text(hint, { size: F.caption, color: 'text-2' }) : '',
    icon('mdi:chevron-down', { size: 14, color: 'text-2' }))
}

export function field(value, { w = 220, mono = true, placeholder = false, suffix, iconName, focused = false } = {}) {
  return frame({ name: 'field', flex: 'row', w, h: 28, px: 8, items: 'center', gap: 6, stroke: v(focused ? 'accent-ink' : 'border-control'), strokeWidth: focused ? 2 : 1, bg: v('fill-normal') },
    iconName ? icon(iconName, { size: 13, color: 'text-2' }) : '',
    frame({ flex: 'col', grow: 1 }, text(value, { size: F.bodySmall, color: placeholder ? 'text-2' : 'fg', maxLines: 1, w: 'fill' })),
    suffix ? text(suffix, { size: F.caption, color: 'text-2' }) : '')
}

export function slider(p, { w = 180, ticks = 0, label } = {}) {
  const tw = w - 14
  return frame({ name: 'slider', flex: 'row', gap: 10, items: 'center' },
    frame({ name: 'track', w, h: 22 },
      rect({ x: 7, y: 9, w: tw, h: 4, bg: v('fill-selected') }),
      rect({ x: 7, y: 9, w: Math.round(tw * p), h: 4, bg: v('fg') }),
      ellipse({ x: Math.round(7 + tw * p - 7), y: 4, w: 14, h: 14, bg: v('fg'), stroke: v('bg'), strokeWidth: 2 })),
    label ? text(label, { size: F.bodySmall, color: 'fg' }) : '')
}

export function button(label, { kind = 'secondary', iconName, hint, name = 'button', hasCursor = false } = {}) {
  const k = {
    primary: { bg: 'accent', color: 'on-accent', stroke: 'accent' },
    secondary: { bg: hasCursor ? 'fill-hover' : 'fill-normal', color: 'fg', stroke: hasCursor ? 'fg' : 'border-control' },
    danger: { bg: 'fill-normal', color: 'live-ink', stroke: 'live-ink' },
    ghost: { bg: undefined, color: 'fg', stroke: undefined }
  }[kind]
  return frame({ name, flex: 'row', gap: 6, px: 10, h: 28, items: 'center', bg: k.bg ? v(k.bg) : undefined, stroke: k.stroke ? v(k.stroke) : undefined, strokeWidth: k.stroke ? 1 : undefined },
    iconName ? icon(iconName, { size: 13, color: k.color }) : '',
    text(label, { size: F.bodySmall, color: k.color, weight: kind === 'primary' ? 'bold' : undefined }),
    hint ? text(hint, { size: F.caption, color: kind === 'primary' ? 'on-accent' : 'text-2' }) : '')
}

export function sectionHeader(label, trailing = '') {
  // PanelSectionHeader: caption, bold, Qt.darker(fg,1.4) -> hark text-2 (AA), uppercase with tracking
  return frame({ name: 'section-header', flex: 'row', w: 'fill', items: 'center', gap: 8, pt: 6 },
    text(label, { size: F.caption, weight: 'bold', color: 'text-2', upper: true, track: 1.2 }), spacer(), trailing)
}

export function separator() { return rect({ name: 'separator', w: 'fill', h: 1, bg: v('border-hover') }) }

export function modifiedDot() { return ellipse({ name: 'modified', w: 6, h: 6, bg: v('accent-ink') }) }

// A settings row: label + description on the left, control on the right (Voxtype/Omarchy SettingDelegate shape)
export function settingRow({ label, desc, control = '', cfgKey, hasCursor = false, modified = false, warn, badge, name = 'row', disabled = false }) {
  return frame({ name, flex: 'row', w: 'fill', gap: 16, px: 12, py: 9, items: 'center', bg: hasCursor ? v('fill-hover') : undefined, stroke: hasCursor ? v('border-hover') : undefined, strokeWidth: hasCursor ? 1 : undefined, opacity: disabled ? 0.55 : undefined },
    frame({ name: 'label', flex: 'col', grow: 1, gap: 3 },
      frame({ flex: 'row', gap: 6, items: 'center' }, modified ? modifiedDot() : '', text(label, { size: F.body, color: 'fg', weight: hasCursor ? 'bold' : undefined }), badge || ''),
      desc ? text(desc, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 15 }) : '',
      warn ? frame({ flex: 'row', gap: 5, items: 'center' }, icon('mdi:alert-outline', { size: 12, color: 'warn-ink' }), text(warn, { size: F.bodySmall, color: 'fg' })) : '',
      cfgKey ? text(cfgKey, { size: F.caption, color: 'text-2' }) : ''),
    control)
}

export function listRow({ iconName, label, value, chevron = true, hasCursor = false, valueColor = 'text-2', name = 'list-row', trailing = '' }) {
  return frame({ name, flex: 'row', w: 'fill', gap: 8, px: 10, py: 7, items: 'center', bg: hasCursor ? v('fill-hover') : undefined, stroke: hasCursor ? v('border-hover') : undefined, strokeWidth: hasCursor ? 1 : undefined },
    iconName ? icon(iconName, { size: F.icon, color: 'fg' }) : '',
    text(label, { size: F.body }), spacer(),
    value ? text(value, { size: F.body, color: valueColor, maxLines: 1 }) : '',
    trailing,
    chevron ? text('›', { size: F.body }) : '')
}

// Popup card chrome: [popups] background + 2px border (hyprland.active-border -> accent), rounding = Style.cornerRadius (0)
export function card(props, ...children) {
  return frame({ bg: v('bg'), stroke: v('accent'), strokeWidth: 2, flex: 'col', ...props }, ...children)
}

// Level bars used by the pill and the bar widget (rolling RMS history, newest on the right)
export function levelBars(levels, { color = 'fg', h = 16, barW = 3, gap = 2, floor = 2 } = {}) {
  return frame({ name: 'level', flex: 'row', gap, items: 'center', h },
    levels.map((l) => rect({ w: barW, h: Math.max(floor, Math.round(l * h)), bg: v(l < 0.08 ? 'border-hover' : color) })))
}

export function annotation(str, { w = 260, color = 'text-2' } = {}) {
  return frame({ name: 'note', flex: 'row', gap: 6, w, items: 'start' }, text('↳', { size: F.caption, color }), text(str, { size: F.caption, color, w: 'fill', lineHeight: 14 }))
}

// Neutral canvas label used on presentation boards (not part of the product UI)
export function boardTitle(title, sub) {
  return frame({ name: 'board-title', flex: 'col', gap: 4, w: 'fill', pb: 12 },
    text(title, { size: F.heading, weight: 'bold' }),
    sub ? text(sub, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 15 }) : '')
}
