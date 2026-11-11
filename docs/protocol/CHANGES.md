# hark.v1 — changes from proposal-a

Canonical schema: [`proto/hark/v1/hark.proto`](../../proto/hark/v1/hark.proto) (v1.0, final).
Base: [`proposal-a.proto`](proposal-a.proto). Decision: [ADR-001](ADR-001-protocol.md).
Everything not listed here is unchanged from proposal-a.

proposal-a was never deployed, so removed fields are `reserved` only for hygiene; no wire compatibility with it is promised.

## ADR-001 amendments

| # | Amendment | Schema change |
|---|---|---|
| 1 | Riva-aligned bias terms | `BiasTerm` documented as one Riva `SpeechContext` group (`phrases=[canonical, aliases…]`, one `boost`). `weight` range is now **[0,1]** (was (0,1]); weight 0 = lowest native boost, not "off" (use `BiasList.strength=0`). Default mapping `boost_min + w·(boost_max−boost_min)`. **Added** `BiasCapabilities.boost_min/boost_max` (12, 13) and `AppliedConfig.term_boosts` (10, id → applied native boost). **Removed** `SpokenAlias.weight` (3, reserved): a Riva group has a single boost, so per-alias weights cannot map 1:1. |
| 2 | `AudioChunk.start_sample` | Field already existed in proposal-a; its rules are now normative: placement is by `start_sample`; forward jump = gap (zero-fill + `WARNING_CODE_AUDIO_GAP` + `flags.audio_gap`); backward jump = `PROTOCOL_VIOLATION`; `seq` strictly increasing; PCM `num_samples == len(data)/2`. `EndOfAudio.last_seq/total_samples` must match what was received. **Added** `StreamStatus.samples_received` (4) as the ack. |
| 3 | Eager finalize | **Added** `TranscribeRequest.eager_end` (6) with `EagerEnd{total_samples, speech_end_sample}`, feature string `hark.eager_end`, `Timings.eager_hit` (16). Any later chunk or context update invalidates the speculative result. |
| 4 | Exactly one terminal message | **Added** `TranscribeResponse.session_ended` (6), `SessionEnded{reason, error, utterance_id}`, `enum SessionEndReason {CANCELLED, REJECTED, FAILED}`. Every Transcribe call the server can still write to ends with exactly one `final_result` (then OK) or `session_ended` (then the mirrored non-OK status, ErrorDetail also in `grpc-status-details-bin`), including rejection before `accepted` and explicit `Cancel`. **Added** `ERROR_CODE_CANCELLED` (13). |
| 5 | `quality`/`omitted` → `ResultFlags` | **Removed** `n_best_skipped/anchor_skipped/timestamps_skipped` (2–4, reserved). **Added** `enum FieldStatus {PRESENT, NOT_REQUESTED, SKIPPED_BUDGET, UNSUPPORTED, FAILED}` and `ResultFlags.n_best/anchor/word_timestamps/word_confidence/bias_hits` (9–13). B's per-field *trust* levels are not repeated per result: they are static per model (`timestamps.source`, `confidence_calibrated`, `anchor_cost`, `bias_with_n_best`) plus `Hypothesis.origin` and `AppliedConfig`. |
| 6 | Quoted-text anchored cleanup edits | LLM edit schema is `{find, replace, kind, reason}`; **no `occurrence` field**: `find` must be unique in the text (overlapping occurrences count). The server resolves to byte ranges and validates. **Added** `CleanupResponse.rejections` (7) with `EditRejection{find, replace, reason}` and `enum EditRejectionReason`. |
| 7 | Privacy rejection | `PrivacyFlags` now states the rejection rule: an `allow_*` flag the server cannot act on (or a `false` flag the server cannot honour) → `FAILED_PRECONDITION` + **added** `ERROR_CODE_PRIVACY_UNSATISFIABLE` (12), offending flags in `unavailable_features` (e.g. `privacy.allow_eval_capture`). **Added** `Capabilities.privacy` (8) = `PrivacyCapabilities` so clients can avoid rejected combinations. Applies to `Cleanup` too. |
| 8 | Audio defaults | `AudioFormat` documents defaults: unset encoding → PCM_S16LE, 0 Hz → 16000, 0 channels → 1, 40 ms chunks; Opus optional and unadvertised until measured. |
| 9 | Security | Header comment only (mTLS default, bearer optional, plaintext on loopback only). |

## Other deviations

- `SessionStart.required_features` additionally accepts `result.word_confidence`; unknown feature names fail (cannot honour what is not understood). Required result fields are never shed for budget.
- `ResultFlags.finalize_budget_exceeded` is defined as `end_to_final_ms > finalize_budget_ms`.
- Compiled with grpcio-tools 1.85 (`server/scripts/gen-proto`). Not verified: `buf lint`/`buf breaking` (buf is not installed here). One `buf lint` STANDARD finding is inherited from proposal-a: `GetCapabilities` returns `Capabilities` instead of `GetCapabilitiesResponse` (RPC_RESPONSE_STANDARD_NAME). Left as is; decide when buf is added to CI.
