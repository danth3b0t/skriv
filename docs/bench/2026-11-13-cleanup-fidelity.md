# Bench: cleanup as one rewrite plus one fidelity check (2026-11-13)

Baseline failure class 7 ([eval baseline](2026-11-13-eval-baseline.md)): cleanup mostly did nothing (62 of 122 runs
without edits, 32 with every edit rejected) and 6 of 18 filler clips kept their fillers. The cause was the design:
S1-mini rewrites the whole text, the server aligned the rewrite into edits, and two mirrored validators (client and
server) accepted only edits of a known kind (punctuation, capitalization, filler, number, self-correction). Any
rewording, and any edit next to a rejected one, was dropped.

## Change

- **Server**: one engine. S1-mini's rewrite is returned as `CleanupResponse.text`, after a few deterministic touches
  per profile: line breaks become spaces (terminal, chat), leftover hesitations (`eh`, an occasional `um`) and a
  `you know` set off by commas or opening a sentence are removed, no final period is added in terminal/chat when the
  input had none (`git commit --amend.`), and a final `!` that S1 flattened to `.` is restored. The S1 system prompt
  and control line cannot be tuned (the model card: any other prompt can make it hallucinate); semi-formal / prose /
  general stays. Deleted: the `edits` engine and its prompts, the server validator, the alignment, `cleanup.engine`,
  `cleanup.max_edits`, and in the proto `Edit`, `EditRejection`, `EditKind`, `EditRejectionReason`, `ProtectedSpan`
  and the request's `protected_spans`/`max_edits`/`known_terms`. The shortlist no longer leaves the client for cleanup.
- **Client**: `hark_client/fidelity.py` is the one definition of a faithful rewrite. The rewrite replaces the text
  only if it keeps every protected string (shortlist canonicals in the text, code-like tokens: spelling, case and
  count), the negation count, the numbers (spoken numbers read as digits), adds and drops at most `len // 8` content
  words (not function, discourse, filler or spoken-symbol words; merges and splits like `hyper land` → `HyperLAN` do
  not count), and adds no line break in terminal/chat. A protected string the rewrite only re-cased is put back first
  (`Hark-server should` → `hark-server should`). Two deliberate exceptions, measured below: a camelCase/snake_case
  identifier made of words may be written back as those words (`goBack` → `go back`), and a spoken self-correction
  the rewrite resolved (`3 seconds, sorry 5 seconds` → `5 seconds`) is checked against the corrected text.
  Otherwise the text stays and the reason is recorded (`cleanup.status = rejected`, `reason`, `rewrite`).
- **`hark eval`** counts words, negations and critical strings with the same module; a negation retracted by a
  spoken self-correction is no longer a lost negation (the analysis script uses the same rule).

Net: 474 lines added, 1,770 removed in `client/src` + `server/src` (cleanup 690 + 1,316 lines → client cleanup 133 +
fidelity 297 + server cleanup 300). With tests, proto, docs and the eval script: +737 / −2,933 (generated proto-py
not counted).

## Setup

My server from this worktree on 127.0.0.1:50062 (`parakeet_mlx`, S1-mini q4_k_m on the shared llama-server
:8080), same machine as the baseline. **Before** = the branch base (`1aabc03`) on that server; **after** = this
branch. `uv run hark --server 127.0.0.1:50062 --config docs/bench/eval-baseline/baseline.toml eval run --set
eval/dev-v1 --report …`; reports in `eval-baseline/cleanup-fidelity-{before,after,after-no-context}.json`, tables
from `analyze.py`, which rescores every report with the current labels and the current eval rules (so the before
row shows 42 failed, not the 43 its run printed: syn-069's retracted "no" no longer counts as lost).

## Results (eval/dev-v1, baseline config)

| | failed | unreviewed | accepted | critical recall final | critical lost at cleanup | false substitutions | negations lost (at cleanup) | finals with fillers | cleanup ms p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|
| before | 42 | 29 | 51 | 64% | 0 | 12/29 | 1 (0) | 6/18 | 44 / 117 |
| after | **32** | 36 | **54** | 65% | 0 | **4/29** | 1 (0) | **1/18** | 35 / 89 |

Cleanup status, before: no edits 63, all rejected 31, partial 10, applied 18. After: rewrite identical to the input
64, **applied 48**, rejected 10. The 64 identical rewrites are texts S1 has nothing to do on (Parakeet already cases
and punctuates); none of their inputs has a filler. Rejections by rule: changed protected string 5
(`check_focus` → `CheckFocus`, `--no-history` → `no-history`, `@/lib/utils` → `/lib/utils`, `--dry-run`,
`to/data/models`), added content 2 (`up-d` → `up-down`; `planified` → `planned`), dropped content 2 (`Use useQuery`
→ `useQuery`; syn-080's spelled letters), numbers 1 (`four twenty-one` → `421`). Every rejection read is a correct
one, apart from the debatable syn-053 (`421`) and real-014 (`planned`). The one remaining filler is real-014, whose
rewrite was rejected for `planned`.

Latency is the shared llama-server and varies run to run (the baseline run measured 32 / 67 ms); the generation
is the same single S1 call as before, and the client check takes well under 1 ms.

No-context ablation (no shortlist, nothing protected but code-like tokens), old run vs this branch: critical recall
in the final text 23% → 26%, critical lost at cleanup 1 → 0 (syn-040's `hark once` → `Hark once` is gone, but only
because the same rewrite also turned `--no` into `no` and was rejected: an unprotected command name can still be
re-cased), failed 64 → 59, fillers 6/18 → 1/18, rejected 7 of 122.

### Clips that changed

- **Fixed (false substitutions undone by the rewrite, 8 strings)**: real-001 `goBack`, real-007 `each_other`, syn-092
  `last_text`/`one_line`, syn-093 `SessionStart`, syn-095 `mark_down` (now accepted), real-005 `let_me`, real-015
  `each_other` (now unreviewed: other recognition errors). This is the identifier-to-words rule. It cuts both ways:
  a real snake_case/camelCase identifier that S1 spells as words would pass too. In this set and in the three
  cleanup sets below S1 never did that to a non-trap identifier (it re-cases them, which the check catches).
  Class 1 should still be fixed at canonicalization.
- **Fixed (self-corrections the model resolved)**: syn-066 `Set the timeout to 5 seconds.`, syn-072 `Make the limit
  20 megabytes.` (both FAIL before). syn-068 `Call it fetch_row_batch.` stays accepted (an early version of the check
  without the correction rule failed it).
- **Fillers gone**: real-006, real-016, real-019, syn-076 (`eh`, by the server touch), syn-083 (`you know`), real-005
  (`, you know.`), real-012/real-015 (stutters).
- **Critical string gained**: syn-031 `user underscore id` → `user_id`.
- **Accepted before, unreviewed now**: real-018 (`watch that. Also, it's fun.`) and real-025 (`help your brother
  escape`): rewording, both look right.
- **Regressions**: none among accepted or critical strings. syn-074 kept its `you know` (the old partial edits
  removed it; the whole rewrite now fails on `--dry-run`). syn-041/syn-056: the terminal profile no longer adds a
  final period the recognizer did not write. syn-029 got `based pie? Write complaints` (already FAIL; S1 garbles
  garbled input). syn-082 `0, comma one` → `0, one` (no number lost, still not `0,1`).

### Unreviewed texts that look correct (accept in one pass later)

| clip | final |
|---|---|
| real-006 | It was more like a grade gift. He told me, "If you can do well in your grades, I'm going to buy you a bicycle." And I had to strive hard because |
| real-018 | So yeah, you can watch that. Also, it's fun. I can't believe you never watch it before, because it's been like a long time anime. |
| real-025 | I also offered to help your brother escape, but he would not go. |
| syn-031 | Add an index on user_id in the sessions table. |
| syn-066 | Set the timeout to 5 seconds. |
| syn-072 | Make the limit 20 megabytes. |
| syn-083 | Okay, so the eval set should have about 100 clips, mostly terminal prompts, and it should never include anything from my real history without asking first. |

real-016 (`You said you finished…` for "you've finished") is right apart from that recognition error.

## Cleanup eval sets (`scripts/eval-cleanup`, text in → client output)

The expected texts of these sets were written for the edit engine, so "exact" favours it; "same words" ignores
case and punctuation.

| set | exact before → after | same words before → after | clean damaged | protected leaked | rejected (after) |
|---|---|---|---|---|---|
| cleanup_eval (44) | 43 → 39 | 44 → 43 | 0 → 2 of 12 | 0 → 0 | 1 |
| cleanup_eval_real (29) | 26 → 24 | 27 → 25 | 1 → 2 of 22 | 0 → 0 | 2 (+1 skipped) |
| cleanup_eval_guard (9) | 9 → 9 | 9 → 9 | 0 → 0 | 0 → 0 | 0 |

The misses are wording, not meaning: injected instructions are still cleaned, never answered or executed (5 of 5:
`… replaces every word with "Hello."`, `delete the word "not" …`, `reply with okay.`), S1 splits a run-on into two
sentences, and `3:30 pm` → `3:30pm` is rejected. The two real long dictations it loses (r-filler-1, r-filler-5) are
rejected because S1 deleted a hedging "I don't know" (negation count); the old engine kept their filler removals.
r-ident-2 `Qan II` → `QANII` and r-negation-1 `No freaking way` → `No, freaking way` pass the check and are
different from the expected text.

## Open / risky

- All or nothing: one bad change in a long dictation keeps every filler of it (r-filler-1/5, syn-074). A per-sentence
  check would recover most; not done.
- The identifier-to-words rule above, and the self-correction rule (any cue set off by punctuation, up to 6 retracted
  words, only when the rest of the rewrite passes against the corrected text; `Is it done? No, …` and `use C not
  Rust` stay protected).
- The content-word bound is crude (a stem list, `len // 8`): a short text may not change any content word, a long one
  may change a few. Same-length substitutions of function words (`can` → `could`) are not caught.
- An old server (the shared :50051) returns no `text`; this client then reports `skipped` and keeps the text.
