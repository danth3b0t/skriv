# Bench: accuracy baseline on eval/dev-v1 (2026-11-13)

> **Caveat.** 96 of the 122 clips are synthetic speech (Kokoro-82M and macOS `say`) with added noise. The other
> 26 are real recordings of conversation (EdACC) and read books (LibriSpeech), not of developers dictating. The
> numbers show *which failure classes exist and where in the pipeline they arise*. They do not measure accuracy
> on a person at a desk. The traps (ordinary words that collide with shortlist identifiers) were chosen on
> purpose, so the false-substitution rate is an upper bound, not a frequency.

## Result in one paragraph

With every default, 51 of 122 final texts say exactly what was said (strict human review: fillers gone,
self-corrections applied, no claim added, dropped or reversed). 43 fail a mechanical check, and 28 more were
reviewed and rejected. Context earns its keep: without it the critical-string recall of the final text falls from
64% to 23% and accepted texts fall from 51 to 35. It is also the main source of damage: **12 of 29 trap strings
became false identifiers**, all at canonicalization (`each other` → `each_other`, `go back` → `goBack`,
`wait for` → `wait_for`, `session start` → `SessionStart`). Only 64% of critical strings arrive at all, and
almost every miss (41 of 43) is a string that no stage ever produced. The causes, in order: spoken forms
missing for names that are not pronounced as spelled (Hyprland, hyprctl, kubectl, `ctl`, Ghostty, basedpyright,
SQLite), acoustic errors on short command words that no context covers (`git` → "Get"), and gaps in the
spoken-symbol rules. The anchor gate changes the text of 55 clips, and on WER it is a coin flip (15 better, 15
worse). Turning it off is far worse anyway (recall 64% → 40%) because canonicalization depends on the
confidences the gate hands over. Cleanup never lost a critical string or a negation. Mostly it does nothing: 62
`no_edits`, 32 rejected, and 6 of 18 filler-bearing finals keep their fillers. One server defect is
severe: after a particular sequence of sessions, a 2 s clip decodes to `ÌÌÌÌ…` (`<unk><unk>…` without context),
and that text is pasted as-is.

## Setup

| | |
|---|---|
| Host | MacBook, Apple M5 Pro, macOS 26.5.1. The shared stack was started from the main checkout at `3b6349d` (same code as this branch for the server). Other agents may have used it at the same time. |
| Server | `hark-server --backend parakeet_mlx` (parakeet-tdt-0.6b-v3, decode-time boost, anchor `IF_CHEAP`) on 127.0.0.1:50051. Cleanup: `--cleanup-engine s1`, llama-server with `s1-mini-q4_k_m` on 127.0.0.1:8080. |
| Client | this branch. `uv run hark --config C eval run --set eval/dev-v1 --report C.json` with the configs in [`eval-baseline/`](eval-baseline/): **baseline** (every default), **no-context** (`[context] max_terms = 0`: the replayed shortlist is cut to nothing, so there is no bias, no canonicalization and no terms for cleanup), **no-cleanup** (`[cleanup] enabled = false`), **no-gate** (`[gate] enabled = false`). Audio is fed as fast as possible, so nothing is pasted or written to history. |
| Set | [`eval/dev-v1`](../../eval/dev-v1), built by [`scripts/make-eval-set`](../../scripts/make-eval-set) (seeded, reproducible byte for byte). 122 clips, 24.3 MB, 5.1 s median (p95 10.9 s). |
| Synthetic | 96 prompts to coding agents ([`scripts/eval_prompts.py`](../../scripts/eval_prompts.py)): 11 short commands, 8 long multi-sentence prompts, 31 with identifiers, 15 paths, 11 flags, 13 numbers or versions, 25 negations, 8 explicit self-corrections, 12 with fillers, 9 traps. 76 hark, 10 web, 5 ML and 5 ops prompts. The TTS reads the spoken form (`p n p m`, `hyper land`), never a critical spelling. Voices: en_US 34 (14 Kokoro, Samantha), en_GB 18 (8 Kokoro, Daniel), en_AU 7 (Karen), en_IE 7 (Moira), en_IN 14 (Rishi, Aman, Tara, 4 Kokoro Hindi voices), en_ZA 7 (Tessa), and 9 non-native Kokoro voices (es, fr, it, pt). Rate 0.85-1.25× (Kokoro) or 150-230 wpm (`say`). Talker level −32 to −20 dBFS. 40% of clips get synthetic room reverb (RT60 0.2-0.7 s) and 30% a laptop-mic response. Every clip has room tone. Background: quiet 26, office 17, cafeteria 11, keyboard 12, fan 12, keyboard + office 7, living room 7, meeting babble 4, at SNR 6-24 dB (DEMAND CC-BY-4.0 noise; fan and typing are synthesized). |
| Real | 20 EdACC test utterances (CC-BY-SA-4.0; 2 per shard: Scottish, Indian, Romanian, Nigerian, US, Kenyan, Spanish, Chinese/Southern British, Jamaican/Italian/Israeli, Vietnamese speakers) and 6 LibriSpeech dev-clean utterances (CC-BY-4.0). Expected text = the dataset transcript. Clips whose words collide with a shortlist identifier are preferred. That identifier goes on the clip's dense hark shortlist and into `forbidden`. |
| Context | The `retry.save` snapshot format, built with the real ranker (`context.rank`). Shortlists hold up to 30 terms: the clip's terms with recent agent/terminal evidence plus 15-28 distractors from the project's vocabulary (108 full, 4 sparse, 10 empty). Apps: 107 terminal (Ghostty, Alacritty, kitty), 15 prose/chat (VS Code chat, Slack, Obsidian). |
| Review | Every baseline final was read against `said`. A final was accepted only if it says exactly what was said: fillers removed, self-corrections applied, wording may be clearer, nothing added, dropped or reversed, identifiers right. Ablation finals that differ from an accepted text were reviewed the same way (17 more accepted). Rejected texts stay unreviewed. Tables: [`eval-baseline/analyze.py`](eval-baseline/analyze.py). Raw per-clip reports: `eval-baseline/*.json`. |

## Results

| condition | failed | rejected (unreviewed) | accepted | recognition WER, mean (median) | critical recall: raw / gated / canonical / uncleaned / final | false substitutions | negations lost¹ | finals with fillers² | key-up → final ms p50 / p95 | server end → final ms p50 / p95 | cleanup ms p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **baseline** | 43 | 28 | **51** | 14.8% (8.3%) | 24% / 23% / 61% / 64% / **64%** | **12/29** | 2 | 6/18 | 265 / 478 | 205 / 406 | 32 / 67 |
| no-context | 65 | 22 | 35 | 16.4% (8.3%) | 20% / 20% / 20% / 24% / 23% | 0/29 | 3 | 6/18 | 78 / 141 | 66 / 117 | 32 / 65 |
| no-cleanup | 45 | 33 | 44 | 14.8% (8.3%) | 24% / 23% / 61% / 64% / 64% | 12/29 | 2 | 16/18 | 284 / 478 | 220 / 409 | 0 |
| no-gate | 63 | 23 | 36 | 14.8% (8.3%) | 24% / 24% / 36% / 39% / 40% | 8/29 | 3 | 4/18 | 269 / 489 | 207 / 418 | 34 / 62 |

121 critical strings in 70 clips. False substitutions count trap and real-speech `forbidden` strings (identifiers
an ordinary phrase was rewritten into) that appear in the final text. Recognition WER compares the raw text with
`said`, per clip capped at 100%, case and punctuation ignored. It overstates synthetic errors because `said`
spells symbols and numbers as spoken ("dash dash", "three point twelve") and the recognizer writes `--` and
`3.12`. Latency: audio is replayed faster than real time, so key-up → final is about the whole decode, which is
larger than live use, where audio streams. Compare it only between conditions.
¹ Fewer negations in the final than in `said`, unless that text is accepted. In the baseline these are real-017
("I never watched it" → "And I watch it.", recognition) and syn-069 (a correctly retracted "no" in a text
rejected for "foulback"). ² Clips whose final keeps um/uh/eh/mm/ah, out of the 18 whose `said` has them.

**What each stage contributes** (baseline vs ablation):

- **Context** (bias + canonicalization + cleanup terms): critical recall in the final text 23% → 64%, accepted
  35 → 51, failed 65 → 43. Raw WER barely moves (16.4% → 14.8%), so the gain comes from writing identifiers,
  not from hearing words better. Cost: all 12 false substitutions, and about 140 ms at p50 (290 ms at p95) of
  server time for the biased beam search and the anchor.
- **Anchor gate**: off → recall 64% → 40%, failed 43 → 63, accepted 51 → 36. It has two jobs. One is text: 55
  clips change, 15 better / 15 worse / 25 equal on WER. The other is confidence: boosted beam words come back
  with confidence ≈0.1, and canonicalization skips words below 0.5, so with the gate off most canonicalizations
  never fire. syn-013 gets only one of `useSessionStore`/`useWorkspaceStore`. syn-024 and syn-028 lose two of
  three identifiers. syn-039, syn-040 and syn-048 lose their path or flag. That is also why gate-off has fewer false substitutions (8): it
  canonicalizes less of everything.
- **Cleanup**: accepted 44 → 51, fillers left 16/18 → 6/18. No critical string and no negation lost at
  cleanup (0 of 121, 0 of 25 negation prompts). 32 ms p50. In 62 of 122 clips it proposes nothing, and in 32 all
  its edits are rejected (dropped edits: invariant 25, protected_span 19, dependent 6, overlap 2, global 1).

By group (baseline; small n, read as hints): recognition WER is highest for en_IN (20.9% mean, 7 of 14 failed),
EdACC (18.0%) and en_GB (17.0%), lowest for LibriSpeech (2.8%), en_IE (6.5%) and en_AU (7.9%). Background noise
at these SNRs does not drive errors: quiet-room clips have the highest WER (19.7%) because the hardest prompts
landed there. Prose/chat clips: 9 of 15 accepted, 2 failed. Terminal clips: 42 of 107 accepted.

## Failure classes, ranked

Ranked by how many baseline finals they break and how badly. "Clips" counts non-accepted baseline clips; a clip
can be in two classes. Each class names the stage where the text goes wrong and the code that owns it.

### 1. Canonicalization turns ordinary phrases into identifiers. Stage: canonicalization (`context/canon.py`). 10 clips, 12 strings

A multi-word alias of an identifier made of dictionary words matches plain speech, and nothing but word
confidence (≥ 0.5) guards it. Cleanup cannot repair it, because canonicalized terms are protected spans (real-005:
`dropped: protected_span`).

| clip | said | final |
|---|---|---|
| real-007 (EdACC) | "…we'll see each other" | "We'll see `each_other`." |
| real-015 (EdACC) | "after we're done speaking with each other" | "…speaking with `each_other`, …" |
| real-005 (EdACC) | "so don't let me give you that impression" | "So don't `let_me` give you that impression." |
| real-001 (EdACC) | "it was very fun to go back there" | "…very fun to `goBack` there…" |
| syn-093 | "we need a session start and end time in the report" | "We need a `SessionStart` and end time…" |
| syn-092 | "show me the last text you generated, and keep it to one line" | "Show me the `last_text` you've generated and keep it to `one_line`." |
| syn-096 | "read the text field first … the screen text" | "read the `TextField` first … the `ScreenText`." |
| syn-091 | "write a quick shell script" | "Write a `QuickShell` script…" |
| syn-095 | "mark down the time…" | "`mark_down` the time…" |
| syn-084 | "…the user says wait for the build, we must not write it with an underscore" | "…says `wait_for` the build…" |

Every multi-word trap on the real-speech clips leaked: 4 of 4 among the clips where recognition kept the phrase.
Single-word sound-alikes never leaked: 0 of 12 (`hard`/`heart` → `hark`, `rough` → `ruff`, `Highland` →
`Hyprland`, `ghost` → `Ghostty`, `you've` → `uv`, `quick` → `QuickShell`). The single-word rule and the gate
work for them. The terms are real hark names
(`SessionStart`, `TextField`, `ScreenText`, `wait_for`, `last_text`, `one_line`, `mark_down`) or common ones
(`goBack`, `each_other`, `let_me`). Fix direction: an alias whose words are all dictionary words should need
more than a match. Options: the recognizer's bias hit on that span, identifier syntax around it ("rename X",
"call X", "in X", a following "function"/"flag"/"field"), or a spoken separator ("underscore", "dot"). Leave
the decision to cleanup by not protecting those spans.

### 2. Spoken forms missing for names not pronounced as spelled, and partial aliases. Stage: canonicalization aliases (`context/aliases.py` `spoken_forms`), which also feed the bias list. 15 clips, ~20 critical strings

The aliases come from the spelling only, so the way people say these names never matches:

| term | what was said / heard | result |
|---|---|---|
| `Hyprland` (aliases: `hyprland`) | "hyper land" → "Hyperland", "Hyper LAN" | syn-063, syn-075 miss it |
| `hyprctl` (`hyprctl`) | "hyper control" → "Hyper-Control" | syn-075 |
| `kubectl` (`kubectl`) | "cube control" | syn-010 (no context there; the alias would not match anyway) |
| `hark ctl …` (`hark ctl paste last`, `hark c t l …`) | "hark control paste last", "harp control toggle" | syn-077, syn-078 (output "hark control paste-last") |
| `Ghostty` (`ghostty`) | "ghosty" | syn-078 |
| `basedpyright` (`basedpyright`) | "based pyright" → "based Pyright", "wave-based pie write" | syn-029, syn-037 |
| `ruff` | "ruff check" → "Rough Check" (the bias did not win) | syn-037 |
| `SQLite` | "sequel light" | syn-065 |
| `read_wav` / `write_wav` (`read wav`) | "read wave" | syn-029 |
| `S1-mini` (`s one mini`, `s 1 mini`, `s1mini`) | "S1 Mini" (tokens `s1`, `mini`) | syn-087: letter+digit tokens are not split |
| `Qwen3` (`qwen three`, `qwen 3`) | "Quen 3" | syn-087 |
| `parakeet-tdt-0.6b-v3` (`… zero dot six …`) | "zero point six" → "0.6" | syn-054: versions are said with "point" and written as digits |
| `node:22-alpine` (`node colon twenty two alpine`) | "node twenty two alpine" | syn-055: the separator has to be spoken |
| `~/.kube/config` (`kube slash config`) | "tilde slash dot kube slash config" | syn-046: "…from tilde slash dot `~/.kube/config`" (the critical string is present but the text is garbled) |
| `@/lib/utils` (`lib slash utils`) | "at slash lib slash utils" | syn-050: "…resolve at slash `@/lib/utils`" (same) |
| `$XDG_RUNTIME_DIR` (`xdg runtime dir`) | "dollar XDG runtime dir" | syn-079 |
| `hark history` | "Hark history" (case only) | syn-036: canonicalization skips case-only rewrites; commands keep the sentence capital |

Fix direction: a pronunciation lexicon for common developer names (Hyprland, hyprctl, kubectl, `ctl`, Ghostty,
basedpyright, SQLite, Postgres, Qwen, wav, npm/pnpm…), merged into `spoken_forms` and the bias list, plus
`[[words]]` defaults. Generated variants: digits with "point" (and the digit form the recognizer writes),
optional spoken separators (":" "/"), leading "tilde slash dot", "at slash", "dollar", and letter+digit token
normalization (`s1` = `s 1`). For path, home and alias prefixes, either match the whole spoken path or nothing.
Allow a case-only rewrite for COMMAND terms at the start of a terminal utterance.

### 3. The anchor gate reverts correct text outside the term, drops words, and owns confidence. Stage: gate (`gate.py`). 12 clips damaged; recall −24 pts without it

44 `reverted/outside_term` decisions. On WER, 15 clips got better and 15 got worse. Damage:

- Words the biased decode had right: real-004 "so hard … Edinburgh" → "so hot … Edendra"; real-002 "quick very
  quickly" → "quickly very quickly"; real-013 "is the" → "his"; syn-081 "Redis" → "redisc"; syn-037 "based
  Pyright" → "baste pi right"; syn-054 "Parakeet TdT" → "Parake Td"; syn-073 "APID" → "agit".
- A dropped clause: real-008 "I asked you a question. So, why…" → "So why…". The anchor never heard the first
  sentence, and the gate deleted it.
- Spans that cost a critical string: syn-019 "the is terminal" → "this terminal" (`is_terminal` lost), syn-036
  "hark history" → "Hark history" (`hark history` lost), syn-007 "uv run pyTest-k" → "UV runpytest-k", syn-087
  "S1Mini" → "S1 Mini" (`anchor_confident`).
- It helped elsewhere: syn-008 "MT" → "empty", syn-015, syn-018, real-006, real-014, real-020.

The gate is also the only source of usable word confidence for canonicalization (see "What each stage
contributes"). Fix direction: separate the two jobs. Give biased words a confidence from the anchor alignment
whether or not text is reverted. Revert only regions that overlap a bias hit. Never take an anchor region that
drops words, or that differs only in case.

### 4. Acoustic errors on short command words and rare names, with no context to help. Stage: recognition. ~16 clips

`git status` → "Get status." (syn-005) and `git push` → "Get push" (syn-041): `git` is never on the shortlist
unless something showed it. Other cases: "main" → "me" (syn-012, which reverses the target branch), "make" →
"I"/"Eck" at the very start (syn-016), "add" → "At" (syn-017), "false" → "paused" (syn-021), "fallback" →
"foulback" (syn-069), "offsite" → "inside" (syn-090), "CUDA" → "PUDA" (syn-056), "zod" → "audit" (syn-042),
"httpx dot async client" → "http ake.usic client" (syn-032), "tauri dot conf" → "Indo we dot conf" (syn-049),
"journalctl … dash u hark" → "journal control-user dash you rock" (syn-044), "dir" → "diar" (syn-043),
"p n p m add" → "B N B M R" (syn-080), "dash dash force, dash dash verbose" → "dash-bos, dash-hubbles"
(syn-074). On real speech, first words get lost: "who loved each other no…" → "No…" (real-011), "you know the
last time I heard…" → "We had…" (real-012). Fix direction: in the terminal profile, always put a small base
vocabulary of shell commands and their spoken forms on the shortlist (git, kubectl, pnpm, uv, npm, docker,
journalctl…). Also check why the first word is lost on syn-016/real-011 even with 150-500 ms of lead silence
(not investigated).

### 5. Spoken symbols and numbers. Stage: symbols (`symbols.py`), which cleanup does not touch. 8 clips

| said | final | gap |
|---|---|---|
| "docker compose up dash d" | "Docker compose up-d" | recognizer-joined `word-x` is only split after known commands |
| "ruff check with dash dash fix" | "with dash-fix" | recognizer-joined "dash-fix" is not read as `--fix` |
| "uv run pytest dash k gate" | "pyTest-k" | same |
| "curl localhost colon eight thousand slash health" | "localhost:8000 slash health" | one trailing "slash X" after a host:port is not joined |
| "set … to slash data slash models" | "to/data/models" | a leading-slash path is glued to the previous word |
| "chmod plus x" | "chmod plus x" | no rule for "plus x" |
| "user underscore id" | "user underscore id" | no "underscore" rule without a shortlist term |
| "zero comma one" | "0, comma one" | — |
| "issue four twenty one … PR four thirty seven" | "four twenty-one … four thirty-seven" | number words after "issue"/"PR"/"port" stay words |

### 6. Self-corrections: substitutions are not applied, and one false positive drops a claim. Stage: corrections (`corrections.py`) and cleanup protected spans. 4 clips

`resolve()` needs a word after the cue that repeats an earlier word ("in C, no, in Rust"). When a speaker just
replaces the value, nothing happens, and cleanup's deletion edit is dropped because the retracted number or term
is a protected span:

- syn-065 "use Postgres, no wait, SQLite for the cache" → "Use postgres. No wait. Sequel light for the cash."
- syn-066 "set the timeout to three seconds, sorry, five seconds" → "…to 3 seconds, sorry 5 seconds." (`protected_span`)
- syn-072 "make the limit ten megabytes, no, twenty megabytes" → "…10 megabytes, no, 20 megabytes." (`protected_span`)

The repeated-word restarts were resolved deterministically: syn-067, syn-070, syn-071, and syn-069 apart from
"foulback". The cleanup model resolved syn-068 ("call it fetch rows, I mean fetch row batch").

False positive on real speech, real-009: raw "I didn't even realize the ghost one left. No, the only character I
remember was…" → "I didn't even realize the only character I remember was…". The "No," was a recognition error
for "you know", and `resolve()` retracted "the ghost one left. No," across a sentence end. A claim was dropped
deterministically before the model ever saw it.

### 7. Cleanup leaves fillers and rarely acts. Stage: cleanup (server S1 engine + client validator). 7 clips

6 of 18 finals keep um/uh/eh/mm (real-006 "It was more like um, you know, grade gift. He he told me…"
`partial 1 of 8 edits applied; dropped: dependent, invariant`; real-014 "…not mm planified"; real-016 "uh that
uh"; real-019; real-020; syn-076 "In the PyTorch training loop, eh, move…"). syn-083 keeps "you know". Of 122
runs: 62 no edits, 32 all edits rejected, 10 partial, 18 applied. In the baseline it dropped no negation and no
critical string. The only critical string lost at cleanup in any run is in the no-context run: syn-040 "hark
once" → "Hark once". Without the shortlist the command is not a protected span, and it got a sentence capital.
Fix direction: read
the `invariant` and `protected_span` rejections (25 + 19). A filler that touches a protected span or sits between
repeated words ("um, you know") is probably rejected as a whole.

### 8. Degenerate recognizer output after a particular session sequence. Stage: recognition (hark-server `parakeet_mlx`); guardrails pass it. 1 clip, deterministic

syn-005 (2.3 s, "git status") decodes to `ÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌÌ` (or 60× `<unk>` with no bias terms) in four of
the five full runs, and that is the final text that would be pasted. Alone, or after any one other clip, it
decodes as "Get status." Minimal repro (deterministic, both with and without context): replay real-024, syn-001,
syn-002, syn-003, syn-004, syn-005 in that order (`hark eval run --set DIR` with only those six). None of the
smaller prefixes tried triggers it: syn-001…004 without real-024, or real-024 with two of syn-001…004. The bug is server-side state that carries over between sessions. The client's
guardrails should also refuse a final text made of `<unk>` or of one repeated character.

### 9. Negations changed by recognition on accented real speech. Stage: recognition. 4 clips

real-017 (Jamaican English) "I never watched it, the first time I hear about it" → "And I watch it. At first my
ear bought it." (negation lost). "you know" is heard as "No," three times (real-003, real-009, real-013). That
adds a negation, which the negation check does not count, and in real-009 it triggered class 6. No negation was
lost after recognition in any condition.

## Per-clip triage (baseline, not accepted)

| clip | result | class | stage | evidence |
|---|---|---|---|---|
| real-001 | FAIL | 1 | canonical | `goBack` |
| real-002 | rejected | 3, 4 | gate, recognition | "quick" → "quickly"; "in our school" → "or so" |
| real-003 | rejected | 9 | recognition | "you know" → "No," (adds a negation) |
| real-004 | rejected | 3 | gate | "hard", "Edinburgh" → "hot", "Edendra" |
| real-005 | FAIL | 1 | canonical | `let_me` |
| real-006 | rejected | 7 | cleanup | "um, you know", "He he" kept |
| real-007 | FAIL | 1 | canonical | `each_other` |
| real-008 | rejected | 3, 4 | gate | first sentence dropped by the gate (and misheard: "let me ask" → "I asked") |
| real-009 | rejected | 6, 9 | corrections | "the ghost one left. No," retracted |
| real-011 | rejected | 4 | recognition | first clause lost |
| real-012 | rejected | 4 | recognition | "you know the last time I heard" → "We had"; "you play cool" → "it's really cool" |
| real-013 | rejected | 3, 9 | gate, recognition | "is the" → "his"; "No, I don't know" added |
| real-014 | rejected | 7 | cleanup | "I I I", "mm" kept |
| real-015 | FAIL | 1, 4 | canonical | `each_other`; "i can then get" → "I think I'm gonna get" |
| real-016 | rejected | 7 | cleanup | "uh that uh" kept |
| real-017 | FAIL | 9 | recognition | "never watched" → "watch" |
| real-019 | rejected | 4, 7 | recognition | "get negative" → "guess"; "uh" kept |
| real-020 | rejected | 4, 7 | recognition | "had one" → "have one"; "uh" kept |
| real-024 | rejected | 4 | recognition | "Ruggedo" → "Ruggedau" |
| syn-003 | rejected | 4 | recognition | "looks good, ship it" → "Looks good to ship it." |
| syn-005 | FAIL | 8, 4 | recognition | `ÌÌÌ…`; otherwise "Get status." |
| syn-007 | FAIL | 5, 3 | symbols, gate | "pyTest-k"; gate → "UV runpytest-k" |
| syn-010 | FAIL | 2, 4 | recognition | "Cube control get pods dash n staging" (empty context) |
| syn-012 | rejected | 4 | recognition | "main" → "me" |
| syn-016 | FAIL | 4 | recognition | "make load snapshot" → "Eckload snapshot" |
| syn-017 | rejected | 4 | recognition | "Add" → "At" |
| syn-019 | FAIL | 3, 4 | gate | "the is terminal" → "this terminal"; "to, uh, terminal like" → "2 a terminal light" |
| syn-021 | rejected | 4 | recognition | "false" → "paused" |
| syn-029 | FAIL | 2 | canonical | "read wave", "based pie write" |
| syn-031 | FAIL | 5 | symbols | "user underscore id" (empty context) |
| syn-032 | FAIL | 4 | recognition | "http ake.usic client" |
| syn-036 | FAIL | 2, 3 | gate | "Hark history" (case) |
| syn-037 | FAIL | 2, 5, 3 | canonical, symbols, gate | "Rough Check with dash-fix … based Pyright" |
| syn-041 | rejected | 4 | recognition | "git push" → "Get push" |
| syn-042 | FAIL | 4 | recognition | "npm install zod at" → "NPM installs audit" |
| syn-043 | FAIL | 4, 5 | recognition, symbols | "diar"; "to/data/models" |
| syn-044 | FAIL | 4, 2 | recognition | "journal control-user dash you rock" |
| syn-045 | FAIL | 4, 5 | recognition, symbols | "make dash a val dash set"; "plus x" |
| syn-046 | rejected | 2 | canonical | "from tilde slash dot ~/.kube/config" |
| syn-047 | FAIL | 5 | symbols | "up-d", "localhost:8000 slash health" |
| syn-049 | FAIL | 4 | recognition | "Indo we dot confot jisan" |
| syn-050 | rejected | 2 | canonical | "resolve at slash @/lib/utils" |
| syn-051 | rejected | 4 | recognition | "Python 3.123.13" (critical strings present as substrings: the check is weak there) |
| syn-053 | FAIL | 5 | symbols/cleanup | "four twenty-one", "four thirty-seven" |
| syn-054 | FAIL | 2, 3 | canonical, gate | "0.6 B V3"; gate → "Parake Td" |
| syn-055 | FAIL | 2 | canonical | "node 22 Alpine" |
| syn-056 | rejected | 4 | recognition | "CUDA" → "PUDA" |
| syn-063 | FAIL | 2 | canonical | "Hyperland" |
| syn-065 | FAIL | 2, 6 | canonical, corrections | "Use postgres. No wait. Sequel light for the cash." |
| syn-066 | FAIL | 6 | corrections, cleanup | "3 seconds, sorry 5 seconds" |
| syn-069 | FAIL | 4 | recognition | "foulback" (correction itself right) |
| syn-072 | FAIL | 6 | corrections, cleanup | "10 megabytes, no, 20 megabytes" |
| syn-073 | rejected | 3, 4 | gate | "app id" → "APID" → "agit" |
| syn-074 | FAIL | 4 | recognition | "dash-bos, dash-hubbles and dash v" |
| syn-075 | FAIL | 2 | canonical | "HyperLAN", "HyperControl" |
| syn-076 | FAIL | 3, 7 | gate, cleanup | "model dot train" → anchor "model.Train"; "eh," kept |
| syn-077 | FAIL | 2 | canonical | "hark control paste-last" |
| syn-078 | FAIL | 2 | canonical | "ghosty", "harp control toggle" |
| syn-079 | FAIL | 4, 2 | recognition | "$XDG runtime die", "slash temp" → "/10" |
| syn-080 | FAIL | 4 | recognition | "B N B M R dash capital D Vtest" |
| syn-081 | rejected | 3 | gate | "Redis" → "redisc" |
| syn-082 | rejected | 5, 4 | symbols | "0, comma one"; "training run" → "training" |
| syn-083 | rejected | 7 | cleanup | "you know" kept |
| syn-084 | FAIL | 1 | canonical | `wait_for the build` |
| syn-087 | FAIL | 2, 3 | canonical, gate | "S1 Mini", "Quen 3" |
| syn-090 | rejected | 4 | recognition | "offsite" → "inside" |
| syn-091 | FAIL | 1 | canonical | `QuickShell` |
| syn-092 | FAIL | 1 | canonical | `last_text`, `one_line` |
| syn-093 | FAIL | 1 | canonical | `SessionStart` |
| syn-095 | FAIL | 1 | canonical | `mark_down` |
| syn-096 | FAIL | 1 | canonical | `TextField`, `ScreenText` |

## Reproduce

```sh
scripts/make-eval-set                                   # only to rebuild; the set is committed
for c in baseline no-context no-cleanup no-gate; do
  uv run hark --config docs/bench/eval-baseline/$c.toml eval run --set eval/dev-v1 \
    --report docs/bench/eval-baseline/$c.json > docs/bench/eval-baseline/$c.txt
done
uv run python docs/bench/eval-baseline/analyze.py      # the tables above (scored against the current reviews)
```
