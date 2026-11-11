# hark dataset spec: context-aware dictation (v0.1 draft)

Status: draft for Phase 0. Owner: Dataset Designer. Companion: `sample.schema.json` (JSON Schema 2020-12).
`[U]` marks an uncertain item: a tool choice, threshold, or policy we must still validate.

Goal: measure, and later train, dictation that uses desktop, terminal, repo, and agent context. The main focus is
prompts dictated to coding agents in terminals; general app dictation is secondary. Linux first, macOS second.
The dataset holds real recordings of one user, made two ways: reading aloud real past prompts from
`~/.pi/agent/sessions`, and spontaneous dictation during real work. Each recording is paired with the context
snapshot taken at key-down and is evaluated under controlled context variants. TTS and voice-clone audio is
used for training only.

---

## 0. What we borrow and from where

| Source | What we take | What we change |
|---|---|---|
| **VoiceCodeBench** (openbenchmarks-labs/voice-code-bench) | Three text layers: `template`, `acoustic`, `canonical`. Entities carry `acoustic` and `canonical` forms. A 26-type taxonomy of structured tokens (cli_flag, file_path, code_symbol, env var, version, URL…). **CTEM** = correct target entities / targets. **TSR** = recordings with all targets correct. Format-invariant WER: each entity span accepts its acoustic *or* canonical rendering through an alternative-reference lattice DP. | Our layers are `verbatim` (spoken), `verbatim_canonical` (derived), and `intended` (what should be pasted). We use 9 coarse types and map each to `vcb_type`. Entity correctness comes from a deterministic located exact match, not VCB's LLM verifier. Our TSR is restricted to *critical* entities. |
| **ScreenASR-Bench** (arXiv 2610.16260; HF `MingweiFu/ScreenASR-Bench`: `caseid, ref, target, level, audio, keyframes, frame_captions`) | Evidence tiers ETE / EVE / ISE, which run from the keyword written on screen to a semantic inference. Keyword recall. **NVG** = (R_ctx − R_none)/(1 − R_none). They keep keywords only when the keyword is hard from audio alone. | Our tiers E1/E2/E3/E0 (E2 = *derivable*, e.g. an identifier split or a path component) are judged against the captured text context, not pixels. Screenshots are optional. |
| **HearInContext** (arXiv 2609.18680; OPPO-Mente-Lab) | Same audio under several context conditions (none / relevant / unrelated / explicit). `example_id = parent::condition::voice`. Paired bootstrap that resamples by `parent_id`. Pooled counts, not averaged percentages. Entity hits = min(R,H) occurrences. | Our conditions are correct / missing / stale / unrelated / adversarial. We cluster by dictation session. |
| **ContextASR-Bench** (arXiv 2507.05727) | Three context granularities: contextless, coarse (domain), fine (entity list). NE-WER and NE-FNR (= 1 − exact entity recall). TTS pipeline with a PER ≤ 0.03 pronunciation check through 2 ASR systems and G2P. | Fine-grained lists = our shortlists. The TTS pipeline is reused for **training augmentation only**. |
| **SlideSpeech** | Bias lists from different sources (noisy OCR vs curated keywords). U-WER / B-WER / Recall reported **per list source**. | Per-source ablations (`missing_partial:<source>`). |
| **Preference-ASR** (nvidia/preference-asr-bench) | A preference-aware normalizer that keeps the distinction under test, such as disfluency keep/remove, casing, or number format. Separate `text` vs `preference_text` references. | Our code-preserving normalizer N1, plus the separate `verbatim` vs `intended` references. |
| **LibriSpeech biasing** (Le et al., Interspeech 2021; fbai-speech `is21_deep_bias/score.py`) | Per-utterance bias lists = rare reference words + N random distractors (N = 100…2000). B-WER counts reference words in the bias set; U-WER counts the rest. Insertions are attributed by whether the inserted word is in the set. Alignment costs: sub 4, ins/del 3. | Our `forced_list:n=N` variants. B/U partition by the variant's list; a variant-independent E/N partition is added. |
| **HF datasets** | AudioFolder `metadata.jsonl` with `file_name` / `*_file_name` relative paths. Parquet with embedded audio for files under 1 MB (100 rows per row group). WebDataset tars for large-scale training. | Export targets only; JSON per sample is the source of truth. |
| **Lhotse** | Recording / Supervision / Cut JSONL(.gz) manifests. `custom` exists on SupervisionSegment and Cut. **`Recording` has no `custom` field** (checked in `lhotse/audio/recording.py`). Word `alignment` lives on supervisions. | Generated exports (§1.4). |
| **pi-share-hf** (badlogic) | Secret pipeline: exact-value redaction (env file + secrets list) → deny regexes → TruffleHog on the *redacted* output, where any finding blocks → LLM review → manual reject → upload. | Applied to every context blob and label (§1.8). The LLM review runs on a local model only. |

---

## 1. Layout, formats, IDs, versioning, splits, privacy

### 1.1 Repositories

- **hark code repo**: `docs/dataset/` (this spec, the schema, the labeling guide), plus the scorer, variant generator, and
  labeling CLI (planned: `hark-data`, `hark-label`, `hark-score`, `hark-replay`) [U names].
- **hark-data** is a separate private repo at `~/hark-data` on each host. It is never inside the code repo.
  Git tracks the JSON and manifests. Audio and blobs live in a content-addressed store synced with rsync/rclone
  between the laptop, desktop and Mac [U: git-annex or DVC instead].

### 1.2 Directory layout (hark-data)

```
hark-data/
  DATASET_CARD.md            # datasheet: motivation, composition, consent, limits
  CONSENT.md                 # owner self-consent scope; third-party consent records index
  registry/
    hosts.json  mics.json  speakers.json  repos.json   # repos.json: repo_id -> private name/URL (private tier)
    heldout_repos.json       # frozen at Phase 0
  inbox/                     # client drops capture bundles here (dataset mode); ingest moves them
  samples/YYYY/MM/<sample_id>.json        # SOURCE OF TRUTH, one file per sample (merge-friendly across hosts)
  audio/
    original/YYYY/MM/<sample_id>.flac     # device-native, lossless
    asr16k/YYYY/MM/<sample_id>.flac       # exact 16 kHz mono s16 signal streamed to ASR
  blobs/
    raw/sha256/ab/<sha256>.zst            # unscrubbed text (private_raw), encrypted at rest [U age vs LUKS/FileVault]
    scrubbed/sha256/ab/<sha256>.zst
  screenshots/<sample_id>.png.age         # opt-in only, encrypted
  repo_index/<repo_id>/<commit>.{files,symbols}.zst   # shared across samples; dedup by hash
  variants/<generator_version>/variants.jsonl         # $defs/variant records
  runs/<run_group_id>/
    run.json                 # system config (full), config_sha256, code commits, host, date
    outputs.jsonl            # $defs/system_output, one per (sample, variant, repeat)
    scores/<scorer_version>/{per_item.jsonl, summary.json, alignments.jsonl, report.md}
  manifests/                 # GENERATED; never hand-edited
    samples.jsonl.gz         # compiled from samples/
    samples.parquet  entities.parquet  variants.parquet   # flattened, DuckDB-friendly
    lhotse/{recordings,supervisions}.jsonl.gz  lhotse/cuts_<split>_<variant_kind>.jsonl.gz
    nemo/<split>.json        # audio_filepath/duration/text
    hf/<split>/metadata.jsonl  (+ parquet for the public subset)
  releases/<release>.json    # frozen split membership + label_versions + hashes
  tombstones.jsonl           # deleted sample_ids (never reused)
  tts/                       # training-only synthetic audio + its own samples/ tree (speaker.synthetic=true)
```

### 1.3 File formats

| Artifact | Format | Notes |
|---|---|---|
| Original audio | FLAC, device-native rate, channels, and bit depth (e.g. 48 kHz s24 stereo) | If the device only delivers compressed audio (BT mSBC/LC3), store what arrives and set `codec` and `bt_profile`. If the client only ever had 16 kHz, `original` and `asr16k` are the same file (`derivation: null` on both). |
| ASR audio | FLAC level 8, 16 kHz, mono, s16 | Must be **exactly** what the server received, after any client DSP; listed in `client_dsp`. No loudness normalization. `pcm_sha256` hashes the decoded PCM. Resampler, if any, is soxr VHQ, recorded in `derivation`. |
| Sample record | JSON (UTF-8, NFC, sorted keys, 2-space indent) | Validated against `sample.schema.json` plus semantic validator checks (§4.6). |
| Text blobs | zstd-compressed UTF-8, content-addressed by sha256 of the uncompressed bytes | Separate raw and scrubbed namespaces. Repo symbol and file lists are shared by hash. |
| Manifests | JSONL.gz (canonical exports), Parquet (analysis) | Regenerated by `hark-data build`. |
| Runs and scores | JSONL + JSON summary | `per_item.jsonl` holds raw counts so any slice can be re-aggregated. |

Duration limits: 0.3–180 s per sample. Null presses (key held, no speech) are allowed and labeled with empty gold.

### 1.4 Export compatibility

- **Lhotse.** `Recording(id=sample_id, sources=[{type:"file", channels:[0], source:"audio/asr16k/..."}], sampling_rate=16000, num_samples, duration)`.
  `SupervisionSegment(id=sample_id, recording_id=sample_id, start=0, duration, channel=0, text=gold.verbatim_canonical,
  language="en", speaker=speaker_id, alignment={"word":[(symbol,start,dur,score)…]}, custom={intended, entities(compact), profile,
  app_class, mode, label_version})`. Recording metadata goes into supervision/cut `custom` because `Recording` has none.
  There is one `MonoCut` per variant (`id=variant_id`, `custom={variant_kind, shortlist_terms, aliases, free_text_context,
  context_texts (scrubbed)}`), written per split and variant kind. Lhotse Shar export is used for H100 training [U].
- **NeMo**: `{"audio_filepath","duration","text"}` plus extra keys for context (NeMo ignores unknown keys) [U per-collate support].
- **HF**: `metadata.jsonl` with `file_name` (relative path to the 16 kHz FLAC) and flattened columns. The public subset ships as
  Parquet with embedded audio (clips are under 1 MB). WebDataset `.tar` shards (~1 GB) only for large TTS training sets.

### 1.5 Stable IDs

| Entity | Format | Rule |
|---|---|---|
| sample | `s_<ULID>` | Minted by the client at key-down. Monotonic ULID; unique across hosts; never reused (tombstones). |
| dictation session | `ds_<ULID>` | New if no dictation for 30 min, or the matched agent session changes. Bootstrap cluster. |
| repo | `r_<12 hex>` | HMAC-SHA256(dataset_key, normalized origin URL, or root-commit SHA if there is no remote). Hides private repo names. |
| speaker / host / mic | `spk_owner`, `spk_tts_<engine>_<voice>`, `spk_clone_owner_<engine>`; `host_<slug>`; `mic_<slug>` | Kept in registry files. |
| entity | `e1…` local; global key `<sample_id>#e1` | Local ids are stable across relabels: new entities get new numbers and deleted ids are not reused. |
| variant | `<sample_id>::<kind>[:<param>]::g<generator_version>` | e.g. `s_…::forced_list:n=100::g0.1`. Seed = first 8 bytes of sha256(variant_id). |
| run | `run_<ULID>`; run group dir `rg_<date>_<slug>` | Live outputs use variant `<sample_id>::live`. |
| blob | `blob:{raw|scrubbed}:sha256:<hex>` | Content address. |

### 1.6 Versioning and releases

- `schema_version` (semver) is in every record. Breaking changes ship with a migration (`hark-data migrate`).
- Every gold edit increments `gold.label_version`. Git history of `samples/` is the audit log.
- Independently versioned: `guideline_version` (labeling guide), `norm_version` (N0/N1/N2 rules), `scorer_version`,
  `generator_version` (variants and distractors), `ranker_version`.
- **Release** (`releases/eval-v0.json`): `{release, created_at, schema_version, norm_version, scorer_version,
  generator_version, splits:{dev:[[sample_id,label_version]…], test_time:[…], test_repo:[…]}, time_cutoffs,
  heldout_repos, manifest_sha256}`.
  Test releases are **frozen**. A gold fix creates `eval-v0.1` with an errata list, and the old release stays reproducible.
- Every reported number cites (release, scorer_version, norm_version, generator_version, system config_sha256).

### 1.7 Splits

The goal is to measure generalization to *new repos* and *future time* without leakage.

1. **Repo held-out.** At Phase 0, freeze ≥ 3 repos (≥ 15% of samples), including at least one repo whose main language is not
   the user's main language. Every sample whose context `repo_id` is held out goes to `test_repo`, for good.
   General-app samples (no repo) are never `test_repo`.
2. **Clusters.** Group samples by `session_id`. Merge clusters that share a `parent_id`, the same seed pi-session file, or
   near-duplicate seed text (MinHash Jaccard ≥ 0.8 on word 5-gram shingles).
3. **Chronological.** Sort the remaining clusters by start time. In Phase 0 the earliest third goes to `dev` and the rest to
   `test_time`. From Phase 1 on: oldest → `train`, then `dev`, newest → `test_time`. Cutoffs are recorded in the release.
4. **Speaker.** The owner appears in all splits. Other real speakers, if any are recorded with consent, go only to
   `test_speaker`. Synthetic speakers (TTS or clone) go **only to `train`**.
5. **Leakage guards** (enforced by `hark-data build`, which fails on violation):
   - TTS and LLM-generated training text must not near-duplicate dev or test text and must not use held-out repos'
     symbols or files.
   - Variant donors (stale/unrelated contexts) are drawn from the same split.
   - Distractor pools for dev and test exclude the sample's own repo. Pools for train exclude held-out repos.
   - Ranker, threshold, and prompt tuning use `dev` only.

### 1.8 Privacy, consent, licensing, public subset

**Tiers** (`privacy.tier`):
- `private_raw`: unscrubbed blobs, screenshots, original audio. Stays on the user's own machines (laptop, desktop, Mac),
  encrypted at rest [U: per-file `age` vs full-disk only].
- `private_scrubbed`: scrubbed blobs plus audio. May go to the university H100s on encrypted scratch, deleted after the job.
  No screenshots, no clipboard, no raw blobs [U: check the university data policy].
- `public_candidate` → `public`: see the public subset rules below.

**Capture-time protections** (client, dataset mode). These match the guardrails the product needs anyway:
- App denylist (password managers, banking, private messengers, …) gives `context.blocked.reason=denylisted_app`, so no
  context is captured. Audio is still kept unless the user drops it at triage.
- Password fields (AT-SPI/AX role) are never read. Private browser windows count as blocked.
- Clipboard is **off by default**. When off, nothing is stored, not even a hash, because short secrets could be recovered
  from a hash by dictionary attack. Per-session opt-in only.
- Screenshots are **off by default**. When on: focused window only, encrypted, OCR stored as a blob.

**Scrub pipeline** (`hark-data scrub`, adapted from pi-share-hf). It runs on every text blob, the window title, the cwd, and
the gold texts:
1. Exact-value redaction of known secrets: values from shell rc/env files, `.env*` files in captured repos, and an explicit
   secrets list. Each is replaced by a typed placeholder `⟦SECRET:<kind>:<n>⟧`, which the N1 tokenizer treats as one token.
2. High-precision format rules: private-key blocks, `AKIA…`, `ghp_…`, `sk-…`, JWTs, `Authorization:` headers, and URLs
   with credentials. $HOME → `~`; the OS username → `⟦USER⟧`.
3. Deny regexes (people, clients, private project names) block the sample from public tiers.
4. A detector backstop scans the **scrubbed** output with TruffleHog (or gitleaks) [U]. Any finding (verified, unverified, or
   unknown) blocks the sample from leaving `private_raw` (`detector_findings > 0`).
5. LLM review, `public_candidate` only, **local model only** → `pass | fail | manual`.
6. Manual review before `public`.

If the user *dictated* a secret, set `audio_contains_secret=true` and `gold.flags.contains_secret=true`. The audio never
leaves `private_raw`, and gold uses the placeholder.

**Consent.** The owner's self-consent and its scope (eval, training, public decisions per sample) are recorded in `CONSENT.md`.
Voice is biometric, so `voice_release_ok` is a separate decision from text release. Owner voice-clone models and clone audio
are private-only artifacts. Third-party *content* in context (colleagues' names, email threads, chat) sets
`third_party_content` and the sample is never public. Any non-owner speaker signs a consent record.

**Licensing.**
- Private data: all rights reserved.
- Public subset: annotations CC BY 4.0, audio CC BY 4.0 or CC BY-NC 4.0 (owner decides) [U].
- Context excerpts from OSS repos keep their repo license. Store `license_spdx` and the repo URL in the public card.
- Agent *assistant* text in session excerpts may fall under provider terms [U]. Public exports drop assistant text
  (`includes_assistant_text=false`) and keep only user turns and tool names.

**Public subset rules.** All of the following must hold:
- the context repo is public OSS;
- every scrub stage passed, plus manual review;
- `third_party_content=none`;
- `voice_release_ok`;
- no screenshots;
- not `audio_contains_secret`.

**Deletion.** Append to `tombstones.jsonl`, delete the audio and blobs, rebuild manifests, and add errata to affected releases.

---

## 2. Per-sample schema

The normative definition is `sample.schema.json`. A sample is the immutable capture (audio, recording metadata, context
snapshot, live shortlist and outputs) plus mutable gold labels and the observed user correction. Variants and offline runs
are separate records (`$defs/variant`, `$defs/system_output`) keyed by `sample_id`.

### 2.1 Field map

| Group | Key fields | Notes |
|---|---|---|
| ids | `sample_id`, `parent_id`, `session_id`, `created_at` | `created_at` = key-down time (UTC). |
| `collection` | `mode` (read_seed / spontaneous / scripted_entity / tts_synthetic), `seed{source, session_entry_id, seed_text, reading_style, seed_in_context_check}`, `selection{reason, inclusion_prob}` | `inclusion_prob` allows importance weighting once selection becomes active (Phase 1+). |
| `privacy` | tier, consent, scrub report, `audio_contains_secret` | §1.8 |
| `speaker` | `speaker_id`, `synthetic`, `synthesis{engine, voice, clone_of, text_source, spoken_form_input, pronunciation_check}` | |
| `audio` | `original`, `asr_16k` (path, sha256, pcm_sha256, rate, channels, duration), `client_dsp`, `key_up_s`, `speech{start,end,vad_model}`, `stream.chunk_log`, `quality{snr, rms, lufs, clipping}` | t=0 is key-down. `chunk_log` enables faithful streaming replay. |
| `recording` | host (os, desktop, display server, audio stack), mic (kind, BT profile, gain), environment (noise class, dBA, distance), client version/commit/config hash | |
| `target` | `app_id`, `app_class`, `profile` (agent_prompt, shell_command, chat, email…), `agent{name, version, detected_by}`, `input_kind`, `paste_method` | |
| `context` | `$defs/context_snapshot` at key-down: `origin` (live / live_reenacted / reconstructed), `offset_from_keydown_ms`, `blocked`, `focused{app, title}`, `focused_text{value, caret, selection}`, `terminal{visible_text, scrollback, cwd, fg process}`, `repo{repo_id, commit, dirty, file_list(+sha256), symbol_index(+sha256), indexer, recent_files}`, `agent_session{harness, session id, last_entry_id, match{method, confidence, candidates}, excerpt}`, `screenshot{ref, scope, redaction, ocr}`, `clipboard{opt_in,…}`, `history`, `user_lexicon` | Large texts are blob refs. `file_list_sha256` and `symbol_list_sha256` = sha256 of the sorted, unique, `\n`-joined list. |
| `shortlist_used` | `ranker_version`, `terms[{term, aliases, sources, score, weight}]`, `free_text_context`, `payload_sha256` | Exactly what the live client sent. |
| `outputs[]` | `$defs/system_output`: system config, shortlist, `asr{text, nbest, words(t, conf), unbiased_text}`, `canonicalized_text`, `cleanup{edits[find, replace, reason, applied, rejected_reason], timed_out, text}`, `final_text`, `fallback`, `guardrail_events`, `latency_ms{first_partial, keyup_to_asr_final, asr_final_to_cleanup_done, keyup_to_paste, rtf, network}` | Live outputs. Offline runs use the same item schema in `runs/`. |
| `gold` | §2.2 | |
| `user_correction` | `observed`, `source` (undo, swap-to-raw, history edit, post-paste field diff, **agent_session_submission**), `observed_text`, `delay_ms`, `char_edit_distance_from_pasted`, `match_confidence`, `trusted_as_label` | For agent prompts, the text actually *submitted* appears as the next user message in the pi session file. The diff against the pasted text is a free correction signal. Silver until confirmed. |

### 2.2 Gold labels

Three text layers, adapted from VoiceCodeBench's template / acoustic / canonical:

- **`verbatim.text`**: everything spoken, as plain lowercase-ish words, including disfluencies. Entities appear in their
  **spoken** form (`dash dash dry run`). Annotated with standoff `spans` (code-point offsets, half-open) of kind
  `filler | repetition | false_start | reparandum | repair | editing_term | command | noise | unintelligible | foreign`.
- **`verbatim_canonical`** (derived, validated): verbatim with each entity span replaced by its `canonical`. This is the
  reference for ASR-stage scoring.
- **`intended.text`**: what the user wanted pasted. Disfluencies are removed, self-corrections applied, voice commands
  executed, and punctuation, casing, and formatting follow the per-profile style in the labeling guide. Entities use
  canonical form. `intended.alternatives` lists genuinely equivalent forms (rare); the scorer takes the minimum over them.

**Entities** (`gold.entities[]`): `id`, `type`, `subtype`, `canonical` (byte-exact after NFC), `spoken[]` (where `[0]` is
what was said), `verbatim_span`, `intended_span` (null if the entity sits inside a reparandum), `critical`, `case_sensitive`,
`equivalents` (tiny; e.g. `do not` for `don't`), and
`grounding{in_repo_index, repo_symbol_kind, new_symbol, evidence_sources, evidence_tier, natural_competitors}` plus `vcb_type`.

| type | covers (subtype examples) | canonical examples | critical by default |
|---|---|---|---|
| `identifier` | function, method, class, type, variable, constant, module, package, env_var, branch, commit, command, config_key | `parse_config`, `useEffect`, `DATABASE_URL`, `feat/gpu-pb` | yes |
| `path` | file, dir, glob | `src/cache.rs`, `~/.pi/agent/sessions`, `**/*.toml` | yes |
| `flag` | cli_flag, flag_value | `--dry-run`, `-j8`, `--model=qwen3-asr` | yes |
| `number` | integer, decimal, version, port, line_number, issue_ref, duration | `8080`, `v0.3.1`, `#412`, `line 57` | yes |
| `url` | url, domain, email, ip | `https://…`, `localhost:3000` | yes |
| `name` | person, product, org, model, library | `Parakeet-TDT`, `Hyprland`, `SGLang` | if misspelling changes the referent |
| `negation` | not, never, no, n't-forms | `don't`, `never`, `no` | **always** |
| `literal` | spelled, quoted, regex, code snippet | `R I N A`, `"exact phrase"` | yes |
| `term` | domain jargon, not code-shaped | `label-looping`, `mTLS` | no |

Rules: label an entity whenever its spelling would not follow from ordinary English orthography, or whenever it is a
number or negation. `critical=true` means a wrong value would break the downstream action or meaning.

**Protected spans** (`gold.protected_spans[]`, offsets into `intended.text`): every critical entity, every number and
negation, quoted or dictated-verbatim text (`kind=quoted`), code, and user-marked spans. Cleanup must leave these byte-identical.

**Self-corrections** (`gold.self_corrections[]`): `kind` (replace / delete / restart / spelling_clarification),
`reparandum`, `editing_term` (e.g. "no wait", "I mean", "scratch that"), and `repair`, all as verbatim offsets.
**Commands** (`gold.commands[]`): spoken formatting or editing commands ("new line", "scratch that").

**Flags**: `unintelligible`, `contains_secret`, `truncated_start/end`, `background_speech`, `bias_flag` (§4.5),
`exclude_from_eval` (+reason).

**Example** (gold block only; offsets are real):

```json
{
  "label_status": "verified", "label_version": 3, "guideline_version": "0.1", "language": "en",
  "verbatim": {
    "text": "um so in parse config can you make the dash dash dry run flag skip the network calls no wait skip the uploads and don't touch src slash cache dot rs",
    "spans": [
      {"start": 0, "end": 2, "kind": "filler"}, {"start": 3, "end": 5, "kind": "filler"},
      {"start": 62, "end": 84, "kind": "reparandum", "ref": "c1"},
      {"start": 85, "end": 92, "kind": "editing_term", "ref": "c1"},
      {"start": 93, "end": 109, "kind": "repair", "ref": "c1"}
    ]
  },
  "verbatim_canonical": "um so in parse_config can you make the --dry-run flag skip the network calls no wait skip the uploads and don't touch src/cache.rs",
  "intended": {"text": "In parse_config, can you make the --dry-run flag skip the uploads, and don't touch src/cache.rs."},
  "entities": [
    {"id": "e1", "type": "identifier", "subtype": "function", "canonical": "parse_config", "spoken": ["parse config"],
     "verbatim_span": {"start": 9, "end": 21}, "intended_span": {"start": 3, "end": 15}, "critical": true,
     "grounding": {"in_repo_index": true, "repo_symbol_kind": "function", "new_symbol": false,
                   "evidence_sources": ["repo_symbols", "agent_session"], "evidence_tier": "E2"}, "vcb_type": "code_symbol"},
    {"id": "e2", "type": "flag", "subtype": "cli_flag", "canonical": "--dry-run", "spoken": ["dash dash dry run"],
     "verbatim_span": {"start": 39, "end": 56}, "intended_span": {"start": 34, "end": 43}, "critical": true, "vcb_type": "cli_flag"},
    {"id": "e3", "type": "negation", "canonical": "don't", "equivalents": ["do not"], "spoken": ["don't"],
     "verbatim_span": {"start": 114, "end": 119}, "intended_span": {"start": 71, "end": 76}, "critical": true},
    {"id": "e4", "type": "path", "subtype": "file", "canonical": "src/cache.rs",
     "spoken": ["src slash cache dot rs", "source slash cache dot r s"],
     "verbatim_span": {"start": 126, "end": 148}, "intended_span": {"start": 83, "end": 95}, "critical": true, "vcb_type": "file_path"}
  ],
  "protected_spans": [
    {"id": "p1", "span": {"start": 3, "end": 15}, "kind": "entity", "entity_id": "e1"},
    {"id": "p2", "span": {"start": 34, "end": 43}, "kind": "entity", "entity_id": "e2"},
    {"id": "p3", "span": {"start": 71, "end": 76}, "kind": "negation", "entity_id": "e3"},
    {"id": "p4", "span": {"start": 83, "end": 95}, "kind": "entity", "entity_id": "e4"}
  ],
  "self_corrections": [{"id": "c1", "kind": "replace", "reparandum": {"start": 62, "end": 84},
                        "editing_term": {"start": 85, "end": 92}, "repair": {"start": 93, "end": 109}}],
  "flags": {"unintelligible": false, "contains_secret": false}
}
```

---

## 3. Collection protocol and context variants

### 3.1 Collection modes

**A. `read_seed`: real past prompts read aloud (about half of Phase 0)**
1. **Extract.** Parse `~/.pi/agent/sessions/--<path>--/*.jsonl`. The header gives `cwd`. Take `type=message` entries with
   `message.role=user` and text content. Keep prompts that:
   - are 6–150 words of English prose;
   - contain no code fences, pasted logs (more than 3 non-prose lines), slash commands, or image-only content;
   - are not one-word confirmations.
   Scrub (§1.8), dedupe (MinHash), then **stratify** by repo (cwd → repo_id) and by entity density (count of N1
   code-like tokens, §5.1). Over-sample prompts with ≥ 2 code-like tokens.
2. **Re-enact the context** (`origin=live_reenacted`):
   - Make a `git worktree` at `git rev-list -1 --before=<seed_ts> HEAD`.
   - Open a terminal in that cwd and open a *fork* of the original pi session at the entry **before** the seed message.
     In `/tree`, select the preceding assistant entry; selecting the user message would put the seed text into the editor
     and contaminate the context.
   - Run hark normally. The paste lands in the pi editor; discard it (never submit in the original session).
   - [U] Check how faithful the reenactment is, and how cheaply pi can open a session at an entry.
   - Fallback: build the context offline from the session and repo at seed time (`origin=reconstructed`). Reconstructed
     samples are reported separately and never mixed into headline `live` numbers.
3. **Read.** The seed is shown on a **separate device** (phone or second host), never on a captured screen.
   `reading_style=natural_paraphrase` is the default: say it the way you would dictate it now, rephrasing is fine, and
   speak identifiers naturally. Spell only when you normally would. Gold is labeled from the audio, not copied from the seed.
4. **Contamination check.** Word 5-gram overlap between the normalized seed and every captured context blob. Two or more
   consecutive overlapping 5-grams that entity strings do not explain → `seed_in_context_check=contaminated`. The sample is
   then excluded from `correct`-variant headline metrics and may be re-recorded.

**B. `spontaneous`: real work (about 40%)**
- hark's **dataset mode** keeps every dictation (audio, context, live outputs) in `inbox/`.
- Daily triage takes 5 min or less with `hark-data triage`: keep / drop / private-only / secret.
- `selection.reason=all` in Phase 0. Phase 1 adds active selection (§6.2).
- These samples carry the natural distribution, real disfluencies and self-corrections, real `user_correction` signals,
  and naturally *legitimate* context, such as replying to an agent that just mentioned the symbol.

**C. `scripted_entity`: stress set (about 10%)**
- Templates fill in real entities sampled from the user's repos: flags with values, deep paths, versions, ports, URLs,
  env vars, negations ("don't run the migrations", "never push to main"), spelled sequences, and numbers in prose.
- The user still speaks naturally; the script fixes the *content*, not the wording.

**Across all modes:** rotate mics (builtin, wired or USB headset, BT headset) and environments (quiet, keyboard/fan, noisy),
and record a small number of `parent_id` re-recordings of the same seed on a second mic to measure mic robustness.
Include about 8 **null presses** (key held, cough or silence) to measure hallucination and context echo.

### 3.2 Context capture at key-down

The capture runs asynchronously and must not block audio. Budget: about 150 ms or less after key-down [U]. Timing goes into
`offset_from_keydown_ms` and `capture_duration_ms`. Caps: focused text within ±4,000 chars of the caret; terminal = visible
screen plus the last 200 lines of scrollback; agent excerpt = last 6 turns, capped at 8,000 chars; symbol lists uncapped
(blob, shared by hash).

| Field | Linux (Hyprland first) | macOS | Default |
|---|---|---|---|
| focused app/title | `hyprctl activewindow -j` (sway IPC / X11 fallbacks) | NSWorkspace + AX | on |
| focused text + selection | AT-SPI Text/EditableText (Chromium/Electron may need accessibility forced on) [U] | AX `AXValue`, `AXSelectedTextRange` | on, except password fields and denylist |
| terminal text | kitty `@ get-text`, `wezterm cli get-text`, `tmux capture-pane -p`; foot/alacritty lack an API → OCR or none [U] | iTerm2 Python API / Terminal.app AX [U] | on |
| cwd | OSC 7, emulator API, or `/proc/<fg-shell-pid>/cwd` | `lsof -p` / iTerm2 API [U] | on |
| repo | `git rev-parse`, `git ls-files -co --exclude-standard`, symbol index (universal-ctags or tree-sitter tags [U]), recent files by mtime | same | on |
| agent session | newest `~/.pi/agent/sessions/--<cwd>--/*.jsonl` by mtime; confidence from cwd match, mtime recency, and number of candidates (pid/tty when available) | same | opt-in |
| screenshot + OCR | `grim -g <focused geometry>` + tesseract [U] | `screencapture -l <wid>` + Vision OCR | off |
| clipboard | `wl-paste` | `NSPasteboard` | off |
| own history | last 5 dictations in this app | same | on |

### 3.3 Context variants

Each variant is a full `$defs/variant` record with its own context snapshot (blob refs), its own shortlist, distractors,
and `gold_in_context[]` (per gold entity: in shortlist? evidence tier?). Variants are deterministic: same `variant_id` gives
the same bytes. Shortlists come from the **current** `ranker_version` unless `forced=true`. The live shortlist is kept as
the pseudo-variant `live` so live outputs can be reproduced.

| kind | Construction | Measures |
|---|---|---|
| `correct` | The captured snapshot as-is. | Contextual gain. |
| `missing` | All context null, empty shortlist, no free text. **Mandatory anchor** in every evaluation. | No-context baseline; damage reference. |
| `missing_partial:<src>` | Correct, minus one source (`terminal`, `repo`, `agent_session`, `focused_text`, `screenshot_ocr`). | Per-source value (SlideSpeech-style). |
| `stale:<Δ>` (Δ ∈ {1d, 14d}) | Same `repo_id` and app. Donor = the latest snapshot (another sample, or a periodic context heartbeat in dataset mode [U]) with `captured_at ≤ t−Δ`. If there is no donor: repo index at `git rev-list -1 --before=t−Δ` plus the latest agent excerpt before t−Δ. Not generated if the repo is younger than Δ (logged in the coverage table). | Graceful degradation when context is out of date; entities introduced after t−Δ are E0. |
| `unrelated` | Donor = a same-split sample from a different `repo_id` (and a different language or domain where possible; general-app donors for terminal samples and vice versa). Constraints: **no** gold canonical (lenient match) anywhere in donor context, and no donor term within `τ_far` phone distance of a gold entity. Keeps the sample's own `focused.app_id` and `target`. | False insertions from irrelevant context (HearInContext's "unrelated"). |
| `adv_swap` | Correct context, but for up to 2 critical gold entities, *replace* every occurrence of the canonical (and its E2-derivable forms) in all text fields and the shortlist with its best `same_phones` or `near_phones` distractor. | **False contextual substitution.** Ideal output: not the distractor. EEM must not fall below `missing`. |
| `adv_add` | Correct context plus k=3 distractors per targeted entity (one each of `same_phones`, `near_phones`, `affix`); the gold term stays. | Discrimination among near-homophones. |
| `adv_common` | Correct context plus distractors near-homophonous to 3–5 *non-entity* content words in the utterance (e.g. said "cash" → inject `cache`; said "rust" → `Rusts`). | Damage to ordinary words (U-WER, FCS on plain words). |
| `forced_list:n=N` (N ∈ {0, 20, 100, 500}) | Bypasses the ranker: shortlist = gold entity canonicals + aliases, filled to N with `random_rare` pool terms, order shuffled. `n=0` = gold only (upper bound). | ASR biasing and gating in isolation; list-size scaling (LibriSpeech-style). |

Parameters: `inject=ranker|forced` for adversarial kinds. `ranker` puts distractors into the ranker's *inputs*, with feature
values set to the median of correct terms, so the ranker cannot trivially drop them. `forced` writes them straight into the
shortlist. Both are recorded in `params`.

### 3.4 Adversarial distractor construction

1. **Pool.** Symbols, file basenames, flags, and env vars from every *other* repo in the registry (dev and test exclude the
   sample's own repo; train excludes held-out repos), plus public corpora (symbol lists of large OSS repos such as cpython,
   the Linux kernel, react, and the Rust std library) and a names list. Each pool item has
   `{canonical, kind, source, source_repo_id, freq, spoken[], phones[]}`.
2. **`speak(term)` → spoken alternatives.**
   - Split identifiers on case, `_ - . /`, and letter–digit boundaries.
   - Separators are either silent or spoken ("underscore", "dash", "dot", "slash"); generate both.
   - Digits produce both a cardinal and a digit-by-digit reading.
   - All-caps tokens of 5 letters or fewer get a letter-spelled reading, plus a word reading if pronounceable or in the
     lexicon (JSON → "jason", "j s o n").
   - The abbreviation lexicon adds alternates (cfg→config, ctx→context, src→source, impl, env, dir), plus user-lexicon aliases.
   - This same function feeds the labeling tool's canonical lookup and the TTS spoken-form input.
3. **Phones.** CMUdict, falling back to a G2P model (g2p_en, or espeak-ng via phonemizer) [U]. Stress is stripped (ARPAbet).
4. **Distance.** `d(a,b)` = minimum over spoken alternatives of a weighted phone Levenshtein. Substitution cost comes from
   articulatory feature distance (PanPhon [U]) in [0,1]; ins/del cost 1; the result is normalized by the longer length.
5. **Classes.**

   | class | rule | example |
   |---|---|---|
   | `same_phones` | d = 0, canonical differs | `parseConfig` vs `parse_config`; `cache` vs "cash" |
   | `near_phones` | 0 < d ≤ τ_near | `passConfig` |
   | `affix` | word-level prefix/suffix | `parse_config_file` |
   | `common_word` | targets a non-entity word | |
   | `random_rare` | d > 0.6 | used by forced lists |

   Start with τ_near = 0.25 and τ_far = 0.6; tune on dev so humans judge "hard" pairs as confusable [U].
6. **Filters.** Reject a distractor when:
   - it equals gold canonical or equivalents;
   - it appears (lenient match) anywhere in gold intended;
   - it is a common English word, except in class `common_word`;
   - it contains placeholders.

   A candidate already present in the sample's *own* correct context is not injected. It is a natural competitor: list it
   in the entity's `grounding.natural_competitors` and report those entities separately.
7. **Select** per target: the best `same_phones`, `near_phones`, and `affix` candidates. Ties break by pool frequency, then a
   seeded RNG.
8. **Inject** realistically:
   - into the shortlist inputs (with median features);
   - into terminal text and agent excerpt by templates, e.g. a line of `ls` or `rg` output, or a sentence that mentions the
     symbol [U template realism];
   - in `adv_swap`, as in-place substitution of the gold string.

   Record `injection_sites`.
9. **Variant validation** (build fails on any violation):
   - regeneration is byte-identical;
   - `missing` has zero context bytes;
   - `unrelated` contains no gold strings (lenient);
   - `adv_swap` contains no targeted gold canonical or its E2 forms;
   - a coverage table reports kind × split counts.

---

## 4. Labeling workflow (single annotator)

### 4.1 Pipeline

```
capture → inbox → ingest(validate, hash, blobs) → triage(keep/drop/private/secret) → scrub
       → prelabel(ASR ensemble + forced align + local LLM proposal + repo canonical lookup)
       → correct (hark-label) → [test only] verify pass ≥24h later → release
```

### 4.2 Pre-labeling (`hark-data prelabel`, batch on the desktop GPU)

1. ASR ensemble *without* context: Parakeet-TDT (NeMo) and Whisper-large-v3 (or turbo). Add one *with* the correct context
   (Qwen3-ASR free-text). Word agreement across systems gives per-word confidence. Disagreements are highlighted.
2. Forced alignment of the best hypothesis gives word timings and per-word alignment scores (NeMo forced aligner / MMS / CTC
   segmentation [U]).
3. A local LLM with a JSON schema proposes `verbatim` (disfluencies kept), `intended`, entities (type, canonical, spoken),
   critical flags, and self-corrections. Inputs: the ASR outputs, the scrubbed context, the seed text (read_seed), and the
   profile style guide.
4. Deterministic canonical lookup: for each entity's spoken form, search the repo index with `speak()` matching
   (§3.4). Propose the canonical plus up to 5 candidates with symbol kind. This sets `grounding.in_repo_index`.

Pre-label bias risk: the pre-labeler sees the same kind of context as the systems under test, so shared errors could be
"confirmed". The mitigations are mandatory listening plus the blind re-label check (§4.5).

### 4.3 Correction tool (`hark-label`)

CLI with an editor round-trip, the fastest thing to build [U: a TUI later]:
- `hark-label next [--split test] [--mode read_seed]` plays audio through mpv: space = pause, ←/→ = ±2 s,
  `[`/`]` = replay the current word using the alignments, `s` = 0.75× speed.
- It opens `$EDITOR` on a YAML view. Pre-labels are filled in. Comments show the context excerpt, ASR disagreements, and
  low-confidence words.
- The verbatim field uses a compact inline markup that the tool converts to standoff spans. It is safe because verbatim
  holds spoken words only, never literal brackets.
  - `{um}` = filler; `{~the the}` = repetition; `{-we sh-}` = false start.
  - `<<skip the network calls>>((no wait))[[skip the uploads]]` = reparandum / editing term / repair.
  - `[parse config|parse_config|identifier/function!]` = entity (`!` critical, `?` not critical).
  - `{!new line}` = command.
- The intended field is plain text. Entities are located by canonical string; the tool asks when a match is ambiguous.
  Protected spans are derived automatically (critical entities, numbers, negations) and can be edited.
- On save the validator (§4.6) runs. On error the editor reopens with the errors as comments. On success
  `label_status=corrected`, `label_version += 1`, and `labeled_at` is set.

Throughput target: about 3–6 min per sample with good pre-labels [U].

### 4.4 Labeling guide (`docs/dataset/labeling-guide.md`, versioned; to write)

The guide covers:
- what counts as a filler (e.g. a turn-initial "so" or "okay" the user would not type);
- repetition vs emphasis;
- per-profile intended style:
  - agent_prompt: sentence case and punctuation, no backticks unless spoken, identifiers raw;
  - shell_command: no trailing punctuation;
  - chat: casual;
  - email: full sentences;
- how to label new symbols (`new_symbol=true`);
- spoken "quote … unquote" → `literal/quoted`;
- spelled letters;
- numbers: digits vs words per profile;
- when to use `intended.alternatives`.

Every ambiguous decision is logged in a decisions section and bumps `guideline_version`.

### 4.5 Self-consistency (no inter-annotator agreement; one user)

- **Two-pass for test.** A verify pass at least 24 h after correction: listen through at 1.0× with the label shown →
  `verified`. Dev gets one pass plus the automated checks.
- **Blind re-label (test-retest).** Re-label a stratified random 10% of test from scratch at least 7 days later, from audio
  only (repo symbol lookup allowed for spelling), with no pre-labels and no context. Compare the passes:

  | Measure | Target [U] |
  |---|---|
  | verbatim WER (N1) | ≤ 1.5% |
  | intended WER (N1) | ≤ 2% |
  | entity canonical agreement | ≥ 98% |
  | entity type agreement | ≥ 95% |
  | critical flag agreement | ≥ 95% |
  | disfluency span F1 | ≥ 0.85 |

  Misses lead to a guideline fix and a re-label of the affected category. Entity disagreements between the context-seen
  pass and the blind pass set `gold.flags.bias_flag` and are adjudicated by listening.
- **Intra-label drift.** Each release re-labels 10 old samples and tracks agreement over time.

### 4.6 Automated validator (`hark-data validate`; runs at save, in CI, and at build)

1. **Schema.** JSON Schema validation. Offsets are within bounds and non-overlapping where required. NFC. No trailing spaces.
2. **Text consistency.** `verbatim.text[entity.verbatim_span] == entity.spoken[0]`. `intended.text[intended_span] == canonical`.
   `verbatim_canonical` re-derives exactly. Every protected span sits inside `intended.text`. Every negation entity is protected.
3. **Alignment.** Align `intended` to `verbatim_canonical` minus disfluency spans (N1, lenient case). More than 2 unexplained
   insertions or deletions → flag.
4. **Format.** Per-subtype regexes, e.g. flag `^-{1,2}[A-Za-z0-9][\w.-]*(=\S+)?$`, a version regex, URL parse, paths without
   spaces unless quoted.
5. **Grounding.** An identifier or path with `in_repo_index=false` and `new_symbol=false` → flag.
6. **Cross-sample.** The same (repo_id, lenient spoken[0]) maps to the same canonical within 30 days unless `new_symbol` or a
   rename. The same canonical always gets the same type.
7. **Acoustic.** Per-word forced-alignment score below threshold, or all ensemble ASR systems agree on a word that differs
   from the label → re-listen flag.
8. **Seed drift.** For read_seed, a verbatim-vs-seed WER above 30% → flag (possible misattribution).
9. **Privacy.** No raw blob refs in any record above `private_raw`. Placeholders intact.

---

## 5. Metrics and scoring

**Item** = (sample, variant, system, repeat). **Stages** of a system output:
- `asr` = `asr.text`;
- `canon` = `canonicalized_text`, falling back to asr;
- `final` = `final_text`.

**References:**
- `asr` and `canon` are scored against `verbatim_canonical`, with optional disfluency tokens.
- `final` is scored against `intended` (minimum over alternatives).

Every metric is computed from **pooled counts** (never from averaged per-item percentages) and reported per variant kind,
split, and slice: profile, mode, OS, mic, noise, entity type, evidence tier, `origin`.

### 5.1 Normalization (`norm_version = n-0.1`; the scorer's unit tests are normative)

- **N0 (unnormalized).** NFC; collapse whitespace; tokens = whitespace split. Case, punctuation, quotes, dashes, and newlines
  are all significant (newlines count as a `⏎` token). Measures paste-exactness.
- **N1 (code-preserving, primary).**
  1. NFC. NBSP and thin spaces become space. Curly quotes become straight; `…` becomes `...`. **Dashes are not mapped**:
     `—dry-run` ≠ `--dry-run`, because apps that autocorrect `--` are a real failure.
  2. Split on whitespace (newline = whitespace). Placeholders `⟦…⟧` are atomic tokens.
  3. A token is **code-like** if it matches
     `[_/\\.:@#$=<>{}\[\]()`~|*]` internally, `[a-z][A-Z]`, a letter–digit mix, or `^-{1,2}[A-Za-z0-9]`.
  4. Strip the leading characters `( [ { " '`, and a trailing `)`/`]`/`}` only if unmatched inside the token. Strip trailing
     `" ' , ; : ! ?` and one trailing `.` (all trailing dots for `...`). Never strip a token to empty, and never strip a
     leading `. - ~ / @ # $`. Drop tokens made only of prose punctuation `[.,;:!?"'()…—–]`.
  5. Lowercase a token only if it matches `^[A-Z]?[a-z]+('[a-z]+)?$` (plain or capitalized words). All-caps, camelCase,
     and code-like tokens keep their case.
  6. No number or contraction normalization (digits vs words is part of hark's job).

  Test vectors:

  | input | N1 output |
  |---|---|
  | `Edit src/cache.rs.` | `edit src/cache.rs` |
  | `(see parse_config)` | `see parse_config` |
  | `call foo()` | `call foo()` |
  | `The API` | `the API` |
  | `"--dry-run",` | `--dry-run` |
  | `cd ..` | `cd ..` |
  | `use .env` | `use .env` |
  | `Wait...` | `wait` |

- **N2 (literature-comparable).** A pinned Whisper `EnglishTextNormalizer` [U pin a version, as HearInContext does] on
  non-entity chunks. Entity chunks are a lattice of {normalized canonical, each normalized `spoken[i]`}, following
  VoiceCodeBench's format-invariant WER.

### 5.2 Alignment

- Token Levenshtein with costs **sub 4, ins 3, del 3** (as in fbai `score.py` / sclite) for the backtrace. Each S, D, or I
  counts as one error. Deterministic tie-break: diagonal, then deletion, then insertion.
- **Lattice references.** The reference is a sequence of chunks, and each chunk is a set of alternative token sequences:
  - plain text: a single alternative;
  - optional disfluency (`filler`, `repetition`, `false_start`, `editing_term`, `noise`): {tokens, ∅};
  - N2 entity: {canonical, spoken…}.

  The DP keeps the minimum-cost state over alternatives at chunk boundaries (VoiceCodeBench
  `compute_alternative_reference_wer`, extended with ∅ alternatives). **Denominator** = token count of the canonical,
  disfluency-free path, so it is fixed per item regardless of the path the DP picks.
- Each hypothesis token keeps its char offsets in the raw (N0) hypothesis, so located entity checks run on raw text.

### 5.3 Metric definitions

| Metric | Definition | Stage(s) | Headline variants |
|---|---|---|---|
| **WER / CER (N1)** | (S+D+I)/N_ref on N1 tokens; CER on N1 tokens joined with spaces | asr vs verbatim_canonical (lattice, optional disfluencies); final vs intended | correct, missing |
| **WER / CER (N0)** | The same on N0, final vs intended. CER-N0 approximates the user's edit effort. | final | correct |
| **WER (N2, format-invariant)** | Lattice WER with spoken/canonical alternatives | asr | missing (comparable to the literature) |
| **E-WER / N-WER** | WER restricted to reference tokens inside / outside gold entity spans (canonical tokens). An insertion counts toward E if it falls between hyp tokens aligned to the same entity span. Variant-independent partition. | all | all |
| **B-WER / U-WER** | Le et al.: B = reference tokens that are part of a term in the variant's shortlist L_v; U = the rest. An insertion goes to B iff the inserted token is part of a term in L_v. To compare *across* variants of one sample, use the partition from L_correct ("anchored B/U"). | asr, final | correct, forced_list, adv_* |
| **Exact entity match (EEM)** | Located, byte-exact (§5.4). Rate = hits / gold entities (excluding entities with `intended_span=null` at final). NE-FNR = 1 − EEM. Per type and evidence tier. | all | all |
| **Lenient entity match** | Same location, compared case- and separator-insensitively (`_ - . / space` removed). EEM − lenient separates "heard right, formatted wrong" from "misheard". | all | all |
| **ACEC** (all critical entities correct) | Items where every critical entity is EEM-exact **and** there is no negation flip and no protected-span violation, over items with ≥ 1 critical entity (n reported). The critical-only analogue of VCB TSR. | final | correct |
| **NVG** | (EEM_v − EEM_missing)/(1 − EEM_missing) (ScreenASR-Bench). Negative = context hurt. | final | every v |
| **FCS / 1k words** | §5.5 | asr, final | unrelated, adv_*, forced_list:100 |
| **Context echo rate** | Items where the hyp contains ≥ 6 consecutive N1 tokens copied from the free-text context or excerpt that are not aligned to matching reference tokens. On null presses: any non-empty output = hallucination. | asr | all (key for free-text-prompt providers) |
| **Damage / gain** | §5.6 | stage pairs, variant pairs | missing→{correct, unrelated, adv_*}; canon→final |
| **Protected-span violation rate** | §5.7 | canon→final | correct |
| **Negation flip count** | §5.7; target 0 | all | all |
| **Disfluency P/R/F1**, **self-correction resolution** | §5.8 | canon→final; oracle track | correct |
| **Sentence exact rate** | final == intended (or an alternative) under N1 / N0 | final | correct |
| **Latency** | §5.9 | live, replay | correct |

### 5.4 Locating entities (exact match)

1. Align the hyp (stage) to the reference with §5.2. The entity's reference span maps to reference token indices [i, j).
2. Hyp window = hyp tokens aligned (match or sub) to [i, j), plus insertions between them. If the span boundary touches a
   deletion or insertion, widen the window by one hyp token on each side. Take the **raw N0 substring** that covers the window.
3. **Exact hit** iff `canonical` (or an `equivalents` entry) occurs in that substring with boundaries: the preceding char is
   start, whitespace, or one of `( [ { " ' `` ` ``; the following char is end, whitespace, or one of `) ] } " ' `` ` `` . , ; : ! ?`.
   So `` `src/cache.rs` `` passes, `src / cache.rs` fails, and `src/cache.rs.` passes.
4. Count with multiplicity: each gold entity occurrence is located separately (similar to HearInContext's min(R,H)).

### 5.5 False contextual substitution (FCS)

For an item with variant v:
- T_v = shortlist canonicals ∪ injected distractors. For free-text-context providers, also every code-like token in
  `free_text_context`.
- Scan the stage hyp (N0) for boundary-respecting exact occurrences of any t ∈ T_v, multi-word terms included.
- For each occurrence: map it to hyp tokens, then to aligned reference tokens (∅ for an insertion). It is an **FCS** iff t
  does not occur exactly in the aligned reference region and t is not the canonical or an equivalent of a gold entity located
  there.
- `FCS_abs/1k = 1000 · ΣFCS / ΣN_ref` (N1 reference tokens).
- `FCS_induced/1k`: the same, excluding occurrences where the same system's `missing` output also has t aligned to the same
  reference index. This is the headline because it isolates harm caused by context.
- `FCS_lenient`: the matching is case- and separator-insensitive. This is needed for `adv_common`, where the distractor
  `Cache` produces "cache".

### 5.6 Damage on already-correct (and gain)

Anchor A and treatment T share the backend and the item's audio. Pairs:
- variant: `missing`→`correct`, `missing`→`unrelated`, `missing`→`adv_*`;
- stage: `asr`→`canon` (canonicalizer) and `canon`→`final` (cleanup).

Definitions:
- **Entity damage** = #{entities exact in A ∧ not exact in T} / #{exact in A}.
  **Entity gain** = #{not exact in A ∧ exact in T} / #{not exact in A}.
- **Token damage**: the same over reference tokens classified as matched or unmatched by alignment.
- **Item damage** = #{A perfect ∧ T not perfect} / #{A perfect}. Perfect = N1 WER 0 against the stage reference and all
  entities exact.

Report damage next to gain. The design goal (from research) is near-zero damage at positive gain.

### 5.7 Protected spans and negation

- Let P = the pre-cleanup text (`canon`) and F = `final`. Map each gold protected span from `intended` to `verbatim_canonical`
  through the intended↔verbatim_canonical alignment. That alignment is not stored: the scorer recomputes it deterministically
  with the same procedure as §4.6 check 3. Then locate the span in P and in F with §5.4.
- **Violation** = located exactly in P and not located exactly in F.
  **Rate** = violations / spans present in P.
  Also report "repaired by cleanup" (absent from P, exact in F).
- **Negation flip**, at any stage:
  - (a) a gold negation not located in the hyp;
  - (b) a hyp negation token (closed list: not, n't-forms, never, no, cannot, nothing, none) aligned to a reference position
    with no negation.

  Both count; report the count and the rate per 100 negations.

### 5.8 Cleanup: disfluency P/R and self-corrections

- Label P's tokens by aligning them to `verbatim_canonical`. Disfluent = aligned to a span of kind
  filler / repetition / false_start / reparandum / editing_term; insertion tokens are excluded.
- Removed = P tokens with no match in the P→F alignment.
- TP = removed ∧ disfluent; FP = removed ∧ fluent; FN = kept ∧ disfluent. Report P, R, and F1 per kind.
- An FP on an entity or protected token is also a protected violation.
- **Self-correction resolution** = share of gold self-corrections where F contains the repair's content tokens and none of
  the reparandum-only content tokens.
- **Oracle track**: run cleanup with P := gold `verbatim_canonical` to isolate cleanup from ASR errors.

### 5.9 Latency

- Client monotonic timestamps:
  - `keyup_to_paste` (primary; budget ≤ 1000 ms p95 on LAN);
  - `keyup_to_asr_final`, `asr_final_to_cleanup_done`, `first_partial`;
  - RTF.
- Live numbers come from `outputs` (real conditions).
- **Replay** (`hark-replay`) streams `asr_16k` in real time using `chunk_log` pacing and signals key-up at `key_up_s`. Each
  item is replayed R=5 times per config, so p95 rests on more than 1,000 observations (latency does not depend on labels).
- Report p50 / p90 / p95 / p99, the share over 1 s, and breakdowns by audio-duration bucket (<5, 5–15, 15–30, >30 s),
  network (LAN, remote over Tailscale, offline CPU), and backend. CI by cluster bootstrap on the quantile [U: rough at n≈200
  sessions-limited; prefer replay repeats].

### 5.10 Paired bootstrap by session

```
clusters = session_id groups (merged across parent_id / seed-file links, §1.7); all variants and repeats of a sample stay together
for b in 1..B (B = 10_000, fixed seed recorded):
    draw |clusters| clusters with replacement
    for system in (A, B): pool raw counts over drawn items → metric (ratio of sums)
    Δ_b = metric_B − metric_A
report Δ on the full set, the 95% percentile CI of Δ_b, and P(Δ_b improves)
```

- Slices with fewer than 30 clusters are reported as "insufficient clusters" (point estimate only). Phase 0 caps sessions at
  about 15 samples each, splitting long sessions into time blocks [U], to keep the cluster count up.
- Pre-registered primary family: ACEC, EEM, FCS_induced/1k, item damage, protected violation rate, and p95 latency.
  Holm correction within the family [U]. Everything else is exploratory.
- Per-item output (`per_item.jsonl`) stores raw counts (S, D, I, N per partition; entity hits by id; FCS events; damage flags;
  latencies), so any re-aggregation and bootstrap is exact.

---

## 6. Sizes, growth, TTS policy

### 6.1 Phase 0: 300 real samples (all eval, no training)

| Dimension | Target |
|---|---|
| Split | dev 100 / test 200 (test_repo ≈ 60 from ≥ 3 held-out repos + test_time ≈ 140) |
| Mode | read_seed 150, spontaneous 120, scripted_entity 30 |
| Profile | agent_prompt ≈ 195, shell_command ≈ 15, general app ≈ 90 (chat 30, email/doc 30, browser/search/notes 30) |
| Host | Linux (Hyprland) 240, macOS 60 |
| Mic | builtin ≥ 120, wired/USB headset ≥ 90, BT headset ≥ 45 |
| Environment | quiet ≥ 180, keyboard/fan ≥ 60, noisy (cafe/music/background speech) ≥ 30 |
| Duration | 2–90 s, median 10–15 s, ≥ 30 samples over 30 s |
| Repos | ≥ 12 distinct |
| Entities | ≥ 900 gold (≥ 600 critical). Per type ≥ 40, with negation ≥ 50, url ≥ 25 [U: feasible from natural prompts], literal ≥ 25 |
| Evidence tiers | report the natural distribution; aim for ≥ 150 E1/E2 critical entities in test |
| Self-corrections | ≥ 60 samples |
| Special | 8 null presses; 20 `parent_id` mic pairs; ≥ 20 samples with `user_correction.observed` |
| Sessions | ≥ 30 clusters in test |

Variants per sample: correct, missing, 2–3 partials, stale (about 70% coverage), unrelated, adv_swap, adv_add, adv_common,
forced_list n ∈ {0, 20, 100, 500}. That is about 12 variants and about 3,600 items per system configuration, cheap in batch
mode. Effort: about 3–4 h of recording sessions and about 15–30 h of labeling [U].

Statistical expectation: with 200 test items, a binary metric near 50% has a CI half-width of about ±7 pp. Paired
comparisons are tighter. Phase 0 detects **large** effects (≥ 8–10 pp ACEC, or FCS rising from ~0 to > 2/1k), not subtle ones.

### 6.2 Growth plan

| Phase | When [U] | Real samples | Changes |
|---|---|---|---|
| 0 | weeks 1–4 | 300 | Freeze `eval-v0` (dev/test). Freeze held-out repos. Lock the norm, scorer, and generator versions. |
| 1 | months 2–3 | +700 → 1,000 | Dataset-mode logging with **active selection**: 50% random (unbiased estimates) + 50% signal-driven (low confidence, observed user correction, ≥ 2 code-like tokens, new repo). `inclusion_prob` is recorded for weighting. Build `test-v1` = `eval-v0` test + ~150 newer samples + new held-out repo(s). The rest becomes `train`. macOS share rises to ~30%. |
| 2 | months 4–6 | 3,000–5,000 | Mostly **silver** labels for training: confirmed `user_correction` and agent-submitted text, 10% audited. Gold for dev and test only. Uses: training the ranker, gating thresholds, and the cleanup model, and LoRA/adapter ASR on the H100s [U]. |
| ongoing | monthly | +50 test_time | A drift slice of new repos and vocabulary. Old tests stay frozen for regression. |

### 6.3 TTS / voice-clone augmentation (training only)

- **Never** in dev or test, and never in headline metrics. Records carry `speaker.synthetic=true` and the full `synthesis{…}`
  block, stored under `tts/`.
- **Text sources:**
  - scrubbed pi-session user prompts from *train-split* clusters;
  - LLM-generated agent prompts that mention real symbols from non-held-out repos;
  - entity templates (flags, paths, versions, negations);
  - disfluency injection (fillers, repetitions, "no wait" repairs) for cleanup training. Its gold is generated
    programmatically from the injected structure.
- **Spoken-form input.** TTS receives `speak()` output ("use effect", "dash dash dry run") and never the canonical string.
  Gold canonical comes from the template. `spoken_form_input=true`.
- **Voices.**
  - Multi-speaker zero-shot TTS with licensed reference voices [U: check engine and reference-voice licenses].
  - An owner voice clone built from ≥ N minutes of owner train-split audio (never from dev or test audio). Private only;
    the model and outputs are never published.
- **Quality gate** (ContextASR-Bench style): transcribe with 2 ASR systems and compute PER (G2P) against the spoken-form text.
  Keep only PER ≤ 0.03 [U]. For identifier spans, gate with forced alignment of the spoken form rather than ASR, because ASR
  misrecognizes exactly these. Allow 3 retries with a new voice or seed.
- **Acoustic augmentation** (train only, Lhotse transforms): real noise recorded in the user's rooms, RIRs, codec/BT
  simulation (mSBC 16 kHz), gain and speed perturbation [U: MUSAN license fit].
- **Contamination guards** (§1.7): no near-duplicates of dev/test text, no held-out repo vocabulary, and clone reference audio
  from train only.
- **Mix and ablation.** At most 3 synthetic hours per real hour [U]. Every trained model is reported with and without
  synthetic data, and on real `test` only.

---

## Appendix A: open items [U] to resolve in Phase 0

1. AT-SPI coverage per terminal (foot, alacritty, ghostty) and Electron apps on Hyprland, and the macOS terminal APIs.
2. Pi session fork-at-entry workflow for reenactment, and how faithful it is in practice.
3. G2P and phone-distance tooling (CMUdict + g2p_en/espeak-ng, PanPhon) and the τ thresholds.
4. Symbol indexer (universal-ctags vs tree-sitter tags) for repo indices across languages.
5. Secret detector (TruffleHog vs gitleaks) and at-rest encryption method; the university H100 data policy.
6. Public licensing of the owner's voice; provider terms for assistant text in excerpts.
7. Labeling throughput and blind re-label targets.
8. Quantile bootstrap adequacy for p95, and whether the Holm family is the right size.
9. Context heartbeat snapshots (needed for natural `stale` donors) and their storage cost.
