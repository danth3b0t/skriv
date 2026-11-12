# Bench: acoustic options for the recognition errors no context covers (2026-11-13, w3-recognition)

Workstream for triage section E of [the merged eval](2026-11-13-eval-merged.md): 34 clips fail or are rejected
because recognition misses a word that no context covers. This note measures the acoustic options we already have or
can get cheaply on the whole eval/dev-v1 set (122 clips) and separately on its 26 real-speech clips: parakeet-tdt-0.6b-v3
vs the English-only v2 that the server can already route to, the boost range and beam settings in `[backend.options]`,
what the anchor costs, and whisper large-v3-turbo as an upper bound on what better acoustics would buy.

**Result: no option is a clear net win, so no server default or config changes.** The defaults (v3, `beam_size=4`,
`patience=20`, `boost_min=1`, `boost_max=3`) have the fewest failed clips of every server setting measured. The only
clean gain is client-side: give `git` a stronger boost (weight 0.65 instead of `TERMINAL_WEIGHT` 0.3, which is a boost of
2.3 instead of 1.6). That fixes syn-005 `git status`, makes no false substitution, and does not touch real speech (20
failed instead of 21). `TERMINAL_WEIGHT` is in `client/src/hark_client/context/words.py`, outside this workstream, so it
is handed over below rather than changed here. "Before" and "after" are therefore the same run:
`v3-default` = `/tmp/w3-recognition-before.json` (21 failed, the merged eval's numbers, reproduced exactly).

## Setup

- Branch `w3-recognition` at `ae2fdbb`. Server: `hark-server --backend parakeet_mlx` from this worktree on
  127.0.0.1:50064, restarted per option with `[backend.options]` ([`sweep.sh`](w3-recognition/sweep.sh)). Cleanup
  used the shared S1-mini on :8080. Client: `docs/bench/eval-baseline/baseline.toml` (every default) unless the row
  says otherwise.
- [`run.py`](w3-recognition/run.py) is `hark eval run` plus each clip's server `Timings.backend_ms`, model id and anchor
  text in the report. It also has env overrides for the per-term weight probes. [`compare.py`](w3-recognition/compare.py)
  makes the tables below. The critical-recall and latency columns come from `eval-baseline/analyze.py --reports
  docs/bench/w3-recognition`.
- Runs are deterministic. A second run of the default server, and the `patience` runs, gave byte-identical raw and
  final texts. The client-side latencies (key-up → final) include the shared llama-server, which other agents were also
  using: the same default ran at 346 ms p50 once and at 260–275 ms in later runs. Compare server-side
  `backend_ms` (greedy, beam) instead.
- "Accepted" counts only texts that someone reviewed on earlier v3 runs, so a new correct text from another option
  counts as unreviewed, not as accepted. Compare options on failed, WER and critical recall. Clips that look correct
  but are unreviewed are listed at the end.
- Columns: **failed / accepted** = the mechanical check (critical string missing, forbidden string present, or a
  negation lost without an accepted text). **WER** = recognition WER against `said`, mean, capped at 100% per clip.
  **E clips** = the 34 section E clips. **E targets in raw** = how many of the 22 synthetic E words appear in the
  recognized text (regex list in `compare.py`: `git`, `main`, `make`, `httpx`, `zod`, `tauri`, `cuda`, …).
  **false subs** = forbidden strings (context terms) in the final text of trap and real clips, as in `analyze.py`
  (n = 29). **critical recall** = critical strings in the final text (121).

## 1. Model: parakeet-tdt-0.6b-v3 vs the English-only v2

When is v2 used? The server loads v2 next to a multilingual model as its companion (`english_model`, default
`mlx-community/parakeet-tdt-0.6b-v2`, loaded on first use). `AsrCore.route` gives a session the model with the
fewest languages that covers `SessionStart.languages`. So v2 serves exactly the sessions whose client sets
`[server] language = "en"`. The client's default `language = ""` sends no languages, so every default dictation goes to
v3. The language fallback (`session._language_fallback`: decode again with v2 when v3's text is implausible) needs
`"en"` among several allowed languages. The client only ever sends one language (`[cfg.server.language]`), and a
session with `["en"]` is already on v2. **So with today's client the fallback never fires.** That is a fact for the
client owner, not a change made here.

| option | failed / accepted | real failed / accepted (26) | WER all | WER real | WER E clips | E targets in raw | false subs | critical recall final | negations lost | greedy ms p50 / p95 | beam ms p50 / p95 | key-up → final p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **v3 (default)** | **21** / 73 | 1 / 13 | 14.0% | **14.2%** | 28.0% | 4/22 | 0 | **79%** | 1 | 40 / 94 | 178 / 424 | 346 / 715 (later runs 260–275 / ~480) |
| v2 (`language = "en"`) | 27 / 48 | 0 / 4 | 14.0% | 14.7% | 26.0% | 6/22 | 0 | 74% | 0 | 28 / 56 | **108 / 196** | **187 / 342** |
| v3, no context | 58 / 45 | 1 / 12 | 15.9% | 15.1% | 29.8% | 2/22 | 0 | 31% | 1 | 34 / 70 | – | 79 / 134 |
| v2, no context | 60 / 31 | 1 / 5 | 16.2% | 14.6% | 25.9% | 6/22 | 0 | 31% | 1 | 34 / 67 | – | 94 / 168 |

v2 hears several section E words that v3 misses: syn-012 "never push directly to **main**" (v3: "me", which reverses
the target branch), syn-003 "looks good, ship it", syn-051 "3.12, 3.13", syn-063 "bar". It also keeps "never" in
real-017 and drops the false "No," in real-013. It still writes "Get status", "PUDA", "audit" (zod) and "Indo we"
(tauri). Its losses are larger, though: `git push` → "Give push" (syn-041 FAIL), `kube`/`kubectl` → "cube" (syn-010,
syn-046 FAIL), "no" in a self-correction → "nerve" (syn-070 FAIL: `TypeScript` stays), "app id" → "ACID",
"hark" → "heart", `hark ctl paste` → "HARK Control-Paste" (syn-077 FAIL), and worse real-speech WER (14.7% vs 14.2%;
real-006 +19 points, real-008 opens with "Matthew, Cavasta"). Critical recall drops from 79% to 74%.
**Answer: English dictation should not default to v2.** Two side findings: v2's biased decode is clearly faster
(beam 108 vs ~140–180 ms p50, because its 1024-token vocabulary makes booster tables cheaper), and it is the better
model for some everyday words. A per-clip choice between the two models would need a signal we do not have.

## 2. Boost range (`boost_min`, `boost_max`)

A term's boost is `boost_min + w · (boost_max − boost_min)` (strength 1; the client never sets it). Context terms have
w ≈ 0.3–0.9. The terminal base commands (`git`, `uv`, `kubectl`, …) have w = 0.3, which is 1.6 at the defaults.

| boost_min–max | failed / accepted | real failed / accepted | WER all | WER real | WER E clips | E targets | false subs | critical recall final | beam ms p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|
| 1–2 | 26 / 68 | 1 / 12 | 14.1% | 14.3% | 27.9% | 3/22 | 0 | 73% | 159 / 344 |
| 1.5–3 | 22 / 70 | 1 / 12 | 14.4% | 14.7% | 28.0% | 4/22 | 0 | 77% | 140 / 287 |
| **1–3 (default)** | **21** / 73 | 1 / 13 | 14.0% | 14.2% | 28.0% | 4/22 | 0 | 79% | 178 / 424 |
| 2–3 | **21** / 71 | 1 / 13 | 13.6% | 14.0% | 25.8% | 6/22 | 0 | 79% | 154 / 329 |
| 2–4 | 22 / 68 | **2** / 12 | **13.3%** | 14.4% | 25.6% | 7/22 | **1** | 79% | 183 / 346 |
| 1–5 | 23 / 66 | **2** / 11 | 13.6% | 13.7% | 27.0% | 6/22 | **1** | 78% | 148 / 313 |
| 2–5 | 23 / 64 | 1 / 11 | 13.7% | 14.5% | 25.2% | 7/22 | **1** | 78% | 185 / 384 |
| 3–5 | 25 / 59 | **2** / 9 | 14.6% | 15.7% | 27.0% | 6/22 | **1** | 77% | 196 / 381 |

- Weaker (1–2) loses identifiers: 5 more FAILs, recall 73%.
- Every range with `boost_max > 3` puts the context term "hark" into real-006 ("and I **hark** to strive hard":
  accepted → FAIL, a false substitution in real speech). That confirms the clamp at 3 from the boost bench.
- **2–3** is the closest call. It raises only the weakly weighted terms and leaves max at 3. Failed stays at 21, WER goes
  14.0 → 13.6%, real 14.2 → 14.0%, E clips 28.0 → 25.8%, with no false substitution. But it fixes 2 clips (syn-005
  `git status`, syn-078 `hark ctl toggle`, which becomes accepted) and breaks 2 (syn-014 "use effect hook**UM**", which
  loses `useEffect`; syn-038 "uv-sync", which loses `uv sync`). syn-045 gets worse (C-HMOD), and syn-065's accepted
  "cache" becomes "cash". On real speech, no clip changes state and the text changes are noise (real-020 "riding" →
  "to ride", real-017 still loses "never"). Net zero on the check, so this is not the clear win the brief asks for.

## 3. Beam search (`beam_size`, `patience`)

| option | failed / accepted | real failed / accepted | WER all | WER real | WER E clips | E targets | false subs | critical recall final | negations lost | beam ms p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| beam 1 | 28 / 63 | 1 / 10 | 14.7% | 14.7% | 29.5% | 4/22 | 0 | 73% | 1 | 43 / 97 |
| beam 2 | 25 / 63 | 1 / 10 | 14.2% | 14.8% | 28.1% | 4/22 | 0 | 75% | 1 | 119 / 216 |
| **beam 4 (default)** | **21** / 73 | 1 / 13 | 14.0% | 14.2% | 28.0% | 4/22 | 0 | **79%** | 1 | 178 / 424 (later runs 131–147 / ~290) |
| beam 8 | 23 / 64 | **0** / 10 | 13.7% | 13.7% | 27.1% | 5/22 | 0 | 74% | **0** | 291 / 630 |
| beam 4, patience 5 | 21 / 73 | 1 / 13 | 14.0% | 14.2% | 28.0% | 4/22 | 0 | 79% | 1 | 131 / 289 |
| beam 4, patience 40 | 21 / 73 | 1 / 13 | 14.0% | 14.2% | 28.0% | 4/22 | 0 | 79% | 1 | 147 / 293 |

Beam 8 is the interesting one for real speech: real-017 keeps "I **never** watch it", which removes the set's only
lost negation, and real WER drops to 13.7%. It also fixes syn-041 (`push`), syn-046 (`kube config`) and syn-008 (`empty`).
But it doubles beam time (291 ms p50, 630 p95) and loses identifiers elsewhere (syn-047 `-d`, syn-038, syn-081 `redis`:
critical recall 79 → 74%, 23 FAIL). Patience 5 and 40 give the same texts as 20 on every clip, so the beam search
ends on its own before the patience limit at this beam size. Patience 5 would not change any text, but the latency
difference (131 vs 147 ms p50) is within run-to-run noise, so it is left alone.

## 4. How much boost wins "git" over "Get"

Probe: the same default server, with only the `git` BiasTerm's weight changed by the client (`W3_TERM_WEIGHT=git=w`),
so boost = 1 + 2w. Two clips say `git`: syn-005 "git status" and syn-041 "git push…". In syn-041 the biased decode
already writes "Git", and the gate reverts it to the anchor's "Get" (`anchor_confident`, section A), so recognition
cannot help there.

| git weight → boost | failed | WER all | syn-005 raw | other clips changed | spurious "git" |
|---|---|---|---|---|---|
| 0.3 → 1.6 (today) | 21 | 14.0% | Get status. | – | 0 |
| 0.5 → 2.0 | 21 | 13.9% | Get status. | syn-051 better, syn-073 "app id" → "agit" | 0 |
| **0.65 → 2.3** | **20** | **13.5%** | **Git status.** → final `git status.` | same two | **0** |
| 0.8 → 2.6 | 20 | 13.5% | Git status. | same two | 0 |
| 1.0 → 3.0 | 21 | 13.6% | Git status. | + syn-049 | **2**: syn-010 "Cube control **git** pods" (accepted → FAIL), real-002 "banned as a **git**" |
| all terminal commands 0.65 | 20 | 14.0% | Git status. | syn-045 worse, syn-065 "cache" → "cash", real-020 | 0 |

`git` wins from a boost of about 2.3. At 3.0 it starts to replace "get" ("kubectl get pods") and words in real
speech. So **`git` at weight 0.65 (boost 2.3) is a clean win**: syn-005 FAIL → correct, 0 false substitutions, no
real clip touched. The only other text change in that run, syn-073's "app id" → "agit", happens at 0.5 too, and that
clip is still rejected. Raising every terminal command to 0.65 is no better than raising `git` alone. **Handover
(client, `context/words.py`)**: a per-command weight with `git` at 0.65 and the rest at 0.3. That is a client change, so
measure it there. It covers syn-005. syn-041 also needs the gate fix from section A.

## 5. What the anchor costs

With context, `parakeet_mlx` always runs the greedy decode first. It is the deletion guard's reference and also the
anchor, which the session gets for free (`ANCHOR_COST_FREE`, reused encoder output). Its share of the server's decode
is **34–46 ms p50, 70–94 ms p95** across all v3 runs, against 131–180 ms p50 for the boosted beam. Requesting the anchor
(`ANCHOR_MODE_IF_CHEAP`, the client default) adds no decode time on top of that. Turning it off would save nothing
unless the deletion guard went too, and the guard is what catches beams that skip speech. Without context, the decode
is greedy only and anchor == primary. Client side, the gate takes 0.1 ms. So the anchor costs one greedy pass, about
20% of a biased decode, and that pass is spent anyway.

## 6. Quality reference: whisper large-v3-turbo (whisper.cpp)

`whisper-cli` (Homebrew) with Superwhisper's `ggml-large-v3-turbo.bin`, `-l en`, beam 5, Metal, no context; then
with the clip's shortlist (first 30 canonicals) as `--prompt`, which is Whisper's only way to take context.
[`whisper_ref.py`](w3-recognition/whisper_ref.py). This is raw recognition only, without our pipeline. Whisper
removes fillers and repetitions and writes symbols, and `said` is verbatim, so the table also gives WER with fillers
removed from both texts, and critical strings in the *raw* text.

| recognizer | WER all (filler-free) | WER real (filler-free) | WER E clips | E targets in raw | critical in raw | model ms p50 / p95 |
|---|---|---|---|---|---|---|
| parakeet v3 greedy, no context | 15.9% (15.8%) | 15.1% (15.0%) | 29.8% | 2/22 | 24/121 | ~35 / ~70 (greedy) |
| parakeet v3, biased (default) | 14.0% (13.8%) | 14.2% (13.9%) | 28.0% | 4/22 | 29/121 | ~180 / ~420 (greedy + beam) |
| whisper large-v3-turbo | 17.8% (17.4%) | 20.8% (19.6%) | 30.7% | 8/22 | 34/121 | 250 / 308 |
| whisper large-v3-turbo + shortlist prompt | 20.1% (19.8%) | 19.7% (18.8%) | 31.8% | 8/22 | 70/121 | 264 / 383 |

What better acoustics would buy is small. Whisper hears "make" (syn-016), "looks good, ship it",
"bar", "3.12, 3.13" and "pnpm". But it also writes "Get status", "Maine" for `main`, "audit" for zod, "tovi" for
tauri, "Poudre" for CUDA, and "http.asic" for httpx. It drops real speech: real-008's first clause, real-013's first
clause, "zero comma one" in syn-082. With the prompt it hallucinates on accented speech (real-017: "Nere ba ba
chitah?"). Its WER is worse on real speech even after removing fillers, and it is 7× slower than parakeet greedy. Its
critical recall with a prompt (70/121 in raw text) is about what our pipeline gets after canonicalization (69%). So
the shortlist is doing the work Whisper's prompt would do. **Not integrated**: not a clear win and not fast. The
section E words that remain (`git`, `main`, zod, tauri, CUDA, httpx, spelled pnpm) are hard for all three models.
They need context, which the next fixes in section E already name (`main` in a git sentence, CUDA in an ML repo), not
a bigger acoustic model.

## Section E, per option (what moved)

- syn-005 `git`: fixed by `git` boost ≥ 2.3 (section 4) or `boost_min ≥ 2`. No model or beam fixes it.
- syn-041 `git push`: recognition is already right ("Git push"); the gate reverts it (section A). Beam 8 also fixes
  "bush" → "push".
- syn-012 `main`: only v2 hears it. syn-003, syn-051, syn-063: v2 and whisper.
- syn-016 `make`: whisper only. syn-046 `kube`: beam 8 only (v2 makes it worse). syn-049 tauri: `boost_max ≥ 4`
  (2–4: `tauri.conf.json` correct), which also brings the real-006 false substitution.
- syn-032 httpx, syn-042 zod, syn-056 CUDA, syn-079 `dir`/`tmp`, syn-069 fallback, syn-090 offsite: no option.
- syn-080 spelled pnpm and syn-074 flags in one breath: no option produces the critical strings (whisper gets
  "pnpm" and "vitest.config.ts", not `-D vitest`).
- Real speech: the "you know" → "No," clips (real-003, -009, -013) keep "No," on v3 at every setting. v2 drops it only
  in real-013. real-017 keeps "never" with v2 and beam 8. real-008/-011 lost first clauses: no option recovers them
  (whisper loses more).

## Clips with new final texts that look correct but are unreviewed

None of these come from a default run, since no default changed. They are listed so they can be accepted in one pass
if a later branch adopts the option.

- syn-005 (`git` weight 0.65, boost 2–3 or 2–4): `git status.`
- syn-012 (v2): `Never push directly to main.`
- syn-003 (v2): `Looks good, ship it.`
- syn-021 (v2): `Check whether smart_spacing is off when check_focus is false.`
- syn-046 (beam 8): `Use the kube config from ~/.kube/config.`
- syn-049 (boost 2–4): `In tauri.conf.json, set the window to not be resizable.`

## Reproduce

```sh
# server options per row: docs/bench/w3-recognition/sweep.sh NAME MODEL "OPTIONS" CLIENT_CONFIG...
docs/bench/w3-recognition/sweep.sh v3-default "" "" docs/bench/eval-baseline/baseline.toml \
  docs/bench/eval-baseline/no-context.toml docs/bench/w3-recognition/lang-en-no-context.toml
docs/bench/w3-recognition/sweep.sh boost-2-3 "" "boost_min = 2.0" docs/bench/eval-baseline/baseline.toml
docs/bench/w3-recognition/sweep.sh beam-8 "" "beam_size = 8" docs/bench/eval-baseline/baseline.toml
# v2: default server, client config lang-en.toml (output named v2-lang-en)
# git boost: default server, W3_TERM_WEIGHT=git=0.65 uv run python docs/bench/w3-recognition/run.py \
#   --config docs/bench/eval-baseline/baseline.toml --server 127.0.0.1:50064 eval run --set eval/dev-v1 --report ...
uv run python docs/bench/w3-recognition/compare.py --diff v3-default boost-2-3 v2-lang-en   # tables + changed clips
uv run python docs/bench/eval-baseline/analyze.py --reports docs/bench/w3-recognition v3-default boost-2-3
uv run python docs/bench/w3-recognition/whisper_ref.py ".../ggml-large-v3-turbo.bin" OUT.json [--prompt]
```

Reports and printed output for every row are in [`w3-recognition/`](w3-recognition/). The second default run is only
`v3-default-rerun.txt`; its texts are identical to `v3-default`.
