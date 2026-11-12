# Offline context-ranking experiment

## Decision in brief

**Best development-selected configuration: `tune_n8_d0.9`, K=40.** Use eight previous response
turns, 0.9 per-turn decay, broader name extraction, previous user messages, and a modest
per-repo plus per-user vocabulary learned only from already-ended sessions. Keep tool
paths and recent repo vocabulary available, but do not give them dominant weights.

On held-out short main-session prompts, macro recall rises from **5.4%
(current-engine replay) to 25.9%**, a
**20.4-percentage-point** gain.
Micro recall is 27.3%, or **41 of 150 gold occurrences**. Removing
current repo-state inputs leaves 25.1% macro recall: the gain is mostly temporal vocabulary,
not current-code leakage. The strict identifier/path/flag subset improves less; much of the
benefit is names and acronyms.

**Do not interpret this as ASR accuracy or proof that 40 boosted terms are safe.** Only
1.0% of shortlist entries occur in the next prompt; on average
39.59 of 40 are distractors. K=40 is a *retrieval experiment cap*,
not a minimum fill requirement or a validated acoustic boost setting. Preserve an unbiased
ASR anchor and test false substitutions with recorded speech before deployment.

## Corpus, privacy, and replay

Read-only local inputs: Hark README, `docs/00-context.md`, and
`client/src/hark_client/context/{aliases,rank,agents,repo,canon,engine}.py`; Pi main/subagent,
Claude project, and Codex session JSONL files. No network calls, model calls, external
services, session modification, server interaction, or production code changes were used.
Git calls use `--no-optional-locks`. All experiment writes are in this directory.

The final scan counted 93 Pi-main, 414
Pi-subagent, 304 Claude, and 211 Codex
files. **726 sessions and 2,065 distinct eligible target records** remain. The raw user-role
record count is 5,099; 778 scaffold/inherited records and 2,194 exact cross-session duplicate
targets were excluded, alongside empty text and this experiment's own session. These are
pipeline counters, not a mutually exclusive conservation table. The target-time cutoff is
saved in `cutoff.json`; later appended records are ignored. File counts may grow on rerun
as live agents create empty/newer files. No raw sessions, per-target rows, identifiers,
repo names, private paths, vocabulary caches, or raw audit samples are persisted.

| Partition | Primary sessions | Short main prompts | Gold-bearing short prompts | Gold occurrences | Long/code prompts | Subagent prompts |
| --- | --- | --- | --- | --- | --- | --- |
| train | 77 | 891 | 312 | 683 | 165 | 320 |
| dev | 25 | 206 | 88 | 177 | 44 | 98 |
| test | 25 | 217 | 65 | 150 | 101 | 23 |

Primary prompts are non-subagent user messages with at most 2,000 characters and no fenced
code block, after cleaning known metadata. They are *dictation-like*, not proven dictation:
short pasted logs, delegated instructions, UI payloads, and code can still survive.
Long/code-heavy messages and recognized subagent sessions are measured separately, not
silently discarded. Single-user data cannot establish multi-user generalization.

### Session parsing and time controls

- Pi: `message.role`, text/toolCall blocks, `id/parentId` ancestry. Claude:
  `uuid/parentUuid` ancestry, text/tool_use blocks; tool_result user records are not human
  targets. Sidechain files are auxiliary. Codex: response_item messages and function/custom
  calls, session metadata cwd; response records only, avoiding event_msg duplicates.
- A target's context starts at its parent, never the target itself. Pi/Claude sibling
  branches are excluded. Adjacent assistant text/tool blocks between user prompts form a
  response turn; tool results and reasoning blocks are not candidate text. No arbitrary
  shell-command path inference is attempted. Explicit path/file_path/notebook_path
  arguments and patch headers supply tool paths. Nested JSON arguments are supported.
- Each assistant turn contributes its **last 1,500 characters**, including for expanded
  extraction. Previous user turns contribute up to 6,000 characters. Up to eight prior
  user-response groups are retained; empty response groups still consume an age.
- Cwd comes from recorded session/entry metadata. Recorded branch names are used when
  present (chiefly Claude); current repo branch is an explicitly leaky fallback.
- Persistent vocabulary consumes only sessions with **end < current session start**, not
  merely an earlier filename/start time. It never learns from future turns of the current
  session. Main-session user text teaches vocabulary; recognized subagent sessions do not.
  This is stricter than online per-message updating. Earlier held-out sessions may teach
  later held-out sessions: this is a prequential personalization evaluation, not frozen
  zero-shot inference. Repo scope falls back to exact cwd when the repo no longer exists.
- Exact repeated text in *different* sessions is evaluated once, to reduce fork/copied-task
  inflation. Repetitions within a session remain. Duplicated text can still be legitimate
  historical context; cross-session near-duplicates are not fully removed.

The initial global chronological split produced **no short main prompts in test**, because
later files were mostly generated/long jobs. The final split therefore partitions
eligible-main sessions chronologically 60/20/20, and auxiliary-only sessions separately
60/20/20. All targets within a session stay together. Model selection uses **development
primary targets only**; auxiliary results are diagnostic. Test primary has 25 sessions,
217 targets, only 65 gold-bearing targets: confidence is limited. 206/217 test targets are
Pi; Claude contributes two prompts with no gold, Codex nine with five gold occurrences.
No cross-provider superiority claim is justified.

### Repo approximation and baseline fidelity

**Implementation pin:** `b71af4ad4a23dc9e0d42b0112d196f2114692830` matches the
context files read at task start. Other agents committed name-extraction/history changes
during the experiment; a differential reader test caught that API drift. The final replay
loads the pinned context package directly from Git objects in memory, without checkout or
working-tree edits. All tables below come from that final pinned rerun. `current_engine`
therefore means **task-start engine**, not the newer HEAD at report delivery. Current repo
vocabulary is still scanned live. Some recommendations below may already be partially
implemented by concurrent work; reconcile against HEAD rather than applying them blindly.

Nine current repositories were available; 153/217 primary test targets have a usable repo.
Current repo state can expose files/symbols created *after* the target. It is not treated as
historical ground truth. Recent vocabulary uses tracked working changes plus the last ten
commits; production extraction scans up to 12 files and keeps 150 identifiers. Broad repo
vocabulary includes up to 5,000 tracked filenames/full paths, with symbol extraction bounded
to the first 200 shallow files and 10 MiB per repo (1,458 files actually scanned). Thus
“whole” means **bounded broad repo**, not exhaustive symbol indexing. Recent plus whole can
hurt through competition and duplicate evidence, not just add recall.

`current_agent` uses the production code-style extractor and `ranked()` on the latest
nonempty assistant response. `current_engine` additionally reproduces tool basenames/stems
and production `repo_candidates()` ranking. The real engine **already uses tool paths**;
these are not a new discovery. This offline baseline is not a live `collect()` benchmark:
foreground/session-confidence matching, provider deadlines, focus/title/selection, 4 MiB
reader tail limits, and the 400-entry chain limit are not simulated. It assumes the correct
session is known. For safety and self-contamination control, repo scanning omits untracked
files, obvious environment/dependency directories, and this research directory; recent
file ordering is not production mtime ordering. These deviations apply across strategies.

## Gold terms and manual validation

Gold extraction is implemented independently of production `extract_terms()` using lexical
patterns, the native English dictionary (234,456 case-folded entries), simple inflection
rules, a fixed small technology lexicon, and quoting/capitalization signals. It recognizes
flags, paths/extensions, snake/camel/mixed alphanumeric tokens, rare acronyms, unknown
capitalized or explicitly quoted names, and conservative backticked 2–4-word title-like
entities. Unquoted ordinary lowercase words, common English hyphenated prose, URLs/email,
known secret formats, long hex/hash IDs, one-letter-number labels, XML tag syntax, and
non-ASCII word fragments are excluded. No gold list is obtained by looking up a term in
future assistant replies or current repo contents.

This is an **ASR-risk proxy**, not a confusion-trained gold standard. Arbitrary lowercase
product names are undercounted; non-English ordinary words, inflection gaps, task labels,
short typo fragments, and copied error-log paths can be overcounted. Exact canonical
matching is deliberately strict: basename coverage does not count as retrieval of a full
path, and case variants do not count as recall hits. This penalizes useful partial context.
Expanded candidate extraction intentionally overlaps these lexical rules; it is not an
independent learned NER evaluation.

Term-only hand review of a seeded 80-occurrence sample found **70 clearly eligible terms
(87.5%), six ambiguous (7.5%), four ordinary-word/typo false positives (5%)**. Duplicates
were retained in sampling to reflect occurrence frequency. In twelve sampled zero-gold
prompt prefixes, two clearly missed an unquoted lowercase name. This does **not** estimate
extractor recall. Initial pilot samples exposed English-inflection, hash-ID, XML, and
Unicode-fragment errors, which were tightened before the final run. Audit/debugging used
pooled prompts including held-out periods; only ranking selection was strictly development
only. This is consequently exploratory held-out evidence, not a pristine sealed benchmark.
`manual_audit.json` contains aggregate judgments only.

## Metrics and selection rule

- **Macro recall@K:** mean `|shortlist ∩ gold| / |gold|` over gold-bearing targets.
  Gold-free prompts are not assigned artificial recall zero/one.
- **Micro recall@K:** total exact gold hits / total gold occurrences.
- **Precision proxy:** fraction of all emitted shortlist entries appearing in the target
  under case-insensitive, word-boundary-aware literal matching, including non-gold terms.
  It is not linguistic precision or canonicalization accuracy.
- **Distractor load:** emitted entries minus entries present in the target, averaged over
  *all* targets, including zero-gold prompts. Actual list length can be below K.
- Source attribution divides each selected term/hit equally among contributing sources;
  sources overlap, so this is descriptive, not a causal attribution. Post-selection
  source-removal ablations provide a better test of marginal contribution.
- `oracle_macro_recall` in CSV is a deliberately broad **all-input-source union ceiling**
  shared across strategies, including eight turns and broad repo. It is **not** each
  strategy's own candidate ceiling, and includes leaky repo inputs. It is 46.4% on primary
  test; missing context/extractor coverage remains a bigger problem than rank alone.

There are 35 selection-eligible variants and five subsequent selected-configuration
source ablations. Select maximum development macro recall among configurations emitting
at most **40 terms on average**; among configurations within one percentage point, prefer
fewer distractors, then higher recall, then smaller K. This budget is an explicit resource
choice, not an acoustically learned optimum. No statistical significance claim is made for
small grid differences; all K/strategy combinations are retained in `metrics.csv`.

## Main comparison

All precision/distractor numbers below include zero-gold targets. The first sequence uses
N=4 and decay=0.7 except where named; `expanded` adds names/mixed tokens/conservative quoted
phrases, `tools` adds full paths plus basenames/stems, `own_previous` adds prior user turns,
and persistent variants add earlier-session vocabulary. `current_engine` is a separate
production-style reference, not the preceding row plus one feature.

| Strategy | Dev macro R@40 | Test macro R@40 | Test micro R@40 | Precision proxy | Mean distractors |
| --- | --- | --- | --- | --- | --- |
| current_agent | 10.9% | 4.7% | 4.0% | 1.2% | 4.59 |
| current_engine | 11.7% | 5.4% | 4.7% | 0.3% | 29.60 |
| last1 | 10.9% | 4.7% | 4.0% | 1.2% | 4.34 |
| last2 | 11.1% | 5.1% | 6.7% | 1.4% | 7.40 |
| last4 | 12.7% | 5.1% | 6.7% | 0.9% | 12.40 |
| last8 | 13.1% | 5.4% | 7.3% | 0.7% | 17.86 |
| expanded | 18.8% | 12.5% | 17.3% | 1.5% | 18.76 |
| tools | 18.5% | 12.5% | 17.3% | 1.5% | 19.86 |
| repo_recent | 19.4% | 15.2% | 19.3% | 1.0% | 35.13 |
| repo_whole | 19.1% | 17.2% | 20.7% | 0.9% | 35.15 |
| repo_both | 18.5% | 13.3% | 18.0% | 0.9% | 35.17 |
| own_previous | 23.2% | 18.7% | 22.0% | 1.0% | 35.40 |
| persist_repo | 27.6% | 24.1% | 26.7% | 1.1% | 38.00 |
| persist_user | 27.8% | 20.3% | 22.7% | 0.9% | 39.63 |
| persist_both | 31.9% | 25.5% | 26.7% | 1.0% | 39.59 |
| tune_n8_d0.9 | 35.7% | 25.9% | 27.3% | 1.0% | 39.59 |

Plain code-style history length barely helps on held-out prompts. Expanded extraction is a
larger step. User-owned vocabulary matters; tool paths contribute no exact gold hits here.
Recent vs broad repo differences are inconsistent across dev/test and current-state-leaky.
Do not infer that whole-repo dumping is universally better from one test column.

### Ranking formulas and grid

| Ranking variant | Dev R@40 | Test R@40 | Test mean terms |
| --- | --- | --- | --- |
| rank_current | 22.3% | 13.4% | 40.00 |
| rank_frequency | 31.9% | 25.3% | 40.00 |
| rank_tfidf | 31.8% | 26.2% | 40.00 |
| rank_no_bonus | 30.7% | 25.5% | 40.00 |
| tune_n2_d0.4 | 27.3% | 22.6% | 40.00 |
| tune_n2_d0.7 | 30.2% | 23.9% | 40.00 |
| tune_n2_d0.9 | 30.3% | 24.1% | 40.00 |
| tune_n4_d0.4 | 30.2% | 24.1% | 40.00 |
| tune_n4_d0.7 | 31.9% | 25.5% | 40.00 |
| tune_n4_d0.9 | 32.5% | 25.6% | 40.00 |
| tune_n8_d0.4 | 29.4% | 24.2% | 40.00 |
| tune_n8_d0.7 | 30.8% | 25.5% | 40.00 |
| tune_n8_d0.9 | 35.7% | 25.9% | 40.00 |
| tune_user0.7 | 30.3% | 25.3% | 40.00 |
| tune_user1.5 | 31.9% | 26.2% | 40.00 |
| gate0.3 | 30.3% | 25.5% | 38.40 |
| gate0.5 | 30.0% | 20.8% | 30.17 |
| gate0.7 | 26.6% | 18.3% | 23.79 |

`rank_current` applies production's source weights, 0.6 relevance floor, 0.05 source bonus,
and 1.0 cap to the enlarged candidate set. It ranks substantially worse than the decaying
alternatives in this replay. `rank_frequency` increases the log-frequency coefficient to
0.5 instead of 0.15 and omits the explicit multisource bonus. `rank_no_bonus` removes just
that bonus. `rank_tfidf` multiplies the ordinary score by `0.5 + 0.5 × normalized_IDF`.
Its TF signal is decayed distinct source-turn incidence, not raw token repetition.

The English background is **500 locally installed section-1 manual pages**, stripped of
roff directive lines: 11,630 distinct words. Component IDF is
`log((501)/(1+document_frequency)) / log(501)`, averaged over identifier components.
This is an offline *technical-English* corpus, not a representative general-English corpus;
no internet corpus was downloaded. TF-IDF slightly wins on test but loses to the selected
configuration on dev; its confidence interval overlaps. Do not claim a proven TF-IDF win.

### K tradeoff for the selected strategy

| K | Dev macro recall | Test macro recall | Test micro recall | Precision proxy | Mean distractors |
| --- | --- | --- | --- | --- | --- |
| 10 | 22.1% | 14.2% | 13.3% | 1.7% | 9.83 |
| 20 | 31.4% | 18.7% | 20.0% | 1.4% | 19.72 |
| 40 | 35.7% | 25.9% | 27.3% | 1.0% | 39.59 |
| 60 | 38.7% | 27.4% | 28.7% | 0.8% | 59.54 |
| 100 | 41.7% | 32.3% | 32.0% | 0.5% | 99.47 |

**Recommend K=40 as the next offline/acoustic evaluation cap.** Moving 40→60 buys only
1.5 macro-recall points on test for about 20 additional distractors. K=100 reaches 32.3%
recall but has roughly 99.5 distractors. For deployment, allow fewer/zero terms and compare
K=10/20/40 under actual false-substitution costs; do not simply fill every request to 40.
152/217 test prompts have no extracted gold, and their K=40 lists average 39.92 distractors.
Even restricting to the 65 gold-bearing prompts yields only 3.0% lexical precision.

### Paired uncertainty and source-removal checks

2,000 seeded session-cluster bootstrap resamples of the 25 primary test sessions, recomputing
macro recall; intervals are percentile intervals on paired differences, in percentage points.
They are conditional on the development-selected configuration and do not capture extractor
error, repo-state leakage, or all model-selection uncertainty.

| Selected minus comparator | Paired 95% session-bootstrap interval (pp) |
| --- | --- |
| current_agent | [11.9, 37.7] |
| current_engine | [10.5, 36.5] |
| history_no_persist | [1.8, 22.5] |
| persist_repo | [-3.2, 8.3] |
| rank_tfidf | [-6.5, 6.4] |

The improvement over current-engine replay is substantial in this sample; fine distinctions
between good rankers are not resolved.

| Same selected parameters, remove… | Test R@40 | Test precision |
| --- | --- | --- |
| tune_n8_d0.9 | 25.9% | 1.0% |
| selected_no_repo | 25.1% | 1.0% |
| selected_no_persist | 20.5% | 1.0% |
| selected_no_user | 21.9% | 1.0% |
| selected_no_tools | 25.9% | 1.0% |
| selected_repo_persist | 24.4% | 1.0% |

`selected_no_repo` removes current repo files/symbols/branch/cwd candidates but retains
historical vocabulary scoped by recorded cwd/current repo identity. It is therefore a
no-current-content check, not a no-repo-identity check. Removing prior-session vocabulary
costs 5.4 recall points; removing prior user messages costs 4.0. Removing tool paths changes
no gold recall. Repo-only persistent vocabulary is a reasonable narrower privacy scope:
24.4% vs 25.9% with both stores, at slightly shorter lists. These ablations were added after
selection for diagnosis and were not allowed to win the development search.

### Which sources supply useful terms?

| Source | Share of 8,680 shortlist slots | Attributed gold hits (of 41) |
| --- | --- | --- |
| assistant | 36.5% | 22.42 |
| cwd | 0.5% | 1.08 |
| persist_repo | 21.5% | 8.08 |
| persist_user | 20.6% | 2.00 |
| repo_recent | 12.6% | 1.00 |
| tools | 1.7% | 0.00 |
| user | 6.6% | 6.42 |

The source-pool coverage diagnostic finds 28/150 gold occurrences somewhere in assistant
history, 24 in repo-scoped persistent vocabulary, 17 in user-wide vocabulary, 11 in previous
user messages, zero in explicit tool paths, one in recent repo candidates, and 20 in broad
repo vocabulary. Counts overlap. Eleven broad-repo hits are exclusive to that pool but
usually rank too low; current-code leakage may contribute to them. Assistant-pool coverage
here is before persistence/source bonuses; attribution above includes overlaps.

### Robustness to labels and target mix

| Strict identifier/path/flag gold | Macro R@40 | Micro R@40 |
| --- | --- | --- |
| current_engine | 9.0% | 7.3% |
| tune_n8_d0.9 | 15.1% | 12.7% |

Strict gold removes `NAME` labels, leaving only 55 test occurrences across 30 targets. The
smaller gain shows that proper-name/acronym recovery is an important part of the headline,
not merely improved ranking of existing code identifiers.

| Held-out cohort | Targets | Gold occurrences | Macro R@40 | Precision proxy |
| --- | --- | --- | --- | --- |
| primary | 217 | 150 | 25.9% | 1.0% |
| long_or_code | 101 | 22284 | 16.7% | 88.9% |
| subagent | 23 | 652 | 8.6% | 10.5% |

Long prompts contain enormous copied context: pooling them would inflate lexical precision
from 1.0% to something misleadingly high. Generated prompts are useful stress cases but
are not dictation. See `provider_metrics.csv` and `strata.csv` for first-turn/history,
repo-available/missing, zero-gold, and provider breakdowns. First-turn prompts with no
history are not excluded: there are 24 in primary test.

## Alias quality

Use the unmodified production `spoken_forms()` on all unique primary gold terms across
splits. A plausible alias must be alphabetic-space-only, preserve lexical components in
order-insensitive substring checks (allowing spelled small numbers), include “dash” for
long flags and “dot” for dotted paths, and contain at most 24 words. This is deliberately
cheap: it checks representability, **not real pronunciation, acoustic confusability,
ordering correctness, or canonicalizer eligibility**. Acronyms can be letter-spaced.

| Gold kind | Unique terms | Any alias | Heuristic plausible | Contains an alias with digits |
| --- | --- | --- | --- | --- |
| identifier | 204 | 204 | 191 (93.6%) | 35 |
| path | 130 | 130 | 92 (70.8%) | 55 |
| name | 195 | 195 | 195 (100.0%) | 0 |
| flag | 3 | 3 | 3 (100.0%) | 0 |

All 532 unique terms have at least one alias; **481/532 (90.4%)** pass the heuristic.
Path failures are concentrated around long/number-bearing paths; 90 terms have at least
one alias retaining digits. Numbers 0–20 may also have a spelled alternative, so the latter
count is not a failure count. There are **27 shared alias strings among 1,549 distinct
alias strings** across the entire vocabulary, including case variants. This is an upper
inventory ambiguity diagnostic, not a measured per-shortlist substitution rate.

Three short **synthetic, non-corpus** examples:

- `HTTPServer2` includes “h t t p server two”: good acronym/number alternative.
- `--dry-run` includes “double dash dry run”: good flag alternative.
- `report_v256.json` only offers forms retaining “256”: no fully spelled-number form.

No audio was decoded and no false substitution rate, WER, CER, or pronunciation annotation
is claimed. Single-word proper-name aliases may help ASR but still be rejected by
`canon.py`'s intentional single-word rewrite guard; **do not relax that guard** based on
these retrieval numbers.

## Concrete next implementation slice — recommendations only

### `agents.py`

1. Add a bounded structured history result with up to **8 completed response/user groups**,
   per-turn age, previous user text, and explicit tool paths. Keep the existing latest-reply
   compatibility field. Preserve parent-chain traversal, sidechain isolation, tail/chain
   limits, session-confidence checks, and provider deadlines; do not assume one JSONL row
   equals one assistant turn. Extend bounded reads deliberately rather than unbounded
   session loading as in this offline analysis.
2. Never include the target/current unfinished user message in pre-decode context. Filter
   Claude tool_result records and synthetic metadata; avoid Codex event/response duplication
   and distinguish inherited history from fresh user turns. Record parse/truncation counters.
3. Retain full explicit tool paths **and** basenames/stems, newest first, up to **30 per
   turn** here. The measured gold gain is zero, so treat as a low-cost optional provider,
   not an excuse to parse arbitrary shell scripts or expand filesystem access.
4. Store opt-in per-repo user vocabulary from prior completed sessions; optional cross-repo
   user scope. Do not persist full messages. Cap **300 candidate terms per scope**, decay
   with a **30-day half-life**, include deletion/reset controls, and scrub secrets. The
   experiment is in-memory only; it did not implement this production store.

### `aliases.py`

1. Keep code-style extraction and add a separate conservative name/mixed/quoted-entity
   pass. Preserve exclusion of ordinary English kebab-case prose; plain backticked prose
   must not become a phrase boost. A small lexicon plus robust English inflection handling
   is preferable to treating every unknown lowercase word as a name. This run does not
   validate a general multilingual NER model.
2. Gold audits exposed Unicode prefix fragments, XML closing tags, opaque call IDs,
   contraction fragments, and ordinary inflections: explicitly test these false positives.
   Do not weaken secret filtering to capture paths; use path-aware recognition plus
   secret redaction so long real paths and opaque tokens can be distinguished safely.
3. Add bounded integer pronunciation/digit-spelling options beyond 20. Test paths with
   version/date/line-number components, not just tiny numbers. Preserve the **8-alias cap**;
   prioritize natural/acronym/number variants before Cartesian separator combinations
   exhaust the 64-combination exploration budget. This quality improvement is motivated
   by heuristic failures, not a measured acoustic benefit.
4. Track alias collisions per actual shortlist and preserve `canon.py` ambiguity,
   word-boundary, clause-boundary, and confidence safeguards. Do not automatically rewrite
   ordinary words into capitalized names.

### `rank.py`: reproduce this candidate-ranking recipe, then calibrate acoustics

Selected numerical parameters:

| Parameter | Value |
| --- | --- |
| History | N=8; 1,500 assistant chars / 6,000 previous-user chars per group |
| Recency | `d=0.9` per user-response group; age 0 is latest |
| Source weights | user 1.10; assistant 0.85; tools 0.75; prior-repo 0.55; repo-recent/cwd 0.45; prior-user-wide 0.30; broad repo 0.25 |
| Frequency | 0.15 log multiplier on decayed distinct source-turn incidence |
| Multiple-source bonus | 0.12 per additional source class |
| Persistent scope cap | 300 terms each; session-frequency saturation at 5 prior sessions |
| Persistent decay | 30-day half-life, measured from earlier session end |
| Shortlist | K≤40, no tested nonzero score gate in selected configuration |
| Tie-break | descending score, then exact canonical string |

For candidate term `t`, source `s`, turn age `a`, and source-internal score `r`:

```text
v = weight[s] * 0.9**a * r
F = sum(0.9**a for distinct supporting (source, age))
score = max(v) * (1 + 0.15*log1p(max(0, F-1))) + 0.12*(distinct_sources-1)

persistent_r = (0.5 + 0.5*min(1, log1p(session_frequency)/log(6)))
               * 2**(-days_since_session_end/30)
```

Repo source-internal scores retain production file-rank scores. Assistant/user/tool entries
start at 1. Do not copy production's **0.6 recency floor** into this rule: it prevents stale
items from fading. Do not saturate all strong scores at 1 before sorting: that produces
alphabetical ties. These are ranking scores and can exceed one; map monotonically to the
bounded `Term.weight`/ASR boost domain only after ranking, with acoustic calibration.

Per-repo and user-wide persistence overlap and are counted as separate source classes in
this experiment. They are **correlated evidence**, not independent confirmation. A future
implementation should compare origin-aware deduplication/source bonuses; this experiment
does not establish the best treatment. Likewise, tuning user weight from 1.1 to 1.5 or
adding IDF is not a validated win over the selected recipe.

Next acceptance test: recorded, consented dictation with the no-context anchor, K=10/20/40,
score-gated lists, correct context, stale context, and deliberately wrong-repo distractors.
Measure target-term recall **and** false substitutions, WER/CER, end-to-end latency, and
canonicalization safety. Until then, do not replace an acoustic safety decision with the
1%-precision retrieval result.

## Reproduce and inspect

Uses Python 3.12.15 from the already-installed project environment; stdlib only
in the experiment itself. The pinned Git-object importer supplies task-start context behavior. No dependency
installation or uv sync is needed (avoids writes outside the permitted output directory).

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/danth3b0t/projects/hark/.venv/bin/python \
  /Users/danth3b0t/projects/hark/docs/research/context-ranking/experiment.py
PYTHONDONTWRITEBYTECODE=1 /Users/danth3b0t/projects/hark/.venv/bin/python \
  /Users/danth3b0t/projects/hark/docs/research/context-ranking/validate.py
PYTHONDONTWRITEBYTECODE=1 /Users/danth3b0t/projects/hark/.venv/bin/python \
  /Users/danth3b0t/projects/hark/docs/research/context-ranking/render_report.py
```

`experiment.py --audit` prints a private sample to the local console only. **Do not redirect
that output into a published artifact.** Its sample is intentionally not saved here.
`validate.py` checks independent gold expectations, sibling/target exclusion against the
production Pi reader using a temporary synthetic fixture, metric denominators/distractors,
and aggregate test-split/K integrity. Four checks pass. No production tests were modified.

Artifacts:
- `metrics.csv`: every strategy × K × split × cohort; aggregate only.
- `summary.json`: configuration, counts, selected development row, runtime version, and
  the baseline Git revision and hashes of its six context modules used for this run.
- `population.csv`, `split_stats.csv`, `provider_metrics.csv`, `strata.csv`,
  `strict_gold_metrics.csv`, `gold_kinds.csv`: cohort and robustness breakdowns.
- `source_attribution.csv`, `source_coverage.csv`: overlapping source contributions.
- `aliases.csv`, `bootstrap.csv`, `manual_audit.json`: alias diagnostics, uncertainty,
  and aggregate manual-label review.
- `cutoff.json`, `run.log`, and three Python scripts: cutoff, aggregate progress, replay,
  validation, and report rendering.

Session files and repositories are live and not copied: the cutoff prevents reading future
records but does not freeze retroactive edits, session deletions, current repo state, or
changes in working-tree repo contents. Context implementation imports are revision-pinned;
re-running later is still a new repo-state approximation. Compare corpus counts and repo
availability, and retain the baseline Git revision. The original raw corpus is deliberately not bundled.
