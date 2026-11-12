# hark control socket, JSON v1

The daemon (`hark run`) listens on `$HARK_CTL_SOCKET` if set, else `$XDG_RUNTIME_DIR/hark/ctl.sock` (Linux), `~/Library/Caches/hark/ctl.sock`
(macOS), else `$TMPDIR/hark-<uid>/ctl.sock`; directory 0700, socket 0600. One request line, one reply line, then
the daemon closes the connection. Requests are at most 512 bytes.

Two protocols share the socket, chosen by the first byte of the line:

- **Text** (compositor binds, `nc -U`): `start | stop | toggle | cancel | status | ping | paste-last |
  profile [next | auto | set NAME] | reload` → `ok[ <detail>]` or `error <message>`; words after the command are
  its arguments. Unchanged since the POC except the added `paste-last` and `profile`; `hark ctl <cmd>` exits 0 on `ok`, 1 on `error`, 2 with no daemon.
- **JSON v1** (status bars, panels, scripts): a line starting with `{`. `hark ctl --json <cmd>` sends one and prints
  the reply.

## Request

```json
{"version": 1, "command": "status"}
```

`version` must be `1`. `command` is one of the text commands. Optional `args`: a list of single words, the
command's arguments (`{"version": 1, "command": "profile", "args": ["set", "chat"]}`). Unknown fields are ignored.

## Replies

Action commands (`start`, `stop`, `toggle`, `cancel`, `ping`, `paste-last`, `profile`):

```json
{"version": 1, "ok": true, "command": "start", "result": "recording"}
```

`result` is the text protocol's detail: `start` → `recording | already recording`; `stop` → `stopped | idle`;
`cancel` → `cancelled | idle`; `toggle` → one of those; `ping` → `pong`; `paste-last` → `pasted <n> chars` (pastes
the newest dictated text again into the focused window; never echoes the text) or `failed` with `nothing to paste
yet` / `paste failed: …`. `profile` → the override for
the next dictation: `auto` (chosen by app again), `<name> (next dictation)`, or `<name>` with
`[profile_switch] sticky = true` (kept until `profile auto`); `next` cycles auto → each profile in config order →
auto; no arguments reports the current override.

`status`:

```json
{"version": 1, "ok": true, "command": "status",
 "state": "idle", "finishing": 0,
 "last": {"at": 1762950000.123, "utterance_id": "0199…", "status": "pasted", "profile": "prose",
          "paste_verdict": "landed", "keyup_to_paste_ms": 412.5},
 "profile_override": null}
```

- `state`: `idle | recording`. `finishing`: dictations still being transcribed/pasted.
- `last`: the most recent finished dictation of this daemon, or `null`. `status` is the history status (`pasted`,
  `copied`, `paste_failed`, `focus_changed`, `empty`, `too_short`, `no_speech`, `error`); `paste_verdict` is
  `landed | missing | not_text | unknown`; `keyup_to_paste_ms` may be `null`. **Never** contains dictated text,
  window titles or app content, so a bar module may log it.
- `profile_override`: the profile picked with `profile` for the next dictation(s), or `null` (by app).

Additional `status` fields (facts only; labels, colours and which one wins on a three-word pill belong to
the UI):

```json
{"phase": "idle", "paused": false,
 "mic": {"status": "ok"},
 "server": {"name": "desktop", "state": "ok", "rtt_ms": 38.0, "fallback": false},
 "config": {"changed": false, "restart_needed": []},
 "last": {"...": "as above", "stop_reason": "key_up", "provider": "desktop", "fallback": false,
          "cleanup": "applied", "delivery": "sent", "retry_available": true, "mic_interrupted": false}}
```

- `phase`: `idle | recording | finishing` (`state` keeps its two values).
- `paused`: dictation paused (start refused, mic released).
- `mic.status`: `ok`, `lost` (the stream stopped delivering audio or could not be reopened; hark keeps
  trying), or `off` (paused, or released between dictations with `audio.keep_open = false`).
- `server`: the provider the next dictation would use, from the cached health (never probes): its `name`,
  `state` (`ok | slow | down | unknown`; `down` when none is healthy, then `name` is the first one), `rtt_ms`
  and `fallback` (it is not the first in priority order).
- `config.changed`: the config file changed since it was loaded (`reload` applies it); `restart_needed`: the
  tables a `reload` could not apply (`audio`, `hotkey`, `providers`, ...).
- `last.stop_reason`: `key_up` (release, tap, `stop`) or `limit` (`audio.max_ms`); `null` when the dictation
  never got that far. `last.provider` / `last.fallback`: which provider transcribed it and whether that was
  not the first one (a failover counts). `last.cleanup`: the cleanup outcome (`applied`, `skipped`,
  `rejected`, `timeout`, `error`, ...; `null` before cleanup). `last.delivery`: `confirmed` (pasted and read
  back), `sent` (paste shortcut sent, not confirmed: terminals and unreadable fields), `copied` (left in the
  clipboard: focus moved, paste failed after the copy, or clipboard mode), `failed`, `none` (nothing to
  deliver). `last.retry_available`: its audio is kept for `hark retry`. `last.mic_interrupted`: the mic
  dropped out during the recording (words may be missing).

## Dictation lifecycle

- **One dictation at a time.** `start` while the previous one is still finishing is refused (`error busy`;
  the indicator flashes *Working*; nothing is recorded). Results therefore never paste out of order.
- **Cancel** (`cancel`, Esc while recording or finishing): a recording is dropped; a finishing dictation is
  dropped unless it has passed the point of no return, just before the clipboard write / paste, then the reply
  is `error too late: already pasting`. A dictation cancelled while finishing stays available to `hark retry`
  (its audio is kept before it is transcribed).
- **Length limit**: reaching `audio.max_ms` stops that dictation only (`stop_reason: limit`), never a later one.
- **Failover**: when the chosen provider fails before returning any result (connection refused, stream
  broken), the buffered audio goes once to the next healthy provider, within the same
  `server.final_timeout_ms` budget counted from key-up. Never after a result, during cleanup, or after a
  paste attempt.
- **Paste**: a paste command that may have been delivered (a timeout) is never followed by another attempt;
  the text is left in the clipboard.

## `reload`

Re-reads the config file. On error the reply is `error <config error>` and the running config stays. Otherwise
the tables that apply per dictation take effect for the next one (`words`, `context`, `gate`, `cleanup`,
`paste` except `method`, `feedback`, `profiles`, `profile_switch`, `history`, `retry`) and the reply is `ok
reloaded`, or `ok reloaded; restart for audio, hotkey` naming the tables that need `hark` restarted.

## `subscribe` (JSON only)

`{"version": 1, "command": "subscribe"}` keeps the connection open: the daemon writes one `status` reply line
at once and another whenever a field changes (coalesced, at most every 100 ms), and a `status` line every 30 s
while nothing changes, so a dead daemon is noticed. Close the connection to stop. Lines carry the same fields
as `status`; there is no audio level stream. A reconnecting UI just gets the current state again; nothing is
replayed.

Errors:

```json
{"version": 1, "ok": false, "error": {"code": "unknown_command", "message": "…"}}
```

`code`: `bad_json`, `unsupported_version`, `unknown_command` (also `subscribe` over the text protocol), `bad_args` (`args` is not a list of single
words), `too_long`, `failed` (the command ran and refused,
e.g. a text `error …` reply), `internal` (exception in the daemon).

## Compatibility

New fields may be added to replies within v1; clients must ignore unknown fields. Retyping a field or changing a
`code` needs `version: 2`. hark is a personal tool with clients in this repository only, so fields of features
that were removed are dropped within v1 (removed so far: the `learning` field; the `undo` and `swap-raw`
commands). The shapes above are pinned by `client/tests/test_client_ctl.py`
(`test_json_v1_contract`), which runs against the real daemon controller.
