// Page list for design/hark-ui.fig. Each frame() returns { jsx, theme }.
// Root frames named `export/<name>` are exported as design/exports/<name>.png.
import { pillBoard, pillAnatomy } from './screens-indicator.mjs'
import { glowHero, glowLevels, glowStates, glowFocusMoved, glowReduced, glowFullscreen, glowTiming } from './screens-glow.mjs'
import { quickPanelIdle, quickPanelRecording, quickPanelProfile, quickPanelOff, barWidgetStates } from './screens-panel.mjs'
import { SECTIONS, settingsWindow, onScrim } from './screens-settings.mjs'
import { historyPasted, historyFailed, historySearch } from './screens-history.mjs'
import { ONBOARDING } from './screens-onboarding.mjs'
import { cover, tokensBoard, componentsBoard, themeMatrix, motionA11y } from './screens-system.mjs'

const T = ['tokyo-night', 'catppuccin-latte']
const tk = 'tokyo-night'
const lt = 'catppuccin-latte'

const settingsFrame = (key, theme) => {
  const s = SECTIONS[key](theme)
  return onScrim(`export/settings-${key}-${theme}`, `Settings · ${s.title} · ${theme}`, null, settingsWindow({ section: key, ...s, navCursor: undefined }))
}

export const PAGES = [
  {
    key: 'cover', name: '00 Cover', layout: 'row',
    frames: () => [{ jsx: cover(tk), theme: tk }]
  },
  {
    key: 'tokens', name: '01 Tokens & components', layout: 'row',
    frames: () => [
      { jsx: tokensBoard(tk), theme: tk }, { jsx: tokensBoard(lt), theme: lt, breakAfter: true },
      { jsx: componentsBoard(tk), theme: tk }, { jsx: componentsBoard(lt), theme: lt }
    ]
  },
  {
    key: 'indicator', name: '02 Indicator pill', layout: 'row',
    frames: () => [
      ...T.map((t, i) => ({ jsx: pillBoard(t), theme: t, breakAfter: i === T.length - 1 })),
      { jsx: pillAnatomy(tk), theme: tk }
    ]
  },
  {
    key: 'glow', name: '03 Edge glow', layout: 'row',
    frames: () => [
      { jsx: glowHero(tk), theme: tk },
      { jsx: glowHero(lt), theme: lt, breakAfter: true },
      { jsx: glowLevels(tk), theme: tk },
      { jsx: glowStates(tk), theme: tk, breakAfter: true },
      { jsx: glowFocusMoved(tk), theme: tk },
      { jsx: glowReduced(lt), theme: lt, breakAfter: true },
      { jsx: glowFullscreen(tk), theme: tk },
      { jsx: glowTiming(tk), theme: tk }
    ]
  },
  {
    key: 'panel', name: '04 Bar widget & quick panel', layout: 'row',
    frames: () => [
      { jsx: barWidgetStates(tk), theme: tk }, { jsx: barWidgetStates(lt), theme: lt, breakAfter: true },
      { jsx: quickPanelIdle(tk), theme: tk }, { jsx: quickPanelIdle(lt), theme: lt },
      { jsx: quickPanelRecording(tk), theme: tk, breakAfter: true },
      { jsx: quickPanelProfile(lt), theme: lt }, { jsx: quickPanelProfile(tk), theme: tk }, { jsx: quickPanelOff(tk), theme: tk }
    ]
  },
  {
    key: 'settings', name: '05 Settings', layout: 'row',
    frames: () => Object.keys(SECTIONS).flatMap((k) => [
      { jsx: settingsFrame(k, tk), theme: tk },
      { jsx: settingsFrame(k, lt), theme: lt, breakAfter: true }
    ])
  },
  {
    key: 'history', name: '06 History', layout: 'row',
    frames: () => [
      { jsx: onScrim(`export/history-detail-${tk}`, 'History · pasted dictation · tokyo-night', 'List + detail. The detail explains the pipeline: what was heard, which context words won, what cleanup changed, how and where it was pasted.', historyPasted(tk)), theme: tk },
      { jsx: onScrim(`export/history-detail-${lt}`, 'History · pasted dictation · catppuccin-latte', null, historyPasted(lt)), theme: lt, breakAfter: true },
      { jsx: onScrim(`export/history-failed-${tk}`, 'History · failed dictation, retry · tokyo-night', 'Failures are rows with their audio, never silent: Retry re-runs with the original context and target.', historyFailed(tk)), theme: tk },
      { jsx: onScrim(`export/history-failed-${lt}`, 'History · failed dictation, retry · catppuccin-latte', null, historyFailed(lt)), theme: lt, breakAfter: true },
      { jsx: onScrim(`export/history-search-${tk}`, 'History · search · tokyo-night', 'Search runs in the daemon across raw and final text (Superwhisper’s Mac history searches only the original).', historySearch(tk)), theme: tk }
    ]
  },
  {
    key: 'onboarding', name: '07 Onboarding', layout: 'row',
    frames: () => [
      { jsx: ONBOARDING.welcome(tk), theme: tk }, { jsx: ONBOARDING.server(tk), theme: tk }, { jsx: ONBOARDING.mic(tk), theme: tk, breakAfter: true },
      { jsx: ONBOARDING.keys(tk), theme: tk }, { jsx: ONBOARDING.privacy(tk), theme: tk }, { jsx: ONBOARDING.done(tk), theme: tk, breakAfter: true },
      { jsx: ONBOARDING.welcome(lt), theme: lt }, { jsx: ONBOARDING.keys(lt), theme: lt }, { jsx: ONBOARDING.privacy(lt), theme: lt }
    ]
  },
  {
    key: 'matrix', name: '08 Theme matrix', layout: 'row',
    frames: () => [{ jsx: themeMatrix(), theme: tk }]
  },
  {
    key: 'motion', name: '09 Motion & accessibility', layout: 'row',
    frames: () => [{ jsx: motionA11y(tk), theme: tk }, { jsx: motionA11y(lt), theme: lt }]
  }
]
