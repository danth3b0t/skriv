# Bench: decode-time bias in the parakeet_mlx backend (2026-11-11)

> **TTS caveat.** Every clip here is macOS `say` (2 voices, 10 sentences). The speech is clean, evenly paced and
> synthetic, and its errors are not the errors of real dictation. The numbers show that the mechanism works and what it
> costs, and they are good enough to pick defaults. They are **not** accuracy claims. The dataset spec keeps TTS out of
> dev/test, so re-run on real recordings (dataset phase 0) before quoting anything.

## Result in one paragraph

With bias terms, the `parakeet_mlx` backend now decodes with TDT beam search (beam 4) and a case-insensitive
parakeet-mlx word booster over each term's aliases and (when spellable) canonical form. The unbiased greedy anchor
reuses the encoder output, and it is free because the primary decode computes it anyway. On 20 TTS clips with a
39-term shortlist (19 relevant, 20 distractors), WER after client-style canonicalization drops from **0.176**
(greedy + the same terms used only post hoc) to **0.107**, and term recall rises from 46/74 to 57/74. There were
**0 false APPLIED hits**, and with a distractor-only list the raw WER barely moves (0.393 → 0.397). The cost is end-of-audio→final p50
**211 / 460 / 895 ms** for 3 / 9 / 20 s clips, versus 88 / 148 / 242 ms greedy. Without terms the backend stays greedy and
its latency is unchanged.

## Setup

| | |
|---|---|
| Host | MacBook, Apple M5 Pro, 48 GB, macOS 26.5.1. Other agents were working in parallel; load average 1.7–3.6 during the latency runs. |
| Server | `hark-server --backend parakeet_mlx`, `mlx-community/parakeet-tdt-0.6b-v3`, bfloat16, defaults `beam_size=4, patience=20, boost_min=1, boost_max=3`. |
| Versions | Python 3.12.15, parakeet-mlx 0.7.0, mlx 0.32.4 |
| Client | `tools/hark-probe` on loopback, through [`parakeet-mlx-boost/run_bench.py`](parakeet-mlx-boost/run_bench.py). Accuracy uses `--fast`; latency uses real-time pacing with 40 ms chunks and `--words`, anchor `IF_CHEAP`. |
| Clips | [`clips.json`](parakeet-mlx-boost/clips.json): 10 sentences × voices Samantha and Daniel = 20 clips, 2.3–20.8 s. Identifiers are spoken the way a developer says them ("use session store", "session store dot ts", "parse CLI args"); product names are left to `say` ("Hyprland", "Superwhisper", "basedpyright", "kubectl"). |
| Terms | [`terms.json`](parakeet-mlx-boost/terms.json): 19 relevant terms (each occurs in some clip) and 20 distractors (none occurs, several are phonetic neighbours: `Hypercorn`, `useSearchStore`, `parse_argv`, `whisper.cpp`, `basedmypy`, `kubens`, `Tailwind`, …). Weights 0.5–0.9, aliases as a client would derive them (camelCase/snake_case split, spelled punctuation). |
| Metrics | WER over lower-cased words with edge punctuation stripped, against the text the user wants (`useSessionStore`, `session_store.ts`, …). **Raw**: `n_best[0].text`. **Canonicalized**: every `BiasHit` span replaced by its term's canonical, which is what the client does before pasting. **Term recall**: clip terms whose canonical appears in the canonicalized text (74 occurrences). **False APPLIED**: hits with decision APPLIED on terms not in the clip. |

## Mapping hark.v1 bias terms to the booster

parakeet-mlx 0.7 boosts inside beam search only: `Beam(booster=boost.lift(boost.words({form: score}), vocabulary))`.
`words` is a character trie. A form earns its score when it is completed at a word boundary, and the score is granted
progressively along the prefix (a "potential") and taken back if the beam leaves the form. The score adds to
`0.3 · log p(token)` (TDT beam weighs token log-probs by `1 − duration_reward`), so 1.0 is worth about 3.3 nats of
acoustic evidence.

| hark.v1 | backend |
|---|---|
| `weight` w ∈ [0,1] (absent = 1), `strength` s | native boost b = min(boost_max, s · (boost_min + w · (boost_max − boost_min))); default range [1, 3]. Reported in `AppliedConfig.term_boosts` and `BiasHit.boost`. The clamp exists because strength > 1 would otherwise reach the deletion regime (below). |
| aliases | Every alias is boosted with b, lower-cased and whitespace-normalised. The decoder then writes the spoken form ("use session store"), hits report `via_alias`, and the client canonicalizes. |
| canonical | Boosted too if every character can be produced by the tokenizer: `Hyprland`, `useSessionStore`, `tsconfig.json`, `basedpyright` yes. `parse_args` and `session_store.ts` no: the v3 vocabulary has **no `_`**. Their count is reported as `backend_params.canonical_not_spellable`. |
| casing | Case-insensitive (`premap(str.lower)`), so `case_insensitive=true` is advertised. The model's casing is kept; canonicalization fixes it. |
| duplicates, b ≤ 0 | A form shared by two terms gets the larger b. b ≤ 0 is not boosted. If no forms remain, the decode is greedy. |
| capabilities | `BIAS_MECHANISM_DECODE_TIME_BOOST`, max 100 terms / 8 aliases / 128 bytes, per-term weight, aliases supported, midstream update FULL (BUFFERED: decoding starts at the end), `anchor_cost FREE`, `n_best.max_n 1`, no hit scores. CTC checkpoints (greedy only in parakeet-mlx) keep mechanism NONE. |
| diagnostics | `AppliedConfig.backend_params`: `decoder` (`tdt_beam4+greedy` / `greedy`), `boosted_forms`, `canonical_not_spellable`. `Timings.backend_ms`: `greedy`, `booster_build`, `beam`, and `beam_discarded` when the guard fires. |

## Accuracy (end to end, hark-probe, 20 clips)

| condition | WER raw | WER canonicalized | anchor WER | term recall | false APPLIED hits | beam discarded |
|---|---|---|---|---|---|---|
| none (no terms → greedy) | 0.393 | 0.393 | 0.393 | 21/74 | 0 | 0 |
| posthoc (39-term shortlist, `strength 0`: greedy, terms only for hits) | 0.393 | 0.176 | 0.393 | 46/74 | 0 | 0 |
| relevant (only the clip's terms, 1–13) | 0.393 | **0.092** | 0.393 | 60/74 | 0 | 0 |
| shortlist (19 relevant + 20 distractors) | 0.382 | **0.107** | 0.393 | 57/74 | 0 | 0 |
| distractors only (20) | 0.397 | 0.397 | 0.393 | 21/74 | 0 | 0 |

These are the clips from `run_bench.py make-clips` (deterministic from run to run). An earlier hand-made generation of the
same sentences differed slightly in a few clips (`say` output was not byte-identical, e.g. kubectl-Samantha 3.14 s vs
3.10 s). On those clips: none 0.405, posthoc 0.187, relevant 0.099, shortlist 0.111, distractors 0.405 (raw), with the
same recall and 0 false hits. The sweep and latency sections below used the earlier clips.

Beam without boost (in process, earlier clips): greedy 0.405, beam 2 0.405, beam 4 0.397 raw WER. **Beam search alone buys
nothing.** It is only the vehicle for the booster, so the backend decodes greedily whenever no form is boosted.

What boosting changed (shortlist vs greedy on the earlier clips, per-clip text on stderr of `run_bench.py accuracy`): "cubectal" →
"kubectl" (both voices), "beset prite" → "based pyright" (an alias, so canonicalized to `basedpyright`), "cargo clicky" →
"cargo clippy", "Hipperland" → "hyperland" (Daniel, alias → `Hyprland`), "parsecli args" → "parse cli args", "Parakeek"
→ "Parakeet", and "tail scale" → "tailscale". Partly fixed: "Runba said pi right" → "Run based piright". Still wrong in
every condition: Samantha's "Hipperland" (boosting "hyper land" is not enough), the short `rename` clip ("pars args",
"Parzags to Paz Cleogs"), "based pyrite" (Daniel), and "nanospace" for "namespace".

Distractors are never written into the output. They do perturb segmentation through prefix credit: "tailscale" →
"tail scale" (via "tail wind"), "tsconfig.json" → "ts config.json", and once "cubectal" → "kubectl" (via "kubens").
The net WER change is small (0.405 → 0.405 and 0.393 → 0.397). The client's anchor comparison sees the changes as non-hits.

## Picking beam size and boost (in process, earlier clips, same metrics)

`boost_min = boost_max / 3`. "guard" = clips where the deletion guard replaced the beam (relevant / shortlist /
distractors).

| beam | boost range | relevant: canon WER (recall) | shortlist: canon WER (recall) | distractors: raw WER | guard | 20 s beam decode, no boost (boost adds ~20%) |
|---|---|---|---|---|---|---|
| 2 | 0.67–2 | 0.134 (53) | 0.141 (53) | 0.408 | 0/0/0 | ~390 ms |
| 2 | 1–3 | 0.137 (53) | 0.149 (53) | 0.408 | 0/1/0 | |
| 2 | 1.33–4 | 0.122 (56) | 0.164 (50) | 0.412 | 1/3/4 | |
| 2 | 1.67–5 | 0.115 (58) | 0.141 (53) | 0.420 | 1/2/6 | |
| 3 | 1–3 | 0.130 (56) | 0.126 (56) | 0.393 | (before guard: 1 deletion) | ~500 ms |
| 4 | 0.67–2 | 0.137 (55) | 0.126 (56) | 0.393 | 0/0/0 | |
| **4** | **1–3** | **0.099 (60)** | **0.111 (57)** | **0.405** | **0/0/0** | ~660 ms |
| 4 | 1.33–4 | 0.076 (63) | 0.076 (63) | 0.416 | 0/0/2 | |
| 4 | 1.67–5 | 0.076 (64) | 0.092 (61) | 0.401 | 0/0/6 | |

**Defaults: beam 4, boost 1–3.** Ranges above 3 score better on canonicalized WER, but they produce deletions the
guard cannot catch. In 3 of 20 clips, "refactor use session store in session store dot ts" became
"refactor use session_store.ts": the alias "session store.ts" stretched over "session store in session", so every
anchor word stays time-covered. That is the kind of silent error dictation must not make, and none occurred at 1–3.
For long dictation on a slower machine, `beam_size = 2, boost_min = 0.67, boost_max = 2` roughly halves the beam cost
at canonicalized WER 0.134 / 0.141.

## Failure modes found and fixed

1. **Beam search truncates the utterance end** (parakeet-mlx 0.7, `patience=1`). With beam ≥ 3, the search stops once
   `beam_size · patience` hypotheses have reached the last frame. Blank-with-duration jumps get there first, so the final
   word piece or the full stop is dropped ("push the bran", "in the pro", "cargo clipp"; beam 4: 20/20 clips without a
   final full stop). `length_penalty` does not help. **`patience = 20`** fixes it (0/20) for a few % more decode time.
2. **Boosted beams skip speech.** Hypotheses that hold a partial-match credit survive pruning while emitting blanks with
   long TDT durations. At high boost they delete whole stretches: "run cube in the default nanospace" (lost "get pods"),
   or half of the 20 s clip. **Deletion guard:** a biased decode first runs greedy over the same encoder output (that
   is the anchor anyway), then the beam. If ≥ 2 consecutive greedy words are not overlapped (±80 ms) by any beam
   **token** span, the greedy result is returned and `backend_ms.beam_discarded` is set. Token spans matter: a skipping
   hypothesis keeps a word open across the gap ("parse arg" … "es"), so word spans would hide it. At the defaults the
   guard never fired on these clips. It is a safety net for operators who raise `boost_max`.
3. **The library's booster tabulation is slow.** For every booster state the beam reaches, `boost.tabulate` steps all
   8192 tokens in Python: 22–35 ms per state, 81 states and **1.4–1.65 s** on the 20 s clip with 19 terms. `TrieBooster` reimplements
   `boost.words` (same trie and node numbering, same step / potential / at_end) with an exact decomposition. A state is a
   set of independent trie tracks; the fresh-match part of every token's delta is precomputed per booster, and the
   carried nodes are followed only through the sorted-vocabulary ranges that keep them alive. The result is
   **0.13–0.16 ms per state with bit-identical tables** (asserted against the library in the tests). The 20 s clip
   decode goes from 2.3–2.7 s to ~0.9 s. It is wired in by wrapping `parakeet_mlx.parakeet.tabulate`; other machines
   still go to the library function. The booster build (~40–90 ms for 39 terms) runs on the worker thread when the
   session starts, while audio streams, so it is off the end-of-audio path.

## Latency: end-of-audio → FinalResult (real-time pacing, 20 runs each, client-measured)

| clip | audio | no terms (greedy) p50 / p95 / max | 39-term shortlist (beam 4 + boost) p50 / p95 / max | server p50, shortlist: encoder / decode (greedy + beam) / anchor |
|---|---|---|---|---|
| kubectl-Samantha | 3.1 s | 88 / 98 / 100 ms | **211 / 223 / 293 ms** | 53 / 156 / 0 ms |
| ten-Samantha | 9.0 s | 148 / 158 / 158 ms | **460 / 500 / 526 ms** | 56 / 403 / 0 ms |
| twenty-Samantha | 19.9 s | 242 / 254 / 649 ms | **895 / 2530 / 2744 ms** | 76 / 810 / 0 ms |

- 18 of the 20 runs on the 20 s clip took 853–988 ms. The two outliers (2.5 s and 2.7 s) also had a 6× slower encoder
  (462 ms) and came while the load average rose. That is contention from other work on the machine, not the decoder,
  but a p95 on a shared machine looks like this.
- The anchor costs 0 ms on top: its greedy pass (~35 / 85 / 165 ms) is inside the primary's `decode_ms`.
- Beam cost grows linearly, about 32–40 ms per audio second at beam 4 (greedy: ~8–11). The parakeet-mlx beam loop runs the prediction and joint
  networks once per hypothesis per step, with a host sync each time. Batching hypotheses is the obvious next speed-up;
  it needs our own beam loop.
- At 20 s the shortlist case sits just inside the 1 s key-up→paste target on this Mac, with no margin. See the beam-2
  setting above.

## Streaming / incremental encoding: measured, not adopted

parakeet-mlx has `model.transcribe_stream(context_size=(left, right))`. It switches the encoder to local attention, keeps
a rotating cache, re-decodes only the "draft" tail, and carries booster state across chunks. In-process on the earlier 20
clips, feeding 0.5 s or 1 s chunks:

| decoder | context (left, right frames) | chunk | raw WER (full-attention: 0.405 greedy, 0.401 boosted) | last chunk after EoA, p50 |
|---|---|---|---|---|
| greedy | (256, 16) / (256, 32) / (256, 64) | 1 s | 0.588 / 0.576 / 0.561 | 97 / 107 / 109 ms |
| greedy | same | 0.5 s | 0.676 / 0.672 / 0.649 | 94 / 107 / 109 ms |
| beam 4 + boost (relevant) | same | 1 s | 0.542 / 0.515 / 0.527 | 149 / 193 / 206 ms |

Local attention costs 15–30 WER points: punctuation disappears and words degrade ("back and", "sessions store",
"soap whisper"). The last-chunk cost (~100–200 ms) is not much below today's whole greedy finalize for short
utterances. Not adopted. Doing most of the work while audio streams would instead need full-attention re-encoding of
the growing buffer plus incremental beam state. That is a session-level change (the server is BUFFERED), and this
bench does not justify it yet.

## Limitations

- TTS, 20 clips, one machine shared with other work. Treat the WER numbers as relative and the latency as indicative.
- `_` cannot be produced by the v3 tokenizer, so snake_case and path canonicals depend on aliases and client
  canonicalization.
- The guard catches skipped speech, not "merge" deletions (see boost > 3), which is why `boost_max` is capped at 3.
- Distractors do not insert terms, but they shift word segmentation.
- No cross-session booster cache: the server promises to cache nothing, and `BiasList.content_hash` (the opt-in for
  caching compiled bias) is not passed to backends. Each session builds its booster (~40–90 ms) on the worker thread
  at session start, which can delay another session's decode queued behind it.
- N-best is still 1: parakeet-mlx returns only the best beam. Beam word confidences are token × duration probabilities;
  greedy ones are entropy-based. Neither is calibrated.
- The library's `tabulate` is wrapped by assignment (parakeet-mlx is pinned `<0.8`, and the equivalence test will fail if
  `boost.words` semantics change).

## Reproduce

```sh
uv sync                                                    # repo root; dev group includes hark-server[mlx]
B=docs/bench/parakeet-mlx-boost
uv run python $B/run_bench.py make-clips /tmp/hark-bias    # say + afconvert, 20 WAVs
(cd server && uv run hark-server --backend parakeet_mlx --listen 127.0.0.1:51031 &)
# wait for "model ready" in the server log
uv run python $B/run_bench.py accuracy /tmp/hark-bias --server 127.0.0.1:51031          # table above (+ per-clip text on stderr)
uv run python $B/run_bench.py latency  /tmp/hark-bias --server 127.0.0.1:51031 --repeat 20
HARK_SLOW=1 uv run pytest server/tests/test_parakeet_mlx.py                             # real-model test
```

Other settings go in `[backend.options]` (e.g. `beam_size = 2`, `boost_min = 0.67`, `boost_max = 2`, `patience = 20`).
