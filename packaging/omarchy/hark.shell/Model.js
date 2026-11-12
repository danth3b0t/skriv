// What the bar glyph and the quick panel show for the daemon's facts (design/SPEC.md §5, §6;
// facts: docs/protocol/ctl-v1.md). Pure functions: Widget.qml binds to them, and
// client/tests/test_omarchy_shell.py runs them under Node.

var PROTOCOL_VERSION = 1
var OFF_AFTER_MS = 35000  // the daemon sends a status line at least every 30 s
var RECOVERY_KEY = "Super Alt D"

// ---- the control socket

function socketPath(env) {
  if (env.HARK_CTL_SOCKET) return env.HARK_CTL_SOCKET
  var runtime = env.XDG_RUNTIME_DIR || ("/run/user/" + (env.UID || ""))
  return runtime + "/hark/ctl.sock"
}

function subscribeRequest() {
  return JSON.stringify({ version: PROTOCOL_VERSION, command: "subscribe" }) + "\n"
}

// A subscribe line as a status object, or null (an error reply, another version, garbage).
function parseStatus(line) {
  var obj
  try {
    obj = JSON.parse(String(line))
  } catch (e) {
    return null
  }
  if (!obj || typeof obj !== "object" || obj.version !== PROTOCOL_VERSION || obj.ok !== true) return null
  if (obj.command !== "status") return null
  return obj
}

// ---- the bar glyph

function isOnline(status, lastLineAtMs, nowMs) {
  return status !== null && nowMs - lastLineAtMs <= OFF_AFTER_MS
}

// off | paused | recording | working | idle
function glyphState(status, online) {
  if (!online || !status) return "off"
  if (status.paused) return "paused"
  if (status.phase === "recording" || (status.phase === undefined && status.state === "recording")) return "recording"
  if (status.phase === "finishing" || status.finishing > 0) return "working"
  return "idle"
}

function lastId(status) {
  return status && status.last ? String(status.last.utterance_id || "") : ""
}

// A failed or paste-failed dictation, or a mic that sent a flat signal, the panel has not
// shown yet.
function unseenFailure(status, seenId) {
  var last = status && status.last
  if (!last) return false
  var failed = last.status === "error" || last.status === "paste_failed" || last.flat_signal === true
  return failed && lastId(status) !== seenId
}

function attention(status, online, seenId) {
  if (!online || !status) return false
  return problem(status, online) !== null || unseenFailure(status, seenId)
}

// Material Design Icons from the Nerd Font Omarchy sets as `monospace`.
var GLYPHS = {
  alert: 0xF002A, "alert-circle": 0xF05D6, copy: 0xF018F, "mic-off": 0xF036D, power: 0xF0425,
  refresh: 0xF0450, check: 0xF012C, close: 0xF0156
}

function glyphText(name) {
  return GLYPHS[name] ? String.fromCodePoint(GLYPHS[name]) : ""
}

// ---- the problem line: the first that applies, with its one verb

var PROBLEMS = {
  off: { label: "Not running", verb: "Start", glyph: "power", tone: "dim" },
  mic: { label: "Mic lost", verb: "Restart", glyph: "mic-off", tone: "live" },
  server: { label: "Server down", verb: "Doctor", glyph: "alert", tone: "warn" },
  restart: { label: "Restart needed", verb: "Restart", glyph: "refresh", tone: "warn" },
  config: { label: "Config changed", verb: "Reload", glyph: "refresh", tone: "warn" }
}

function problem(status, online) {
  var kind = problemKind(status, online)
  if (kind === "") return null
  var p = PROBLEMS[kind]
  return { kind: kind, label: p.label, verb: p.verb, glyph: p.glyph, tone: p.tone }
}

function problemKind(status, online) {
  if (!online || !status) return "off"
  if (status.mic && status.mic.status === "lost") return "mic"
  if (status.server && status.server.state === "down") return "server"
  var config = status.config || {}
  if (config.restart_needed && config.restart_needed.length > 0) return "restart"
  if (config.changed) return "config"
  return ""
}

// The argv a problem's verb runs (null: Doctor, which opens a terminal).
function problemCommand(kind) {
  if (kind === "off") return ["systemctl", "--user", "start", "hark.service"]
  if (kind === "mic" || kind === "restart") return ["hark", "service", "restart"]
  if (kind === "config") return ["hark", "ctl", "reload"]
  return null
}

// ---- header and tooltip

function rtt(server) {
  return server && typeof server.rtt_ms === "number" ? Math.round(server.rtt_ms) + " ms" : ""
}

// The header status: [{text, warn}] parts joined with " · ".
function serverParts(status, online) {
  if (!online || !status) return []
  if (status.paused) return [{ text: "paused", warn: false }]
  var server = status.server || {}
  var name = String(server.name || "")
  if (server.fallback) return [{ text: name, warn: true }, { text: "fallback", warn: true }]
  var parts = [{ text: name, warn: false }]
  var ms = rtt(server)
  if (ms !== "" && server.state !== "down") parts.push({ text: ms, warn: server.state === "slow" })
  return parts.filter(function(p) { return p.text !== "" })
}

// One line: "hark · ready · desktop 38 ms", or the state / the problem. Also the bar item's
// accessible name.
function sentence(status, online, seenId) {
  var p = problem(status, online)
  if (p) return "hark · " + p.label
  var state = glyphState(status, online)
  if (state === "paused") return "hark · paused"
  if (state === "recording") return "hark · recording"
  if (state === "working") return "hark · working"
  if (unseenFailure(status, seenId)) {
    var last = status.last
    if (last.flat_signal === true) return "hark · no audio from mic"
    if (last.status === "paste_failed") return "hark · paste failed, text in clipboard"
    var reason = FAILURE_WORDS[last.failure] || ""
    return "hark · last dictation failed" + (reason !== "" ? " · " + reason : "")
  }
  var server = status.server || {}
  var where = [String(server.name || ""), rtt(server)].filter(function(s) { return s !== "" }).join(" ")
  return "hark · ready" + (where !== "" ? " · " + where : "")
}

// ---- the last dictation (the newest `hark history -n 1 --json` record)

function parseHistory(text) {
  var records
  try {
    records = JSON.parse(String(text))
  } catch (e) {
    return null
  }
  if (!Array.isArray(records) || records.length === 0) return null
  var r = records[records.length - 1]
  return r && typeof r === "object" ? r : null
}

function age(ts, nowMs) {
  var t = Date.parse(String(ts || ""))
  if (isNaN(t)) return ""
  var s = Math.max(0, Math.floor((nowMs - t) / 1000))
  if (s < 60) return "now"
  if (s < 3600) return Math.floor(s / 60) + "m"
  if (s < 86400) return Math.floor(s / 3600) + "h"
  return Math.floor(s / 86400) + "d"
}

function clockText(ms) {
  var s = Math.max(0, Math.round(ms / 1000))
  var rest = s % 60
  return Math.floor(s / 60) + ":" + (rest < 10 ? "0" : "") + rest
}

// The category of a history record's error, as `last.failure` (skriv_client.status.failure).
function failureKind(error) {
  var e = String(error || "")
  if (e.indexOf("asr: degenerate output") === 0) return "degenerate_output"
  if (/within|deadline|timeout|timed out/i.test(e)) return "server_timeout"
  if (/unavailable|refused|no stream|closed/i.test(e)) return "server_down"
  return "other"
}

// Same words as the pill (skriv_client.indicator.FAILURE_WORDS).
var FAILURE_WORDS = {
  server_timeout: "server timeout", server_down: "server down", degenerate_output: "garbled transcript"
}

var CLEANUP_APPLIED = { applied: true, no_edits: true }

// The tag after "app · age": mic dropped, stopped at m:ss, cleanup skipped (the cleanup ran
// and was not kept, or never answered; a profile without cleanup records "skipped" with
// reason "off for this profile" and gets no tag).
function tag(record) {
  if (record.mic_interrupted) return "mic dropped"
  if (record.stop_reason === "limit") {
    var held = record.timings_ms && record.timings_ms.held_ms
    return "stopped at " + clockText(typeof held === "number" ? held : 0)
  }
  var c = record.cleanup
  if (c && !CLEANUP_APPLIED[c.status] && c.reason !== "off for this profile") return "cleanup skipped"
  return ""
}

// What the last-dictation block shows. ``status``: the live status (retry availability is
// only known for the daemon's own last dictation).
function lastView(record, status, nowMs) {
  if (!record) return null
  var app = String(record.app || record.app_id || "")
  var failed = record.status === "error"
  var pasteFailed = record.status === "paste_failed"
  var noSpeech = record.status === "no_speech"
  var headline = ""
  var glyph = ""
  var tone = "fg"
  if (failed) {
    var reason = FAILURE_WORDS[failureKind(record.error)] || ""
    headline = reason !== "" ? "Failed · " + reason : "Failed"
    glyph = "alert-circle"
    tone = "live"
  } else if (noSpeech) {
    // same words as the pill (skriv_client.indicator.no_speech_view): quiet is no error, a
    // flat signal from the mic is
    var device = String(record.input_device || "")
    headline = [record.flat_signal ? "No audio from mic" : "No speech", device].filter(function(s) { return s !== "" }).join(" · ")
    glyph = record.flat_signal ? "mic-off" : "close"
    tone = record.flat_signal ? "live" : "dim"
  } else if (pasteFailed) {
    var copied = record.delivery === "copied"
    headline = copied ? "Paste failed · in clipboard" : "Paste failed"
    glyph = "copy"
    tone = "warn"
  }
  var meta = [app, age(record.ts, nowMs), failed || noSpeech ? "" : tag(record)].filter(function(s) { return s !== "" })
  var live = status && status.last && lastId(status) === String(record.utterance_id || "") ? status.last : null
  var retry = !!(live && live.retry_available)
  return {
    id: String(record.utterance_id || ""),
    headline: headline,
    glyph: glyph,
    tone: tone,
    preview: failed ? "" : String(record.final_text || ""),
    meta: meta.join(" · "),
    failed: failed,
    // Enter: Copy, or Retry on a failed dictation (when its audio is kept); nothing to copy
    // after no speech
    enterVerb: failed ? (retry ? "retry" : "") : noSpeech ? "" : "copy",
    canPaste: !failed && String(record.final_text || "") !== "",
    canRetry: retry
  }
}

// ---- the panel cursor

// Rows top to bottom; the cursor starts on the problem line, else on the last dictation.
function rows(online, hasProblem, hasLast) {
  var out = []
  if (online) out.push("pause")
  if (hasProblem) out.push("problem")
  if (hasLast) out.push("last")
  out.push("config", "doctor")
  return out
}

function initialRow(list, want) {
  if (want !== "last" && list.indexOf("problem") >= 0) return "problem"
  if (list.indexOf("last") >= 0) return "last"
  if (list.indexOf("problem") >= 0) return "problem"
  return list[0]
}

function moveRow(list, current, delta) {
  var i = list.indexOf(current)
  if (i < 0) return list[0]
  return list[Math.max(0, Math.min(list.length - 1, i + delta))]
}

// ---- theme tokens (design/SPEC.md §3; same formulas as skriv_client.indicator.theme)

function parseColors(text) {
  var out = {}
  String(text || "").split("\n").forEach(function(line) {
    var m = /^\s*([A-Za-z_]+)\s*=\s*"(#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?)"/.exec(line)
    if (m) out[m[1]] = m[2].slice(0, 7).toLowerCase()
  })
  return out
}

function rgb(h) {
  return [1, 3, 5].map(function(i) { return parseInt(h.slice(i, i + 2), 16) / 255 })
}

// halves to even, like Python's round(): the same hex as skriv_client.indicator.theme
function roundHalfEven(x) {
  var r = Math.round(x)
  return x % 1 === 0.5 && r % 2 !== 0 ? r - 1 : r
}

function hex(c) {
  return "#" + c.map(function(v) {
    var n = roundHalfEven(Math.max(0, Math.min(1, v)) * 255)
    return (n < 16 ? "0" : "") + n.toString(16)
  }).join("")
}

// a over b at alpha t (1 = a)
function mix(a, b, t) {
  var x = rgb(a)
  var y = rgb(b)
  return hex([0, 1, 2].map(function(i) { return x[i] * t + y[i] * (1 - t) }))
}

function luminance(h) {
  var c = rgb(h).map(function(v) { return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) })
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}

function contrast(a, b) {
  var la = luminance(a)
  var lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

function secondaryText(fg, bg) {
  var best = fg
  for (var i = 0; i <= 60; i++) {
    var c = mix(fg, bg, 1 - i / 100)
    if (contrast(c, bg) < 4.5) break
    best = c
  }
  return best
}

function ink(color, bg, fg, target) {
  for (var i = 0; i <= 100; i++) {
    var c = mix(fg, color, i / 100)
    if (contrast(c, bg) >= target) return c
  }
  return fg
}

// Tokens from colors.toml values; bg/fg/accent from the shell (Commons.Color) when given.
function tokens(colors, bg, fg, accent) {
  bg = bg || colors.background || "#101315"
  fg = fg || colors.foreground || "#cacccc"
  accent = accent || colors.accent || colors.blue || "#cacccc"
  return {
    text2: secondaryText(fg, bg),
    accentInk: ink(accent, bg, fg, 3),
    live: ink(colors.red || "#a55555", bg, fg, 3),
    ok: ink(colors.green || accent, bg, fg, 3),
    warn: ink(colors.yellow || fg, bg, fg, 3)
  }
}

if (typeof module !== "undefined") {
  module.exports = {
    socketPath: socketPath, subscribeRequest: subscribeRequest, parseStatus: parseStatus,
    glyphText: glyphText, isOnline: isOnline, glyphState: glyphState, lastId: lastId, attention: attention,
    problem: problem, problemCommand: problemCommand, serverParts: serverParts, sentence: sentence,
    parseHistory: parseHistory, age: age, lastView: lastView, rows: rows, initialRow: initialRow,
    moveRow: moveRow, parseColors: parseColors, tokens: tokens, contrast: contrast,
    OFF_AFTER_MS: OFF_AFTER_MS
  }
}
