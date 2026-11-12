# hark client ↔ inference-server protocol — Proposal B: `hark-ws/1`

**WebSocket · binary frames · MessagePack control (with a JSON twin for debugging) · raw audio frames with an 8-byte header**

Status: proposal by Designer B, 2026-11-12. Companion schema: [`proposal-b.schema.json`](proposal-b.schema.json) (JSON Schema 2020-12).
Legend: **[S#]** = checked against a source in §20. **[U]** = unverified estimate or assumption that must be measured.

---

## 0. Summary

- One persistent, authenticated WebSocket per (client, server) at `wss://host/v1/stream`. It opens when the client starts, so no handshake is on the key-down → paste path.
- **Two frame kinds.** Every binary frame starts with a kind byte: `0x01` = control message encoded as MessagePack, `0x02` = audio. An audio frame has an 8-byte header (`kind, flags, sid, pos`) followed by PCM16 or a raw Opus packet. The subprotocol `hark.v1.json` carries the same control messages as JSON text frames. Use it with websocat, in logs and in golden tests.
- **Many utterances per connection.** Each utterance gets a 16-bit `sid` (cheap to put in every audio frame) plus a globally unique `utterance_id`, which is the idempotency key.
- **Context goes into the ASR.** `session.start` carries the bias terms (`canonical`, `spoken[]`, `weight ∈ [0,1]`, `class`, `provenance[]`), optional free-text context items, privacy flags and a deadline. `context.update` lets slow context sources arrive after audio has started, if the model supports it.
- **One result shape for every backend.** `final` carries text, words (ms timings, confidence, char spans), N-best, an **unbiased anchor** hypothesis, bias hits, timings, and a `quality` block that says how far each field can be trusted. For example, some backends report a constant confidence of 1.0 [S9].
- **Latency features.** Audio can be sent before `session.started` arrives (no wait for a round trip). The server can finalize speculatively on `audio.eager_end` (client VAD hears a pause while the key is still held). `audio.end.total_samples` lets control messages skip ahead of queued audio. A per-session `deadline_ms` lets the server drop optional extras rather than miss the deadline.
- **Robustness.** The client keeps the whole utterance's audio. That makes resume after a reconnect, retries on another server, and hedging (race the local CPU fallback) straightforward. Every session ends with exactly one terminal message.
- **Cleanup** runs over the OpenAI-compatible `/v1/chat/completions` endpoint with `response_format: json_schema`, which llama.cpp, vLLM and SGLang all support [S24–S26]. hark only defines the edit-list contract, which works on word indices, plus the validation rules the client applies. The hark server can optionally reverse-proxy that endpoint behind its own auth.

---

## 1. Goals and non-goals

### Goals
| # | Goal | How it is met |
|---|---|---|
| G1 | ≤ 1 s p95 key-up → pasted on the LAN | Persistent connection. Session start and audio are pipelined. The only work after key-up is `audio.end` → `final` (one RTT plus the finalize compute). Eager finalize. Deadline-driven omission of extras (§7). |
| G2 | Results do not depend on the backend | Fixed `final` shape plus `quality` metadata and `omitted[]`. The client never has to guess. |
| G3 | Context injected at decode time | Structured bias terms with spoken aliases and weights, free-text context, late updates, an unbiased anchor and bias-hit evidence. |
| G4 | Easy to implement | Python reference server built on Starlette, with backends behind a ~6-method interface. Rust client built on tokio-tungstenite + serde. Swift needs nothing beyond its built-in JSON support. |
| G5 | Debuggable | JSON twin encoding, readable `type` names, structured error codes, the `hark-cat` CLI, message logs with content redacted. |
| G6 | Privacy | Per-session privacy flags. The server advertises its policy. The client fails closed (§12). |
| G7 | Robustness | Cancel, resume, idempotent retries, failover and hedging using audio the client already holds (§9–§10). |

### Non-goals (v1)
- Browser clients. This means no need for the token-in-subprotocol workarounds that NIM uses for browsers [S6].
- Server-side VAD or turn detection. The client owns the hotkey and Silero VAD.
- Diarization, translation, TTS, and long-form transcription over 5 min.
- Server-side canonicalization, guardrails or paste policy. These stay in the client.
- Third-party providers speaking hark. The client talks to them through adapters (§15).
- Defining a new transport for cleanup. It uses OpenAI-compatible chat (§6).

---

## 2. Transport and framing

### 2.1 Endpoints (one port, HTTP/1.1 + WebSocket)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/healthz` | none | Liveness. `200 {"status":"ok"}` |
| `GET` | `/readyz` | none | `200` only when at least one model is **warm**. Body holds only a list of `{model, state}`. |
| `GET` | `/v1/info` | bearer | The same body as the `welcome` message (§3). Used by the client registry for health checks without opening a WebSocket. |
| `GET` (upgrade) | `/v1/stream` | bearer | The realtime protocol (this document). |
| `POST` | `/v1/recognize` | bearer | Batch: a multipart request with a `request` part (a `session.start` object, `sid` = 0) and an `audio` part (raw frames in the declared codec, or WAV). The response is a `final` object. Used for eval runs, re-transcribing from history, and backends that only do batch. |
| `POST` | `/v1/chat/completions` | bearer | **Optional** authenticated reverse proxy to a local OpenAI-compatible LLM, used for cleanup (§6). |

### 2.2 Subprotocols and encodings
The client offers `Sec-WebSocket-Protocol: hark.v1.msgpack, hark.v1.json`. The server picks the first one it supports. If it supports neither, it refuses the upgrade: no `101` is returned, so no WebSocket exists. Starlette answers HTTP 403 when a socket is closed before `accept`.

| Subprotocol | Control messages | Audio |
|---|---|---|
| `hark.v1.msgpack` (production default) | binary frame `0x01` + MessagePack map | binary frame `0x02` + header + payload |
| `hark.v1.json` (debug, golden tests, Swift without dependencies) | **text** frame, UTF-8 JSON object | binary frame `0x02` + header + payload |

Support rules:
- Servers **MUST** support `hark.v1.json` and **SHOULD** support `hark.v1.msgpack`. The Python reference server supports both.
- Clients **MUST** support both. They offer msgpack first, and the debug tools offer json only.

Control messages are limited to the **JSON data model**: string map keys, no `bin` or `ext` types, finite floats only. This makes the two encodings convert into each other mechanically, and one JSON Schema validates both. In Rust, encode with `rmp_serde::to_vec_named`. The plain `to_vec` writes structs as arrays, which breaks interop with Python dicts [S17].

*Why both?* MessagePack lets every frame be binary, gives typed ints and floats, and is about 30–50 % smaller [U]. JSON can be read with any tool and needs no dependency in Swift. Each codec is about 30 lines of serde or Python code. If the cross-review prefers a single encoding, choose JSON; nothing else in this proposal changes.

`permessage-deflate` stays **off**. It does nothing useful for PCM or Opus, and tungstenite still does not support it upstream [S16].

### 2.3 Frame kinds (first byte of every binary frame)
| Byte | Kind | Direction |
|---|---|---|
| `0x00` | reserved (invalid) | — |
| `0x01` | CTRL, MessagePack body (msgpack subprotocol only) | both |
| `0x02` | AUDIO | client → server |
| `0x03–0x7F` | reserved for future versions | — |
| `0x80–0xFF` | experimental/private | — |

On an unknown kind byte the receiver sends `error{code:"bad_frame"}` (non-fatal) and drops the frame. In JSON mode a `0x01` binary frame is a `protocol_error`.

### 2.4 Audio frame
```
offset size  field   encoding     meaning
0      1     kind    u8 = 0x02
1      1     flags   u8           bit0 DISCONTINUITY: client lost audio before this frame
                                  (pos jumps forward; the server zero-fills the gap)
                                  bits1-7 reserved, MUST be 0
2      2     sid     u16 BE       session handle from session.start
4      4     pos     u32 BE       index of the payload's first sample, at the session
                                  sample rate, counted from the session's sample 0
8      n     payload              pcm_s16le: n/2 samples, little-endian
                                  opus: exactly one raw Opus packet (no Ogg)
```
- **No sequence number.** WebSocket over TCP is ordered and reliable. `pos` is enough to align timing, find gaps and overlaps, acknowledge receipt, and resume. A u32 at 16 kHz covers 74 h.
- The server keeps `received_end` per session. If `pos == received_end`, it appends. If `pos < received_end`, it trims the overlap (this happens after a resume). If `pos > received_end` with DISCONTINUITY set, it zero-fills the gap; without the flag it sends `error audio_gap` and zero-fills anyway.
- Recommended frame length: **20 ms** (640 B of PCM at 16 kHz). Any whole number of 10 ms steps from 10 to 120 ms is allowed. Faster-than-real-time bursts for pre-roll, resume and failover can use up to `limits.max_audio_frame_bytes` per frame.
- Overhead per 20 ms PCM frame: 8 B hark header + 8 B WebSocket header (2 bytes + 2 bytes extended length + 4-byte mask) ≈ 2.5 %.

### 2.5 Control message rules
- Every control message is a map with `type` (dotted string). Session-scoped messages also carry `sid`.
- **Receivers MUST ignore unknown fields.** Senders MUST produce messages that validate against the schema. The schema is strict for senders: `additionalProperties:false`, except top-level `x_*` extension fields.
- An unknown `type` gets a non-fatal `error{code:"unknown_message_type", ref:<type>}`. The connection stays up.
- Units: durations and word times are integer **milliseconds**. Audio positions are integer **samples**. Text offsets are **Unicode code points**, half-open `[start, end)`. Languages are BCP-47 tags.
- Ordering: messages on a `sid` are handled in arrival order, except that **`session.cancel` takes priority** (§9). The client writer sends control messages ahead of queued audio. `audio.end` stays correct anyway because it names `total_samples`, and the server waits until it has all of them (the same idea as Speechmatics' `EndOfStream.last_seq_no` [S3]).

### 2.6 Versioning and evolution
- The **major** version is in the subprotocol (`hark.v1.*`) and the path (`/v1/`). Breaking changes require `hark.v2`, and the server may offer both.
- The **minor** version is in `welcome.protocol.minor`. A minor bump can only add optional fields, optional message types, enum values that receivers can treat as "unknown", or features.
- New server→client message types are only sent after the client opts in (`session.start.output` flags, or the upgrade header `X-Hark-Features: a,b`).
- `welcome.features[]` lists optional capabilities by name (`eager_end`, `late_context`, `resume`, `batch_http`, `cleanup_proxy`). Clients only use features the server lists.
- `server.boot_id` changes on restart. When it changes, all resume state and result caches on the client side are invalid.

### 2.7 Limits (advertised in `welcome.limits`; defaults in brackets)
`max_sessions` [4] · `max_ctrl_bytes` [262 144] · `max_audio_frame_bytes` [65 536] · `max_utterance_ms` [300 000] · `max_bias_terms` [500] · `max_context_chars` [16 000] · `ack_interval_ms` [250] · `resume_window_ms` [10 000] · `result_cache_ms` [60 000].
Library defaults to set explicitly: Python `websockets` `max_size` is 1 MiB [S18]; uvicorn `ws_max_size` is 16 MiB [S19]. Both should be set to `max(max_ctrl_bytes, max_audio_frame_bytes)+16`.

---

## 3. Capability discovery

The server sends `welcome` **right after the upgrade** without being asked. There is no `hello`: client identity comes from the upgrade headers (`Authorization`, `User-Agent`, `X-Hark-Device`). `GET /v1/info` returns the same body. The client registry combines it with static config, and static config wins (for example, an admin can force `context: never` for a provider).

```json
{
  "type": "welcome",
  "protocol": {"name": "hark", "major": 1, "minor": 0},
  "encoding": "msgpack",
  "server": {"name": "hark-server-py", "version": "0.3.1", "instance": "desktop-3060", "boot_id": "01JCX4V6M2Q8Z7R0B1N5K3T9HD"},
  "features": ["eager_end", "late_context", "resume", "batch_http", "cleanup_proxy"],
  "limits": {"max_sessions": 4, "max_ctrl_bytes": 262144, "max_audio_frame_bytes": 65536,
             "max_utterance_ms": 300000, "max_bias_terms": 500, "max_context_chars": 16000,
             "ack_interval_ms": 250, "resume_window_ms": 10000, "result_cache_ms": 60000},
  "privacy": {"retention_supported": ["none", "metrics"], "default_retention": "none",
              "external_forwarding": false, "logs_content": false},
  "default_model": "parakeet-tdt-0.6b-v3+gpupb",
  "models": [
    {
      "id": "parakeet-tdt-0.6b-v3+gpupb", "backend": "nemo",
      "languages": ["en", "de", "fr", "es", "it", "pl"],
      "audio": {"codecs": ["pcm_s16le", "opus"], "sample_rates": [16000]},
      "streaming": "rolling", "partials": true,
      "bias": {"mode": "decode_time", "max_terms": 300, "max_phrase_chars": 64, "max_spoken_per_term": 4,
               "weights": "threshold", "aliases": "boosted", "late_update": "until_end", "cache": true},
      "context_text": {"supported": false},
      "nbest": {"max": 2, "kind": "biased_plus_anchor"},
      "word_timings": {"kind": "native", "resolution_ms": 80},
      "word_confidence": "raw",
      "anchor": {"kind": "shared_encoder", "cost": "low"},
      "formatting": {"punctuation": true, "casing": true, "itn": false},
      "eager_end": true, "max_audio_ms": 300000, "state": "warm",
      "perf": {"finalize_ms_p50": 90, "finalize_ms_p95": 170, "samples": 412}
    },
    {
      "id": "qwen3-asr-1.7b", "backend": "qwen3-asr",
      "languages": ["*"],
      "audio": {"codecs": ["pcm_s16le", "opus"], "sample_rates": [16000]},
      "streaming": "buffered", "partials": false,
      "bias": {"mode": "prompt", "max_terms": 150, "max_phrase_chars": 64, "max_spoken_per_term": 2,
               "weights": "threshold", "aliases": "prompted", "late_update": "until_end", "cache": false},
      "context_text": {"supported": true, "max_chars": 4000, "echo_risk": true},
      "nbest": {"max": 0, "kind": "none"},
      "word_timings": {"kind": "aligned", "resolution_ms": 20},
      "word_confidence": "none",
      "anchor": {"kind": "parallel_decode", "cost": "high"},
      "formatting": {"punctuation": true, "casing": true, "itn": true},
      "eager_end": true, "max_audio_ms": 300000, "state": "cold"
    }
  ],
  "cleanup": {"proxy_path": "/v1/chat/completions", "models": ["qwen3-1.7b-instruct-q8"]}
}
```

Capability enums (full definitions are in the schema):
- `streaming`:
  - `native`: a true streaming decoder, such as cache-aware RNNT.
  - `rolling`: buffers audio and re-decodes it periodically to produce partials.
  - `buffered`: decodes only when finalizing; audio still uploads while the user speaks.
- `bias.mode`:
  - `decode_time`: shallow fusion or boosting tree (GPU-PB, Riva boosting, sherpa hotwords).
  - `rescoring`: post-decode acoustic rescoring (CTC-WS as in FluidAudio).
  - `prompt`: terms rendered into the LLM prompt (Qwen3-ASR, Voxtral).
  - `none`.
- `bias.weights`:
  - `per_term`: each term gets its own boost.
  - `bucketed`: terms are grouped into a few boost levels.
  - `threshold`: weights only decide ordering and truncation.
  - `ignored`.
- `bias.aliases`: `boosted` | `rescored` | `prompted` | `ignored`.
- `bias.late_update`:
  - `none`.
  - `before_audio`: settings lock at the first audio chunk, as in Voxtral [S4].
  - `until_end`: the server applies the newest version when it finalizes.
- `word_timings.kind`:
  - `native`: emitted by the decoder.
  - `frame_grid`: decoder emission positions only, e.g. an 80 ms grid [S9].
  - `aligned`: from a forced aligner.
  - `none`.
- `word_confidence`:
  - `calibrated`.
  - `raw`: monotone in correctness but not a probability.
  - `constant`: useless, e.g. RNNT returns 1.0 [S9].
  - `none`.
- `anchor.kind`:
  - `shared_encoder`: a second greedy decode on the same encoder output.
  - `parallel_decode`: a second full decode.
  - `pre_rescore`: the text before rescoring.
  - `none`.
- `state`: `warm` | `loading` | `cold` | `suspended` (GPU lent to something else) | `unavailable`.

---

## 4. Session lifecycle

```
Client                                                         Server
  |== GET /v1/stream  Upgrade, Authorization: Bearer …,  ======>|
  |   Sec-WebSocket-Protocol: hark.v1.msgpack, hark.v1.json      |
  |<= 101 Switching Protocols (hark.v1.msgpack) ================|
  |<------------------------------------------------- welcome    |
  |   … idle; WS ping every 15 s; app ping/pong every 10 s …     |
key-down
  |-- session.start {sid:7, utterance_id, audio, bias v1 (cheap)}->|   no waiting:
  |== AUDIO sid=7 pos=0      (300 ms pre-roll burst) ==========>|   audio follows
  |== AUDIO sid=7 pos=4800 …  (20 ms frames, real time) =======>|   immediately
  |<------------------------------------------ session.started   |
  |-- context.update {sid:7, bias v2 (repo vocab, AX text)} ---->|
  |<------------------------------------------ audio.ack / partial
  |-- audio.eager_end {sid:7, speech_end} (VAD: 300 ms pause) -->|   server MAY finalize
key-up                                                          |   speculatively
  |== AUDIO sid=7 (last, short frame) =========================>|
  |-- audio.end {sid:7, total_samples, speech_end, key_up} ----->|
  |<------------------------------------------ final (terminal)  |
  client: gate (anchor/bias_hits) → canonicalize → [cleanup via /v1/chat/completions] → paste
```

**Per-sid state machine (server view):**
`FREE —session.start→ STARTING —(validated)→ STREAMING —audio.end→ FINALIZING —→ final → FREE`.
- `session.cancel` from any state → `session.end{status:"cancelled"}` → FREE.
- Validation fails → `session.end{status:"rejected", error}` → FREE.
- Runtime failure → `session.end{status:"failed", error}` → FREE.
- The connection drops during STREAMING/FINALIZING → **ORPHANED** for `resume_window_ms` → resumed, or expired (§10).

**Invariants**
1. Every `session.start` gets **exactly one terminal message**: `final` or `session.end`. A `sid` can be reused only after its terminal message.
2. The client MAY send audio frames for a `sid` before `session.started` arrives. The server buffers them while it validates, and drops them if it rejects the session.
3. Frames for an unknown or finished `sid` are dropped silently (counted in metrics). This prevents error storms after a cancel.
4. `final.utterance_id` + `final.attempt` identify the result. The client pastes **at most once per `utterance_id`**.

---

## 5. Message reference

All examples are shown in the JSON encoding. Full field definitions are in the schema.

### 5.1 Client → server

#### `session.start`
```json
{
  "type": "session.start",
  "sid": 7,
  "utterance_id": "01JCX5A0F3W2N8Q6R4T1Y9KZ7M",
  "attempt": 1,
  "resume": false,
  "model": "default",
  "mode": "stream",
  "audio": {"codec": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_ms": 20},
  "languages": ["en"],
  "bias": {
    "version": 1,
    "list_id": "b3:9f2c41d0a7e5",
    "strength": 0.5,
    "terms": [
      {"id": "t1", "canonical": "kubectl", "spoken": ["cube control", "cube cuttle", "kube c t l"],
       "weight": 0.92, "class": "identifier", "boost_canonical": false,
       "provenance": [{"source": "terminal", "ref": "h:4be1", "score": 0.8}, {"source": "history", "score": 0.6}]},
      {"id": "t2", "canonical": "Parakeet", "spoken": [], "weight": 0.7, "class": "product",
       "provenance": [{"source": "repo_vocab", "ref": "h:77ac"}]},
      {"id": "t3", "canonical": "Hyprland", "spoken": ["hyper land"], "weight": 0.55, "class": "product",
       "provenance": [{"source": "window_title"}]}
    ]
  },
  "context": {
    "version": 1,
    "items": [
      {"source": "app", "text": "Terminal (kitty) in ~/projects/hark, git branch protocol-b", "weight": 0.6},
      {"source": "domain_hint", "text": "shell commands and Kubernetes operations", "weight": 0.5}
    ]
  },
  "output": {"partials": true, "partial_interval_ms": 300, "partial_words": false, "nbest": 3,
             "word_timings": true, "word_confidence": true, "anchor": true, "bias_hits": true,
             "punctuation": true, "casing": true, "itn": true},
  "deadline_ms": 600,
  "priority": "interactive",
  "privacy": {"retention": "none", "allow_external": false, "sensitive": true},
  "trace_id": "c7f1e2"
}
```
Field semantics:
- `sid`: chosen by the client (u16), unique among the connection's live sessions.
- `utterance_id`: ULID or UUIDv7 generated by the client. It stays the same across retries, resumes and failover. `attempt` goes up by 1 on each retry or failover.
- `mode`:
  - `stream` (default): partials if the model supports them.
  - `buffered`: the server only accumulates audio and decodes at the end. Use it when the client does not want partials, so a rolling backend does no extra re-decode work.
- `model`: `"default"` or an id from `welcome.models[].id`. If the requested model is cold, the session is rejected with `model_unavailable` and `retry_after_ms`. The server does **not** silently swap models; routing belongs to the client.
- `languages`: absent or empty means auto-detect. One entry forces that language. Several entries are a hint, the same as OpenAI's `languages` [S1].
- **Bias terms**
  - `canonical`: the exact written form the user wants pasted.
  - `spoken[]`: how the term is said or how ASR tends to write it.
  - `boost_canonical=false`: use when the canonical form is something the ASR would never emit (`k8s`, `kubectl`). The server then boosts only the spoken forms and still reports the hit against the term id.
  - `weight`: the client ranker's relevance in [0,1]. The server maps it **monotonically** onto its backend scale (§14), so clients never deal with backend-specific boost numbers.
  - `strength`: a global multiplier. 0.5 means the server default, which is tuned on our eval set.
  - `class`: lets servers apply class-specific handling, e.g. capitalization for cased models. GPU-PB needs key phrases capitalized for cased models [S8].
  - `provenance`: for observability and dataset building. `ref` is an opaque hash, never raw text. If the provider policy sets `minimize`, the client strips provenance entirely.
  - `list_id`: a content hash of `terms`. The server uses it as a cache key; NeMo's per-stream `cache_key` gives "near-zero overhead" with memory caching [S7].
- `context.items[]`: free text labelled by source. The server renders it with a **model-specific template** (§14) and reports `prompt_hash` in the `final`. The client applies the privacy policy before sending: no context at all if the provider is not trusted for it.
- `deadline_ms`: the server's budget from receiving `audio.end` (with all of `total_samples`) to **sending** `final`. To meet it the server drops optional extras (anchor, then nbest, then bias-hit scores, then alignment) and lists what it dropped in `final.omitted`. It never drops the primary text. If the decode itself runs past the deadline, the server still sends the result. At 3× the deadline with no `final`, the server cancels the session on its own and replies `session.end{status:"expired"}`.
- `privacy`: see §12.

#### `context.update`
```json
{"type": "context.update", "sid": 7,
 "bias": {"version": 2, "list_id": "b3:1d0e88aa", "strength": 0.5, "terms": [ … full replacement … ]},
 "context": {"version": 2, "items": [ … ]}}
```
- Each field **replaces** the previous set. Its `version` must be higher than the last one.
- Allowed only if the model's `bias.late_update ≠ "none"`, and only until `audio.end`.
- With `before_audio`, an update after the first audio frame gets `error late_update_rejected` (non-fatal) and is ignored.
- `final.effective.bias_version` says which version was actually used.
- *Rationale:* key-down must not wait for AT-SPI, repo or OCR gathering. Context sources that cost ≤ 30 ms go into `session.start`; slower ones follow in an update.

#### `audio.eager_end` (feature `eager_end`)
`{"type":"audio.eager_end","sid":7,"speech_end":81920}`. The client's VAD has seen ≥ 300 ms [U] of non-speech after speech while the key is still held.
- The server MAY start a speculative finalize over `[0, speech_end + pad)`, covering both the biased decode and the anchor. It caches the result under the key `(speech_end, bias_version, context_version)`.
- A newer `eager_end` replaces the older one, and any in-flight speculation is cancelled.
- This is a hint only. The server still waits for `audio.end`.

#### `audio.end`
`{"type":"audio.end","sid":7,"total_samples":89600,"speech_end":81920,"reason":"key_up"}`
- The server finalizes once it has received `total_samples`.
- If the cached eager result has the same `speech_end` and the same bias and context versions, the server returns it immediately (`timing.eager_hit=true`).
- `reason` is one of `key_up`, `toggle`, `vad`, `max_duration`.

#### `session.cancel`
`{"type":"session.cancel","sid":7,"reason":"user"}`. Idempotent. `reason` is one of `user`, `superseded`, `failover`, `timeout`, `shutdown`.

#### `ping` / `warmup`
- `{"type":"ping","id":41,"t_ms":123456789}` is answered by `pong`. `t_ms` is echoed back unchanged and is used to measure RTT.
- `{"type":"warmup","id":3,"model":"qwen3-asr-1.7b"}` asks the server to load or warm a model. The server answers with `warmup.result`.

### 5.2 Server → client

#### `session.started`
```json
{"type": "session.started", "sid": 7, "utterance_id": "01JCX5A0F3W2N8Q6R4T1Y9KZ7M",
 "model": "parakeet-tdt-0.6b-v3+gpupb", "resumed": false, "received": 4800,
 "bias": {"version": 1, "accepted": 3, "dropped": []},
 "context": {"version": 1, "chars_used": 0, "truncated": false},
 "warnings": ["context_text_unsupported"]}
```
`dropped[]` items look like `{"id":"t9","reason":"too_long"|"over_limit"|"empty_tokenization"|"low_weight"|"unsupported_chars"|"duplicate"}`. The client logs them, and they also feed the ranker's eval.

#### `audio.ack`
`{"type":"audio.ack","sid":7,"received":24000,"decoded":19200}`. Sent every `ack_interval_ms` of received audio.
- `received`: drives the resume point and the client's view of what is in flight.
- `decoded`: shows server lag. `received − decoded` is the backlog the server has not yet processed.

#### `partial`
`{"type":"partial","sid":7,"rev":4,"text":"run cube control get","stable_chars":9,"decoded":38400}`.
- A partial is a **full replacement** for the previous one, not a delta. It is at most a few hundred bytes.
- `stable_chars` is the length of the prefix the server does not expect to change.
- `rev` is monotone. Partials are display-only and are never pasted.

#### `final` (terminal, success)
```json
{
  "type": "final", "sid": 7, "utterance_id": "01JCX5A0F3W2N8Q6R4T1Y9KZ7M", "attempt": 1,
  "status": "ok",
  "text": "Run cube control get pods.",
  "words": [
    {"text": "Run",     "start_ms": 320,  "end_ms": 480,  "conf": 0.97, "span": [0, 3]},
    {"text": "cube",    "start_ms": 560,  "end_ms": 800,  "conf": 0.81, "span": [4, 8],  "term": "t1"},
    {"text": "control", "start_ms": 800,  "end_ms": 1200, "conf": 0.78, "span": [9, 16], "term": "t1"},
    {"text": "get",     "start_ms": 1280, "end_ms": 1440, "conf": 0.96, "span": [17, 20]},
    {"text": "pods.",   "start_ms": 1440, "end_ms": 1840, "conf": 0.93, "span": [21, 26]}
  ],
  "nbest": [
    {"text": "Run cube control get pods.", "score": -4.21},
    {"text": "Run cube controller get pods.", "score": -6.02}
  ],
  "anchor": {"text": "Run cube control get pods.", "score": -4.35,
             "words": [ … same shape as words … ]},
  "bias_hits": [
    {"term": "t1", "words": [1, 3], "surface": "cube control", "matched": "spoken", "spoken_index": 0,
     "in_anchor": true, "anchor_surface": "cube control", "evidence": "match"}
  ],
  "language": {"code": "en", "confidence": 0.99},
  "audio": {"received": 89600, "decoded": 89600, "duration_ms": 5600, "discontinuities": 0},
  "timing": {"end_to_final_ms": 74, "queue_ms": 2, "decode_ms": 61, "anchor_ms": 9, "lag_at_end_ms": 40, "eager_hit": false},
  "model": {"id": "parakeet-tdt-0.6b-v3+gpupb", "revision": "2026-09-30"},
  "effective": {"bias_version": 2, "context_version": 2, "bias_terms_used": 41, "context_chars_used": 0},
  "quality": {"word_timings": "native", "word_confidence": "raw", "nbest": "biased_plus_anchor", "anchor": "shared_encoder"},
  "omitted": [],
  "flags": []
}
```
- `text`: display text after the requested punctuation, casing and ITN. `words[].span` maps each word into `text`, so the client can splice the canonical form ("kubectl") into the right place without re-tokenizing.
- `words[].term`: set when this word is part of a bias hit. `words[].alts`: optional word-level alternatives from a beam or confusion network.
- `nbest[0]` is always the primary hypothesis. `score` is a natural-log probability that can only be compared within this `final`; it is `null` if the backend has none. If no N-best is available the array is empty and `"nbest"` appears in `omitted`.
- `anchor`: the hypothesis decoded **without bias and without context**, from the same audio and model. The client compares `anchor` with the primary at each bias hit. If a term shows up only in the biased decode, has weak confidence, and the anchor has a plausible ordinary word there, the client reverts it. This is the "always keep an unbiased anchor" rule from the research findings.
- `bias_hits[].evidence`:
  - `decoder`: the backend reports the boost path itself.
  - `spotter`: an acoustic keyword-spotter score, e.g. FluidAudio CTC-WS [S13].
  - `match`: the server only string-matched the output against the spoken and canonical forms.
  
  The client re-derives hits from `words` and `anchor` anyway. Server hits only add scores the client cannot compute.
- `flags`: `context_echo_suspected` (the output contains ≥ 6 consecutive words of context text that are absent from the anchor), `late_context_ignored`, `truncated`, `replayed`, `audio_gap_filled`.
- `status:"no_speech"`: `text` is empty, and the client pastes nothing.

#### `session.end` (terminal, non-success)
`{"type":"session.end","sid":7,"utterance_id":"…","status":"rejected","error":{"code":"model_unavailable","message":"qwen3-asr-1.7b is cold","retryable":true,"retry_after_ms":8000}}`
`status` is one of `cancelled`, `rejected`, `failed`, `expired`.

#### `error` (non-terminal)
`{"type":"error","sid":7,"code":"late_update_rejected","message":"…","retryable":false,"fatal":false,"ref":"context.update"}`
- `fatal:false` errors are warnings scoped to the connection or a session.
- `fatal:true` means the server will close the connection next. All live sessions become ORPHANED and can be resumed (§10).

**Error codes**

| Code | Typical scope | Retryable |
|---|---|---|
| `unauthorized`, `forbidden` | upgrade (HTTP 401/403, no WebSocket) | no |
| `protocol_error`, `bad_frame`, `unsupported_encoding`, `message_too_large` | connection | no |
| `unknown_message_type`, `invalid_message`, `late_update_rejected` | warning | no |
| `sid_in_use`, `unknown_sid`, `too_many_sessions` | session reject | yes (new sid / later) |
| `unknown_model`, `unsupported_audio`, `unsupported_feature` | session reject | no (route elsewhere) |
| `bias_too_large`, `context_too_large` | session reject | yes, after the client trims |
| `context_not_allowed`, `privacy_unsatisfiable` | session reject | no |
| `model_unavailable`, `overloaded`, `draining` | session reject | yes (`retry_after_ms`) |
| `audio_gap` | warning | — |
| `audio_overflow`, `utterance_too_long` | session fail | no |
| `deadline_exceeded` | session expire | yes (elsewhere) |
| `decode_failed`, `internal` | session fail | yes |

**WebSocket close codes**

| Code | Meaning |
|---|---|
| 1000 | normal |
| 1001 | server shutdown (after `server.notice{draining}`) |
| 1002 | protocol error |
| 1008 | policy violation after the upgrade |
| 1009 | message too big |
| 1011 | internal |
| 1013 | try again later (overload), as NIM uses it [S6] |
| 4400–4499 | hark-specific mirrors of fatal `error.code`s |

#### `pong`, `warmup.result`, `server.notice`
- `{"type":"pong","id":41,"t_ms":123456789,"load":{"active_sessions":1,"queued":0,"est_finalize_ms":95}}`
- `{"type":"warmup.result","id":3,"model":"qwen3-asr-1.7b","state":"warm","took_ms":5400}`
- `{"type":"server.notice","kind":"model_state","model":"qwen3-asr-1.7b","state":"suspended"}`
- `{"type":"server.notice","kind":"draining","retry_after_ms":30000}`: the server accepts no new sessions, and existing ones finish.

---

## 6. Cleanup: an OpenAI-compatible transport with a hark-defined edit contract

**Decision.** Cleanup does **not** get its own hark message. The client calls `POST /v1/chat/completions` on an OpenAI-compatible server with `response_format: {type:"json_schema", json_schema:{name:"hark_edits_v1", strict:true, schema:…}}`. llama.cpp turns the schema into a grammar [S24], vLLM [S25] and SGLang [S26] support the same field, and so do cloud providers. The hark server may expose an **authenticated reverse proxy** to its local LLM (`welcome.cleanup.proxy_path`), so the LLM is never reachable directly on the network.

**Why not in-band over the WebSocket?**
1. It would save only one LAN RTT (about 1–5 ms [U]) compared with a warm HTTP keep-alive connection.
2. Canonicalization and protected-span detection happen in the client *after* `final`. Chaining cleanup on the server would mean moving that logic to the server.
3. Any cleanup provider works unchanged: local llama.cpp, the Mac, or cloud when policy allows.
4. Validation has to happen in the client anyway.

### 6.1 Contract
The input is the **canonicalized** text, tokenized by the client into words, each with a code-point span. The LLM edits by **word index**, which is far more reliable for small models than character offsets or free-form find/replace. The client builds the schema per request with `maximum: n_words`, so the grammar itself bounds the indices. Support for `minimum`/`maximum` in llama.cpp's grammar is [U]; the validator below covers it either way.

User message content: `CleanupLLMInput`, serialized as JSON.
```json
{"v": 1, "lang": "en", "app_kind": "terminal",
 "style": {"fillers": "remove", "punctuation": "fix", "casing": "keep", "self_corrections": "apply", "numbers": "keep"},
 "words": ["so", "um", "run", "kubectl", "get", "pods", "actually", "get", "nodes"],
 "protected": [3]}
```
The response must match `CleanupLLMOutput` (constrained):
```json
{"edits": [
  {"from": 0, "to": 2, "expect": "so um", "replace": "", "reason": "filler"},
  {"from": 4, "to": 7, "expect": "get pods actually", "replace": "", "reason": "self_correction"}
]}
```
The system prompt is a fixed, versioned text (`hark-cleanup-v1`). Temperature is 0. `max_tokens` = 64 + 24 × `max_edits`. The client sets the deadline (default 300 ms after the request is sent) and falls back to the raw text when it passes.

### 6.2 Client validation (authoritative; any failure drops that edit, global failures drop all)
1. The response is valid JSON and matches the schema. Otherwise drop **all** edits.
2. `0 ≤ from ≤ to ≤ n`. `from == to` (an insertion) is allowed only for `punctuation` and `formatting`.
3. `expect` must equal the original words `[from,to)` joined with single spaces. This catches the model pointing at the wrong words.
4. Edits must not overlap one another. Process them sorted and drop later overlapping edits.
5. `[from,to)` must not intersect `protected`, and an insertion point must not sit strictly inside a protected run. The client marks as protected: identifiers, paths, URLs, numbers, negations (`not`, `n't`, `never`, `no` as a determiner), canonicalized bias terms, and anything inside code fences.
6. Per-`reason` checks:
   - `filler`: `replace == ""`.
   - `casing`: lowercase(replace) == lowercase(expect).
   - `punctuation`: the letters and digits of replace and expect are identical.
   - `spacing`: identical once whitespace is removed.
   - `self_correction`: deletion only.
   - `grammar`: character edit distance ≤ max(3, 0.3·|expect|).
7. `replace` may not introduce digits, negation tokens or newlines unless `app_kind` allows them.
8. Global: at most `max_edits` [8] edits, and changed characters ≤ `max_change_ratio` [0.3] × |text|. Otherwise drop **all** edits; the model is probably hallucinating.
9. Apply edits right to left on the code-point spans. Keep `CleanupResolvedEdit[] {start,end,before,after,reason}` in history so undo and swap-to-raw are exact.

---

## 7. Latency

### 7.1 Budget, key-up → pasted (LAN, RTX 3060, Parakeet-TDT + GPU-PB)
| Stage | Target p95 | Notes |
|---|---|---|
| key-up → last frame + `audio.end` written | ≤ 25 ms | flush the partial frame (≤ 20 ms); control goes ahead of audio |
| network, one way (LAN or direct tailnet) | ≤ 5 ms [U] | a DERP-relayed path can add tens of ms [U]; the registry should then prefer local CPU |
| server: wait for remaining samples + finalize decode | ≤ 200 ms [U] | `rolling` mode has already decoded most of the audio; an eager hit cuts this to ≈ 0 |
| anchor (shared encoder, second greedy pass) | ≤ 30 ms [U] | dropped first if over the deadline |
| `final` → client | ≤ 5 ms | |
| client gating + canonicalization | ≤ 10 ms | |
| cleanup (optional) | ≤ 300 ms (hard deadline) | otherwise the raw text is used |
| paste (Wayland clipboard + key synthesis) | ≤ 60 ms [U] | |
| **Total** | **≈ 330 ms without cleanup, ≈ 650 ms with** [U] | leaves ≥ 350 ms headroom under 1 s |

Qwen3-ASR 1.7B in `buffered` mode is expected to finalize in 300–700 ms [U]. On that backend, eager finalize matters most and cleanup usually has to be skipped.

### 7.2 Protocol mechanisms that save time
1. **Warm connection.** The connection opens at client start, is pinged every 15 s, and reconnects with backoff on network change. On key-down with no live connection, the client captures into its local buffer while connecting in parallel, then sends the audio in a burst.
2. **No round trip at start.** The client sends `session.start` and audio together. The server accepts frames before it has acknowledged the session.
3. **Context never blocks.** Cheap context goes into `session.start`, the rest into `context.update`.
4. **Upload overlaps speech.** Even `buffered` models receive audio while the user talks, so only compute remains after key-up.
5. **Eager finalize.** Speculative decoding during pauses while the key is held. With a single user the GPU has spare capacity, so the cost is mostly wasted work, not added latency [U].
6. **Deadline-driven omission.** Optional extras are dropped instead of making the client wait.
7. **TCP_NODELAY on both ends.** 20 ms frames combined with Nagle's algorithm and delayed ACKs can stall for tens of ms [U]. In tokio-tungstenite, set `disable_nagle=true` in `connect_async_tls_with_config` [U: verify signature in the pinned version]. On the Python side, confirm that asyncio/uvicorn set NODELAY [U].
8. **Hedging.** If no `final` has arrived `hedge_after_ms` after key-up (default 450 ms [U]), the client starts the local CPU fallback on the buffered audio. The first valid `final` wins and the other session gets `session.cancel{reason:"superseded"}`. If the slower result is better, it can be offered as a "swap".

---

## 8. Backpressure and flow control

- **Audio is a fixed 32 KB/s for PCM** (about 4 KB/s for Opus), so the real problem is a stalled server, not too much data.
- **Server.** One reader task per connection reads frames continuously and **never blocks on inference**. Audio goes into a per-session buffer, bounded by `max_utterance_ms` (5 min of PCM ≈ 9.6 MB). Decoding runs in a separate worker (a GPU thread or process with a batching scheduler). Control messages, including `session.cancel`, are therefore never stuck behind inference. If the hard memory cap is hit, the session gets `session.end{failed, audio_overflow}`. The server deliberately does not stop reading the socket, because that would also block control messages.
- **Outbound from the server.** A single writer task drains a bounded queue. Under pressure, partials are coalesced: only the newest `rev` per sid is kept. `final` and `session.end` are never dropped.
- **Client.** The audio capture thread writes into a lock-free ring buffer and never blocks. A network task sends from the ring. The writer uses a biased `select!` that prefers the control channel over the audio channel; this is safe because of `total_samples` (§2.5).
- **Stall detection** (client):
  - `sent_pos − ack.received > 2 s`: the network or server is stalled.
  - `ack.received − ack.decoded > 3 s`: the server is falling behind.
  
  Either one marks the provider degraded for routing. If it happens during an utterance, the client hedges immediately.
- **Admission control.** The server rejects `session.start` with `overloaded` and `retry_after_ms` *before* accepting audio. The client then routes to the next provider in the chain without losing audio, because it holds the audio locally.
- **Faster than real time.** Servers MUST accept bursts (pre-roll, resume, failover). Flow control is plain TCP. The Python `websockets` `write_limit` (32 KiB by default [S18]) and tungstenite's write buffer provide natural backpressure for the sender.

---

## 9. Cancellation

- `session.cancel{sid}` is idempotent and always ends with `session.end{status:"cancelled"}`, unless a `final` was already sent, which is then the terminal message.
- Server behaviour:
  1. Mark the session cancelled. Any result produced afterwards is discarded.
  2. Free the buffers.
  3. Remove the session from the batch scheduler at the next chunk boundary. GPU kernels cannot be interrupted, so this is bounded by one decode step, ≤ 100 ms [U].
  4. Release bias caches according to LRU.
- The client ignores any message for a sid after it has sent cancel, apart from the terminal message. It does not reuse that sid until the terminal message arrives. The 16-bit sid space makes this cheap.
- Closing the connection does **not** cancel a session immediately; it becomes ORPHANED (§10). The client cancels explicitly when it actually means to cancel.

---

## 10. Reconnect, resume, idempotency, failover

- **The client holds the audio.** All of the utterance's audio stays in memory: 60 s of PCM16 is 1.9 MB. The client needs it anyway for history, re-transcription and undo.
- **Resume on the same server.**
  1. After a drop, the client reconnects. If `welcome.server.boot_id` is unchanged, it sends `session.start{resume:true}` with the same `utterance_id`, a fresh `sid`, and the same bias and context.
  2. The server looks up `(principal, utterance_id)`:
     - **Orphaned session alive** (within `resume_window_ms`): `session.started{resumed:true, received:N}`. The client resends from `pos=N`; overlap is trimmed. If `audio.end` had already been sent, the client sends it again.
     - **Final cached** (within `result_cache_ms`, same request hash): `session.started{resumed:true}` followed straight away by `final{flags:["replayed"]}`.
     - **Nothing found**: `session.started{resumed:false, received:0}`, and the client resends everything.
  3. The request hash covers model, audio format, `bias.list_id` and version, the context hash, and the `output` options. If the bias changed, the server computes a new result.
- **Failover to another server or provider.** The client sends a new `session.start` with the same `utterance_id`, `attempt+1`, `resume:false`, and bursts the whole buffer. Third-party adapters do the same through their own protocol.
- **Idempotent paste.** The client keeps `utterance_id → pasted?`. A late or duplicate `final` never causes a second paste. It can only be offered as a swap.
- **Reconnect policy.** Jittered exponential backoff (100 ms → 5 s). Reconnect immediately on network-change events (NetworkManager or Tailscale status) and on hotkey press.

---

## 11. Health, warmup, load reporting

- **At boot,** the server loads each configured model and runs warmup utterances of 1 s of silence and 2 s of noise. These cover the biased path (one sample bias list, which also warms the cache) and the unbiased path, and both partial and final paths. CUDA graphs are captured during this step, since NeMo uses them by default for `greedy_batch` [S8-nightly]. `/readyz` returns 200 only after this completes.
- **Model state is pushed** with `server.notice{model_state}` on transitions. Examples: the desktop GPU is borrowed for gaming or training (`suspended`), or the Mac unloads a model when idle to free unified memory (`cold`). The client's `warmup` message lets it pre-load on demand; for example, it can send it on hotkey-down while the local fallback is racing.
- **Routing inputs.**
  - `pong.load.est_finalize_ms` and `welcome.models[].perf` (rolling p50/p95 finalize time measured by the server).
  - Client-measured RTT from `ping`.
  - `/readyz` polled every 30 s by the registry. `/v1/info` is re-fetched when `boot_id` changes.
- **Liveness.** WebSocket pings in both directions (uvicorn and `websockets` default to 20 s intervals with 20 s timeouts [S18][S19]). The client pings every 15 s. The server closes connections idle for 120 s with no sessions; the client reopens them lazily.

---

## 12. Security and privacy

### 12.1 Authentication (defence in depth)
1. **Network.** The server binds only to its Tailscale address or to `127.0.0.1` behind `tailscale serve`. `tailscale serve` adds automatic HTTPS for `*.ts.net`, proxies HTTP and WebSocket, and injects `Tailscale-User-Login` for user-owned devices. It strips any client-supplied `Tailscale-*` headers, but it gives no identity headers for **tagged** devices [S20]. Optionally, the server checks the peer's node identity through the LocalAPI `GET /localapi/v0/whois?addr=` [S21] against an allowlist of nodes.
2. **Bearer token (required).** `Authorization: Bearer hark_<32 random bytes, base64url>` on the upgrade request.
   - Tokens are issued per device and carry scopes: `asr`, `asr:context` (allowed to send context text; a less-trusted device can get a token without it), `cleanup`, `admin`.
   - The server stores SHA-256 hashes and compares in constant time.
   - Tokens are **never put in query strings**: NIM rejects URL tokens because they end up in proxy logs [S6]. Tokens are never logged.
   - A missing or invalid token is refused at the upgrade (HTTP 401 or 403, depending on the framework), before any WebSocket exists.
3. **mTLS (optional, recommended outside the tailnet).** For example, the university H100 host if it is not on Tailscale. Uvicorn: `--ssl-certfile … --ssl-keyfile … --ssl-ca-certs client-ca.pem --ssl-cert-reqs 2` [S19]. Rust client: rustls with a client certificate from a private CA. The bearer token stays as a second factor and carries the scopes.
4. **Backends that are plaintext by design** stay on loopback behind the hark server. `riva_server` uses plaintext gRPC credentials [S10].

### 12.2 Privacy
- `session.start.privacy`:
  - `retention`:
    - `none` (default): no audio, text or context is persisted anywhere; only counters are kept.
    - `metrics`: timings and sizes, no content.
    - `debug`: content may be kept for at most the TTL the server advertises; used for dataset building, opt-in only.
  - `allow_external`: false means the server MUST NOT forward audio, text or context to anything outside its own host. For example, a hark gateway that proxies to Deepgram must reject the session with `privacy_unsatisfiable`.
  - `sensitive`: the session carries screen or code text. The server keeps it in memory only, never writes it to logs or traces, and drops it at the terminal message.
- `welcome.privacy` advertises what the server can honour. The **client fails closed**: if the registry policy for a provider requires something the server does not advertise, the client sends no `context`, and no `bias` either if the policy says so.
- Logs contain message `type`, sizes, ids and timings. Content appears only with `retention:"debug"`. The `hark.v1.json` debug dump in the server redacts `context.items[].text`, `bias.terms[].canonical/spoken`, and all text fields unless the session is `debug`.

---

## 13. Audio codec

| | PCM s16le 16 kHz mono (**default**) | Opus (optional) |
|---|---|---|
| Bitrate | 256 kb/s | 24–32 kb/s recommended [U]; RFC sweet spot for WB speech is 16–20 kb/s [S22] |
| Client CPU | none | small (libopus, `audiopus` crate [S28]) |
| Server | used directly | decoded to PCM before the backend (`opuslib` [S28]) |
| ASR effect | none | **measurable**: 16 kb/s Opus raised WER by 12.6 % relative in one (multichannel) study [S23] |
| Backend compatibility | everything; NeMo-Speech.cpp gRPC and NIM realtime accept **only** PCM16 [S9][S6] | needs decoding in the hark server |

- **Policy.** Use PCM on the LAN and on direct tailnet links. Opus is for metered or slow uplinks, such as a phone hotspot or a DERP relay. The client chooses automatically (measured throughput below 1 Mb/s, or a config setting) and only enables it after our eval confirms ≤ 1 % relative WER loss at the chosen bitrate [U].
- **Opus parameters.** 16 kHz, mono, `OPUS_APPLICATION_VOIP`, 20 ms frames (320 samples [S22]), one raw packet per audio frame. FEC is off (TCP is reliable) and DTX is off (contiguity matters).
- **Sample rate.** 16 kHz end to end, because every target model consumes 16 kHz. The client resamples from 48 kHz capture. Adapters resample again where a provider needs it (OpenAI examples use 24 kHz [S1]).

---

## 14. Backend mapping (hark server side)

The Python reference server hosts backends behind one small interface:
```python
class Backend(Protocol):
    caps: ModelCaps  # → welcome.models[]

    async def open(self, start: SessionStart) -> "BackendSession": ...
    async def warmup(self) -> None: ...


class BackendSession(Protocol):
    def push(
        self, pcm16k: np.ndarray, pos: int
    ) -> None: ...  # contiguous, already decoded from Opus
    def update(self, bias: BiasSet | None, ctx: Context | None) -> None: ...
    async def partial(self) -> PartialResult | None: ...  # called every partial_interval_ms
    async def eager(self, speech_end: int) -> None: ...  # speculative finalize (optional)
    async def finalize(self, deadline: float, want: Output) -> FinalResult: ...
    def cancel(self) -> None: ...
```
The server handles framing, auth, privacy, Opus decoding, acks, deadlines, the anchor/N-best omission policy, bias-hit string matching, and echo detection. Backends deal only in PCM, bias and context.

### 14.1 Summary table
| Backend | Streaming | Bias mechanism → mapping | Free text | N-best | Word timings / conf | Anchor | Late ctx |
|---|---|---|---|---|---|---|---|
| **NeMo GPU-PB** (Python; Parakeet-TDT 0.6B v2/v3, cache-aware RNNT) | `rolling` (TDT) / `native` (cache-aware) | per-stream GPU-PB, greedy label-looping TDT/RNNT [S7] → phrases = spoken ∪ (canonical if `boost_canonical`); weights `threshold` (per-phrase weight in GPU-PB [U]); `cache_key = list_id` [S7] | ✗ | ✗ with per-stream bias (greedy only [S7]) → `biased_plus_anchor` | native TDT / raw (entropy-based word confidence [S8-nightly]) | `shared_encoder` [U] | `until_end` |
| **NeMo-Speech.cpp** (`riva_server` gRPC or `nemo-speech serve` WS) behind the hark gateway | `native` (cache-aware RNNT, buffered CTC); offline TDT has no streaming endpointing [S9] | `speech_contexts[{phrases, boost}]`; **works only for flashlight CTC and cache-aware RNNT; TDT and greedy CTC ignore it** [S9] → weights `bucketed` into ≤ 3 contexts; RNNT boost 1.5–4 (typical 2–3, cap 5), CTC 6–10 (cap 10) [S9] | ✗ (`prompt` = one phrase at boost 10 [S11]) | ✗ (not implemented [S9]) | `frame_grid` 80 ms [S9] / RNNT **`constant` 1.0**, CTC greedy mean posterior [S9] | `parallel_decode` (second stream without contexts) | `before_audio` (gateway buffers) |
| **Qwen3-ASR** (vLLM / transformers) | `buffered` (+ eager); streaming exists but is vLLM-only, re-feeds all audio every 2 s, no timestamps, no batching [S12] | `prompt`: context is the **system message** [S12]; rendered as `Vocabulary: kubectl (said "cube control"), Parakeet, …` + context items; weights `threshold` (order/truncate) | ✓ (echo risk) | ✗ (sampling n>1 possible [U]) | `aligned` via Qwen3-ForcedAligner (≤ 5 min [S12]; extra latency [U]) / `none` (logprobs [U]) | `parallel_decode`, batched in vLLM [U cost] | `until_end` |
| **FluidAudio** (Swift, CoreML/ANE; Mac) | `rolling` (SlidingWindowAsrManager [S13]) | `rescoring`: CTC-WS over TDT output; `CustomVocabularyTerm(text: canonical, aliases: spoken)` [S13]; weights `ignored` (global `cbw` [S13]) | ✗ | ✗ | native `tokenTimings` / raw [U per-word] | `pre_rescore` (free) | `until_end` |
| **MLX** (parakeet-mlx, Python; Mac) | `buffered`/`rolling` (streaming API is still a TODO [S14]) | `none` (or add CTC-WS rescoring later [U]) | ✗ | ✗ | native token timestamps [S14] / `none` [U] | `none` (= primary) | n/a |
| **Local CPU** (laptop, in-process) | `rolling` | sherpa-onnx transducer hotwords with `modified_beam_search` and **per-phrase `:score`** [S15] → weights `per_term`, score = 1.0 + 2.5·w [U tuning] | ✗ | from beam [U] | native [U] / raw [U] | `parallel_decode` (second recognizer stream without hotwords) [U cost on CPU] | `until_end` (buffered decode) |

### 14.2 Notes per backend
- **NeMo GPU-PB.**
  - Build one `BiasingRequestItemConfig(boosting_model_cfg=BoostingTreeModelConfig(key_phrases_list=…), cache_key=list_id)` per session and register it with the decoder's multi-model.
  - Pass `multi_biasing_ids[i]` per batch row [S7]. The doc shows the flow; the exact keyword names should be checked against the pinned NeMo version.
  - **Anchor:** duplicate the encoder output into a second batch row with biasing id `-1`. The greedy decode is cheap compared with the encoder [U].
  - Cased models need capitalized key phrases [S8]. The server applies `class`-aware casing to spoken forms (`identifier` stays as-is; `name`/`product` are title-cased) [U policy].
  - Per-stream biasing is greedy-only [S7], so true beam N-best is not available while biasing per stream. In that case `nbest` = [biased, anchor] and `quality.nbest="biased_plus_anchor"`.
  - **Rolling mode for TDT** (an offline model): re-decode the whole buffer every `partial_interval_ms` while the utterance is under 30 s [U cost]. Final = a full decode at `audio.end`. With `mode:"buffered"`, only the final decode runs.
- **NeMo-Speech.cpp.**
  - The hark Python gateway translates hark ↔ `StreamingRecognize`: `RecognitionConfig.speech_contexts`, `enable_word_time_offsets`, `interim_results` [S27].
  - `runtime_config["force_eou"]="true"` finalizes the audio received so far [S9], which is how eager-end is implemented.
  - The gateway decodes Opus, because the backend is PCM-only [S9].
  - Because biasing is ignored for Parakeet TDT here [S9], a TDT model on this backend must advertise `bias.mode:"none"`. The client then routes context-heavy dictation to GPU-PB or Qwen. This exact gap is why capabilities must be advertised per model, not per server.
  - Alternatively, `nemo-speech serve`'s own WebSocket (`/v1/audio/transcriptions/realtime`) already takes **binary PCM16 frames + JSON `session.update` with `speech_contexts`** [S11]. That is close enough to hark that the gateway can use it instead of gRPC.
- **Qwen3-ASR.**
  - Template (`qwen-ctx-v1`, hashed into `prompt_hash`): the context items, sorted by weight, followed by `Vocabulary:` with the terms. Truncated to `context_text.max_chars`.
  - Anchor = a second request with an empty system message, submitted together with the main one so vLLM batches them [U].
  - Echo detection is server-side (`flags: context_echo_suspected`); the client also checks against the anchor.
  - Via vLLM's OpenAI transcription endpoint, `prompt` maps to the system turn [S29], which allows a thin batch backend.
- **FluidAudio.**
  - Run a small Swift server (e.g. Hummingbird or Vapor WebSocket [U]) that implements the mandatory `hark.v1.json` with Codable only. msgpack is optional and can come later.
  - `rescore()` returns `replacements` and `detectedTerms` [S13], which map to `bias_hits` with `evidence:"spotter"`. The pre-rescore TDT text is the anchor, at no cost.
  - FluidAudio's own docs report that its default thresholds replaced **correctly heard** words in dictation clips (`twist`→`Tuist`, `Xcode`→`Codex`) [S13]. This is concrete evidence for anchor-based gating in the client.
- **Local CPU in-process.**
  - **No wire format.** The Rust client defines `trait Recognizer { fn caps(); async fn start(SessionStart) -> SessionHandle }`. `SessionHandle` has `push(AudioFrame)`, `update(ContextUpdate)`, `eager_end`, `end(AudioEnd)`, `cancel`, and an event stream of the same `ServerMessage` enum.
  - The WebSocket client, every third-party adapter, and the in-process sherpa-onnx engine all implement this trait, so routing, gating and fusion are written once.
  - If crash or memory isolation is wanted later, the local engine can run as a daemon speaking `hark.v1.msgpack` over a Unix-socket WebSocket without code changes.

---

## 15. Third-party adapters (client side, implementing `Recognizer`)

| Provider | Transport and audio | Bias / aliases / weights | Free text | Late update | Words / conf / N-best | Finalize |
|---|---|---|---|---|---|---|
| OpenAI Realtime transcription (`gpt-live-transcribe`) | WS JSON, `input_audio_buffer.append` **base64** PCM, example uses 24 kHz [S1] | `keywords[]` (no `<`, `>`, CR/LF) [S1]; aliases → extra keywords; weights ✗ | `prompt` [S1] | `session.update` during the session [S1] | ✗ timestamps, ✗ confidence [S1] → no acoustic gating | `input_audio_buffer.commit` → `…transcription.completed` [S1] |
| Deepgram Nova-3 | WS, **binary audio** + JSON control [S2] | `keyterm` (query param, repeated; 500-token limit) [S2]; weights ✗; aliases → extra keyterms | ✗ | `Configure{keyterms}` replaces the list [S2] | words with start/end/confidence, alternatives [S2] | `Finalize` → `from_finalize:true`; `CloseStream` [S2] |
| Mistral Voxtral realtime | WS JSON; `pcm_s16le` 16 kHz default [S4] | `context_bias[]` strings (terms *or* descriptions) [S5]; weights ✗ | as entries [S5] | **locked at the first audio chunk** [S4] → adapter holds audio ≤ `context_grace_ms` (150 ms) | text deltas only, append-only [S4] | `input_audio.end` → `transcription.done` [S4] |
| Speechmatics RT | WS, binary `AddAudio`, `AudioAdded{seq_no}` acks [S3] | `additional_vocab[{content, sounds_like[]}]` [S3] → **direct match for canonical + spoken**; weights ✗ | ✗ | `SetRecognitionConfig` (limited fields) [S3] | words with timings/confidence [S3] | `EndOfStream{last_seq_no}` → `EndOfTranscript`; `ForceEndOfUtterance` [S3] |
| NVIDIA NIM realtime (HTTP WS) | `POST /v1/realtime/transcription_sessions` then WS `?intent=transcription`; base64 pcm16 [S6] | `word_boosting.word_boosting_list` [S6]; weights ✗ | `prompt` [S6] | `transcription_session.update` [S6] | `words_info` with timings/confidence; `max_alternatives` [S6] | `input_audio_buffer.commit`/`done` [S6]; token in subprotocol `realtime-token.<t>` [S6] |
| Riva gRPC (NIM / riva_server) | bidi stream, PCM | `speech_contexts{phrases, boost}` [S27] → bucketed weights | ✗ | ✗ (config in the first message) | `max_alternatives`, word time offsets [S27] (riva_server: 1 alternative [S9]) | half-close / `force_eou` [S9] |
| OpenAI-compatible batch transcription (vLLM Qwen3-ASR, NeMo-Speech.cpp HTTP) | multipart upload at `audio.end` | vLLM: `prompt` → system turn [S29]; NeMo-Speech.cpp: `speech_contexts` form field [S11] | `prompt` | n/a | `verbose_json` words [S11] | single request |

Adapters fill `final.quality` honestly, e.g. `word_confidence:"none"` for OpenAI. The client's fusion policy reads `quality`: with no timings or confidence it falls back to text-only canonicalization with stricter thresholds, and by default it does not send context to such providers.

---

## 16. Implementation sketches

### 16.1 Python server core (Starlette/FastAPI)
```python
import json, struct, msgpack
from starlette.websockets import WebSocket, WebSocketDisconnect

SUBPROTOCOLS = ("hark.v1.msgpack", "hark.v1.json")  # server preference order
AUDIO, CTRL = 0x02, 0x01
HDR = struct.Struct(">BBHI")  # kind, flags, sid, pos  (8 bytes)


@app.websocket("/v1/stream")
async def stream(ws: WebSocket):
    principal = auth.verify(ws.headers.get("authorization"))
    offered = ws.scope.get("subprotocols", [])
    proto = next((p for p in SUBPROTOCOLS if p in offered), None)
    if principal is None or proto is None:
        await ws.close(code=1008)  # before accept → upgrade refused (HTTP 403)
        return
    await ws.accept(subprotocol=proto)
    binary = proto.endswith("msgpack")
    conn = Connection(ws, principal, binary)  # owns: sessions{sid}, out-queue, writer task
    conn.start_writer()
    await conn.send(welcome_msg(binary))
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            if (b := m.get("bytes")) is not None:
                if b and b[0] == AUDIO and len(b) >= HDR.size:
                    _, flags, sid, pos = HDR.unpack_from(b)
                    conn.on_audio(sid, pos, flags, memoryview(b)[HDR.size :])  # non-blocking append
                elif b and b[0] == CTRL and binary:
                    conn.on_ctrl(msgpack.unpackb(b[1:], raw=False))
                else:
                    conn.warn("bad_frame")
            elif (t := m.get("text")) is not None and not binary:
                conn.on_ctrl(json.loads(t))
            else:
                await conn.fatal("protocol_error")
                break
    except WebSocketDisconnect:
        pass
    finally:
        conn.orphan_live_sessions()  # resume window (§10)
```
`on_ctrl` validates against the schema in debug and test builds (`jsonschema`); production builds use pydantic models generated from the schema [U tooling: datamodel-code-generator]. Inference runs in one GPU worker with a micro-batcher (5–10 ms window) so concurrent sessions can share batches, with per-row bias ids as GPU-PB allows [S7].

### 16.2 Rust client essentials (tokio-tungstenite + serde)
```rust
#[derive(Serialize, Deserialize)]
#[serde(tag = "type")]
enum ClientMsg {
    #[serde(rename = "session.start")]  SessionStart(SessionStart),
    #[serde(rename = "context.update")] ContextUpdate(ContextUpdate),
    #[serde(rename = "audio.eager_end")] EagerEnd { sid: u16, speech_end: u32 },
    #[serde(rename = "audio.end")]      AudioEnd { sid: u16, total_samples: u32, speech_end: Option<u32>, reason: EndReason },
    #[serde(rename = "session.cancel")] Cancel { sid: u16, reason: CancelReason },
    #[serde(rename = "ping")]           Ping { id: u64, t_ms: u64 },
    #[serde(rename = "warmup")]         Warmup { id: u64, model: String },
}

fn encode_ctrl(m: &ClientMsg, binary: bool) -> Message {
    if binary {
        let mut v = vec![0x01];
        v.extend(rmp_serde::to_vec_named(m).unwrap());   // _named: maps, not arrays [S17]
        Message::Binary(v.into())
    } else {
        Message::Text(serde_json::to_string(m).unwrap().into())
    }
}

fn encode_audio(sid: u16, pos: u32, flags: u8, payload: &[u8]) -> Message {
    let mut v = Vec::with_capacity(8 + payload.len());
    v.push(0x02);
    v.push(flags);
    v.extend_from_slice(&sid.to_be_bytes());
    v.extend_from_slice(&pos.to_be_bytes());
    v.extend_from_slice(payload);
    Message::Binary(v.into())
}

// connect: add Authorization + subprotocols to the request; disable Nagle.
let mut req = url.into_client_request()?;
req.headers_mut().insert(AUTHORIZATION, format!("Bearer {token}").parse()?);
req.headers_mut().insert(SEC_WEBSOCKET_PROTOCOL, "hark.v1.msgpack, hark.v1.json".parse()?);
// writer task: tokio::select! { biased; Some(c) = ctrl_rx.recv() => …, Some(a) = audio_rx.recv() => … }
```
Note: `Option` fields must use `#[serde(skip_serializing_if = "Option::is_none")]`. Otherwise `None` is written as `nil`/`null`, and the strict sender schema rejects it.

### 16.3 Debug tooling
- `hark-cat` (Python, about 150 LOC): `hark-cat --json wss://desktop.tailnet.ts.net/v1/stream --wav sample.wav --bias terms.yaml --realtime`. It prints every event as JSON lines and replays at real-time or burst speed.
- Server flag `--dump-ctrl`: writes a JSONL log of control messages, redacted per §12.2. Audio is logged only as `{sid,pos,len}`.

---

## 17. Conformance and testing

- **The schema is the source of truth.** CI validates every message the reference server and the Rust client emit, in both encodings, using `jsonschema` (Python) and the `jsonschema` crate (Rust tests).
- **Scenario files** (`conformance/*.jsonl`) contain scripted client actions and expected server events, with a `scripted` backend that returns fixed hypotheses and timings. Scenarios:
  - happy path
  - pipelined audio before `session.started`
  - late `context.update` (accepted and rejected)
  - eager hit and eager miss
  - cancel during finalize
  - resume from an orphaned session
  - replay from the result cache
  - overload rejection
  - unknown fields and unknown message types
  - DISCONTINUITY gap
  - oversize messages
  - deadline-driven omission
  
  Servers that only implement `hark.v1.json` run the same scenarios in that encoding.
- **Fuzz** the frame parser (header and msgpack) with `cargo fuzz` and `atheris` [U tooling].
- **Latency harness.** Play recorded utterances in real time and measure key-up→`final` per backend, with and without the anchor and eager-end. This harness decides the [U] numbers in §7.
- **Cleanup validator tests.** Adversarial LLM outputs: wrong `expect`, overlaps, edits touching protected spans, digit injection, deletion of a negation.

---

## 18. Pros and cons versus gRPC

| Aspect | `hark-ws/1` (this proposal) | gRPC bidi streaming |
|---|---|---|
| Python server | Starlette/FastAPI; same process and port as `/healthz`, `/v1/info`, `/v1/recognize`, the cleanup proxy; no codegen | grpcio asyncio + protoc codegen; health and HTTP endpoints need a second server or grpc-gateway |
| Rust client | tokio-tungstenite + serde; trivial framing | tonic (excellent); prost codegen |
| Swift / Mac server | built-in JSON and WebSocket APIs (`hark.v1.json`) | grpc-swift, a heavier dependency |
| Debuggability | JSON twin, `hark-cat`, websocat for control; human-readable logs | grpcurl/reflection; binary protobuf in logs |
| Schema and types | JSON Schema + conformance tests; types written by hand or generated [U] | `.proto` is the source of truth; strong codegen; well-understood evolution rules |
| Deadlines, cancel, status | defined by us (`deadline_ms`, `session.cancel`, error codes) | built in (deadline propagation, cancellation, status codes) |
| Flow control | TCP plus app-level acks; multiplexing by `sid` | HTTP/2 per-stream flow control; one stream per utterance |
| Head-of-line blocking | one TCP stream | also one TCP stream under HTTP/2 — **no real difference** |
| Audio payload | raw bytes, 8-byte header | `bytes` field in protobuf; about the same efficiency |
| Ecosystem fit | matches how most realtime STT APIs work: Deepgram, Speechmatics, OpenAI, Mistral, NIM realtime, NeMo-Speech.cpp `serve` [S1–S6][S11] | Riva/NIM gRPC and `riva_server` speak it natively [S9][S27], but their proto has no aliases, provenance, anchor, bias hits or quality, so it would need extending anyway |
| Proxies and auth | any HTTP reverse proxy, including `tailscale serve`; bearer in a header; mTLS via uvicorn | needs HTTP/2-capable proxies; per-RPC metadata auth; mTLS is standard |
| Health | `/healthz`, `/readyz` | standard `grpc.health.v1` |

**Verdict.** For one user, a handful of servers, mostly Python on the server side, and a strong need to debug during research, WebSocket costs less to build and operate. Everything gRPC gives for free (deadlines, cancellation, status codes) is a few fields here and is covered by conformance tests. gRPC would be the better choice if there were many server implementations in many languages, or a multi-tenant deployment. To use Riva directly, the gateway translation in §14.2 costs about 200 LOC [U].

---

## 19. Risks and open questions

1. **Two encodings.** This doubles the codec surface, though not the schema. If reviewers prefer a single encoding, go with JSON-only; the frame layout keeps working because text frames carry control and binary frames carry audio.
2. **Mapping weights to backend scales** (§14) has to be tuned per backend on our dataset, using distractor context to measure false substitutions. The mapping functions should be versioned in server config and reported in `final.model.revision` [U].
3. **Cost of the anchor** on Qwen3-ASR (`parallel_decode`) may push finalize time past the deadline. Option: the client asks for `anchor` only when `bias_hits` would be non-empty. This would need a second round trip; postponed to v1.1.
4. **Eager-end waste and correctness.** Speculative decodes on long pauses: cap at 2 in flight per session [U].
5. **Applying `context.update` mid-stream** on `native` streaming backends affects only future chunks unless the server re-decodes. The server should advertise this precisely; it may need a new `late_update` value, `"future_audio"`.
6. **Per-phrase weights in GPU-PB** are unverified [U]. If they are unsupported, `threshold` is the honest capability.
7. **Partial stability (`stable_chars`)** for rolling re-decode needs a heuristic, e.g. the longest common prefix of the last two revisions [U].

---

## 20. Sources (accessed 2026-11-12)
- **S1** OpenAI — Realtime transcription guide (session.update `type:"transcription"`, `input_audio_buffer.append/commit`, `prompt`/`keywords`/`languages`/`delay`, no timestamps or confidence for gpt-live-transcribe). https://platform.openai.com/docs/guides/realtime-transcription
- **S2** Deepgram — Live Audio `/v1/listen` AsyncAPI (binary media; Finalize/CloseStream/KeepAlive/Configure; `keyterm`, 500-token limit; Results with words/confidence, `from_finalize`). https://developers.deepgram.com/reference/speech-to-text/listen-streaming
- **S3** Speechmatics — Realtime API reference (StartRecognition, binary AddAudio, AudioAdded `seq_no`, EndOfStream `last_seq_no`, ForceEndOfUtterance, `additional_vocab{content,sounds_like}`, `max_delay` 0.7–4 s). https://docs.speechmatics.com/rt-api-ref
- **S4** Mistral — Realtime transcription (session.update before first audio, settings locked; `input_audio.append/end`; `transcription.text.delta/done`; `target_streaming_delay_ms`). https://docs.mistral.ai/studio/audio/speech-to-text/transcribe-in-realtime
- **S5** Mistral — Bias context and vocabulary (`context_bias` entries as terms or descriptions; shares the context window). https://docs.mistral.ai/studio/audio/speech-to-text/bias-context-and-vocabulary
- **S6** NVIDIA Speech NIM — Realtime API reference (transcription_sessions, `?intent=transcription`, base64 pcm16, `word_boosting`, `max_alternatives`, `words_info`, ephemeral token in subprotocol, no URL tokens, close codes incl. 1013). https://docs.nvidia.com/nim/speech/latest/reference/api-references/asr/realtime-asr.html
- **S7** NeMo-Speech (nightly) — Word Boosting: per-stream phrase boosting for greedy label-looping TDT/RNNT incl. cache-aware; `biasing_request`; `cache_key` memory caching "near-zero overhead". https://docs.nvidia.com/nemo/speech/nightly/asr/asr_customization/word_boosting.html
- **S8** NeMo Framework 26.02 — Word Boosting (GPU-PB for CTC/RNN-T/TDT/AED; capitalize phrases for cased models; `context_score`, `depth_scaling`, `boosting_tree_alpha`). https://docs.nvidia.com/nemo-framework/user-guide/26.02/nemotoolkit/asr/asr_customization/word_boosting.html — and **[S8-nightly]** ASR LM & customization (CUDA graphs defaults; confidence estimation). https://docs.nvidia.com/nemo/speech/nightly/asr/asr_language_modeling_and_customization.html
- **S9** NVIDIA NeMo-Speech.cpp — ASR configuration (boosting only flashlight CTC and cache-aware RNNT; TDT and greedy CTC ignore it; boost ranges and caps; LINEAR_PCM only; no N-best; RNNT confidence 1.0; 80 ms timestamp grid; `force_eou`). https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/asr/configuration.md
- **S10** NeMo-Speech.cpp — server.md (`serve` vs `riva_server`; riva_server plaintext; health/ready). https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/server.md
- **S11** NeMo-Speech.cpp — api.md (WS `/v1/audio/transcriptions/realtime`: binary PCM16 + JSON `session.update` with `speech_contexts`; HTTP `speech_contexts`, `prompt`=one phrase at boost 10). https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/api.md
- **S12** QwenLM/Qwen3-ASR — README and `qwen_asr/inference/qwen3_asr.py` (context → system message; streaming vLLM-only, re-feeds accumulated audio, 2 s chunks, no timestamps/batching; ForcedAligner ≤ 5 min). https://github.com/QwenLM/Qwen3-ASR
- **S13** FluidAudio — CustomVocabulary.md (CTC-WS rescoring; `CustomVocabularyTerm(text:aliases:)`; `detectedTerms`/`replacements`; default thresholds replaced correct words on dictation clips). https://github.com/FluidInference/FluidAudio/blob/main/Documentation/ASR/CustomVocabulary.md
- **S14** parakeet-mlx on PyPI (timestamps; streaming listed as TODO). https://pypi.org/project/parakeet-mlx/
- **S15** sherpa-onnx — Hotwords (modified_beam_search; per-phrase `:score`). https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html
- **S16** tungstenite-rs issue #2 — permessage-deflate still not upstream (open as of 2026-11). https://github.com/snapview/tungstenite-rs/issues/2
- **S17** rmp-serde Serializer docs — structs as arrays by default; `to_vec_named` / `with_struct_map`. https://docs.rs/rmp-serde/latest/rmp_serde/encode/struct.Serializer.html
- **S18** websockets (Python) — asyncio client/server defaults: `max_size` 1 MiB, `ping_interval`/`ping_timeout` 20 s, `write_limit` 32 KiB. https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html
- **S19** Uvicorn settings — `ws_max_size` 16 MiB, ping 20 s; `--ssl-ca-certs` + `--ssl-cert-reqs 2` for mTLS. https://uvicorn.dev/settings/
- **S20** Tailscale Serve — identity headers (`Tailscale-User-Login`), stripped if client-supplied, not for tagged devices. https://tailscale.com/docs/features/tailscale-serve
- **S21** Tailscale LocalAPI `whois` (`/localapi/v0/whois?addr=`). https://github.com/tailscale/tailscale/blob/main/ipn/localapi/localapi.go
- **S22** RFC 6716 Opus (frame durations; WB speech sweet spot 16–20 kb/s with 20 ms frames). https://www.rfc-editor.org/rfc/rfc6716
- **S23** arXiv 2002.00122 — Multi-channel acoustic modeling using mixed-bitrate Opus (16 kb/s → +12.6 % relative WER). https://arxiv.org/abs/2002.00122
- **S24** llama.cpp server README — `response_format` `json_schema` → grammar. https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- **S25** vLLM — Structured outputs (`response_format` json_schema on the OpenAI-compatible server). https://docs.vllm.ai/en/latest/features/structured_outputs/
- **S26** SGLang — OpenAI-compatible `response_format` json_schema. https://github.com/sgl-project/sglang
- **S27** NVIDIA Riva `riva_asr.proto` (SpeechContext phrases/boost, `max_alternatives`, `enable_word_time_offsets`, `interim_results`). https://docs.nvidia.com/deeplearning/riva/archives/2-17-0/reference/protos/riva_asr.proto.html
- **S28** `opuslib` (Python, raw-packet decoding) https://github.com/orion-labs/opuslib ; `audiopus` (Rust encoder) https://docs.rs/audiopus
- **S29** vLLM — OpenAI speech-to-text example (Qwen3-ASR `prompt` → system turn). https://docs.vllm.ai/en/latest/examples/speech_to_text/openai/
