// Page list for design/hark-ui.fig. Root frames named `export/<name>` are exported as design/exports/<name>.png.
import { pillBoard } from './screens-indicator.mjs'
import { quickPanelIdle, quickPanelAttention, quickPanelStates, quickPanelProfile, quickPanelOff, barWidgetStates } from './screens-panel.mjs'
import { historyPasted, historyFailed, historyDiff } from './screens-history.mjs'
import { cover, systemBoard, themeMatrix } from './screens-system.mjs'

const tk = 'tokyo-night'
const lt = 'catppuccin-latte'
const both = (fn) => [{ jsx: fn(tk), theme: tk }, { jsx: fn(lt), theme: lt, breakAfter: true }]

export const PAGES = [
  { key: 'cover', name: '00 Cover', layout: 'row', frames: () => [{ jsx: cover(tk), theme: tk }] },
  { key: 'tokens', name: '01 System', layout: 'row', frames: () => both(systemBoard) },
  { key: 'indicator', name: '02 Indicator', layout: 'row', frames: () => both(pillBoard) },
  {
    key: 'panel', name: '03 Bar & quick panel', layout: 'row',
    frames: () => [
      ...both(barWidgetStates), ...both(quickPanelIdle),
      { jsx: quickPanelStates(tk), theme: tk }, { jsx: quickPanelAttention(tk), theme: tk, breakAfter: true },
      { jsx: quickPanelProfile(tk), theme: tk }, { jsx: quickPanelOff(tk), theme: tk }
    ]
  },
  {
    key: 'history', name: '04 History (later)', layout: 'row',
    frames: () => [...both(historyPasted), { jsx: historyDiff(tk), theme: tk }, { jsx: historyFailed(tk), theme: tk }]
  },
  { key: 'matrix', name: '05 Theme matrix', layout: 'row', frames: () => [{ jsx: themeMatrix(), theme: tk }] }
]
