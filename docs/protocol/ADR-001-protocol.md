# ADR-001: Client ↔ inference-server protocol

Status: accepted (2026-11-11). Inputs: proposal-a (gRPC), proposal-b (WebSocket), proposal-c (Riva standards-first).

## Decision
**skriv.v1 = a typed gRPC/protobuf protocol based on proposal-a**, amended with ideas from B and C below.
Third-party and stock servers (Riva / NeMo-Speech.cpp `riva_server`, OpenAI-compatible, vendor APIs) are reached through
**client-side adapters**, not by making them the native protocol.

## Why
- All three proposals found the same backend limits (NeMo GPU-PB per-request bias = greedy only, no N-best;
  NeMo-Speech.cpp ignores bias for Parakeet TDT, constant RNNT confidence, plaintext; Qwen3-ASR context = system prompt,
  no streaming timestamps). Per-model capability advertising with typed fields is therefore essential (A, B agree).
- Typed bias hits, anchor hypothesis, degradation flags and finalize budgets are first-class needs; Riva's
  JSON-in-`custom_configuration` (C) makes them stringly-typed and per-server ambiguous.
- gRPC gives deadlines, cancellation, status codes and health checking for free; mature in Rust (tonic) and Python
  (grpc.aio); same stack as the Riva adapter. WebSocket's debuggability advantage (B) is covered by `grpcurl`/`buf curl`
  and a JSON transcoding of messages for golden tests.

## Amendments to proposal-a
1. **Riva alignment (C):** bias term = canonical + spoken aliases + weight, mapped 1:1 to a Riva `SpeechContext` group by
   the Riva adapter; client sends weights in [0,1], server converts to its native scale and reports the applied value.
2. **`AudioChunk.start_sample` (B):** every chunk carries the index of its first sample (gap detection, resume, acks).
3. **Eager finalize (B):** add `EagerEnd` client message (VAD pause while key held) → server may finalize speculatively;
   a later chunk invalidates the speculative result.
4. **Exactly one terminal message per session (B):** `FinalResult` or `SessionEnded{reason}`.
5. **`quality` / `omitted` (B) merge into A's `ResultFlags`:** every optional field reports present / skipped-for-budget /
   unsupported.
6. **Cleanup:** keep A's `TextCleanup` service for skriv servers, **and** the client must support calling any
   OpenAI-compatible `/v1/chat/completions` with the same JSON edit schema (B, C). Edits are quoted-text anchored
   (`find` unique) → server/client resolve to byte ranges; the **client validator is authoritative**.
7. **Privacy (A, C):** server must reject (not silently ignore) unsupported retention flags; client policy is the real gate.
8. **Audio:** PCM16 16 kHz mono, 40 ms chunks default; Opus optional only after accuracy is measured (B cites +12.6% rel.
   WER at 16 kb/s).
9. **Security:** mTLS (private CA, device allowlist) default; bearer optional; Tailscale is network, not auth.
   Optional small gateway adds TLS in front of plaintext servers (NeMo-Speech.cpp).

## Consequences
- Native skriv servers: Python (NeMo GPU-PB, Qwen3-ASR), later Swift (FluidAudio) if worth it.
- Client adapters: `riva` (NeMo-Speech.cpp, stock Riva — anchor via parallel unbiased request), `openai` (batch/transcription
  + chat cleanup), vendor adapters later.
- A conformance tool replays recorded sessions (dataset format) against every server/adapter.
- Open experiments (from proposals' [U] lists): RTX 3060 + laptop CPU latency, anchor encoder reuse in NeMo, per-phrase
  weights in GPU-PB, Qwen3-ASR timestamps via vLLM, Opus accuracy cost.
