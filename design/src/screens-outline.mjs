// Target outline (S2, part of the indicator): which window the text will land in. Static; no glow, no flash.
import { frame, v, boardTitle } from './lib.mjs'
import { desktop } from './screens-desktop.mjs'
import { pill } from './screens-indicator.mjs'

export function outlineHero(theme) {
  return frame({ name: `export/outline-recording-${theme}`, flex: 'col', gap: 32, p: 48, w: 1376, bg: v('dark-bg') },
    boardTitle('Target outline'),
    desktop({ name: 'desktop', hark: 'recording', pill: pill('recording') }))
}

export function outlineFocusMoved(theme) {
  return frame({ name: `export/outline-focus-moved-${theme}`, flex: 'col', gap: 32, p: 48, w: 1376, bg: v('dark-bg') },
    boardTitle('Target outline · focus moved'),
    desktop({ name: 'desktop', hark: 'recording', focus: 'right', pill: pill('recording') }))
}
