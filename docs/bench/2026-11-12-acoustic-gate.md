# Bench: client acoustic anchor gate (2026-11-12)

> **TTS caveat.** Every clip is macOS `say` (2 voices, 28 sentences, 56 clips, 426 reference words). The speech is
> clean and synthetic. The numbers show what the gate does and what it costs; they are **not** accuracy claims. One
> threshold (`confident_anchor = 0.97`) was set after looking at these clips' anchor confidences (below), so it is
> tuned on the test set. Re-run on real recordings (dataset phase 0) before trusting the defaults.

## Result in one paragraph

`hark_client/gate.py` compares the biased result with the unbiased anchor of the same audio (both already in every
`FinalResult` from `parakeet_mlx`) and keeps a differing span only if it is a bias hit that the anchor heard as
something close, with no negation, number, inflection-only, dropped-function-word or out-of-hit change. On an
adversarial set (sentences with the terms and the right lists; sentences without them and lists of near-homophones
only) it removes **every** bias-induced false substitution at all three boost levels: 1 → 0, 7 → 0 and 42 → 0
(98.6 → 0 per 1k words at the most aggressive setting), and keeps distractor-clip WER at the anchor's 0.020. On the
target clips it costs some recall against an idealized "biased" pipeline (36 → 35, 40 → 37 and 37 → 32 term
occurrences, canonical or alias, out of 42), mainly the true homophone "rough"/"ruff". Against the shipped
pipeline, gated output is better everywhere (overall WER 0.077 → 0.047 at the default boost), but most of that
comes from a side effect: boosted beam words have word confidence ~0.1, so canonicalization (min 0.5) never fired on
them; the gate hands the anchor's confidence to the spans it accepts. Cost: p50 0.02 ms, max 0.15 ms per utterance.

## Setup

| | |
|---|---|
| Host | MacBook, Apple M5 Pro, macOS 26.5.1; other agents were running (load average 6–9). |
| Server | own `hark-server --backend parakeet_mlx --listen 127.0.0.1:50081`, `mlx-community/parakeet-tdt-0.6b-v3`, beam 4. Three boost ranges via `[backend.options]`: **default** `boost_min = 1, boost_max = 3` (shipped), **strong** 1.67–5, **extreme** 3–9. |
| Versions | Python 3.12.15, parakeet-mlx 0.7.0, mlx 0.32.4 |
| Client | `tools/hark-probe --fast --words` (anchor `IF_CHEAP`, which is free on this backend), through [`acoustic-gate/run_bench.py`](acoustic-gate/run_bench.py) `collect`. `score` then replays the client offline on the stored `FinalResult`s: gate on/off → `context.canonicalize` with the same list. This is exactly the code path of `pipeline.Utterance` after `FinalResult`. |
| Clips | [`acoustic-gate/clips.json`](acoustic-gate/clips.json), voices Samantha and Daniel. **12 target** sentences with their own lists (1–3 terms, e.g. `kubectl`, `parse_config`, `hark`, `NeMo`, `Tauri`, `ruff`). **16 distractor** sentences that say none of the terms, each with a list of near-homophones only: "parse the config" + `parse_config`, "hard" + `hark`, "my heart is not in it" + `hark`, "Finding Nemo" + `NeMo`, "rough draft" + `ruff`, "two more minutes" + `Tauri`, "four seconds" + `Fortran`, "a bite" + `Vite`, "use the state" + `useState`, "parsed the arguments" + `parse_args`, "pie chart" + `PyTorch`, "will not notice" + `Notion`, "the cube on the control panel" + `kubectl`, "fetch the rows" + `fetch_rows`, "no demo" + `Nodemon`, "a pie, test it" + `pytest`. |
| Systems | **anchor**: unbiased decode, canonicalized with the list (post hoc only). **biased**: gate off, today's pipeline. **gated**: gate on (defaults `max_distance 0.5`, `confident_anchor 0.97`). **canon w/o conf**: the same, canonicalized without word confidences. That removes canonicalization's confidence check and isolates the gate's text decisions. |
| Metrics | **WER** on lower-cased words with edge punctuation stripped, against the text the user wants (`parse_config`, `useSessionStore`). **Term recall** over the 42 term occurrences in target refs: *canonical* = written exactly as the canonical (case-sensitive); *canonical or alias* = the term's canonical or a spoken alias, any case (did the recognizer get the term at all). **False substitutions**: list-term occurrences (canonical or alias, any case) in the output beyond those in the ref, over all 56 clips, per 1k reference words. A casing-only change ("Nemo" → "NeMo") does not count. |

## Results

### Default boost (1–3, shipped)

| system | WER all | WER target | WER distractor | recall canonical | recall canonical or alias | false substitutions (per 1k words) |
|---|---|---|---|---|---|---|
| anchor | 0.085 | 0.172 | 0.020 | 19/42 | 23/42 | 0 (0.0) |
| biased (gate off) | 0.077 | 0.161 | 0.016 | 19/42 | 36/42 | 1 (2.3) |
| **gated** | **0.047** | 0.083 | 0.020 | 28/42 | 35/42 | **0 (0.0)** |
| biased, canon w/o conf | 0.042 | 0.067 | 0.024 | 28/42 | 36/42 | 1 (2.3) |
| gated, canon w/o conf | 0.047 | 0.083 | 0.020 | 28/42 | 35/42 | 0 (0.0) |

Decisions: 12 kept (`close`), 2 reverted `outside_term`, 1 `not_close`.

### Strong boost (1.67–5)

| system | WER all | WER target | WER distractor | recall canonical | recall canonical or alias | false substitutions (per 1k words) |
|---|---|---|---|---|---|---|
| anchor | 0.085 | 0.172 | 0.020 | 19/42 | 23/42 | 0 (0.0) |
| biased (gate off) | 0.089 | 0.133 | 0.057 | 23/42 | 40/42 | 7 (16.4) |
| **gated** | **0.035** | 0.056 | 0.020 | 30/42 | 37/42 | **0 (0.0)** |
| biased, canon w/o conf | 0.059 | 0.028 | 0.081 | 33/42 | 40/42 | 7 (16.4) |
| gated, canon w/o conf | 0.035 | 0.056 | 0.020 | 30/42 | 37/42 | 0 (0.0) |

Decisions: 14 kept, 4 `not_close`, 4 `word_dropped`, 1 `outside_term`.

### Extreme boost (3–9)

| system | WER all | WER target | WER distractor | recall canonical | recall canonical or alias | false substitutions (per 1k words) |
|---|---|---|---|---|---|---|
| anchor | 0.085 | 0.172 | 0.020 | 19/42 | 23/42 | 0 (0.0) |
| biased (gate off) | 0.176 | 0.283 | 0.098 | 20/42 | 37/42 | 42 (98.6) |
| **gated** | **0.054** | 0.100 | 0.020 | 27/42 | 32/42 | **0 (0.0)** |
| biased, canon w/o conf | 0.160 | 0.167 | 0.154 | 32/42 | 37/42 | 42 (98.6) |
| gated, canon w/o conf | 0.054 | 0.100 | 0.020 | 27/42 | 32/42 | 0 (0.0) |

Decisions: 9 kept; reverted 12 `word_dropped`, 11 `not_close`, 4 `outside_term`, 4 `anchor_confident`,
3 `in_anchor`, 2 `inserted`, 1 `negation`.

### Thresholds (gated, all clips)

| setting | default boost: recall (c/c+a), false | strong | extreme |
|---|---|---|---|
| `max_distance 0.35` | 28 / 34, 0 | 30 / 36, 0 | 27 / 31, 0 |
| **`max_distance 0.5`** | 28 / 35, 0 | 30 / 37, 0 | 27 / 32, 0 |
| `max_distance 0.65` | 28 / 35, 0 | 30 / 37, 0 | 28 / 34, **1** |
| `confident_anchor` off | same as 0.5 | same as 0.5 | 27 / 32, **4** (WER 0.063) |

WER moved only in the `confident_anchor`-off row. 0.5 is the largest `max_distance` without a false substitution
here (0.65 lets one through at the extreme boost). The confidence rule matters only at the extreme boost, where it
is the only rule that stops "hard" → "hark" and "bite" → "vite" (distance 0.25 each).

## What the gate did

Kept at the default and strong boost: "cubectal"/"Qbectel" → `kubectl`, "pass config" → "parse config", "pars
args" → "parse args", "you session store" → "use session store", "fetch rose" → "fetch rows", "Tori" → `Tauri`,
"V." → "Veet", "Hipperland" → "Hyperland", "Harkon" → "Hark on" (a re-split: the other biased word only
re-splits the anchor's letters), "PyVest" → "py test", "Heart" → "hark" (anchor confidence 0.83), and at the strong
boost "Runba said pi right" → "Run based pyright". At the extreme boost the first seven survive; the rest come out
of the beam mangled and are reverted.

Blocked:

| reason | examples (anchor → biased) |
|---|---|
| `word_dropped` | "Parse the config" → "Parse config", "Use the state" → "Use state", "Fetch the rose" → "Fetch rows", "cube on the control" → "cube control", "Train the model with" → "Nemo nemo" |
| `not_close` | "rough" → "ruff", "Build" → "Veet", "cube" → "kubectl", "Heart Doctor" → "hark hark" |
| `anchor_confident` | "hard" (1.0) → "hark", "bite" (1.0) → "vite" |
| `negation` | "heart is not" → "hark hark hark hark hark hark" |
| `inserted` | "" → "ruff ruff" (beam appended terms after the speech) |
| `in_anchor` | "PyTorch," → "pie torch," (the anchor already had the term: keep the anchor's words) |
| `outside_term` | "Pause" → "Parse" (no hit), "4" → "four", a dropped "to" |
| (no region) | "pie, test it," → "pie test it": same words, so the anchor's punctuation stays. At the default boost the beam dropped the commas, which made the `pytest` alias "pie test" appear; that is the one false substitution of "biased" there. |

Lost (better biased spans reverted on target clips): "rough" → "ruff" (all levels; true homophones, letters 0.6
apart, and the phonetic key does not map "gh" to "f"), "by said parite" → "based pyright" (strong, `word_dropped`
because "by" is in the function-word list) and "Runba said pi right" → "Run based piright" (default, no hit since
"piright" is not a form).

The `confident_anchor` value: the lowest anchor confidence in a region was 0.67–0.94 for every correct replacement
here, and 1.0 for "hard" and "bite". 0.97 sits in that gap, which is why it is tuned on this set.

## Findings outside the gate

- **Canonicalization never fires on boosted words today.** parakeet_mlx beam word confidences on boosted words are
  0.06–0.11 (e.g. "kubectl" 0.11, "parse" 0.09, "use" 0.06), so `canonicalize` (min 0.5) skips them. That is why
  "biased" has the same canonical recall as the anchor at the default boost (19/42) although it recognizes 36/42.
  The gate passes the anchor's lowest confidence of the region on to the words it keeps. A confidence for the
  boosted span would be better computed on the server.
- At the **default boost**, biasing alone caused only one false substitution here (a comma drop, see above). The
  gate's protection is insurance there, and it becomes essential as soon as the boost is raised.
- The model itself writes "Finding NeMo" in the anchor (casing only, not counted). Single-word casing rewrites are
  canonicalization's business (`context/canon.py`), not the gate's.

## Protocol

No server or proto change was needed. The anchor is a full `Hypothesis` with word timings and confidences, and
`BiasHit` byte spans locate the hits in `n_best[0]`. The gate aligns the two word sequences itself: token Levenshtein
via difflib, then each hit widened to whole replaced runs. The client now asks for the anchor explicitly
(`ResultOptions.anchor = IF_CHEAP`, the server default before). On backends where the anchor is `FULL_PASS` there is
no anchor, and the gate passes the biased text through (`history.gate.status = "no_anchor"`).

## Limitations

- TTS, 56 clips, one model; the target lists are 1–3 terms. Real shortlists are larger, which means more
  near-homophone competition (research §3.3, R5).
- `confident_anchor` is tuned on these clips, and confidences are uncalibrated (ADR-001).
- The function-word rule is a short English list. Negations and number words are English only.
- The gate cannot fix spans the biased decode did not mark as hits. These are reverted (`outside_term`), even when
  the bias improved them ("Pause" → "Parse").

## Reproduce

```sh
B=docs/bench/acoustic-gate
uv run python $B/run_bench.py make-clips /tmp/hark-gate/clips                   # say + afconvert, 56 WAVs
uv run hark-server --backend parakeet_mlx --listen 127.0.0.1:50081 &             # wait for "model ready"
uv run python $B/run_bench.py collect /tmp/hark-gate/clips --server 127.0.0.1:50081 --tag default
# strong / extreme: a config with [backend] kind = "parakeet_mlx" and [backend.options] boost_min/boost_max
# (1.67/5, 3/9), `hark-server --config that.toml --listen 127.0.0.1:50081`, then collect --tag strong|extreme
uv run python $B/run_bench.py score /tmp/hark-gate/clips --tag default -v       # tables; per-clip texts + decisions on stderr
uv run python $B/run_bench.py score /tmp/hark-gate/clips --tag extreme --max-distance 0.65 --confident-anchor 2
```
