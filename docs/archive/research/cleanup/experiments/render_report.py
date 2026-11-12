"""Render the report from recorded measurements, never private transcript text."""
import json
from pathlib import Path
P=Path(__file__).parent
S=json.loads((P/'summary.json').read_text())
M=json.loads((P/'models-manifest.json').read_text())
def table(names):
 out=['| Configuration | Exact raw → repaired /80 | Runtime flags | Gold audit violations raw → repaired | Corrections /14 | Fillers /40 | p50 / p95 ms |','|---|---:|---:|---:|---:|---:|---:|']
 for n in names:
  s=S[n];out.append(f"| {n} | {s['exact_raw']} → {s['exact']} | {s['invalid']} | {s['audit_raw']} → {s['audit_repaired']} | {s['correction'][0]} | {s['fillers'][0]} | {s['p50']} / {s['p95']} |")
 return '\n'.join(out)
text='''# Cleanup quality experiment — M5 Pro, 48 GB

2026-11-12 UTC. **Exploratory measurements, not a production change or a claim of parity with the Superwhisper application.** All code/results are in this directory. Models were downloaded only to `models/`. Only our own port **8101** was used; every child server was stopped. Neither :8080 nor :50051 was contacted or stopped. No production code was changed.

## Decision

**Keep “S1-mini” by “Superwhisper”, Q4_K_M, semi-formal / prose / general, with prompt-lookup speculation and validation.** It is the best measured interactive cleanup choice. It resolves **14/14 self-corrections**, removes **38/40 annotated hesitation fillers**, reaches **57/80 exact** and **73/80 lexical exact** after experimental span repair. The identity baseline is **20/80 exact**.

**Do not claim the complete goal is solved.** S1 does not reliably produce parentheses, em-dashes, ellipses, lists or paragraph layout. Its raw rewrite violates the label-aware preservation audit on 9/80 inputs; repair reduces that to **1/80**, not zero. The remaining error removes a literal mention of a hesitation word. Neither the experimental heuristic nor punctuation freedom guarantees meaning preservation.

**The 200 ms target is workload-dependent.** S1's curated-set p95 was **96 ms**, then **144–154 ms** in three repeated runs. On **all 97 unique nonempty real snapshot dictations, repeated three times**, p50/p95/max were **40/225/493 ms**. Thus the actual workload narrowly misses 200 ms p95. For ≤20 lexical-token inputs the history p95 was **105 ms**; for longer inputs it was **489 ms**. A hard deadline needs a raw-text fallback (or earlier streaming work), not a larger model. Such a deadline policy was **not implemented or timed** here.

For an **explicit slower rich-formatting mode**, Qwen3-4B Q4_K_M with the stronger `prompt-v3` demonstrations is worth further work: **57/80 exact, 14/14 corrections, zero residual gold-audited violations in this sample**, but **851 ms p95** and some gratuitous punctuation. It is not the default. Qwen3.5-2B Q4_K_M with that prompt is a compromise at **51/80, 362 ms p95**. Neither meets 200 ms.

## Data and privacy

80 hand-reviewed cases:

- All **44** existing handwritten regression cases.
- **10** selected existing real-dictation cases, including long, garbled, Czech, identifier, filler and correction cases.
- **10** additional snapshot cases (not duplicate selections), including two real self-corrections and long dictations.
- **16** new handwritten cases: chained corrections, meaningful `like` / `I mean it`, parentheses, em-dashes, quotes, colon, ellipsis, enumeration, email paragraphs, integer/currency/percentage ITN, known-term merge, contraction preservation, and filler-only input.

The supplied 108-line snapshot contains **97 distinct nonempty raw texts**. All were used for the separate latency stress test; only the selected, reviewed cases have quality labels. Source counts and snapshot digest are recorded in `dataset-info.json` / `eval-manifest.json`.

Gold preserves the speaker's words and order. Existing expected alternatives are retained, except one existing real-case alternative that deletes meaningful `I don't know`. Literal mentions of `etc etc` and hesitation words are retained; trailing enumerative `etc etc` may become an ellipsis. Grammar errors and ASR mistakes are not silently repaired. The rich-style gold often represents **one desirable style, not the only valid punctuation**: a comma-delimited aside can be good prose but miss the parentheses gold. Rich-mark counts are tiny; do not interpret their percentages as general accuracy estimates.

Private inputs, golds, outputs and server logs are local `private-*` files, gitignored; private dataset/output JSON files have mode 0600. `build_eval.py` requires the local `private-golds.json` and original snapshot to recreate private cases. The public manifest omits private text/gold. This report contains no full private dictations. No dictation was sent to web/search services; all inference was local.

## Measurement and scoring

- Host: Apple M5 Pro, 48 GiB, macOS 26.5.1. llama.cpp **b12284 / 9e9619d03**, Metal offload (`-ngl 99`), context 4096, one slot, temperature 0, seed 42, thinking disabled with the model template. Binary hash and host metadata: `environment.json`.
- Models run **serially**, never concurrently with each other. Existing user/system workloads were not stopped. Machine load and thermal state were not controlled; repeat latency drift is visible.
- Full-text `/v1/chat/completions`, warm model and one warm-up request per configuration, static prompt caching. HTTP wall time plus experimental checker/repair is reported; **not ASR, gRPC, protected-span client validation, typing/paste, model loading or cold start**.
- S1 uses the exact model-card system prompt. All **4 × 2 × 2 = 16** trained control combinations were tested. Generic models use a policy plus seven few-shot examples. Stronger `prompt-v3` adds punctuated correction/ITN examples; the complete prompts are in scripts. Chat outputs consistently suppress an added final period when the input had no final punctuation.
- Speculation: `--spec-type ngram-simple --spec-ngram-simple-size-n 1 --spec-ngram-simple-size-m 4`. All primary results use it; S1 also ran without it.
- p50/p95 use nearest-rank empirical quantiles. Each main row is **one pass / 80 requests**, not a large performance study. The three S1 repeats have identical outputs to the main speculative run.
- Pipeline times sum the recorded S1 request+repair and the second-pass request+repair **per case**. They are warm **sequential replay estimates**, not a simultaneous two-resident-server end-to-end deployment measurement. Strict insertion-only variants are offline acceptance replays of those outputs; negligible extra check time is not separately timed.
- Truncated/error generations fall back to input. “Raw” in tables means **pre-invariant-repair, after truncation fallback and chat final-period convention**, not a claim that a truncated output was successful. Per-case finish reasons are saved.

### Lexical-preservation checker

`checker.py` aligns lexical tokens with `SequenceMatcher(autojunk=False)` and records character spans. Casing is free; punctuation at lexical gaps is free. Internal contractions, code-like tokens and numeric punctuation remain lexical. Canonicalization supports bounded integer/ordinal/percent/dollar ITN and explicit known-term merges (`super whispered` / `super whisper` → `Superwhisper`). It does **not** certify arbitrary dates, time expressions, emails, locale number formats or arbitrary ASR “fixes”.

Runtime allowed deletions: hesitation fillers, locally recognized discourse fillers, immediate repetitions, trailing `etc etc`, and bounded spans ending in correction cues, including repeated prepositions. A rejected lexical block is replaced with its input span; the result is rechecked, falling back to all-original if still invalid. This recovers valid distant cleanup without importing production modules or changing production code.

There are two distinct preservation metrics:

1. **Runtime flags:** heuristic rejection before repair. This checker never consults gold.
2. **Gold audit:** independently requires every gold-required token to survive, in order, while prohibiting novel/reordered tokens except canonicalized transforms; extra input words may remain. This catches over-deletion that the runtime heuristic permits. It is used **only for scoring**, never repair. It remains alignment-based and label-dependent, not a proof of semantics.

Ten checker tests cover insertions/reordering/negation, meaningful `like`, literal correction cues, numeric value errors, merges, per-span fallback, punctuation-pass casing/lexicon preservation, gold-audit over-deletion, insertion-only punctuation and anchored mark locations. All pass. Runtime validation rejects 4/80 first gold outputs (ambiguous discourse filler contexts), demonstrating false rejections as well as false acceptance. Repeated expressive words can be wrongly treated as stutters; self-correction scope is heuristic. **Do not deploy this checker as a replacement for hark's protected-span/security/language/client validators.**

Exact match is case/punctuation/whitespace sensitive against any accepted gold. Correction accuracy is lexical exact on the 14 correction cases. Filler removal is recall for 40 annotated hesitation occurrences, **not** recall for every discourse filler. Punctuation P/R counts marks **at aligned lexical gaps**, not just bag-of-marks counts; decimal/currency punctuation inside recognized tokens and contraction apostrophes are excluded; colon-separated time components are separate gaps and their colon is counted. Paragraphs are blank-line gaps; bullets and ellipses are separate types.

## Full-text model comparison

All rows below use the initial consistent raw-transcript few-shot prompt (after correcting the pilot's header mismatch). No Q8 variant gave a compelling quality/latency advantage.

'''
text+=table(['identity','s1-semi-formal-prose-general','qwen3-1.7b-q4_k_m','qwen3-1.7b-q8_0','qwen3-4b-q4_k_m','qwen3-4b-q8_0','qwen3.5-0.8b-q4_k_m','qwen3.5-0.8b-q8_0','qwen3.5-2b-q4_k_m','qwen3.5-2b-q8_0','gemma3-4b-q4_k_m','gemma3-4b-q8_0','phi4-mini-q4_k_m','phi4-mini-q8_0'])
text+='''

### Prompt sensitivity: stronger correction demonstrations

The pilot mixed raw-input few-shots with final `Profile/Transcript` labels. Qwen3-4B copied those headers in 76/80 cases. Those results are retained in `pilot/` and **excluded from conclusions**. Removing the mismatch fixed that artifact. More importantly, explicitly demonstrating punctuated corrections substantially changed quality; model rankings are not prompt-independent.

'''+table(['prompt-v3-qwen3-1.7b-q4_k_m','prompt-v3-qwen3-4b-q4_k_m','prompt-v3-qwen3.5-2b-q4_k_m'])
text+='''

The first two stronger-prompt runs respectively had 1 and 2 length-limit fallbacks; Qwen3.5-2B had none. This was an exploratory ablation after seeing failures, **not a held-out confirmatory test**. No extensive model-specific prompt search was performed. One should not conclude that a general-purpose model is intrinsically incapable of cleanup from its initial prompt results.

## S1 control-line sweep

Each row is the same 80 cases, including already-cased ASR. “Formal” expands contractions; “casual” strips casing/apostrophes. Both conflict with this task even when they match their trained intent. Email/list controls can change lexical content, not just layout. They should not be switched globally.

| Styling | Structure | Context | Exact /80 | Runtime flags | Corrections /14 | p95 ms |
|---|---|---|---:|---:|---:|---:|
'''
for styling in ['semi-formal','semi-casual','casual','formal']:
 for structure in ['prose','lists']:
  for context in ['general','email']:
   s=S[f's1-{styling}-{structure}-{context}'];text+=f"| {styling} | {structure} | {context} | {s['exact']} | {s['invalid']} | {s['correction'][0]} | {s['p95']} |\n"
text+='''
## Rich punctuation: mark-level precision / recall

After repair; location-aware, first gold alternative. `— / 0` means no predictions and zero recall, not perfect precision. The two quotes and two dashes refer to individual marks, not pairs. Alternatives such as comma-delimited asides can be acceptable despite missing this style gold.

| Mark | Gold count | S1 semi-formal prose | Qwen3-4B prompt-v3 | Qwen3.5-2B prompt-v3 |
|---|---:|---:|---:|---:|
'''
for mark,label in [('.', 'Period'),(',', 'Comma'),(';','Semicolon'),(':','Colon'),('?','Question'),('!','Exclamation'),('(','Opening parenthesis'),(')','Closing parenthesis'),('—','Em-dash'),('"','Quotation'),('…','Ellipsis'),('bullet','Bullet'),('paragraph','Paragraph')]:
 vals=[];gold=0
 for n in ['s1-semi-formal-prose-general','prompt-v3-qwen3-4b-q4_k_m','prompt-v3-qwen3.5-2b-q4_k_m']:
  q=S[n]['punctuation'].get(mark,dict(gold=0,precision=None,recall=None));gold=q['gold']
  vals.append(' / '.join('—' if q[k] is None else f"{100*q[k]:.0f}%" for k in ['precision','recall']))
 text+=f"| {label} | {gold} | {' | '.join(vals)} |\n"
text+='''
**Interpretation:** S1 does ordinary commas/colons and cleanup well, but rich punctuation is not “unlocked” merely by relaxing the application's punctuation allowlist. The stronger 4B prompt produces parentheses/dashes/quotes, but adds extra dashes/asides outside the chosen gold, and still fails the explicit list/paragraph layout targets. It is not a complete Superwhisper-quality formatter.

Short public/synthetic examples:

- `I want it written in C, uh no, in Rust.` → S1: `I want it written in Rust.` ✓
- `The worker which is optional runs locally` → S1 chooses comma delimiters; prompt-v3 can produce the desired parentheses without changing words.
- `The fix and this is important must not change words` → the stronger 4B prompt can insert the desired pair of em-dashes.
- S1 changes `I'm gonna keep this wording…` to `I'm going to keep this wording…`; span repair restores `gonna`.
- S1's speech-quote case changes `left` to `leave`. Repair restores the word, but leaves bad quote scope. **Same words do not guarantee correct meaning or readable repair.**

## Two-pass pipeline

Pass 1 is S1 semi-formal/prose/general with lexical repair. Pass 2 is a punctuation-only prompt. The `pipeline-*` rows allow punctuation changes but require identical lexical spellings/case and repair bad spans. The **strict** variant additionally requires the complete first-pass character sequence to survive in order; only punctuation/whitespace may be inserted. If not, it keeps pass 1. Thus “punctuation only” cannot silently delete existing marks or change casing.

The first punctuation prompt inherited a quotation example that changed letter case. This was corrected in the `pipeline-v3` demonstrations; those variants are shown too. Neither version improved overall exact match over S1 alone.

'''+table(['s1-semi-formal-prose-general','strict-pipeline-qwen3-1.7b-q4_k_m','strict-pipeline-qwen3-4b-q4_k_m','strict-pipeline-qwen3.5-0.8b-q4_k_m','strict-pipeline-qwen3.5-2b-q4_k_m','strict-pipeline-gemma3-4b-q4_k_m','strict-pipeline-phi4-mini-q4_k_m','strict-pipeline-v3-qwen3.5-0.8b-q4_k_m','strict-pipeline-v3-qwen3-4b-q4_k_m'])
text+='''
The corrected strict 0.8B / 4B second passes reject 11 / 19 outputs. Pass-1 lexical errors remain: a punctuation-only model cannot recover deleted words. Strict insertion also cannot replace S1's existing comma pair with clean parentheses without retaining those commas, or remove a surviving `etc` to make an ellipsis. This is a structural limitation of this pipeline. It costs another full decode and does **not** meet 200 ms p95. All relaxed/strict variants and mark metrics are in `TABLES.md` / `summary.json`.

## Speculation, length and repeatability

| S1 configuration | p50 / p95 ms | Max ms | Exact /80 |
|---|---:|---:|---:|
'''
for n in ['s1-semi-formal-prose-general','repeat-1-s1','repeat-2-s1','repeat-3-s1','s1-semi-formal-prose-general-nospec']:
 s=S[n];text+=f"| {n} | {s['p50']} / {s['p95']} | {s['max_ms']} | {s['exact']} |\n"
text+='''
Speculation is the largest measured speed lever: 363 ms p95 without it versus 96–154 ms with it in these serial runs. Do not interpret that as a perfectly controlled speedup ratio: load/thermal drift is visible. Spec versus no-spec differed by **one comma in one long case**; aggregate quality was unchanged. Greedy speculation did not give bit-for-bit identical outputs here. Three speculative repeats were byte-identical to the initial speculative run.

| Full private history latency stress | Requests | p50 ms | p95 ms | >200 ms |
|---|---:|---:|---:|---:|
| All 97 unique inputs ×3 | 291 | 40.1 | 224.5 | 17 |
| ≤20 lexical tokens | 252 | 38.2 | 105.0 | 0 |
| >20 lexical tokens | 39 | 163.2 | 489.1 | 17 |

The curated set has only 7 inputs above 20 lexical tokens; its overall p95 hides part of the long-tail cost. The real-history stress test is the better budget signal. It has no additional quality gold and must not be used to inflate the 80-case accuracy sample.

## GBNF feasibility probe

`run.py::grammar_for` builds a linear suffix grammar permitting ordered input-word copying, arbitrary deletions, punctuation and whitespace. Tested Qwen3-1.7B Q4 on the 16 new cases:

- Initial grammar: **4/16 exact, 68/143 ms p50/p95**, 0/3 corrections. Two outputs failed the lexical checker: a hyphenated number unsupported by canonicalization and a concatenated term. A grammar copying word strings with optional separators does not ensure word boundaries.
- Boundary-enforcing version (`grammar_boundary.py`): **2/16 exact, 636/1072 ms**, **14/16 hit the output-token limit** and fell back. Required separators induced whitespace continuation instead of termination. Zero violations after fallback is **not** successful constrained cleanup.

This demonstrates API/GBNF feasibility, not a shippable grammar. Arbitrary deletion still permits an empty or semantically damaged answer; the grammar does not know allowed retraction scope. It also lacks casing/ITN branches. A useful next prototype would compile **validated optional deletion spans**, keep mandatory words non-optional, provide explicit terminal alternatives, bound separator runs, and add approved ITN/merge transitions. A full dynamic per-input preservation automaton was not implemented.

## Deployment recommendation and RTX 3060

1. **Interactive default:** existing S1 engine, exact trained prompt, semi-formal/prose/general, greedy, thinking disabled, warm cached prefix, full GPU offload and ngram-simple lookup. Keep all production protection/language/security/client guards. The research checker is an audit/prototype, not a drop-in replacement.
2. **Safety before expressive style:** typed punctuation edits for parentheses/quotes/em-dashes should preserve words and protected spans and check balanced delimiters; do not simply permit arbitrary model rewrites. Keep per-group rejection. Add literal-filler mentions and ambiguous correction scopes to production regression coverage before relaxing deletion policy. Repair can leave awkward syntax, so acceptance needs more than lexical identity.
3. **Budget:** for a strict 200 ms experience, use a deadline with unchanged-text fallback, or process stable sentence chunks before the utterance finishes. Both tradeoffs require a separate implementation/measurement. Do not promise full long-dictation cleanup in <200 ms from these data.
4. **Optional rich mode:** try the stronger 4B prompt only when the user accepts roughly 0.9 s p95; label it experimental. The smaller 2B strong-prompt variant is about 0.36 s p95. Avoid unconditional second passes.
5. **3060 estimate, not measurement:** desktop RTX 3060 12 GB should fit S1 (~0.48 GB weights), either small Qwen3.5, or one 4B Q4 (~2.4–2.5 GB weights), plus KV/runtime overhead. Even 4B Q8 weights (~4 GB class) should fit alone with a modest context, but ASR, a second model and other workloads reduce headroom. A 6 GB laptop 3060 is materially tighter; prefer S1 or a single Q4 model. Use a CUDA llama.cpp build with GPU offload and benchmark prompt lookup on that hardware. **No 3060 latency or <200 ms claim was measured.**

## Model provenance, artifacts and reproduction

Repositories were verified through the Hugging Face model API on the experiment date; downloads are pinned to resolved commits and SHA-256 checked against LFS metadata. S1's repo offers no Q8_0 file, so only its published Q4_K_M was tested. Qwen3.5 **0.8B and 2B GGUFs exist and ran successfully** in this llama.cpp build. Gemma is text-only here; no vision projector is needed.

'''
text+='| Model | Weight GiB | Pinned repository / URL + hash record |\n|---|---:|---|\n'
for m in M:
 repo=m['url'].split('/resolve/')[0];text+=f"| {m['key']} | {m['bytes']/1024**3:.2f} | [{repo.removeprefix('https://huggingface.co/')}]({repo}); `models-manifest.json` |\n"
text+='''
`models-manifest.json` contains **full immutable URLs and full SHA-256 values for every evaluated model**, including the preexisting weights. S1's required naming is “S1-mini” by “Superwhisper”; its existing LICENSE/NOTICE remain alongside its weights. Other model licenses are recorded in the cached Hub metadata; check upstream terms before redistribution.

Key artifacts:

- `REPORT.md`, `TABLES.md`, `summary.json`: conclusions, every configuration's aggregates, per-mark/source/length metrics.
- `*-results.json`: per-case metrics/timings without transcript text. `pilot/`: superseded pilot measurements.
- `eval-manifest.json`, `build_eval.py`, private local gold/data: reviewed evaluation set.
- `checker.py`, `test_checker.py`: independent research checker, span fallback and tests.
- `run.py`, `extra_runs.py`, `prompt_ablation.py`, `grammar_boundary.py`, `history_latency.py`: exact prompts, server lifecycle and experiments.
- `download.py`, `download_q8.py`, `models-manifest.json`, `*-hub.json`, `*-command.json`, `environment.json`: weights and execution provenance.

From this directory, with the supplied local private artifacts still present:

```sh
PY=/Users/danth3b0t/projects/hark/.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1
$PY -m unittest -v test_checker
$PY build_eval.py
$PY run.py                 # refuses an occupied 8101; owns/stops its children
$PY extra_runs.py
$PY prompt_ablation.py
$PY grammar_boundary.py
$PY history_latency.py
$PY summarize.py
$PY render_report.py
```

Downloads can be reproduced with `download.py` / `download_q8.py`; for the *exact* evaluated revisions use `models-manifest.json` rather than resolving current main again. Do not run the inference scripts simultaneously: they deliberately share only their own port 8101. The static narrative in the report describes this recorded run; rerunning the renderer updates tables, not narrative observations.

Sources: [S1 model card/control matrix](https://huggingface.co/superwhisper/s1-mini), [S1 GGUF](https://huggingface.co/superwhisper/s1-mini-GGUF), pinned repository URLs above, and the repository's `README.md`, `docs/bench/2026-11-11-cleanup-s1.md`, `scripts/llama-up`, `scripts/fetch-models`, `scripts/eval-cleanup`, existing eval JSONL and `server/src/hark_server/cleanup.py` (all read-only). These experiments bypass the production gRPC/client validators; their exact scores are **not directly comparable** to the prior 43/44 production-pipeline result.
'''
(P/'REPORT.md').write_text(text)
