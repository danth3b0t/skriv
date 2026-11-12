# Cleanup quality: rich punctuation without paraphrase (research, 2026-11-12)

> **Scope.** Research and a plan only. No code was changed. hark facts come from `README.md`,
> `docs/bench/2026-11-11-cleanup-s1.md`, `server/src/hark_server/cleanup.py` and `client/src/hark_client/cleanup.py`,
> all read without changes. I read every external source in this session unless it is marked **[U]**. [U] means
> unverified: it comes from memory, a secondary summary or marketing, or it is my own estimate. Sources are listed at
> the end.

## Summary

- **The goal fits one rule that a program can check, the lexical-preservation invariant (LPI).** The output has the
  input's words, in the same order. Only two kinds of word change are allowed. The first is licensed deletions:
  fillers, repeats, retracted self-correction spans and spoken punctuation commands. The second is licensed rewrites:
  numbers and other ITN, merges into known terms, apostrophe repair and hyphen joins. Everything *between* words is
  free if it follows the structural rules: punctuation, quotes, brackets, dashes, ellipses, line and paragraph breaks
  and list markers. Casing is not generated. It is derived by rules afterwards.
- **The literature supports this split.** Gold disfluency removal is deletion-only (DRES), and reasoning LLMs delete
  too much fluent content. Prompted LLMs over-correct in minimal-edit GEC. They change input tokens when asked to
  restore punctuation. When they correct ASR text without the audio, they damage 13–32% of tokens that were already
  correct. Tagging and edit models (GECToR, LaserTagger, the Thutmose ITN tagger, BERT punctuators, a 33M paragraph
  tagger) are precise because they *cannot* rewrite.
- **Industry uses a small fine-tuned model plus a coarse guard.** Superwhisper's S1-mini is a 0.6B normalizer
  steered only by its prompt. Wispr Flow fine-tunes Llama with a budget under 200 ms for the LLM step. ReFyneLabs
  fine-tunes Qwen3.5-2B and checks the output with regexes. Rosehill's paragraph model only checks that nothing but
  whitespace changed. None of them states a word-level invariant. Wispr avoids em-dashes and does not add an
  ellipsis to a cut-off sentence, so these two should be per-profile style switches.
- **Recommended architecture: lattice → constrained decoding → LPI projector → recaser.**
  1. A deterministic lattice lists what may be deleted or rewritten.
  2. A small LM decodes under a grammar built from that lattice for each request. It cannot paraphrase. It only
     chooses punctuation, breaks and which licensed deletions to make.
  3. An LPI verifier and projector checks the result. The client stays authoritative.
  4. Deterministic passes set casing and apply the profile style.
  
  For self-corrections, the deterministic layer opens the windows (correction cue, rough copy, matching word class).
  Inside a window the model chooses the scope. By construction it cannot keep the wrong value and drop the correction.
- **Model: 0.6–0.8B for the 150 ms budget.** S1-mini in hark has p50 42 ms, but a 929-char dictation took 542 ms.
  Fine-tune S1-mini, Qwen3-0.6B or Qwen3.5-0.8B on the H100s, using synthetic and teacher-labelled data that satisfies
  the LPI. Use 1.7–2B only as a quality tier; 4B and up do not fit the budget. Train a 30–150M token classifier as a
  challenger and fallback.
- **Phases:**
  - **P0:** LPI checker and projection, and a richer eval. The model does not change.
  - **P1:** constrained decoding with the stock S1-mini.
  - **P2:** data and fine-tune.
  - **P3:** tagger challenger; latency work (chunking, streaming).
  - **P4:** audio cues and personal style.

---

## 1. The goal as a specification

| The user wants | Rule |
|---|---|
| cleaner, more readable text | punctuation, casing, line/paragraph breaks and list layout are free (within §5.3) |
| **never** rephrased or paraphrased | no inserted words, no substituted words, no reordering. The only exceptions are the transformations in §5.2 |
| self-corrections resolved like Superwhisper | delete the *reparandum* (what was retracted) and the *editing term* (`uh no`, `I mean`); keep the *repair* |
| fillers removed | delete filled pauses, discourse fillers and immediate repeats |
| rich punctuation | `, . ; : ? ! … — – ( ) " " " ' ' '`, line break, blank line, list markers |
| light restructuring | a list only where speech clearly enumerates (≥3 items); paragraph breaks in long dictation |
| proper casing | sentence starts, `I`, context terms, days/months; identifiers untouched |

Examples (the targets are mine):

| Input (ASR) | Output | Operations |
|---|---|---|
| `I want it written in C, uh no, in Rust.` | `I want it written in Rust.` | RETRACT `in C` + editing term `uh no` |
| `So, um, there are three things the parser, the lexer and the, uh, the type checker.` | `So there are three things: the parser, the lexer and the type checker.` | FILLER, REPEAT, colon (free) |
| `the migration and this is the annoying part needs a downtime window` | `The migration — and this is the annoying part — needs a downtime window.` | dashes, period, casing (free) |
| `he said quote ship it end quote and left` | `He said, "Ship it," and left.` | SPOKEN_PUNCT, quotes (free) |
| `we could use redis or memcached or whatever etc etc` | `We could use Redis or Memcached or whatever…` | ETC; TERM casing from context |
| `I'm gonna check it` | unchanged except punctuation | `gonna` → `going to` is **forbidden** (rephrasing) |

## 2. Where hark is today

The pipeline: Parakeet returns cased, punctuated text. S1-mini rewrites it with the control line `semi-formal / prose /
general`. The rewrite is aligned word by word (`difflib` on the words with case and punctuation removed) and turned
into quoted edits. Each edit must pass the invariant of one *kind*: punctuation, capitalization, spacing,
filler_removal, self_correction or number_format. Edits that fail are dropped together with their *group* (edits at
most one word apart). Global rules then check negations, digits and `!`. The client validator is authoritative.

Results: 43/44 exact on the handwritten set and 26/29 on the real set. S1-mini latency is p50 42 ms and p95 78 ms on
the 44-case set (M5 Pro), but the 929-char dictation took 542 ms.

Gaps relative to the goal (from reading the code):

1. **The punctuation alphabet is too small.** `PUNCTUATION = set(".,;:?!'\u2019-")` has no quotes, brackets,
   em/en dashes or ellipses. The bench rejects quotes around mentioned words "by design".
2. **The cue list cannot express the user's headline example.** `CORRECTION_CUES` contains `no wait`, `wait no`,
   `no sorry`, `sorry no`, `I mean`, `scratch that`, `actually no`, `no actually` and `or rather`. It has no lone `no`
   or `actually`, does not allow fillers inside the editing term (`uh no`), and has no `make that` or `never mind`. So
   `in C, uh no, in Rust` → `in Rust` is neither a filler removal nor a self-correction, and S1's correct rewrite
   would be dropped. I worked this out from the code; I did not run it.
3. **Validation is by kind, and failures drop whole groups.** When one word change fails, its neighbours' valid
   punctuation and casing edits are dropped with it. Long dictations therefore lose part of their cleanup: in
   `r-filler-5`, 6 of 14 edits were applied.
4. **S1-mini is not trained for rich punctuation, and its input does not match its training data.** Its card
   documents periods, commas and casing, lists and email layout, but not dashes, brackets or ellipses. `semi-formal`
   smooths colloquialisms (`gonna` → `going to`), and `formal` expands contractions; both break the LPI. S1 was
   trained on input that is "usually lowercase and unpunctuated", but hark sends it Parakeet's cased, punctuated text.
5. **Latency grows with length.** Dictations longer than about 45 words do not fit 150 ms with a single sequential
   decode (§9).

## 3. Industry practice

### 3.1 Superwhisper

- **The S1 family (August 2026).**
  - S1-Voice: cloud ASR.
  - S1-Language: a cloud instruction model for cleanup, formatting and summaries.
  - S1-mini: open weights; a 0.6B fine-tune of Qwen3-0.6B that runs locally.
- **S1-mini** ([card](https://huggingface.co/superwhisper/s1-mini), [blog](https://superwhisper.com/blog/s1)):
  - **What it does.** It removes fillers, resolves false starts and self-corrections "to the value the speaker
    landed on", adds punctuation and casing, and writes numbers, dates, times, currency and emails in written form.
  - **How it is steered.** Only through an exact system prompt plus a control line:
    - `Styling`: casual / semi-casual / semi-formal / formal.
    - `Structure`: prose / lists. Lists need at least 3 items, and "anything that isn't really a list stays as prose".
    - `Context`: general / email.
  - **Required settings.** Thinking must be disabled; otherwise the output is blank. Values outside the trained set
    "can hallucinate".
  - **Reported results.** 94.8% token accuracy on 7,519 held-out cases, an 11.6% text-edit error rate, list vs
    paragraph chosen correctly 97.6% of the time, and under 1% degenerate outputs.
  - **Behaviour.** The blog says it leaves "three or four days" and "tea or coffee" alone, and that it "will never
    add content you did not say".
- **Modes.** Message mode "removes speech artifacts and filler words, fixes grammar and punctuation". Super mode adds
  context: the app, selected text and the clipboard. "S1-mini can't run Super Mode: it works only in Message and Mail
  modes" ([docs](https://superwhisper.com/docs/modes/built-in)).
- **Super-mode prompt [U].** A gist from a user (2025-09) says it is Superwhisper's system prompt for gpt-5-nano. It
  contains:
  - "PRIMARY RULE: PRESERVE THE ORIGINAL MESSAGE … When in doubt, leave the original text unchanged"
  - conservative spelling fixes from the vocabulary list
  - self-correction ("8pm actually I mean 9pm" → "9pm")
  - URL/email formatting and "Preserve Intent"
  - output wrapped in `<sw_response_content>`
  
  Superwhisper has not confirmed it ([gist](https://gist.github.com/nandcello/410934d1251d58cb865634d3d429b4ef)).
- **Takeaway.** Locally, Superwhisper relies on a task-specific fine-tune that is guarded only by its training. In the
  cloud it relies on prompts. Neither checks words mechanically.

### 3.2 Wispr Flow

- **Models.** Fine-tuned **Llama** models for "real-time transcript cleanup", served on Baseten. The whole pipeline
  takes **under 700 ms at p99** ([Baseten](https://www.baseten.co/resources/customers/wispr-flow/)). The targets are
  under 200 ms for the LLM and under 200 ms for ASR. On token-level formatting: "LLMs are phenomenal at recall, but
  very low precision". Flow learns from user edits
  ([post](https://wisprflow.ai/post/technical-challenges),
  [languages](https://wisprflow.ai/research/supporting-languages)). Its quality metric is the **Dictation Edit
  Rate**: the share of dictations the user edits ([Canto 1](https://wisprflow.ai/canto-1-results)).
- **Documented behaviour**
  ([Smart Formatting & Backtrack](https://docs.wisprflow.ai/articles/5373093536-how-do-i-use-smart-formatting-and-backtrack),
  [Android](https://docs.wisprflow.ai/articles/6568835559-why-wispr-flow-sometimes-removes-or-changes-words-on-android-smart-formatting)):
  - **Backtrack (self-corrections).**
    - Trigger phrases: "actually", "scratch that", "never mind", "I mean", "no wait", "wait no", "forget that",
      "cancel that", "strike that". A plain restatement also works.
    - It removes "the whole abandoned attempt".
    - "A cue word alone is insufficient". "Ambiguous replacements preserve both versions". An unfinished fragment
      that is not restarted stays.
    - "I actually enjoyed the movie" is preserved.
  - **Punctuation.**
    - "Flow avoids inserting em dashes automatically".
    - It "does not add ellipses or terminal punctuation to a cut-off final sentence".
    - Spoken punctuation ("comma", "em dash", "new paragraph") wins over automatic punctuation.
    - When a mark is clearly talked about ("remove the comma"), it stays a word.
  - **Lists and paragraphs.** Lists come from "one… two…" or "first… second…", and the spoken markers are removed.
    Paragraph breaks go at topic changes.
  - **Wording.** "gonna" is expanded unless the tone is informal. One-word compounds are preferred.
  - **Coding.** Formatting is *off* when dictating into AI apps or coding-agent terminals.
  - **Known failures.** It drops a leading "so", deliberate repetition and emphasis.
  - **Undo.** "Undo AI edit" restores the raw transcript.

### 3.3 Aqua Voice

Aqua Voice offers the Avalon ASR, which is tuned for technical vocabulary, and an Edit Mode for voice commands on
selected text ([Avalon](https://aquavoice.com/guide/how-to-use-avalon)). Its 2024 launch described an LLM that decides
"whether to transcribe what you said or … execute it" ([HN](https://news.ycombinator.com/item?id=39828686)). Its site
says the model "preserve[s] every word while refining your text" [U, marketing; no technical detail]
([site](https://aquavoice.com/)).

### 3.4 Google

- **2022.** A BERT token classifier, trained on Switchboard, detects disfluencies. Self-training distilled it to a
  **3.1M-parameter** on-device model. A streaming version adds a learned "wait" head
  ([blog](https://research.google/blog/identifying-disfluencies-in-natural-speech/),
  [paper](https://arxiv.org/abs/2205.00620)).
- **2026.** Gboard *Rambler* runs on **Gemini 3.5 Transcribe**, which goes straight from audio to polished,
  formatted text. It handles self-corrections ("let's meet Tuesday—no, Wednesday"), removes fillers and formats
  automatically ([blog](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5-transcribe/)).
  Gboard's advanced voice typing adds punctuation automatically
  ([help](https://support.google.com/gboard/answer/11197787)).

### 3.5 Apple

- **Advanced Dictation (iOS/macOS 27).** It runs on the on-device model AFM 3 Core Advanced. Apple promises more
  reliable casing and punctuation
  ([MacRumors](https://www.macrumors.com/2026/06/22/advanced-ai-dictation-not-enabled-by-default/),
  [TechCrunch](https://techcrunch.com/2026/06/08/apple-introduces-systemwide-dictation/)). Third-party guides say
  Apple's documentation describes neither filler removal nor spoken corrections [U].
- **Fine-tuning experiment.** EnviousWispr trained a LoRA (rank 32) on Apple's ~3B on-device model with about 2,000
  pairs. Self-correction rose from **13% to 86%** on 15 held-out cases, and latency grew by 35%. Apple's adapter API
  is end-of-life in version 27
  ([post](https://enviouswispr.com/blog/fine-tuning-apple-on-device-model-self-corrections/)).

### 3.6 Open models aimed at the same job

| Model | What it shows |
|---|---|
| [S1-ASR-Cleanup](https://blog.danielrosehill.com/posts/s1-asr-cleanup-dictation-transcript-cleanup-y73m4i/) (Rosehill, S1-mini + LoRA) | S1-mini can be fine-tuned under its license; it learns paragraphs and house style. It expands `gonna`, which breaks the LPI |
| [Gemma 3 270M paragraph breaks](https://blog.danielrosehill.com/posts/asr-paragraph-breaks-gemma-3-270m-85hckk/) | "Changes nothing else" is *checked*: if anything but whitespace changed, the input is returned. Break F1 0.78. 2/26 outputs fell back. The untuned base changed the text on 26/26. Few-shot Claude Haiku scored F1 0.73 |
| [MiniLM-L12 paragraph tagger](https://blog.danielrosehill.com/posts/update-minilm-asr-paragraph-breaks-8hlzau/) (33M) | **Beats the generator**: F1 0.85 vs 0.78, 32 MB vs 437 MB, 0.65 s vs 23–48 s per memo on one CPU thread. It "cannot change a word" |
| [ReFyneLabs simplewords v2](https://huggingface.co/ReFyneLabs/simplewords-dictation-cleanup-v2) (Qwen3.5-2B QLoRA, MLX) | v1 kept the *wrong* side of corrections (inversion) because its data generator started from the clean text. v2 uses 48k rows (25% self-correction, labelled `{wrong, right, retained}`) and reaches 100% on template-disjoint held-out cases. **Fusing at 4-bit dropped self-correction 100% → 57.8%** while val loss looked perfect. 8-bit fused: 214 ms per utterance on an M3 Ultra. The checkpoint was chosen by behaviour, not val loss |
| [stillerman fdt-disfluency-mini-11m](https://huggingface.co/stillerman/fdt-disfluency-mini-11m) | 11M BERT tagger with labels KEEP / DELETE / KEEP_STRIP_COMMA / KEEP_CAPITALIZE: "deletion-only by construction". DELETE-F1 0.985 on synthetic data |
| [Mów disfluency classifier](https://huggingface.co/sam-castro/mow-disfluency-classifier) (ModernBERT-base int8) | Labels: filled pause, repetition, revision, partial word. 5–50 ms per sentence on Apple Silicon |
| [SpeakoFlow dictation-cleanup-bench](https://huggingface.co/datasets/SpeakoFlow/dictation-cleanup-bench) | 150 rows, **68 of them no-ops** whose output must equal the input byte for byte. "The no-op half is the part most models fail" (search snippet; the page returned 401) |

### 3.7 Lessons

1. Everyone runs a generator plus a guard, and the guards are coarse: length ratios, list keywords, a diff that may
   only change whitespace. The LPI is a stronger guard that is still cheap.
2. **Self-correction is the hardest behaviour and the most sensitive to the weights.** Base models fail: Apple's
   stock model 13%, the Qwen3.5-2B base 0–19%. Fine-tunes reach 86–100%. Aggressive quantization breaks it silently.
3. When the task is to label positions (punctuation, paragraph breaks, deletions), a tagger matches or beats a
   generator at a fraction of the cost.
4. Leaving clean text alone (the no-op cases) is where models fail. Evaluation must weight these cases heavily.
5. Em-dashes and trailing ellipses are a matter of taste. Wispr leaves them out by default; the user wants them.
   Make them switches per profile.

## 4. Literature

### 4.1 The structure and detection of disfluencies

- **Anatomy.** A repair has a reparandum, an interruption point, an optional interregnum (editing term) and the
  repair (Levelt 1983 and Shriberg 1994, via [Ginzburg et al.](https://clp.ling.uni-potsdam.de/publications/Ginzburg-2014-1.pdf)).
  Shriberg found regularities in Switchboard disfluencies
  ([ICSLP](https://asel.udel.edu/icslp/cdrom/vol3/1031/a1031.pdf)). A sentence of 10–13 words has a 50% chance of
  containing one, and the chance rises with length
  ([Google](https://research.google/blog/identifying-disfluencies-in-natural-speech/)). About 5.9% of Switchboard
  words are disfluent [U, secondary summary]. Early work combined pattern matching, syntax and acoustics to find
  repairs ([Shriberg, Bear, Dowding 1992](https://aclanthology.org/H92-1085.pdf)).
- **The rough copy.** The reparandum is often a "rough copy" of the repair: `in C … in Rust`, `on Tuesday … on
  Wednesday`. Johnson & Charniak's noisy-channel TAG model is built on this idea ([ACL 2004](https://aclanthology.org/P04-1005/)).
  The deterministic resolver in §6 uses the same signal.
- **State of the art on Switchboard.** Self-attentive parsers that are self-trained on Fisher and then ensembled
  ([Jamshid Lou & Johnson 2020](https://arxiv.org/abs/2004.05323)). Small BERTs reach comparable quality on device
  ([Rocholl et al. 2021](https://arxiv.org/abs/2104.10769)).
- **Data.**
  - Switchboard and Fisher come from LDC and are licensed.
  - [DisfluencySpeech](https://arxiv.org/html/2406.08820) has transcripts at several levels of cleanup.
  - [AMI](https://groups.inf.ed.ac.uk/ami/corpus/license.shtml) is CC BY 4.0 and has disfluency annotations.
  - [LARD](https://arxiv.org/abs/2201.05041) generates synthetic repetitions, replacements and restarts from fluent
    text.
  - [Disfl-QA](https://arxiv.org/abs/2106.04016) contains contextual disfluencies.

### 4.2 Evaluating disfluency removal with LLMs

- **[DRES](https://arxiv.org/abs/2509.20321).** "Gold disfluency removal is a deletion-only task", which makes it a
  probe of whether a model performs "faithful structural repair or biased reinterpretation". Its findings:
  - Models fall into stable precision/recall regimes.
  - **Reasoning models systematically over-delete fluent content.**
  - Fine-tuning gives state-of-the-art results but harms generalization.
- **[Z-Scores](https://arxiv.org/abs/2509.20319).** Word-level P/R/F1 (E-scores) plus span-level scores per category:
  EDITED (repairs), INTJ (interjections, fillers) and PRN (parentheticals). A deterministic alignment maps generated
  text back onto the disfluent input. hark should adopt both the categories and the alignment idea; the LPI aligner is
  that alignment.

### 4.3 Punctuation restoration, including rich punctuation

- **The standard setup** labels each token with one of 3–4 classes (comma, period, question mark, other). BERT-based
  models dominate it ([Alam et al. 2020](https://aclanthology.org/2020.wnut-1.18.pdf)).
- **[Comprehensive punctuation restoration](https://aclanthology.org/2021.findings-emnlp.393.pdf)** (Pogoda &
  Walkowiak 2021) works for rich punctuation and fits the LPI directly:
  - The label of a token is *the whole non-alphanumeric string that follows it*.
  - English has 513 such classes, with a long tail. The classes up to the 99th percentile number 23 plus "none".
  - Training on the larger label set raised recall by 12–14 points without hurting the basic marks. The best English
    F1 was 84.3.
  - Scoring uses strict exact-class P/R/F1 plus a lenient overlap score (mRS).
  
  This "gap string as label" view is exactly the LPI's free gap slot (§5.3).
- **LLMs as punctuators.**
  - "LLMs tend to repeatedly use the same punctuation marks and alter input text tokens"; a two-stage BERT model does
    better ([Zhong & Sun, ICASSP 2025](https://doi.org/10.1109/icassp49660.2025.10889374), abstract).
  - LLaMA punctuates well, but its decoding is slow and hallucinates. Forward-pass-only decoding (FPOD) is 19.8×
    faster with no hallucinations ([Pang 2024](https://arxiv.org/abs/2408.11845)).
- **Audio helps** where text cannot decide ("Oh yeah?" vs "Oh yeah."): NeMo has a lexical-plus-audio punctuation
  model ([docs](https://docs.nvidia.com/nemo-framework/user-guide/24.12/nemotoolkit/nlp/punctuation_and_capitalization_lexical_audio.html)).
  hark has Parakeet's word timestamps, which give pause lengths for free.
- **Benchmarks and off-the-shelf models.**
  - [LibriSpeech-PC](https://arxiv.org/abs/2310.02943) is punctuated, cased LibriSpeech under CC BY 4.0. It is read
    speech, so it is an evaluation set for punctuation only.
  - Off-the-shelf punctuators restore few marks. [deepmultilingualpunctuation](https://github.com/oliverguhr/deepmultilingualpunctuation)
    restores `. , ? - :` and was trained on Europarl.
    [punct_cap_seg_47_language](https://huggingface.co/1-800-BAD-CODE/punct_cap_seg_47_language) does punctuation,
    truecasing and sentence segmentation for 47 languages; Czech is not among them.

### 4.4 Spoken-to-written normalization (ITN)

- Production ITN mostly uses rule-based WFST grammars, for example NeMo's
  ([NVIDIA](https://developer.nvidia.com/blog/text-normalization-and-inverse-text-normalization-with-nvidia-nemo/)).
- Neural seq2seq ITN can make "unrecoverable" errors, so production systems decide at run time whether to trust it
  ([Sunkara et al. 2021](https://arxiv.org/abs/2102.06380)).
- [Thutmose](https://www.isca-archive.org/interspeech_2022/antonova22_interspeech.pdf) turns ITN into tagging to avoid
  hallucination. Shallow fusion of a WFST with an LM gets the best of both
  ([2203.15917](https://arxiv.org/abs/2203.15917)).
- For hark: the model may *propose* written forms, but a deterministic verifier must *accept* them. hark already
  checks the values of spoken numbers this way.

### 4.5 Minimal edits vs fluency rewrites, and LLM over-editing

- **The two goals in GEC.** GEC distinguishes minimal-edit correction from fluency rewriting.
  [Sakaguchi et al. 2016](https://aclanthology.org/Q16-1013/) argued *for* fluency rewriting in learner GEC. hark's
  user explicitly wants the opposite for words and fluency only for punctuation, so the LPI is a minimal-edit regime
  for words.
- **Prompted LLMs over-correct.**
  - ChatGPT over-corrects more than other systems, especially on long sentences
    ([Fang et al. 2023](https://arxiv.org/abs/2304.01746)).
  - GPT-4 tops JFLEG, a fluency benchmark, but edits differently from humans
    ([Coyne et al. 2023](https://arxiv.org/abs/2303.14342)).
  - High recall comes with low precision ([Penteado & Perez 2023](https://arxiv.org/abs/2306.15788)).
  - Zero- and few-shot LLMs "systematically overcorrect … by rewriting well-formed spans"
    ([Karpo & Chernodub 2026](https://aclanthology.org/2026.findings-emnlp.1049/)).
- **What reduces over-correction.**
  - Fine-tuning and adapting the error rate ([Staruch et al. 2025](https://aclanthology.org/2025.bea-1.9/)).
  - Edit-level majority voting ([BEA 2026](https://aclanthology.org/2026.bea-1.60.pdf)).
  - Ensembling and ranking ([Omelianchuk et al. 2024](https://aclanthology.org/2024.bea-1.3/)).
- **ASR correction.** Split every WER change into repaired and damaged tokens: text-only Gemini 2.5 Flash correction
  "breaks 13–32% of already-correct" tokens ([Kudryavtsev et al. 2026](https://arxiv.org/abs/2610.30385)). Use the
  same repair/damage split in hark's metrics.

### 4.6 Text-editing models

- [LaserTagger](https://aclanthology.org/D19-1510.pdf) predicts KEEP or DELETE per token, plus phrases to add before
  a token. It is "less prone to hallucination" and more data-efficient.
- [GECToR](https://aclanthology.org/2020.bea-1.16/) is Grammarly's "tag, not rewrite" model.
- For hark the useful pattern is to restrict the output space to edit operations. The LPI does this for a generator
  through constrained decoding (§8). A tagger does it natively (§11.6).

### 4.7 Constrained decoding

- **Engines.**
  - **llama.cpp GBNF.** Grammars can be passed per request (the `grammar` field of the server's completion
    endpoints). It has documented "performance gotchas"
    ([README](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)).
    [PR #4306](https://github.com/ggerganov/llama.cpp/pull/4306) checked the sampled token first and scanned the
    whole vocabulary only when needed. Since [#17937](https://github.com/ggml-org/llama.cpp/pull/17937), "the grammar
    is now always applied first to the raw logits", so that saving may be gone [U for the current cost].
  - **llama.cpp + llguidance** (`-DLLAMA_LLGUIDANCE=ON`; grammars start with `%llguidance`). A token mask takes about
    **50 µs** for a 128k vocabulary (p99 0.5 ms)
    ([docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/llguidance.md)). It can also fast-forward tokens
    that are forced ([llguidance](https://github.com/guidance-ai/llguidance)).
  - [XGrammar](https://blog.mlc.ai/2024/11/22/achieving-efficient-flexible-portable-structured-generation-with-xgrammar)
    serves vLLM and SGLang.
  - MLX: `generate_step` takes `logits_processors`
    ([mlx-lm](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/generate.py)).
- **Theory and pitfalls.**
  - Automata can express constraints at the character level and be composed with the tokenizer
    ([Koo et al. 2024](https://arxiv.org/abs/2407.08103)). Promoting a character-level automaton to subwords has
    subtle pitfalls ([Cognetta et al.](https://openreview.net/notes/edits/attachment?id=PDbSZ14Qqo&name=pdf)).
  - Constraints that do not align with the subword vocabulary "significantly impair task accuracy"
    ([DOMINO, ICML 2024](https://proceedings.mlr.press/v235/beurer-kellner24a.html)). How a grammar treats
    whitespace tokens changes accuracy ([2502.14969](https://arxiv.org/abs/2502.14969)).
  - Masking distorts the model's distribution
    ([Grammar-Aligned Decoding](https://arxiv.org/abs/2405.21047)). This is the main argument for *also* fine-tuning
    on data that satisfies the LPI, so that the mask rarely has to intervene.
- **Speed.** Prompt-lookup decoding drafts from the input itself and gives a "2x-4x" speedup on input-grounded tasks
  ([repo](https://github.com/apoorvumang/prompt-lookup-decoding)). hark already runs llama.cpp `ngram-simple`
  speculation.

## 5. The lexical-preservation invariant

### 5.1 Definition

Tokenize a text into alternating gaps and words: `g0 w1 g1 w2 … wn gn`. Words are the units in §5.5. Gaps are
everything between them.

`LPI(input, output)` holds when both conditions are true:

- A **monotone derivation** maps the input words to the output words. It uses only the operations in §5.2, and every
  input word and every output word is used exactly once.
- Every output gap is **valid gap material** under the profile (§5.3).

Casing is compared case-insensitively and then *recomputed* (§5.4). With constrained decoding the derivation comes as
a by-product. Without it, an aligner recovers the derivation (§5.5).

### 5.2 Operations

| Op | Input → output | Licence (deterministic) | Metric category |
|---|---|---|---|
| KEEP | `w` → `v` | `key(w) == key(v)`; protected spans byte-equal | – |
| FILLER | `um`/`uh`/`er`/`erm`/`ah`/`hmm`/`mm`… → ∅ | lexicon | INTJ |
| DISCOURSE | `like`, `basically`, `literally`, `you know`, `kind of`, `sort of`, `I mean` (not a cue), `so`/`well` at the start of an utterance → ∅ | lexicon; capped share; logged. The model decides *where*, because `I like it` is not a filler | INTJ |
| REPEAT | `x1..xk x1..xk` → `x1..xk` (k ≤ 3); fragment `proc-` before `processed` | keys equal / prefix fragment | EDITED |
| RETRACT | `R I r` → `r`: reparandum R (1–8 words), interregnum I (cue phrase ± fillers), repair r (kept, non-empty) | cue in lexicon **and** (cue set off by punctuation **or** rough copy `key(R₀)=key(r₀)` **or** matching class of R's last and r's first word: number/time, weekday, month, name, identifier). Negations, digits and protected spans may disappear only inside R | EDITED |
| RESTART | `R r` → `r` without a cue | rough copy of ≥ 2 words, or R ends in a fragment | EDITED |
| CANCEL | `S scratch that` → ∅ (also `never mind`, `forget that`) | S is the preceding sentence; the cue ends the utterance or a sentence | EDITED |
| SPOKEN_PUNCT | `comma`, `period`, `question mark`, `colon`, `new line`, `new paragraph`, `quote … end quote`, `open/close paren`, `em dash` → the mark, in the adjacent gap | lexicon; the model decides content vs command ("remove the comma") | – |
| ITN | spoken numbers, ordinals, `percent`, currency, times, dates, emails, URLs → written form | value-equality verifier (hark's `_spoken_numbers`, extended) or equality under a WFST ITN | – |
| TERM | 1–3 words → the canonical form of a **context term** (`super whispered` → `Superwhisper`, `main dot py` → `main.py`) | target is in the request's term list; small key distance after expanding spoken symbols (`dot` `dash` `slash` `underscore` `at`) | – |
| APOS | `im` → `I'm`, `dont` → `don't` | allowlist of apostrophe-less forms that are **not** words themselves (excludes `were`, `its`, `well`, `cant`, `wont`, `ill`, `shed`, `lets`) | – |
| HYPHEN | `follow up` ↔ `follow-up` | concatenated keys equal; joiner `-` | – |
| ETC | `etc etc`, `et cetera et cetera` → `etc.` or `…` | lexicon | INTJ |
| ENUM | `first`/`second`/… or `one`/`two`/… at the start of a list item → list numbering | only when the output is a numbered list | – |

**Everything else is forbidden.** That includes:

- inserting words and replacing words with synonyms;
- colloquialism smoothing (`gonna`) and expanding or contracting contractions;
- reordering;
- deleting content words outside RETRACT, RESTART and CANCEL.

ASR misrecognitions stay unless they are TERM merges into the context vocabulary. That is deliberate: vocabulary
biasing upstream is hark's tool for those.

### 5.3 Gap material and structure

Gaps contain no letters and no digits. The one exception is list numbering at the start of a line. Gaps are built
from:

```
OPEN    ( “ " ‘ '
CLOSE   , ; : . ? ! … ) ” " ’ '
DASH    " — "  "—"  " – "            (style knob decides which)
BREAK   " "  "\n"  "\n\n"  "\n- "  "\n<n>. "
gap     = CLOSE{0,3} (BREAK | DASH) OPEN{0,2}     between words
start   = OPEN{0,2} ; end = CLOSE{0,3}
```

Structural rules. A regular grammar cannot check all of them, so they are verified after decoding:

- Quotes and brackets are balanced within a paragraph. No pair is empty. No pair crosses a paragraph break.
- No doubled or clashing marks (hark's `_SLOPPY`).
- A `?` in the input stays. A `!` in the input stays. A `!` is never added, because that changes the tone.
- `…` appears only at a trailing-off point (end of the utterance, or before a restart), or from the ETC op.
- Lists need at least 3 items. Items start a line with the same marker type. The list is preceded by `:` or a break.
  Lists are allowed only in multi-line profiles.
- Paragraph breaks only fall between sentences and only in multi-line profiles.
- Single-line profiles (terminal, chat) allow `" "` as the only BREAK.

### 5.4 Casing is derived, not generated

The model's casing is ignored. Gaps and the derivation decide where sentences start, and casing then follows rules:

1. Protected spans and identifiers keep their input bytes, so `npm` stays lowercase at a sentence start.
2. TERM and ITN outputs take their canonical form.
3. The first word of a sentence, paragraph, list item or quoted sentence is upper-cased (first letter).
4. `I` and its contractions, days and months, and context terms take their canonical case.
5. A capital that only came from an input sentence start, where the output no longer starts a sentence, is lowercased
   if the word is a common lowercase word (frequency lexicon). This covers ASR "stray capitals" such as
   `we could Use those` and the merge in `Tuesday. No wait, Wednesday.`.
6. Otherwise the input's casing stays. That keeps the proper nouns ASR got right.

Casing errors become a deterministic, testable component, and the grammar can be case-insensitive (§8).

### 5.5 Implementing the check

1. **Tokenize** (Unicode, NFKC).
   - Protected spans come first: hark's identifiers, paths, flags, numbers with internal punctuation (`3:30`,
     `23,450`, `v1.2`), emails, URLs and known abbreviations (`e.g.`, `a.m.`). Each is one atomic word.
   - Other words match `[\p{L}\p{M}\p{N}]+(?:['’ʼ][\p{L}]+)*`, so a contraction is one word, plus hyphenated compounds
     and fragments that end in `-`.
   - A `'` or `’` with letters on both sides belongs to the word. Otherwise it is a quote in the gap.
   - A trailing `s'` counts as a possessive only if the input had it.
2. **Compute keys.** `key(w)` is the casefolded word with apostrophes removed (`I'm` → `im`). Protected words
   compare bytes.
3. **Anchor.** Run `SequenceMatcher` on the keys, as `_align` does today, to fix long equal runs.
4. **Derive the changed regions.** A small dynamic program over the operations in §5.2 runs inside each changed
   region and its neighbours. The windows are bounded: K = 8 for RETRACT, at most 6 words for ITN, at most 3 for
   TERM.
   - Deleted blocks **slide** across equal neighbours. `in C uh no in` + `Rust` is the same edit as `in` + `C uh no in
     Rust`, and each slide position is tried for a licence.
   - Cost: O(n·K). That is under 1 ms for dictation-sized input [U, estimate].
5. **Validate gaps** against §5.3 and the profile.
6. **Project on failure** instead of rejecting. Run the dynamic program again with costly fallback operations:
   - REVERT_SUB: output words become the input words they replaced.
   - REVERT_DEL: an unlicensed deletion is restored.
   - REVERT_INS: an inserted word is removed.
   
   Inside a reverted region the input gaps return. At its edges the model's gap is kept if it is still valid. Then
   the structure is checked again; a region that still fails gets its input gaps back. This keeps the model's
   punctuation even when one word change is rejected. Today's group drop loses it. The final guards stay: removed
   share, `?`/`!` kept and the language guard. If they fail, the raw text is used.
7. **Recase** (§5.4) and apply the **style pass**: dash and quote style, chat's rule against a final period,
   single-line folding.

### 5.6 Edge cases

| Case | Handling |
|---|---|
| `don't` vs `do not`, `cannot` vs `can not` | different keys → forbidden (no contraction changes) |
| `its`/`it's`, `were`/`we're` | not on the APOS allowlist → the input form stays |
| curly vs straight quotes and apostrophes | equal for comparison; the profile picks the output style (terminal: straight) |
| `...` vs `…`, `--` vs `—` | normalized in gaps; the style pass picks one |
| `23,450`, `3:30` | inner punctuation belongs to the number, not the gap |
| `main.py.` at a sentence end | the protected word is `main.py`; the final `.` is gap |
| `e-mail` / `email` | HYPHEN only with a `-` joiner; joins without a hyphen only via TERM |
| `delete the word not` → `delete the word "not"` | quotes are gap material → allowed. The negation is kept |
| `I don't know` used as a filler | content, not a filler: it is kept (the bench's `r-filler-1`) |
| `there is no wait time` | `no wait` is not set off and has no matching repair → no RETRACT |

### 5.7 Fit with hark

- **One shared module.** Server and client each need the LPI aligner, which makes two callers. Today the files are
  mirrored "keep the two in step"; a single shared module removes that duplication.
- **Protocol.** The server returns edits as now, each labelled with the operation that licensed it. The client applies
  them and runs the LPI on the *whole* result, then projects; it does not drop groups. ADR-001 #6 still holds: the
  client is authoritative.
- **What goes.** The LPI replaces `_INVARIANTS`, `classify`, the `GROUP_GAP_WORDS` grouping and most of
  `_global_blame`.
- **What stays.** The protected spans, the language guard, the budget and the timeouts. The negation and digit rules
  become assertions, because operation licences already imply them.

## 6. Self-corrections: rules or the model?

**What rules do well.** A rule-based resolver (cue lexicon + rough copy + class match) is precise on common patterns,
transparent and free. It fails on the *scope* of ambiguous corrections, on restarts without a cue and on cues that are
also content words (`actually`, `no`). An LM gets the scope right but can (a) keep the wrong value (ReFyneLabs v1) or
(b) delete too much (DRES). The two combine well.

**Deterministic scoring.** For each cue site c (the interregnum is the cue plus optional fillers), take the repair r
after it. Then score each possible reparandum start a in [c−8, c−1]:

- +3 for a rough copy (`key(w_a) == key(r₀)`);
- +2 for a class match between `w_{c−1}` and the head of r;
- +1 when the cue is set off by punctuation;
- −2 when the window crosses a sentence boundary, unless a class match holds;
- −1 when it would drop a negation that r does not contain.

Resolve when the best score is ≥ 3 and unique. Otherwise leave the text as it is. Wispr does the same: "ambiguous
replacements preserve both versions".

| Input | Rule-based result | Why |
|---|---|---|
| `I want it written in C, uh no, in Rust.` | drops `in C, uh no,` | rough copy `in`, set off |
| `Run the tests on Tuesday, no wait on Wednesday.` | drops `on Tuesday, no wait` | rough copy `on` |
| `the meeting is on Tuesday I mean Thursday` | drops `Tuesday I mean` | class: weekday |
| `the answer is forty two no sorry forty three` | after ITN, drops `42 no sorry` | class: number |
| `let's do coffee at 2 actually 3` | drops `2 actually` | class: number |
| `red one no the blue one actually the green one` | partial; needs the model | the scope covers two cues |
| `I actually enjoyed the movie` | none | no copy, no class, not set off |
| `There is no wait time` / `No freaking way` | none | no repair match / no reparandum |

**Recommendation: the model chooses inside rule-based windows.**

1. The lattice opens RETRACT and RESTART windows only at cue sites and rough copies. The model chooses whether to
   delete and where in the window to start.
2. **The model cannot keep the wrong value.** Deletion can end only at the end of the interregnum, so the repair
   cannot be dropped and the reparandum kept.
3. **Veto.** When a model deletion gets a negative rule score, it is not applied. This prevents over-deletion.
4. **Confidence.** At the branch token (`C` vs `Rust` after `in`), the logprob margin from llama.cpp `n_probs` is
   available. A deletion with a small margin is not applied, because precision matters more than recall here.
5. **Uses of the rule-based resolver.** It runs alone when the LM times out. It is also the oracle that labels
   synthetic data and the regression tests.

## 7. Rich punctuation and profiles

| Switch | terminal | chat | prose |
|---|---|---|---|
| line/paragraph breaks, lists | no | no | yes (lists ≥3 items) |
| em-dash / en-dash | no | off by default | **on** (user request) |
| parentheses, colon, semicolon | yes | yes | yes |
| quotation marks | straight | curly | curly |
| ellipsis (trailing off, `etc etc`) | no | yes | yes |
| final period | yes for sentences, never after commands | not added (existing rule) | yes |
| text that is itself a command (`git rebase -i HEAD~3`) | untouched | – | – |

Wispr turns formatting off entirely for coding-agent terminals. hark's terminal profile should stay conservative too.

## 8. Constrained decoding that enforces the LPI

### 8.1 The automaton

States are input word positions, plus sub-states for ITN and TERM. From state i the model can:

- emit a casing variant of `w_i`;
- skip `w_i` if a licence allows it (FILLER, REPEAT, DISCOURSE);
- **jump** from i to the repair start of a licensed RETRACT, RESTART or CANCEL window;
- emit a licensed ITN or TERM form and advance over its span;
- emit a gap.

The automaton is right-linear (a regular language) with O(n·K) states. Paths that share a prefix (rough copies) stay
ambiguous until they diverge, and the parser keeps both alive.

### 8.2 Sketch for the user's example (GBNF; not tested)

```
root ::= w1
w1  ::= "I" g w2
w2  ::= [Ww] "ant" g w3
w3  ::= [Ii] "t" g w4
w4  ::= [Ww] "ritten" g w5
w5  ::= [Ii] "n" g w6 | w9          # RETRACT window: skip "in C, uh no," and resume at the repair "in"
w6  ::= [Cc] g w7
w7  ::= [Uu] "h" g w8 | w8          # FILLER: optional
w8  ::= [Nn] "o" g w9
w9  ::= [Ii] "n" g w10
w10 ::= [Rr] "ust" e
g   ::= cl? cl? ( " " | " — " ) op?   # simplified gap; prose adds "\n", "\n\n", list markers
e   ::= cl? cl?
cl  ::= [,;:.?!…)”’"']
op  ::= [(“‘"']
```

Both `w5` and `w9` start with `in`. The model's next token (`C` or `Rust`) decides whether it deletes. That one
token's logprob margin is the confidence in §6.

### 8.3 Engines and cost

| Engine | Fit for hark |
|---|---|
| llama-server, `grammar` per request (GBNF) | simplest; hark already runs llama-server on macOS. GBNF cost over a 151k Qwen vocabulary each token is **[U]**: measure it. Use the raw `/completion` endpoint with the literal S1 template from the model card. Whether `/v1/chat/completions` passes `grammar` through is [U] |
| llama.cpp + llguidance | about 50 µs per token mask; needs a build with Rust. Preferred if GBNF is slow. The Lark-style grammar must avoid empty lexemes [U] |
| MLX `logits_processors` | for an MLX backend; plug in llguidance or a small NFA over the tokenizer trie |
| vLLM/SGLang + XGrammar | if the RTX 3060 path moves off llama.cpp |

### 8.4 Pitfalls

- **Tokenization.** Constrain at the *character* level and allow every tokenization. Do not force a canonical one,
  because that hurts accuracy (DOMINO). Watch tokens with a leading space and multi-character tokens such as `.\n\n`.
- **Distortion.** When a stock model "wants" to write `going to`, the mask forces it down an unusual path. Its
  punctuation there may suffer. Track the **intervention rate**: steps where the mask changed the argmax. Fine-tuning
  on LPI data (§11) should push it below 1%.
- **Counting.** Balanced quotes and brackets need counting, so they are verified afterwards (§5.3), or the grammar
  allows only bounded nesting.
- **Hybrid models.** Qwen3.5's small models are a Gated-DeltaNet hybrid (in llama.cpp `qwen35` since 2026-02).
  Speculative decoding has to roll back recurrent state. Support for that combined with grammars is [U]; test it.

### 8.5 Speed

Most tokens are forced copies of the input, so drafting from the input (prompt lookup, or hark's `ngram-simple`) is
accepted almost every time. Inside a word the grammar fast-forwards (llguidance). One forward pass that reads the
logits at every input position (the FPOD idea) is a research option for the tagger-like path.

## 9. Models and latency

hark's measurements (M5 Pro, llama.cpp, Q4_K_M):

- S1-mini (0.6B): **p50 42 ms, p95 78 ms** on short cases. Real dictations: p95 281–312 ms. The **929-char case took
  542 ms**, about 2.5 ms per output token in effect.
- Qwen3-1.7B (edits engine, JSON output): p50 179 ms.

At about 2.5 ms per token, **150 ms is about 60 output tokens, or about 45 words**, for the 0.6B. A 1.7B is roughly
2.5–3× slower [U, scaling estimate]: about 18 words.

| Option | Size | Evidence | Role |
|---|---|---|---|
| S1-mini Q4_K_M (current) | 0.6B | numbers above; strong self-correction; rich punctuation undocumented | P0/P1 generator |
| S1-mini Q8_0 | 0.6B | 4-bit fusing broke self-correction in an analogous fine-tune (ReFyneLabs). Q8 costs about 1.7× the weight bytes [U on latency] | measure in P0 |
| Fine-tuned S1-mini / Qwen3-0.6B / Qwen3.5-0.8B | 0.6–0.8B | Rosehill fine-tuned S1-mini under its license. [Unsloth](https://www.unsloth.ai/docs/models/qwen3.5/fine-tune): Qwen3.5-0.8B LoRA in 3 GB | **target default** |
| Qwen3-1.7B / Qwen3.5-2B | 1.7–2B | ReFyneLabs 2B 8-bit: 214 ms per utterance (M3 Ultra) | quality tier for short dictations; zero-shot rich punctuation *under the grammar* in P1 |
| Gemma 3 1B, [Gemma 4 E2B](https://huggingface.co/blog/gemma4) | 1–2B eff. | Gemma 3 270M needed fine-tuning to stop changing text (Rosehill) | candidates; [U] latency |
| Qwen3.5-4B/9B, Phi-4-mini 3.8B | 4–9B | over budget [U] | offline teachers only |
| ModernBERT-base / DeBERTa-v3 tagger | 150–180M | Mów: 5–50 ms per sentence (int8, Apple Silicon) | **challenger/fallback** (§11.6) |
| MiniLM-L12 tagger | 33M | paragraph F1 0.85; beat a 270M generator | paragraph head |
| deepmultilingualpunctuation, punct_cap_seg_47 | XLM-R | few marks; no Czech in the 47 | baselines only |

Two ways to fit long dictations into the budget:

1. **Chunk.** Split at sentence boundaries that have no cue in the next sentence's first 3 words, and decode the
   chunks in parallel (`-np 4`, continuous batching). A small model's decode is memory-bound, so a batch of 4 costs
   little more than 1 [U].
2. **Stream.** Clean finished sentences while the user is still talking, keeping one sentence of look-back for
   corrections that cross a boundary. After the key is released, only the tail is left. The RTX 3060 has roughly the
   memory bandwidth of the M-series Pro chips [U]; measure it.

## 10. Recommended architecture

```
Parakeet text (+ word timings) ──► tokenizer + protected spans (existing) + context terms
                                          │
                                          ▼
                              lattice builder (deterministic)
     fillers · repeats · cue windows · rough copies · class matches · ITN · TERM · spoken punctuation
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
   LM under per-request grammar (llama.cpp)          deterministic fallback
   S1-mini → hark fine-tune 0.6–0.8B                 (lattice + §6 resolver)
   output + derivation + branch logprobs             on timeout or error
                  └───────────────────────┬───────────────────────┘
                                          ▼
               LPI verifier / projector (shared module; the client is authoritative)
                                          ▼
                    recaser (§5.4) → profile style pass (§7) → paste
```

- **Failure policy.** Violations are projected region by region. The raw text is pasted only when a global guard
  fails. History keeps the derivation (the operation for each change), so the user and the eval can see why each
  change happened.
- **Optional heads.** Paragraph breaks can come from a 33M tagger when the LM under-splits. Pause lengths from Parakeet
  timestamps can feed the lattice as hints for interruption points (P4).

## 11. Training plan

### 11.1 Data sources

- **hark history.** Real Parakeet output, private and local. Label it with a *local* teacher on the H100s, for example
  Qwen3.5-122B-A10B [U on quality]. Keep about 300 human-reviewed cases as **gold eval only**.
- **Public spontaneous speech.** AMI (CC BY 4.0), DisfluencySpeech (check the license), and Switchboard/Fisher if
  licensed. LibriSpeech-PC for punctuation eval.
- **Synthetic dictation.** It is the main training source (§11.2).

### 11.2 Generating data under the invariant

1. **Clean targets.** A strong LLM writes realistic dictated content in hark's registers: coding-agent prompts, chat,
   email, notes and docs. The text uses natural rich punctuation, lists and paragraphs. The targets are the ground
   truth C.
2. **Spoken form.** Remove punctuation and paragraphing and verbalize numbers (NeMo TN). Optionally go through TTS
   and Parakeet so the punctuation, casing and word errors look like real ASR [U on the TTS choice]. This gives S₀.
3. **Disfluencies with labels** (LARD-style plus LLM paraphrase of the reparanda):
   - fillers and discourse fillers;
   - repeats and fragments;
   - RETRACT with `{wrong, right, retained}` labels, over ≥ 30 cue variants and value classes: numbers, times, days,
     names, files, flags, languages;
   - RESTART, CANCEL, `etc etc`, trailing off and spoken punctuation commands.
   
   This produces S. Corrections that *change values* are injected explicitly, which avoids ReFyneLabs v1's
   clean-anchored blind spot.
4. **Check by construction.** Assert `LPI(S, C)`. Drop any pair that fails.
5. **Hard negatives:**
   - cue words used as content (`I actually liked it`, `there is no wait time`, `I mean it`, `No freaking way`,
     `tea or coffee`, `three or four days`, `sorry for the delay`);
   - commands and identifiers;
   - prompt injections;
   - Czech text, which must be left alone.
6. **Teacher labels for real text.** Two or three teachers each produce a cleaned version. Keep the edits they agree
   on (edit-level majority voting reduces over-correction), project the result onto the LPI, and spot-check it.

**Mix** (about 50k examples): **40% no-op or punctuation-only**, 25% fillers/repeats, 20% self-corrections (split so
that test templates do not occur in training), 10% rich structure (lists, quotes, parentheticals, dashes, paragraphs,
email) and 5% adversarial. Each input carries a control line with the profile and style switches (§7).

### 11.3 Training recipe

| | |
|---|---|
| Base candidates | S1-mini (keeps the task prior; license: credit "S1-mini" by "Superwhisper", keep LICENSE/NOTICE), Qwen3-0.6B, Qwen3.5-0.8B; Qwen3-1.7B or Qwen3.5-2B for the quality tier |
| Method | full fine-tune for ≤ 1B (cheap on an H100) and LoRA r = 32–64 on all linear layers for comparison [U which is better]. Loss on completions only. lr 1e-4–2e-4 cosine, 1–3 epochs, seq 1024 |
| Compute | about 20M tokens per epoch. Minutes to an hour per run on 1× H100 [U, estimate]. Data quality, not GPU time, is the bottleneck |
| Checkpoint choice | by the **behavioural gates** in §12, not val loss (ReFyneLabs saw behaviour change at random points while val loss was flat) |
| Optional | DPO with LPI-conforming good outputs vs over-editing or wrong-value bad ones; or RL with the LPI checker plus a reference match as reward |

### 11.4 Quantization and export

Export GGUF at Q8_0, Q6_K and Q4_K_M. Evaluate **self-correction and inversion per quantization** before choosing:
ReFyneLabs found the drop is silent. Never fuse a LoRA into a 4-bit base.

### 11.5 Prompt format

Keep a fixed system line and a control line, as S1 does. After fine-tuning, values outside S1's set are safe. Thinking
stays off.

### 11.6 Tagger challenger

The same data, with labels taken from the LPI derivation:

- per word: KEEP, FILLER, REPEAT, REPARANDUM, INTERREGNUM, SPOKEN_PUNCT;
- per gap: a Pogoda-style class, about 25–30 classes including `—`, `(`, `)`, quotes, `:`, `…`, `\n\n` and list
  markers;
- ITN stays a separate deterministic pass.

Train ModernBERT-base and MiniLM-L12. It is invariant by construction and runs in 5–50 ms. It is the fallback when the
LM times out. If it wins on punctuation F1 (as for paragraph breaks), a hybrid can use the tagger for gaps and the LM
for deletions.

## 12. Evaluation plan

**Sets:**

- hark's 44 handwritten and 29 real cases.
- **About 300 gold real dictations** with rich-punctuation references and labelled corrections.
- About 1,500 synthetic held-out cases, with self-correction templates that do not occur in training.
- SpeakoFlow bench, after checking its license.
- LibriSpeech-PC for punctuation.
- A DRES-style Switchboard subset if LDC data is available.

| Metric | Definition | Gate (for promotion) |
|---|---|---|
| LPI residual | violations after projection | **0** |
| Raw LPI violations | utterances whose generation needed projection | 0 when constrained; < 1% unconstrained after fine-tuning |
| Intervention rate | grammar steps that changed the argmax | < 1% after fine-tuning |
| Over-deletion (damage) | fluent words deleted / fluent words | ≤ 0.3% |
| Deletion E/Z-scores | P/R/F1 per INTJ / EDITED / PRN / repeat | report; ≥ S1-mini |
| Self-correction | exact match of the retained text; **inversion rate**; false triggers on cue words used as content | ≥ 95% held-out; inversion 0 on regression |
| Punctuation | per-mark P/R/F1 on aligned gaps (strict class + lenient); rich marks reported separately; quote/bracket balance | `, . ?` ≥ S1-mini; rich-mark precision ≥ 0.85 |
| Structure | paragraph-break F1; list precision (an unrequested list is an error) | break F1 ≥ 0.8; list precision ≥ 0.95 |
| No-op preservation | clean inputs with zero word changes and no punctuation lost | ≥ 99% |
| Casing / ITN / TERM | accuracy per class | report |
| Latency | p50/p95/p99 by length bucket, on the M5 Pro and the RTX 3060 | p95 ≤ 150 ms for ≤ 45 words |
| Human preference | blind pairwise vs current hark on 200 real dictations, 2 raters: "reads better" and "meaning changed" separately; κ reported; an LLM-judge ensemble calibrated against them for scale | ≥ 60% wins among non-ties; meaning changed ≤ 1% |

## 13. Phased plan

| Phase | Work | Exit criteria |
|---|---|---|
| **P0** (≈1 wk) | Shared LPI module (tokenizer, ops, aligner, projection, recaser) replaces the kind validators on server and client. Wider gap alphabet. Cue lexicon that allows fillers inside the interregnum, a lone `no`/`actually` gated by licence, and `make that` / `never mind`. Eval: §12 metrics, a first gold set. Experiments: **E1** S1 input = raw vs punctuation stripped vs lowercase and unpunctuated (its training form); **E2** `[Structure: lists]` and `[Context: email]` for prose; **E3** Q4_K_M vs Q8_0 | no regression on 44 + 29; `in C, uh no, in Rust` passes; long dictations keep their punctuation edits; LPI residual 0 |
| **P1** (1–2 wk) | Per-request grammar from the lattice; GBNF first, then llguidance if slow. **E4** stock S1-mini under the grammar: quality, overhead, intervention rate. **E5** zero-shot rich punctuation under the grammar with Qwen3-1.7B, Qwen3.5-2B and Gemma 4 E2B plus a style prompt; the grammar removes the risk of paraphrase. **E6** rule-based resolver alone vs S1 on the self-correction set | 0 raw violations; overhead ≤ 10% of decode; decision on the generator for P2 |
| **P2** (≈3 wk) | Data pipeline (§11.2), about 50k examples plus the gold set; fine-tune 2–3 bases; quantization study; checkpoint choice by gates | beats S1-mini on rich punctuation F1 and human preference; inversion 0; all §12 gates |
| **P3** (≈2 wk) | Tagger challenger; chunked parallel decode; cleanup of finished sentences during dictation; RTX 3060 numbers | p95 ≤ 150 ms after key release for typical dictations; tagger kept as fallback or promoted |
| **P4** (open) | Pause features from Parakeet timestamps; per-user style learned from edits (the history already holds the raw text and the dropped edits); Czech | – |

## 14. Risks and open questions

- **Meaning.** Deleting discourse fillers (`kind of`, `like`) can remove hedging. DISCOURSE deletions are budgeted
  and logged, and the human eval asks about meaning separately.
- **The cue `actually`.** It is the riskiest cue. Without a rough copy or a class match it should almost never fire.
- **Grammar cost on large vocabularies.** GBNF with Qwen's 151k vocabulary; whether llama.cpp supports
  hybrid-architecture speculation together with a grammar is [U]. Measure before committing.
- **Taste in punctuation.** Em-dashes and ellipses are a style choice. Keep them as profile switches, off for terminal.
- **Licenses.** S1-mini has a naming clause. Some community data is non-commercial (DailyDialog in stillerman's
  model), and Switchboard needs LDC. Use AMI and LibriSpeech-PC (CC BY 4.0), synthetic data and the user's own
  history.
- **Missed ASR errors.** The LPI deliberately keeps misrecognitions (except TERM merges). Quality there depends on
  hark's context biasing, which is the right layer.

## Sources

hark: `README.md`; `docs/bench/2026-11-11-cleanup-s1.md`; `server/src/hark_server/cleanup.py`;
`client/src/hark_client/cleanup.py`.

Industry: [S1-mini card](https://huggingface.co/superwhisper/s1-mini) · [S1 blog](https://superwhisper.com/blog/s1) ·
[Superwhisper modes](https://superwhisper.com/docs/modes/built-in) ·
[Super-mode prompt gist [U]](https://gist.github.com/nandcello/410934d1251d58cb865634d3d429b4ef) ·
[Wispr technical challenges](https://wisprflow.ai/post/technical-challenges) ·
[Wispr on Baseten](https://www.baseten.co/resources/customers/wispr-flow/) ·
[Wispr Smart Formatting & Backtrack](https://docs.wisprflow.ai/articles/5373093536-how-do-i-use-smart-formatting-and-backtrack) ·
[Wispr Android formatting](https://docs.wisprflow.ai/articles/6568835559-why-wispr-flow-sometimes-removes-or-changes-words-on-android-smart-formatting) ·
[Wispr languages](https://wisprflow.ai/research/supporting-languages) · [Wispr Canto 1](https://wisprflow.ai/canto-1-results) ·
[Aqua Avalon](https://aquavoice.com/guide/how-to-use-avalon) · [Aqua HN launch](https://news.ycombinator.com/item?id=39828686) ·
[Aqua site [U]](https://aquavoice.com/) ·
[Google disfluency blog](https://research.google/blog/identifying-disfluencies-in-natural-speech/) ·
[Teaching BERT to Wait](https://arxiv.org/abs/2205.00620) ·
[Gemini 3.5 Transcribe](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5-transcribe/) ·
[Gboard voice typing](https://support.google.com/gboard/answer/11197787) ·
[MacRumors iOS 27 dictation](https://www.macrumors.com/2026/06/22/advanced-ai-dictation-not-enabled-by-default/) ·
[TechCrunch Apple dictation](https://techcrunch.com/2026/06/08/apple-introduces-systemwide-dictation/) ·
[EnviousWispr Apple LoRA](https://enviouswispr.com/blog/fine-tuning-apple-on-device-model-self-corrections/) ·
[S1-ASR-Cleanup](https://blog.danielrosehill.com/posts/s1-asr-cleanup-dictation-transcript-cleanup-y73m4i/) ·
[Gemma 3 270M paragraphs](https://blog.danielrosehill.com/posts/asr-paragraph-breaks-gemma-3-270m-85hckk/) ·
[MiniLM paragraphs](https://blog.danielrosehill.com/posts/update-minilm-asr-paragraph-breaks-8hlzau/) ·
[ReFyneLabs simplewords v2](https://huggingface.co/ReFyneLabs/simplewords-dictation-cleanup-v2) ·
[stillerman deletion tagger](https://huggingface.co/stillerman/fdt-disfluency-mini-11m) ·
[Mów classifier](https://huggingface.co/sam-castro/mow-disfluency-classifier) ·
[SpeakoFlow bench (snippet)](https://huggingface.co/datasets/SpeakoFlow/dictation-cleanup-bench).

Literature: [Shriberg ICSLP](https://asel.udel.edu/icslp/cdrom/vol3/1031/a1031.pdf) ·
[Shriberg, Bear, Dowding 1992](https://aclanthology.org/H92-1085.pdf) ·
[Ginzburg et al. 2014](https://clp.ling.uni-potsdam.de/publications/Ginzburg-2014-1.pdf) ·
[Johnson & Charniak 2004](https://aclanthology.org/P04-1005/) · [Jamshid Lou & Johnson 2020](https://arxiv.org/abs/2004.05323) ·
[Rocholl et al. 2021](https://arxiv.org/abs/2104.10769) · [DRES](https://arxiv.org/abs/2509.20321) ([code](https://github.com/mariateleki/dres)) ·
[Z-Scores](https://arxiv.org/abs/2509.20319) · [LARD](https://arxiv.org/abs/2201.05041) · [Disfl-QA](https://arxiv.org/abs/2106.04016) ·
[DisfluencySpeech](https://arxiv.org/html/2406.08820) · [AMI license](https://groups.inf.ed.ac.uk/ami/corpus/license.shtml) ·
[Pogoda & Walkowiak 2021](https://aclanthology.org/2021.findings-emnlp.393.pdf) · [Alam et al. 2020](https://aclanthology.org/2020.wnut-1.18.pdf) ·
[Zhong & Sun 2025](https://doi.org/10.1109/icassp49660.2025.10889374) · [FPOD](https://arxiv.org/abs/2408.11845) ·
[LibriSpeech-PC](https://arxiv.org/abs/2310.02943) ·
[NeMo P&C lexical-audio](https://docs.nvidia.com/nemo-framework/user-guide/24.12/nemotoolkit/nlp/punctuation_and_capitalization_lexical_audio.html) ·
[deepmultilingualpunctuation](https://github.com/oliverguhr/deepmultilingualpunctuation) ·
[punct_cap_seg_47](https://huggingface.co/1-800-BAD-CODE/punct_cap_seg_47_language) ·
[NeMo TN/ITN](https://developer.nvidia.com/blog/text-normalization-and-inverse-text-normalization-with-nvidia-nemo/) ·
[Neural ITN](https://arxiv.org/abs/2102.06380) ·
[Thutmose](https://www.isca-archive.org/interspeech_2022/antonova22_interspeech.pdf) · [WFST+LM fusion](https://arxiv.org/abs/2203.15917) ·
[Sakaguchi et al. 2016](https://aclanthology.org/Q16-1013/) · [Coyne et al. 2023](https://arxiv.org/abs/2303.14342) ·
[Fang et al. 2023](https://arxiv.org/abs/2304.01746) · [Penteado & Perez 2023](https://arxiv.org/abs/2306.15788) ·
[Staruch et al. 2025](https://aclanthology.org/2025.bea-1.9/) · [Omelianchuk et al. 2024](https://aclanthology.org/2024.bea-1.3/) ·
[Karpo & Chernodub 2026](https://aclanthology.org/2026.findings-emnlp.1049/) · [Edit-level voting 2026](https://aclanthology.org/2026.bea-1.60.pdf) ·
[Kudryavtsev et al. 2026](https://arxiv.org/abs/2610.30385) · [LaserTagger](https://aclanthology.org/D19-1510.pdf) ·
[GECToR](https://aclanthology.org/2020.bea-1.16/).

Decoding and models: [llama.cpp GBNF](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md) ·
[llama.cpp llguidance](https://github.com/ggml-org/llama.cpp/blob/master/docs/llguidance.md) ·
[PR #4306](https://github.com/ggerganov/llama.cpp/pull/4306) · [PR #17937](https://github.com/ggml-org/llama.cpp/pull/17937) ·
[llguidance](https://github.com/guidance-ai/llguidance) ·
[XGrammar](https://blog.mlc.ai/2024/11/22/achieving-efficient-flexible-portable-structured-generation-with-xgrammar) ·
[Koo et al. 2024](https://arxiv.org/abs/2407.08103) ·
[Cognetta et al.](https://openreview.net/notes/edits/attachment?id=PDbSZ14Qqo&name=pdf) ·
[DOMINO](https://proceedings.mlr.press/v235/beurer-kellner24a.html) · [Grammar-Aligned Decoding](https://arxiv.org/abs/2405.21047) ·
[Lost in Space](https://arxiv.org/abs/2502.14969) · [Prompt lookup decoding](https://github.com/apoorvumang/prompt-lookup-decoding) ·
[mlx-lm generate](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/generate.py) ·
[Qwen3.5 small models](https://artificialanalysis.ai/articles/qwen3-5-small-models) ·
[llama.cpp Qwen3.5 support](https://github.com/ggml-org/llama.cpp/commit/39bf692af1cba2a1072e4a42425611bf1ec2807d) ·
[Gemma 4](https://huggingface.co/blog/gemma4) · [Unsloth Qwen3.5 fine-tuning](https://www.unsloth.ai/docs/models/qwen3.5/fine-tune).
