# Robust context pipeline for hark: sourcing, retrieval, safety, evaluation

Status: proposal, 2026-11-12. Scope: the client context engine (`client/src/hark_client/context/`), how
`pipeline.py` uses it, and the hark.v1 fields it relies on (`proto/hark/v1/hark.proto`, ADR-001).

Inputs: `README.md`, `docs/00-context.md`, `docs/protocol/ADR-001-protocol.md`, `docs/dataset/spec.md`, and the
current `context/*.py` (read-only). Two experiments run in parallel and are **not** repeated here:

- **capture-capability (macOS):** which sources each app actually exposes. Its results fill the provider matrix
  (§5.3, cells marked `cap-exp`).
- **offline ranking ablations on agent sessions:** these tune the prior ranker weights, list sizes and thresholds.
  Values that come from that work are marked `abl`.

This document defines the contracts both experiments plug into.

`[U]` marks something unverified: a guess, a number still to be measured, or a source that was only read through
a search summary. Sources are cited as [Sn] (list at the end). Unless marked otherwise, every source was read at
the URL on 2026-11-12.

---

## 0. Decisions on one page

1. **Gather context continuously.** A context daemon listens to focus and title events (Hyprland `socket2`; macOS
   `NSWorkspace` plus `AXObserver`) and to file events (`.git`, agent session directories). It keeps a warm
   candidate pool for each window. At key-down it only refreshes the *volatile* sources (selection, text near the
   caret, visible terminal text) within a hard ~80 ms budget [U]. That refresh never delays `SessionStart`.
2. **Rank relevance in two stages.**
   - A **prior ranker** (no audio) builds a pool of ≤ 5,000 candidates and a small **seed** list of ≤ 30.
   - During speech, **phonetic retrieval** runs against the server's partial hypotheses. It picks out the terms the
     user is actually saying, and the client sends a refined list in a `ContextUpdate`.
   - The final list has ≤ 50 terms, or ≤ 10 for prompt-based backends (Qwen3-ASR).
3. **Check every biased change against an unbiased anchor** (same audio, no context). A biased span survives only
   if the anchor heard something phonetically close *and* the term has evidence that hark did not produce itself.
   Otherwise the anchor's words come back. Differences outside bias-hit spans are reverted.
4. **Give every term provenance.** Each term carries its source, evidence span, age, caret distance and provider
   confidence from capture through paste. A per-dictation trace is logged, and that trace is what the dataset's
   `shortlist_used` records.
5. **Privacy defaults:**
   - Deny-listed apps and secure fields yield no context.
   - Secrets are scrubbed *before* term extraction.
   - Only terms leave the client, never raw text.
   - Clipboard is off. When the user opts in, it stays local (canonicalization only) unless a provider policy allows
     sending it.
   - OCR is opt-in, and the image is discarded after OCR.
6. **Evaluate per component** on the dataset variants (`missing`, `correct`, `missing_partial:<src>`, `stale`,
   `unrelated`, `adv_*`, `forced_list`), using:
   - an entity **funnel**: pool → seed → retrieved → sent → hit → exact;
   - pre-registered ship gates on ACEC, FCS_induced, item damage and p95 latency.

---

## 1. The current code

What it already does well:

- **Robust collection.** `collect()` never raises. Each provider has its own timeout:
  - focus 150 ms, terminal 200 ms, repo 500 ms, agent 300 ms;
  - all clipped to the overall deadline (`context.deadline_ms = 150`).

  A provider that misses the deadline keeps running so the repo cache gets warm. That cache is keyed by
  (HEAD, index mtime) with a 30 s TTL.
- **Agent session matching with a confidence score.** Scores run from 0.2 to 0.95. Ambiguity multiplies the score
  by 0.7, and only matches ≥ 0.7 are used. Only assistant turns and tool-call paths are read.
- **Conservative canonicalization** (`canon.py`). It matches exact whole-chunk aliases only, never crosses a
  clause, accepts single-word aliases only for "informative" canonicals, skips ambiguous aliases, and requires word
  confidence ≥ 0.5. This already avoids the unchecked fuzzy swaps that Handy measured as harmful [S7].
- **Late context is not wasted.** Context that arrives after `SessionStart` is still used for canonicalization,
  profile choice and the focus check.

Gaps this design closes:

| # | Gap | Effect |
|---|---|---|
| G1 | Capture happens only at key-down, and `SessionStart` waits up to 150 ms for `collect()`. There is no prefetch. | The first press after a window switch pays the cold costs (psutil tree walk, `git ls-files`, 4 MB tail read of the session file) or gets no list at all. |
| G2 | One shot. The list is fixed at `SessionStart`: no partials, no `ContextUpdate`, no retrieval against what is being said. | Static weights fill 60 slots. Repo-wide files (up to 300 files plus directories) crowd out the few terms that matter. |
| G3 | `Term.source` keeps only the strongest source. There is no evidence span, age or caret distance, and history logs only the term *count*. | Wins and failures cannot be attributed. The dataset's `shortlist_used.sources` cannot be filled faithfully. |
| G4 | The anchor is never requested explicitly (the server default is `IF_CHEAP`), and the client ignores both the anchor and `bias_hits`. | No protection against false contextual substitutions. |
| G5 | No app deny-list and no secure-field check before `AXSelectedText` is read. `scrub()` covers only the agent reply, not the selection or window title. There is no term-level secret filter. | Possible leakage of secrets into bias lists or logs. |
| G6 | `FOCUSED_TEXT` and `TERMINAL_TEXT` exist in `TermSource` but have no provider. Linux has no selection source. | The strongest "near the caret" evidence is missing [S2][S7]. |
| G7 | The focus re-check before paste runs the full `collect()` (terminal walk, repo, agent) under a 100 ms deadline. | Wasted work and threads on the latency-critical path. A focus-only probe is enough. |
| G8 | hark's own output re-enters context: pasted text sits in focused fields and scrollback, and agent user turns are dictations. Nothing marks it. | Once text sources are added, an error can reinforce itself (§7.4). |

---

## 2. How products source context

| Product | What is read | When | Where it goes | Deadline behavior | Privacy | Src |
|---|---|---|---|---|---|---|
| **Aqua Voice "Deep Context"** | One screenshot of the screen ("reads the whole screen you're on"). Words you might say (names, code identifiers such as `useTaskStore`) are picked out of it. | Taken as dictation starts; read *while you talk*. | Per-dictation words added to the persistent Dictionary. | "If it isn't done when you stop, your text goes in without it." | Off until enabled. The image is not kept. Privacy Mode also redacts the words found. Admin controls. | [S1] |
| **Wispr Flow "Context Awareness"** | Textbox before, selected and after the cursor; on-screen text; recipient names; variable and file names in coding apps; app category; website from the address bar. Variable recognition needs VS Code's *Screen Reader Optimized* mode. File tagging uses trigger words ("tag", "at"). | Per dictation. Switches between apps are "picked up right away". | Sent with the dictation to the cloud (unless Privacy Mode). | "Context reading is skipped for any dictation where it cannot complete quickly." | Works from text, no screenshots. Excludes password fields (custom or web ones may slip through), numeric-only fields, URL bars, and placeholder/hint text. Banking apps are skipped. Context is cleared when the dictation ends. | [S2][S3] |
| **Superwhisper Super/Custom modes** | Active app and the text of its input field; selected text; clipboard copied ≤ 3 s before or during dictation. With app context it also adds date/time, the user's full name and the computer name. | At dictation. | LLM post-processing prompt (`Application Context`, `Selected Text`, `Clipboard Context`). | — | History shows "everything that was sent to the AI". | [S4] |
| **VoiceInk** | Selected text (AX), clipboard, OCR of the active window (Apple Vision). | In-memory snapshot at recording start. | AI enhancement prompt. Mode switches control what is *included*, not what is read. | — | The screenshot is not sent. History stores request metadata, including the context. | [S5][S6] |
| **Handy Context** (Handy fork) | Window title; selection; last 8,000 chars of the focused field; front-window AX tree walked breadth-first, stopping at 4,000 elements, 30,000 chars or 350 ms. | Key-down. Waits ≤ 600 ms after key-up. | ≤ 1,000 terms to a local Parakeet server. Up to 9 decodes; the winner is chosen by unbiased log-likelihood plus a bonus per term. "Checked fixes" follow. | Capture deadline. | Local only, no screenshots. | [S7] |
| **Deepgram keyterms** (API reference point) | — | — | ≤ 500 tokens. "Focus on the most important 20–50 terms." Mid-stream replacement via `Configure`. | — | Keyterms appear in usage records unless `redact_usage=true`. | [S8] |

What follows from this:

- **Two families.** ASR-side products (Aqua, Handy, Deepgram) put vocabulary into decoding. LLM-side products
  (Superwhisper, VoiceInk) pass context to a post-processor. hark is ASR-side by design (`00-context.md`).
- **Everyone treats context as best-effort with a deadline.** Nobody blocks dictation on it.
- **Text beats pixels.** Wispr and Handy read AX text. Aqua and VoiceInk do OCR and discard the image.
- **Only Handy publishes damage numbers.** They are the most useful single datapoint:
  - Screen words wrong: 5.83 % → 3.81 % (current slide) and 4.07 % (whole talk).
  - Overall WER: 8.76 → 8.80 (current slide), 8.87 (whole talk) and 8.78 (another talk's slide). The whole-talk
    increase of +0.11 failed the author's own +0.15 CI bar.
  - VoiceInk's fuzzy swap pushed WER from 8.76 to **10.70** with a whole talk on screen. About half of its bad swaps
    only changed a word ending ("career" → "Careers").
  - Qwen3-ASR-1.7B with a 1,000-word list missed list words as often as with none, and ran ~10× slower.
  - A vocabulary prompt made Whisper worse overall (3.53 % → 4.8–5.6 % WER).
  - Trained keyword spotters cut 1,000 terms to ~3 while keeping 98 % of the correct ones [S7].

---

## 3. What the literature says

### 3.1 Screen and slide context

- **ScreenASR-Bench** [S9] tiers evidence into ETE (the keyword is written on screen), EVE (the referent is visible
  but not as text) and ISE (inferred from the scene).
  - For ASR models, average keyword recall rises from 48.18 % to 62.20 %, and WER falls from 6.02 % to 5.19 %.
  - The gain shrinks with semantic distance: recall +26.53 pp for ETE, +6.95 for EVE, +4.39 for ISE.
  - Textualized visuals (captions) match raw images on recall but give lower WER. For Qwen3-Omni: recall
    74.09 vs 74.89 %, WER 4.01 vs 5.49 %.
  - **For hark:** turn every visual source into text terms before use, and rank evidence where the term is written
    verbatim (dataset tier E1) above derived (E2) or semantic (E3) evidence.
- **SlideSpeech** [S10] built bias lists with this pipeline: middle frame → text detection and OCR (MMOCR) →
  KeyBERT → **50 keywords** → contextual ASR with a `<nobias>` token.
  - Test B-WER fell from 12.70 to 9.32, recall rose from 87.43 to 90.86 %, and overall WER moved only from
    12.89 to 12.38 (L95 model).
  - **For hark:** a noisy, automatically extracted per-view list still helps. Report results per list source.
- **Handy** [S7] adds the busy-screen effect: the whole talk's slides help less, and cost more WER, than the current
  slide.

### 3.2 Personal catalogs (contacts, apps, media)

- **Google, shallow-fusion biasing** [S12]. Biasing an "anti-context" set (voice search, no relevant phrases) with
  200 random phrases raised WER from **6.9 to 12.5**. Activating phrases only after mined prefixes ("call", "text",
  "play"), with a lower weight when the prefix is empty, brought it back to **7.3** while keeping the gains on the
  biasing sets.
  - **For hark:** always-on biasing damages unrelated speech, and carrier words are cheap evidence. Wispr's "tag" /
    "at" triggers are the same idea [S3].
- **Amazon, gated contextual adapters** [S13]. Ungated adapters cost −3.0 % relative WER on a general set. A learned
  per-frame gate kept the named-entity gain (≈ 26 % WERR) while cutting the general-set loss to about −1.3 to −1.7 %.
  It ran biasing on < 15 % of frames (< 4 % on general speech). The gate stayed off for easy words: "mom" was listed
  but recognized without bias.
  - **For hark:** decide *where* biasing is allowed to act, not only *what* is in the list.
- **Amazon, Retrieve and Copy** [S14]. ANN retrieval (FAISS) shortlists 20K-entry catalogs with ≥ 20 % per-frame
  speedup. Larger catalogs contain more phonetically similar entries, which degrades recall, so they fine-tune on
  hard negatives from the same phonetic cluster.
  - **For hark:** near-homophone density grows with pool size (all repo symbols), so ranking and the gate must handle
    competition (§6.4 L6, §11 `adv_add`).
- **Apple, VQ retrieval** [S17]. Quantized retrieval grounded on audio shortlists catalogs of thousands of entries:
  up to 71 % relative error reduction on personal entities, and 85–95 % less memory for lists up to 1M.
- **Apple, phonetic retrieval** [S16], ICASSP 2025:
  1. Decode without context and tag named entities.
  2. Retrieve phonetically similar entries by normalized phone edit distance (NPD). Keep an entry if NPD ≤ 1.2 × the
     best, or NPD < 0.2, up to **10** candidates.
  3. Decode again with those candidates as the prompt.

  Results: −30.2 % relative WER and −73.6 % relative NE error. **20 candidates were worse than 10.** Simply
  replacing the entity with the top retrieved candidate was much worse than re-decoding.
  - **For hark:** retrieve from a first-pass hypothesis, keep the list tiny, and let the decoder (acoustics) choose.
- **Apple, RAG correction** [S24] queries a vector DB with possibly wrong ASR text, then an LLM corrects the
  transcript: −33 to −39 % WER on rare music entities, with no regression on STOP. The same retrieval idea, but
  applied post hoc. hark prefers in-decoder biasing (`00-context.md`).

### 3.3 Retrieval before biasing

| Work | What it adds |
|---|---|
| **BR-ASR** [S20] | Speech–bias contrastive retrieval with homophone-aware curriculum. Lists of 200k entries cost only +0.3 / +2.9 abs WER / B-WER, with 99.99 % pruning and 20 ms per query. |
| **CTC-assisted LLM contextual ASR** [S21] | A coarse CTC decode filters the hotwords before prompting. Works with 2,000 bias words. |
| **PTC-Bias** [S19] | Phoneme-CTC posteriors with **temporal competition**: overlapping near-homophone paths compete, and an empty set is allowed. Keeps ≤ 10 words, then a post-decode local comparison replaces a span only if the acoustic margin is ≥ 2.0. B-WER −23 % relative vs CTC-Filter with U-WER nearly unchanged. Retrieval takes 7.6 ms on CPU at N = 2,000. Notes that **hypothesis-based retrieval misses words absent from the first transcription**. |
| **Deferred NAM** [S18] | Selects phrases with a light pass *before* encoding context: 20K phrases with < 33 ms pre-decoding delay. **For hark:** select first, then compile the boosting tree, and cache it by `content_hash`. |
| **CB-Whisper** [S25] [U, abstract only] | Open-vocabulary keyword spotting picks the terms, then only those are prompted. |

### 3.4 Acoustic verification and gating

- **CTC-WS** [S22] (NeMo) spots context terms on CTC log-probs and replaces the greedy span only for valid
  candidates in their frame interval.
- **Handy** [S7] uses two acoustic checks:
  - it scores each candidate decode by its exact RNN-T log-likelihood *under the unbiased model*, plus 8 per term
    word;
  - "checked fixes" are priced against the audio, and a fix that only changes a word ending is vetoed.
- **PTC Correction** [S19] compares the term with the transcript span in the same phonetic context and requires a
  margin.
- **Gated adapters** [S13] and the `<nobias>` token [S10][S13] let the model abstain.

### 3.5 Decode-time boosting infrastructure

- **TurboBias** [S23] is a GPU phrase-boosting tree for CTC, transducer and AED models: up to 20K phrases with no
  noticeable slowdown, in NeMo.
- **TurboBias 2.0** [S23] adds per-stream batched decoding (an independent list per utterance in a batch), a
  case-insensitive graph, and streaming with greedy or beam search. That matches hark's per-utterance lists and
  revisions.

---

## 4. Robustness principles to copy

| # | Principle | Evidence | hark consequence |
|---|---|---|---|
| R1 | **Prefer verbatim text evidence near the user's focus.** | ETE ≫ EVE > ISE; captions beat images [S9]. Current slide beats whole talk [S7]. Text near the cursor [S2]. | Rank by evidence tier, caret/prompt proximity and window scope. No semantic expansion in v1. |
| R2 | **Use short lists, chosen per utterance.** | Anti-context damage 6.9 → 12.5 WER [S12]. 10 retrieved better than 20 [S16]. 20–50 recommended [S8]. k = 30/50 [S7]. K = 10 [S19]. | Seed ≤ 30, final ≤ 50, prompt ≤ 10. |
| R3 | **Retrieve against a hypothesis or the audio before biasing.** | [S14][S16][S17][S18][S19][S20][S21] | Two-pass retrieval on partials (§6.3). |
| R4 | **Let acoustics decide; the list only proposes.** | Unbiased log-likelihood selection [S7]; CTC-WS [S22]; margin check [S19]; gating [S13]. | Anchor gate (§7.1). |
| R5 | **Expect near-homophone competition, growing with pool size.** | Hard negatives [S14]; temporal competition cuts near-homophone recall from 69.3 to 22.0 % [S19]. | Competition groups (§6.4 L6); `adv_add` and `adv_swap` evaluation. |
| R6 | **Never block; meet the deadline, then degrade.** | [S1][S2][S7] | Per-source budgets. Late evidence goes into a `ContextUpdate` or is used for canonicalization only. |
| R7 | **Treat staleness as a first-class property.** | Clipboard counts only if copied within 3 s [S4]; per-dictation capture [S1]. | Every evidence item has `captured_at`, a validator and a focus scope. Age decay. |
| R8 | **Prefetch on focus change.** | App switches "picked up right away"; site identified from the address bar [S2]. Compositor and AX events exist [S26][S27]. | Event-driven daemon (§5.4). |
| R9 | **Make provenance visible to the user and to evaluation.** | History shows what was sent [S4][S5]; per-source metrics [S10]. | Per-term trace (§9). |
| R10 | **Protect privacy by exclusion and minimization.** | Field and app exclusions, context cleared after dictation [S2]; image discarded [S1][S6]; keyterms logged by the vendor [S8]; clipboard markers [S31][S32]. | §8. |
| R11 | **Measure anti-context explicitly.** | Voice-search anti-context set [S12]; another talk's slide [S7]; unrelated context [S11]. | `unrelated`, `adv_*`, null presses (§11). |
| R12 | **Do not learn from your own output.** (hark-specific reasoning; no external source.) | hark's paste reappears in focused fields, scrollback and agent user turns. | Tag self-output as weak evidence unless the user edited it (§7.4). |

---

## 5. Sourcing layer

### 5.1 Overview

```
 OS events ──┐ Hyprland .socket2.sock: activewindowv2, windowtitlev2   [S26]
             │ macOS: NSWorkspace didActivateApplication; AXObserver:
             │   focused-window / focused-UI-element / title changed   [S27]
 FS events ──┤ inotify / FSEvents: <repo>/.git/{HEAD,index}, worktree (debounced),
             │   agent session dirs, vocabulary / learned files
 Timers   ───┤ TTL expiry, idle repo-symbol refresh
 Key-down ───┘
      │
      ▼
 ContextDaemon (one owner thread + bounded worker pool)
   FocusTracker  → FocusKey(window_id, app_id, title_sha, cwd, tty, agent)
   Scheduler     → per-provider jobs: budgets, cancellation, coalescing, backoff
   EvidenceStore → (provider, scope) → Evidence{captured_at, validator, confidence,
                                                privacy_class, spans | items}
   CandidatePool[FocusKey] → terms + aliases + phones + prior + provenance (≤ 5,000)
      │
      │ key-down: focus probe (≤ 10 ms) → seed from pool (≤ 5 ms) → SessionStart
      │           volatile refresh (≤ 80 ms, async) → maybe ContextUpdate rev 2
      ▼
 Utterance
   SessionStart{seed (≤ 30), partials=on, anchor=IF_CHEAP | ALWAYS(dataset)}
   PartialResult → Retriever(pool, partial) → ListPlanner → ContextUpdate rev n (≤ 3)
   key-up → EndOfAudio → FinalResult{n_best[0] biased, anchor, bias_hits}
   AnchorGate → canonicalize (gated) → cleanup → guardrails → paste
   TraceSink → history (provenance) / dataset inbox (spec §2.1 shortlist_used, outputs[])
```

### 5.2 Provider contract

The Python signatures are in §10. A provider has:

- `id`, the `TermSource` it feeds;
- a `scope`: global, app, window, element, tty/pane, cwd, repo, or agent session;
- `triggers`: which events schedule it;
- `budget_ms`;
- a `volatile` flag;
- a `privacy_class` (§8.1);
- `capture(scope, cancel) -> Evidence`.

Rules:

- **Nothing blocks the daemon thread.** Each capture runs in a worker with a hard timeout and can be cancelled;
  subprocesses are killed on cancel. AX calls keep a per-call messaging timeout (`AXUIElementSetMessagingTimeout`,
  as today).
- **Backoff.** A provider that times out goes into backoff for that scope (1 s, 2 s, 4 s … up to 30 s) [U]. A hung
  Electron app or `git` on a network filesystem then cannot tax every key-down.
- **Scrub before storing, and bound every output.** Byte and item caps follow spec §3.2: focused text ±4,000 chars
  around the caret, terminal = visible screen plus 200 lines, agent excerpt 8,000 chars.
- **Replayability.** Every provider must be replaceable by a *recorded-evidence provider* fed from a dataset
  sample's `context` snapshot. That makes offline evaluation run the same code as live dictation. It is also the
  second implementation that justifies the `Provider` protocol.

### 5.3 Provider matrix

Privacy classes P0–P3 are defined in §8.1. `cap-exp` = to be filled from the macOS capture-capability experiment.

| Provider (TermSource) | Scope | Triggers | At key-down | Budget [U] | Validator / TTL | Confidence | Class | Default |
|---|---|---|---|---|---|---|---|---|
| focus (app, title, pid, window) | window | focus and title events | probe only | 10 ms (hyprctl), 20 ms (AX) | event-driven | 1.0 | P2 (titles can name documents) | on |
| window_title terms | window | title event | no | — | title hash | 0.7 × focus | P2 | on |
| selection | element | key-down | yes | 40 ms | none (TTL 0) | 1.0 | P2 | on, never in secure fields; Linux via AT-SPI `cap-exp`/[U] |
| focused_text (± N chars of caret) | element | focus event (warm) + key-down | yes | 60 ms | element id + value hash; TTL 2 s | AX read 1.0; placeholder/unknown role 0.5; secure 0 | P2 | on, except secure or deny-listed. Chromium/Electron need `AXManualAccessibility` (macOS) [S28] or `--force-renderer-accessibility` (Linux) [S29][U]. `cap-exp` |
| terminal_text (visible + 200 lines) | tty/pane | focus event + key-down | yes | 50 ms | (pane id, tty mtime); TTL 1 s | 0.8 | P2 | on where an API exists (kitty, wezterm, tmux [U]); foot/alacritty: none, or OCR if opted in |
| terminal_state (cwd, fg process, agent) | window | focus event; validated at key-down | validate (`/proc` read ≤ 5 ms) | 20 ms | (tty, tpgid, fg pid) | resolver confidence (§5.6) | P1 | on (existing `terminal.py`) |
| repo_index (files, symbols) | repo | cwd change; `.git/HEAD`, `.git/index` events; idle timer | never | async, ≤ 2 s | (HEAD, index mtime); serve last-good | 0.9 | P1 | on |
| repo_recent (changed files and their identifiers) | repo | worktree FS events (debounced 1 s) | never | async | dirty-file mtimes | 0.9 | P1 | on |
| agent_session (last reply, tool paths) | cwd + agent | session-dir FS event; agent pid change | validate (stat ≤ 2 ms) | async incremental tail parse ≤ 50 ms | (path, size, mtime) | match confidence 0.2–0.95 (existing) | P2 | opt-in per spec §3.2 [U: on for the owner] |
| own_history (last 5 dictations in this app) | app | after each paste | no | — | append-only | 0.5, `self=true` | P2 | on |
| learned lexicon (undo, swap-to-raw, agent-submission diffs) | global / repo | on change | no | — | file mtime | 1.0 | P0 | on |
| user vocabulary | global | file change | no | — | mtime | 1.0 | P0 | on |
| clipboard | global | key-down | yes, if opted in | 10 ms | change count moved within [t−3 s, t] or during dictation [S4]; skip concealed/transient [S31][S32] | 0.6 | P3 | **off**; local-only when on |
| OCR (focused window only) | window | key-down; runs while speaking | concurrent | ≤ 400 ms; late → `ContextUpdate` | none | mean OCR word confidence | P2 | **off**; image discarded [S1][S6] |

### 5.4 Event-driven prefetch and key-down refresh

```
t < 0     focus/title/FS events → warm pool for FocusKey:
          terminal_state, repo (stale-while-revalidate), agent_session, title,
          focused_text (warm read, 300 ms after focus settles; debounce 150 ms) [U]
t = 0     key-down: focus probe → FocusKey_now
          if FocusKey_now == pool.key: seed = pool.top(K_seed)
          else (focus race): seed = global-only terms (P0 + repo of the same cwd if known)
t ≤ 15ms  SessionStart{context rev 1 = seed} sent. Audio already queues behind it (pipeline does this today).
t ≤ 80ms  volatile refresh (selection, focused text, terminal text, clipboard if opted in) merged into the pool;
          if the seed changes materially → ContextUpdate rev 2
t ≥ 300ms PartialResult → retrieval → ContextUpdate rev n (≤ 3 per utterance, throttled to 1 per 400 ms)
key-up    EndOfAudio (no updates after it) → FinalResult → gate → canonicalize → cleanup → paste
```

- **Today's `deadline_ms = 150` becomes the volatile-refresh budget.** It no longer blocks `SessionStart`.
- **Servers without midstream updates** (`MidstreamUpdate.UNSUPPORTED`):
  - `SessionStart` waits for the volatile refresh, but at most 80 ms;
  - retrieval output is used only for gating and canonicalization.

  This trade-off is measured in E7 (§11).
- **macOS.** `NSWorkspace` activation alone is not enough. The current code already notes that its frontmost-app
  value only refreshes inside an AppKit run loop. The daemon therefore needs a run loop thread for `AXObserver`
  notifications on the active app: focused window changed, focused UI element changed, title changed [S27].
- **Hyprland.** `activewindowv2>>ADDRESS` gives the active window and `windowtitlev2>>ADDRESS,TITLE` gives title
  changes [S26]. Title changes of *other* windows are ignored. Per the wiki, the request socket must be opened and
  closed for each request, because an unclosed connection freezes Hyprland [S26]. The event socket (`.socket2`)
  stays open.
- **Linux non-terminal apps.** AT-SPI focus events (`object:state-changed:focused`, `window:activate`) would replace
  polling [U: availability on Hyprland is open question §13.5].

### 5.5 Caching and invalidation

- **C1 Stamping.** Evidence is keyed by (provider, scope key) and stamped with `captured_at` (monotonic and wall
  clock), its validator, and the `FocusKey` it was captured under.
- **C2 Validators.** At key-down, non-volatile sources are validated with cheap stats:
  - repo: (HEAD, index mtime), as today;
  - agent session: (path, size, mtime);
  - title: hash;
  - terminal state: (tty, tpgid).
- **C3 Stale-while-revalidate** for expensive sources (repo index, agent parse). Key-down uses the last good value,
  its age becomes a ranking feature, and the refresh runs in the background.
- **C4 Volatile sources** (selection, focused text, terminal text, clipboard, OCR) are never served past their TTL
  (0–2 s).
- **C5 Scope binding.** Window-scoped evidence is used only when the key-down `FocusKey` has the same window.
  cwd-scoped evidence (repo, agent) needs the same cwd. Global P0 sources are always allowed. This stops
  cross-window leakage when the user presses the key right after switching (tested in E10).
- **C6 Content-addressed derived caches:**
  - `extract_terms(text)` keyed by sha256(text);
  - `spoken_forms` and phones per canonical (LRU, ~50k entries);
  - pools per `FocusKey` (LRU 32).
- **C7 Invalidation triggers:**

  | Event | Action |
  |---|---|
  | focus change | switch pools (do not delete) |
  | title change | refresh title evidence only |
  | FS event | debounced refresh of that source |
  | privacy setting change | purge everything |
  | focus enters a deny-listed app | purge that window's volatile evidence |

- **C8 Bounds.** The pool holds ≤ 5,000 candidates by prior, with per-provider byte and item caps (§5.2).
- **Agent sessions.** Parse incrementally from the last byte offset. Today's code re-reads the 4 MB tail on every
  dictation.

### 5.6 Per-source confidence

`confidence ∈ [0, 1]` estimates the probability that this evidence describes what the user is looking at *now*.
It multiplies the prior (§6.2).

| Source | Confidence |
|---|---|
| focus | 1.0 when the key-down probe matches. |
| terminal_state | 0.9 when the tty-atime heuristic finds a single active tty; 0.6 when several ttys are within 2 s; 0.5 for the newest-process fallback [U, abl]. |
| agent_session | The existing match confidence. |
| focused_text | 1.0 for an AX/AT-SPI value read; 0.5 if the role is unknown or the text looks like a placeholder; 0 for a secure field. |
| OCR | Mean word confidence. |
| repo | 1.0 when the cwd is inside the repo. |

Every source is further multiplied by `exp(−age/τ_src)` (τ: volatile 2 s, agent 10 min, repo 1 h [U, abl]).

### 5.7 Failure modes

| Failure | Detection | Behavior |
|---|---|---|
| AX/AT-SPI call hangs (busy app) | messaging timeout; provider budget exceeded | Evidence missing; backoff for that app; logged in trace. |
| Focus switched between key-down and capture | `FocusKey` mismatch (C5) | Drop window-scoped evidence; use global terms only. |
| Two agent sessions in the same cwd | ambiguity factor (existing) | Below 0.7 → not used. |
| Huge repo or network filesystem | git timeouts (2 s, existing) | Background only; last good value served with its age. |
| Electron or Chromium exposes no AX tree | empty tree on a known Electron app | `ax_tree_empty` in trace; `hark doctor` explains the flag or attribute. |
| Context arrives after `SessionStart` | revision timing | `ContextUpdate` if supported, else canonicalization only. |
| Server drops or ignores terms | `AppliedConfig.bias_terms_dropped`, `WARNING_CODE_CONTEXT_IGNORED` | Logged; planner learns the server's real cap. |
| Server echoes context (prompt backends) | `flags.context_echo_suspected` or client echo check | Output the anchor (§7.3). |
| Network down, local CPU fallback has no biasing | provider registry | Canonicalization still uses the pool. |
| Secret in captured text | scrubber or term filter | Redacted before extraction; never a term (§8.5). |
| hark's own paste in focused text or session | self-output matcher | Weak evidence only (§7.4). |

---

## 6. Relevance: pool, prior ranking, retrieval, list planning

### 6.1 Candidate extraction and normalization

- **One `speak()` everywhere.** The client and the dataset tooling (spec §3.4 step 2) use one function. Today it is
  `aliases.spoken_forms`. It gains **phones**:
  - CMUdict per word, with a G2P fallback [U tool: g2p_en vs espeak-ng];
  - ARPAbet with stress stripped;
  - each phone mapped to one character, so standard Levenshtein libraries work.
- **Common-word filter.** A single plain word that is in a ~5k common-English list never becomes a term unless it
  comes from vocabulary or learned sources. Handy used Meta's 5k list [S7]; today's analogue is
  `is_interesting_identifier`.
- **Secret filter** (§8.5), plus length and entropy caps.
- **Placeholder and hint text is excluded.** Use the AX placeholder attribute, as Wispr does [S2].
- **Evidence tier per candidate** (spec E1–E3):
  - E1: the term appears verbatim in text evidence;
  - E2: derived (identifier split, path component, file stem; `file_candidates` already makes stems);
  - E3: semantic. Not produced in v1 (R1).

### 6.2 Prior ranker (stage A, no audio)

It extends the existing formula (`SOURCE_WEIGHT × (0.6 + 0.4·score) + 0.05 per extra source`) with these features:

| Feature | Definition | Why |
|---|---|---|
| source tier | max over evidence of `SOURCE_WEIGHT[source]` | baseline (existing) |
| agreement | number of distinct sources | multi-source evidence is more precise (existing bonus) |
| proximity | text: `exp(−caret_distance/1000 chars)`; terminal: `exp(−lines_from_bottom/40)`; agent reply: `exp(−chars_from_end/1500)`; tool-call path: `1/(1+rank)` [U, abl] | R1; near the cursor [S2][S7] |
| recency | `exp(−age/τ_src)`; mtime rank for repo_recent | R7 |
| evidence tier | E1 = 1.0, E2 = 0.7 [U] | R1 [S9] |
| source confidence | §5.6 | wrong-window and wrong-session risk |
| history | accepted occurrences in this app or repo over the last 30 days (+); corrections toward the term (+); undo or swap-to-raw after it was applied (−) | user feedback |
| self_only | every evidence item is hark's own output | R12 |

v1 score:

```
prior = clip( conf · tier · (0.6 + 0.4·max(proximity, recency, tier_E))
              + 0.05·(n_sources − 1) + w_hist·history − w_self·self_only , 0, 1 )
```

It is replaced by a logistic model once the ranking-ablation experiment has labels (`abl`).

Outputs:

- the **pool**: ≤ 5,000 candidates by prior;
- the **seed**: top `K_seed = 30` by prior, with no source supplying more than 50 % of the seed, and diversity across
  kinds (paths, identifiers, flags, names).

### 6.3 Two-pass retrieval (stage B): the first-pass hypothesis picks the terms

This is the per-partial algorithm on the client. It is incremental: only spans touching text after the last stable
prefix (`PartialResult.stable_bytes`) are re-queried.

1. **Tokenize** the partial into lowercase words. Numbers are spelled with the same `speak()` rules.
2. **Build spans.** For each span of 1…L words (L = min(6, longest alias)) that is not all stopwords, compute three
   keys:
   - the token tuple;
   - the phone string;
   - the joined letters with spaces removed (Handy's trick for split identifiers, e.g. "use task store" →
     `usetaskstore`) [S7].
3. **Generate candidates:**
   - exact alias index (token tuple → term);
   - a phone 3-gram inverted index, keeping candidates that share ≥ m 3-grams and pass a length filter:
     |Δlen| ≤ max(3, 0.5·len) [S7].
4. **Score.** `d_phone` is the weighted phone Levenshtein normalized by the longer length (the same distance as
   spec §3.4). `d_letters` is the same on joined letters. `sim = 1 − min(d_phone, d_letters)`. Per span, keep
   candidates that satisfy all of:
   - `sim ≥ θ` (start at 0.65 [U, abl]);
   - Apple's rule: d ≤ 1.2 · d_best, or d < 0.2;
   - at most 10 [S16].
5. **Exact hits.** If the span equals an alias and the term is multi-word or single-word informative, set `sim = 1`.
6. **Per-term match.** `m(t)` = the max over spans. Keep the span (token offsets) for the trace and the gate.
7. **Retrieved set.** `R_t` = terms with `m ≥ θ`, sorted by `m · (0.5 + 0.5·prior)`, capped at 30.

**Cost.** Pool of 5k terms with ~2 aliases each; ~30 new spans per update; < 200 index candidates per span. That is
≤ 6k distance computations per update:

- with a C Levenshtein (rapidfuzz, a new dependency [U]) roughly 1–5 ms [U];
- in pure Python roughly 30–60 ms [U].

Budget: 20 ms p95 per update.

**Known limitation.** Hypothesis-based retrieval misses a term whose first-pass rendering is too far from every
alias (PTC-Bias says this explicitly [S19]). Mitigations:

- the seed is part of every revision, so prior recall is kept;
- matching on joined letters handles splits;
- N-best partials are used where a server offers them;
- later, **server-side acoustic retrieval** (CTC or phoneme posteriors, CTC-WS-style [S22], PTC-style [S19]) behind
  a capability flag (§12, phase 5).

Where the first pass comes from:

| Server | First pass | Notes |
|---|---|---|
| Incremental with partials (`hark.partials`) | Partials of the seed-biased decode (same stream, no extra cost). | Default path. E4 checks whether seed-biased partials bias retrieval [U]. |
| Buffered, no partials | (a) The speculative `EagerEnd` result at a VAD pause. Send `ContextUpdate` first, then a new `EagerEnd`, because an update invalidates speculation (ADR-001 #3). (b) At key-up, a second decode only if `PerfEstimate` predicts it fits the finalize budget. | A `Rebias(utterance_id, BiasList)` RPC that reuses cached encoder output would make (b) cheap. Additive protocol change, needs an ADR [U]. |
| Prompt-based (Qwen3-ASR, `BIAS_MECHANISM_PROMPT`) | Apple's three steps: unbiased decode → retrieval → prompted decode with ≤ 10 terms [S16]. | Two full passes, so desktop GPU only. No streaming timestamps (ADR-001). |

The alternative is Handy-style **server-side retrieval**: upload a ≤ 1,000-term pool and run multiple decodes on the
server [S7]. It gives better acoustics (exact unbiased log-likelihoods) but sends more context and needs new server
work. Plan: build client-side retrieval first (protocol unchanged), and evaluate server-side retrieval for trusted
servers only, in phase 5.

### 6.4 List planner: size control, weights, strength, competition, revisions

Inputs: pool, seed, `R_t`, model capabilities (`max_terms`, `max_aliases_per_term`, `max_total_tokens`, mechanism,
`per_term_weight`, `midstream_update`), the provider's privacy policy, and VAD statistics.

- **L1 Privacy first.** Drop terms above the provider's maximum class (§8.1). For third parties, set
  `minimal_metadata` and send no provenance.
- **L2 Size.**

  | Backend | Cap |
  |---|---|
  | `DECODE_TIME_BOOST`, `ACOUSTIC_RESCORE` | `K_final = min(caps.max_terms, 50)` |
  | `PROMPT` | 10 [S16][S19] |
  | Deepgram adapter | ≤ 50 terms and ≤ 500 tokens [S8] |

  The seed is ≤ 30 in every case.
- **L3 Composition.**
  - Retrieved terms come first (sorted by `m·prior`), then seed terms by prior.
  - No source may supply more than 50 % of the list.
  - Each term sends `min(caps, 3)` aliases, always including the alias that matched.
- **L4 Weights** in [0, 1]; the server maps them to its native scale (ADR-001 #1).
  - Retrieved: `w = 0.3 + 0.4·prior + 0.3·m`.
  - Seed-only: `w = 0.3 + 0.5·prior` [U, abl].
- **L5 Strength** (`BiasList.strength`):

  | Condition | Strength |
  |---|---|
  | ≥ 1 retrieved match | 1.0 |
  | seed only | 0.7 [U] |
  | VAD speech < 250 ms (null press) | 0 |
  | deny-listed app | 0 |
  | empty pool | 0 |

  This is the anti-context lever from R2 and R11.
- **L6 Competition groups.** Canonicals form a group when they normalize the same way (case- and
  separator-insensitive: `parse_config`, `parseConfig`, `parse-config`) or have phone distance ≤ 0.1.
  - Send only the group's best term by prior.
  - If the top two priors are within 0.1: send both at half weight and mark the group *ambiguous*. The canonicalizer
    then rewrites to neither, and the gate requires a score margin.

  This follows PTC's temporal competition [S19] and the existing ambiguous-alias rule in `canon.py`.
- **L7 Stability.**
  - Send a new revision only if Jaccard(new, sent) < 0.85 or a term with `m ≥ 0.85` is newly added.
  - At most 3 revisions per utterance.
  - No updates after `EndOfAudio`.
  - `ContextUpdate` always goes before `EagerEnd`.
- **L8 Caching.** Set `BiasList.content_hash` so servers reuse compiled trees for the same list ([S18]: select
  before you compile). Term ids are stable (`sha256(canonical)[:12]`), so revisions can be diffed.

### 6.5 Plans per backend

| Mechanism or adapter | First pass | List | Anchor | Gate | Notes |
|---|---|---|---|---|---|
| `DECODE_TIME_BOOST` (NeMo GPU-PB/TurboBias, parakeet_mlx) | partials | ≤ 50; `ContextUpdate` if `midstream_update = FULL` | `IF_CHEAP` (`DECODER_ONLY`); `ALWAYS` in eval and dataset mode | full §7.1 | Per-stream lists [S23]. GPU-PB per-request bias is greedy-only, with no N-best (ADR-001). |
| Riva adapter (NeMo-Speech.cpp) | — | n/a: the server ignores bias for TDT (ADR-001) | parallel unbiased request (ADR-001) | canonicalization gates only | |
| `PROMPT` (Qwen3-ASR) | unbiased decode | ≤ 10; no free text by default | the unbiased decode | §7.3 echo guard + text-aligned gate | `free_text.echo_risk` |
| `ACOUSTIC_RESCORE` (FluidAudio) | — | ≤ 50 | `FREE` | `bias_hits` | |
| `NONE` / offline CPU fallback | — | — | — | canonicalization only | |
| Deepgram adapter | streaming results | ≤ 50 and ≤ 500 tokens; replaced mid-stream via `Configure` [S8] | none | canonicalization gates | `redact_usage=true` |

---

## 7. Safety against distractors and false substitutions

### 7.1 Anchor gate (client, after `FinalResult`)

Inputs: the biased hypothesis B (`n_best[0]`, with word times and confidences), the anchor A (`FinalResult.anchor`),
`bias_hits`, and the plan that was sent.

1. **Align** A and B by word timings, falling back to token Levenshtein. Collect the differing regions D.
2. **For each region r that overlaps an `APPLIED` bias hit for term t**, accept B_r only if all of these hold:
   - **(a) Acoustic plausibility.** `npd(spoken(A_r), best_alias(t)) ≤ τ_gate` (start at 0.5 [U, abl]), or
     `term_score − anchor_score ≥ −δ` when the server reports `hit_scores`. The anchor must have heard something
     close [S7][S19][S22].
   - **(b) Evidence.** t has at least one non-self evidence item and `prior ≥ p_min` (0.3 [U]).
   - **(c) Not a morphology-only change of a plain word.** Handy's rule: a shared prefix of ≥ 5 letters reaching to
     within 3 letters of the end [S7].
   - **(d) No negation or number change.** The region must not add or remove a negation, or change a digit sequence
     present in A. Spec §5.7 makes negation flips critical.
   - **(e) Not ambiguous.** t is not in an ambiguous group (L6), unless a score margin is available.

   Otherwise restore A_r and log the reason.
3. **Collateral guard.** For a region with *no* bias hit within ±1 word, restore A_r, unless B_r's minimum word
   confidence beats A_r's by ≥ 0.2 [U]. Biasing should only change the term span; always-on biasing damages
   neighboring speech [S12][S13].
4. **No anchor available** (`ANCHOR_COST_UNAVAILABLE` or skipped for budget):
   - accept `APPLIED` hits whose words have confidence ≥ 0.5, noting that confidences are uncalibrated (ADR-001);
   - L5 has already lowered the strength.
5. **Output.** Gated text, per-region decisions and reasons go to the trace. Cost ≤ 2 ms.

The anchor costs `DECODER_ONLY` for TDT (cheap) but `FULL_PASS` for prompt backends. Production uses `IF_CHEAP`;
evaluation and dataset mode use `ALWAYS`, so `outputs.asr.unbiased_text` is always filled (spec §2.1).

### 7.2 Canonicalization gates (extends `canon.py`)

The existing rules stay: whole-chunk alias, clause boundary, informative single words, ambiguity skip, confidence ≥
0.5. New rules:

- **Membership.** The term is in the sent list, or in the pool with `prior ≥ p_canon` (0.4 [U]).
- **Self-only evidence → skip** (§7.4).
- **Profile-aware.** In prose profiles (chat, email, docs), rewriting a multi-word alias to an identifier needs E1
  evidence *in the focused app*, not repo-wide. For example, "use effect" in an email stays prose unless `useEffect`
  is visible there.
- **Carrier words** are a gate feature, not a hard rule [U, abl]. These raise confidence when adjacent to a match:
  - spoken separators: "dash dash", "dot", "slash", "underscore";
  - carriers: "the function", "file", "flag", "variable", "in".

  Their absence on a single-word match in prose lowers it. This is Zhao's prefix activation [S12] and Wispr's "tag"
  / "at" [S3], reduced to a feature.
- **Provenance.** Each replacement records the term's evidence ids for attribution (§9).

### 7.3 Echo guard (prompt backends)

- Free text goes only to local, trusted servers. It is limited to a description plus ≤ 300 chars before the caret
  [U].
- **Echo** is ≥ 6 consecutive N1 tokens copied from the context and not present in the anchor (the spec's
  context-echo definition), or `flags.context_echo_suspected`. On echo, output the anchor and log it.

### 7.4 Self-contamination

- hark keeps sha256 hashes plus the text of its last N = 20 pastes per app, in memory and in history.
- Evidence spans that exactly match a recent paste are tagged `self = true`.
- Agent-session user turns are compared with the pasted text:
  - **identical:** self-output;
  - **edited before submission:** the diff becomes learned evidence: positive for the edited-in canonical, negative
    for the replaced one. This is the dataset's `user_correction.source = agent_session_submission` signal, reused
    live.
- Today `read_pi` reads only assistant turns, so the rule matters as soon as focused-text or terminal providers ship.

### 7.5 Null presses and anti-context

- VAD speech < 250 ms → strength 0. This blocks hallucinated terms on coughs (spec: null presses).
- No retrieval match → only the seed, at strength 0.7.
- Evaluated with `unrelated`, `adv_common` and null presses (§11).

---

## 8. Privacy controls

### 8.1 Classes and provider policy

| Class | Contents | Own servers (mTLS, Tailscale) | Third-party APIs |
|---|---|---|---|
| P0 user-owned | vocabulary, learned lexicon | send | send |
| P1 workspace | repo files and symbols, branch, cwd basename, fg process name | send | send terms [U: or P0 only] |
| P2 screen | focused text, selection, terminal text, window title, agent replies, OCR, own history | send **terms** | **no** (default) |
| P3 sensitive | clipboard; anything from deny-listed apps or secure contexts | clipboard terms: local only unless opted in per provider; deny-listed and secure: never captured | never |

- Only **terms** leave the client.
- `FreeTextContext` goes to local trusted servers only.
- Third-party adapters set `minimal_metadata`, plus vendor flags such as Deepgram `redact_usage=true` [S8].
- The server-side `PrivacyFlags` stay all false (ADR-001 #7). The client policy is the real gate.

### 8.2 App deny-list and allow-list

- **Match on** `app_id` (bundle id or window class) and title regex.
- **Default deny-list:**
  - password managers (1Password, Bitwarden, KeePassXC);
  - banking apps;
  - the private messengers from spec §1.8;
  - system authentication dialogs;
  - private or incognito browser windows (by title).
- **Denied focus:** nothing is captured, the strength is 0, and only P0 terms remain available for canonicalization
  [U: or nothing].
- **Optional allow-list mode:** capture only in listed apps.

### 8.3 Secure fields

- **macOS AX:** skip focused text and selection when the role or subrole marks a secure text field.
- **macOS secure input:** also skip when `IsSecureEventInputEnabled()` is true at key-down. Apple documents this as a
  system-wide secure-input state, so it is a heuristic, not proof of a password field [S30].
- **Linux AT-SPI:** skip `ROLE_PASSWORD_TEXT` [U].
- **Custom or web password fields slip through** (Wispr warns about this [S2]). Also skip fields whose value is all
  bullets, or whose label, description or placeholder matches `/pass(word|code|phrase)|pin|otp|secret|token/i` [U].

### 8.4 Terminals

Skip terminal text when either holds [U]:

- the foreground process is one of `sudo`, `su`, `ssh`, `gpg`, `pinentry*`, `passwd`, `op`, `pass`, `age`, `kinit`;
- the last visible line matches `/(password|passphrase|pin|token)[^:\n]*:\s*$/i`.

### 8.5 Scrubbing before extraction

- Reuse dataset scrub stages 1–2 (spec §1.8) **at capture time**:
  - exact-value redaction from env files, `.env*` in the captured repo, and an explicit secrets list;
  - format rules: keys, tokens, JWTs, `Authorization:` headers, URL credentials;
  - `$HOME` → `~`.
- Today's `_SECRET_RE` is a subset and currently covers only agent replies. Apply it to every text source.
- **Term-level filter.** Never accept as a candidate:
  - anything matching a secret format;
  - Shannon entropy ≥ 3.5 bits/char with length ≥ 16 [U];
  - hex or base64 runs of 20 or more characters;
  - email addresses and phone numbers (P2; never to third parties).

### 8.6 Clipboard

- Off by default.
- When opted in:
  - only content whose change count moved within 3 s before key-down, or during dictation, is used (Superwhisper's
    rule [S4]);
  - skip items carrying `org.nspasteboard.ConcealedType`, `TransientType` or `AutoGeneratedType` [S31], or
    `x-kde-passwordManagerHint=secret` on Linux [S32] [U: wl-paste type listing];
  - terms are local-only unless the provider policy allows them.
- hark's own clipboard writes (the paste path) are marked `TransientType` + `AutoGeneratedType` on macOS [S31] and
  ignored by hark's clipboard provider.

### 8.7 Retention and transparency

- **Retention.** Raw evidence lives in RAM. P2/P3 raw text is dropped after the dictation and its TTL. Caches keep
  derived P0/P1 terms only. History keeps terms plus provenance *metadata* (source, offsets, age, hashes), not raw
  context, except in dataset mode, which follows spec §1.8 tiers.
- **Transparency** (R9): `hark context explain <utterance_id>` shows exactly what was sent and why. Settings expose
  each source and each provider's maximum class.

---

## 9. Observability

**Trace record** (per utterance; JSON). It is stored as `context_trace` in history and maps onto the dataset sample
(`shortlist_used`, `outputs[]`):

```json
{
  "trace_version": "ctx-0.1",
  "focus_key": {"app_id": "com.mitchellh.ghostty", "window": "…", "title_sha": "…", "cwd": "~/projects/hark", "agent": "pi"},
  "keydown_probe_ms": 6.1, "focus_race": false, "denylisted": false, "secure_field": false,
  "providers": [
    {"id": "repo_index", "status": "cache", "age_ms": 41000, "ms": 0.4, "items": 5000, "validator": "HEAD=ab12;idx=1731…", "confidence": 1.0},
    {"id": "agent_session", "status": "ok", "ms": 3.2, "age_ms": 5300, "confidence": 0.95},
    {"id": "terminal_text", "status": "timeout", "ms": 50}
  ],
  "pool": {"size": 2140, "by_source": {"repo": 1500, "repo_recent": 410, "agent_session": 80, "window_title": 6}},
  "revisions": [
    {"rev": 1, "t_ms": 9, "cause": "seed", "terms": 28, "strength": 0.7, "content_hash": "…"},
    {"rev": 2, "t_ms": 1420, "cause": "retrieval", "partial_seq": 7, "added": ["t3f9a"], "removed": ["t0c21"], "strength": 1.0}
  ],
  "terms": [
    {"id": "t3f9a", "canonical": "parse_config", "kind": "identifier", "aliases_sent": ["parse config"],
     "prior": 0.71, "weight": 0.86, "stage": "retrieved", "first_rev": 2,
     "match": {"partial_seq": 7, "span_text": "parse config", "sim": 1.0},
     "evidence": [
       {"source": "agent_session", "evidence_id": "sha256:…", "span": [812, 824], "age_ms": 5300, "tier": "E1", "self": false},
       {"source": "repo_recent", "evidence_id": "sha256:…", "age_ms": 41000, "tier": "E1", "self": false}]}
  ],
  "server": {"applied_terms": 28, "dropped": [], "anchor": "present",
             "bias_hits": [{"term_id": "t3f9a", "decision": "APPLIED", "anchor_text": "pass config", "term_score": -3.1, "anchor_score": -2.9}]},
  "gate": [{"words": [3, 5], "term_id": "t3f9a", "decision": "accept", "npd": 0.18}],
  "canonicalize": [{"before": "dash dash dry run", "after": "--dry-run", "term_id": "t77b0", "reason": "alias match conf=0.93"}],
  "attribution": [{"final_span": [3, 15], "term_id": "t3f9a", "sources": ["agent_session", "repo_recent"]}]
}
```

- **Dataset mapping.**
  - `shortlist_used.terms[{term, aliases, sources, score, weight}]` = the last revision.
  - Proposed spec extension [U, needs a spec bump]: `stage`, `first_rev`, `evidence[]`, `revisions[]`, `gate[]`.
  - Prefetched evidence has a negative `offset_from_keydown_ms`, which the spec must allow [U].
- **Live counters** (local, no content):
  - prefetch hit rate (pool ready at key-down);
  - key-down → `SessionStart`;
  - provider timeout rate per app;
  - revisions per utterance;
  - gate revert rate;
  - canonicalizations;
  - **undo or swap-to-raw within 10 s after a context-applied change.** This is the strongest live negative signal.
- **Tools:**
  - `hark context watch`: live pool for the focused window;
  - `hark context explain <utterance_id>`;
  - `hark doctor`: lists provider availability for the focused app (the same probes as the capture experiment).

---

## 10. Interfaces

### 10.1 Python (now)

The public API stays stable:

- `collect(deadline_s, max_terms)` becomes a shim over the daemon: a warm pool plus a volatile refresh.
- `canonicalize(...)` keeps its signature.
- `Term` gains defaulted fields, so existing callers keep working.

Following the repo rule ("no abstraction until the second caller"), only `Provider` is a `Protocol`. Its second
implementation is the recorded-evidence provider used for replay. Everything else is a concrete class.

```python
# context/types.py (additions)
class PrivacyClass(IntEnum):
    P0 = 0; P1 = 1; P2 = 2; P3 = 3

class Tier(StrEnum):
    E1 = "E1"; E2 = "E2"; E3 = "E3"

@dataclass(frozen=True, slots=True)
class FocusKey:
    window_id: str
    app_id: str
    title_sha: str
    cwd: str | None = None
    tty: str | None = None
    agent: str | None = None

@dataclass(frozen=True, slots=True)
class TextSpan:
    text: str                    # scrubbed
    caret_distance: int | None   # chars from caret / lines from bottom / chars from end of reply
    self_output: bool = False

@dataclass(frozen=True, slots=True)
class Evidence:
    source: TermSource
    scope: str                   # e.g. "window:0x55…", "repo:/home/u/p/hark", "global"
    focus_key: FocusKey | None   # None for global/cwd-scoped sources
    captured_at: float           # time.monotonic()
    validator: str               # "" for volatile sources
    confidence: float            # §5.6, before age decay
    privacy: PrivacyClass
    spans: tuple[TextSpan, ...] = ()                       # free text to extract terms from
    items: tuple[tuple[str, TermKind, float], ...] = ()    # structured (canonical, kind, rank score)
    errors: tuple[str, ...] = ()

    @property
    def evidence_id(self) -> str: ...                      # sha256 over content (blob address in dataset mode)

@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source: TermSource
    evidence_id: str
    span: tuple[int, int] | None
    age_s: float
    proximity: float
    tier: Tier
    self_output: bool

@dataclass(frozen=True, slots=True)
class Term:                       # existing fields first: unchanged constructor order
    canonical: str
    spoken: tuple[str, ...]
    weight: float
    kind: TermKind
    source: TermSource           # strongest source (kept for compatibility)
    id: str = ""                 # sha256(canonical)[:12]
    sources: tuple[TermSource, ...] = ()
    prior: float = 0.0
    evidence: tuple[EvidenceRef, ...] = ()

# context/providers.py
class Trigger(StrEnum):
    FOCUS = "focus"; TITLE = "title"; KEY_DOWN = "key_down"; FILE = "file"; TIMER = "timer"

class Provider(Protocol):
    id: TermSource
    triggers: frozenset[Trigger]
    budget_s: float
    volatile: bool
    privacy: PrivacyClass
    def scope(self, focus: FocusKey) -> str | None: ...       # None => not applicable here
    def validator(self, scope: str) -> str: ...               # cheap stat; "" if volatile
    def capture(self, scope: str, cancel: threading.Event) -> Evidence: ...  # may raise; daemon catches

# context/daemon.py
@dataclass(frozen=True, slots=True)
class KeydownContext:
    focus: FocusInfo
    focus_key: FocusKey
    focus_race: bool
    seed: tuple[Term, ...]       # ready immediately
    pool_size: int

class ContextDaemon:
    def __init__(self, providers: Sequence[Provider], policy: PrivacyPolicy, *, clock: Callable[[], float] = time.monotonic) -> None: ...
    def start(self) -> None: ...                      # event sources: Hyprland socket2 / macOS observers / FS watchers
    def stop(self) -> None: ...
    def keydown(self, refresh_budget_s: float = 0.08) -> KeydownContext: ...   # never blocks > probe + 5 ms
    def refreshed(self, timeout_s: float) -> tuple[Term, ...] | None: ...      # seed after the volatile refresh
    def pool(self, key: FocusKey) -> CandidatePool: ...
    def focus_probe(self) -> FocusInfo: ...           # fixes G7: used for the pre-paste re-check

# context/retrieval.py
@dataclass(frozen=True, slots=True)
class Match:
    term_id: str
    sim: float
    span_tokens: tuple[int, int]
    span_text: str
    partial_seq: int

class Retriever:
    def __init__(self, pool: CandidatePool, *, theta: float = 0.65, per_span: int = 10, cap: int = 30) -> None: ...
    def update(self, partial_text: str, stable_bytes: int, seq: int) -> tuple[Match, ...]: ...   # incremental

# context/planner.py
@dataclass(frozen=True, slots=True)
class BiasPlan:
    revision: int
    terms: tuple[Term, ...]        # ordered, weights set, aliases trimmed to caps
    strength: float
    content_hash: str
    cause: str                     # "seed" | "refresh" | "retrieval"

class ListPlanner:
    def __init__(self, caps: ModelCaps, policy: ProviderPolicy) -> None: ...
    def seed(self, ctx: KeydownContext, vad_speech_ms: int | None) -> BiasPlan: ...
    def revise(self, current: BiasPlan, matches: Sequence[Match], pool: CandidatePool) -> BiasPlan | None: ...  # None = keep

# context/gate.py
@dataclass(frozen=True, slots=True)
class GateDecision:
    words: tuple[int, int]
    term_id: str | None
    decision: str                  # "accept" | "revert" | "collateral_revert"
    reason: str
    npd: float | None

class AnchorGate:
    def apply(self, biased: Hypothesis, anchor: Hypothesis | None, hits: Sequence[BiasHit],
              plan: BiasPlan) -> tuple[str, list[WordInfo], list[GateDecision]]: ...
```

Integration in `pipeline.Utterance`, in outline:

```python
kctx = daemon.keydown()                                    # ≤ ~15 ms
plan = planner.seed(kctx, vad_speech_ms=None)
stream.start(session_start(plan, partials=caps.partials, anchor=anchor_mode))
retriever = Retriever(daemon.pool(kctx.focus_key))
for partial in stream.partials():                          # on the response thread
    if (new := planner.revise(plan, retriever.update(partial.text, partial.stable_bytes, partial.seq), pool)):
        stream.update(new); plan = new                     # ContextUpdate{revision}
final = stream.wait_final(...)
text, words, decisions = AnchorGate().apply(final.n_best[0], final.anchor or None, final.bias_hits, plan)
canon, reps = context.canonicalize(text, words, plan.terms)
trace.write(...)                                           # §9
```

### 10.2 Rust (later; the thin client from `00-context.md`)

- **Closed provider set → enum dispatch.** Native `async fn` in traits is not dyn-compatible, so providers are an
  enum `AnyProvider` rather than trait objects.
- **One `tokio` runtime.** `tokio::select!` runs over:
  - the Hyprland `socket2` `UnixStream`;
  - `notify` watchers;
  - the macOS observer bridge, a run-loop thread feeding an `mpsc` channel;
  - key events.
- **Snapshots** are `ArcSwap<CandidatePool>` per `FocusKey`.
- **Retrieval** runs on `spawn_blocking` or rayon.
- **gRPC** via `tonic`/`prost` (ADR-001).
- **Crates [U to verify]:** `tokio`, `tokio-util` (`CancellationToken`), `notify`, `arc-swap`, `rapidfuzz` or
  `strsim`, `zbus`/`atspi` (Linux AT-SPI), `objc2` + `objc2-app-kit` + an AX binding (macOS), `procfs`/`sysinfo`.
- **G2P** has no mature Rust option. Precompute phones for the CMUdict vocabulary and run an embedded small G2P for
  out-of-vocabulary words, or keep G2P in a sidecar [U].

```rust
pub enum Trigger { Focus, Title, KeyDown, File(std::path::PathBuf), Timer }

pub trait Provider: Send + Sync + 'static {
    fn id(&self) -> TermSource;
    fn triggers(&self) -> &'static [TriggerKind];
    fn budget(&self) -> std::time::Duration;
    fn volatile(&self) -> bool;
    fn privacy(&self) -> PrivacyClass;
    fn scope(&self, focus: &FocusKey) -> Option<ScopeKey>;
    fn validator(&self, scope: &ScopeKey) -> Option<Validator> { None }
    fn capture(&self, scope: &ScopeKey, cancel: tokio_util::sync::CancellationToken)
        -> impl std::future::Future<Output = Result<Evidence, ProviderError>> + Send;
}

pub enum AnyProvider { Focus(FocusProvider), Terminal(TerminalProvider), Repo(RepoProvider),
                       Agent(AgentProvider), FocusedText(FocusedTextProvider), Recorded(RecordedProvider), /* … */ }

pub struct Daemon { pools: dashmap::DashMap<FocusKey, arc_swap::ArcSwap<CandidatePool>>, /* scheduler, store */ }
impl Daemon {
    pub fn keydown(&self, refresh_budget: std::time::Duration) -> KeydownContext;
    pub fn pool(&self, key: &FocusKey) -> std::sync::Arc<CandidatePool>;
}
pub struct Retriever { /* phone 3-gram index over the pool */ }
impl Retriever { pub fn update(&mut self, partial: &str, stable_bytes: usize, seq: u32) -> Vec<Match>; }
pub struct ListPlanner;  pub struct AnchorGate;   // same semantics as Python §10.1
```

### 10.3 Protocol touchpoints

| Need | Field |
|---|---|
| Revisions | `RecognitionContext.revision`, `ContextUpdate`, `PartialResult.context_revision`, `FinalResult.context_revision` |
| First pass | `ResultOptions.partials`, `PartialResult.stable_bytes`, `EagerEnd` |
| List control | `BiasList.strength`, `BiasList.content_hash`, `BiasTerm.weight`, `BiasTerm.provenance`, `PrivacyFlags.minimal_metadata` |
| Server limits | `BiasCapabilities.{max_terms, max_aliases_per_term, max_total_tokens, midstream_update, hit_scores}`, `AppliedConfig.bias_terms_dropped` |
| Gate | `ResultOptions.anchor`, `FinalResult.anchor`, `BiasHit.{decision, anchor_text, term_score, anchor_score}`, `ResultOptions.bias_diagnostics` |

No protocol change is needed for phases 0–4. Candidates for later additive changes, each needing an ADR [U]:

- a `Rebias` RPC that reuses cached encoder output;
- a `hark.acoustic_retrieval` feature, where the server returns term spots from CTC or phoneme posteriors.

---

## 11. Evaluation plan (tied to `docs/dataset/spec.md`)

### 11.1 What already exists and what this adds

The spec provides variants (§3.3), metrics (§5: EEM, ACEC, NVG, FCS, damage, B/U-WER, latency), the session-cluster
bootstrap (§5.10) and run records (§1.2). This plan adds three things:

- **system-configuration ablations**, run over the same variants;
- **funnel metrics** for sourcing and retrieval;
- **two proposed variant kinds** [U, spec bump].

All tuning uses `dev`. `test` is run once per phase.

### 11.2 System configurations

| id | Configuration |
|---|---|
| C0 | No context: strength 0, the output is the anchor. |
| C1 | Today: static ranker, 60 terms at `SessionStart`, canonicalize. |
| C2 | Prior ranker v1, seed only (`K_seed = 30`), list planner. |
| C3 | C2 + two-pass retrieval on partials (`ContextUpdate`). |
| C4 | C3 + anchor gate. |
| C5 | C4 + extended canonicalization gates (target system). |
| C3a | C3 with retrieval on the final *anchor* instead of partials. Upper bound without the partial-latency limit. |
| C3o | C3 with retrieval on gold `verbatim`. Oracle upper bound for retrieval. |

Dev sweeps: `K_final ∈ {10, 20, 30, 50, 100}`, `θ ∈ {0.55, 0.65, 0.75}`, `strength_seed ∈ {0.5, 0.7, 1.0}`,
`τ_gate ∈ {0.35, 0.5, 0.65}`, `K_seed ∈ {10, 20, 30}`. Ranker weights come from the ablation experiment (`abl`).

### 11.3 Experiments

| # | Question | Configs × variants | Metrics |
|---|---|---|---|
| E1 | Which sources earn their cost? | C5 × `correct` vs `missing_partial:<src>` for every source | Δ EEM, ACEC and NVG per source and evidence tier; provider latency; FCS by source. Decides which sources are on by default. |
| E2 | How fast does context go stale? | C5 × `stale:1d`, `stale:14d`, plus replays with evidence aged artificially (5 s, 60 s, 10 min) | EEM vs age curve; FCS. Live: prefetch hit rate, age at key-down. |
| E3 | Distractor robustness | C1…C5 × `unrelated`, `adv_swap`, `adv_add`, `adv_common`, `forced_list:n=500` (busy screen) | FCS_induced/1k, item damage, entity damage, negation flips. |
| E4 | Where are entities lost? (funnel) | C3, C3a, C3o × `correct`, `adv_add` | For each gold critical entity: in pool → in seed → retrieved → sent → `APPLIED` hit → exact in asr → exact in final. Also Recall_B@99 (mean list size for 99 % recall) and near-homophone distractor recall Recall_H@K [S19]. Checks whether seed-biased partials hurt retrieval. |
| E5 | Does the gate revert the right spans? | C4 vs C3, items where B ≠ A | Revert precision and recall (a revert is correct iff A_r is right and B_r is wrong); damage avoided vs gain lost. |
| E6 | How does list size scale? | C2 vs C4 × `forced_list:n ∈ {0, 20, 100, 500}` | B-WER and U-WER (Le et al., spec §5.3); FCS. |
| E7 | What does it cost in latency? | `hark-replay`, real-time pacing, R = 5 (spec §5.9); prefetch on/off; midstream on/off | key-down → `SessionStart`, partial → `ContextUpdate`, key-up → paste p50/p95/p99; Δ vs C0. |
| E8 | Self-contamination | C5 × proposed `adv_self` [U]: a wrong spelling of a gold entity planted as earlier hark output (focused text or agent user turn) and given as the only evidence | FCS_induced must not rise vs `missing`. |
| E9 | Privacy payload audit (CI, no ASR) | Golden synthetic contexts with planted secrets: AWS/GitHub/OpenAI keys, JWT, a `sudo` prompt, concealed clipboard, a deny-listed app, a secure field | **Zero** planted values in any `SessionStart`, `ContextUpdate`, trace or history record. Deny-listed → empty payload. |
| E10 | Focus races | Replay with the previous window's evidence present at key-down; scope binding (C5) on/off | FCS, EEM. |
| E11 | Do the backends differ? | C5 on parakeet (boost), Qwen3-ASR (prompt, K = 10), Riva adapter (canonicalization only) | All primary metrics; echo rate for prompt backends. |

Proposed variant kinds, for the dataset owner [U]:

- `adv_self` (E8);
- `race` (E10): the `correct` snapshot of the *previous* sample in the same session, but the current focus.

### 11.4 Pre-registered ship gates

The primary family follows spec §5.10: ACEC, EEM, FCS_induced/1k, item damage, protected-span violations, p95
key-up → paste. Holm correction is applied within the family.

- **G-a, no harm:**
  - item damage on `correct` ≤ the previous config's + 0.5 pp (95 % CI upper bound);
  - FCS_induced/1k on `unrelated` and `adv_common` ≤ 0.5;
  - zero new negation flips.
- **G-b, gain:** the ACEC Δ vs the previous config has a CI lower bound > 0. Phase 0 (≈ 200 test items) detects only
  effects of about 8–10 pp or more (spec §6.1). Smaller wins wait for phase 1 data.
- **G-c, latency:**
  - p95 key-down → `SessionStart` ≤ 30 ms;
  - p95 key-up → paste ≤ C0 + 50 ms;
  - retrieval ≤ 20 ms p95 per update.
- **G-d, retrieval:** recall of gold E1/E2 critical entities in the *sent* list ≥ 90 % on `correct` [U].

### 11.5 Logging this plan requires

- Every revision's list, retrieval matches, the anchor text, gate decisions and evidence refs, all in `outputs[]` and
  `shortlist_used` (§9 mapping).
- The server partial log stored with `chunk_log`, so replays of retrieval timing are deterministic [U].
- The run config hash covers ranker, planner, gate and privacy policy versions (`ranker_version` already exists;
  add `planner_version` and `gate_version` [U]).

### 11.6 Division of labor

- **Ranking-ablation experiment:** offline, text-only, on agent sessions. It fits §6.2 weights, `K_seed` and θ using
  the feature definitions above. Hand-off: a weights file plus the dev metrics it optimized.
- **Capture-capability experiment:** fills the `cap-exp` cells in §5.3 and the per-app availability shown by
  `hark doctor`.
- **This plan:** the audio-dependent system ablations E1–E11 and the ship gates.

---

## 12. Phased rollout

| Phase | Content | Exit criteria |
|---|---|---|
| **0. Instrument and secure** (now; no behavior change except privacy) | Trace record (§9) with multi-source `Term.sources` and evidence refs. Request the anchor (`ALWAYS` in dataset mode, else `IF_CHEAP`) and log anchor and `bias_hits`. Deny-list, secure-field checks, scrub selection and title, term-level secret filter. Mark hark's clipboard writes. Focus-only probe for the pre-paste check (G7). | E9 green in CI. Traces on 100 % of dictations. Overhead ≤ 2 ms. |
| **1. Context daemon and prefetch** | Hyprland `socket2` listener; macOS `NSWorkspace` + `AXObserver` run-loop thread; FS watchers (`.git`, session dirs); `EvidenceStore` with validators and scope binding; `SessionStart` from the warm pool; volatile refresh → `ContextUpdate` rev 2 where supported; incremental agent parsing. | p95 key-down → `SessionStart` ≤ 30 ms. Prefetch hit ≥ 90 % [U]. No ACEC regression vs C1 (E2, E7, E10). |
| **2. Prior ranker, list planner, anchor gate** | §6.2 features; `K_seed`; per-source caps; competition groups; strength; gate v1; extended canonicalization gates. | G-a, G-b, G-c for C2 and C4 vs C1 (E3, E5, E6). |
| **3. Two-pass retrieval** | Phones/G2P, phone 3-gram index, incremental retrieval; partials enabled on parakeet backends; `EagerEnd` ordering; prompt-backend three-step flow. | E4 funnel meets G-d. C5 vs C4: ACEC gain, no FCS regression, latency gate (E3, E4, E7, E11). |
| **4. More sources and learning** | Focused text (AX, AT-SPI), terminal text APIs, OCR opt-in, learned lexicon from undo, swap-to-raw and agent-submission diffs; ranker re-fit (`abl`). | E1 shows positive value per enabled source within budget. E8 passes. |
| **5. Rust daemon and server-side options** | Port the daemon (§10.2). Optional ADRs: `Rebias` RPC, server-side pool retrieval for trusted servers, acoustic retrieval (CTC-WS or phoneme posteriors). | Parity on conformance replays; latency gains; E4 recall gain for acoustic retrieval. |

---

## 13. Open questions [U]

1. **Midstream cost.** What does a `FULL` midstream update cost on parakeet_mlx and GPU-PB when a revision lands
   shortly before key-up? Does it re-decode at finalize, and how long does that take?
2. **Partials for retrieval.** Do partials from a *seed-biased* decode give better or worse retrieval than unbiased
   partials (E4)? Should the server offer an unbiased partial stream?
3. **Phonetics tooling.** CMUdict + g2p_en vs espeak-ng. How to pronounce code tokens (`ctx`, `kubectl`, `wl-paste`).
   Rust options.
4. **Thresholds.** θ, τ_gate, p_min, p_canon, `strength_seed` and the collateral-guard margin need dev tuning.
   Confidences are uncalibrated (ADR-001).
5. **Linux accessibility.** AT-SPI coverage on Hyprland: focus events, Chromium/Electron flags [S29], terminals. What
   does enabling the accessibility bus globally cost?
6. **Third-party defaults.** Should third-party providers receive P1 terms by default, or P0 only?
7. **First-pass strategy.** A `Rebias` RPC vs uploading the pool to trusted servers for Handy-style multi-decode
   selection.
8. **Session binding.** How to bind an agent session to a pid when several sessions share a cwd (open file
   descriptors? pi metadata?).
9. **Spec changes.** Extensions for `shortlist_used` (stage, evidence, revisions, gate), negative
   `offset_from_keydown_ms`, and the `adv_self` and `race` variants.
10. **Accessibility cost on macOS.** Does setting `AXManualAccessibility` on Electron apps have side effects? Electron
    notes a performance cost for building the tree [S28].

---

## Sources

Read on 2026-11-12 unless noted. "(search summary)" means only a search-engine summary or abstract was seen, so the
claim is marked [U] where used.

- [S1] Aqua Voice, *Deep Context*. https://aquavoice.com/deep-context
- [S2] Wispr Flow docs, *Context Awareness*. https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness
- [S3] Wispr Flow docs, *Use Flow with Cursor, VS Code and other IDEs*. https://docs.wisprflow.ai/articles/6434410694-use-flow-with-cursor-vs-code-and-other-ides
- [S4] Superwhisper docs (`modes/built-in.mdx` Super mode context awareness; `modes/custom.mdx`). https://github.com/superultrainc/superwhisper-docs
- [S5] VoiceInk docs, *Privacy and Data*. https://tryvoiceink.com/docs/privacy-and-data
- [S6] VoiceInk docs, *Context Awareness*. https://tryvoiceink.com/docs/context-awareness
- [S7] *Handy Context: Offline Context-Aware Dictation Without Screenshots, LLMs or Fine-Tuning*, More Than a Machine. https://morethanamachine.com/posts/handy-context
- [S8] Deepgram docs, *Keyterm Prompting*. https://developers.deepgram.com/docs/keyterm
- [S9] Song et al., *ScreenASR-Bench: A Tiered Benchmark for Speech Recognition with Visual Context*, arXiv:2610.16260. https://arxiv.org/abs/2610.16260 (HTML v1 read)
- [S10] Wang et al., *SlideSpeech: A Large-Scale Slide-Enriched Audio-Visual Corpus*, ICASSP 2024, arXiv:2309.05396. https://arxiv.org/abs/2309.05396 (HTML v3 read)
- [S11] *HearInContext: A Benchmark for Implicit Context in Speech Recognition*, arXiv:2609.18680. https://arxiv.org/abs/2609.18680 (search summary)
- [S12] Zhao et al., *Shallow-Fusion End-to-End Contextual Biasing*, Interspeech 2019. https://www.isca-archive.org/interspeech_2019/zhao19d_interspeech.pdf
- [S13] Alexandridis et al., *Gated Contextual Adapters for Selective Contextual Biasing in Neural Transducers*, ICASSP 2023. https://cdn.amazon.science/a1/76/7d5695df4d4092e150b6f01a230c/gated-contextual-adapters-for-selective-contextual-biasing-in-neural-transducers.pdf
- [S14] Jayanthi et al., *Retrieve and Copy: Scaling ASR Personalization to Large Catalogs*, EMNLP 2023 Industry. https://aclanthology.org/2023.emnlp-industry.60.pdf
- [S15] (reserved; Sathyendra et al., *Contextual Adapters for Personalized Speech Recognition in Neural Transducers*, ICASSP 2022, is cited only through [S13] and was not read)
- [S16] Lei et al., *Contextualization of ASR with LLM Using Phonetic Retrieval-Based Augmentation*, ICASSP 2025, arXiv:2409.15353. https://arxiv.org/abs/2409.15353 (HTML read); Apple page https://machinelearning.apple.com/research/asr-contextualization
- [S17] Flemotomos et al., *Optimizing Contextual Speech Recognition Using Vector Quantization for Efficient Retrieval*, SLT 2024. https://machinelearning.apple.com/research/optimizing-contextual
- [S18] Wu et al., *Deferred NAM: Low-latency Top-K Context Injection via Deferred Context Encoding for Non-Streaming ASR*, NAACL 2024 Industry, arXiv:2404.10180. https://arxiv.org/abs/2404.10180
- [S19] Ai et al., *PTC-Bias: Phoneme-Level Temporal Competition for Bias Retrieval and Post-Decoding Correction in Speech LLMs*, arXiv:2609.28727. https://arxiv.org/abs/2609.28727 (HTML v1 read)
- [S20] Gong et al., *BR-ASR: Efficient and Scalable Bias Retrieval Framework for Contextual Biasing ASR in Speech LLM*, Interspeech 2025, arXiv:2505.19179. https://arxiv.org/abs/2505.19179
- [S21] Yang et al., *CTC-Assisted LLM-Based Contextual ASR*, SLT 2024, arXiv:2411.06437. https://arxiv.org/abs/2411.06437
- [S22] Andrusenko et al., *Fast Context-Biasing for CTC and Transducer ASR Models with CTC-based Word Spotter*, Interspeech 2024, arXiv:2406.07096. https://arxiv.org/abs/2406.07096
- [S23] Andrusenko et al., *TurboBias*, ASRU 2025, arXiv:2508.07014. https://arxiv.org/abs/2508.07014 ; *TurboBias 2.0: Streaming Context-Biasing for Production-Efficient ASR Systems* (NVIDIA; submitted by V. Bataev; author order [U]), arXiv:2608.21343. https://arxiv.org/abs/2608.21343
- [S24] Pusateri et al., *Retrieval-Augmented Correction of Named Entity Speech Recognition Errors*, ICASSP 2025. https://machinelearning.apple.com/research/retrieval-asr
- [S25] *CB-Whisper: Contextual Biasing Whisper Using Open-Vocabulary Keyword-Spotting*, LREC-COLING 2024. https://aclanthology.org/2024.lrec-main.262.pdf (search summary)
- [S26] Hyprland wiki, *IPC* (`.socket2.sock` events `activewindowv2`, `windowtitlev2`; request-socket freeze warning). https://wiki.hypr.land/ipc/
- [S27] Apple developer docs, `kAXFocusedUIElementChangedNotification`, `kAXFocusedWindowChangedNotification`, `AXObserverAddNotification`. https://developer.apple.com/documentation/applicationservices/kaxfocuseduielementchangednotification (search summary)
- [S28] Electron docs, *Accessibility* (`AXManualAccessibility`; `app.setAccessibilitySupportEnabled` performance note). https://github.com/electron/electron/blob/main/docs/tutorial/accessibility.md (search summary)
- [S29] freedesktop AT-SPI2 wiki; trycua/cua issue #4768 (Chromium/Electron AT-SPI tree on Linux needs `--force-renderer-accessibility`). https://wiki.freedesktop.org/www/Accessibility/AT-SPI2/ , https://github.com/trycua/cua/issues/4768 (search summary)
- [S30] Apple Carbon Event Manager reference, `IsSecureEventInputEnabled`. https://leopard-adc.pepas.com/documentation/Carbon/Reference/Carbon_Event_Manager_Ref/index.html (search summary)
- [S31] NSPasteboard.org, *Identifying and Handling Transient or Special Data on the Clipboard*. https://nspasteboard.org/
- [S32] KeePassXC issue #12700 (`x-kde-passwordManagerHint: secret`). https://github.com/keepassxreboot/keepassxc/issues/12700 (search summary)
- [S33] Van Gysel et al., *Phonetically-Augmented Discriminative Rescoring for Voice Search Error Correction* (Apple). https://machinelearning.apple.com/research/phonetically-augmented (background: phonetic alternatives from ASR output plus rescoring)
