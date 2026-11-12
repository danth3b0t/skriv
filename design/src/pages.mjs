// Page list for design/hark-ui.fig. Root frames named `export/<name>` are exported as design/exports/<name>.png.
import { pillBoard } from './screens-indicator.mjs'
import { outlineHero, outlineFocusMoved } from './screens-outline.mjs'
import { quickPanelIdle, quickPanelAttention, quickPanelStates, quickPanelProfile, quickPanelOff, barWidgetStates } from './screens-panel.mjs'
import { historyPasted, historyFailed } from './screens-history.mjs'
import { cover, systemBoard, themeMatrix } from './screens-system.mjs'

const tk = 'tokyo-night'
const lt = 'catppuccin-latte'
const both = (fn) => [{ jsx: fn(tk), theme: tk }, { jsx: fn(lt), theme: lt, breakAfter: true }]

export const PAGES = [
  { key: 'cover', name: '00 Cover', layout: 'row', frames: () => [{ jsx: cover(tk), theme: tk }] },
  { key: 'tokens', name: '01 System', layout: 'row', frames: () => both(systemBoard) },
  { key: 'indicator', name: '02 Indicator', layout: 'row', frames: () => both(pillBoard) },
  {
    key: 'outline', name: '03 Target outline', layout: 'row',
    frames: () => [...both(outlineHero), { jsx: outlineFocusMoved(tk), theme: tk }]
  },
  {
    key: 'panel', name: '04 Bar & quick panel', layout: 'row',
    frames: () => [
      ...both(barWidgetStates), ...both(quickPanelIdle),
      { jsx: quickPanelStates(tk), theme: tk }, { jsx: quickPanelAttention(tk), theme: tk, breakAfter: true },
      { jsx: quickPanelProfile(tk), theme: tk }, { jsx: quickPanelOff(tk), theme: tk }
    ]
  },
  {
    key: 'history', name: '05 History', layout: 'row',
    frames: () => [...both(historyPasted), { jsx: historyFailed(tk), theme: tk }]
  },
  { key: 'matrix', name: '06 Theme matrix', layout: 'row', frames: () => [{ jsx: themeMatrix(), theme: tk }] }
]
