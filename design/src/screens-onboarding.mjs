// First run: `hark.settings` opened with {"onboarding": true} by omarchy-install-dictation-hark (or `hark setup`).
// 720 × 520 card, one decision per step, every step skippable, Esc keeps what is already saved.
import { F, frame, text, icon, rect, ellipse, v, harkMark, toggle, segmented, dropdown, field, button, chip, keys, sectionHeader, separator, spacer, dot, levelBars, annotation } from './lib.mjs'

const STEPS = ['Welcome', 'Server', 'Microphone', 'Keys', 'Privacy', 'Done']

function stepper(i) {
  return frame({ name: 'stepper', flex: 'row', w: 'fill', gap: 4, items: 'center' },
    STEPS.map((s, k) => frame({ flex: 'col', gap: 5, grow: 1 },
      rect({ w: 'fill', h: 3, bg: v(k < i ? 'fg' : k === i ? 'accent' : 'fill-selected') }),
      text(`${k + 1} ${s}`, { size: F.caption, weight: k === i ? 'bold' : undefined, color: k === i ? 'fg' : 'text-2' }))))
}

function shell(i, title, sub, body, { next = 'Continue', back = true, skip = true } = {}) {
  return frame({ name: 'onboarding card', flex: 'col', w: 720, p: 2, bg: v('bg'), stroke: v('accent'), strokeWidth: 2 },
    frame({ flex: 'col', gap: 18, p: 24, grow: 1, w: 'fill' },
      frame({ flex: 'row', w: 'fill', items: 'center', gap: 10 }, harkMark(18), text('hark', { size: F.title, weight: 'bold' }), text('first run', { size: F.title, color: 'text-2' }), spacer(), text(`step ${i + 1} of ${STEPS.length}`, { size: F.caption, color: 'text-2' })),
      stepper(i),
      frame({ flex: 'col', gap: 6, w: 'fill' }, text(title, { size: F.heading, weight: 'bold' }), sub ? text(sub, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 16 }) : ''),
      frame({ name: 'step-body', flex: 'col', gap: 10, w: 'fill', grow: 1 }, body)),
    frame({ name: 'actions', flex: 'row', w: 'fill', gap: 8, px: 24, py: 14, items: 'center', bg: v('fill-normal') },
      back ? button('Back', { kind: 'ghost', hint: 'Shift+Tab' }) : '',
      skip ? text('Esc · finish later (nothing is lost)', { size: F.caption, color: 'text-2' }) : '',
      spacer(),
      skip ? button('Skip', { hint: 'S' }) : '',
      button(next, { kind: 'primary', hint: '↵' })))
}

function board(name, title, sub, card) {
  return frame({ name, flex: 'col', gap: 14, p: 32, w: 840, bg: v('dark-bg') },
    frame({ flex: 'col', gap: 4, w: 'fill' }, text(title, { size: F.heading, weight: 'bold' }), sub ? text(sub, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 15 }) : ''),
    frame({ flex: 'row', p: 20, bg: v('selection') }, card))
}

const bullet = (glyph, a, b) => frame({ flex: 'row', gap: 12, w: 'fill', items: 'start' }, frame({ w: 28, h: 28, items: 'center', justify: 'center', flex: 'row', bg: v('fill-selected') }, icon(glyph, { size: 16 })), frame({ flex: 'col', gap: 2, grow: 1 }, text(a, { size: F.body, weight: 'bold' }), text(b, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 15 })))

const option = (selected, title, sub, extra = '', cursor = false) => frame({ flex: 'row', w: 'fill', gap: 12, p: 12, items: 'start', stroke: v(selected ? 'accent-ink' : 'border-control'), strokeWidth: selected ? 2 : 1, bg: cursor ? v('fill-hover') : undefined },
  frame({ w: 16, h: 16, stroke: v(selected ? 'accent-ink' : 'border-control'), strokeWidth: 2, items: 'center', justify: 'center', flex: 'row' }, selected ? rect({ w: 8, h: 8, bg: v('accent-ink') }) : ''),
  frame({ flex: 'col', gap: 6, grow: 1 }, text(title, { size: F.body, weight: 'bold' }), text(sub, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 15 }), extra))

export const ONBOARDING = {
  welcome: (theme) => board(`export/onboarding-1-welcome-${theme}`, 'First run · 1 Welcome', 'Opened once by `omarchy-install-dictation-hark` after it selects hark as Omarchy’s dictation backend.',
    shell(0, 'Talk, and it types where you are', 'Hold a key, speak, let go: the text lands in the window you were typing in. hark looks at what you are working on so names like `kubectl` or `learn.py` come out right.', [
      bullet('mdi:server-network', 'Your machines only', 'Speech is recognised on a server you run: this laptop or a desktop on your network. No cloud, no account.'),
      bullet('mdi:crosshairs-gps', 'Context stays here', 'Window, repo and agent sessions are read locally; only a short list of words goes to your server.'),
      bullet('mdi:shield-check-outline', 'Never in the wrong place', 'If the window changes before the paste, the text goes to the clipboard instead. Terminals never get an Enter.')
    ], { next: 'Set up · 4 steps', back: false })),
  server: (theme) => board(`export/onboarding-2-server-${theme}`, 'First run · 2 Server', 'Defaults to this computer; the common Omarchy case is a laptop without a GPU pointing at a desktop on Tailscale.',
    shell(1, 'Where should speech be recognised?', 'You can add more servers later; the first one that answers is used.', [
      option(false, 'This computer', '127.0.0.1:50051 · hark-server is not running', frame({ flex: 'row', gap: 8 }, button('Start hark-server', { iconName: 'mdi:play' }), text('CPU only: expect ~1 s per sentence', { size: F.caption, color: 'text-2' }))),
      option(true, 'Another machine', 'A desktop with a GPU on your LAN or Tailscale.', frame({ flex: 'col', gap: 8, w: 'fill' },
        frame({ flex: 'row', gap: 8, items: 'center' }, field('desktop.tailnet:50051', { w: 300, focused: true }), button('Test', { hint: 'T' })),
        frame({ flex: 'row', gap: 8, items: 'center' }, icon('mdi:check-circle-outline', { size: 14, color: 'ok-ink' }), text('SERVING · parakeet-tdt-0.6b-v3 · 38 ms · biasing ✓', { size: F.bodySmall })),
        text('Plaintext is allowed only for loopback and Unix sockets; a LAN address needs TLS (CA file) — set in Recognition.', { size: F.caption, color: 'text-2', w: 'fill', lineHeight: 14 })), true)
    ])),
  mic: (theme) => board(`export/onboarding-3-microphone-${theme}`, 'First run · 3 Microphone', 'A live meter and a real test phrase; catches the muted / wrong-device / too-quiet cases before the first dictation fails.',
    shell(2, 'Check your microphone', 'Say the sentence below at your normal volume.', [
      frame({ flex: 'row', gap: 10, items: 'center', w: 'fill' }, text('Input', { size: F.body }), dropdown('Built-in Mic (sof-hda-dsp)', { w: 300 }), spacer(), text('PipeWire default', { size: F.caption, color: 'text-2' })),
      frame({ name: 'meter', flex: 'col', gap: 10, p: 16, w: 'fill', bg: v('fill-normal'), stroke: v('border-control'), strokeWidth: 1 },
        text('“The quick brown fox jumps over the lazy dog.”', { size: F.title, weight: 'bold' }),
        levelBars([0.1, 0.2, 0.5, 0.8, 0.6, 0.9, 0.7, 0.4, 0.75, 0.95, 0.6, 0.3, 0.55, 0.85, 0.7, 0.5, 0.65, 0.8, 0.45, 0.2, 0.1, 0.3, 0.6, 0.9, 0.7, 0.5, 0.3, 0.15, 0.1, 0.05, 0.04, 0.03, 0.02, 0.02, 0.03, 0.02], { h: 40, barW: 6, gap: 3 }),
        frame({ flex: 'row', gap: 8, items: 'center' }, icon('mdi:check-circle-outline', { size: 14, color: 'ok-ink' }), text('Heard you at −24 dBFS; the room is at −61 dBFS. Good.', { size: F.bodySmall }))),
      annotation('Failure copy: “Nothing heard — is the mic muted? (wpctl set-mute @DEFAULT_SOURCE@ 0)”, “Very quiet: move closer or raise input volume”.', { w: 640 })
    ])),
  keys: (theme) => board(`export/onboarding-4-keys-${theme}`, 'First run · 4 Keys', 'Omarchy owns the keys (dictation contract), so this step teaches rather than configures. The practice field is inside the card and the step only advances after a real dictation landed.',
    shell(3, 'Try it', 'Hold Right Alt, say “hello hark”, let go.', [
      frame({ flex: 'row', gap: 16, w: 'fill' },
        frame({ flex: 'col', gap: 8, p: 14, grow: 1, stroke: v('accent-ink'), strokeWidth: 2, items: 'center' }, keys('Right Alt'), text('hold to talk', { size: F.bodySmall, weight: 'bold' }), text('release to paste', { size: F.caption, color: 'text-2' })),
        frame({ flex: 'col', gap: 8, p: 14, grow: 1, stroke: v('border-control'), strokeWidth: 1, items: 'center' }, keys('Alt', 'Space'), text('hands-free', { size: F.bodySmall, weight: 'bold' }), text('press again to stop', { size: F.caption, color: 'text-2' })),
        frame({ flex: 'col', gap: 8, p: 14, grow: 1, stroke: v('border-control'), strokeWidth: 1, items: 'center' }, keys('Super', 'Esc'), text('cancel', { size: F.bodySmall, weight: 'bold' }), text('nothing is pasted', { size: F.caption, color: 'text-2' }))),
      frame({ name: 'practice', flex: 'col', gap: 6, w: 'fill' },
        text('PRACTICE FIELD', { size: F.caption, weight: 'bold', color: 'text-2', track: 1.2 }),
        frame({ flex: 'row', w: 'fill', h: 40, px: 12, items: 'center', gap: 8, stroke: v('accent-ink'), strokeWidth: 2, bg: v('fill-normal') }, text('Hello hark.', { size: F.title }), rect({ w: 2, h: 18, bg: v('fg') }), spacer(), icon('mdi:check-circle-outline', { size: 16, color: 'ok-ink' }), text('pasted · 402 ms', { size: F.caption, color: 'text-2' }))),
      annotation('AltGr layouts: Right Alt types characters, so use Alt+Space (Omarchy manual). The card shows this hint when the keymap has AltGr.', { w: 640 })
    ])),
  privacy: (theme) => board(`export/onboarding-5-privacy-${theme}`, 'First run · 5 Privacy', 'Recommended defaults shown; everything is a switch here and in Settings › Context / Privacy.',
    shell(4, 'What may hark look at?', 'Context makes names come out right. All of it is read on this computer.', [
      ...[
        ['mdi:application-outline', 'App and window title', true],
        ['mdi:console', 'Terminal folder and git repository', true],
        ['mdi:robot-outline', 'Coding-agent sessions (pi, Claude Code, Codex)', true],
        ['mdi:cursor-text', 'Text near the cursor (never password fields)', true],
        ['mdi:eye-outline', 'Learn from my corrections for 30 s after a paste', false]
      ].map(([g, l, on]) => frame({ flex: 'row', w: 'fill', gap: 10, px: 4, items: 'center' }, icon(g, { size: 15 }), text(l, { size: F.body }), spacer(), toggle(on))),
      separator(),
      frame({ flex: 'row', w: 'fill', gap: 10, items: 'center' }, text('Keep history for', { size: F.body }), spacer(), segmented(['Off', '7 days', '30 days', 'Forever'], '30 days'))
    ])),
  done: (theme) => board(`export/onboarding-6-done-${theme}`, 'First run · 6 Done', 'Ends on a doctor summary, so problems surface now instead of at the first real dictation.',
    shell(5, 'hark is ready', 'You can change everything later from the bar: click the waveform glyph.', [
      ...[
        ['ok', 'Server', 'desktop.tailnet:50051 · 38 ms'],
        ['ok', 'Microphone', 'Built-in Mic · −24 dBFS'],
        ['ok', 'Keys', 'Right Alt, Alt+Space (Omarchy)'],
        ['ok', 'Paste', 'Hyprland in-compositor'],
        ['warn', 'Paste verification', 'install python-gobject to check pastes']
      ].map(([s, a, b]) => frame({ flex: 'row', w: 'fill', gap: 10, px: 4, items: 'center' }, icon(s === 'ok' ? 'mdi:check-circle-outline' : 'mdi:alert-outline', { size: 15, color: s === 'ok' ? 'ok-ink' : 'warn-ink' }), frame({ w: 150 }, text(a, { size: F.body, weight: 'bold' })), text(b, { size: F.bodySmall, color: 'text-2' }))),
      frame({ flex: 'row', gap: 8, items: 'center', pt: 6 }, chip('tip'), text('Middle-click the bar glyph to paste your last dictation again.', { size: F.bodySmall }))
    ], { next: 'Finish', skip: false }))
}
