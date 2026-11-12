// Settings window: `hark.settings`, an omarchy-shell `panel` plugin summoned centred on the focused output
// (Omarchy menu › Setup › Dictation › hark, bar right-click, `omarchy-shell hark.settings section Context`).
// Card 960 × 640 = Style.space(), scrim [menu] scrim (bg @ 0.5), border 2 px [popups] border.
import { F, FONT, TOKENS, VARS, frame, text, icon, rect, ellipse, v, harkMark, toggle, segmented, dropdown, field, slider, button, chip, keys, sectionHeader, separator, settingRow, spacer, dot, annotation } from './lib.mjs'

export const NAV = [
  ['general', 'General', 'mdi:tune-variant'],
  ['recognition', 'Recognition', 'mdi:waveform'],
  ['context', 'Context', 'mdi:crosshairs-gps'],
  ['cleanup', 'Cleanup', 'mdi:auto-fix'],
  ['output', 'Output', 'mdi:keyboard-outline'],
  ['indicator', 'Indicator & sounds', 'mdi:bell-ring-outline'],
  ['privacy', 'Privacy & history', 'mdi:shield-lock-outline'],
  ['history', 'History', 'mdi:history'],
  ['diagnostics', 'Diagnostics', 'mdi:stethoscope']
]

const CARD_W = 960
const CARD_H = 640
const NAV_W = 208

function navItem([key, label, glyph], active, { cursor = false, badge } = {}) {
  return frame({ name: `nav/${key}`, flex: 'row', w: 'fill', gap: 8, px: 10, py: 7, items: 'center', bg: active ? v('fill-selected') : cursor ? v('fill-hover') : undefined, stroke: cursor ? v('border-hover') : undefined, strokeWidth: cursor ? 1 : undefined },
    rect({ name: 'active-bar', w: 2, h: 16, bg: active ? v('accent') : '#00000000' }),
    icon(glyph, { size: 14, color: active ? 'fg' : 'text-2' }),
    text(label, { size: F.body, weight: active ? 'bold' : undefined, color: active ? 'fg' : 'fg' }), spacer(),
    badge || '')
}

export function settingsWindow({ section, title, intro, body, footer, navCursor, wide = false, restart = false, search = '' }) {
  const nav = frame({ name: 'nav', flex: 'col', w: NAV_W, h: 'fill', minH: CARD_H - 80, gap: 2, p: 10, bg: v('fill-normal') },
    NAV.map((n) => navItem(n, n[0] === section, { cursor: n[0] === navCursor, badge: n[0] === 'context' ? chip('3 new', { tone: 'accent' }) : n[0] === 'history' ? text('2 failed', { size: F.caption, color: 'text-2' }) : undefined })),
    spacer(),
    frame({ flex: 'col', gap: 3, px: 10 },
      text('~/.config/hark/config.toml', { size: F.caption, color: 'text-2' }),
      text('hark 0.1 · ctl v1', { size: F.caption, color: 'text-2' })))
  const header = frame({ name: 'header', flex: 'row', w: 'fill', h: 48, px: 16, gap: 12, items: 'center' },
    harkMark(18, 'fg'), text('hark', { size: F.title, weight: 'bold' }), text('settings', { size: F.title, color: 'text-2' }),
    frame({ flex: 'row', gap: 6, px: 8, py: 3, items: 'center', stroke: v('border-control'), strokeWidth: 1 }, dot('ok-ink', 6), text('running · local server', { size: F.caption, weight: 'bold', color: 'text-2' })),
    spacer(),
    field(search || 'Search settings', { w: 250, placeholder: !search, iconName: 'mdi:magnify', suffix: search ? '' : '/', focused: !!search }),
    button('Edit config', { iconName: 'mdi:file-document-edit-outline' }))
  const content = frame({ name: 'content (Flickable; scrolls under fixed header, nav and footer)', flex: 'col', grow: 1, gap: 14, px: 24, pt: 20, pb: 20 },
    frame({ flex: 'col', gap: 4, w: 'fill' },
      text(title, { size: F.heading, weight: 'bold' }),
      intro ? text(intro, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 16 }) : ''),
    restart ? frame({ name: 'restart-banner', flex: 'row', w: 'fill', gap: 8, px: 10, py: 7, items: 'center', bg: v('accent-fill'), stroke: v('accent-ink'), strokeWidth: 1 }, icon('mdi:restart', { size: 14, color: 'accent-ink' }), text('Server address changed — applies to the next dictation', { size: F.bodySmall }), spacer(), button('Apply now', { kind: 'primary' })) : '',
    frame({ name: 'body', flex: 'col', gap: 8, w: 'fill' }, body))
  const foot = frame({ name: 'footer', flex: 'row', w: 'fill', h: 30, px: 16, gap: 14, items: 'center', bg: v('fill-normal') },
    footer || [
      frame({ flex: 'row', gap: 5, items: 'center' }, keys('↑', '↓'), text('move', { size: F.caption, color: 'text-2' })),
      frame({ flex: 'row', gap: 5, items: 'center' }, keys('↵'), text('change', { size: F.caption, color: 'text-2' })),
      frame({ flex: 'row', gap: 5, items: 'center' }, keys('Tab'), text('sections', { size: F.caption, color: 'text-2' })),
      frame({ flex: 'row', gap: 5, items: 'center' }, keys('/'), text('search', { size: F.caption, color: 'text-2' })),
      frame({ flex: 'row', gap: 5, items: 'center' }, keys('Bksp'), text('reset to default', { size: F.caption, color: 'text-2' })),
      spacer(),
      text('saved · written atomically, comments kept', { size: F.caption, color: 'text-2' }),
      frame({ flex: 'row', gap: 5, items: 'center' }, keys('Esc'), text('close', { size: F.caption, color: 'text-2' }))
    ])
  return frame({ name: 'hark.settings card (960 × 640 min; shown at full content height)', flex: 'col', w: CARD_W, minH: CARD_H, p: 2, bg: v('bg'), stroke: v('accent'), strokeWidth: 2 },
    header, separator(),
    frame({ name: 'main', flex: 'row', w: 'fill', items: 'stretch' }, nav, content),
    foot)
}

// Present a window on a scrimmed desktop crop
export function onScrim(name, title, sub, win) {
  return frame({ name, flex: 'col', gap: 16, p: 32, w: CARD_W + 64 + 40, bg: v('dark-bg') },
    frame({ flex: 'col', gap: 4, w: 'fill' }, text(title, { size: F.heading, weight: 'bold' }), sub ? text(sub, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 15 }) : ''),
    frame({ name: 'scrim ([menu] scrim: bg @ 0.5)', flex: 'row', p: 20, bg: v('selection') }, win))
}

const R = settingRow

// Rich text with per-run styles (keeps the spaces between runs). Span colours cannot take a variable in this
// build, so they are resolved from the frame's theme.
export function spans(theme, runs, { size = F.body, w = 'fill', lineHeight = 20 } = {}) {
  const t = TOKENS[theme]
  const hex = (name) => t[VARS[name][0]]
  const style = {
    keep: `color="${hex('fg')}"`,
    del: `color="${hex('text-2')}" textDecoration="strikethrough"`,
    ins: `color="${hex('accent-text')}" weight="bold"`,
    hit: `color="${hex('accent-text')}" weight="bold" textDecoration="underline"`
  }
  return `<Text size={${size}} font="${FONT}" w="${w}" lineHeight={${lineHeight}} color="${hex('fg')}">` +
    runs.map(([s, k]) => `<Span ${style[k]}>{${JSON.stringify(s)}}</Span>`).join('') + '</Text>'
}

// ---------------------------------------------------------------- sections
export const SECTIONS = {
  general: (theme) => ({
    title: 'General',
    intro: 'How you start and stop a dictation, and which profile shapes the text. On Omarchy the keys belong to Omarchy’s dictation contract; hark just answers them.',
    body: [
      sectionHeader('Dictation keys', chip('omarchy-dictation-hark')),
      R({ label: 'Push to talk', desc: 'Hold, speak, release to paste. Bound by Omarchy (o.bind_hold); change it in ~/.config/hypr/bindings.lua.', control: keys('Right Alt'), cfgKey: 'omarchy: default/hypr/bindings/dictation.lua' }),
      R({ label: 'Hands-free toggle', desc: 'Press once to start, again to stop. Good for long dictations.', control: keys('Alt', 'Space') }),
      R({ label: 'Cancel', desc: 'Discards the recording; nothing is pasted. Esc is not swallowed, the app sees it too.', control: frame({ flex: 'row', gap: 8, items: 'center' }, keys('Super', 'Esc'), button('Bind…', { kind: 'ghost' })), cfgKey: 'hark ctl cancel' }),
      R({ label: 'Short tap locks hands-free', desc: 'A press shorter than 300 ms keeps recording instead of cancelling (Superwhisper-style double use of one key).', control: toggle(false), cfgKey: 'hotkey.tap_to_lock · proposed', hasCursor: true }),
      sectionHeader('Profiles', button('Add rule', { iconName: 'mdi:plus', kind: 'ghost' })),
      profileTable(),
      R({ label: 'Test the rules', desc: 'Focused window now: Chromium (chromium) → prose', control: button('Test focused window', { iconName: 'mdi:target' }) })
    ]
  }),
  recognition: (theme) => ({
    title: 'Recognition',
    intro: 'Where speech is turned into text. Audio goes only to the servers listed here; context goes as a short list of words (Context section).',
    restart: true,
    body: [
      sectionHeader('Servers', button('Add server', { iconName: 'mdi:plus', kind: 'ghost' })),
      providerRow({ name: 'desktop.tailnet:50051', sub: 'RTX 3060 · NeMo parakeet-tdt-0.6b-v3 · TLS (mTLS)', status: 'ready · 38 ms', tone: 'ok', caps: ['STREAMING', 'BIAS', 'ANCHOR', 'WORD CONF'], primary: true, cursor: true }),
      providerRow({ name: '127.0.0.1:50051', sub: 'laptop CPU · transcribe.cpp parakeet Q4 (proposed fallback)', status: 'cold · loads in ~2 s', tone: 'warn', caps: ['BUFFERED', 'NO BIAS'], primary: false }),
      annotation('Order = fallback order. hark never falls back to a server you have not listed, and never to the cloud.', { w: 640 }),
      sectionHeader('Model and language'),
      R({ label: 'Model', desc: 'Server default unless set. The list comes from the server’s Capabilities.', control: dropdown('Server default · parakeet-tdt-0.6b-v3'), cfgKey: 'server.model_id' }),
      R({ label: 'Language', desc: 'Detect, or a BCP-47 tag. Cleanup only runs on English text.', control: dropdown('Detect'), cfgKey: 'server.language' }),
      sectionHeader('Timing'),
      R({ label: 'Give up after', desc: 'Key-up to final text; the audio is kept for `hark retry` either way.', control: field('5000', { w: 120, suffix: 'ms' }), cfgKey: 'server.final_timeout_ms' }),
      sectionHeader('Anchor gate · advanced'),
      R({ label: 'Revert unsupported biased words', desc: 'Biased words the unbiased decode does not support go back to what was heard.', control: toggle(true), cfgKey: 'gate.enabled' })
    ]
  }),
  context: (theme) => ({
    title: 'Context',
    intro: 'Before you speak, hark looks at what you are working on and biases recognition toward those names. Everything is read on this computer; only a ranked list of up to 30 words is sent to your server.',
    body: [
      sectionHeader('Sources'),
      sourceRow('mdi:application-outline', 'Focused app and window title', 'App id picks the profile; title words become terms. Read at key-down.', 'window_title', true),
      sourceRow('mdi:console', 'Terminal and git repository', 'Working directory of the focused terminal, file names and symbols of recent commits.', 'repo, repo_recent', true),
      sourceRow('mdi:robot-outline', 'Coding-agent sessions', 'Recent turns of pi, Claude Code and Codex for that repo (~/.pi, ~/.claude, ~/.codex). Scrubbed; never sent.', 'agent_session', true, true),
      sourceRow('mdi:cursor-text', 'Text near the cursor', 'Visible terminal screen (tmux) or the focused field (AT-SPI). Password fields and sudo/ssh prompts are never read.', 'focused_text, terminal_text', true),
      sourceRow('mdi:selection-drag', 'Selected text', 'Words in your current selection.', 'selection', false),
      sectionHeader('Learned words', frame({ flex: 'row', gap: 8, items: 'center' }, text('learning on · 30 s after each paste', { size: F.caption, color: 'text-2' }), toggle(true))),
      learnedTable(),
      R({ label: 'Ask before using a learned word', desc: 'New corrections wait here for Approve instead of being used after 2 sightings.', control: toggle(true), cfgKey: 'learn.require_approval · proposed' })
    ]
  }),
  cleanup: (theme) => ({
    title: 'Cleanup',
    intro: 'A small local model tidies punctuation, fillers and self-corrections. Every edit is checked by hark before it is used; anything doubtful keeps your words.',
    body: [
      R({ label: 'Clean up dictations', desc: 'Global switch; each profile opts in below.', control: toggle(true), cfgKey: 'cleanup.enabled' }),
      R({ label: 'Strength', desc: 'Light: punctuation and casing. Standard: also fillers and “no, I mean” corrections. Never rewrites style.', control: segmented(['Off', 'Light', 'Standard'], 'Standard'), cfgKey: 'cleanup.strength · proposed', hasCursor: true, modified: true }),
      R({ label: 'Wait at most', desc: 'After this the uncleaned text is pasted.', control: field('1000', { w: 120, suffix: 'ms' }), cfgKey: 'cleanup.deadline_ms' }),
      sectionHeader('Preview · your last dictation'),
      previewDiff(theme),
      sectionHeader('Per profile'),
      frame({ name: 'profile-cleanup', flex: 'row', gap: 10, w: 'fill' },
        ['terminal', 'chat', 'prose'].map((p) => frame({ flex: 'col', gap: 6, p: 10, grow: 1, stroke: v('border-control'), strokeWidth: 1 },
          frame({ flex: 'row', w: 'fill', items: 'center' }, text(p, { size: F.body, weight: 'bold' }), spacer(), toggle(true)),
          text(`server profile “${p}”`, { size: F.caption, color: 'text-2' }))))
    ]
  }),
  output: (theme) => ({
    title: 'Output',
    intro: 'How text reaches the window you dictated into. hark never presses Enter and never pastes into a window you did not dictate to.',
    body: [
      R({ label: 'Paste method', desc: 'Auto found Hyprland 0.55 (Lua): one in-compositor transaction re-checks the target window, then sends the paste chord to it.', control: dropdown('Auto · Hyprland in-compositor', { hint: 'detected' }), cfgKey: 'paste.method', hasCursor: true }),
      R({ label: 'Check focus before pasting', desc: 'If the window changed since key-down, copy instead and say so.', control: toggle(true), cfgKey: 'paste.check_focus' }),
      R({ label: 'Smart spacing', desc: 'Adds a leading or trailing space and lower-cases “And/The…” mid-sentence, from the text around the caret.', control: toggle(true), cfgKey: 'paste.smart_spacing' }),
      R({ label: 'Restore the clipboard', desc: 'Puts back what you had copied 250 ms after the paste (text only on Linux).', control: frame({ flex: 'row', gap: 8, items: 'center' }, field('250', { w: 90, suffix: 'ms' }), toggle(true)), cfgKey: 'paste.restore_clipboard · paste.restore_delay_ms' }),
      R({ label: 'Check that the paste landed', desc: 'Reads the field back over AT-SPI: landed / missing / unknown. Recorded in history only.', control: toggle(true), cfgKey: 'paste.verify', warn: 'PyGObject with Atspi missing: results will be “unknown”' }),
      sectionHeader('Terminals · always on'),
      frame({ name: 'terminal-rules', flex: 'row', gap: 10, w: 'fill', wrap: true },
        ['one line, no newlines', 'Ctrl+Shift+V', 'never Enter', 'escape sequences removed', 'spoken symbols: “dash dash verbose” → --verbose'].map((s) => chip(s)))
    ]
  }),
  indicator: (theme) => ({
    title: 'Indicator & sounds',
    intro: 'What you see and hear while hark listens. Nothing here takes keyboard focus, so the paste always goes where you were typing.',
    body: [
      R({ label: 'Style', desc: 'Pill: words at the bottom of the screen. Glow: lights up the target window.', control: styleChooser(), cfgKey: 'indicator.style · proposed (today: indicator.enabled)', hasCursor: true }),
      R({ label: 'Position', desc: 'Bottom sits under Omarchy’s volume OSD; top sits under the bar.', control: segmented(['Bottom', 'Top'], 'Bottom'), cfgKey: 'indicator.position' }),
      R({ label: 'Glow follows your voice', desc: 'Brightness tracks the input level (capped at 3 changes a second).', control: toggle(true), cfgKey: 'indicator.glow_level · proposed' }),
      R({ label: 'Glow strength', control: slider(0.6, { w: 160, label: 'Normal' }), cfgKey: 'indicator.glow_strength · proposed' }),
      R({ label: 'Motion', desc: 'Follow system uses Hyprland animations (`omarchy toggle animations`).', control: segmented(['Follow system', 'Reduce'], 'Follow system'), cfgKey: 'indicator.motion · proposed' }),
      sectionHeader('Sounds and notifications'),
      R({ label: 'Sounds', desc: 'Start, done, copied and error cues (pw-play). The start cue is not recorded.', control: frame({ flex: 'row', gap: 8, items: 'center' }, button('Preview', { iconName: 'mdi:play', kind: 'ghost' }), toggle(true)), cfgKey: 'feedback.sounds' }),
      R({ label: 'Notifications', desc: 'For copied-only and failed dictations, through Omarchy notifications.', control: toggle(true), cfgKey: 'feedback.notify' })
    ]
  }),
  privacy: (theme) => ({
    title: 'Privacy & history',
    intro: 'What hark keeps on this computer, and for how long. Files are private to you (0600 in a 0700 folder).',
    body: [
      leavesMachine(),
      sectionHeader('Keep'),
      R({ label: 'Dictation history', desc: 'Text, app, profile, timings and paste result of each dictation.', control: segmented(['Off', '7 days', '30 days', 'Forever'], '30 days'), cfgKey: 'history.enabled · history.retention_days · proposed', modified: true, hasCursor: true }),
      R({ label: 'Audio for retry', desc: 'The last N recordings with their context, so a failed dictation can be retried.', control: slider(10 / 50, { w: 160, label: '10 recordings' }), cfgKey: 'retry.keep' }),
      R({ label: 'Keep the microphone open', desc: 'Instant start and 200 ms of audio from before the key press, held in memory only. While it is open PipeWire shows a capture stream, so Omarchy’s microphone icon stays lit; the hark glyph turns red only when audio is used.', control: toggle(true), cfgKey: 'audio.keep_open · audio.preroll_ms' }),
      R({ label: 'Save recordings for the dataset', desc: 'Raw audio and unscrubbed context of every dictation. Only for building evaluation data.', control: toggle(false), cfgKey: 'history.save_recordings', warn: 'Contains window text and agent replies; never synced.' }),
      sectionHeader('Storage', frame({ flex: 'row', gap: 6 }, button('Open folder', { kind: 'ghost', iconName: 'mdi:folder-outline' }))),
      storageRow('History', '1.2 MB · 214 dictations', 'Delete history…'),
      storageRow('Retry audio', '18.4 MB · 10 recordings', 'Delete audio…'),
      storageRow('Learned words', '4 KB · 17 pairs', 'Clear…')
    ]
  }),
  diagnostics: (theme) => ({
    title: 'Diagnostics',
    intro: '`hark doctor`, live. Each failing check says what to do; nothing here sends a report anywhere.',
    body: [
      frame({ flex: 'row', w: 'fill', gap: 8, items: 'center' }, button('Run checks', { kind: 'primary', iconName: 'mdi:play', hint: 'R' }), button('Copy report', { iconName: 'mdi:content-copy' }), text('redacted: no text, titles or paths', { size: F.caption, color: 'text-2' }), spacer(), text('last run 14:31 · 1.4 s', { size: F.caption, color: 'text-2' })),
      check('ok', 'Server', 'desktop.tailnet:50051 · health SERVING · 38 ms · mTLS'),
      check('ok', 'Microphone', 'Built-in Mic · −31 dBFS speech, −62 dBFS room'),
      check('ok', 'Paste', 'wl-copy, hyprctl (Lua repl) · in-compositor focus check works'),
      check('warn', 'Paste verification', 'AT-SPI typelib not importable: verdicts will be “unknown”', 'sudo pacman -S python-gobject at-spi2-core'),
      check('ok', 'Daemon', 'running · pid 4242 · $XDG_RUNTIME_DIR/hark/ctl.sock (0600)'),
      check('ok', 'Context', 'tmux ✓ git ✓ agent sessions: pi ✓ claude ✓ · 140 ms of 150'),
      check('fail', 'Indicator', 'gtk4-layer-shell missing for the GTK fallback (QuickShell indicator unaffected)', 'sudo pacman -S gtk4-layer-shell'),
      sectionHeader('Last dictation · key-up → paste 412 ms'),
      timingBar()
    ]
  })
}

function profileTable() {
  const rows = [
    ['terminal', 'ghostty, kitty, alacritty, foot, *terminal* +9', 'terminal-safe · cleanup “terminal”', true],
    ['chat', 'Slack, discord, signal, telegram +3', 'cleanup “chat”', false],
    ['prose', 'everything else (fallback, cannot be removed)', 'cleanup “prose”', false]
  ]
  return frame({ name: 'profile-rules', flex: 'col', w: 'fill', stroke: v('border-control'), strokeWidth: 1 },
    rows.map(([n, apps, what, first], i) => frame({ flex: 'row', w: 'fill', gap: 12, px: 10, py: 8, items: 'center', bg: i === 1 ? v('fill-hover') : undefined },
      icon('mdi:drag-vertical', { size: 14, color: 'text-2' }),
      frame({ w: 70 }, text(n, { size: F.body, weight: 'bold' })),
      frame({ flex: 'col', grow: 1, gap: 2 }, text(apps, { size: F.bodySmall, w: 'fill', maxLines: 1 }), text(what, { size: F.caption, color: 'text-2' })),
      text(i === 2 ? 'fallback' : `#${i + 1}`, { size: F.caption, color: 'text-2' }),
      text('›', { size: F.body }))))
}

function providerRow({ name, sub, status, tone, caps, primary, cursor }) {
  return frame({ name: `provider/${name}`, flex: 'row', w: 'fill', gap: 12, px: 12, py: 10, items: 'center', stroke: v(cursor ? 'border-hover' : 'border-control'), strokeWidth: 1, bg: cursor ? v('fill-hover') : undefined },
    icon('mdi:drag-vertical', { size: 14, color: 'text-2' }),
    icon(primary ? 'mdi:server-network' : 'mdi:laptop', { size: 16 }),
    frame({ flex: 'col', gap: 4, grow: 1 },
      frame({ flex: 'row', gap: 8, items: 'center' }, text(name, { size: F.body, weight: 'bold' }), primary ? chip('primary', { tone: 'accent' }) : chip('fallback')),
      text(sub, { size: F.bodySmall, color: 'text-2' }),
      frame({ flex: 'row', gap: 4 }, caps.map((c) => chip(c)))),
    frame({ flex: 'row', gap: 6, items: 'center' }, dot(tone === 'ok' ? 'ok-ink' : 'warn-ink', 7), text(status, { size: F.bodySmall })),
    text('›', { size: F.body }))
}

function sourceRow(glyph, label, desc, key, on, cursor = false) {
  return frame({ name: `source/${label}`, flex: 'row', w: 'fill', gap: 12, px: 12, py: 8, items: 'center', bg: cursor ? v('fill-hover') : undefined, stroke: cursor ? v('border-hover') : undefined, strokeWidth: cursor ? 1 : undefined },
    icon(glyph, { size: 16, color: on ? 'fg' : 'text-2' }),
    frame({ flex: 'col', gap: 2, grow: 1 },
      text(label, { size: F.body, weight: cursor ? 'bold' : undefined }),
      text(desc, { size: F.bodySmall, color: 'text-2', w: 'fill', lineHeight: 15 }),
      text(`context.sources.${key.split(',')[0].trim()} · proposed`, { size: F.caption, color: 'text-2' })),
    frame({ flex: 'col', items: 'end', gap: 4 }, toggle(on), text(on ? 'sends: terms only' : 'off', { size: F.caption, color: 'text-2' })))
}

function learnedTable() {
  const rows = [
    ['kube control', 'kubectl', '3×', 'in use', 'Ghostty · terminal', 'in-use'],
    ['carry', 'Carrie', '2×', 'in use', 'Slack', 'in-use'],
    ['hyper land', 'Hyprland', '1×', 'waiting', 'Chromium', 'pending'],
    ['omar key', 'Omarchy', '2×', 'approve?', 'Slack · today', 'approve']
  ]
  return frame({ name: 'learned-words', flex: 'col', w: 'fill', stroke: v('border-control'), strokeWidth: 1 },
    frame({ flex: 'row', w: 'fill', gap: 12, px: 10, py: 6, bg: v('fill-normal') },
      ...[['heard', 104], ['written', 96], ['seen', 34], ['where', 118], ['status', 80]].map(([h, w]) => frame({ w }, text(h, { size: F.caption, weight: 'bold', color: 'text-2', upper: true, track: 1 }))), spacer()),
    rows.map(([heard, written, n, status, where, kind], i) => frame({ flex: 'row', w: 'fill', gap: 12, px: 10, py: 7, items: 'center', bg: i === 3 ? v('fill-hover') : undefined, stroke: i === 3 ? v('border-hover') : undefined, strokeWidth: i === 3 ? 1 : undefined },
      frame({ w: 104 }, text(heard, { size: F.bodySmall, color: 'text-2', decoration: 'strikethrough' })),
      frame({ w: 96 }, text(written, { size: F.bodySmall, weight: 'bold' })),
      frame({ w: 34 }, text(n, { size: F.bodySmall, color: 'text-2' })),
      frame({ w: 118 }, text(where, { size: F.bodySmall, color: 'text-2', maxLines: 1 })),
      frame({ w: 80 }, kind === 'approve' ? chip('approve?', { tone: 'accent' }) : kind === 'pending' ? chip('1 of 2') : chip('in use', { tone: 'ok' })),
      spacer(),
      kind === 'approve' ? frame({ flex: 'row', gap: 6 }, button('Approve', { kind: 'primary' }), button('Dismiss')) : button('Forget', { kind: 'ghost' }))))
}

function previewDiff(theme) {
  return frame({ name: 'cleanup-preview', flex: 'col', w: 'fill', gap: 8, p: 12, bg: v('fill-normal'), stroke: v('border-control'), strokeWidth: 1 },
    spans(theme, [['um ', 'del'], ['So', 'ins'], [' the deploy ', 'keep'], ['failed on uh no wait ', 'del'], ['passed on staging', 'keep'], [',', 'ins'], [' but the kubectl rollout didn’t finish', 'keep'], ['.', 'ins']]),
    text('heard: “um so the deploy failed on uh no wait passed on staging but the kubectl rollout didn’t finish”', { size: F.caption, color: 'text-2', w: 'fill' }),
    frame({ flex: 'row', gap: 6, wrap: true },
      chip('3 fillers removed', { tone: 'ok' }), chip('self-correction applied', { tone: 'ok' }), chip('negation kept', { tone: 'accent', iconName: 'mdi:shield-check-outline' }), chip('kubectl protected', { tone: 'accent', iconName: 'mdi:shield-check-outline' })),
    frame({ flex: 'row', gap: 8, items: 'center' }, button('Try another sentence', { kind: 'ghost', iconName: 'mdi:microphone' }), text('runs against your server; nothing is saved', { size: F.caption, color: 'text-2' })))
}

function styleChooser() {
  const tile = (label, inner, selected) => frame({ name: `style/${label}`, flex: 'col', gap: 4, items: 'center' },
    frame({ w: 78, h: 50, bg: v('dark-bg'), stroke: v(selected ? 'accent-ink' : 'border-control'), strokeWidth: selected ? 2 : 1 }, inner),
    text(label, { size: F.caption, weight: selected ? 'bold' : undefined, color: selected ? 'fg' : 'text-2' }))
  const miniPill = rect({ x: 24, y: 38, w: 30, h: 6, bg: v('fg') })
  const miniGlow = [rect({ x: 8, y: 8, w: 40, h: 32, stroke: v('accent'), strokeWidth: 3, blur: 3 }), rect({ x: 8, y: 8, w: 40, h: 32, stroke: v('accent'), strokeWidth: 1 })].join('')
  return frame({ flex: 'row', gap: 8 },
    tile('Pill', miniPill, false), tile('Pill + glow', miniGlow + miniPill, true), tile('Glow', miniGlow, false), tile('Off', text('—', { size: F.body, color: 'text-2' }), false))
}

function leavesMachine() {
  const item = (glyph, a, b) => frame({ flex: 'row', gap: 8, items: 'start', w: 'fill' }, icon(glyph, { size: 14 }), frame({ flex: 'col', gap: 1, grow: 1 }, text(a, { size: F.bodySmall, weight: 'bold' }), text(b, { size: F.caption, color: 'text-2', w: 'fill', lineHeight: 14 })))
  return frame({ name: 'what-leaves', flex: 'col', gap: 8, p: 12, w: 'fill', stroke: v('border-control'), strokeWidth: 1 },
    text('WHAT LEAVES THIS COMPUTER', { size: F.caption, weight: 'bold', color: 'text-2', track: 1.2 }),
    frame({ flex: 'row', gap: 16, w: 'fill' },
      item('mdi:waveform', 'Audio', 'to your servers only, while you hold the key'),
      item('mdi:format-list-bulleted', 'Up to 30 words', 'names and identifiers from context, per dictation'),
      item('mdi:text', 'Text to clean up', 'when the profile has cleanup on')),
    text('Nothing else: no telemetry, no crash reports, no cloud fallback.', { size: F.caption, color: 'fg' }))
}

function storageRow(label, value, action) {
  return frame({ flex: 'row', w: 'fill', gap: 12, px: 12, py: 6, items: 'center' }, frame({ w: 140 }, text(label, { size: F.body })), text(value, { size: F.bodySmall, color: 'text-2' }), spacer(), button(action, { kind: 'danger' }))
}

function check(status, label, detail, fix) {
  const g = { ok: ['mdi:check-circle-outline', 'ok-ink', 'ok'], warn: ['mdi:alert-outline', 'warn-ink', 'warning'], fail: ['mdi:close-circle-outline', 'live-ink', 'failed'] }[status]
  return frame({ name: `check/${label}`, flex: 'row', w: 'fill', gap: 10, px: 12, py: 7, items: 'center', bg: status === 'ok' ? undefined : v(status === 'warn' ? 'warn-fill' : 'live-fill') },
    icon(g[0], { size: 16, color: g[1] }),
    frame({ w: 150 }, text(label, { size: F.body, weight: 'bold' })),
    frame({ flex: 'col', gap: 3, grow: 1 }, text(detail, { size: F.bodySmall, color: status === 'ok' ? 'text-2' : 'fg', w: 'fill' }),
      fix ? frame({ flex: 'row', gap: 6, items: 'center' }, text('fix:', { size: F.caption, color: 'text-2' }), frame({ px: 6, py: 1, bg: v('fill-selected') }, text(fix, { size: F.caption })), text('C copy', { size: F.caption, color: 'text-2' })) : ''),
    text(g[2], { size: F.caption, weight: 'bold', color: 'text-2', upper: true, track: 1 }))
}

export function timingBar() {
  const parts = [['context', 0, 'fill-selected'], ['final', 268, 'accent-fill'], ['gate', 6, 'fill-selected'], ['cleanup', 104, 'ok-fill'], ['paste', 34, 'warn-fill']]
  const total = 412
  const W = 640
  return frame({ name: 'timing', flex: 'col', gap: 6, w: 'fill' },
    frame({ flex: 'row', w: W, h: 22 }, parts.filter((p) => p[1] > 0).map(([l, ms, c]) => frame({ w: Math.round((ms / total) * W), h: 22, bg: v(c), px: 4, items: 'center', flex: 'row', overflow: 'hidden' }, text(`${l} ${ms}`, { size: F.caption, maxLines: 1 })))),
    text('context 140 ms overlaps speech (not on the critical path) · keyup→final 268 · gate 6 · cleanup 104 · focus+paste 34', { size: F.caption, color: 'text-2' }))
}
