# Bench: anchor gate and self-corrections (failure classes 3 and 6), 2026-11-13

Branch `acc-gate-corrections`, client only, shared server. Set `eval/dev-v1`, config
[`eval-baseline/baseline.toml`](eval-baseline/baseline.toml). Baseline report:
[2026-11-13-eval-baseline.md](2026-11-13-eval-baseline.md).

## Changes

- **Gate** (`gate.py`): the output is now the *biased* decode. Every biased word gets the
  confidence of the anchor word aligned to it. In a differing region it gets the anchor's
  least confident word there, and its own beam confidence if the anchor heard nothing.
  Canonicalization has usable confidences whether or not anything is reverted. A region
  with no bias hit keeps the biased words (`kept/no_hit`). A hit region keeps them when the
  anchor has the same letters (case, spacing or punctuation only: `same_letters`), or when
  its neighbours really differ and the anchor has fewer words (`anchor_dropped`: "the is
  terminal" is no longer reverted to "this terminal"). A pure bias insertion (the anchor
  heard nothing at all) is still reverted (`inserted`). The other hit rules are unchanged.
- **Corrections** (`corrections.py`): value substitutions after a cue. "3 seconds, sorry 5
  seconds" (a number before a repeated unit) and "use Postgres, no wait, SQLite for the
  cache" (one value for another of the same kind: number, word with a digit or code
  punctuation, or a capitalized word that does not start the sentence). They never apply
  across a sentence end. A one-word swap needs the cue set off by commas, on both sides
  and with numbers on both sides for a bare cue. A swapped name must end its clause (punctuation, end of text, or
  "for/in/on/to/and/…" after it), so "Ask Sarah, no wait, John wants to know" does not
  fire. A bare cue right after a sentence end is never a cue (real-009).
- `pipeline.py`: no change needed (it already hands `gated.words` to canonicalization).

## Measuring

The shared server was busy. With `ANCHOR_MODE_IF_CHEAP` it skipped the anchor
(`SKIPPED_BUDGET`) on 16 clips of the plain before-run, and recognition WER moved from 14.8%
to 15.8%. That run is not comparable. To get a stable reference, a temporary probe (not
committed) re-ran the before state with `result.anchor` required, and pickled every clip's
`FinalResult`, bias terms and shortlist. That run (**before**) reproduces the committed
baseline exactly: same decisions, same numbers. The probe then replayed gate →
canonicalize → symbols → corrections → guardrails offline, and its texts matched the live
run's uncleaned text on all 122 clips. Every variant below was measured that way first. The
**after** run is a plain live run (1 clip without anchor: syn-074).

| condition | failed | unreviewed | accepted | gated WER (mean, capped) | critical recall raw / gated / canonical / uncleaned / final | false subst. | negations lost¹ | fillers left |
|---|---|---|---|---|---|---|---|---|
| before (anchor required) | 43 | 28 | 51 | 15.1% (real 15.3%) | 24 / 23 / 61 / 64 / **64%** | 12/29 | 2 | 6/18 |
| **after** | **39** | 34 | 49 | **14.8%** (real **14.5%**) | 24 / 24 / 64 / 67 / **68%** | 12/29 | 3 | 4/18 |
| baseline no-gate (reference) | 63 | 23 | 36 | 14.8% (real 14.5%) | 24 / 24 / 36 / 39 / 40% | 8/29 | 3 | 4/18 |

¹ After: real-017 (recognition, as before), syn-069 (as before) and syn-072. syn-072's
lost "no" is the retracted cue of a correct correction, and the clip leaves the list once
its text is accepted.

Critical strings lost at the gate: 1 → 0. Never produced: 41 → 38. Gate decisions:
before `reverted/outside_term` 44, `kept/close` 22, `reverted/in_anchor` 2,
`reverted/anchor_confident` 1. After `kept/no_hit` 40, `kept/close` 15, `kept/same_letters` 7,
`kept/anchor_dropped` 2, `reverted/in_anchor` 1. Offline, on identical server output: critical
strings in the uncleaned text go 77 → 81 of 121 with the gate (47 without it), and forbidden
strings in the uncleaned text go 15 → 12. The 12 false substitutions in the final text are all
class 1 (canonicalization) and do not change. The no-gate gap remains (recall 68% vs 40%),
and it now comes from the confidences alone, since the gate reverts almost nothing.

## Clips that changed (final text, before → after)

Better:
- syn-036 "Hark history" → "hark history" (case-only revert gone; **FAIL → accepted**).
- syn-016 `load_snapshot`, syn-019 `is_terminal`, syn-087 `S1-mini` now present (the
  clips still fail on other strings or words: "I load_snapshot", "FocusInfo 2 a terminal light",
  `Qwen3`).
- real-008: the first sentence is back ("I asked you a question. So, why…"; still misheard
  "let me ask"). real-004 "so hard … Edinburgh", real-013 "is the", real-002 "quick",
  real-016 "you've", real-019 "nineteen", syn-081 "Redis", syn-054 "Parakeet TdT",
  syn-007 "uv run pyTest -k" (was "UV runpytest-k"), syn-073 "APID" (was "agit").
- syn-066 "Set the timeout to 5 seconds." and syn-072 "Make the limit 20 megabytes."
  (FAIL → correct, unreviewed). syn-068 is now resolved deterministically before cleanup
  (its final was already right).
- real-009 keeps "the ghost one left. No, …": no claim dropped.

Worse (the cost of keeping biased text outside hits, and of its punctuation):
- syn-008 "handle empty transcripts" → "handle MT transcripts" and syn-015 "So the bug…"
  (biased-decode errors outside any hit; **accepted → unreviewed**).
- syn-025 "…single letters, that's wrong." → "…letters that's wrong?" (the biased decode's
  punctuation; **accepted → unreviewed**).
- real-017 "ear bought it" → "earbudy", real-020 "uh ride" → "to ride", real-006 "it it" →
  "it's it", syn-079 "slash temp" → "/10", syn-032, syn-049 (unreviewed or failing before
  too).
- syn-005 degenerate output: `<unk>…` → `ÌÌÌ…` (class 8, not changed here).

Net accepted 51 → 49 until the clips below are reviewed. With syn-066, syn-072 and syn-081
accepted, it is 52.

## Variants measured and not taken

- **Revert a no-hit region where the biased decode has fewer words** (the mirror of
  `anchor_dropped`): gated WER 14.84% → 14.68%, 4 clips better (real-017, real-012, syn-018,
  syn-079), 2 worse (real-014, syn-049), recall unchanged. It contradicts "revert only
  regions that overlap a bias hit", so it is left as an option.
- **Keep the anchor's words in identical runs** (its punctuation and case), biased only
  in hit regions and kept regions: fixes syn-025, syn-059, syn-062, syn-050 punctuation but
  breaks real-018, syn-088, syn-006, syn-041 and splices seams ("So The bug", "Oh no not
  yet"). A wash, and two decodes mixed word by word; not taken.

## Why cleanup's deletion of retracted values was rejected as `protected_span`

Not changed here (cleanup is being rewritten on another branch). In syn-066 and syn-072
the S1 rewrite is right ("to 3 seconds, sorry" → "to"). The server's
`self_correction_cut` (`server/src/hark_server/cleanup.py`, mirrored by the client's
`cleanup.self_correction_cut`) only accepts a **bare** cue ("no", "sorry") when the
correction repeats the first retracted word (a restart) or replaces a **single** word.
"3 seconds, sorry 5 seconds" retracts two words (number + unit), so no growth of the edit
(`rewrite_edits.correction`) makes it a valid self-correction. The edit is then checked as
a plain deletion, it removes a protected NUMBER span, and `edit_protected` rejects it. The
reason shown is `protected_span`, but the real cause is that the correction shape is not
recognized. syn-065 fails another rule: a "." next to the cue ("postgres. No wait.")
is only allowed for restarts. Fix direction for the cleanup owner: accept "number + repeated
unit" after a bare cue (as `corrections.py` now does), and report a self-correction that
failed its shape as such instead of as `protected_span`. With this branch, those two clips
reach cleanup already resolved.

## Open

- syn-065 still fails: the ASR writes "Use postgres. No wait sequel light", with a sentence end
  before the cue, "postgres" in lower case and SQLite not recognized (class 2). Correctly
  punctuated, "use Postgres, no wait, SQLite for the cache" resolves.
- Under load the server skips the anchor (`IF_CHEAP` + `SKIPPED_BUDGET`). Without it,
  canonicalization gets beam confidences (< 0.5 on a quarter of non-hit words, ~0.1 on boosted
  words) and most identifiers are not written. That is the no-gate result, silently, on a busy server.
  Worth a look by whoever owns the server budget or the confidence hand-off.

## Unreviewed finals that look correct (after run)

- syn-066: `Set the timeout to 5 seconds.`
- syn-072: `Make the limit 20 megabytes.`
- syn-081: `In the Redis config set maxmemory-policy to allkeys-lru and don't enable persistence.`
