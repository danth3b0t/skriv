# hark protocol — Proposal A: gRPC + protobuf, bidirectional streaming

Status: proposal, written by Designer A (designed without seeing B or C). Date: 2026-11-12.
Canonical schema: [`proposal-a.proto`](proposal-a.proto). The schema is also reproduced in full in §5.
`[U]` marks claims I could not verify, or numbers that still need measuring. §17 lists them.

---

## 0. Decisions in one screen

| # | Decision |
|---|---|
| D1 | **gRPC over HTTP/2 + protobuf**, package `hark.v1`. Services: `Asr` (`GetCapabilities`, `Transcribe` bidi stream, `Recognize` unary, `Warmup`), `TextCleanup` (`Cleanup` unary) and the standard `grpc.health.v1.Health`. |
| D2 | **One `Transcribe` call is one utterance.** The client opens the stream at **hotkey-down** and streams audio live. After **key-up** it sends only `EndOfAudio`, so the critical path is one RTT plus finalization. |
| D3 | The wire **audio is PCM s16le, 16 kHz, mono, in 40 ms chunks** by default. **Opus** (20 ms packets, about 32 kbit/s) is an optional, negotiated feature for slow links. gRPC compression is off. |
| D4 | **Every context feature goes through capability negotiation.** Each model advertises: bias mechanism (decode-time boost / acoustic rescore / prompt / none), max terms and aliases, whether per-term weights work, free-text context and its echo risk, N-best size, timestamp source, word confidence, anchor cost, streaming mode, formats and languages. `required_features` turns "best effort" into "fail fast". |
| D5 | **Bias terms carry canonical spelling, spoken aliases, a normalized weight in (0,1], kind and provenance.** The server maps the weight onto its own backend scale and reports the mapping. `content_hash` lets the server cache compiled bias artifacts (e.g. the NeMo `cache_key`). |
| D6 | **`FinalResult` always contains `n_best[0]`.** It can also carry N-best entries, word timings and confidences, `bias_hits` (term, span, decision, scores, and what the unbiased decode heard over the same span), an **unbiased `anchor` hypothesis**, a timing breakdown, degradation flags, and a privacy acknowledgement. |
| D7 | **A finalize budget is a protocol field** (`finalize_budget_ms`), separate from the gRPC deadline. The server drops optional work (N-best, then anchor, then alignment) to meet it and says so in `ResultFlags`. |
| D8 | **Retries are client-driven, keyed by `utterance_id`.** The client keeps the full utterance audio in RAM, replays it to the same or the next provider (`attempt+1`), can hedge with `Recognize`, and pastes each `utterance_id` at most once. The server keeps an optional RAM-only replay cache. |
| D9 | **Cleanup is a separate unary gRPC RPC** that returns an edit list (`find/replace/kind/reason` resolved to byte ranges). The hark server implements it as an adapter over a localhost OpenAI-compatible LLM server using a JSON-schema constrained response. The client also has a direct OpenAI-compatible adapter that uses the same schema. **The client-side validator is always authoritative.** |
| D10 | **Security: mTLS by default** with a private CA and per-device client certs. A bearer token is optional as a second factor or behind a TLS-terminating proxy. Tailscale is the network layer, not the auth layer. All privacy flags default to the most private value. |
| D11 | **Libraries.** Rust client: `tonic` 0.14 + `tonic-prost`/`prost`. Python servers: `grpcio` (`grpc.aio`). Mac FluidAudio server: `grpc-swift-2`. Schema CI: `buf lint` + `buf breaking`. |
| D12 | **The proto types are also hark's internal lingua franca.** Third-party APIs (OpenAI, Deepgram, Voxtral, Riva/NeMo-Speech.cpp) and the in-process CPU fallback all implement one Rust `SpeechProvider` trait that consumes and produces `hark.v1` messages. Recordings are stored as length-delimited `hark.v1` messages (`.harkrec`), so the eval dataset format *is* the protocol. |

---

## 1. Goals and non-goals

**Goals**
1. Key-up → pasted **≤ 1 s p95 on LAN**, including optional cleanup. Graceful over Tailscale, including DERP relays.
2. **Inject context into decoding** (decode-time boosting, prompt conditioning) and return enough evidence for the client to gate it: anchor, per-term hits, word timings and confidences, N-best. The client must be able to fuse and canonicalize the same way regardless of backend.
3. One protocol for heterogeneous backends: NeMo GPU-PB (Python), NeMo-Speech.cpp (Riva gRPC), Qwen3-ASR (prompt context), FluidAudio (Swift, CTC rescoring), MLX, an in-process CPU fallback and third-party APIs. Differences are expressed through capabilities, not forks.
4. Explicit privacy semantics: what may be sent, what may be retained, and an acknowledgement of what was retained.
5. Evolvable without lockstep upgrades. The laptop client and three servers will drift.
6. Directly usable as a recording and evaluation format.

**Non-goals**
- Browser clients. gRPC bidi streaming does not work in browsers and hark has no browser UI.
- Multi-tenant or public serving, billing, quotas.
- Long-form transcription (meetings) or diarization. The protocol caps utterances at `max_audio_ms`, which defaults to 5 min.
- Multi-utterance sessions on one stream. HTTP/2 streams are cheap, and one call per utterance gives clean cancel and retry semantics.
- Server-side context gathering. Context is collected, ranked and redacted on the client only.
- Chaining cleanup automatically on the server after ASR. Cleanup has to run *after* client fusion and canonicalization, so the client calls it explicitly (§8).

---

## 2. Transport and connection model

- **gRPC over HTTP/2 with TLS 1.3 (mTLS).** One long-lived channel per provider. `Transcribe`, `Recognize`, `Cleanup`, `GetCapabilities` and `Health.Watch` are all multiplexed on it.
- **Keep the connection warm** so TCP and TLS handshakes stay off the critical path:
  - Client: HTTP/2 keepalive PING every 20 s with `keep_alive_while_idle(true)`, and `tcp_nodelay` (tonic's default is `true`).
  - Server (grpcio): `grpc.keepalive_permit_without_calls=1` and `grpc.http2.min_ping_interval_without_data_ms=15000`. Otherwise gRPC core answers frequent pings with `GOAWAY too_many_pings`; core's default minimum is 5 min.
  - The client reconnects proactively on network change and on resume from suspend. If the channel is cold at hotkey-down, connection setup overlaps with speech.
- **Metadata.**
  - Request: `authorization: Bearer <token>` (optional), `hark-client: hark/0.3.1`, `hark-protocol: 1.<minor>`.
  - Response initial metadata: `hark-protocol: 1.<minor>`, `hark-instance: <instance_id>`.
- **Message limits.** `max_receive_message_length` is 32 MiB on servers, for `Recognize` with up to ~15 min of PCM. Streaming chunks are about 1.3 KB.
- **Local transport.** A same-host server (e.g. the Mac dev box running its own server) may listen on a **Unix domain socket**. tonic supports UDS clients via `connect_with_connector`, and grpcio servers accept `unix:` addresses.

### 2.1 Sequence (happy path)

```
client (hotkey-down)                                    server
  |-- HEADERS (POST /hark.v1.Asr/Transcribe) ------------->|
  |-- start{utterance_id, model, ctx rev1, options} ------>|  admission, start bias compile (async)
  |<------------------------------ accepted{applied cfg} --|
  |-- audio seq0..N (40 ms each, live) ------------------->|  buffer / incremental decode
  |-- context_update{rev2} (ranker finished late) -------->|  (if midstream_update != UNSUPPORTED)
  |<------------------------- status / partial (opt.) ----|
(key-up)
  |-- audio seqN+1 (flushed partial chunk) --------------->|
  |-- end_of_audio{last_seq, total_samples, KEY_UP} ------>|  finalize within finalize_budget_ms:
  |-- (half-close) ------------------------------------->  |  encoder, biased decode, anchor, N-best...
  |<------------------------------------- final_result ----|
  |<------------------------------------- status OK -------|
client: fusion → canonicalize → guardrails → [Cleanup RPC] → paste
```

### 2.2 Stream rules (normative)

1. The first client message MUST be `start`; otherwise the server fails with `INVALID_ARGUMENT` / `PROTOCOL_VIOLATION`. The server MUST send `accepted` promptly, without waiting for bias compilation. This also flushes response headers, so `tonic`'s call future resolves.
2. `audio.seq` MUST be contiguous from 0, and `start_sample` MUST equal the sum of previous `num_samples`. On a gap the server zero-fills, emits `warning{AUDIO_GAP}` and sets `flags.audio_gap`.
3. The server MUST accept audio **faster than real time**, because replays after a failover burst the buffered audio.
4. `context_update` may arrive at any time before `end_of_audio`, with strictly increasing `revision`. The server uses the latest revision it received before finalization and reports it as `final_result.context_revision`. If the model's `midstream_update` is `UNSUPPORTED`, the server emits `warning{CONTEXT_IGNORED}`.
5. After `end_of_audio` the client MUST NOT send audio. It may still send `cancel`. Half-close without `end_of_audio` is treated as `end_of_audio{reason=UNSPECIFIED}`.
6. The server sends exactly one `final_result` and then ends with `OK`. On silence it still sends `n_best[0]` with empty text and `flags.no_speech`.
7. While receiving audio, the server sends `status` at least every 500 ms. This doubles as an application-level liveness signal for hedging (§10.4).
8. `partial` is sent only if requested and supported, with strictly increasing `seq`. `stable_bytes` never decreases.

---

## 3. Versioning and capability negotiation

### 3.1 Versioning
- Package `hark.v1`, versioned as major.minor (`ProtocolVersion`). Minor revisions are additive only: new fields, new enum values, new feature strings. Field numbers are never reused; removed fields are `reserved`.
- Every enum has `*_UNSPECIFIED = 0`, and receivers map unknown values to it. prost keeps enums as `i32`, so unknown values survive a decode/re-encode; Python and Swift protobuf also preserve them.
- A breaking change creates `hark.v2`. The server can host v1 and v2 services at once while clients migrate.
- CI runs `buf lint` (STANDARD rules, hence the enum prefixes) and `buf breaking --against .git#branch=main`.
- Version skew rule: the client sends `client_protocol_minor`. The server never *requires* fields newer than that minor, and the client ignores capabilities it does not understand.

### 3.2 Capabilities
`GetCapabilities` returns `Capabilities{protocol, server, models[], cleanup_profiles[], limits, etag, features[]}`. The client caches it per provider in its provider registry. The registry merges it with *client-side* policy (privacy, whether context may be sent, routing order, budgets).

Per model, `ModelCapabilities` answers every question in the brief:

| Question | Field |
|---|---|
| Which models, are they loaded? | `models[].model_id/family/state/is_default`, `Warmup` |
| Bias support, max bias terms | `bias.mechanism`, `bias.max_terms`, `max_aliases_per_term`, `max_term_bytes`, `max_total_tokens`, `per_term_weight`, `aliases_supported`, `case_insensitive`, `hit_scores` |
| Free-text context | `free_text.description/preceding_text/max_bytes/echo_risk` |
| N-best | `n_best.max_n`, `n_best.score_kind`, `bias.bias_with_n_best` |
| Word timings / confidence | `timestamps.source/resolution_ms/word_confidence/confidence_calibrated` |
| Unbiased anchor | `anchor_cost` (FREE / DECODER_ONLY / FULL_PASS / UNAVAILABLE) |
| Languages | `languages[]`, `language_detection` |
| Streaming vs batch | `streaming_mode` (BUFFERED / INCREMENTAL), `partials`, feature `hark.recognize` |
| Mid-utterance context update | `bias.midstream_update` |
| Audio formats | `audio_formats[]` |
| Cleanup endpoints | `cleanup_profiles[]` (+ feature `hark.cleanup`) |
| Routing hints | `perf.rtfx`, `perf.finalize_ms_10s`, `perf.bias_compile_ms` (measured at warmup) |

Rules:
- **Staleness.** `SessionAccepted.capabilities_etag` lets the client notice changes (a model unloaded, a server upgraded) without polling. On mismatch it refetches in the background.
- **Effective configuration.** `AppliedConfig` in `accepted` reports what the server will actually do: terms used and dropped, anchor planned, effective backend parameters such as the weight→boost mapping. The client logs it with the utterance for evaluation.
- **Feature requirements.** A feature listed in `required_features` that cannot be honored fails the call with `FAILED_PRECONDITION`/`REQUIRED_FEATURE_UNAVAILABLE`. Example: the eval harness requiring `result.anchor`. Otherwise the server degrades and flags.
- **Escape hatch.** `backend_params` is namespaced (`nemo.*`, `riva.*`, `qwen.*`, `fluid.*`) for experiments without a schema change. Effective values come back in `AppliedConfig.backend_params`.

---

## 4. Message semantics (beyond the comments in the proto)

### 4.1 Audio
- `AudioFormat{encoding, sample_rate_hz, channels=1, preroll_ms}`.
  - The client resamples capture audio (often 48 kHz) to **16 kHz** before sending. Every target model consumes 16 kHz, and resampling cuts bandwidth and server CPU by 3×.
  - `preroll_ms` declares how much ring-buffer audio from before hotkey-down is included. This avoids clipping the first phoneme. Timestamps include the pre-roll; the client subtracts it.
- PCM chunks are 40 ms (640 samples, 1,280 B). On key-up the client flushes the partial chunk immediately.
- Opus chunks carry **exactly one raw Opus packet** (20 ms) plus `num_samples` (decoded size), so gap detection still works.
- `vad_speech_prob` is the client's Silero VAD output. Servers may use it to skip leading silence or as an endpointing hint, but they MUST NOT drop audio based on it.

### 4.2 Language
`languages` is a list of BCP-47 tags. Empty means auto-detect, and `[0]` is the primary language. Servers translate tags to backend vocabularies; Qwen3-ASR, for example, wants names like `"English"`. The result reports `detected_language`.

### 4.3 Deadlines
- **gRPC deadline** (`grpc-timeout`) on `Transcribe` = `max_audio_ms + finalize_budget_ms + 2 s`. It is a safety net against hung calls only.
- **`finalize_budget_ms`** is the real latency contract. The client computes it as `p95 target − (client fusion + paste + cleanup reserve + 1 RTT estimate)`, typically 300–450 ms on LAN. The server sheds optional work in this order: N-best → anchor (only if cost ≥ FULL_PASS) → forced alignment → word confidence. It never sheds the primary decode. Every skip sets a flag.
- `Cleanup` uses the plain gRPC deadline plus a `budget_ms` field for generation.

### 4.4 Privacy flags
All `allow_*` flags default to `false`, i.e. the most private behavior.
- Servers log sizes, hashes, timings and model ids only. They never log audio, text or context unless the matching flag is set.
- `PrivacyAck` in the result reports what was actually done, so the client can detect a misconfigured server (e.g. eval capture left on) and mark the provider untrusted.
- **What context is sent at all is decided client-side** by the provider registry's per-provider policy. The protocol cannot make a server honest; it makes behavior explicit and auditable. If `minimal_metadata` is set, the client has stripped `provenance` and `free_text.description`.
- The replay cache is RAM-only and requires `allow_replay_cache`.

---

## 5. Full `.proto`

Also saved as [`proposal-a.proto`](proposal-a.proto). If the two copies ever disagree, that file is canonical.

```proto
// hark client <-> inference-server protocol — Proposal A (gRPC + protobuf)
// Status: PROPOSAL. Companion document: docs/protocol/proposal-a.md
//
// Conventions (normative)
// - All strings are UTF-8. Every "*_byte_*" offset is a UTF-8 byte offset
//   into the string named in the comment, half-open [start, end).
// - All audio times are integer milliseconds from sample 0 of the utterance
//   (the first sample of the first AudioChunk, i.e. including pre-roll).
// - Scalars whose zero value is meaningful use proto3 `optional`.
// - Every enum's value 0 is *_UNSPECIFIED and means "server default" or
//   "unknown". Receivers MUST treat unknown enum values like UNSPECIFIED.
// - Compatibility: additive changes only within package hark.v1 (bump
//   Capabilities.protocol.minor). Never renumber or reuse a field number;
//   `reserved` removed ones. Breaking changes => package hark.v2, served
//   side by side. Enforced in CI with `buf breaking`.
// - Required-ness is enforced by the receiver's validation code, not by
//   proto (proto3 has no required). Violations => INVALID_ARGUMENT.

syntax = "proto3";

package hark.v1;

// =============================================================================
// Services
// =============================================================================

// Speech recognition. One server process may host several models.
// Also implement grpc.health.v1.Health with service names
// "" (process), "hark.v1.Asr", "hark.v1.TextCleanup".
service Asr {
  // Cheap and cacheable. Call on connect, on reconnect, and whenever
  // SessionAccepted.capabilities_etag differs from the cached etag.
  rpc GetCapabilities(GetCapabilitiesRequest) returns (Capabilities);

  // Primary path. One call == one utterance.
  // Client -> server: start, (audio | context_update)*, end_of_audio | cancel,
  //                   then half-close.
  // Server -> client: accepted, (partial | status | warning)*, final_result,
  //                   then status OK. Fatal errors end the call with a non-OK
  //                   gRPC status carrying ErrorDetail in
  //                   google.rpc.Status.details.
  rpc Transcribe(stream TranscribeRequest) returns (stream TranscribeResponse);

  // Whole-utterance unary variant. Used for replay to a fallback provider,
  // hedged requests, the evaluation harness and batch-only backends.
  // Same semantics as Transcribe with all audio delivered at once.
  rpc Recognize(RecognizeRequest) returns (RecognizeResponse);

  // Make a model resident and warm (it may have been unloaded to free VRAM).
  // Idempotent. Returns when warm or when the call deadline expires.
  rpc Warmup(WarmupRequest) returns (WarmupResponse);
}

// Optional post-ASR cleanup producing a validated edit list.
service TextCleanup {
  rpc Cleanup(CleanupRequest) returns (CleanupResponse);
}

// =============================================================================
// Capability negotiation
// =============================================================================

message GetCapabilitiesRequest {
  string client_version = 1;        // e.g. "hark/0.3.1 (linux-x86_64)"
  uint32 client_protocol_minor = 2; // highest hark.v1 minor the client knows
}

message ProtocolVersion {
  uint32 major = 1; // always 1 in package hark.v1
  uint32 minor = 2; // additive revisions
}

message Capabilities {
  ProtocolVersion protocol = 1;
  ServerInfo server = 2;
  repeated ModelCapabilities models = 3;
  repeated CleanupProfile cleanup_profiles = 4; // empty => no TextCleanup
  ServerLimits limits = 5;
  // Opaque; changes whenever anything in this message changes (model load
  // state included). Echoed in SessionAccepted.
  string etag = 6;
  // Small namespaced boolean features. Unknown strings MUST be ignored.
  // Defined in v1.0: "hark.opus", "hark.partials", "hark.replay_cache",
  // "hark.midstream_context", "hark.recognize", "hark.cleanup".
  repeated string features = 7;
}

message ServerInfo {
  string name = 1;           // user-visible, e.g. "desktop-3060"
  string implementation = 2; // e.g. "hark-server-py/0.2.0 nemo/2.x"
  string accelerator = 3;    // e.g. "RTX 3060 12GB", "Apple M5 Pro", "cpu"
  uint64 started_unix_ms = 4;
  string instance_id = 5;    // random per process start; change => caches gone
}

message ServerLimits {
  uint32 max_concurrent_sessions = 1;
  uint32 max_audio_ms = 2;
  uint32 max_message_bytes = 3;
  uint32 replay_cache_ttl_ms = 4; // 0 => no replay cache
}

message ModelCapabilities {
  string model_id = 1;     // stable id used in SessionStart.model_id
  string display_name = 2;
  string family = 3;       // e.g. "parakeet-tdt-0.6b-v3", "qwen3-asr-1.7b"
  ModelState state = 4;
  bool is_default = 5;

  repeated string languages = 6; // BCP-47 tags; empty => unknown
  bool language_detection = 7;

  StreamingMode streaming_mode = 8;
  bool partials = 9;
  repeated AudioFormatSupport audio_formats = 10;

  BiasCapabilities bias = 11;
  FreeTextCapabilities free_text = 12;
  NBestCapabilities n_best = 13;
  TimestampCapabilities timestamps = 14;
  AnchorCost anchor_cost = 15;

  bool punctuation = 16;
  bool casing = 17;
  bool inverse_text_normalization = 18;

  PerfEstimate perf = 19; // measured at warmup on this host; advisory
}

message AudioFormatSupport {
  AudioEncoding encoding = 1;
  repeated uint32 sample_rates_hz = 2; // accepted input rates
}

message BiasCapabilities {
  BiasMechanism mechanism = 1;
  uint32 max_terms = 2;
  uint32 max_aliases_per_term = 3;
  uint32 max_term_bytes = 4;
  uint32 max_total_tokens = 5;   // backend token budget, 0 = n/a
  bool per_term_weight = 6;
  bool aliases_supported = 7;    // aliases influence recognition/matching
  bool case_insensitive = 8;     // canonical casing need not match model casing
  MidstreamUpdate midstream_update = 9;
  bool bias_with_n_best = 10;    // N-best comes from the biased decode
  bool hit_scores = 11;          // BiasHit.term_score/anchor_score populated
}

message FreeTextCapabilities {
  bool description = 1;
  bool preceding_text = 2;
  uint32 max_bytes = 3;
  bool echo_risk = 4; // model may copy context into output (e.g. Qwen3-ASR)
}

message NBestCapabilities {
  uint32 max_n = 1; // 1 => best only
  ScoreKind score_kind = 2;
}

message TimestampCapabilities {
  TimestampSource source = 1;
  uint32 resolution_ms = 2;         // e.g. 80 for 8x-subsampled FastConformer
  bool word_confidence = 3;
  bool confidence_calibrated = 4;   // only if validated on held-out data
}

message PerfEstimate {
  float rtfx = 1;               // audio seconds per wall second, one stream
  uint32 finalize_ms_10s = 2;   // EndOfAudio -> FinalResult, 10 s audio, p50
  uint32 bias_compile_ms = 3;   // cold compile of a 100-term list, p50
}

// =============================================================================
// Transcribe: client -> server
// =============================================================================

message TranscribeRequest {
  oneof msg {
    SessionStart start = 1;          // MUST be first, exactly once
    AudioChunk audio = 2;
    ContextUpdate context_update = 3;
    EndOfAudio end_of_audio = 4;     // at most once; no audio afterwards
    Cancel cancel = 5;
  }
}

message SessionStart {
  string utterance_id = 1;       // client UUIDv7; idempotency/correlation key
  uint32 attempt = 2;            // 0 first try; +1 per retry of same utterance
  string model_id = 3;           // empty => server default model
  repeated string languages = 4; // BCP-47; empty => detect; [0] = primary
  AudioFormat audio_format = 5;
  RecognitionContext context = 6;
  PrivacyFlags privacy = 7;
  ResultOptions options = 8;
  Deadlines deadlines = 9;
  ClientInfo client = 10;
  // Feature names (Capabilities.features or "result.word_timestamps",
  // "result.n_best", "result.anchor", "bias", "free_text") that MUST be
  // honored; otherwise the call fails with FAILED_PRECONDITION and
  // ErrorDetail.code = REQUIRED_FEATURE_UNAVAILABLE. Others are best effort.
  repeated string required_features = 11;
  // Escape hatch, namespaced by backend, e.g. "nemo.boosting_tree_alpha".
  // Servers MUST ignore unknown keys and report used ones in AppliedConfig.
  map<string, string> backend_params = 15;
}

message AudioFormat {
  AudioEncoding encoding = 1;
  uint32 sample_rate_hz = 2; // PCM: sample rate. OPUS: decode rate (16000)
  uint32 channels = 3;       // MUST be 1 in v1
  uint32 preroll_ms = 4;     // audio captured before hotkey-down (ring buffer)
}

message RecognitionContext {
  uint32 revision = 1;       // 1, 2, ... strictly increasing in a session
  BiasList bias = 2;
  FreeTextContext free_text = 3;
}

message BiasList {
  // Ordered by the client ranker, most relevant first. Servers that must
  // truncate drop from the END and report dropped ids.
  repeated BiasTerm terms = 1;
  // Lowercase hex SHA-256 of the protobuf encoding of a BiasList containing
  // only `terms` (fields 2 and 3 unset), as produced by the client. Opaque
  // cache key for compiled bias artifacts, keyed (model_id, content_hash).
  // Empty => do not cache.
  string content_hash = 2;
  // Global strength multiplier applied at decode time. Absent => 1.0.
  // 0.0 => no biasing (decode is effectively the anchor).
  optional float strength = 3;
}

message BiasTerm {
  string id = 1;          // client-stable id, unique within the list
  string canonical = 2;   // exact desired output: "PyTorch", "kubectl"
  repeated SpokenAlias aliases = 3;
  optional float weight = 4; // relative relevance in (0, 1]; absent => 1.0
  TermKind kind = 5;
  repeated ContextSource provenance = 6; // empty if privacy.minimal_metadata
  string language = 7;    // BCP-47 if different from session primary
}

message SpokenAlias {
  string text = 1;            // "pie torch", "cube control", "k u b e c t l"
  AliasKind kind = 2;
  optional float weight = 3;  // relative to parent term; absent => 1.0
}

message FreeTextContext {
  // Short natural-language description, already redacted by the client, e.g.
  // "Terminal in git repo 'hark' (Rust), branch feat/vad."
  string description = 1;
  // Text immediately before the caret, already redacted, oldest first.
  string preceding_text = 2;
}

message PrivacyFlags {
  // All false (the default) == most private. A server MUST NOT do anything
  // not allowed here and MUST report what it did in PrivacyAck.
  bool allow_audio_retention = 1;      // persist audio beyond the call
  bool allow_transcript_retention = 2; // persist text beyond the call
  bool allow_context_logging = 3;      // log bias list/free text (any level)
  bool allow_eval_capture = 4;         // write a dataset record
  bool allow_replay_cache = 5;         // keep FinalResult in RAM for TTL
  bool minimal_metadata = 6;           // client stripped provenance/description
}

message ResultOptions {
  bool partials = 1;
  uint32 partial_interval_ms = 2; // 0 => server default
  uint32 n_best = 3;              // 0 or 1 => best only
  bool word_timestamps = 4;
  bool word_confidence = 5;
  AnchorMode anchor = 6;
  bool bias_diagnostics = 7;      // also report rejected candidates
  bool n_best_words = 8;          // words for every N-best entry, not only [0]
}

message Deadlines {
  // After EndOfAudio is received the server MUST write FinalResult within
  // this budget, dropping optional work (N-best, anchor, alignment) as
  // needed and setting ResultFlags. 0 => server default.
  uint32 finalize_budget_ms = 1;
  // Hard cap on utterance audio; beyond it the call fails AUDIO_TOO_LONG.
  uint32 max_audio_ms = 2;
}

message ClientInfo {
  string client_version = 1;
  string device_id = 2;  // random per install, never hardware-derived
}

message AudioChunk {
  bytes data = 1;          // PCM_S16LE: samples; OPUS: exactly one packet
  uint64 seq = 2;          // 0, 1, 2, ... contiguous
  uint64 start_sample = 3; // offset of first sample at AudioFormat rate
  uint32 num_samples = 4;  // samples this chunk represents (decoded, mono)
  optional float vad_speech_prob = 5; // client Silero VAD, max over chunk
}

message ContextUpdate {
  RecognitionContext context = 1; // full replacement of the previous context
}

message EndOfAudio {
  uint64 last_seq = 1;
  uint64 total_samples = 2;
  EndReason reason = 3;
}

message Cancel {
  CancelReason reason = 1;
}

// =============================================================================
// Transcribe: server -> client
// =============================================================================

message TranscribeResponse {
  oneof msg {
    SessionAccepted accepted = 1;     // exactly once, first
    PartialResult partial = 2;
    FinalResult final_result = 3;     // exactly once, last (unless error)
    StreamStatus status = 4;
    ServerWarning warning = 5;
  }
}

message SessionAccepted {
  string session_id = 1;
  string model_id = 2;
  AppliedConfig applied = 3;
  uint64 server_time_unix_ms = 4;
  string capabilities_etag = 5;
  // A cached FinalResult for (utterance_id) follows immediately; the client
  // SHOULD stop sending audio and send end_of_audio.
  bool replayed = 6;
}

message AppliedConfig {
  BiasMechanism bias_mechanism = 1;
  uint32 bias_terms_used = 2;
  repeated string bias_terms_dropped = 3; // ids
  bool free_text_used = 4;
  uint32 n_best = 5;
  bool anchor_planned = 6;
  bool word_timestamps = 7;
  bool word_confidence = 8;
  // Effective backend values, incl. weight->boost mapping, e.g.
  // {"nemo.boosting_model_alpha": "1.5", "riva.boost_buckets": "2,3,4"}.
  map<string, string> backend_params = 9;
}

message PartialResult {
  string text = 1;
  uint32 stable_bytes = 2;       // prefix of text the server will not revise
  uint32 audio_processed_ms = 3;
  uint32 context_revision = 4;
  uint32 seq = 5;                // strictly increasing
}

message StreamStatus {
  uint32 audio_received_ms = 1;
  uint32 audio_processed_ms = 2; // == received for BUFFERED servers
  uint32 queued_sessions = 3;
}

message ServerWarning {
  WarningCode code = 1;
  string message = 2;            // never contains user text/context
  repeated string term_ids = 3;
}

message FinalResult {
  string utterance_id = 1;
  // n_best[0] is the primary result. Always present (empty text on silence).
  repeated Hypothesis n_best = 2;
  // Same audio, same model, NO bias list, NO free-text context.
  // Absent when not requested, unavailable or skipped (see flags).
  Hypothesis anchor = 3;
  repeated BiasHit bias_hits = 4;
  string detected_language = 5;  // BCP-47
  optional float language_confidence = 6;
  uint32 context_revision = 7;   // context revision actually used
  Timings timings = 8;
  ResultFlags flags = 9;
  PrivacyAck privacy = 10;
  bool replayed = 11;
}

message Hypothesis {
  string text = 1;
  repeated Word words = 2;
  optional float score = 3;
  ScoreKind score_kind = 4;
  optional float confidence = 5; // utterance-level, [0, 1]
  HypothesisOrigin origin = 6;
}

message Word {
  string text = 1;
  uint32 start_ms = 2;
  uint32 end_ms = 3;
  optional float confidence = 4; // absent if backend has none or a placeholder
  uint32 text_byte_start = 5;    // into the parent Hypothesis.text
  uint32 text_byte_end = 6;
  string bias_term_id = 7;       // set if the word lies inside a BiasHit span
}

message BiasHit {
  string term_id = 1;
  string matched_form = 2;       // canonical or alias text that matched
  bool via_alias = 3;
  BiasHitDecision decision = 4;
  BiasMechanism mechanism = 5;
  uint32 text_byte_start = 6;    // span in n_best[0].text (0,0 if REJECTED)
  uint32 text_byte_end = 7;
  uint32 start_ms = 8;
  uint32 end_ms = 9;
  string anchor_text = 10;       // anchor words overlapping [start_ms, end_ms)
  optional float term_score = 11;   // backend log-score of term over the span
  optional float anchor_score = 12; // backend log-score of anchor_text there
  optional float boost = 13;        // effective boost, backend scale
}

message Timings {
  uint32 audio_ms = 1;
  uint32 queue_ms = 2;
  uint32 bias_prepare_ms = 3;
  bool bias_cache_hit = 4;
  uint32 lag_at_end_ms = 5;      // audio unprocessed when EndOfAudio arrived
  uint32 end_to_final_ms = 6;    // EndOfAudio received -> FinalResult written
  uint32 encoder_ms = 7;
  uint32 decode_ms = 8;
  uint32 anchor_ms = 9;
  uint32 n_best_ms = 10;
  uint32 alignment_ms = 11;
  uint32 postprocess_ms = 12;
  uint64 server_end_received_unix_us = 13;
  uint64 server_final_sent_unix_us = 14;
  map<string, uint32> backend_ms = 15;
}

message ResultFlags {
  bool finalize_budget_exceeded = 1;
  bool n_best_skipped = 2;
  bool anchor_skipped = 3;
  bool timestamps_skipped = 4;
  bool context_truncated = 5;
  bool context_echo_suspected = 6;
  bool no_speech = 7;
  bool audio_gap = 8;            // seq/sample gap; server zero-filled
}

message PrivacyAck {
  bool audio_retained = 1;
  bool transcript_retained = 2;
  bool context_logged = 3;
  bool eval_captured = 4;
  bool replay_cached = 5;
}

// Packed into google.rpc.Status.details (grpc-status-details-bin) on any
// non-OK status from any hark RPC.
message ErrorDetail {
  ErrorCode code = 1;
  string message = 2;            // never contains user text/context
  bool retryable = 3;            // safe to retry same utterance, same server
  uint32 retry_after_ms = 4;
  string utterance_id = 5;
  repeated string unavailable_features = 6;
}

// =============================================================================
// Recognize / Warmup
// =============================================================================

message RecognizeRequest {
  SessionStart config = 1;
  repeated AudioChunk audio = 2; // complete utterance, contiguous seq
}

message RecognizeResponse {
  SessionAccepted accepted = 1;
  FinalResult final_result = 2;
  repeated ServerWarning warnings = 3;
}

message WarmupRequest {
  string model_id = 1;   // empty => default model
  bool include_bias = 2; // also exercise the bias path (graph capture etc.)
}

message WarmupResponse {
  ModelState state = 1;
  uint32 warm_ms = 2;
  uint64 server_time_unix_ms = 3;
}

// =============================================================================
// TextCleanup
// =============================================================================

message CleanupProfile {
  string id = 1;                       // e.g. "prose", "chat", "commit_message"
  string description = 2;
  repeated EditKind allowed_kinds = 3;
  uint32 max_input_bytes = 4;
  uint32 typical_ms = 5;
  string model = 6;
}

message CleanupRequest {
  string request_id = 1;
  string utterance_id = 2;
  string text = 3;                     // client text after fusion/canonicalization
  repeated ProtectedSpan protected_spans = 4; // byte spans into `text`
  string profile_id = 5;
  string language = 6;
  CleanupContext context = 7;
  uint32 max_edits = 8;                // 0 => profile default
  uint32 budget_ms = 9;                // generation budget, < gRPC deadline
  PrivacyFlags privacy = 10;
}

message ProtectedSpan {
  uint32 byte_start = 1;
  uint32 byte_end = 2;
  ProtectedSpanKind kind = 3;
}

message CleanupContext {
  string app_category = 1;   // "terminal", "chat", "editor", "email", ...
  string preceding_text = 2; // only if the provider's privacy policy allows
}

message CleanupResponse {
  repeated Edit edits = 1;   // sorted by byte_start, non-overlapping
  string model = 2;
  uint32 generation_ms = 3;
  uint32 total_ms = 4;
  bool truncated = 5;        // budget hit; only complete edits returned
  uint32 rejected_by_server = 6;
}

message Edit {
  uint32 byte_start = 1;     // into CleanupRequest.text; end > start
  uint32 byte_end = 2;
  string find = 3;           // MUST equal text[byte_start:byte_end]
  string replace = 4;
  EditKind kind = 5;
  string reason = 6;         // short; for history/debug UI only
}

// =============================================================================
// Enums (values prefixed per buf STANDARD lint rules)
// =============================================================================

enum ModelState {
  MODEL_STATE_UNSPECIFIED = 0;
  MODEL_STATE_LOADING = 1;
  MODEL_STATE_READY = 2;
  MODEL_STATE_COLD = 3;      // unloaded; Warmup needed (seconds)
  MODEL_STATE_FAILED = 4;
}

enum StreamingMode {
  STREAMING_MODE_UNSPECIFIED = 0;
  STREAMING_MODE_BUFFERED = 1;    // audio streamed in, decoded at EndOfAudio
  STREAMING_MODE_INCREMENTAL = 2; // decoded while audio arrives
}

enum AudioEncoding {
  AUDIO_ENCODING_UNSPECIFIED = 0;
  AUDIO_ENCODING_PCM_S16LE = 1;
  AUDIO_ENCODING_OPUS = 2;        // raw packets, no Ogg container
}

enum BiasMechanism {
  BIAS_MECHANISM_UNSPECIFIED = 0;
  BIAS_MECHANISM_NONE = 1;
  BIAS_MECHANISM_DECODE_TIME_BOOST = 2; // shallow-fusion boosting (GPU-PB, Riva)
  BIAS_MECHANISM_ACOUSTIC_RESCORE = 3;  // post-decode CTC spotting (FluidAudio)
  BIAS_MECHANISM_PROMPT = 4;            // prompt/keyword conditioning (LLM-ASR)
}

enum MidstreamUpdate {
  MIDSTREAM_UPDATE_UNSPECIFIED = 0;
  MIDSTREAM_UPDATE_UNSUPPORTED = 1;  // only SessionStart context is used
  MIDSTREAM_UPDATE_FULL = 2;         // latest revision applies to all audio
  MIDSTREAM_UPDATE_FORWARD_ONLY = 3; // applies to audio decoded after update
}

enum ScoreKind {
  SCORE_KIND_UNSPECIFIED = 0;
  SCORE_KIND_LOG_PROB = 1;           // total log-probability (natural log)
  SCORE_KIND_LOG_PROB_PER_TOKEN = 2; // length-normalized
  SCORE_KIND_POSTERIOR = 3;          // [0, 1]
  SCORE_KIND_RANK_ONLY = 4;          // order meaningful, values are not
}

enum TimestampSource {
  TIMESTAMP_SOURCE_UNSPECIFIED = 0;
  TIMESTAMP_SOURCE_NONE = 1;
  TIMESTAMP_SOURCE_DECODER_EMISSION = 2; // frame of emission (TDT/RNNT/CTC)
  TIMESTAMP_SOURCE_FORCED_ALIGNMENT = 3; // separate aligner pass
  TIMESTAMP_SOURCE_PROVIDER = 4;         // opaque third-party
}

enum AnchorCost {
  ANCHOR_COST_UNSPECIFIED = 0;
  ANCHOR_COST_UNAVAILABLE = 1;
  ANCHOR_COST_FREE = 2;          // by-product (e.g. rescoring keeps base text)
  ANCHOR_COST_DECODER_ONLY = 3;  // second decode over shared encoder output
  ANCHOR_COST_FULL_PASS = 4;     // second full inference
}

enum AnchorMode {
  ANCHOR_MODE_UNSPECIFIED = 0;   // server default == IF_CHEAP
  ANCHOR_MODE_OFF = 1;
  ANCHOR_MODE_IF_CHEAP = 2;      // FREE or DECODER_ONLY
  ANCHOR_MODE_ALWAYS = 3;        // also FULL_PASS, budget permitting
}

enum HypothesisOrigin {
  HYPOTHESIS_ORIGIN_UNSPECIFIED = 0;
  HYPOTHESIS_ORIGIN_BIASED = 1;    // decode with bias and/or free-text context
  HYPOTHESIS_ORIGIN_UNBIASED = 2;  // decode without any context
  HYPOTHESIS_ORIGIN_BEAM = 3;      // additional beam entry
  HYPOTHESIS_ORIGIN_RESCORED = 4;  // base text modified by acoustic rescoring
}

enum ContextSource {
  CONTEXT_SOURCE_UNSPECIFIED = 0;
  CONTEXT_SOURCE_USER_DICTIONARY = 1;
  CONTEXT_SOURCE_HISTORY = 2;
  CONTEXT_SOURCE_FOCUSED_TEXT = 3;
  CONTEXT_SOURCE_SELECTION = 4;
  CONTEXT_SOURCE_WINDOW_TITLE = 5;
  CONTEXT_SOURCE_TERMINAL_TEXT = 6;
  CONTEXT_SOURCE_GIT_REPO = 7;
  CONTEXT_SOURCE_AGENT_SESSION = 8;
  CONTEXT_SOURCE_OCR = 9;
  CONTEXT_SOURCE_APP_PROFILE = 10;
}

enum TermKind {
  TERM_KIND_UNSPECIFIED = 0;
  TERM_KIND_WORD = 1;
  TERM_KIND_PERSON_NAME = 2;
  TERM_KIND_PRODUCT = 3;
  TERM_KIND_IDENTIFIER = 4;   // code symbol: snake_case, camelCase, ...
  TERM_KIND_ACRONYM = 5;
  TERM_KIND_PATH = 6;
  TERM_KIND_COMMAND = 7;      // CLI tool / subcommand
}

enum AliasKind {
  ALIAS_KIND_UNSPECIFIED = 0;
  ALIAS_KIND_SPOKEN_FORM = 1;      // how it is said: "pie torch"
  ALIAS_KIND_SPELLED = 2;          // letter by letter: "k u b e c t l"
  ALIAS_KIND_MISRECOGNITION = 3;   // learned ASR error: "pie charm" -> PyCharm
}

enum EndReason {
  END_REASON_UNSPECIFIED = 0;
  END_REASON_KEY_UP = 1;
  END_REASON_VAD_ENDPOINT = 2;
  END_REASON_MAX_DURATION = 3;
  END_REASON_TOGGLE = 4;
}

enum CancelReason {
  CANCEL_REASON_UNSPECIFIED = 0;
  CANCEL_REASON_USER_ABORT = 1;
  CANCEL_REASON_TOO_SHORT = 2;
  CANCEL_REASON_SWITCHED_PROVIDER = 3;
  CANCEL_REASON_SHUTDOWN = 4;
}

enum BiasHitDecision {
  BIAS_HIT_DECISION_UNSPECIFIED = 0;
  BIAS_HIT_DECISION_APPLIED = 1;          // term in n_best[0], not in anchor
  BIAS_HIT_DECISION_ALREADY_IN_ANCHOR = 2; // term present without bias too
  BIAS_HIT_DECISION_REJECTED = 3;          // candidate considered, not applied
}

enum WarningCode {
  WARNING_CODE_UNSPECIFIED = 0;
  WARNING_CODE_BIAS_TERMS_TRUNCATED = 1;
  WARNING_CODE_ALIASES_DROPPED = 2;
  WARNING_CODE_FREE_TEXT_TRUNCATED = 3;
  WARNING_CODE_FEATURE_DEGRADED = 4;
  WARNING_CODE_AUDIO_GAP = 5;
  WARNING_CODE_LANGUAGE_FALLBACK = 6;
  WARNING_CODE_CONTEXT_IGNORED = 7;
  WARNING_CODE_FINALIZE_BUDGET_TIGHT = 8;
}

enum ErrorCode {
  ERROR_CODE_UNSPECIFIED = 0;
  ERROR_CODE_MODEL_NOT_FOUND = 1;         // NOT_FOUND
  ERROR_CODE_MODEL_NOT_READY = 2;         // UNAVAILABLE, retryable
  ERROR_CODE_OVERLOADED = 3;              // RESOURCE_EXHAUSTED, retry_after_ms
  ERROR_CODE_UNSUPPORTED_AUDIO_FORMAT = 4; // INVALID_ARGUMENT
  ERROR_CODE_UNSUPPORTED_LANGUAGE = 5;    // INVALID_ARGUMENT
  ERROR_CODE_REQUIRED_FEATURE_UNAVAILABLE = 6; // FAILED_PRECONDITION
  ERROR_CODE_PROTOCOL_VIOLATION = 7;      // INVALID_ARGUMENT
  ERROR_CODE_AUDIO_TOO_LONG = 8;          // OUT_OF_RANGE
  ERROR_CODE_BACKEND_FAILURE = 9;         // INTERNAL, retryable
  ERROR_CODE_SUPERSEDED = 10;             // ABORTED: newer attempt arrived
  ERROR_CODE_UNAUTHENTICATED = 11;        // UNAUTHENTICATED
}

enum ProtectedSpanKind {
  PROTECTED_SPAN_KIND_UNSPECIFIED = 0;
  PROTECTED_SPAN_KIND_IDENTIFIER = 1;
  PROTECTED_SPAN_KIND_NUMBER = 2;
  PROTECTED_SPAN_KIND_NEGATION = 3;
  PROTECTED_SPAN_KIND_BIAS_TERM = 4;
  PROTECTED_SPAN_KIND_URL = 5;
  PROTECTED_SPAN_KIND_PATH = 6;
  PROTECTED_SPAN_KIND_CODE = 7;
  PROTECTED_SPAN_KIND_QUOTED = 8;
  PROTECTED_SPAN_KIND_USER_LOCKED = 9;
}

enum EditKind {
  EDIT_KIND_UNSPECIFIED = 0;
  EDIT_KIND_PUNCTUATION = 1;     // alphanumerics unchanged
  EDIT_KIND_CAPITALIZATION = 2;  // case-folded text unchanged
  EDIT_KIND_SPACING = 3;         // non-whitespace unchanged
  EDIT_KIND_FILLER_REMOVAL = 4;  // only listed fillers removed
  EDIT_KIND_NUMBER_FORMAT = 5;   // numeric value unchanged
  EDIT_KIND_SELF_CORRECTION = 6; // "no wait, I mean ..." (not mechanically checkable)
  EDIT_KIND_GRAMMAR = 7;         // not mechanically checkable
}
```

---

## 6. Bias and context semantics

### 6.1 Term model
`BiasTerm{id, canonical, aliases[], weight, kind, provenance[], language}`:
- **`canonical`** is the exact output form (`PyTorch`, `kubectl`, `hark_server::session`). The client ranker produces about 20–100 terms per utterance, **ordered by relevance**.
- **`aliases`** come in three kinds:
  - `SPOKEN_FORM`: "pie torch", "cube control".
  - `SPELLED`: "k u b e c t l".
  - `MISRECOGNITION`: learned from history, i.e. what ASR produced when the user meant this term.

  How aliases are used depends on the backend (§6.3). They are always echoed back through `BiasHit.matched_form`/`via_alias`, so client canonicalization can rely on them.
- **`weight`** is a *normalized relevance* in (0, 1], not a backend score. Backends use incompatible scales: per-phrase alpha × `boosting_model_alpha` in GPU-PB, typical boost 2–3 for cache-aware RNNT versus 8–10 for Flashlight CTC in NeMo-Speech.cpp, a single `cbw` in FluidAudio, and no weights at all in prompt APIs. The server owns the mapping because only it knows the decoder. It reports the result in `AppliedConfig.backend_params`.
- **`provenance`** helps server-side evaluation and logging. The client omits it when `minimal_metadata` is set.

### 6.2 Truncation
The client should already respect `max_terms`, `max_aliases_per_term` and `max_total_tokens`. If the server still has to truncate, it drops from the **end** of the list (lowest rank), fills `AppliedConfig.bias_terms_dropped`, and emits `warning{BIAS_TERMS_TRUNCATED}`. This keeps the "short, relevance-filtered list" principle in the client's hands.

### 6.3 Mechanism-specific use of weights and aliases

| Mechanism | canonical | SPOKEN_FORM / SPELLED aliases | MISRECOGNITION aliases | weight |
|---|---|---|---|---|
| DECODE_TIME_BOOST (GPU-PB, Riva, sherpa hotwords) | boosted (GPU-PB `bpe_mode=case_insensitive`, so casing need not match) | boosted only if the canonical form is unlikely to tokenize as spoken (`TermKind` IDENTIFIER/COMMAND/PATH); server-tunable `nemo.boost_aliases` | **never boosted**; used only for hit detection | per-phrase alpha (GPU-PB) or bucketed `SpeechContext.boost` (Riva) |
| ACOUSTIC_RESCORE (FluidAudio) | `CustomVocabularyTerm.text` | `CustomVocabularyTerm.aliases` | `aliases` too (this is exactly their purpose) | not per-term [U]; `strength` scales the global `cbw` |
| PROMPT (Qwen3-ASR, gpt-transcribe keywords, Voxtral `context_bias`, Deepgram keyterm) | listed | omitted by default (echo risk) | omitted | used for ordering and truncation only |

### 6.4 `content_hash` and caching
`content_hash = hex(sha256(prost_encode(BiasList{terms})))`, computed by the client. The server treats it as an opaque key: `(model_id, content_hash) → compiled artifact` in an LRU held in RAM only. For NeMo GPU-PB this is the `BiasingRequestItemConfig.cache_key` facility. NVIDIA documents that "with memory caching, per-stream boosting achieves near-zero overhead compared to global (shared) boosting".

Per-term weights are baked into the GPU-PB tree at build time, so they are part of the hash. `strength` is applied at decode time, so it is not.

I rejected a separate `PrepareContext` RPC. `start` is sent at hotkey-down, so compilation already overlaps with speech, and the hash covers repeated contexts.

### 6.5 Free-text context
`FreeTextContext{description, preceding_text}`. The client must redact both before sending.
- The server composes the backend-specific prompt. For Qwen3-ASR that is the system message; Qwen3-ASR's `transcribe(context=...)` puts `context` into the system role.
- When `echo_risk` is true, the server runs an **echo guard**. If the output contains a run of ≥ 4 words copied from the context that the anchor does not support, it sets `flags.context_echo_suspected`. The client then prefers the anchor for that span.

### 6.6 Anchor (the unbiased decode)
The anchor is the same audio, model and language, with **no bias list and no free-text context**, using greedy or default decoding. It always has word timings if the biased hypothesis has them, so the client can align the two by time.
- `AnchorMode.IF_CHEAP` (the default) produces it when `anchor_cost` is FREE or DECODER_ONLY.
- `ALWAYS` also pays for FULL_PASS if the budget allows. This is used for evaluation and for prompt-based backends.

How the client uses the evidence (informative; the policy lives in the client):

```
for hit in final.bias_hits where decision == APPLIED:
    if hit.term_score and hit.anchor_score are present:
        keep  = hit.term_score - hit.anchor_score >= tau[kind]
    else:
        keep  = min word confidence in span >= c_min[kind]
                and edit_similarity(hit.anchor_text, hit.matched_form) >= s_min[kind]
    if not keep: replace span [text_byte_start, text_byte_end) with hit.anchor_text
```

`ALREADY_IN_ANCHOR` hits are always kept. `REJECTED` (only sent with `bias_diagnostics`) feeds the distractor/false-substitution metrics.

---

## 7. Results semantics

- **`n_best[0]`** is the primary hypothesis. Extra entries appear only if requested and supported. `score_kind` says how to read `score`; `RANK_ONLY` means do not compare values. For backends that cannot produce N-best from the biased decode (`bias_with_n_best=false`, e.g. NeMo per-stream GPU-PB, which works only with greedy label-looping), entries ≥ 1 are `origin=BEAM` from an *unbiased* beam search. The client must not treat them as biased alternatives.
- **`words[]`** come by default only for `n_best[0]` and `anchor`. This keeps a typical `FinalResult` under about 12 KB, inside a fresh TCP initial congestion window, which matters after idle periods (§9). `n_best_words` lifts that limit.
- **Word fields.** `text_byte_start/end` index into the hypothesis text, so the client never re-tokenizes. `confidence` is **absent** when the backend has none *or reports a placeholder*. For example, NeMo-Speech.cpp's RNNT results report 1.0, and the server or adapter must drop that. `timestamps.confidence_calibrated` is true only after hark's own calibration on held-out data.
- **`bias_hits`** report:
  - `decision`: APPLIED, ALREADY_IN_ANCHOR, or REJECTED (diagnostics only).
  - The span in `n_best[0]` and its time range.
  - `anchor_text` over the same time range.
  - Backend scores when `hit_scores=true`.

  For DECODE_TIME_BOOST and PROMPT backends, the decoder does not report which phrase fired. The server derives hits by matching the canonical form and aliases in the biased output (case- and space-normalized) and diffing against the anchor on word timings. This logic lives in one shared Python module, `hark_server.hits`, and is ported once to Rust for adapters.
- **`timings`** cover the server side only. The client computes network time as `(t_final_recv − t_eoa_send) − timings.end_to_final_ms`. No clock sync is needed, because both values are monotonic durations on their own hosts. Every utterance's history record stores the full `Timings`, which makes p95 budgets observable per provider.

---

## 8. Cleanup

### 8.1 Design
`TextCleanup.Cleanup(CleanupRequest) → CleanupResponse`, unary, with deadline = `budget_ms + 50 ms`. Inputs and outputs:
- `text` is the *client's* text after fusion, canonicalization and guardrails.
- `protected_spans` are byte spans for identifiers, numbers, negations, bias terms, URLs, paths, code, quotes and user-locked text.
- `profile_id` selects one of the server-advertised profiles, each with its allowed `EditKind`s.
- The response is **edits**: `{byte_start, byte_end, find, replace, kind, reason}`, sorted and non-overlapping.

The server-side flow:
1. Render a profile-specific prompt that marks protected spans inline (e.g. `⟦kubectl⟧`).
2. Call the local LLM (`llama-server`, vLLM or SGLang) through its **OpenAI-compatible** `/v1/chat/completions` with `response_format: {type: "json_schema", ...}` [U: exact parameter shape differs per engine; the adapter normalizes it]. The schema is:
   ```json
   {"type":"object","additionalProperties":false,"required":["edits"],
    "properties":{"edits":{"type":"array","maxItems":16,"items":{
      "type":"object","additionalProperties":false,
      "required":["find","occurrence","replace","kind","reason"],
      "properties":{
        "find":{"type":"string","minLength":1,"maxLength":200},
        "occurrence":{"type":"integer","minimum":1},
        "replace":{"type":"string","maxLength":200},
        "kind":{"enum":["punctuation","capitalization","spacing","filler_removal",
                        "number_format","self_correction","grammar"]},
        "reason":{"type":"string","maxLength":80}}}}}}
   ```
3. **Resolve** each `find`/`occurrence` against the unmarked text to get byte ranges. LLMs are bad at producing offsets, but they reliably quote spans.
4. Run the same validator as the client (below) and count rejections in `rejected_by_server`.
5. If the budget allows and everything was rejected, retry once.

### 8.2 Client validator (authoritative, normative)
For each edit:
1. Range on char boundaries, `byte_end > byte_start`, `text[range] == find`.
2. No intersection with any protected span.
3. `kind` is in the profile's `allowed_kinds` **and** passes its mechanical invariant:
   - PUNCTUATION: alphanumerics equal.
   - CAPITALIZATION: case-folded strings equal.
   - SPACING: non-whitespace equal.
   - FILLER_REMOVAL: `replace` equals `find` minus tokens from the filler list.
   - NUMBER_FORMAT: parsed numeric values equal.
   - SELF_CORRECTION and GRAMMAR have no mechanical check. They are disabled in default profiles.
4. Edits must not overlap one another.

Then, globally:
- The negation-token count and the digit multiset are unchanged across the whole text.
- Total changed bytes ≤ max(8, 15 % of text).

Invalid edits are dropped individually. If more than 50 % are invalid, all are dropped, because the model is misbehaving. On deadline, error, or zero valid edits, the raw text is pasted.

### 8.3 Why a gRPC RPC rather than having the client call the OpenAI-compatible endpoint directly
- **Same warm, mTLS-authenticated channel and the same capability/health model.** No second TLS stack, token or port, and the LLM server can stay bound to localhost. llama.cpp and NeMo-Speech.cpp HTTP servers ship with weak or optional auth.
- **Server-side span resolution, first-pass validation and one in-budget retry** take parsing and retry logic off the latency-critical client path.
- **Prompt templates are versioned with the model they were tuned for**, while the client sees a stable `profile_id`.
- **Escape hatch kept.** The client also ships `OpenAiCompatCleanup`, which sends the same JSON schema directly to any OpenAI-compatible endpoint (third-party, or llama.cpp on the Mac without a hark server). The validator is shared, so the safety properties are identical.

---

## 9. Latency analysis

### 9.1 What is hidden behind speech
Everything before key-up happens while the user speaks and costs nothing extra:
- TCP/TLS (re)connect if the channel was cold: 2 RTT.
- Stream open: one HEADERS frame on the warm connection, 0 RTT.
- `start` processing, bias compilation (cache miss: `perf.bias_compile_ms` [U], tens of ms), the late `context_update`, audio upload, and incremental decoding.

The **critical path is: key-up → flush → 1 RTT → finalize → client → (cleanup) → paste.**

### 9.2 Budget — LAN, desktop RTX 3060, Parakeet-TDT-0.6B-v3 + per-stream GPU-PB, 8 s utterance

| Stage | p50 ms | p95 ms | Notes |
|---|---:|---:|---|
| Key-up → last chunk + `end_of_audio` written | 2 | 120 | Flush immediately. p95 includes the optional tail capture of ≤ 120 ms if VAD reports speech at key-up (client policy). |
| Uplink | 1 | 10 | Wired ≈ 0.3 ms; Wi-Fi jitter dominates p95 [U] |
| Server lag at EOA | 0 | 5 | BUFFERED: none to catch up |
| Encoder over 8 s | 25 | 50 | [U] measure; FastConformer 0.6B fp16 |
| Biased greedy + anchor greedy (batch of 2) | 15 | 35 | [U] encoder-output reuse optional (§14.1) |
| Timestamps, confidence, hits | 2 | 5 | |
| Downlink `final_result` (≈ 6–12 KB) | 1 | 10 | |
| Client fusion, gating, canonicalization | 2 | 5 | |
| Paste on Wayland (clipboard + virtual-keyboard paste) | 15 | 60 | [U] |
| **Subtotal (no cleanup)** | **≈ 65** | **≈ 300** | Summing p95s is pessimistic |
| Cleanup (1–4B model, ≤ 40 output tokens, same GPU) | 180 | 400 | [U]; skipped when heuristics predict no edit |
| **Total with cleanup** | **≈ 245** | **≈ 700** | Meets ≤ 1 s p95 |

Qwen3-ASR-1.7B on the same GPU, estimated:
- Decode of 8 s: 200–450 ms [U].
- Anchor via a concurrent vLLM request: +30–60 % [U].
- Qwen3-ForcedAligner for timestamps: +60–150 ms [U].

That is already 0.5–0.9 s p95 before cleanup. Rule: with Qwen, the client sets a tight `finalize_budget_ms` so the server sheds alignment and anchor, and cleanup is skipped unless budget remains.

### 9.3 Tailscale

| Path | Added to the critical path | Notes |
|---|---|---|
| Same LAN, direct WireGuard | ≈ +0.2–1 ms | encryption cost; userspace `tailscaled` on macOS a bit higher [U] |
| Remote, direct (e.g. laptop on campus → home desktop) | +1 RTT ≈ 10–50 ms | [U]; one RTT, because audio was already uploaded while speaking |
| Remote via DERP relay | +1 RTT ≈ 30–150+ ms | Tailscale: DERP adds a relay hop; throughput can be limited, but 32 KB/s audio is trivial |
| Cold connection (resume, network switch) | 0 if it overlaps with speech | client reconnects on resume and network events; DERP→direct upgrade happens later |

Over Tailscale the p95 risk is **TCP loss recovery, not bandwidth**. Linux's minimum RTO is 200 ms, so one lost packet of the final result can blow the budget. Mitigations:
- Small finals (§7).
- Warm connection.
- `status` liveness, which triggers hedging (§10.4).
- On a degraded link, start the local CPU fallback speculatively *while* streaming.

### 9.4 gRPC overhead
A 40 ms PCM chunk is 1,280 B payload + ~8 B protobuf + 5 B gRPC prefix + 9 B HTTP/2 frame header, about 2 % overhead at 25 msg/s. Python `grpc.aio` per-message cost is in the tens of µs [U]. Neither is significant.

---

## 10. Backpressure, cancellation, retries, idempotency

### 10.1 Backpressure
- **Client.** The capture thread pushes into a bounded channel (e.g. 256 × 40 ms ≈ 10 s), feeding the gRPC sender task, and *also* appends every sample to the utterance buffer, which is the source of truth for replay. HTTP/2 flow control naturally slows the sender if the server stops reading. The client never drops audio. If the gRPC sender falls behind real time by more than 1.5 s, or `status.audio_processed_ms` lags `audio_received_ms` by more than 2 s on an INCREMENTAL server, the provider counts as lagging: the client starts the next provider with a replay (§10.3) and cancels the lagging one once the replacement is accepted.
- **Server.**
  - Admission control at `start`: if `max_concurrent_sessions` is reached, fail fast with `RESOURCE_EXHAUSTED` + `ErrorDetail{OVERLOADED, retry_after_ms}`, within one RTT, so routing happens before the user finishes the first word.
  - The per-session audio buffer is bounded by `max_audio_ms`.
  - A single GPU worker serializes and micro-batches finalize jobs, and `queue_ms` exposes queueing.

### 10.2 Cancellation
- **Explicit.** On `cancel{reason}`, the server stops work, frees state, sends nothing more, and ends the call with `CANCELLED`. The client need not wait.
- **Implicit.** Dropping the call sends RST_STREAM(CANCEL). The server sees it as `asyncio.CancelledError` in grpc.aio (or `context.cancelled()`), and as stream termination in tonic or grpc-swift, and runs the same cleanup. In-flight GPU work cannot be pre-empted mid-kernel; its result is discarded.
- **Too-short presses** (under 200 ms of audio) → `cancel{TOO_SHORT}`.
- **Hedging losers** → `cancel{SWITCHED_PROVIDER}`.
- Cancelled sessions are never captured or cached, whatever the privacy flags say.

### 10.3 Retries and idempotency
- `utterance_id` (UUIDv7) is the idempotency key, and `attempt` counts tries. Transcription has no side effects apart from opt-in retention and capture, and the server de-duplicates those by `utterance_id`.
- Failure handling:

| Failure | Action |
|---|---|
| Connect error / `UNAVAILABLE` before `accepted` | Next provider in the routing chain immediately, replaying the buffer at full speed, then continue live. Same provider only if it was healthy < 5 s ago and the reconnect succeeds within 100 ms. |
| Stream breaks after `accepted`, before EOA | New `Transcribe` (attempt+1) to the same or next provider with full replay. If a newer attempt arrives on the same server, the server aborts the old session with `ABORTED/SUPERSEDED`. |
| No `final_result` by `finalize_budget_ms + 2×RTT_est` | Hedge (§10.4). |
| `RESOURCE_EXHAUSTED` / `MODEL_NOT_READY` | Next provider. `Warmup` on the cold one in the background. |
| `INVALID_ARGUMENT` / `FAILED_PRECONDITION` | No retry with the same config. Retry once without `required_features` if the caller allows it. |
| `INTERNAL` with `retryable=true` | One retry on the same provider via `Recognize`, if the budget allows. |

- **Replay cache.** With `allow_replay_cache`, the server keeps `FinalResult` in RAM for `replay_cache_ttl_ms` (default 120 s), keyed by `utterance_id`. A retry whose final was computed but lost in transit gets `accepted{replayed=true}` plus the cached result immediately. The cache is valid only on the same `instance_id`.
- **Client-side exactly-once paste.** Each `utterance_id` is pasted at most once. Late results from hedged or abandoned attempts go into history only (useful for "swap to alternative" UI and evaluation).

### 10.4 Hedging
gRPC's built-in retry and hedging policies are not used: tonic does not implement them [U], and they cannot replay a client stream anyway. hark hedges at the utterance level:
- If there is no `status` for 750 ms during streaming, or no final by the deadline above, the client sends the buffered audio via `Recognize` to the next provider, or runs the in-process CPU engine, and takes the first valid result.
- With the provider registry's `speculative_local=true` (e.g. on battery-free laptops or known-bad networks), the local engine decodes in parallel from the start.

---

## 11. Health, warmup, admission

- **`grpc.health.v1.Health`.** Service names: `""`, `hark.v1.Asr`, `hark.v1.TextCleanup`. SERVING means at least one model is READY.
  - The client keeps one `Health.Watch` stream per provider, which pushes NOT_SERVING immediately, so the route decision at hotkey-down is instant and costs no RTT.
  - Python: `grpcio-health-checking`. Rust: `tonic-health` (client stubs; server side for the gateway). Swift: grpc-swift-2 health service [U].
- **Startup warmup (server).**
  1. Load models.
  2. Run synthetic decodes: 1 s and 10 s, with and without a 100-term bias list. This triggers CUDA-graph capture, cuDNN autotune and the vLLM compile.
  3. Measure and fill `PerfEstimate`.
  4. Only then set SERVING.
- **`Warmup` RPC.** Servers may unload models to free VRAM (e.g. when the desktop is used for games) and report `MODEL_STATE_COLD`. A client at hotkey-down never waits for a cold model. It routes to the next provider and fires `Warmup` in the background. `Warmup` returns `server_time_unix_ms` and doubles as an RTT probe.
- **Reflection.** `grpcio-reflection` / `tonic-reflection` are enabled in dev builds so `grpcurl` / `buf curl` can poke the server. In production builds reflection sits behind auth like everything else.

---

## 12. Security

- **Network.** Bind to the Tailscale interface address (100.x) or the LAN IP, with host firewall rules. Tailscale ACLs restrict which nodes can reach port 50051.
- **mTLS (default).** `hark pki init` (a thin wrapper, or documented `step`/`openssl` commands) creates a private CA:
  - The server cert's SANs are its MagicDNS name and LAN IP.
  - Each client device gets its own cert.
  - The server *requires* a client cert and checks the fingerprint against an allowlist in its config, so revoking a device is a one-line removal.
  - tonic, server: `ServerTlsConfig::new().identity(...).client_ca_root(...)`. Client: `ClientTlsConfig::new().ca_certificate(..).identity(..).domain_name(..)`.
  - grpcio: `grpc.ssl_server_credentials([(key, cert)], root_certificates=ca, require_client_auth=True)`.
- **Bearer token (optional).** `authorization: Bearer <token>`, checked by a server interceptor with constant-time comparison. Use it when TLS terminates at a proxy (e.g. in front of NeMo-Speech.cpp's `riva_server`, which currently uses **plaintext** server credentials) or as a second factor.
- **Tailscale-only mode** (h2c on the tailnet IP, no TLS) is supported for experiments but documented as lower assurance: any process on an allowed node can connect, and the context is sensitive.
- **Logging hygiene.** `ServerWarning.message` and `ErrorDetail.message` must never contain user text. This is enforced by code review and by a test that greps server logs from conformance runs for canary strings planted in contexts.

---

## 13. Compression and audio codec

- **gRPC compression: off.**
  - PCM speech barely compresses under gzip [U], and Opus is the right tool for that.
  - Control messages are a few KB.
  - The two stacks also don't agree: grpcio offers only gzip and deflate (no zstd), while tonic offers gzip, deflate and zstd.

  If it is ever enabled, use gzip and only for `Recognize`/`Cleanup`.
- **Default codec: PCM s16le, 16 kHz mono.** 256 kbit/s, zero CPU, bit-exact, and accepted by every backend, including NeMo-Speech.cpp's Riva adapter, which is LINEAR_PCM only. That is trivial on LAN and Tailscale-direct.
- **Optional codec: Opus** (`hark.opus`).
  - 16 kHz, mono, 20 ms packets, VBR ~32 kbit/s, complexity 5. FEC off (TCP has no packet loss to conceal). DTX off (silence timing matters for timestamps). Application VOIP vs AUDIO is to be chosen by measurement [U].
  - Use it when the provider registry marks a link as slow (cellular, DERP).
  - Evidence: one study reports ~12.6 % relative WER degradation at 16 kbit/s, and Nuance recommends 28 kbit/s with a 20 kbit/s minimum for its ASR. hark must measure term recall on its own data before enabling Opus by default.
  - Servers decode with libopus. Rust: `opus` (libopus bindings) or `audiopus`. Python: `opuslib` [U] or PyAV. Swift: libopus via SPM [U]. Riva-only backends get PCM from the gateway.

---

## 14. Backend mappings

Capability matrix (initial values; servers measure and report the real ones):

| Backend | mode | bias | max terms | aliases | per-term w | free text | N-best | timestamps | word conf | anchor |
|---|---|---|---|---|---|---|---|---|---|---|
| NeMo GPU-PB (Py, 3060) | BUFFERED (TDT) / INCREMENTAL (cache-aware) | DECODE_TIME_BOOST | 200 (soft) | spoken forms optionally boosted | yes [U per-stream] | no | beam, unbiased only | DECODER_EMISSION, 80 ms | yes (uncalibrated) | DECODER_ONLY / FULL_PASS |
| NeMo-Speech.cpp `riva_server` | INCREMENTAL | DECODE_TIME_BOOST for cache-aware RNNT and Flashlight CTC; **NONE for Parakeet TDT and greedy CTC** | per config | no (canonical only) | bucketed | no | **1** (not implemented) | DECODER_EMISSION, 80 ms | CTC: yes; RNNT: placeholder → absent | FULL_PASS (2nd stream) |
| Qwen3-ASR (Py, vLLM) | BUFFERED (+ partials) | PROMPT | ~100 (token budget) | no | ordering only | yes, echo_risk | 1 [U: vLLM n>1] | FORCED_ALIGNMENT (11 langs) | from token logprobs [U] | FULL_PASS (concurrent) |
| FluidAudio (Swift, Mac) | BUFFERED / INCREMENTAL | ACOUSTIC_RESCORE | ~100+ | yes, native | no [U] | no | 1 | DECODER_EMISSION (token timings) | [U] | **FREE** |
| parakeet-mlx (Py, Mac) | INCREMENTAL | NONE [U] | 0 | — | — | no | 1 | DECODER_EMISSION | [U] | FREE (== best) |
| In-process CPU (laptop) | INCREMENTAL | DECODE_TIME_BOOST via sherpa-onnx hotwords [U for NeMo TDT] | ~100 | spoken forms | hotword score [U] | no | 1 | token timestamps [U] | [U] | OFF (too costly) |
| OpenAI `gpt-transcribe` | BUFFERED (upload at EOA, `stream=true` deltas) | PROMPT (`keywords` + `prompt`) | [U] | no | no | yes | no | NONE (word timestamps only on `whisper-1`, deprecated) | logprobs [U] | FULL_PASS ($, off) |
| Deepgram Nova-3 | INCREMENTAL (WS) | PROMPT (`keyterm`) | 100 terms / 500 tokens | no | no | no | `alternatives` [U] | PROVIDER | yes | FULL_PASS ($, off) |
| Mistral Voxtral | BUFFERED / realtime | PROMPT (`context_bias`) | context-window bound | no | no | via a description entry | [U] | [U] | [U] | FULL_PASS ($, off) |

### 14.1 NeMo GPU-PB Python server (`hark-server-py`, backend `nemo_tdt`)
- **Model and decoding.** Parakeet-TDT-0.6B-v3 with `decoding.strategy="greedy_batch"`, `greedy.loop_labels=True` and `greedy.enable_per_stream_biasing=True`. Per-stream boosting is documented only for greedy label-looping transducers.
- **`start`.** Map `BiasList` → `BiasingRequestItemConfig(boosting_model_cfg=BoostingTreeModelConfig(key_phrase_items_list=[{phrase, alpha}], ...), boosting_model_alpha=strength×α_model, cache_key=content_hash)`.
  - Use `bpe_mode="case_insensitive"`, `context_score=1.0` and `depth_scaling=2.0`, NVIDIA's recommendations.
  - Per-phrase alpha = `0.5 + weight` (tunable).
  - Whether per-stream requests accept `key_phrase_items_list` with per-phrase alpha is [U]. Fallback: bucket terms into ≤ 3 alpha groups.
  - Compilation starts asynchronously in a thread executor.
- **`audio`.** Append to a float32 buffer. BUFFERED mode does nothing else. Optional partials: re-decode the whole buffer every `partial_interval_ms` (default 600 ms, off by default), which is cheap for ≤ 30 s on GPU.
- **`end_of_audio`.**
  1. One finalize job on the GPU worker: `transcribe(audio=[wav, wav], partial_hypothesis=[Hypothesis.empty_with_biasing_cfg(req), None], return_hypotheses=True, timestamps=True)`. This is a batch of two that yields biased + anchor, following NVIDIA's documented per-stream API, where `None` means no biasing.
  2. Optimization [U]: run the encoder once and call `decoding.rnnt_decoder_predictions_tensor` twice, or with tiled encoder output, to get `ANCHOR_COST_DECODER_ONLY`.
  3. Word confidence comes from `confidence_cfg.preserve_word_confidence=True`.
  4. N-best, if requested and in budget: an unbiased `malsd_batch` beam with `return_best_hypothesis=False` → `origin=BEAM`.
  5. Hits come from `hark_server.hits` (§7).
- **Cache-aware streaming models** (e.g. `parakeet-rnnt-1.1b` cache-aware / nemotron-streaming) support per-stream biasing too, so they map to INCREMENTAL with `midstream_update=FORWARD_ONLY`.

### 14.2 NeMo-Speech.cpp (Riva-compatible gRPC)
**Recommended shape:** a **`RivaAdapter` in Rust** implementing `SpeechProvider`. The same adapter code also runs inside a tiny **`hark-gateway`** (tonic server) on the GPU box, which listens for `hark.v1` with mTLS and talks to `riva_server` on `localhost:50051`. The gateway adds what `riva_server` lacks: TLS (it is plaintext), Opus decode (it is LINEAR_PCM only), an anchor via a parallel second stream, and the hit derivation.

Mapping:
- `start` → `StreamingRecognitionConfig{config: RecognitionConfig{encoding=LINEAR_PCM, sample_rate_hertz=16000, language_code, max_alternatives=1, enable_word_time_offsets=true, enable_automatic_punctuation, speech_contexts=[…]}, interim_results=partials}`.
- Weights → ≤ 3 `SpeechContext{phrases, boost}` buckets on the backend's scale: cache-aware RNNT `1+2w`, capped by `boosting_max_boost` (default 5); Flashlight CTC `6+4w`, capped by `max_boost` (default 10).
- Aliases are not sent. Riva boosting outputs boosted phrases verbatim, so canonicalization stays client-side.
- `audio` → `audio_content`. `end_of_audio` → half-close. A mid-stream `force_eou` runtime config exists but is not needed.
- `StreamingRecognitionResult` → `PartialResult`/`FinalResult`: `alternatives[0].transcript`, `words[]{start_time,end_time,word,confidence}` in ms. Confidence = 1.0 from RNNT means *absent*.
- Anchor = a second concurrent Riva stream without `speech_contexts` (FULL_PASS, used only if the GPU has headroom).
- **Model choice matters.** In NeMo-Speech.cpp, **Parakeet TDT ignores `speech_contexts`** and has no streaming endpointing. Use a cache-aware RNNT (e.g. nemotron-speech-streaming-en-0.6b) or Flashlight CTC when bias is wanted. The adapter reports `mechanism=NONE` for TDT so the client doesn't think it is biasing.
- N-best: `max_alternatives` is accepted, but only one alternative is returned → `max_n=1`.
- The Riva protos are compiled into `hark-proto` alongside `hark.v1` (`tonic-prost-build`).

### 14.3 Qwen3-ASR (Python, `qwen_asr` with vLLM backend)
- **`start`.** Build `context` = `free_text.description` + "\nVocabulary: " + canonical terms in rank order, truncated to the token budget. Languages → Qwen names (`"English"`). Setting `language` forces transcription-only output in the prompt.
- **`end_of_audio`.**
  1. `model.transcribe(audio=[(wav,16000),(wav,16000)], context=[ctx, ""], language=[L, L])` → biased + anchor in one batch.
  2. If timestamps are requested and in budget, use `return_time_stamps=True` with `Qwen3-ForcedAligner-0.6B` (11 languages) → `TIMESTAMP_SOURCE_FORCED_ALIGNMENT`. Without the aligner, words have no timings, and the client's acoustic gating falls back to text-level diffing against the anchor.
- **Partials (optional).** `init_streaming_state(context=..., chunk_size_sec=2.0)` + `streaming_transcribe()`. These work only on vLLM, re-feed all accumulated audio per chunk, and return no timestamps, so they are display-only.
- **Echo guard** as in §6.5. `free_text.echo_risk=true`.
- **Confidence** from vLLM token logprobs aggregated per word, once timestamps exist [U].
- **Process topology.** Start the vLLM engine (which spawns workers) before creating the grpc.aio server, or run vLLM as a separate `qwen-asr-serve` process and talk to it locally. This avoids fork/spawn interactions with gRPC's C-core [U].

### 14.4 FluidAudio on Mac (`hark-fluid`, Swift)
- **Server.** A Swift executable using **grpc-swift-2** (`GRPCServer` + HTTP/2 NIO transport with TLS, and the protobuf codegen plugin [U exact package names: `grpc-swift-nio-transport`, `grpc-swift-protobuf`]).
- **Model and bias.** Parakeet TDT 0.6B v3 on CoreML/ANE. Bias is FluidAudio's **CTC vocabulary boosting**, which needs a separate CTC 110M encoder for 0.6B models.
  - `BiasTerm` → `CustomVocabularyTerm(text: canonical, aliases: aliases.map(\.text))`. That is a 1:1 fit with hark's term model.
  - Use the **non-mutating `ctcTokenEvaluateCandidates`** API. It returns the untouched base transcript, which is a **free anchor**, plus candidates with `canonicalTerm`, `matchedAlias`, raw original/vocabulary CTC scores, `comparisonPassed`, `wordRange` and `baseTextUTF8Range`.
  - These map directly to `BiasHit{matched_form, via_alias, term_score, anchor_score, decision, text_byte_*}` with `hit_scores=true`.
  - `n_best[0]` is the rescored text when hark lets FluidAudio apply its decision, or the base text with hits reported only (`backend_params["fluid.apply"]="false"`), leaving the decision to the client.
  - FluidAudio's own docs warn that its default thresholds favour recall and that in dictation a wrong replacement costs more than a miss. The thresholds go in `backend_params`.
- **Streaming.** BUFFERED by default, because ANE decode of ≤ 30 s is fast. INCREMENTAL via FluidAudio's sliding-window manager when partials are wanted.
- **Alternative on the Mac:** run `hark-server-py` with **parakeet-mlx** (`transcribe_stream()`, token-level `AlignedResult` timestamps) and no bias. That is a useful baseline and is quicker to ship than the Swift server.

### 14.5 Local CPU fallback (in-process, laptop)
- **No gRPC loopback.** The fallback is a `LocalProvider` implementing the same `SpeechProvider` trait over `hark.v1` types, so there is no serialization, no ports, and no extra process to supervise. A UDS server mode (tonic UDS) is available for crash isolation if the engine proves unstable.
- **Engine candidates** [U, to benchmark on the laptop CPU]:
  - sherpa-onnx (Rust `sherpa-onnx`/`sherpa-rs` crates) with Parakeet TDT int8. Hotwords need `modified_beam_search` on transducers; support for NeMo TDT in sherpa-onnx is [U].
  - NeMo-Speech.cpp as a library with `gpu=-1` and a cache-aware RNNT, which supports boosting.
  - A smaller model (TDT-CTC-110M) if 0.6B misses the budget on CPU.
- **Capabilities** are static per engine: anchor `UNAVAILABLE` (too costly) and N-best 1.

### 14.6 Third-party APIs (client-side Rust adapters)
- Adapters live in the client: no extra hop, and API keys stay on the device. A user who wants them on the GPU box can run them in `hark-gateway` instead.
- The **privacy default** for every third-party provider is: send no context (`bias` and `free_text` stripped), so bias = NONE until the user opts in per provider.
- **Context timing.** Deepgram keyterms and Voxtral realtime `context_bias` must be set before the first audio frame (Voxtral: in `session.update`). For these, `midstream_update=UNSUPPORTED`, and the adapter delays opening the upstream connection until context revision 1 is ready (≤ ~150 ms), buffering audio locally meanwhile.
- **OpenAI.**
  - `gpt-transcribe`: `prompt` ← description + preceding text, `keywords[]` ← canonical terms (no `<`, `>` or newlines allowed), `languages[]` ← languages, `stream=true` for deltas, upload at EOA.
  - Word timestamps exist only on `whisper-1`, and `whisper-1`, `gpt-4o-transcribe` and `gpt-4o-mini-transcribe` shut down on 2027-02-26.
  - Live streaming would use the Realtime transcription API (`gpt-live-transcribe`) [U details].
- **Deepgram Nova-3.** WS streaming. `keyterm` ≤ 100 terms and ≤ 500 tokens total, otherwise the request is rejected, so the adapter truncates by rank. `words[]` with confidences map directly.
- **Voxtral.** `context_bias` = canonical terms (+ an optional description entry). Oversized requests fail rather than truncate, so the adapter truncates by rank.

---

## 15. Library choices and project layout

### 15.1 Rust (client, gateway, conformance tool)
- `tonic = { version = "0.14", features = ["tls-ring"] }`, `tonic-prost = "0.14"`, `prost = "0.14"`, build-dep `tonic-prost-build = "0.14"`. Current is 0.14.9 (2026-10-29), MSRV 1.88, repo now `grpc/grpc-rust`. Since 0.14, prost codegen lives in `tonic-prost`/`tonic-prost-build`.
- `tonic-health` (Watch client), `tonic-types` (rich `google.rpc.Status` details) [U], `tokio`, `tokio-stream`.
- No `tls-native-roots` (private CA only). It is needed only by HTTPS adapters (reqwest + rustls) for third-party APIs.
- Opus: `opus` crate (libopus).

Client streaming sketch:
```rust
let (tx, rx) = tokio::sync::mpsc::channel::<pb::TranscribeRequest>(256);
tx.send(start_msg(&utt, &ctx, &opts)).await?;               // at hotkey-down
let mut req = tonic::Request::new(tokio_stream::wrappers::ReceiverStream::new(rx));
req.set_timeout(Duration::from_millis(max_audio_ms + budget_ms + 2_000));
let mut inbound = asr.transcribe(req).await?.into_inner();  // resolves on server `accepted`
// capture task: tx.try_send(audio_chunk(seq, start_sample, pcm)) — on Full => mark lagging
// key-up: tx.send(end_of_audio(..)).await?; drop(tx);       // half-close
while let Some(msg) = inbound.message().await? {
    match msg.msg { Some(Msg::FinalResult(f)) => return Ok(f), Some(Msg::Status(s)) => liveness.bump(s), _ => {} }
}
```

### 15.2 Python servers
- **`grpcio` (grpc.aio)** 1.85.x (Python ≥ 3.10) with `grpcio-tools` (codegen), `grpcio-health-checking`, `grpcio-reflection`, `grpcio-status` (rich errors) and `mypy-protobuf` (typed stubs).
- **Why not `grpclib`** (pure-asyncio, h2-based, 0.4.9 from Dec 2025)? It is pleasant and has no C extension. But C-core gives better throughput and maturity, covers the full keepalive/mTLS/health/reflection ecosystem, and hark needs no exotic asyncio integration. grpclib is the fallback if grpcio wheels ever conflict with the CUDA/vLLM environment.
- **Structure.**
  - `server.py`: grpc.aio server, auth interceptor, health, reflection.
  - `session.py`: the §2.2 state machine, audio buffer, Opus decode, privacy enforcement.
  - `backends/{nemo_tdt,qwen3,mlx_parakeet}.py`: implement `Backend.capabilities()` and `open(start) → BackendSession{add_audio, update_context, finalize(budget)}`.
  - `gpu_worker.py`: one thread that owns the CUDA context, with an asyncio↔thread queue and micro-batching. **Never run inference on the event loop.**
  - `hits.py`: shared bias-hit derivation.

Handler sketch:
```python
async def Transcribe(self, request_iterator, context):
    it = request_iterator.__aiter__()
    first = await anext(it)
    if first.WhichOneof("msg") != "start":
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "first message must be start")
    sess = await self.sessions.open(first.start, context)  # admission + async bias compile
    yield pb.TranscribeResponse(accepted=sess.accepted())
    out: asyncio.Queue = asyncio.Queue()
    pump = asyncio.create_task(sess.pump(it, out))  # audio/context/end/cancel
    try:
        while (m := await out.get()) is not None:  # status/partials/final
            yield m
    finally:
        pump.cancel()
        await sess.close()  # also runs on RST_STREAM
```

### 15.3 Swift (Mac)
`grpc-swift-2` (2.x, released 2025, v1 in maintenance), its NIO HTTP/2 transport and protobuf packages, and SwiftProtobuf. Codegen via the SwiftPM plugin or `protoc-gen-grpc-swift` [U name].

### 15.4 Repository layout and CI
```
proto/hark/v1/hark.proto        # split later into asr/cleanup/common.proto
proto/riva/...                  # vendored Riva protos for the adapter
buf.yaml / buf.gen.yaml         # lint STANDARD, breaking FILE, codegen py/swift
crates/hark-proto               # tonic-prost-build; re-exports pb types
crates/hark-client              # providers, adapters, fusion, history
crates/hark-gateway             # optional tonic server hosting adapters (Riva, 3rd-party)
crates/hark-conformance         # replays .harkrec against any server, checks §2.2 invariants
server-py/hark_server           # grpcio
server-swift/HarkFluid          # grpc-swift-2
```
**Conformance suite.**
- It replays recorded `.harkrec` sessions against any server and asserts: message order, offset validity (byte ranges on char boundaries, words within audio), `finalize_budget_ms` honored or flagged, privacy ack consistent with flags, cancellation freeing sessions, and replay behavior.
- It runs in CI against the Python server (CPU, tiny model) and manually against the GPU, Swift and gateway servers.
- This is what keeps three implementations honest.

---

## 16. gRPC/protobuf vs WebSocket + JSON/msgpack

| Aspect | **gRPC + protobuf (this proposal)** | WebSocket + JSON | WebSocket + msgpack |
|---|---|---|---|
| Schema / codegen in Rust, Python, Swift | Strong; one `.proto`, generated types everywhere | Hand-written serde/pydantic/Codable; drift risk | Same as JSON |
| Schema evolution | Field numbers, `reserved`, `buf breaking` in CI | Discipline only | Discipline only |
| Binary audio | Native `bytes` | base64 (+33 %) or a second, binary framing mode | Native |
| Multiplexing | HTTP/2: health, capabilities, cleanup and transcribe on one warm connection | One socket per session or a custom mux | Same |
| Deadlines, cancellation, status codes | Built in (`grpc-timeout`, RST_STREAM, canonical codes + rich details) | Custom | Custom |
| Health checking | Standard `grpc.health.v1` with push `Watch` | Custom `/health` | Custom |
| Fit with backends | Riva / NeMo-Speech.cpp are gRPC → adapter is a type mapping | OpenAI Realtime, Deepgram, Voxtral and the NeMo-Speech.cpp HTTP realtime API are WS+JSON | — |
| Debuggability | Needs grpcurl/buf curl + reflection; binary on the wire | websocat/curl, human-readable | Binary |
| Toolchain cost | protoc/buf, `build.rs`, Swift plugin, pinned versions in 3 languages | Minimal | Minimal |
| Proxies / browsers | Needs end-to-end HTTP/2; no browser bidi | Works everywhere | Works everywhere |
| Per-chunk overhead (40 ms audio) | ~22 B | ~100 B + base64 inflation | ~30 B |
| Head-of-line blocking | TCP (same) | TCP (same) | TCP (same) |
| Python performance | C-core; fine | `websockets`; fine | fine |

**Pros of A**
- One typed, versioned contract across three server languages and many adapters.
- The rich result structure (N-best, words, hits, timings, flags) stays type-safe end to end, and the same types serve as the client's internal model and the recording format.
- Cancellation and deadlines come with the transport.
- Health `Watch` gives instant failover signals.
- mTLS is first-class in every stack.

**Cons of A, and mitigations**
- Heavier toolchain. *Mitigation:* buf + checked-in generated code for Python/Swift; Rust generates in `build.rs`.
- Harder ad-hoc debugging. *Mitigation:* reflection in dev and `hark-conformance --dump` to print sessions as JSON (protobuf JSON mapping).
- grpcio and CUDA/vLLM process models can interact badly [U]. *Mitigation:* start the engine first or run it as a separate process.
- proto3's lack of required fields and its zero-defaults need care. *Mitigation:* `optional` where zero is meaningful, plus explicit validators.
- No help with third-party WS APIs. *Mitigation:* adapters are needed under any choice.
- TCP head-of-line blocking over lossy links. *Mitigation:* the same as WS; QUIC/HTTP/3 gRPC is not mature in tonic/grpcio, so I don't rely on it.

**Verdict.** hark owns both ends, has three server languages and a deep result schema, and puts evaluation first. Those favour gRPC. WebSocket+JSON would win only if a browser client or zero-toolchain hacking mattered, and neither is a goal.

---

## 17. Uncertain items `[U]` and measurement plan

1. Parakeet-TDT-0.6B-v3 encoder and decode timings on the RTX 3060 for 2–30 s audio, with and without the anchor batch; GPU-PB per-stream compile time for 20/100/200 terms; cache-hit overhead. → Fill `PerfEstimate` and re-check the §9.2 budget.
2. Whether NeMo per-stream `BiasingRequestItemConfig` accepts `key_phrase_items_list` with per-phrase `alpha`, and whether biased + unbiased decoding over one encoder pass is achievable via public APIs.
3. Qwen3-ASR-1.7B latency on 12 GB alongside a cleanup LLM (VRAM contention); echo frequency with distractor contexts; logprob-based word confidence.
4. sherpa-onnx support for NeMo TDT + hotwords on CPU; laptop CPU RTF for 0.6B int8 vs 110M.
5. FluidAudio: per-term weights, word-confidence availability, and exact grpc-swift-2 package and plugin names.
6. Opus at 24/32 kbit/s vs PCM: term recall and false-substitution rate on hark recordings; VOIP vs AUDIO mode; the Python Opus binding.
7. Wayland paste latency (clipboard + virtual keyboard) on Hyprland.
8. Wi-Fi p95 RTT at home; Tailscale remote and DERP RTT from typical locations.
9. tonic lacking gRPC retry/hedging policies (irrelevant to the design, which hedges at the application level); `tonic-types` API for rich error details; grpc-swift-2 health service.
10. gzip effectiveness on PCM (expected to be poor; compression stays off regardless).
11. The OpenAI Realtime transcription session details for `gpt-live-transcribe`, and OpenAI keyword limits; Deepgram `alternatives`; Voxtral timestamps.
12. The exact JSON-schema `response_format` shapes in llama.cpp, vLLM and SGLang.

---

## References (checked 2026-11-12)
- tonic 0.14.9 docs, feature flags and changelog (grpc/grpc-rust): https://docs.rs/crate/tonic/latest · tonic-prost-build: https://docs.rs/crate/tonic-build/latest
- tonic `Endpoint` defaults (tcp_nodelay=true, keepalive unset): https://docs.rs/tonic/latest/src/tonic/transport/channel/endpoint.rs.html
- grpcio 1.85.1 on PyPI: https://pypi.org/project/grpcio/ · grpclib 0.4.9: https://pypi.org/project/grpclib/
- gRPC Python compression (gzip/deflate only): https://grpc.github.io/grpc/python/grpc.html · keepalive guide: https://grpc.io/docs/guides/keepalive/
- grpc-swift 2: https://www.swift.org/blog/grpc-swift-2/ · https://swiftpackageindex.com/grpc/grpc-swift-2
- NeMo word boosting / GPU-PB / per-stream boosting: https://docs.nvidia.com/nemo/speech/nightly/asr/asr_customization/word_boosting.html
- NeMo-Speech.cpp server and ASR configuration (Riva gRPC, speech_contexts, TDT ignores boosting, LINEAR_PCM only, no N-best, plaintext gRPC): https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/server.md · https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/asr/configuration.md
- Riva ASR proto reference: https://docs.nvidia.com/deeplearning/riva/archives/2-19-0/public/reference/protos/riva_asr.proto.html
- Qwen3-ASR README and `qwen_asr/inference/qwen3_asr.py` (context as system message, vLLM-only streaming without timestamps, ForcedAligner): https://github.com/QwenLM/Qwen3-ASR
- FluidAudio custom vocabulary (CTC boosting, aliases, non-mutating candidate evidence): https://github.com/FluidInference/FluidAudio/blob/main/Documentation/ASR/CustomVocabulary.md
- parakeet-mlx: https://github.com/senstella/parakeet-mlx
- sherpa-onnx hotwords: https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html · sherpa-rs: https://docs.rs/crate/sherpa-rs/latest
- OpenAI file transcription (gpt-transcribe prompt/keywords/languages, stream, deprecations): https://platform.openai.com/docs/guides/speech-to-text
- Deepgram keyterm prompting: https://developers.deepgram.com/docs/keyterm
- Mistral Voxtral context_bias: https://docs.mistral.ai/studio/audio/speech-to-text/bias-context-and-vocabulary
- Tailscale connection types (direct vs DERP): https://tailscale.com/docs/reference/connection-types
- Opus and ASR: https://arxiv.org/abs/2002.00122 · Nuance audio formats: https://docs.nuance.com/mix/apis/asr-grpc/v1/ref-topics/audio-formats/ · Rust `opus`: https://docs.rs/opus/latest/opus/
