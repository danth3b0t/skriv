# Competitive analysis: hark × Voxtype × Superwhisper

**Research date:** 2026-11-11. **Scope:** developer dictation on a CPU-only Linux/Hyprland/Omarchy laptop, backed by user-owned inference machines; macOS second. This is a recommendation document, not an implementation plan committed to delivery.

## Executive judgment

**Keep hark's recognition-time context and client-enforced safety; adopt the competitors' delivery reliability, recovery, and visible state.** A better generic rewriting assistant is not the best differentiation.

- **Voxtype is the integration reference:** compositor-native control, explicit output-driver fallback, modifier-release protection, accessible CLI/TUI/settings surfaces, and model/setup diagnostics. Its current source also has profile vocabulary and validated JSON edit lists: do not describe it as merely Whisper plus shell substitution.
- **Superwhisper is the product reference:** a small recording surface, obvious active mode/context, history that preserves failures and permits reprocessing, separate recognition vocabulary and exact replacements, correction learning, and model selection that does not require editing service configuration.
- **Both now compete on Linux.** Superwhisper's Linux release is real, with Wayland support, offline inference, history, learned vocabulary, an edge glow, and Omarchy integration. Voxtype also supports Apple Silicon macOS. Old “Linux CLI versus Mac GUI” comparisons are obsolete.
- **hark's advantage is architectural, not yet product completeness:** short context-ranked identifiers enter ASR before decoding; an unbiased anchor and word evidence support canonicalization; edits are validated by the client; terminal output never sends Enter. Its current Python POC is not the future Rust client, and its current ASR streams transport audio but buffers inference.

**First investments:** delivery diagnostics and safe recovery; a non-focus-stealing status surface; scoped vocabulary/replacements; replayable history with explicit retention. Then measure and ship the laptop CPU fallback. Do not begin with meetings, broad agent control, or a large model catalog.

## Evidence standard and version caveats

- **[H]** = local hark documentation/source inspected. **[V…] / [S…]** = public sources listed in §7. **[L]** = read-only observations from the installed Mac application/configuration, detailed in §5.
- **[U] means unverified, uncertain, conflicting, or requiring measurement.** “Not found” is not proof that a proprietary implementation lacks a feature. Product documentation verifies what the vendor says, not its performance/security guarantees.
- Voxtype source snapshot: `peteonrails/voxtype`, `dev`, commit `fbedd62537d5334dd7e7416189db21549184b034`. News lists **1.2.1, November 10**, and **1.2.0, November 9**. Source-only observations below are explicitly observations of that snapshot; inclusion of every feature in a released binary is **[U]**.
- Superwhisper docs repository snapshot: `superultrainc/superwhisper-docs`, commit `86cc0811e7eae16db7adbfc37eea90df9d117ad0`. Installed Mac app: **3.2.2**, matching the November 9 changelog. Linux latest release at inspection: **1.3.1**, November 11. Mac documentation is **not** evidence of Linux feature parity.
- No apps or servers were launched, stopped, reconfigured, or interacted with. No microphone recordings, inference benchmarks, or paste tests were performed. UX observations come from documentation, release notes, distributed strings/resources, and metadata schemas—not live dogfooding.

### Important source disagreements

1. **Voxtype's older guides are stale in places.** `PARAKEET` still says English-only in its limitations, while `MODEL_SELECTION_GUIDE` describes multilingual v3; engine counts and ONNX-only advice also conflict with 1.2.0's expanded GGUF catalog. Use model-specific capabilities and current release notes, not one global “Parakeet supports X” assertion. [V2–V4]
2. **hark's server README contains a stale “boosting not wired up” paragraph.** Its capability table and `server/src/hark_server/backends/parakeet_mlx.py` show actual boosted TDT beam decoding with a cached greedy anchor and a deletion guard. This report follows the source. CTC takes a different, unboosted path. [H]
3. **S1 benchmark numbers vary by publication:** the launch blog reports 6.8% average WER; current voice-model docs report 6.6%. Neither is an independently reproduced hark comparison. [S4, S13]
4. **Superwhisper context capture is not one atomic snapshot:** selection at recording start; clipboard within three seconds before/during recording; application context after ASR. This is documented and matters when switching windows. [S3]

## 1. Feature matrix

“Current” for hark means the inspected POC, not everything described in `docs/00-context.md` or ADR-001.

| Dimension | hark — current and gaps | Voxtype — documented / inspected source | Superwhisper — documented / local evidence |
|---|---|---|---|
| **Capture and hotkeys** | Mac Right Option hold/release; Esc cancels but leaks to the target app. Linux compositor → Unix control socket: start/stop/toggle/cancel/status. Persistent mic and 200 ms pre-roll; 300 ms minimum and 120 s default maximum. Client VAD absent. | evdev PTT/toggle plus compositor commands; per-recording profiles/modifier keys; cancel recording or transcription; mouse hotkeys in recent release. Experimental ONNX Parakeet live typing requires toggle because synthetic output interferes with held-key release. [V1, V2, V4] | Mac toggle and PTT, quick-tap toggle, mouse shortcuts, mode-specific shortcuts, Esc; confirmation for recordings ≥30 s. Linux Hyprland Right Alt hold/release, mouse buttons, Esc; supported desktop paths differ. [S7, S10, S12] |
| **ASR models and biasing** | Real MLX Parakeet TDT boosting from ranked canonical terms/aliases, greedy anchor from shared encoder, word timings and uncalibrated confidence; N-best currently 1. Mock works; NeMo GPU-PB is a stub. No laptop CPU backend or provider fallback yet. | Whisper local/remote, Soniox cloud; ONNX families and newer GGUF families including Parakeet, Cohere, Qwen3-ASR, Moonshine, SenseVoice, GigaAM, Nemotron. Whisper prompt and profile terms; source documents bias forwarding to Whisper-compatible/Soniox paths. Do not assume native Parakeet boosting or hark-like anchors. [V1–V4, V7] | Local Whisper/Parakeet/Cohere; cloud S1-Voice/Nova/Scribe, model-specific realtime and diarization. Vocabulary recognition hints; installed binary also names an audio/phoneme vocabulary correction component. Exact per-model bias mechanism, anchor exposure, and N-best contract **[U]**. [S4, S6, L] |
| **Vocabulary, replacements, learning** | Repo, selection, window and agent-derived terms; generated spoken aliases and context-ranked canonicalization. Persistent user lexicon, explicit replacement management and feedback learning are not established POC features. | Profile `vocabulary.terms`/JSON terms file; conservative phonetic + edit-distance final-text correction, ambiguity abstention, URL/path/email protection, then case-insensitive word-boundary replacements. Automatic correction learning not established **[U]**. [V1, V2, V7] | Distinct recognition hints and deterministic case-insensitive replacements; CSV import. Linux advertises correction learning; 1.3.0 adds consent-first, local suggestions from agent prompts, notes and commits. Mac binary contains correction-detection rejection rules. [S6, S12, S14, L] |
| **Context sources** | Focus/app/title/selection → terminal cwd → repo and agent session files, under 150 ms collection deadline; 60 terms by default. OCR, broader accessibility text and history/alias learning are design goals, not all implemented. Agent-session opt-in is an architectural intention: inspected `context/engine.py` attempts session discovery when cwd exists; do not claim that policy gate is complete. | Explicit profile vocabulary and Whisper initial prompt; `VOXTYPE_CONTEXT` carries previous dictation within 60 s to post-processing; meeting context separated by audio source. Automatic repo/app/selection collection not established **[U]**. [V1, V2, V5] | Super/Custom modes can use application text, selection and recent clipboard; app context may add system/user details. App/site mode activation. Mac agent integrations are also available but install hooks/plugins, unlike hark's passive session reading. Linux vocabulary discovery is distinct from Mac Super-mode context parity. [S1–S3, S14, S15] |
| **LLM processing and prompts** | Optional TextCleanup RPC → OpenAI-compatible upstream, constrained edits and profile allowlists; 600 ms client deadline, raw fallback. Static shared prefix/few-shot prompt caching. Direct client OpenAI-compatible adapter is planned, absent. | Arbitrary configured shell command consumes stdin, returns text; optional `edit_list` consumes JSON `{find,replace,reason}` entries. Profile-specific command/timeout/output. Default post-process timeout 30 s; local deterministic cleanup also available. [V1, V4–V6] | Voice to Text skips LLM; Message/Mail/Note/Super/Custom modes separate voice and language model. Custom prompts/examples and context switches. S1-mini is a specialized local 0.6B cleanup model, not a general Super-mode/meeting model. [S1, S4, S5, S13, S16] |
| **Safety and guardrails** | Control/escape/bidi sanitization, terminal single-line/no Enter, focus recheck, protected identifiers/numbers/negations, unique-quote/range/overlap/edit-kind validation. Raw fallback on invalid edits/timeouts. Server/client self-correction validation differs; README flags this gap. | Edit-list parser rejects unknown fields, ambiguous/overlapping targets and excessive edit distance/growth; atomic fallback. Modifier-release guard avoids accidental shortcuts. These are not semantic, numeric, negation or acoustic guarantees. Configured shell hooks are executable code. [V5, V8] | Static prompts explicitly distinguish transcript content from instructions; clipboard/selection anti-contamination examples. No publicly verified equivalent of hark's client-authoritative protected-span edit validator **[U]**. Shift-to-auto-send is optional; not suitable for hark terminals. [S9, L] |
| **Output / paste drivers** | macOS NSPasteboard snapshot/restore, private CGEvent Cmd+V; Hyprland sendshortcut → wtype; X11 xclip/xdotool; clipboard fallback on focus change. Terminal Ctrl+Shift+V. Linux restores text only; hardware paths still need validation. | Ordered Linux chain: wtype/eitype/dotool/ydotool/clipboard/xclip, configurable; paste, clipboard and file modes; terminal-aware paste settings; MIME-aware clipboard save/restore, optional delay; macOS CGEvent/AppleScript/pbcopy paths. Availability and modifier handling matter more than sheer driver count. [V1, V8] | Auto-paste or clipboard-only; simulated typing alternative; restore previous clipboard recommended/default for new Mac installs; clipboard-history control. Mac release notes discuss dropped-paste retries and copy fallback. Linux release notes document fcitx5 direct insertion on Omarchy/Hyprland and clipboard restoration on KDE. Do not infer one universal delivery driver. [S9, S11, S14] |
| **UX feedback and history** | Start/stop sounds and notifications; doctor and stage timings; JSONL raw/canonical/final text, changes and outcomes. Audio/context/result dataset capture opt-in. No recording HUD, searchable recovery UI, retries or swap-to-raw yet. Start sound can leak into audio. | Waybar state, tray, OSD waveform/styles, sound/media pause/ducking, notifications including changed-text preview in inspected source, CLI/TUI/setup. Superwhisper-like searchable dictation/audio retry history not established **[U]**; meeting storage is a separate feature. [V2, V4, V8, V9] | Main/mini recorder, waveform, mode/context indicator, supported live transcripts, stop/cancel. Search/playback/history detail and reprocess into another mode; failed Linux recordings retained for retry. Linux pill/edge glow and theme integration. Mac history search currently searches original dictation, not AI result. [S7, S8, S11, S14] |
| **Platforms** | Python POC on Apple Silicon and Linux Wayland/X11; Rust thin client is planned. Strongest real inference backend presently Mac; primary Linux GPU backend still stub. | Linux-first, Wayland/X11 integrations; Apple Silicon macOS supported. Treat older “Linux only” descriptions as obsolete. No Windows/mobile support established **[U]**. [V4, V10] | Mac, Windows, iOS, Android docs; Linux native app with GUI/TUI/CLI, x86-64/ARM, Wayland Hyprland/KDE/GNOME and systemd. New Mac Meetings and coding-agent integration are not universally portable. [S12, S15, S17, S18] |
| **Configuration, profiles, modes** | Strict TOML rejects unknown keys/types; first app-glob match then terminal/prose fallback. Per-profile cleanup policy; provider registry/routing remains planned. | TOML, per-recording profiles, TUI, CLI schema/settings commands. Omarchy QML panel consumes the executable's schema instead of duplicating defaults/validation. Catalog download and model activation are explicit operations. [V1, V2, V6, V9] | JSON mode files, voice/language pairing, prompt/examples, app/site rules, context and output options, mode switcher/deep links; settings include vocabulary/replacements and mode registry. UI model library distinguishes local/cloud. [S1, S2, S4, S5, S16, L] |
| **Privacy** | User-owned server, loopback/Unix plaintext restriction, TLS/mTLS for nonlocal access; inference server logs ids/sizes/timings and retains nothing. Bearer auth and device allowlist absent. Client history stores text; opt-in recordings contain sensitive raw context. Provider-policy gates/retention controls are not all implemented. | Local inference possible; remote APIs and shell post-processing may transmit data. Previous dictation in environment is additional sensitive data. “Local tool” does not establish privacy of arbitrary scripts or remote backends. [V1, V5] | Fully local voice + language configuration possible. Vendor claims API zero retention/no training; BYOK has own terms. FileSync syncs config, not audio/history. Local recordings default to **Forever** retention. Full prompt/context history can be sensitive even when no cloud is used. [S8, S9, S19, L] |
| **Performance** | Target ≤1 s p95 key-up→paste, not a demonstrated cross-hardware SLO. Buffered decoding grows with utterance length; boosted beam ~2.3× greedy per project docs. Server warmup and speculative EagerEnd exist; client does not issue EagerEnd yet. | Warm/model residency controls, GPU isolation/on-demand choices, CPU/GPU alternatives; GGUF helper remains resident. Vendor examples: 5 s Parakeet ONNX CPU ~0.15 s on Ryzen 9900X3D; current GGUF iGPU measurements differ substantially by runtime. These do not predict laptop performance. [V1, V3, V4] | Model residency duration, silence removal, dynamic normalization, per-model realtime. S1 blog claims most <30 s dictations appear 0.32 s after speech; Linux page shows 0.4 s demo. Neither establishes p95 end-to-end latency on our hardware **[U]**. [S9, S12, S13] |

## 2. Best ideas to steal — ranked by value / effort

Effort is relative to this POC: **S** = bounded client/config addition; **M** = new client behavior with integration tests; **L** = platform/backend work or a new product surface. Estimates are not schedules. Ranking favors the daily-driver loop rather than feature count.

| Rank | Idea and source | Value | Effort | Why now |
|---|---|---|---|---|
| 1 | Reliable delivery with an honest receipt and recovery — both | Very high | M | Recognition accuracy is irrelevant if output disappears or lands in the wrong app. |
| 2 | Non-focus-stealing recording/status surface — Superwhisper, Voxtype | Very high | S–M | Makes mic failure, wrong profile, processing and copied-only outcomes visible. |
| 3 | Separate scoped vocabulary from explicit replacements — both | Very high | M | Fits hark's identifier advantage without an LLM rewrite dependency. |
| 4 | Searchable history and explicit retry/reprocess — Superwhisper | Very high | M | Turns transient failures into recoverable events and usable evaluation data. |
| 5 | Temporal/provenance-aware context envelope — Superwhisper + `VOXTYPE_CONTEXT` | High | M | Prevents context confusion while improving short follow-up dictations. |
| 6 | Real offline laptop route with truthful capabilities — both | Very high | L | An unavailable home server must not disable dictation. |
| 7 | Correction learning with approval and provenance — Superwhisper | High | M–L | Compounds accuracy, but false learning is costly. |
| 8 | Profile override plus visible app routing — both | High | S–M | Existing app matching needs an ergonomic escape hatch, not a new prompt system. |
| 9 | First-class constrained cleanup adapter; evaluate specialist models — both | High | M | Retain safety while making deployment/model choice less coupled. |
| 10 | One schema for CLI/TUI/Omarchy UI; safe model activation — Voxtype | Medium–high | M–L | Avoids incompatible settings surfaces and surprise model changes. |
| 11 | Mic quality/media handling and warm-state policy — both | Medium–high | M | Reduces avoidable bad audio and cold-start latency. |
| 12 | Meetings and richer agent surfaces — Superwhisper | Low for current goal | XL | Useful later, but competes with core latency/reliability work. |

### 1 — Delivery must produce a receipt, not just execute a command

**Where:** `client/src/hark_client/inject.py`, `pipeline.py`, `doctor.py`, `history.py`; platform-specific helpers.

- Extend the existing outcome into explicit states: `pasted_verified`, `paste_attempted`, `copied_only`, `focus_changed`, `failed`. A successful `hyprctl`/CGEvent dispatch is not proof the field changed.
- On platforms with readable focused text, compare a narrowly scoped before/after region. If verification is unavailable, say so; do not treat inability to read Electron editors as proof of failure. Superwhisper's Linux notes document exactly this false-negative trap. [S14]
- Add a bounded retry **only when non-delivery can be established and target identity is unchanged**. Blind retries risk duplicate insertion. Keep clipboard-only recovery otherwise.
- Preserve hark's focus check, no Enter, and control-character guard. Adopt Voxtype's held-modifier timeout → non-keystroke fallback. Restore clipboard only if its ownership/content has not been superseded by a user's newer copy.
- Investigate MIME-aware Linux restore before adding many new drivers. Evaluate fcitx5 direct commit as an optional Omarchy capability, not a new mandatory dependency. Its availability and behavior outside documented Linux releases remain **[U]**.
- **Acceptance:** focus switch, held modifiers, unavailable driver, delayed clipboard ownership, newer user copy, and unreadable field all leave recoverable text without duplicate paste or command execution.

### 2 — A small, passive status UI before a full desktop application

**Where:** `daemon.py`, `ctl.py`, `feedback.py`; a thin Waybar/Quickshell consumer initially.

Expose a versioned status snapshot/subscription: idle, recording, finalizing, cleaning, copied, delivered, failed; active profile, destination app, mic level, current provider/locality, elapsed time and last recoverable result ID. Build a waveform/pill or optional edge glow from these events without taking focus. Keep an accessible text/status equivalent and reduced-motion/off controls.

The state should explain **“recording, terminal, local server”** or **“copied: focus changed”**, not merely flash. Never claim live transcript support when the model advertises BUFFERED. Support explicit stop/toggle/cancel independently of any animation. Do not implement arbitrary on-screen prompt editing first.

### 3 — Recognition hints, acoustic aliases, and exact replacements are different tools

**Where:** `config.py`, `context/types.py`, `context/rank.py`, `context/aliases.py`, `context/canon.py`, `pipeline.py`.

- Add user terms scoped to profile/repo with canonical spelling, spoken aliases, provenance, optional expiry and bounded weight. Merge into the existing ranked shortlist rather than appending an unlimited dictionary to every request.
- Keep deliberate snippets/replacements separately configured and labelled as explicit text transformations. Resolve against original spans, use boundary-aware matching, reject ambiguous/overlapping rules, and make ordering deterministic. Preserve exact output case.
- Pipeline proposal: ASR → acoustically gated canonicalization → explicit scoped replacements → mark resulting protected spans → cleanup → final output guards. Record every replacement and its source. Terminal sanitation still applies to user snippets.
- Borrow Voxtype's abstention on ambiguous candidates and protection for URLs/paths; do **not** replace hark's word evidence with its small ASCII/DoubleMetaphone heuristic. The inspected Voxtype common-word list is a handful of known traps, not a language-wide dictionary. [V7]
- **Acceptance:** distractor vocabulary cannot force unrelated common words into identifiers; a replacement in an unrelated repo does not fire; repeat processing is idempotent.

### 4 — Recoverability, not telemetry dashboards

**Where:** `history.py`, `__main__.py`, `pipeline.py`, existing `hark once` replay path and `docs/dataset/spec.md`.

Start with `history list/show/copy` and a raw/canonical/final diff, timing breakdown, context source summary, bias hits, cleanup rejection and delivery outcome. Add explicit `reprocess` only for retained audio. Keep each retry as a new attempt linked to the original; preserve original text/model/context provenance instead of overwriting history.

The current default does **not** retain audio: do not silently change that to copy Superwhisper's recovery behavior. Offer consented encrypted/on-disk retention or a bounded in-memory recovery buffer, and explain that audio retry is unavailable after discard. Context replay must default to the captured snapshot, not today's focused app; expose an explicit “fresh context” option later.

Search both recognized and delivered text, unlike the documented Mac history limitation. Add expiry/deletion for transcripts, audio and context independently. A diagnostic export should redact context and omit audio by default. Retry must not auto-paste into whatever now has focus.

### 5 — Mirror the context *structure*, not its volume

**Where:** `context/types.py`, `context/engine.py`, `pipeline.py`, `history.py`; existing protocol fields where possible.

Represent collected pieces as `{source, captured_at, target_identity, scope, trust, allowed_destinations, included, exclusion_reason}`. Freeze app/profile identity at key-down. Keep raw context local; send only the ASR shortlist or separately permitted cleanup evidence. Make opt-in switches real before adding more collectors, especially agent-session data.

Take Superwhisper's separately visible application/mode/system/user blocks and history inspection, but fix its cross-phase capture race. A later context update should require the same target identity and use the protocol's explicit update semantics—not silently replace the start snapshot.

Take Voxtype's short-lived previous-dictation continuity, but constrain it to the **same app/window + profile + repo** with a TTL (60 s is a starting hypothesis). Clear on cancellation, focus/scope change and privacy boundary changes. Prefer extracting names/terms to sending the previous transcript. Do not put private text into subprocess environment variables.

### 6 — CPU fallback must be usable, not a checkbox

**Where:** server `backends/`, client `asr.py` and config/provider routing envisaged by ADR-001.

Evaluate existing runtime options before writing a decoder: Parakeet ONNX CPU, a small Whisper runtime, or another currently installed suitable backend. Voxtype's measurements show ONNX CPU and GGUF CPU are not interchangeable; its own current news advises CPU users to measure before migrating. [V3, V4]

Implement capability-aware local routing: unavailable home server → approved local fallback; never cloud unless explicitly allowed. Advertise unsupported bias/anchors/word confidence honestly; adapt canonicalization conservatively rather than manufacture evidence. Persist model readiness and health, provide a visible cold/loading state, and prewarm only within the user's memory/battery policy.

Measure 2/5/15/60 s utterances, cold/warm, English and required other languages, distractor context, CPU load and energy. Report p50/p95 key-up→paste, not only inference real-time factor. A ≥1 s fallback may be acceptable if clearly labelled, but it is not achievement of the LAN SLO.

### 7 — Learn from corrections, never from a guess that the user accepted them

**Where:** future client-side correction observer, `context/` persistent vocabulary storage and history links; no inference-server learning state required.

Start with explicit “remember this spelling” on a history diff. Later watch only the delivered input region in the same readable, non-secure focused element for a short consented interval. Suggest one clear name/identifier correction; ignore multiword rewrites, ambiguous alignments, capitalization-only changes, ordinary-word swaps, and IME composition. Installed Mac strings reveal several of these rejection conditions; that is useful design evidence, not proof of runtime correctness. [L]

Show provenance and scope, allow accept/reject/forget, and never create a global hard replacement automatically. Keep suggestion discovery from notes/commits opt-in and incremental; hark's existing repo extractor already covers part of this value without scanning the whole home directory. Linux 1.3.0's “ask first, stay local” UX is worth matching. [S14]

### 8 — Make the selected profile explicit and overridable

**Where:** `config.py`, `ctl.py`, `daemon.py`, hotkey bindings and status UI.

Add an explicit per-utterance profile argument to control commands and a small profile chooser. Preserve automatic app matching and announce the result before capture starts. Define precedence: explicit override → app rule → terminal detection → prose. Reject unknown explicit profiles instead of silently falling back, as Voxtype's manual documents doing.

Keep “dictate faithfully,” “cleanup,” and any future “transform selection” visibly separate. Changing a mode mid-utterance must not silently authorize new context export, change a terminal's safety policy, or replace the ASR model after audio has already been sent. Browser-site routing can wait for trustworthy URL acquisition.

### 9 — Improve cleanup portability without weakening the edit contract

**Where:** `client/cleanup.py`, `server/cleanup.py`, client validator and cleanup evaluation suite.

Implement ADR-001's direct OpenAI-compatible client adapter using the same constrained edit schema, deadline and client-authoritative validation. Voxtype demonstrates the usefulness of pluggable cleanup, but its plain-text shell mode and 30–60 s examples should not become hark's default.

Evaluate S1-mini's **specialization strategy**: small model, finite controls, synthetic task-specific examples, explicit disfluency/self-correction tests. Its open weights make an evaluation possible, but do not replace Qwen3-1.7B based on the blog. S1-mini emits cleaned text, not hark's edit protocol; schema adherence, license/use terms, speed, and conversion safety need verification **[U]**. Never treat a text diff of unconstrained output as proof of safe edits.

Strengthen existing evaluations with questions that must remain questions, agent instructions that must remain text, pronouns with distracting clipboard content, profanity/dialect preservation, genuine disjunctions versus spoken self-corrections, and identifiers/numbers/negations. Resolve the known server/client self-correction mismatch before widening allowed transformations.

### 10 — One configuration authority; separate download from activation

**Where:** `config.py`, CLI/doctor, later Omarchy UI; server capability/model-management surface only when needed.

Voxtype's Omarchy panel consumes `config schema --json` and writes through validated CLI commands. Adopt this when a second settings consumer actually exists, rather than building an abstraction now. UI, TUI and CLI should display the same defaults, effective profile and validation errors.

For a model library, distinguish **available → downloaded → validated → selected → loaded/ready**. Expose license, supported languages, memory/locality, streaming/bias/anchor capabilities and measured host latency. Downloads need checksums and explicit consent. Activation should be atomic and preserve the previous working configuration on failure. Do not show a model as usable merely because its file exists.

### 11 — Detect bad microphones and manage latency honestly

**Where:** `mic.py`, `audio.py`, `feedback.py`, `doctor.py`, server warmup and client deadline accounting.

Borrow Linux Superwhisper 1.3.1's actionable muted/silent/disconnected/constant-signal mic errors. Add opt-in media pause/ducking with guaranteed restoration on cancel/error. Do not silently force mic gain to maximum. Keep sounds selectable/off and prevent the start cue contaminating capture without losing initial speech.

Use the planned VAD/EagerEnd path only after endpoint correctness tests. Preserve sample/timestamp mapping if silence is removed: hark's acoustic gates and `AudioChunk.start_sample` contract depend on it. Expose warm versus cold timings; optimize streaming overlap before claiming that a faster LLM solves latency.

### 12 — Defer meetings and active agent control

Superwhisper's meeting UX is strong: separate workspace, mic/system audio, local diarization, summary templates and explicit sharing. Voxtype also has meeting capture. But hark's ≤1 s dictation path and terminal safety should not inherit long-recording storage, consent, calendar or speaker-management complexity.

Likewise, Superwhisper's voice approvals and agent popups are a different product from passive vocabulary collection. Keep hark useful without extensions, never interpret ordinary dictation as tool approval, and never auto-submit terminal text. Revisit only after core delivery, recovery and offline fallback are reliable.

## 3. What they do poorly—or what hark should deliberately avoid

### Voxtype

1. **Capability/documentation drift.** Old engine counts, English-only Parakeet prose, ONNX requirements and newer GGUF releases disagree. Avoid by generating capability-facing docs/UI from model metadata where practical, pinning benchmark versions and distinguishing transport streaming from incremental decoding. [V2–V4]
2. **Configured cleanup can be unrestricted and slow.** Plain-text post-processing accepts wholesale output; shell commands are executable configuration; the default timeout is far outside hark's interactive budget. Edit-list mode is a welcome improvement but optional. Keep a typed, bounded, validated path. [V1, V5]
3. **Edit distance is not meaning preservation.** In the inspected validator, a small edit to a digit or negation can fit the budget; `reason` need only be nonempty. There is no equivalent protected-span semantic policy in that function. Its safeguards should complement, not replace, hark's stronger checks. [V5]
4. **A timeout wrapper is not necessarily process cleanup.** Inspected code writes stdin before wrapping `wait_with_output` in a timeout and does not visibly configure `kill_on_drop`/process-group termination. This is a source-level risk, not a reproduced exploit **[U]**. If hark ever adds executable hooks, bound stdin/stdout/stderr and kill descendants on deadline. [V5]
5. **Approximate vocabulary is still text-only.** The conservative source has useful abstention, but ASCII normalization, a short collision list and no acoustic evidence limit it. Do not let phonetic similarity overrule the unbiased ASR anchor. [V7]
6. **State surprises.** Unknown profile names fall back; experimental streaming can promote hold-to-talk to toggle; model migration/UI availability changes between releases. Surface these choices explicitly rather than silently changing interaction semantics. [V2, V4]

### Superwhisper

1. **Context may come from a different moment/window than speech.** App text is captured after ASR, selection before it; the docs explicitly warn about switching windows. hark should bind target and context at capture start. [S3]
2. **Context-aware rewriting is not universally reliable.** Super-mode docs warn that smaller/local models may fail complex context tasks, and S1-mini cannot run Super mode. More context and a “smart” label do not replace acoustic bias and validation. [S1]
3. **History is both recovery and a sensitive-data archive.** Forever retention and stored complete prompts/context maximize debugging but enlarge exposure. Retention controls exist; hark should offer shorter explicit policies and minimal diagnostic records rather than copying the default. [S8, L]
4. **Platform and mode complexity is easy to misunderstand.** Mac Meetings replaced an older Meeting mode; Windows retains that preset; Linux has a separate implementation/release stream. Voice, language and realtime support vary by hardware/model. Make unsupported combinations impossible or obvious. [S1, S4, S12, S17]
5. **No independent basis for absolute model promises.** “Never adds content” in the S1 blog is stronger than its own less-than-perfect metrics. Vendor speed/WER numbers and relative picker ratings are not our acceptance tests. [S4, S13]
6. **Some convenience features cross hark's safety boundary.** Shift-to-Enter, direct selected-text transformation, copied-text intent inference and voice agent approvals must not leak into ordinary terminal dictation. [S1, S9, S15]
7. **History search misses processed text on Mac.** Search both raw and delivered text in hark. [S8]
8. **Repeated paste/mic fixes reveal the hard part.** Chromium retries, Electron formatting, delayed clipboard ownership, Hyprland changes and constant-signal microphones appear in release notes. This is not proof the current products are broken; it is evidence that command-level mocks alone are insufficient. [S11, S14]

### Also avoid complacency about hark

Its architecture is stronger than its deployment maturity. Linux output is not hardware-validated in the README; CPU fallback and NeMo are missing; client/server cleanup validators differ; agent-context policy gating is not fully reflected in the POC collector; plaintext restrictions do not supply a device allowlist; text history still needs lifecycle controls. Do not advertise planned Rust/provider/privacy features as shipped advantages.

## 4. UX details worth mirroring, precisely

| Detail | Observation | Adaptation boundary |
|---|---|---|
| Recording window | Superwhisper main/mini surface exposes waveform, mode, stop/cancel; context indicator shows selection/clipboard capture. [S7] | Small passive hark surface; never steal target focus. Display context source presence, not private contents, by default. |
| Linux edge glow | Verified in Linux release notes, including 1.1.0's glassy-window blur fix and 1.2.0 launcher integration. [S14] | Optional visual state, not the only indicator; accessibility/reduced-motion alternative. |
| PTT/toggle/cancel | Both support multiple activation styles; Superwhisper supports quick-tap toggle and confirmation for long cancellation. [V2, S7, S10] | Explicit configuration and stable semantics. Immediate terminal-safe cancellation should remain available. |
| Sound feedback | Superwhisper Simple/Classic/Off, volume and previews; installed resources contain start/stop/error/no-result cues. Voxtype offers audio feedback/media controls. [S10, V4, L] | Distinguish started/finished/copied/error; prevent sound entering the ASR stream. |
| Auto-paste and clipboard restore | Superwhisper recommends retaining prior clipboard; Voxtype offers delayed MIME-aware restore. Linux direct IME insertion may avoid clipboard altogether. [V1, S9, S14] | Prefer no clipboard damage; never overwrite a newer user copy; no automatic Enter. |
| Retry | History reprocess with another mode; Linux failed audio survives for retry. Paste retries are a separate mechanism. [S8, S11, S14] | Distinguish ASR retry, cleanup-only retry, copy recovery and delivery retry. Recheck target/privacy each time. |
| Per-app modes | Superwhisper app/site rules and shortcut chooser; Voxtype per-recording profile argument/modifiers. [S2, V2] | Extend existing hark app matching; show resolved profile and keep explicit override deterministic. |
| Vocabulary suggestions | Linux local discovery asks first; local Mac binary exposes correction-observation logic. [S14, L] | Approval, provenance, scope, expiry, forget. No broad automatic home-directory scan. |
| Model library | Separate voice/language choices, local/cloud distinction; Voxtype explicit downloads and validated activation. [S4, S5, V4, V9] | Show actual capability/readiness and host cost. Avoid a misleading universal list. |

## 5. Superwhisper local inspection: what to mirror

### What was inspected, and what was not

Read-only inspection of `/Applications/superwhisper.app/Contents/Info.plist`, bundle resource filenames, and selected static strings from `Contents/MacOS/superwhisper` using `strings`; schema-only inspection of `~/superwhisper/modes/*.json`, `~/superwhisper/settings/*.json`, and **43** `~/superwhisper/recordings/*/meta.json` files.

No personal transcript, replacement value, prompt value, app name from recordings, person name, device name or timestamp is reproduced here. Metadata processing extracted keys/types and structural properties, not transcript content. The binary's generic distributed prompts were read only to summarize their policy patterns. No long prompt excerpts are reproduced.

`~/Library/Application Support/superwhisper` was **listed only**, not opened. Observed files: `database/superwhisper.sqlite`, `s1-mini.gguf`, `ggml-large-v3-turbo.bin`, `model-installs.json`, `model-manifest.json`. A filename establishes presence, not active use, content or correctness. The database, model weights, manifests and credentials were not inspected.

### A. Mode/settings schema

Observed local modes contain:

- **Identity and routing:** `key`, `name`, `type`, `version`, `activationApps`, `activationSites`.
- **Model/language:** `voiceModelID`, `languageModelID`, `language`, `translateToEnglish`.
- **Prompt/context:** `prompt`, `promptExamples`, `contextTemplate`, `contextFromActiveApplication`, `contextFromClipboard`, `contextFromSelection`.
- **Capture/output:** `diarize`, `useSystemAudio`, `realtimeOutput`, `literalPunctuation`, `autocapitalizeInsert`; `autoPaste` appears in one inspected mode.
- **Extension hooks:** `script`, `scriptEnabled`; presence does not establish execution behavior **[U]**.
- Settings keys: `favoriteModelIDs`, `modeKeys`, `vocabulary`, `replacements`; replacement objects have `id`, `original`, `with`.

The public docs-repository schema explains that mode files are registered through `modeKeys`, that built-in prompts can be empty because policy lives in the app, and that app/site activation is independent of prompt content. [S16]

**Mirror:** versioned declarative profiles, independent voice/cleanup capability selection, explicit context switches and stable IDs. **Do not mirror:** a second manual mode-registration list that can disagree with files, arbitrary script execution by default, or a format promising every backend the same features.

### B. Actual recording metadata structure

The union of observed fields included the following. Optional fields can vary; absence in this sample is not a statement about all versions/modes.

```text
meta
  appVersion, datetime, kind, duration
  modelKey, modelName, processingTime
  languageModelKey, languageModelName, languageModelProcessingTime
  languageSelected, modeName, recordingDevice
  applicationContextEnabled, literalPunctuationEnabled
  realtimeEnabled, separateSpeakersEnabled
  systemAudioEnabled, translationEnabled
  rawResult, result, llmResult
  prompt
  promptContext
    applicationContext
      includeInPrompt, name, nouns[]
    modeContext
      type, language
      examples[] { id, input, output }
    systemContext
      language, locale, time, timezone
    userContext {}
  segments[] { start, end, text }
  speakers[]
```

Observed numeric types: `duration`, `processingTime`, `languageModelProcessingTime` were integers; segment start/end were floats. **Units are [U] from schema-only inspection** and must not be inferred from field names. The sample's `userContext` objects exposed no child fields; that does not prove the feature never contains identity data. Speaker item structure was not established.

**Best idea:** storing raw/intermediate/final outputs beside processing configuration and a structured context envelope makes “why did it write that?” answerable. hark already stores richer client/server stage timing and edit provenance; add prompt-policy/model version hashes and context inclusion reasons, not a duplicate metadata system.

**Do not default to storing the rendered full prompt.** Store template ID/version/hash, model ID, validation outcome and source summary; allow full sensitive context/prompt capture only with explicit diagnostic/dataset consent and expiry. Mirror `includeInPrompt` as an **effective inclusion result**, with a reason for omission—not merely a user's requested toggle.

### C. Static prompt and feature patterns found in the Mac binary

These are distributed-string observations, **not proof every installed mode uses them**:

1. **Task boundary:** a shared instruction layer distinguishes processing speech from conversing with the speaker. Questions and requests stay transcript content unless a mode explicitly authorizes instruction-following. Adopt as prompt defense-in-depth; the client validator remains authoritative.
2. **Context anti-contamination:** clipboard/selection is background unless the user explicitly requests transformation; ordinary pronouns should not be resolved to copied names/addresses, and copied material should not be appended or borrowed merely because it is available. This is especially valuable for coding-agent prompts.
3. **Contrastive few-shot examples:** static examples distinguish actual spoken self-correction from innocent hedges, quoted requests, ordinary profanity and figurative uses of emoji words. Mirror the *test categories*, not proprietary prompt wording.
4. **Structured rendering:** strings identify application, user-information, clipboard, selection and correct-behavior sections; app fields include names/nouns, focused element/content, input format and URL. Language instructions appear explicitly. The recorded `promptContext` is structured data, not just an untyped screen dump.
5. **Specialist normalization:** another string describes a normalizer controlled by styling/structure/context settings. This supports testing finite profile controls rather than passing arbitrary elaborate prose instructions to a tiny model.
6. **Correction learning rejects uncertainty:** strings mention rejecting multiple candidate changes, missing pasted region, ordinary dictionary words, capitalization-only changes, unsupported language strategies and unreadable input fields. Japanese-specific handling distinguishes composition/IME submission and orthographic changes. Exact thresholds/runtime coverage **[U]**.
7. **Acoustic vocabulary correction exists as a component:** strings describe wav2vec2 phoneme CTC + grapheme-to-phoneme processing and a Parakeet companion model. This is stronger evidence than assuming all vocabulary behavior is an LLM prompt, but model routing, scoring and exposure to users remain **[U]**. hark should retain its backend capability model rather than assert superiority over an unknown closed pipeline.

### D. Resources

Observed resource names include start/stop/pre-stop/error/no-result sound files; model-family chat templates (`qwen3.jinja`, `llama3.jinja`, `mistral.jinja`, others); `vad-v2.mlmodelc`; a Supervocab phonotactic-ngram resource; and `agent-hook` / `claude-hook` executables. These support the presence of sound themes, local-model plumbing, VAD/vocabulary assets and agent integration packaging. They do **not** prove which code paths were active. Hooks were not executed and templates were not copied.

## 6. Uncertainties and focused follow-up

| [U] | What remains unknown | Smallest useful verification |
|---|---|---|
| U1 | CPU fallback accuracy/latency/battery on the actual laptop | Replay the same retained, consented hark clips through two existing runtimes; include cold start and distractor vocabulary. |
| U2 | Safe paste verification/direct IME insertion across Hyprland, KDE, GNOME, terminals and Electron | A dedicated disposable target-app matrix; never test into a live terminal shell. |
| U3 | Superwhisper per-model vocabulary mechanisms and confidence/anchor handling | Public model-specific technical docs or a controlled black-box experiment; static strings are insufficient. |
| U4 | Full Linux parity for Super mode, custom prompts, Mac model library and context acquisition | Linux documentation/release-specific inspection in a separate authorized session. Do not infer from Mac UI. |
| U5 | S1-mini under hark's deadline and constrained edit schema | Check model card/license, schema adherence and protected-span error rate against the existing cleanup suite. |
| U6 | Voxtype source-only features in a particular packaged release | Compare pinned source paths to the desired release tag before relying on deployment availability. |
| U7 | Superwhisper local metadata timing units and all optional context/speaker shapes | Public schema or synthetic, consented test recording; no need to mine personal recordings. |
| U8 | Closed-source mechanical prompt/paste safety guarantees | Vendor specification or controlled evaluation; absence of public evidence is not evidence of absence. |
| U9 | Cross-product performance claims | Same audio/context/hardware, full key-up→paste timing, p95 and false substitution rate. Blog throughput alone is inadequate. |

## 7. Verified source register

URLs below were retrieved during this research (release-page identities also verified through GitHub's release API). Mutable docs are dated by this report; GitHub source snapshots are pinned where material to a claim. No install commands from these sources were executed.

### hark [H]

- [Project README](../../../README.md), [project context](../00-context.md), [client README](../../../client/README.md), [server README](../../../server/README.md), [ADR-001](../../protocol/ADR-001-protocol.md).
- Source cross-checks: [MLX Parakeet backend](../../../server/src/hark_server/backends/parakeet_mlx.py), [context engine](../../../client/src/hark_client/context/engine.py); source file inventory used for adaptation locations.

### Voxtype

- **V1 — Configuration:** https://voxtype.io/docs/CONFIGURATION — profiles, post-processing/edit lists, previous dictation context, replacement and clipboard/driver settings. Pinned source: https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/docs/CONFIGURATION.md
- **V2 — User manual:** https://voxtype.io/docs/USER_MANUAL — hotkeys/cancel, profiles, output, streaming caveat and Omarchy UI. Pinned source: https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/docs/USER_MANUAL.md
- **V3 — Backend/model selection:** https://voxtype.io/docs/PARAKEET and https://voxtype.io/docs/MODEL_SELECTION_GUIDE — runtime choices, performance examples and stale/conflicting language/engine descriptions.
- **V4 — Current releases/news:** https://voxtype.io/news/ — 1.2.1/1.2.0, GGUF/catalog changes, CPU migration warning, output fixes and macOS support. The fetched page title misleadingly names an older 0.6.6 post; the body contains current release entries.
- **V5 — Edit-list and subprocess implementation:** https://raw.githubusercontent.com/peteonrails/voxtype/dev/src/output/post_process.rs — inspected alongside pinned repository snapshot; https://github.com/peteonrails/voxtype and https://api.github.com/repos/peteonrails/voxtype/git/trees/dev?recursive=1 establish repository/version provenance.
- **V6 — Profile configuration source:** https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/src/config/profile.rs
- **V7 — Vocabulary implementation:** https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/src/text/vocabulary.rs
- **V8 — Output chain/modifier handling:** https://raw.githubusercontent.com/peteonrails/voxtype/dev/src/output/mod.rs and pinned equivalent https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/src/output/mod.rs
- **V9 — Omarchy integration:** https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/omarchy-plugin/README.md and https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/packaging/omarchy/README.md — distinguish current schema-driven panel from older Waybar packaging instructions.
- **V10 — macOS installation:** https://raw.githubusercontent.com/peteonrails/voxtype/fbedd62537d5334dd7e7416189db21549184b034/docs/INSTALL_MACOS.md

### Superwhisper

- **S1 — Modes and Super mode:** https://superwhisper.com/docs/modes/modes ; https://superwhisper.com/docs/modes/built-in ; https://superwhisper.com/docs/modes/customizing-modes
- **S2 — Switching / app rules / deep links:** https://superwhisper.com/docs/modes/switching-modes
- **S3 — Context timing and policy:** https://raw.githubusercontent.com/superultrainc/superwhisper-docs/86cc0811e7eae16db7adbfc37eea90df9d117ad0/common-issues/context.mdx
- **S4 — Voice model library:** https://superwhisper.com/docs/models/voice
- **S5 — Language models:** https://superwhisper.com/docs/models/language
- **S6 — Vocabulary/replacements:** https://superwhisper.com/docs/get-started/interface-vocabulary
- **S7 — Recording window:** https://superwhisper.com/docs/get-started/interface-rec-window
- **S8 — History/reprocess/retention:** https://superwhisper.com/docs/get-started/history
- **S9 — Advanced settings:** https://superwhisper.com/docs/get-started/settings-advanced
- **S10 — Hotkeys/sound/microphone:** https://superwhisper.com/docs/get-started/essential-settings
- **S11 — Mac changelog:** https://superwhisper.com/changelog — 3.2.2, 3.2.0 recovery and 2.19.3 paste retry notes. `?platform=linux` returned the same Mac content at inspection, so it was not used as Linux evidence.
- **S12 — Linux availability/platform:** https://superwhisper.com/linux ; https://superwhisper.com/blog/linux
- **S13 — S1 model family and vendor metrics:** https://superwhisper.com/blog/s1
- **S14 — Linux release evidence:** https://api.github.com/repos/superultrainc/superwhisper-linux-releases/releases?per_page=30 and https://api.github.com/repos/superultrainc/superwhisper-linux-releases/releases/latest. Specific entries: https://github.com/superultrainc/superwhisper-linux-releases/releases/tag/v1.3.1 (microphones/CPU); https://github.com/superultrainc/superwhisper-linux-releases/releases/tag/v1.3.0 (vocabulary suggestions); https://github.com/superultrainc/superwhisper-linux-releases/releases/tag/v1.2.0 (offline fallback/KDE); https://github.com/superultrainc/superwhisper-linux-releases/releases/tag/v1.1.0 (edge glow); https://github.com/superultrainc/superwhisper-linux-releases/releases/tag/v1.0.1 (failed-audio recovery); https://github.com/superultrainc/superwhisper-linux-releases/releases/tag/v0.1.0-beta.14 (fcitx5 insertion); https://github.com/superultrainc/superwhisper-linux-releases/releases/tag/v0.1.0-beta.13 (clipboard/paste verification).
- **S15 — Coding-agent integrations:** https://superwhisper.com/docs/get-started/coding-agents
- **S16 — Public mode schema:** https://raw.githubusercontent.com/superultrainc/superwhisper-docs/86cc0811e7eae16db7adbfc37eea90df9d117ad0/skills/superwhisper-optimize/references/mode-schema.md
- **S17 — Meetings:** https://superwhisper.com/docs/meetings/overview ; https://superwhisper.com/docs/meetings/offline
- **S18 — Public docs repository inventory:** https://api.github.com/repos/superultrainc/superwhisper-docs/git/trees/main?recursive=1 — includes platform docs and published UX screenshots; snapshot pinned above.
- **S19 — Privacy/data flow:** https://superwhisper.com/docs/security/sensitive-data — vendor commitments, local/cloud stage separation, BYOK, FileSync and history distinctions.

**Bottom line:** make hark dependable and inspectable before making it more expressive. Adopt the competitors' recovery loops, state visibility and configuration ergonomics, while preserving the sharper boundary between acoustic recognition, explicit user replacements, constrained cleanup and safe delivery.
