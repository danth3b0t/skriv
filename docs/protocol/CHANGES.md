# skriv.v1 — changes from proposal-a

Canonical schema: [`proto/skriv/v1/skriv.proto`](../../proto/skriv/v1/skriv.proto) (v1.0, final).
Base: [`proposal-a.proto`](proposal-a.proto). Decision: [ADR-001](ADR-001-protocol.md).
Everything not listed here is unchanged from proposal-a.

proposal-a was never deployed, so removed fields are `reserved` only for hygiene; no wire compatibility with it is promised.

## ADR-001 amendments

| # | Amendment | Schema change |
|---|---|---|
| 1 | Riva-aligned bias terms | `BiasTerm` documented as one Riva `SpeechContext` group (`phrases=[canonical, aliases…]`, one `boost`). `weight` range is now **[0,1]** (was (0,1]); weight 0 = lowest native boost, not "off" (use `BiasList.strength=0`). Default mapping `boost_min + w·(boost_max−boost_min)`. **Added** `BiasCapabilities.boost_min/boost_max` (12, 13) and `AppliedConfig.term_boosts` (10, id → applied native boost). **Removed** `SpokenAlias.weight` (3, reserved): a Riva group has a single boost, so per-alias weights cannot map 1:1. |
| 2 | `AudioChunk.start_sample` | Field already existed in proposal-a; its rules are now normative: placement is by `start_sample`; forward jump = gap (zero-fill + `WARNING_CODE_AUDIO_GAP` + `flags.audio_gap`); backward jump = `PROTOCOL_VIOLATION`; `seq` strictly increasing; PCM `num_samples == len(data)/2`. `EndOfAudio.last_seq/total_samples` must match what was received. **Added** `StreamStatus.samples_received` (4) as the ack. |
| 3 | Eager finalize | **Added** `TranscribeRequest.eager_end` (6) with `EagerEnd{total_samples, speech_end_sample}`, feature string `skriv.eager_end`, `Timings.eager_hit` (16). Any later chunk or context update invalidates the speculative result. |
| 4 | Exactly one terminal message | **Added** `TranscribeResponse.session_ended` (6), `SessionEnded{reason, error, utterance_id}`, `enum SessionEndReason {CANCELLED, REJECTED, FAILED}`. Every Transcribe call the server can still write to ends with exactly one `final_result` (then OK) or `session_ended` (then the mirrored non-OK status, ErrorDetail also in `grpc-status-details-bin`), including rejection before `accepted` and explicit `Cancel`. **Added** `ERROR_CODE_CANCELLED` (13). |
| 5 | `quality`/`omitted` → `ResultFlags` | **Removed** `n_best_skipped/anchor_skipped/timestamps_skipped` (2–4, reserved). **Added** `enum FieldStatus {PRESENT, NOT_REQUESTED, SKIPPED_BUDGET, UNSUPPORTED, FAILED}` and `ResultFlags.n_best/anchor/word_timestamps/word_confidence/bias_hits` (9–13). B's per-field *trust* levels are not repeated per result: they are static per model (`timestamps.source`, `confidence_calibrated`, `anchor_cost`, `bias_with_n_best`) plus `Hypothesis.origin` and `AppliedConfig`. |
| 6 | Quoted-text anchored cleanup edits | LLM edit schema is `{find, replace, kind, reason}`; **no `occurrence` field**: `find` must be unique in the text (overlapping occurrences count). The server resolves to byte ranges and validates. **Added** `CleanupResponse.rejections` (7) with `EditRejection{find, replace, reason}` and `enum EditRejectionReason`. |
| 7 | Privacy rejection | `PrivacyFlags` now states the rejection rule: an `allow_*` flag the server cannot act on (or a `false` flag the server cannot honour) → `FAILED_PRECONDITION` + **added** `ERROR_CODE_PRIVACY_UNSATISFIABLE` (12), offending flags in `unavailable_features` (e.g. `privacy.allow_eval_capture`). **Added** `Capabilities.privacy` (8) = `PrivacyCapabilities` so clients can avoid rejected combinations. Applies to `Cleanup` too. |
| 8 | Audio defaults | `AudioFormat` documents defaults: unset encoding → PCM_S16LE, 0 Hz → 16000, 0 channels → 1, 40 ms chunks; Opus optional and unadvertised until measured. |
| 9 | Security | Header comment only (mTLS default, bearer optional, plaintext on loopback only). |

## v1.1 (additive)

- **Added** `FinalResult.model_id` (12): the model that produced `n_best[0]`. A server hosting several models routes a session by `SessionStart.languages` (e.g. an English-only model for `["en"]`) and may re-decode with another model when the output is implausible for the requested languages; it then also sends `WARNING_CODE_LANGUAGE_FALLBACK`. `Capabilities.protocol.minor` = 1.

## Pruned (nothing read or wrote them)

Removed, numbers and names `reserved`: the `Recognize` and `Warmup` RPCs with their messages (and the
`skriv.recognize` feature), `PartialResult` / `TranscribeResponse.partial`, `PrivacyAck` / `FinalResult.privacy`,
`GetCapabilitiesRequest.client_protocol_minor`, `BiasCapabilities.max_total_tokens/hit_scores`,
`FreeTextCapabilities.echo_risk`, `PerfEstimate.bias_compile_ms`, `SessionStart.attempt`,
`PrivacyFlags.minimal_metadata`, `ResultOptions.partial_interval_ms/bias_diagnostics`, `ClientInfo.device_id`,
`AudioChunk.vad_speech_prob`, `EagerEnd.speech_end_sample`, `SessionAccepted.replayed`,
`FinalResult.language_confidence/replayed`, `BiasHit.term_score/anchor_score`,
`Timings.bias_cache_hit/lag_at_end_ms/alignment_ms`, `ResultFlags.context_echo_suspected`, and the enum values
Opus, `ACOUSTIC_RESCORE`/`PROMPT` bias, `FORWARD_ONLY` midstream updates, the log-prob/posterior score kinds,
`FORCED_ALIGNMENT`/`PROVIDER` timestamps, `RESCORED` origin, `OCR`/`APP_PROFILE` context sources,
`PERSON_NAME`/`ACRONYM` term kinds, `SPELLED`/`MISRECOGNITION` aliases, `VAD_ENDPOINT`/`TOGGLE` end reasons,
`SWITCHED_PROVIDER`/`SHUTDOWN` cancel reasons, the `REJECTED` hit decision, `FREE_TEXT_TRUNCATED`/
`FINALIZE_BUDGET_TIGHT` warnings and the `SUPERSEDED`/`UNAUTHENTICATED` error codes. Add them back (new numbers)
when something produces or consumes them.

## Other deviations

- `SessionStart.required_features` additionally accepts `result.word_confidence`; unknown feature names fail (cannot honour what is not understood). Required result fields are never shed for budget.
- `ResultFlags.finalize_budget_exceeded` is defined as `end_to_final_ms > finalize_budget_ms`.
- Compiled with grpcio-tools 1.85 (`proto-py/gen-proto`). Not verified: `buf lint`/`buf breaking` (buf is not installed here). One `buf lint` STANDARD finding is inherited from proposal-a: `GetCapabilities` returns `Capabilities` instead of `GetCapabilitiesResponse` (RPC_RESPONSE_STANDARD_NAME). Left as is; decide when buf is added to CI.
