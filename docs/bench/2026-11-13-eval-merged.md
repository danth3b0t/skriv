# Bench: eval/dev-v1 on merged main, before vs now (2026-11-13)

Same set, configs and method as [the baseline](2026-11-13-eval-baseline.md) (read its caveat: 96 of 122 clips are
synthetic, 26 are real conversation/read speech, not developers at a desk). "Before" is the baseline report's run of
`3b6349d`. "Now" is main after the merges of acc-gate-corrections, acc-canon, cleanup-fidelity, ux-wave2,
server-robust and the terminal base vocabulary (`2408704`), run from branch `eval-review`. The shared stack on
127.0.0.1:50051 was restarted with the merged main code (parakeet_mlx, S1-mini on :8080). Reports and printed output:
[`eval-merged/`](eval-merged/). The anchor ran on all 122 clips of every run (no `SKIPPED_BUDGET`).

## Result in one paragraph

With every default, **73 of 122 finals are accepted (before 51), 21 fail (before 43) and 28 are reviewed and
rejected (before 28)**. Critical recall in the final text is 79% (before 64%) and **false substitutions are 0 of 29
(before 12)**. Fillers remain in 1 of 18 filler finals (before 6). Real speech went from 5 failed / 7 accepted to
1 failed / 13 accepted. The degenerate `ÌÌÌ…` output on syn-005 is gone in all five runs. What is left is mostly
recognition (34 of the 49 open clips have a recognition error that no context covers), then 5 clips where the gate's
remaining reverts or kept biased errors do damage (all three reverts in this run made the text worse), 6
canonicalization misses, and 4 cleanup rewrites. Context is still the biggest lever (failed 21 vs 58 without it),
and the gate is still needed for its confidences (failed 21 vs 47 without it) even though it now changes almost no
text. The open point from acc-canon is **still true**: replayed shortlists do not carry the new lexicon spoken forms
into the bias list. A probe that adds them changes 9 clips, with +2 critical strings and 1 FAIL → correct (syn-078).

## Review done in this pass

- All 47 unreviewed baseline finals were read against `said` with the baseline rules (fillers gone,
  self-corrections applied, wording may be clearer, nothing added/dropped/reversed, identifiers right). 19 accepted
  (`hark eval accept`), 28 rejected. Then the unreviewed ablation finals: 9 more texts accepted (fillers left, false
  starts kept, or recognition errors are rejected). One probe final accepted (syn-078, the exact text). Total: 29
  accepted texts in 25 clips.
- **Label fix, real-004** (EdACC): `said` "…like most of the windows it's just easier…" → "…like most of the winter it
  was just easier…". The biased decode, the anchor (both parakeet-tdt-0.6b-v3) and whisper large-v3-turbo all hear
  "winter", and whisper hears "it was". The note records the change. No other label was changed. `critical` and
  `forbidden` are untouched.
- Accepted with a judgment call: real-005 (the opening false start "you might" and the trailing "you know you you"
  are dropped), real-016 no-context ("you finished" for "you've finished"; whisper hears the same), real-018 (the
  ambiguous "you can watch that also it's fun", split either way). Rejected with a judgment call: syn-025 (`that's
  wrong?` turns the claim into a question), syn-082 ("training run" → "training"), syn-003 ("looks good, ship it" →
  "Looks good to ship it.").

## Results

| condition | run | failed | rejected | accepted | recognition WER, mean (median) | critical recall: raw / gated / canonical / uncleaned / final | false substitutions | negations lost¹ | finals with fillers² | key-up → final ms p50 / p95 | server end → final ms p50 / p95 | cleanup ms p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **baseline** | before | 43 | 28 | 51 | 14.8% (8.3%) | 24% / 23% / 61% / 64% / 64% | 12/29 | 2 | 6/18 | 265 / 478 | 205 / 406 | 32 / 67 |
| | **now** | **21** | 28 | **73** | 14.0% (8.3%) | 24% / 24% / 69% / 78% / **79%** | **0/29** | 1 | **1/18** | 263 / 486 | 204 / 419 | 31 / 68 |
| no-context | before | 65 | 22 | 35 | 16.4% (8.3%) | 20% / 20% / 20% / 24% / 23% | 0/29 | 3 | 6/18 | 78 / 141 | 66 / 117 | 32 / 65 |
| | now | 58 | 19 | 45 | 15.9% (8.3%) | 20% / 20% / 20% / 30% / 31% | 0/29 | 1 | 1/18 | 78 / 134 | 66 / 110 | 32 / 66 |
| no-cleanup | before | 45 | 33 | 44 | 14.8% (8.3%) | 24% / 23% / 61% / 64% / 64% | 12/29 | 2 | 16/18 | 284 / 478 | 220 / 409 | 0 |
| | now | 22 | 41 | 59 | 14.0% (8.3%) | 24% / 24% / 69% / 78% / 78% | 0/29 | 1 | 16/18 | 262 / 465 | 204 / 392 | 0 |
| no-gate | before | 63 | 23 | 36 | 14.8% (8.3%) | 24% / 24% / 36% / 39% / 40% | 8/29 | 3 | 4/18 | 269 / 489 | 207 / 418 | 34 / 62 |
| | now | 47 | 20 | 55 | 14.0% (8.3%) | 24% / 24% / 35% / 44% / 48% | 0/29 | 1 | 1/18 | 270 / 479 | 208 / 405 | 34 / 65 |

"Before" rows are the numbers printed in the baseline report. `analyze.py` rescores them with today's labels and
rules as 42 / 29 / 51 (baseline), 64 / 23 / 35, 44 / 34 / 44 and 62 / 23 / 37, with 1 negation lost in each.
None of today's accepted texts matches an old final, and since cleanup-fidelity a retracted "no" no longer counts.
"Rejected" = not failed and not accepted. In both runs every such baseline final was reviewed and rejected.
¹ Fewer negations in the final than in `said`, unless that text is accepted or the negation was retracted by a
self-correction. Now: only real-017 ("I never watched it" → "And I watch it", recognition). ² Finals with
um/uh/eh/mm/ah, of the 18 clips whose `said` has them. Now: real-014 only (its rewrite was rejected for
"planified" → "planned").

**What each stage contributes now** (baseline vs ablation):

- **Context**: recall in the final text 31% → 79%, failed 58 → 21, accepted 45 → 73. Raw WER 15.9% → 14.0%. Cost:
  0 false substitutions (before: all 12 came from context), about 185 ms at p50 of key-up → final (biased beam +
  anchor). The terminal base vocabulary is part of "context" here (it is not added when `max_terms = 0`).
- **Anchor gate**: off → failed 21 → 47, recall 79% → 48%. Its text job has shrunk: it changed 3 clips, all for
  the worse (before: 55 changed, 15 better / 15 worse). Decisions: `kept/no_hit` 41, `kept/close` 18,
  `kept/same_letters` 7, `kept/anchor_dropped` 2, `reverted/anchor_confident` 1 (syn-041), `reverted/word_dropped` 1
  (syn-045), `reverted/in_anchor` 1 (syn-076). Before: `reverted/outside_term` 44, `kept/close` 22, 3 other reverts.
  What still matters is the confidence hand-off: without the gate, canonicalization sees beam confidences (~0.1 on
  boosted words) and skips most identifiers (canonical recall 35% vs 69%).
- **Cleanup**: accepted 59 → 73, fillers 16/18 → 1/18, failed 22 → 21 (syn-038: cleanup lower-cases "UV Sync" to
  `uv sync`). No critical string and no negation is lost at cleanup in any condition except no-context. There,
  syn-047's sentence capital turns `docker compose up -d` into "Docker compose up -d": without the shortlist the
  command is not protected. Status: rewrite identical 72, applied 44, rejected 6 (changed protected string 5, added content 1). Before:
  no edits 62, all rejected 32, partial 10, applied 18. Latency unchanged (31 ms p50).

By group (baseline now, before in brackets; small n): EdACC 1 failed / 8 accepted of 20 (5 / 2), LibriSpeech 0 / 5 of 6
(0 / 5). en_IN is still the weakest synthetic voice: 20.9% WER, 5 failed / 3 accepted of 14 (7 / 3). en_US 6 / 25 of
34 (13 / 18), en_GB 3 / 13 of 18 (8 / 8). Prose/chat 0 failed / 11 accepted of 15 (2 / 9). Terminal 21 / 62 of 107
(40 / 42). Quiet-room clips still have the highest synthetic WER (16.2%) because the hardest prompts landed there.
Full tables: `uv run python docs/bench/eval-baseline/analyze.py --reports docs/bench/eval-merged`.

### Baseline failure classes, before → now

| # | class (baseline report) | before | now |
|---|---|---|---|
| 1 | canonicalization turns ordinary phrases into identifiers | 10 clips, 12 strings | **0**. Cost: 3 everyday-word identifiers said with no cue stay prose (syn-018, syn-024, syn-033) |
| 2 | spoken forms missing | 15 clips | 4: syn-075 "Hyper LAN", syn-078 "harp control" (fixed once the bias gets the forms, see below), syn-044 "journal control-user", syn-029 |
| 3 | gate reverts correct text | 12 clips damaged | 3 harmful reverts (syn-041, syn-045, syn-076) + 2 kept biased errors (syn-008 "MT", syn-025 "?") |
| 4 | acoustic errors, no context | ~16 clips | 22 synthetic clips (some only partly). syn-010 fixed by the terminal vocabulary. `git` → "Get" (syn-005) unchanged |
| 5 | spoken symbols and numbers | 8 clips | 1: syn-037 `dash-fix` (needs "ruff" recognized first) |
| 6 | self-corrections | 4 clips | **0**. syn-065, -066, -072 resolved, real-009 no longer drops a claim |
| 7 | cleanup leaves fillers / does nothing | 7 clips | 2 fillers left: real-014, syn-074 ("you know"). New: 2 unfaithful rewrites pass the check (below) |
| 8 | degenerate recognizer output | syn-005 in 4 of 5 runs | **0** in 5 runs |
| 9 | negations changed by recognition (real speech) | 4 clips | 4 clips, unchanged (real-003, -009, -013 add "No,", real-017 loses "never") |

## Open point from acc-canon: lexicon spoken forms and the bias list. Still true.

`evalset.run_clip` loads the clip with `retry.load_snapshot`, which reads each term's `spoken` from the saved
`context.json`. `asr.bias_list` sends exactly `t.spoken`. Only `canonicalize()` re-derives `spoken_forms()`, and so do
the terminal base commands and `[[words]]`, which are built fresh. So for the 108 replayed shortlists the recognizer
is never biased toward "hyper land", "journal control", "sequel light", "hark control" and the rest. **308 of
2,492 replayed terms lack forms that `spoken_forms()` now derives, and 22 clips with critical strings are affected.**
Live dictation is not affected (`rank` calls `spoken_forms`). `hark retry` is affected the same way as the eval.

Measured with a temporary probe (not committed): `run_clip` merged `spoken_forms(t.canonical, t.kind)` into every
replayed term, baseline config. Report: [`eval-merged/probe-fresh-aliases.json`](eval-merged/probe-fresh-aliases.json).

| | failed | rejected | accepted | WER | critical recall raw / gated / canonical / uncleaned / final |
|---|---|---|---|---|---|
| baseline now | 21 | 28 | 73 | 14.0% | 24% / 24% / 69% / 78% / 79% |
| + fresh spoken forms in the bias list | 20 | 29 | 73 | 14.2% | 23% / 23% / 71% / 79% / 80% |

9 clips change. syn-078 "harp control toggle" → "hark control toggle" → `hark ctl toggle` (FAIL → correct; that
text is now accepted). syn-073 and syn-047 reach the same final by another route. syn-075: the bias now gives "hyper
land" and "hyper control" → `hyprctl`. But the gate reverts "hyper land" to the anchor's "Hyper LAN" as an
`inflection` ("land"/"lan" share a stem plus "d"), so `Hyprland` is still missed. syn-029: "based pie right" →
`basedpyright` (still FAIL on `read_wav`/`write_wav`). syn-007 is worse: "pyTest" → "pie test", which no stage turns
back into `pytest`. syn-037 and syn-044 change but stay wrong. The effect is small, but it is real, and the eval
should measure what live dictation does. Fix: re-derive spoken forms when a snapshot is replayed
(`retry.load_snapshot` or `run_clip`), or rebuild the set's `context.json` with the current `spoken_forms`.

## Triage of the 49 open clips (now), ranked by stage

Ranked by how many clips a fix would recover and how cheap it is. FAIL = mechanical check failed; rej = reviewed and
rejected.

### A. Gate: 3 harmful reverts, 2 kept biased errors, 1 hidden by the replay (5 clips + syn-075)

| clip | | said | biased → gated / final | rule |
|---|---|---|---|---|
| syn-041 | rej | "git push … don't force push main" | "Git push" → "Get push" (anchor confident); "bush" kept (`no_hit`, anchor had "push") | `anchor_confident` |
| syn-045 | FAIL | "chmod plus x scripts slash make dash eval dash set" | biased exactly right → anchor "script slash make dash a val dash set" → `chmod +x script /make -a val-set` | `word_dropped` (anchor's "a" is part of "eval", not a dropped function word) |
| syn-076 | FAIL | "call model dot train" | "model dot train" → anchor "model.Train", never fixed (critical `model.train`) | `in_anchor` takes the anchor's case |
| syn-008 | rej | "handle empty transcripts" | biased "MT" kept over anchor "empty" | `no_hit` |
| syn-025 | rej | "…single letters, that's wrong" | biased "letters that's wrong?" kept over anchor ", that's wrong." | `no_hit` (punctuation) |
| syn-075 | FAIL | "on Hyprland" | with fresh bias forms: "hyper land" → anchor "Hyper LAN" | `inflection` |

Next fixes (`gate.py`): run the closeness test before `word_dropped`, so an anchor function word whose letters are
part of the term's spoken form ("a val" ≈ "eval") does not count as dropped. Never take `inflection` when the biased
words are exactly one of the term's forms. In `in_anchor`, keep the biased spelling when the two differ only in case or
symbols. For `anchor_confident` on a COMMAND term, accept "term + one of its subcommands" (`git push`) as evidence
for the bias. For `no_hit`, the variant measured on acc-gate-corrections (revert when the biased decode has fewer
words: 4 better, 2 worse) does not cover these. A rule "anchor is a dictionary word, biased is an all-caps
out-of-vocabulary token" would fix syn-008. Measure it before taking it.

### B. Canonicalization and the replayed bias list (6 clips)

| clip | | missing | cause |
|---|---|---|---|
| syn-018 | FAIL | `wait_final` ("why does wait final time out") | everyday-word alias, no cue: the deliberate class-1 trade |
| syn-024 | FAIL | `competition_key` ("a test for competition key with") | same |
| syn-033 | FAIL | `useEffect` ("fetching in a use effect") | same (also cleanup, below) |
| syn-007 | FAIL | `uv run pytest -k gate` | recognizer wrote "pyTest"; `pytest` is on the list but case-only rewrites are allowed only for commands mid-sentence |
| syn-044 | FAIL | `journalctl --user -u hark` | "journal control-user": the recognizer's hyphen joins the alias to the next word, so it never matches. Then "dash u hark" → "UROC" (recognition) |
| syn-078 | FAIL | `hark ctl toggle` | "harp control": the bias never had "hark control" (replay, above). Fixed in the probe |

Next fixes (`context/canon.py`): split recognizer-joined `word-word` tokens before matching aliases (the symbols
step already knows when they are flags). Allow a case-only rewrite of a single token that equals a shortlist term
ignoring case (`pyTest` → `pytest`, `model.Train` → `model.train`); it adds no word, so it is safe. The three
everyday-word misses should stay misses until the recognizer's bias hits are passed to `canonicalize()` as
evidence: a hit on the span is the missing cue. The pipeline has the `BiasHit`s, and `canonicalize` does not take
them yet.

### C. Cleanup (4 clips; 2 rewrites pass the fidelity check but are not faithful)

- syn-033 (FAIL also for `useEffect`): "Use useQuery from TanStack Query…" → "useQuery from TanStack Query…". The verb
  is dropped, the check passes ("use" is not counted as a content word), and the imperative is gone.
- syn-029 (FAIL): "read wave and write wave-based pie write complaints" → "ReadWave and WriteWave-based pie? Write
  complaints". Cleanup invented two CamelCase identifiers that are not on the shortlist (merges do not count as added
  content).
- real-014 (rej): the rewrite is rejected for "planified" → "planned", so "um" and "I I" stay (all or nothing).
- syn-074 (FAIL, also recognition): the rewrite is rejected for `--dry-run`, so "Oh basically" and "you know" stay.

Next fixes (`fidelity.py`): count a new identifier-shaped token (CamelCase/snake_case) that is in neither the input
nor the shortlist as added content. Count a dropped sentence-initial verb as dropped content. Check per sentence
instead of all or nothing (this also fixes real-014 and syn-074, and r-filler-1/5 of the cleanup sets).

### D. Symbols (1 clip)

- syn-037 (FAIL): "Run Rough Check with dash-fix, and then baste Pyrig." `dash-fix` → `--fix` waits for a recognized
  command, and "ruff" was heard as "Rough". Fix: also accept `dash-<known flag on the shortlist>` without the
  command. `basedpyright` is recognition (the probe gets "based pi").

### E. Recognition, with no context that covers the word (34 clips, some also listed above)

Synthetic (22): syn-005 and syn-041 `git` → "Get" (the terminal vocabulary puts `git` on the list, and the boost does
not win; syn-041's biased decode *had* "Git" before the gate reverted it), syn-012 "main" → "me" (reverses the target
branch; empty context), syn-016 "make" → "I", syn-017 "add" → "At", syn-019 "to, uh, terminal like" → "2 a terminal
light", syn-003 "looks good, ship it" → "Looks good to ship it", syn-032 httpx, syn-042 zod (empty context), syn-043
"dir" → "diar" (so `HARK_MODELS_DIR`, on the list, is not matched), syn-046 "kube config" → "cube config", syn-049
tauri, syn-051 "3.12, 3.13" run together as "3.123.13", syn-056 CUDA → "PUDA" (empty context), syn-063 "bar" →
"VAR", syn-069 "fallback" → "foulback", syn-073 "app id" → "agit", syn-074 flags, syn-079 "dir" → "diet", "temp" →
"10", syn-080 spelled `pnpm`/vitest, syn-082 "training run" → "training", syn-090 "offsite" → "inside".

Real speech (12, as in the baseline): real-002, -003, -008, -009, -011, -012, -013, -015, -017, -019, -020, -024. Four
change a negation: "you know" heard as "No," (real-003, -009, -013) and real-017 "never watched" → "watch".
real-008 and real-011 lose or mishear the first clause.

Next fixes: in the terminal profile, a deterministic "Get" + git subcommand (status, push, pull, commit, diff, log,
checkout, rebase, stash) at the start of the utterance → `git`. This covers syn-005 and, with the gate fix,
syn-041. A fuzzy alias match when the speaker spelled the separators ("hark underscore models underscore diar" has
three spoken separators, which is strong evidence) would recover syn-043. The rest needs a better acoustic model or
more context (`main` in a git sentence, CUDA in an ML repo), not text rules.

## Reproduce

```sh
for c in baseline no-context no-cleanup no-gate; do
  uv run hark --config docs/bench/eval-baseline/$c.toml eval run --set eval/dev-v1 \
    --report docs/bench/eval-merged/$c.json > docs/bench/eval-merged/$c.txt
done
uv run python docs/bench/eval-baseline/analyze.py --reports docs/bench/eval-merged   # now
uv run python docs/bench/eval-baseline/analyze.py                                    # before, rescored
```

`analyze.py` now takes `--reports DIR` (default: `docs/bench/eval-baseline`). The probe row needs the temporary
two-line change to `evalset.run_clip` described above.
