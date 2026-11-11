# Proposal C: standards-first, minimal-custom protocol ("hark profile")

Designer C · 2026-11-12 · Status: proposal
Legend: **[U]** = unverified (no primary doc or test confirmed it; check before relying on it). Unmarked factual
claims were checked against the primary docs or protos listed in §11.

---

## 0. TL;DR

- **Don't invent an ASR wire protocol. Use NVIDIA Riva ASR gRPC (`nvidia.riva.asr.RivaSpeechRecognition`) as is**,
  and define a small **"hark profile" (`hark-asr/1`)** on top of it. The profile has:
  - **Conventions** on standard fields. One `SpeechContext` per term group (canonical word first, then its aliases),
    with a per-group `boost`. Key-up = half-close the stream. The server sends one final result.
  - **4 request keys** in Riva's own extension map `RecognitionConfig.custom_configuration`:
    `hark.v`, `hark.context`, `hark.anchor`, `hark.retention`.
  - **2 optional response trailers** (gRPC trailing metadata): `hark-alt-roles` and the W3C-syntax `server-timing`.
  - **1 discovery document**: a JSON object in the `parameters` map of the standard
    `GetRivaSpeechRecognitionConfig` RPC (`hark.profile`, `hark.capabilities`).
- No `.proto` fork, no new service, no new messages. Stock Riva servers (NIM, NeMo-Speech.cpp `riva_server`) work
  unmodified at conformance level **L0**. hark's own servers reach **L1**.
- Everything else also uses existing standards:
  - **Batch / fallback / 3rd-party ASR:** OpenAI `/v1/audio/transcriptions`, with a per-dialect field table.
  - **Cleanup:** OpenAI Chat Completions with `response_format: json_schema`. Only the edit-list schema is hark's.
  - **Health:** `grpc.health.v1`.
  - **Vendor streaming APIs** (Deepgram, AssemblyAI, OpenAI Realtime): thin adapters in the client only.
- **Custom code needed:**
  - One Python Riva servicer for NeMo GPU-PB (and optionally Qwen3-ASR).
  - Client adapters in Rust.
  - Optionally a small Swift Riva servicer for FluidAudio.
- **Free with no new code:**
  - Laptop CPU fallback and Mac: NeMo-Speech.cpp `riva_server` (CPU or Metal), using the same adapter.
  - Qwen3-ASR: vLLM's OpenAI transcription endpoint. vLLM maps `prompt` to Qwen3-ASR's system context.

---

## 1. What hark needs (from `00-context.md`)

| # | Need | Notes |
|---|---|---|
| R1 | Per-request bias list: terms + **spoken aliases** + **weights** | Shortlist of 20–100 terms. Decode-time boosting is preferred over post-hoc LLM fixes. |
| R2 | Free-text context | Qwen3-ASR system prompt, Whisper/OpenAI `prompt`. Echo risk. |
| R3 | N-best | Used for client fusion and canonicalization. |
| R4 | Word timings + confidence | Used for acoustic gating. |
| R5 | Unbiased anchor | Must always be available to guard against false substitutions. |
| R6 | Stream audio during speech; key-up only finalizes | Target ≤1 s p95 key-up → pasted (LAN). |
| R7 | Cancellation | Esc, a new utterance replacing the old one, timeout → failover. |
| R8 | Capability discovery | Per provider and per model. |
| R9 | Privacy flags | Context is screen text and code. |
| R10 | Auth | Tailscale/WireGuard, mTLS and/or bearer tokens. |
| R11 | Codec | 16 kHz mono PCM is fine on LAN. |
| R12 | Health / routing / fallback | Provider registry. |

---

## 2. Candidates surveyed (verified facts)

### 2.1 Riva ASR gRPC (`riva/proto/riva_asr.proto`, MIT)

**RPCs:** `Recognize` (unary), `StreamingRecognize` (bidirectional), `GetRivaSpeechRecognitionConfig` (returns per-model
`map<string,string> parameters`).

**`RecognitionConfig` fields:**
- `encoding`, `sample_rate_hertz`, `language_code` (BCP-47)
- `max_alternatives` (field 4)
- `speech_contexts` (repeated `SpeechContext {repeated string phrases = 1; float boost = 4;}`)
- `enable_word_time_offsets`, `enable_automatic_punctuation`, `verbatim_transcripts` (ITN on/off)
- `model`, `diarization_config`
- **`map<string,string> custom_configuration = 24`**, documented as *"Custom fields for passing request-level
  configuration options to plugins used in the model pipeline"*
- `endpointing_config = 25`

**`StreamingRecognizeRequest`:** the first message is the config; later messages carry `audio_content`. It also has a
per-chunk **`map<string,string> runtime_config = 3`**. Documented key: `force_eou`. NIM docs cite *push-to-talk
release* as the use case.

**Request IDs:** `RequestId id = 100` on requests and responses.

**Responses:**
- `StreamingRecognitionResult {alternatives, is_final, stability, audio_processed, pipeline_states}`
- `SpeechRecognitionAlternative {transcript, confidence, words, language_code}`
- `WordInfo {start_time ms, end_time ms, word, confidence, speaker_tag, language_code}`

**Documented semantics:**
- Recognition ends when the client closes the stream.
- Alternatives are ordered best-first.
- Confidence is "roughly natural-log probability". `0.0` is the "not set" sentinel.
- Words are documented for the top hypothesis only.

**Codecs (`AudioEncoding`):** LINEAR_PCM, FLAC, MULAW, OGGOPUS, ALAW.

**NIM behaviour (Speech NIM docs):**
- Boost scales differ by model: CTC and Nemotron-streaming take −200..200. Other RNNT/TDT models take 0.5–2.0, and
  **a single score applies to all words**.
- Dynamic lists of about 500 words for Parakeet RNNT/TDT. No OOV boosting for RNNT/TDT.
- NIM itself uses `custom_configuration` for runtime keys: `apply_partial_pnc`, `apply_partial_itn`,
  `enable_preprocessing`, `neural_vad.*`, `get_vad_probabilities`.
- So this map is NVIDIA's own vendor-extension mechanism, which hark can reuse legitimately.

**Client libraries:** Rust crate `nvidia_riva_2026` (tonic client generated from the public protos, includes
`grpc.health.v1`); Python `nvidia-riva-client`; C++ clients.

### 2.2 NeMo-Speech.cpp (NVIDIA, ggml; CUDA / CPU / Metal / Vulkan)

**Binaries:** `riva_server` (Riva-compatible gRPC) and `nemo-speech serve` (OpenAI-subset HTTP plus a project-specific
realtime WebSocket).

**Riva adapter limits (its docs):**
- **LINEAR_PCM only.**
- **N-best not implemented.** `max_alternatives` is accepted, but one alternative is returned.
- Confidence: interim results report 0.0. Final greedy CTC reports mean token posterior. **RNNT and beam results
  report 1.0.**
- **Plaintext only.** Put a TLS proxy in front of it for remote use.

**Boosting:**
- Works on cache-aware RNNT (Nemotron 3.5 / Nemotron Speech Streaming): typical scores 2–3, capped at 5.
- Works on CTC with the Flashlight LM decoder.
- **Parakeet TDT ignores `speech_contexts`.**
- Endpointing is off by default: **one final when the client closes the stream.**
- `custom_configuration["stop_history_eou"]` and `runtime_config["force_eou"]` are honoured.

**HTTP API:** OpenAI transcription subset with a non-standard `speech_contexts` field (same JSON shape as gRPC).
`prompt` becomes one phrase at boost 10.

**CPU speed:** Nemotron Speech Streaming 0.6B Q8 runs at 27 ms per 160 ms chunk, about 6× real time (README; which
CPU **[U]**).

**[U]:**
- What `GetRivaSpeechRecognitionConfig` returns.
- How promptly server-side work stops when an RPC is cancelled.

### 2.3 OpenAI `/v1/audio/transcriptions` (batch, multipart) and its dialects

**OpenAI fields:**
- `prompt`, `language` / `languages`
- **`keywords`** (string array; `gpt-transcribe` only; no weights)
- `include[]=logprobs` (gpt-4o-* only, json only)
- `timestamp_granularities[]=word` (needs `verbose_json`; not for gpt-4o)
- `stream` (SSE; output only), `chunking_strategy`
- **No N-best. No streaming upload.**

**Dialects:**
- **vLLM:** for Qwen3-ASR, `prompt` → `<|im_start|>system\n{context}` (verified in vLLM `qwen3_asr.py`).
- **NeMo-Speech.cpp:** `speech_contexts`; `verbose_json` words include `confidence`.
- **Mistral:** `context_bias` list.
- **NIM Nemotron Transcribe:** rejects a non-empty `prompt`, and **returns 400 for unknown fields**.

**Implication:** extensions can't be sent blindly. Each provider needs its own field table, gated by capabilities.

### 2.4 OpenAI Realtime transcription (WebSocket JSON) and its dialects

**OpenAI GA:**
- Connect to `wss://…/v1/realtime?intent=transcription`.
- `session.update{type:"transcription", audio.input.format{type:"audio/pcm", rate:24000}, transcription{model, prompt,
  keywords, languages, delay}, turn_detection:null}`.
- Client sends `input_audio_buffer.append` (base64), then `commit`.
- Server sends `…input_audio_transcription.delta` / `.completed`.
- **`gpt-live-transcribe` returns no word timestamps and no confidence.**

**Dialects:**
- **vLLM:** `transcription.delta` / `transcription.done`, `commit{"final":true}`, 16 kHz.
- **Speaches:** `intent=transcription` URL parameter. `response.cancel` is unsupported.
- **NeMo-Speech.cpp:** a "project-specific event protocol", binary PCM frames allowed.

So this is a family of similar protocols, not one standard.

### 2.5 Wyoming (Home Assistant / OHF)

- **Framing:** JSONL header + optional data + binary payload.
- **STT flow:** `transcribe{name, language, context}` → `audio-start` / `audio-chunk` / `audio-stop` → `transcript{text}`.
- **Streaming:** `transcript-start` / `-chunk` (each chunk replaces the whole text) / `-stop`.
- **Discovery:** `describe` → `info` (models, languages, `supports_transcript_streaming`).
- **Extensibility:** `context` is free-form and programs must ignore unknown keys. Servers drop unknown events.
- **"No authentication or encryption, by design."**
- **Missing:** N-best, timings, confidence, bias.

### 2.6 Speaches (formerly faster-whisper-server)

- OpenAI-compatible transcriptions plus a Realtime WebSocket (transcription-only via `intent`).
- Auth via Bearer, `X-API-Key`, or `api_key` query parameter.
- `hotwords` passthrough: **[U]** (faster-whisper itself has `hotwords` and `initial_prompt`).

### 2.7 Chat Completions + `json_schema` (cleanup); audio-in chat (speech-LLMs)

- **llama.cpp:** `response_format` accepts `{"type":"json_schema","schema":…}` and the OpenAI wrapper
  `{"type":"json_schema","json_schema":{name,strict,schema}}`.
- **llama.cpp cancellation on disconnect:**
  - Works in direct mode (maintainer comment on #24496).
  - Reported broken in router/proxy mode (same issue thread).
- **vLLM / SGLang / Ollama** support `json_schema`. **[U]** exact versions and subsets.
- **Speech-LLMs** (NIM Nemotron Transcribe, NeMo-Speech.cpp SALM, Qwen3-ASR via vLLM chat): audio goes in as
  `input_audio` / `audio_url`.
  - Context goes in as instruction text.
  - Output streams; input does not.

### 2.8 MCP

- JSON-RPC tools, resources and prompts for LLM hosts.
- No bidirectional binary audio streaming. Audio would travel base64 inside tool results.
- Built for agent ↔ tool integration, not for low-latency media.

### 2.9 Vendor APIs (adapter targets)

- **Deepgram:** `keyterm` (repeated; 500-token cap per request; mid-stream `Configure{keyterms}`).
- **AssemblyAI Universal-Streaming:** `keyterms_prompt` (≤100 terms, ≤50 chars each).
- **Mistral:** `context_bias`.
- **OpenAI:** `keywords`, `prompt`.
- **Deepgram** streaming N-best and Finalize semantics: **[U]**.

### 2.10 Backend-native biasing APIs (what servers will translate to)

- **NeMo GPU-PB:**
  - **Per-stream** biasing only for **greedy label-looping transducers** (RNNT/TDT), via
    `BiasingRequestItemConfig(boosting_model_cfg=BoostingTreeModelConfig(key_phrases_list | key_phrase_items_list),
    boosting_model_alpha, cache_key)`.
  - **Per-phrase `alpha`** via `key_phrase_items_list`.
  - `bpe_mode="case_insensitive"`.
  - `cache_key` makes per-stream overhead almost zero.
  - Beam search and N-best are not available with per-stream biasing.
- **NeMo CTC-WS:** context file with alternative spellings per term (`gpu_gpu_g p u`). These map directly to aliases.
- **FluidAudio:**
  - `CustomVocabularyTerm(text:, aliases:)`, applied as **post-decode CTC rescoring**.
  - `ctcTokenEvaluateCandidates` returns the **untouched base text plus candidates**, which is effectively a free
    anchor.
  - Dictation defaults are recall-biased. `cbw: 0, minSimilarity: 0.7` stopped false replacements in their tests.
- **Qwen3-ASR:** context = system turn. Timestamps need Qwen3-ForcedAligner (offline only). vLLM streaming exists.
- **sherpa-onnx:** per-stream hotwords (`create_stream(hotwords)`) need `modified_beam_search`.

---

## 3. Comparison matrix

Legend: ✅ native · ⚠️ partial / dialect / with caveat · ❌ absent · ext = needs an extension

**R1 (bias list), R2 (free-text context), R3 (N-best)**

| Option | R1 bias + aliases + weights | R2 free-text context | R3 N-best |
|---|---|---|---|
| Riva gRPC | ✅ `speech_contexts` (per-context boost). Aliases: ext/convention. Scale model-specific; some models ignore it | ext (`custom_configuration`) | ✅ `max_alternatives` (some servers return 1) |
| OpenAI /audio/transcriptions | ⚠️ `keywords` (OpenAI, no weights), `context_bias` (Mistral), `speech_contexts` (NeMo-Speech.cpp) | ✅ `prompt` (vLLM → Qwen3 system turn) | ❌ |
| OpenAI Realtime (+ dialects) | ⚠️ `keywords` (no weights) | ✅ `prompt` | ❌ |
| Wyoming | ❌ | ⚠️ `context` object | ❌ |
| Speaches | ❌ / [U] hotwords | ✅ prompt | ❌ |
| Chat Completions (audio in / cleanup) | ❌ | ✅ instruction | ⚠️ `n` (often only 1) |
| MCP | ❌ | n/a | ❌ |

**R4 (words + confidence), R5 (anchor), R6 (streaming during speech)**

| Option | R4 words + confidence | R5 anchor | R6 streaming during speech |
|---|---|---|---|
| Riva gRPC | ✅ ms + per-word confidence (semantics vary) | ext, or a 2nd RPC | ✅ bidirectional; half-close = finalize; `force_eou` |
| OpenAI /audio/transcriptions | ⚠️ words (`verbose_json`); token logprobs (gpt-4o, json only) | 2nd request | ❌ upload after key-up |
| OpenAI Realtime | ❌ (gpt-live); ⚠️ older models logprobs [U] | 2nd session | ✅ append/commit |
| Wyoming | ❌ | 2nd connection | ✅ audio-chunk / audio-stop |
| Speaches | ⚠️ `verbose_json` [U] | 2nd request | ✅ realtime |
| Chat Completions | ⚠️ vendor `transcription.words` | 2nd request | ❌ (output-only streaming) |
| MCP | ❌ | ❌ | ❌ |

**R7 (cancellation), R8 (discovery), R9 (privacy flags)**

| Option | R7 cancel | R8 discovery | R9 privacy flags |
|---|---|---|---|
| Riva gRPC | ✅ RPC cancel + deadlines | ⚠️ `GetRivaSpeechRecognitionConfig` (free-form map) + `grpc.health.v1` | ext |
| OpenAI /audio/transcriptions | ⚠️ close the connection (server-dependent) | ⚠️ `/v1/models` | ❌ |
| OpenAI Realtime | ⚠️ `clear` before commit; close the socket | ❌ | ❌ |
| Wyoming | ⚠️ close TCP | ✅ describe/info | ❌ |
| Speaches | ⚠️ (`response.cancel` unsupported) | ⚠️ `/v1/models` | ❌ |
| Chat Completions | ⚠️ close (llama.cpp direct OK; router [U]) | ⚠️ `/v1/models` | ❌ (`store` is not on transcription) |
| MCP | ✅ (irrelevant here) | ✅ | ❌ |

**R10 (auth), R11 (codec), deployed implementations relevant to hark**

| Option | R10 auth | R11 codec | Implementations |
|---|---|---|---|
| Riva gRPC | ✅ TLS/mTLS + `authorization` metadata (NeMo-Speech.cpp: plaintext) | PCM / FLAC / Opus / µ-law / A-law (NeMo-Speech.cpp: PCM) | Riva/NIM, NeMo-Speech.cpp (CUDA/CPU/Metal), Rust/Python/C++ clients |
| OpenAI /audio/transcriptions | ✅ Bearer | files | vLLM, whisper.cpp, Speaches, NeMo-Speech.cpp, NIM, OpenAI, Groq, Mistral |
| OpenAI Realtime | ✅ Bearer | pcm16 24 kHz (OpenAI) / 16 kHz (vLLM); base64 JSON | OpenAI, vLLM, Speaches, NeMo-Speech.cpp (all differ) |
| Wyoming | ❌ by design | raw PCM | HA STT servers |
| Speaches | ✅ | files / pcm | faster-whisper |
| Chat Completions | ✅ | base64 files | llama.cpp, vLLM, SGLang, NIM, OpenAI |
| MCP | ✅ OAuth | base64 | agent hosts |

**Reading:** Riva gRPC covers most needs natively (R1 weights, R3, R4, R6, R7, R10, R11). It has **one** exact schema
shared by several independent implementations, and it ships an extension map. Its gaps are R2 aliases/context, R5 and
R9, which fit into `custom_configuration` plus conventions. Every OpenAI-shaped option lacks N-best and weights, and its
streaming variant is fragmented into dialects. Wyoming and MCP lack most of the payload hark needs.

---

## 4. Decision

| Concern | Wire | Custom surface |
|---|---|---|
| Streaming ASR (primary, all self-hosted ASR) | **Riva ASR gRPC + hark profile** | 4 request keys, 2 trailers, 1 discovery JSON, conventions |
| Batch / fallback / speech-LLM / 3rd-party HTTP ASR | **OpenAI `/v1/audio/transcriptions`** + per-dialect field table | none on the wire (client adapter config only) |
| Vendor streaming ASR (Deepgram, AssemblyAI, OpenAI Realtime) | vendor native | client adapters only |
| Cleanup | **OpenAI Chat Completions + `json_schema`** | edit-list JSON Schema |
| Health | `grpc.health.v1` / HTTP `GET /health`, `/v1/models` | none |

**Rejected as primary:**
- **OpenAI Realtime:** fragmented dialects; no weights, N-best or timings; base64 audio.
- **Wyoming:** no auth; too little payload. It could still be an optional text-only adapter for Home Assistant STT
  servers.
- **MCP:** wrong tool for real-time media.
- **A fully custom protocol:** see §9 for what it would add.

---

## 5. The hark profile of Riva ASR (`hark-asr/1`)

### 5.1 Services used (proto vendored from `nvidia-riva/common`, pinned commit; no modifications)

| RPC | Use |
|---|---|
| `RivaSpeechRecognition/StreamingRecognize` | every dictation |
| `RivaSpeechRecognition/Recognize` | re-runs on the buffered utterance (failover, swap-to-raw, eval replay) |
| `RivaSpeechRecognition/GetRivaSpeechRecognitionConfig` | discovery (§5.9) |
| `grpc.health.v1.Health/Check` (service `nvidia.riva.asr.RivaSpeechRecognition`) | registry health; L1 servers MUST implement it |

**Conformance levels:**
- **L0:** any Riva server. The client uses only standard fields. Capabilities come from client config.
- **L1:** a hark-aware server. It advertises `hark.profile=1` and implements all of §5.

### 5.2 Lifecycle (push-to-talk)

```
(idle)    warm HTTP/2 channel (TLS done), keepalive pings, health OK
key-down  open StreamingRecognize (HEADERS only) · start capture · buffer audio locally
≤300 ms   context ranker done → send msg#1 streaming_config (bias + context)
          flush buffered audio, then 40 ms audio_content chunks while speaking
          ◄ optional interim results (interim_results=true, for HUD only)
key-up    send tail audio · half-close (END_STREAM)          ← "finalize"
          ◄ exactly one StreamingRecognizeResponse{results[0].is_final=true} (L1)
          ◄ trailers: grpc-status=0, hark-alt-roles, server-timing
client    gate / fuse / canonicalize → optional cleanup (deadline) → paste
abort     client cancels the RPC (RST_STREAM CANCEL) at any point
```

**Why context goes first.** Riva requires the config in the first message, so it can't change mid-stream. hark
**buffers audio on the client** until context is ready (budget ≤300 ms). After that, catching up costs nothing on a LAN.
A late-context patch via `runtime_config` was considered and **not adopted** (more surface, small gain).

### 5.3 Request mapping (message #1, `StreamingRecognitionConfig`)

| hark concept | Riva field | Profile rule |
|---|---|---|
| audio format | `config.encoding=LINEAR_PCM`, `sample_rate_hertz=16000`, `audio_channel_count=1` | MUST. Other encodings only if advertised. |
| language | `config.language_code` | BCP-47, or `"auto"` if `"auto"` is listed in capabilities. |
| model | `config.model` | Name from discovery. Empty = server default. |
| N-best | `config.max_alternatives` | 1–5. The server may return fewer. |
| word timings + confidence | `config.enable_word_time_offsets=true` | MUST for hark. |
| punctuation / ITN | `enable_automatic_punctuation`, `verbatim_transcripts` | Set per provider in the registry (default: punct on, ITN on). |
| interim results | `interim_results` | Default false. |
| bias terms | `config.speech_contexts[]` | §5.4. |
| utterance id | `StreamingRecognizeRequest.id.value` (field 100) | UUIDv7 per utterance. L1 echoes it in responses. |
| extensions | `config.custom_configuration` | §5.5. **Sent only to servers that advertise L1.** |
| endpointing | `config.endpointing_config` | L0: raise `stop_history` to the maximum and concatenate multiple finals if they arrive. L1: the server MUST NOT endpoint hark requests. |

### 5.4 Bias encoding: term groups in standard `speech_contexts` (no extension)

- **One `SpeechContext` per term group:**
  - `phrases[0]` = canonical written form.
  - `phrases[1..]` = spoken aliases / alternative spellings.
  - `boost` = that group's weight in the **server's native scale**.
- **Weight → boost:** hark's ranker produces `w ∈ (0,1]`. The client maps it as
  `boost = boost_min + w·(boost_max − boost_min)`. The bounds come from capabilities, or from the registry for L0.
  Calibration happens per provider and is measured in eval. Examples:
  - NIM TDT: 0.5–2.0.
  - NeMo-Speech.cpp RNNT: about 1–3 (cap 5).
  - NeMo GPU-PB: the per-phrase `alpha` multiplier.
- **Single-score backends** (`per_context_boost=false`): send `boost_default` for every group, and drop terms below a
  weight threshold instead.
- **Servers with `alias_groups=false`** (any L0 server): the client leaves out canonical forms that can't be spoken
  (anything outside `[\p{L}\p{N} '.-]`, e.g. `~/.config/hypr`) and sends only the speakable aliases.
- **Canonicalization (alias → canonical) is always done by the client.**
- **L1 servers with `alias_groups=true` MAY:**
  - use the grouping, e.g. FluidAudio `CustomVocabularyTerm(text: phrases[0], aliases: phrases[1...])`, NeMo CTC-WS
    spellings, or a Qwen3 prompt line `kubectl (spoken: "cube control")`;
  - output the canonical form in `biased` alternatives.
- **They MUST NOT rewrite the `anchor` alternative.**
- **Negative boosts** (to suppress known distractors) are sent only if `negative_boost=true` (NIM CTC, Nemotron
  streaming).
- **Size:** `Σ phrases ≤ capabilities.bias.max_phrases`. Otherwise the server returns `INVALID_ARGUMENT`; it does not
  truncate silently.

### 5.5 Extension keys (`RecognitionConfig.custom_configuration`; values are UTF-8 strings)

| Key | Value grammar | Meaning | L1 server MUST |
|---|---|---|---|
| `hark.v` | `"1"` | Marks a hark-profile request. | Apply all §5 conventions. Reject unknown major versions with `INVALID_ARGUMENT`. |
| `hark.context` | free text, ≤ `capabilities.context.max_chars` chars | Client-rendered free-text context (app, window title, nearby text, repo vocabulary sentence). | Use it if `context.free_text=true` (e.g. append to the Qwen3 system turn after the rendered term list). Otherwise ignore it. **Never log it.** |
| `hark.anchor` | `"1"` | Also decode with **no bias and no context**. | Append the unbiased hypothesis as an extra alternative and label it in `hark-alt-roles`. If it can't be computed, omit it and label nothing (the client sees no `anchor` role). |
| `hark.retention` | `"none"` | Do not persist or log audio, transcripts, `speech_contexts`, or `hark.context`. Metrics without content are allowed. | Honour it, or fail with `FAILED_PRECONDITION`. **Never ignore it silently.** |

- **Key naming:** NIM-style flat keys, namespaced with a `hark.` prefix so they can't collide with `neural_vad.*` and
  similar.
- **Reserved:** future keys use `hark.<name>`.
- **Unknown keys:** L1 servers ignore unknown `hark.*` keys except `hark.v`.

### 5.6 Response conventions (final result)

- **One final.** L1: exactly one `results[0]` with `is_final=true`, covering the whole utterance, then status OK. L0: the
  client concatenates finals in order.
- **Alternatives:** best-first biased N-best; the anchor (if requested and computed) comes **last**. Each one's role is
  given in `hark-alt-roles` (§5.7). Riva's best-first ordering is intentionally broken only for hark requests that opted
  in.
- **Words:** L1 SHOULD fill `words` on **every** alternative (Riva documents words only for the top one). hark needs
  per-word alignment between biased and anchor hypotheses for gating.
  - `start_time` / `end_time`: ms from the first audio byte sent.
- **Confidence:** the scale is declared in capabilities (`prob` recommended: (0,1], with 0 = unset).
  - `confidence: "none"` declares that values are meaningless. Example: NeMo-Speech.cpp RNNT always returns 1.0.
- **Interim results** (if requested): no anchor, no words. `stability` as in Riva.

### 5.7 Trailers (gRPC trailing metadata; small, optional except as noted)

| Trailer | Grammar | Notes |
|---|---|---|
| `hark-alt-roles` | `role *("," role)`; `role = "biased" / "anchor" / "plain"` | **MUST** when `hark.anchor=1`. One entry per alternative of the final result. `plain` = decoded with no bias or context because none was sent or the model ignores it. |
| `server-timing` | W3C Server-Timing syntax, e.g. `queue;dur=1, encode;dur=23, decode;dur=8, anchor;dur=7, align;dur=0, total;dur=41` | SHOULD. Used for latency SLO tracking. Durations count from half-close. |

- **Why trailers:** they keep the message schema unchanged and are invisible to stock Riva clients.
- **Size:** tiny compared with gRPC header-list limits (gRPC suggests ≥8 KiB; hyper/tonic default 16 KiB).
- **Not in trailers:** the anchor text itself.

### 5.8 Errors (standard gRPC status codes)

| Status | When | Client action |
|---|---|---|
| `INVALID_ARGUMENT` | bad config, too many phrases, context too long, unsupported `hark.v` | Fix and retry once on the same provider; else fail over. |
| `FAILED_PRECONDITION` | `hark.retention=none` can't be honoured | Drop context and retry, or fail over (per policy). |
| `RESOURCE_EXHAUSTED` | overloaded / queue full | Fail over immediately. |
| `UNAVAILABLE` / `DEADLINE_EXCEEDED` | transport problem / deadline hit | Fail over with the buffered audio via `Recognize`. |
| `CANCELLED` | client cancelled | — |

### 5.9 Capability discovery

`GetRivaSpeechRecognitionConfig(model_name="")` returns one `model_config` per model. L1 servers add:
- `parameters["hark.profile"] = "1"`
- `parameters["hark.capabilities"] = <JSON per the schema below>`

If they're absent, the server is L0. The client then uses the provider's static `capabilities` from config, and
**never sends `hark.*` keys** (avoids unknown-key behaviour; whether NIM rejects unknown keys is **[U]**).

**Trust rule:** server-declared `retention` is advisory. The client's per-provider privacy policy always wins (§8.4).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "hark:asr-capabilities:1",
  "type": "object",
  "required": ["profile", "model", "languages", "audio", "bias", "context", "nbest", "anchor", "words", "retention"],
  "properties": {
    "profile": { "const": "hark-asr/1" },
    "model": { "type": "string" },
    "languages": { "type": "array", "minItems": 1, "items": { "type": "string" } },
    "audio": {
      "type": "object",
      "required": ["encodings", "sample_rates_hz"],
      "properties": {
        "encodings": { "type": "array", "items": { "enum": ["LINEAR_PCM", "FLAC", "OGGOPUS", "MULAW", "ALAW"] } },
        "sample_rates_hz": { "type": "array", "items": { "type": "integer" } },
        "max_audio_ms": { "type": "integer", "minimum": 1 }
      }
    },
    "streaming": {
      "type": "object",
      "properties": {
        "decode_during_speech": { "type": "boolean" },
        "interim_results": { "type": "boolean" }
      }
    },
    "bias": {
      "type": "object",
      "required": ["method"],
      "properties": {
        "method": { "enum": ["none", "gpu-pb", "rnnt-tree", "flashlight", "ctc-ws", "rescoring", "prompt"] },
        "per_context_boost": { "type": "boolean" },
        "boost_min": { "type": "number" },
        "boost_max": { "type": "number" },
        "boost_default": { "type": "number" },
        "negative_boost": { "type": "boolean" },
        "max_phrases": { "type": "integer", "minimum": 0 },
        "alias_groups": { "type": "boolean" },
        "oov": { "type": "boolean" }
      }
    },
    "context": {
      "type": "object",
      "required": ["free_text"],
      "properties": {
        "free_text": { "type": "boolean" },
        "max_chars": { "type": "integer", "minimum": 0 },
        "echo_risk": { "type": "boolean" }
      }
    },
    "nbest": {
      "type": "object",
      "required": ["max"],
      "properties": {
        "max": { "type": "integer", "minimum": 1 },
        "max_with_bias": { "type": "integer", "minimum": 1 }
      }
    },
    "anchor": {
      "type": "object",
      "required": ["inline"],
      "properties": {
        "inline": { "type": "boolean" },
        "shared_encoder": { "type": "boolean" }
      }
    },
    "words": {
      "type": "object",
      "required": ["timestamps", "confidence"],
      "properties": {
        "timestamps": { "type": "boolean" },
        "all_alternatives": { "type": "boolean" },
        "confidence": { "enum": ["none", "prob", "logprob"] }
      }
    },
    "retention": { "enum": ["none", "metadata-logs", "content-logs", "stored"] },
    "max_concurrent_streams": { "type": "integer", "minimum": 1 }
  }
}
```

- **Forward compatibility:** extra properties are allowed; clients ignore unknown ones.
- **Static entry in the client registry** (for L0 servers and every non-Riva provider). Informative TOML:

```toml
[[provider]]
id        = "desk-gpupb"
wire      = "riva"                      # riva | openai-transcriptions | openai-realtime | deepgram | assemblyai | wyoming
endpoint  = "https://desk.tail-net.ts.net:50051"
model     = "parakeet-tdt-0.6b-v3"
profile   = "auto"                      # auto = discover; "none" = never send hark.* keys
auth      = { kind = "mtls", cert = "~/.config/hark/client.crt", key = "~/.config/hark/client.key", ca = "~/.config/hark/ca.crt" }
anchor    = "inline"                    # inline (L1) | parallel (2nd RPC / request) | none
privacy   = { send_terms = true, send_free_text = true, require_retention_none = true, trust = "tailnet" }
finalize_timeout_ms = 600
priority  = 10
# used only when the server does not advertise capabilities:
capabilities = { bias = { method = "rnnt-tree", boost_min = 1.0, boost_max = 3.0, per_context_boost = true }, nbest = { max = 1 }, words = { timestamps = true, confidence = "none" }, context = { free_text = false } }
```

### 5.10 Anchor without L1 (`anchor = "parallel"`)

- **Riva L0:** open a second `StreamingRecognize` with the same audio and no `speech_contexts`.
- **OpenAI-HTTP:** after key-up, send a second request without `prompt` / `keywords` / `context_bias`.
- **Cost:** double upload (≈32 KB/s, negligible on LAN) and double compute (no shared encoder).
- **Skip it** when the backend ignores bias anyway (e.g. Parakeet TDT on NeMo-Speech.cpp), or on the laptop CPU.

---

## 6. Non-streaming wires

### 6.1 OpenAI `/v1/audio/transcriptions` adapter (batch, failover, speech-LLMs, 3rd-party)

The client converts its normalized request into the provider's dialect. **Only fields listed for that dialect are
sent**, because several servers return 400 on unknown fields.

| Dialect | Context | Bias terms | Words / confidence | Notes |
|---|---|---|---|---|
| OpenAI | `prompt` | `keywords` = canonicals + aliases (no weights; top-K by weight) | `verbose_json` + `timestamp_granularities[]=word` (whisper-1); `include[]=logprobs` (gpt-4o-*, json) | Privacy gate applies to cloud. |
| vLLM (Qwen3-ASR, Whisper) | `prompt` (Qwen3: becomes the system turn) | Rendered into `prompt` | Qwen3 word timestamps via this route **[U]** | Zero-code Qwen3 path. |
| NeMo-Speech.cpp HTTP | — | `speech_contexts` JSON string (same shape as gRPC) | `verbose_json` words + `confidence` | — |
| Mistral (Voxtral) | descriptive context **[U]** | `context_bias` | **[U]** | — |
| NIM Nemotron Transcribe | use `/v1/chat/completions` instruction | rendered into the instruction | `word_timestamps` + `transcription.words` | Multipart rejects `prompt`. Unknown fields → 400. |
| whisper.cpp / Speaches | `prompt` | `hotwords` **[U]** | `verbose_json` **[U]** | — |

- **Audio:** WAV PCM16 16 kHz from the client's utterance buffer.
- **Cancel:** close the connection. NIM documents that this cancels server work. Elsewhere it is server-dependent.

### 6.2 Cleanup: Chat Completions + `json_schema`

**Request:**

```
POST /v1/chat/completions
{
  model,
  temperature: 0,
  max_tokens: 256,
  stream: false,
  messages: [system rules, user JSON {text, protected_spans, terms}],
  response_format: {
    type: "json_schema",
    json_schema: { name: "hark_edits", strict: true, schema: <below> }
  }
}
```

**Edit-list schema.** All properties are required, so it is valid in strict mode.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["edits"],
  "properties": {
    "edits": {
      "type": "array",
      "maxItems": 12,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["find", "replace", "nth", "reason"],
        "properties": {
          "find":    { "type": "string", "minLength": 1, "maxLength": 120 },
          "replace": { "type": "string", "maxLength": 160 },
          "nth":     { "type": "integer", "minimum": 1 },
          "reason":  { "enum": ["punctuation", "casing", "term", "filler", "grammar", "formatting"] }
        }
      }
    }
  }
}
```

**Client-side checks (authoritative):**
- the `nth` occurrence of `find` exists;
- edits don't overlap each other;
- no edit touches a protected span (identifiers, numbers, negations);
- `term` edits must produce a term from the shortlist.

**On failure, timeout (deadline e.g. 300 ms) or invalid JSON:** keep the raw text and abort the HTTP request.

**Backend notes:**
- llama.cpp accepts both `response_format` forms.
- Grammar support for `minLength` / `maxLength` varies **[U]**. Validate client-side anyway.

---

## 7. Backend mapping

| Backend (host) | Wire / level | Server code | Bias | Free-text context | N-best | Words + confidence | Anchor | During-speech decode |
|---|---|---|---|---|---|---|---|---|
| **NeMo GPU-PB, Parakeet-TDT (desktop 3060; H100 eval)** | Riva **L1** | **new `hark-asr-server`** (Python `grpc.aio` servicer from the vendored proto + NeMo) | per-stream GPU-PB, greedy label-looping, per-phrase `alpha` via `key_phrase_items_list` **[U] in per-stream path**, `case_insensitive`, `cache_key = hash(groups)`; aliases → extra phrases | no | 1 (beam ≠ per-stream) | `compute_timestamps`; word confidence via NeMo confidence config **[U] with per-stream biasing** | inline: same batch with `partial_hypothesis=None`; shared-encoder reuse **[U]** | buffers audio; decodes at half-close (offline TDT). Chunked encoding **[U]** |
| **Qwen3-ASR (desktop / H100)**, path A | OpenAI-HTTP (vLLM) | none (`vllm serve Qwen/Qwen3-ASR-1.7B`) | rendered into `prompt` | `prompt` → system turn | 1 | **[U]** | parallel request without `prompt` | no (upload after key-up). vLLM realtime dialect exists; prompt support there **[U]** |
| Qwen3-ASR, path B | Riva **L1** via `hark-asr-server` (`qwen-asr` + vLLM in-process) | small module | `bias.method=prompt`: render groups + `hark.context` into the system turn; `echo_risk=true` | yes | via vLLM `n` / beam **[U]** | ForcedAligner (offline, adds latency **[U]**); token logprobs → word confidence | inline (decode without system turn, batched) | optional streaming partials (vLLM) |
| **NeMo-Speech.cpp `riva_server`** (desktop alt; **Mac via Metal**; **laptop CPU fallback** on 127.0.0.1) | Riva **L0** | none | Nemotron 3.5 / Nemotron Streaming RNNT: per-context, boosts ~1–3 (cap 5). Parakeet TDT: **ignored** | no | 1 (not implemented) | timestamps yes. Confidence: CTC mean posterior; RNNT = 1.0 → declare `none` | parallel (desktop) / none (CPU) | yes (cache-aware RNNT); one final on close |
| NVIDIA Riva / Speech NIM (H100 eval) | Riva **L0** | none (NGC licensing **[U]** for this use) | TDT/RNNT single score 0.5–2.0, ~500 words, no OOV; CTC −200..200, per-context, negative | no | `max_alternatives` (actual depth **[U]**) | yes | parallel | yes; `force_eou` on Nemotron streaming |
| **FluidAudio** (Mac, ANE), optional | Riva **L1** via a small Swift servicer (grpc-swift) **[U] effort** | small | `rescoring`: `CustomVocabularyTerm(text: phrases[0], aliases: rest)`. Thresholds `cbw 0, minSimilarity 0.7` (their dictation finding) | no | 1 | token timings → words; confidence **[U]** | inline for free: `ctcTokenEvaluateCandidates` base text = anchor | streaming manager rescoring windows; file mode is more accurate |
| MLX (parakeet-mlx / mlx-audio), optional | Riva L1 via `hark-asr-server` variant | small | none / **[U]** | no | 1 | **[U]** | trivially = primary | **[U]** |
| Local CPU fallback (laptop) | Riva L0 (loopback `riva_server --gpu -1`, Nemotron 3.5 Streaming Q8) | none; client supervises the process (systemd user unit) | `speech_contexts` | no | 1 | timestamps | none | yes (6× RT on "CPU" per README; laptop **[U]**). Plan B: in-process sherpa-onnx (hotwords need modified beam search) **[U]** |
| OpenAI (transcriptions / Realtime), Groq, Mistral | OpenAI-HTTP / vendor WS | none | `keywords` / `context_bias` / prompt | `prompt` | no | dialect-dependent | parallel | Realtime only |
| Deepgram, AssemblyAI | vendor WS | none (client adapter) | `keyterm` (500-token cap) / `keyterms_prompt` (≤100) | no | **[U]** | words + confidence | parallel (2× cost) | yes |
| Home Assistant STT servers, optional | Wyoming | none | none | (`context` object) | no | no | — | yes |
| Cleanup LLM (llama.cpp / vLLM / SGLang / Ollama) | Chat Completions + `json_schema` | none | — | — | — | — | — | — |

**Net new server code:** one Python Riva servicer that hosts two engine modules (NeMo GPU-PB, Qwen3). Everything else is
off the shelf. The Swift FluidAudio servicer is optional and only worth it if eval shows rescoring beats Nemotron 3.5
boosting on the Mac.

**Side benefits:**
- Stock Riva clients (`transcribe_file.py`, `riva_streaming_asr_client`) can smoke-test hark servers.
- The eval harness replays stored `RecognitionConfig` (protojson) + WAV pairs against any backend unchanged.

---

## 8. Latency, cancellation, auth, codec

### 8.1 Latency (target ≤1 s p95 key-up → pasted, LAN)

**Connection setup:**
- Keep a warm HTTP/2 channel per provider: tonic keepalive about 20 s, health check on network change.
- Open the RPC at key-down. The TLS handshake must never sit on the key-up path.

**Audio send:**
- 40 ms chunks (1280 B). Per-message overhead is a few bytes.
- PCM16 at 16 kHz = 256 kbit/s, fine on a LAN or tailnet.

**Key-up path:**
- Budget: tail chunk + half-close (<5 ms LAN) → server finalize → paste (~20–50 ms).
- Server finalize targets: ≤300 ms p95 for TDT/GPU-PB on the 3060, including the inline anchor (**[U]**, measure).
- Qwen3-1.7B decode: **[U]**, likely the long pole.

**Failover:**
- After half-close, wait `finalize_timeout_ms` (e.g. 600), then cancel.
- Re-send the buffered utterance via unary `Recognize` to the next provider.
- If a provider's health is degraded **before** key-down, hedge: also stream to the local fallback from key-down.

**Inline anchor:** costs one extra greedy decode on L1, with a shared encoder **[U]**. Parallel mode doubles compute;
it is acceptable on the GPU and skipped on the CPU.

**Cleanup:** fixed deadline. Past it, paste the raw text and drop the late result.

### 8.2 Cancellation

- **gRPC:** cancelling the RPC is the only abort signal. L1 servers MUST stop decoding, release per-stream state, and
  never deliver late results. Covers Esc, a new utterance replacing the old one, and finalize timeout.
- **HTTP adapters:** close the connection (works on NIM; llama.cpp direct mode; **[U]** elsewhere).
- **Realtime WS:** `input_audio_buffer.clear` before commit; close the socket after.
- **Deadline:** set a gRPC deadline per RPC as a safety cap (max utterance + 5 s). The finalize SLO is enforced on the
  client, not via the deadline.

### 8.3 Auth / transport

| Server | Transport | Auth |
|---|---|---|
| L1 servers (`hark-asr-server`, Swift) | gRPC over TLS 1.3 | mTLS (client certificates from a private CA), or `authorization: Bearer <token>` metadata on the tailnet |
| NeMo-Speech.cpp `riva_server` (plaintext) | loopback only; or bound to the tailnet IP (WireGuard-encrypted, Tailscale ACL by tag); or behind a TLS proxy | per the proxy |

- **Plaintext rule:** the client refuses plaintext to non-loopback endpoints unless the provider has
  `trust = "tailnet"`.
- **H100 eval / off-tailnet:** mTLS required.

### 8.4 Privacy

The real gate is client policy per provider:
- `send_terms`, `send_free_text`, `trust`, `require_retention_none`.

L1 servers additionally:
- enforce `hark.retention` (or reject);
- never log `custom_configuration` or `speech_contexts` content.

Cloud providers get no screen or repo context unless explicitly allowed. Even then, only `keywords` (bias terms), never
raw screen text, by default.

### 8.5 Codec

| Codec | Status |
|---|---|
| `LINEAR_PCM` s16le 16 kHz mono | MUST |
| `FLAC` / `OGGOPUS` | MAY, only if advertised (NIM supports them; NeMo-Speech.cpp does not). For WAN eval only. |
| Opus in Riva | must be Ogg-encapsulated, so not attractive for 40 ms chunking |

The OpenAI-HTTP adapters upload WAV.

---

## 9. What is lost vs a fully custom protocol (and mitigations)

**1. Extensions are untyped.**
- *Lost:* the JSON-in-`map<string,string>` / trailer extensions have no protobuf types or codegen, and are validated
  twice (client and server).
- *Mitigation:* JSON Schema plus shared conformance test vectors.

**2. Little per-hypothesis metadata.**
- *Lost:* only role labels per alternative. No term-hit spans or bias-score attribution, no token logprobs, lattices or
  confusion networks, no per-word alternatives.
- *Mitigation:* the client derives hits by aligning `biased` vs `anchor` words. Research-only detail can come later
  through an escape hatch (below).

**3. Config must come first.**
- *Lost:* no mid-stream context updates.
- *Mitigation:* client-side buffering of ≤300 ms. `runtime_config` stays available if ever needed.

**4. No session-level context references or caching.**
- *Lost:* context (a few KB) is re-sent every time.
- *Mitigation:* negligible on LAN. Servers cache boosting trees by content hash (NeMo `cache_key`).

**5. Same field name, different behaviour across implementations.**
- *Lost:* boost scales, confidence meaning, silent ignoring (TDT boosting in NeMo-Speech.cpp, `force_eou`), unimplemented
  N-best. A custom protocol could force explicit rejection.
- *Mitigation:* discovery, per-provider calibration, and an eval canary set. L1 servers MUST reject rather than ignore.

**6. L1 conventions aren't enforceable on stock servers.**
- *Lost:* one final per stream, words on every alternative, anchor.
- *Mitigation:* the client tolerates L0 (concatenates finals, uses the parallel anchor).

**7. The anchor is linked to its role by a trailer.**
- *Lost:* this out-of-band pairing depends on gRPC-aware proxies keeping trailers **[U]** for any proxy used.
- *Mitigation:* with no `hark-alt-roles` trailer, the client treats the response as "no anchor".

**8. The privacy flag is only a convention.**
- *Lost:* L0 servers can't honour it.
- *Mitigation:* client-side gating, as in §8.4.

**9. Dependency on NVIDIA's proto.**
- *Lost:* hark is tied to how NVIDIA evolves the proto (MIT licensed, additive so far).
- *Mitigation:* vendor and pin it.

**10. Heavier toolchain.**
- *Lost:* gRPC is heavier than JSON over WebSocket for small adapters (Swift, scripts), and harder to curl.
- *Mitigation:* enable gRPC reflection on L1 and use `grpcurl`.

**Escape hatch.** If hark outgrows the profile, define `hark.asr.v2.Hark/Stream`. It would **import** the Riva messages
and add typed fields (anchor, term hits, logprobs). The conventions carry over, and L0 interop is kept by still serving
the Riva service.

**What is gained:**
- About 0 custom wire schema.
- NIM / NeMo-Speech.cpp / Riva tooling work out of the box.
- Local CPU and Mac backends with no hark server code.
- One streaming adapter in the client for all self-hosted ASR.
- Replayable, backend-agnostic eval requests.

---

## 10. Open questions / experiments before committing

**NeMo / server-side:**
1. NeMo: per-stream biasing combined with `key_phrase_items_list` per-phrase `alpha`, word confidence, and timestamps,
   all in one call.
2. Can the anchor reuse the encoder (decode twice from one encoder output)?
3. p95 finalize latency on the 3060 for 5 / 15 / 30 s utterances.

**NeMo-Speech.cpp:**
4. What `GetRivaSpeechRecognitionConfig` returns.
5. How promptly work stops on cancel.
6. Nemotron 3.5 Streaming Q8 real-time factor on the laptop CPU.
7. Boost calibration (1–3) against distractor lists.

**Riva / NIM:**
8. Does it reject or ignore unknown `custom_configuration` keys?
9. Actual `max_alternatives` depth for TDT / RNNT.
10. Licensing terms for university H100 eval use.

**Qwen3-ASR / OpenAI-HTTP backends:**
11. Qwen3-ASR via vLLM transcriptions: are word timestamps possible?
12. Does the vLLM realtime dialect support `prompt`?
13. Echo rate with the rendered term list vs free text.
14. Speaches / whisper.cpp: `hotwords` / `verbose_json` passthrough.

**Mac:**
15. FluidAudio vs NeMo-Speech.cpp Metal (Nemotron 3.5 boosting) on hark's own dictation clips, measured with
    false-substitution rate.

---

## 11. Sources (read for this proposal)

**Riva / NVIDIA:**
- Riva protos: `riva_asr.proto`, `riva_audio.proto`, `riva_common.proto`:
  https://github.com/nvidia-riva/common/tree/main/riva/proto
- Speech NIM ASR customization (boost ranges, `custom_configuration` keys, `force_eou`):
  https://docs.nvidia.com/nim/speech/latest/asr/customization/customization.html
- NIM Nemotron Transcribe API (unknown fields → 400; disconnect cancels):
  https://docs.nvidia.com/nim/speech/latest/reference/api-references/asr/nemotron-transcribe.html
- NeMo-Speech.cpp: README, `docs/server.md`, `docs/api.md`, `docs/asr/configuration.md`, `docs/asr/customization.md`,
  `docs/clients.md`: https://github.com/NVIDIA/NeMo-Speech.cpp
- NeMo word boosting (GPU-PB, per-stream, per-phrase alpha, CTC-WS):
  https://docs.nvidia.com/nemo/speech/nightly/asr/asr_customization/word_boosting.html
- Rust Riva client crate: https://docs.rs/nvidia_riva_2026/

**OpenAI:**
- Create transcription: https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create
- Realtime transcription: https://developers.openai.com/api/docs/guides/realtime-transcription

**vLLM:**
- Qwen3-ASR (prompt → system turn): https://docs.vllm.ai/en/latest/api/vllm/model_executor/models/qwen3_asr/
- Realtime example: https://docs.vllm.ai/en/latest/examples/speech_to_text/realtime/

**Qwen:** Qwen3-ASR: https://github.com/QwenLM/Qwen3-ASR

**Other speech servers and protocols:**
- Wyoming: https://github.com/OHF-Voice/wyoming
- Speaches Realtime: https://speaches.ai/usage/realtime-api/
- FluidAudio custom vocabulary: https://github.com/FluidInference/FluidAudio/blob/main/Documentation/ASR/CustomVocabulary.md

**llama.cpp:**
- Server README (`response_format`): https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- Disconnect / cancellation issue #24496: https://github.com/ggml-org/llama.cpp/issues/24496

**Search summaries only (verify before use):**
- Deepgram keyterm: https://developers.deepgram.com/docs/keyterm
- AssemblyAI keyterms: https://www.assemblyai.com/docs
- Mistral `context_bias`: https://docs.mistral.ai/studio/audio/speech-to-text/transcribe-files
- sherpa-onnx hotwords
- gRPC metadata size limits
