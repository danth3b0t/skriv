# hark control socket, JSON v1

The daemon (`hark run`) listens on `$XDG_RUNTIME_DIR/hark/ctl.sock` (Linux), `~/Library/Caches/hark/ctl.sock`
(macOS), else `$TMPDIR/hark-<uid>/ctl.sock`; directory 0700, socket 0600. One request line, one reply line, then
the daemon closes the connection. Requests are at most 512 bytes.

Two protocols share the socket, chosen by the first byte of the line:

- **Text** (compositor binds, `nc -U`): `start | stop | toggle | cancel | status | ping | paste-last | undo |
  swap-raw` → `ok[ <detail>]` or `error <message>`. Unchanged since the POC except the added `paste-last`, `undo`
  and `swap-raw`; `hark ctl <cmd>` exits 0 on `ok`, 1 on `error`, 2 with no daemon.
- **JSON v1** (status bars, panels, scripts): a line starting with `{`. `hark ctl --json <cmd>` sends one and prints
  the reply.

## Request

```json
{"version": 1, "command": "status"}
```

`version` must be `1`. `command` is one of the text commands. Unknown fields are ignored.

## Replies

Action commands (`start`, `stop`, `toggle`, `cancel`, `ping`, `paste-last`, `undo`, `swap-raw`):

```json
{"version": 1, "ok": true, "command": "start", "result": "recording"}
```

`result` is the text protocol's detail: `start` → `recording | already recording`; `stop` → `stopped | idle`;
`cancel` → `cancelled | idle`; `toggle` → one of those; `ping` → `pong`; `paste-last` → `pasted <n> chars` (pastes
the newest dictated text again into the focused window; never echoes the text) or `failed` with `nothing to paste
yet` / `paste failed: …`; `undo` → `removed <n> chars` (Backspace × n over hark's newest paste); `swap-raw` →
`swapped <n> for <m> chars` (the paste replaced by its uncleaned text; again: back). Both refuse (`failed`, with the
reason; a notification too) unless the caret is known to be right after that paste.

`status`:

```json
{"version": 1, "ok": true, "command": "status",
 "state": "idle", "finishing": 0,
 "last": {"at": 1762950000.123, "utterance_id": "0199…", "status": "pasted", "profile": "prose",
          "paste_verdict": "landed", "keyup_to_paste_ms": 412.5},
 "learning": {"enabled": false, "watching": false}}
```

- `state`: `idle | recording`. `finishing`: dictations still being transcribed/pasted.
- `last`: the most recent finished dictation of this daemon, or `null`. `status` is the history status (`pasted`,
  `copied`, `paste_failed`, `focus_changed`, `empty`, `too_short`, `no_speech`, `error`); `paste_verdict` is
  `landed | missing | not_text | unknown`; `keyup_to_paste_ms` may be `null`. **Never** contains dictated text,
  window titles or app content, so a bar module may log it.
- `learning.enabled`: correction learning is on (`[learn] enabled`) and the platform can read fields;
  `learning.watching`: a post-paste watch window is open right now.

Errors:

```json
{"version": 1, "ok": false, "error": {"code": "unknown_command", "message": "…"}}
```

`code`: `bad_json`, `unsupported_version`, `unknown_command`, `too_long`, `failed` (the command ran and refused,
e.g. a text `error …` reply), `internal` (exception in the daemon).

## Compatibility

New fields may be added to replies within v1; clients must ignore unknown fields. Removing or retyping a field,
or changing a `code`, needs `version: 2`. The shapes above are pinned by `client/tests/test_client_ctl.py`
(`test_json_v1_contract`), which runs against the real daemon controller.
