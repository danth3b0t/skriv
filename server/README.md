# hark-server

Python (`grpc.aio`) server for the **hark.v1** protocol: [`proto/hark/v1/hark.proto`](../proto/hark/v1/hark.proto).
The design is in [ADR-001](../docs/protocol/ADR-001-protocol.md) and [proposal-a](../docs/protocol/proposal-a.md);
differences from proposal-a are listed in [CHANGES.md](../docs/protocol/CHANGES.md).

Services:
- `hark.v1.Asr`: `GetCapabilities`, `Transcribe` (bidi stream, one call per utterance), `Recognize` (unary), `Warmup`.
- `hark.v1.TextCleanup`: `Cleanup`, a proxy to an OpenAI-compatible `/v1/chat/completions` that returns validated edits. Only registered when `[cleanup] base_url` is set. Two engines: `s1` (default; "S1-mini" by "Superwhisper" rewrites the text, the rewrite is aligned into edits) and `edits` (a Qwen3 instruction model returns a JSON edit list).
- `grpc.health.v1.Health`: services `""` and `hark.v1.Asr` are SERVING once the model is READY; `hark.v1.TextCleanup` is SERVING when cleanup is configured.

## Run

```sh
cd server
uv sync                      # Python 3.12 + deps (+ dev group: grpcio-tools, pytest)
uv sync --extra mlx          # + parakeet-mlx (macOS, Apple Silicon)

uv run hark-server --backend mock                       # deterministic fake ASR on 127.0.0.1:50051
uv run hark-server --backend parakeet_mlx               # downloads mlx-community/parakeet-tdt-0.6b-v3 on first run
uv run hark-server --config hark-server.example.toml    # TOML config; CLI flags override it

../tools/hark-probe/hark-probe some.wav --words --bias bias.json   # stream a 16 kHz mono WAV, print FinalResult + latency
../tools/hark-probe/hark-probe some.wav --caps                     # print Capabilities
```

Text cleanup with a local LLM (from the repo root; everything lands in the gitignored `models/`):

```sh
scripts/fetch-llama                 # pinned llama.cpp release (macOS arm64 Metal; linux-x64: cpu | vulkan | cuda)
scripts/fetch-models                # default cleanup model (s1-mini-q4_k_m) + its LICENSE/NOTICE; `all` for every model
scripts/llama-up &                  # llama-server on 127.0.0.1:8080
(cd server && uv run hark-server --cleanup-url http://127.0.0.1:8080/v1 --cleanup-model s1-mini-q4_k_m)
scripts/eval-cleanup --model s1-mini-q4_k_m -v     # 44-case eval through the real Cleanup RPC
scripts/eval-cleanup --model s1-mini-q4_k_m --data server/tests/data/cleanup_eval_real.jsonl   # 29 real dictations
# the Qwen3 edit-list engine: scripts/llama-up qwen3-1.7b-q4_k_m, then --cleanup-engine edits / --engine edits
```

On macOS, `uv run hark service install` runs both (llama-server via `scripts/llama-up`, hark-server with the
same flags as `scripts/poc-up`) as launchd user agents that start at login and restart on crashes; logs in
`~/Library/Logs/hark/`. `hark service status|restart|uninstall`; see [packaging/macos](../packaging/macos/README.md).

The default cleanup model is "S1-mini" by "Superwhisper" ([model card](https://huggingface.co/superwhisper/s1-mini),
Apache 2.0 plus a term that it keep that name). See [the S1-mini bench](../docs/bench/2026-11-11-cleanup-s1.md)
and [the Qwen3 bench](../docs/bench/2026-11-11-cleanup.md) for how the models and flags were chosen.

`hark-probe` paces audio in real time by default; `--fast` sends it all at once. Other flags: `--repeat N`, `--warmup`,
`--json-summary FILE`, `--budget-ms`, `--anchor`, `--n-best`, `--tls-ca/--tls-cert/--tls-key`. See `--help`.

## Configuration

See [`hark-server.example.toml`](hark-server.example.toml). Unknown keys are errors.

| TOML | CLI | Default | Meaning |
|---|---|---|---|
| `server.listen` | `--listen` | `127.0.0.1:50051` | `host:port` or `unix:/path` |
| `server.tls.cert/key` | `--tls-cert/--tls-key` | — | enable TLS |
| `server.tls.client_ca` | `--tls-client-ca` | — | require client certs from this CA (mTLS) |
| `server.allow_insecure_non_local` | `--allow-insecure-non-local` | `false` | allow plaintext on non-loopback addresses |
| `server.warmup_on_start` | `--no-warmup` | `true` | load and warm the model before SERVING; otherwise the model stays COLD until a `Warmup` RPC |
| `server.max_concurrent_sessions` | | 4 | admission limit (`RESOURCE_EXHAUSTED`/`OVERLOADED`) |
| `server.max_audio_ms` | | 300000 | per-utterance cap (`AUDIO_TOO_LONG`) |
| `server.default_finalize_budget_ms` | | 1000 | used when a client sends 0 |
| `server.eager_end` | | `true` | advertise and honour `EagerEnd` |
| `backend.kind` | `--backend` | `mock` | `mock`, `parakeet_mlx`, `nemo_gpupb`, `transcribe_cpp` |
| `backend.model` / `backend.model_id` | `--model` / `--model-id` | backend default | model to load / id advertised to clients |
| `backend.options` | | `{}` | backend constructor kwargs (checked at startup) |
| `cleanup.base_url` | `--cleanup-url` | `""` (off) | OpenAI-compatible base, e.g. `http://127.0.0.1:8080/v1` |
| `cleanup.model` | `--cleanup-model` | | model name sent upstream |
| `cleanup.engine` | `--cleanup-engine` | `s1` | `s1` (S1-mini rewrite → edits) or `edits` (JSON edit list) |
| `cleanup.api_key_env` | | | env var holding the upstream bearer token |

**Transport security.** Plaintext is accepted only on loopback or `unix:` addresses. Any other address needs TLS, or
the explicit `allow_insecure_non_local` override. mTLS (private CA + `client_ca`) is the intended default for LAN or
Tailscale use.

## Backends

| | `mock` | `parakeet_mlx` | `nemo_gpupb` | `transcribe_cpp` |
|---|---|---|---|---|
| Runs on | anywhere | macOS / Apple Silicon (`--extra mlx`) | Linux + NVIDIA + NeMo. **Stub.** | anywhere: CPU; Metal / Vulkan / CUDA if built so (`scripts/fetch-transcribe`) |
| Streaming | BUFFERED | INCREMENTAL from 30 s of audio (sentence segments decoded while audio streams), BUFFERED below | BUFFERED (planned) | BUFFERED |
| Bias | DECODE_TIME_BOOST (simulated: alias → canonical), native boost 1–5 | DECODE_TIME_BOOST: beam 4 + parakeet-mlx word booster (boost 1–3), greedy anchor from the same encoder pass (see docs/bench/2026-11-11-parakeet-mlx-boost.md) | DECODE_TIME_BOOST via GPU-PB per-stream, boost = α_model·(0.5+w) | NONE (transcribe.cpp has no vocabulary input for Parakeet); terms only for hit detection |
| Anchor | DECODER_ONLY (second decode) | FREE (== primary) | FULL_PASS (batch of two) | FREE (== primary) |
| N-best | up to 3 (unbiased, RANK_ONLY) | 1 | unbiased beam (planned) | 1 |
| Word timings / confidence | yes / yes | decoder emission, 80 ms / entropy-based, min per word, uncalibrated | yes / yes (planned) | decoder emission, 80 ms / min token probability per word, uncalibrated |

- **mock**: every `word_ms` (200) window of non-silent audio emits the next word of a fixed script. Latency is configurable (`decode_ms`, `decode_ms_per_s`, `load_ms`). The tests use it.
- **parakeet_mlx**: `from_pretrained` + `get_logmel` → `encoder` → greedy `decode`, all on one worker thread (MLX streams are thread-affine). parakeet-mlx 0.7 *does* have decode-time word boosting, but only in beam search; it is not wired up yet (see the bench doc).
- **transcribe_cpp**: Parakeet TDT 0.6B v3 GGUF (Q4_K_M by default) on [transcribe.cpp](https://github.com/handy-computer/transcribe.cpp) 0.4.2 / ggml, the offline fallback for machines without a GPU server in reach. The model runs in a worker child process (`hark_server.backends.transcribe_worker`, JSON lines): a native crash fails one decode and the next decode restarts the worker; a decode longer than `decode_timeout_s` kills it. `scripts/fetch-transcribe` builds the pinned library into `build/` and downloads the model; `scripts/cpu-server-up` runs it. Latency: [docs/bench/2026-11-12-transcribe-cpp-cpu.md](../docs/bench/2026-11-12-transcribe-cpp-cpu.md).
- **nemo_gpupb**: capabilities, the weight→boost mapping and the intended per-stream GPU-PB call path (as code comments) are in place. Construction fails with a clear error when NeMo is missing; `load()` fails with "stub".

Adding a backend: subclass `hark_server.backends.Backend`, then implement `capabilities()`, `_load_sync`, `_warmup_sync` and
`_decode_sync`, plus optionally `plan_bias` and `_n_best_sync`. Validation, budgets, anchors, hits, gaps and
eager finalize are handled by the server (`session.py`), not by backends.

## Protocol behaviour implemented

- Exactly one terminal message per `Transcribe` call: `final_result` (status OK) or `session_ended{CANCELLED|REJECTED|FAILED}` with `ErrorDetail`. The gRPC status mirrors it, and the `ErrorDetail` is also in `grpc-status-details-bin`.
- `start_sample` placement: a forward gap is zero-filled, with a `WARNING_CODE_AUDIO_GAP` warning and `flags.audio_gap`; overlap is a `PROTOCOL_VIOLATION`. `StreamStatus` is sent every 500 ms with `samples_received`.
- `EagerEnd` starts a speculative finalize. A later chunk or `ContextUpdate` invalidates it. A hit sets `timings.eager_hit`.
- Finalize budget: the primary decode is never shed. Then the anchor and then N-best are run if their estimated cost fits; otherwise they are marked `SKIPPED_BUDGET`. `required_features` are never shed. `finalize_budget_exceeded` is set when end_to_final > budget.
- Every optional field reports a `FieldStatus`. Bias hits are derived by matching the output against the anchor (APPLIED / ALREADY_IN_ANCHOR).
- Privacy: this server retains, logs and caches nothing, so every `allow_*` flag is rejected with `PRIVACY_UNSATISFIABLE`. Logs contain only ids, sizes and timings.
- Cleanup profiles: `terminal` (terminal or coding-agent prompt, single line), `chat` (light touch, single line) and `prose` (email/documents; full punctuation, paragraph breaks; the default). All three allow punctuation, capitalization, spacing, filler_removal and self_correction, plus number_format with the `s1` engine.
- Language guard (both engines): text that looks non-English (more foreign than English stopwords, or over a quarter of the words with non-ASCII letters), or a `language` other than `en*`, gets no edits and no LLM call.
- `s1` engine: the request is S1-mini's exact system prompt and a control line (`[Styling: semi-formal] [Structure: prose] [Context: general]` for every profile: ASR output arrives cased and punctuated, and `casual`/`semi-casual` lowercase sentence starts and drop final periods), with `chat_template_kwargs.enable_thinking = false`, temperature 0. The rewrite is aligned with the input on whitespace tokens (words compared without case and punctuation, `difflib`). Each changed stretch becomes a quoted edit: deletions and insertions take a neighbouring token along, non-unique quotes grow by a token per side, and a stretch with a correction cue grows until it has the self-correction shape below. Single-line profiles turn line breaks in the rewrite into spaces. The edits then go through the validator below, per group: a rejected edit drops only its group, and the rest of the rewrite is applied. The >50% invalid rule does not apply (a rewrite changes words no check can verify); more than `max_edits` accepted edits and the text stays raw. A rewrite cut off by `max_tokens` is dropped.
- `edits` engine: the prompt is a static prefix (shared policy, then profile rules, then few-shot turns) followed by the request, so llama-server's prompt cache reuses the prefix. The JSON schema's `kind` enum is limited to the profile's kinds.
- Cleanup validation: unique-quote edits are resolved to UTF-8 byte ranges and validated: unique find, overlap, protected spans, allowed kinds, per-kind invariants, plus the >50% invalid / negation / digit / changed-bytes rules. If the budget runs out, the response is `truncated` with no edits. Additions:
  - Edits at most one word apart form a group (transitively). An edit that fails a per-edit check takes its group with it (`DEPENDENT`), so dropping `step, uh,` → `step.` also drops `can` → `Can`. An edit overlapping an accepted one is dropped alone.
  - The whole-result rules run on the accepted edits. A negation, digit or `!` change is pinned on the edits that make it; their groups are dropped (`GLOBAL`) and the rules run again. Any other failure (too many changed bytes or removed words, a sloppy result) drops every edit.
  - A mislabelled edit takes the first allowed kind whose invariant it passes.
  - A quote that combines kinds (e.g. a whole sentence re-cased and re-punctuated) is split into minimal token-level edits. It is accepted only if every part passes an allowed kind and stays off protected spans.
  - `self_correction` has a mechanical check: `find` = kept words + 1–6 retracted words (fillers not counted) + a cue (fillers around it allowed) + the correction (at least one word); `replace` = kept words + the correction. A phrase cue (`no wait`, `I mean`, `scratch that`, `or rather`, `make that`, …) is set off by punctuation on at least one side (`there is no wait time` is not a cue). A bare cue (`no`, `sorry`, `actually`) is set off on both sides, and the correction must be of the same kind: it repeats the first retracted word (`in C, uh no, in Rust`) or replaces a single word after a kept word of the same sentence (`on Tuesday, no, Wednesday`). No sentence may end inside the retracted words; a `?` or `!` next to the cue rejects (`Is it done? No, …` is an answer), a `.` next to it is allowed only when the correction repeats the first retracted word (`My name is David. Uh no, my name is Dan.`). The negation and digit rules compare against the text with the self-corrections applied. Protected spans may be retracted with the words and the cue (`port 8080, sorry, I mean port 8081`; the `C` and `no` of `in C, uh no, in Rust`); protected spans in the kept words and the correction must reappear unchanged in `replace`. Elsewhere a negation stays protected (`No, I don't want C.`, `use C not Rust`).
  - A `spacing` edit may merge or split words and change their case if the result is exactly a known term: `CleanupRequest.known_terms` (the client's vocabulary/bias canonicals) or the text of a protected span that is not a number or negation. `Super Whisper.` → `Superwhisper.` passes with the term `Superwhisper`, also over the protected `Super`/`Whisper`; the punctuation at its edges may change like a punctuation edit. Without the term it stays rejected.
  - `filler_removal` may delete um/uh-type fillers, `like`, `basically`, `literally`, the phrases `you know`, `kind of`, `sort of` (not `I mean`: "I mean it"), and a word repeated right after itself (`the the`). Fillers may make up at most half of the words.
  - An edit may overlap a protected span only if the span reappears unchanged in `replace` with no letters or digits glued to it (`main.py` → `main.py.` is fine, `3:30 pm` → `3:30pm` is not). A negation may change case only where it starts a sentence of the result.
  - `number_format`: runs of spoken numbers become digits of the same value (`twenty three thousand` → `23,000`, `ten percent` → `10%`, `fifty dollars` → `$50`, `fifth` → `5th`); other words keep their letters. Times, dates, years and decimals are not checked, so they are rejected.
  - `punctuation` may only add, remove or change `. , ; : ? ! ' ’ -` (no quotes, brackets or symbols), and the result may not lose a `!`.
  - Single-line profiles reject added line breaks.
  - No-op echoes don't count towards the >50% rule.
  - Changed bytes exclude quoted context, filler removal and self-corrections.
  - The result may not gain double spaces, doubled punctuation, a comma glued to a word, or a lowercase word after a sentence end.

## Tests

```sh
cd server && uv run pytest            # ~65 tests, ~8 s, mock backend + fake OpenAI endpoint, no model needed
```

The tests run against the in-process server over real gRPC on localhost. They cover capability negotiation (including
`required_features`), streaming happy path and gaps, eager invalidation, cancel and admission, budget shedding, a
terminal-message table over 20 success and failure paths, privacy rejection, the cleanup proxy, config security, the
probe end to end, and drift between `proto/` and the checked-in stubs.

Regenerate stubs after editing the proto: `proto-py/gen-proto` (from the repo root) (grpcio-tools; buf/protoc are not required).

## Known limitations

- No partials. Below `incremental_min_ms` (30 s) parakeet_mlx decodes at end of audio, so finalize time grows with utterance length up to ~1 s at 30 s with bias terms; longer dictations are decoded a sentence at a time while they stream ([bench](../docs/bench/2026-11-12-parakeet-mlx-incremental.md)). mock and nemo_gpupb are BUFFERED only.
- One model per process. `backend_params` from clients are ignored (none are used yet). Free-text context is ignored by all backends (`CONTEXT_IGNORED`).
- parakeet_mlx: N-best is 1 (beam result + greedy anchor), boosting costs ~2.3× greedy finalize latency, `detected_language` is not reported, and confidence is uncalibrated.
- nemo_gpupb is a stub.
- Not implemented from the ADR or proposal: bearer-token auth, a client-cert fingerprint allowlist (any cert from `client_ca` is accepted), the replay cache, retention/eval capture, Opus, gRPC reflection, `SUPERSEDED` handling of retried attempts, server-side session expiry, hit scores, and REJECTED hits for `bias_diagnostics`.
- Eager finalize: the speculative result's budget is counted from the speculative start. An invalidated speculative decode cannot be pre-empted and keeps the worker busy until it finishes.
- Cleanup: no in-budget retry, non-streaming upstream call. `grammar` has no mechanical check, so no profile enables it. The client validator (authoritative) mirrors the per-kind checks, the protected-span rule for self-corrections and the negation/digit/removed-words/`!` rules, but not the changed-bytes budget or the "sloppy result" patterns. S1-mini is English-only and inputs over ~1,000 tokens are not chunked (the 4,000-byte input cap keeps them below that).
- grpcio's *Python* client replaces the server's status with `INTERNAL` if it is still writing when the server ends the call. Clients should rely on the in-band `session_ended`/`final_result` and stop writing after it (tonic does not have this problem).
