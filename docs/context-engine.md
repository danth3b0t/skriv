# Context engine: sources, ranking, learned vocabulary, prefetch

The "with context" of *recognize with context* ([architecture.md](architecture.md)): `client/src/hark_client/context/`
builds the shortlist sent with `SessionStart` and canonicalizes the result against it. The ranking follows
[archive/research/context-ranking/REPORT.md](archive/research/context-ranking/REPORT.md) and the context-package parts of
[archive/research/context-architecture.md](archive/research/context-architecture.md).

## What `collect()` gathers

| Origin (`rank.Origin`) | From | Weight | Wire `TermSource` |
|---|---|---|---|
| `selection` | selected text (AX) | 1.2 [U] | SELECTION |
| `user` | the user's own prompts, last 8 agent-session groups (tail 6,000 chars each) | 1.1 | AGENT_SESSION |
| `focused_text` | focused text field, ±4,000 chars around the caret (macOS AX, Linux AT-SPI) | 1.0 [U] | FOCUSED_TEXT |
| `terminal_text` | the exact tmux pane of the focused terminal (grid + 200 lines), or Terminal.app via AX | 0.9 [U] | TERMINAL_TEXT |
| `assistant` | agent responses, last 8 groups (tail 1,500 chars each) | 0.85 | AGENT_SESSION |
| `tools` | tool-call paths per group (full path, file name, stem; ≤ 30) | 0.75 | AGENT_SESSION |
| `window_title` | focused window title | 0.7 [U] | WINDOW_TITLE |
| `buffer` | a terminal buffer without a trusted caret (Ghostty 1.4 AX): last 3,000 chars | 0.6 [U] | TERMINAL_TEXT |
| `persist_repo` | vocabulary learned from earlier sessions in this repository | 0.55 | VOCABULARY |
| `repo_recent`, `cwd` | changed/recent files and their identifiers; repo name, branch | 0.45 | REPO_RECENT |
| `persist_user` | vocabulary learned from all earlier sessions | 0.30 | VOCABULARY |
| `repo` | repository-wide files (100 shallowest) | 0.25 | REPO |

`[U]`: not measured (the offline experiment had no screen sources). Screen text is scored by proximity
(`exp(-chars/1000)` from the caret, `exp(-rows/40)` from the tmux cursor row).

Never read: deny-listed apps (password managers, keychain), secure/password fields, tmux panes running a credential
program (`sudo`, `ssh`, `gpg`, `pass`, …) or ending in a password prompt. All text is scrubbed before extraction
(tokens and opaque runs; long real paths are kept), and only terms leave the package.

## Ranking and list planning (`rank.py`)

The development-selected recipe `tune_n8_d0.9`, verbatim: for a term with evidence from origin `s` at group age `a`
(0 = newest) and source-internal score `r`,

```text
v = weight[s] * 0.9**a * r;  F = Σ 0.9**a over distinct (origin, age)
score = max(v) * (1 + 0.15*log1p(max(0, F-1))) + 0.12*(distinct origins - 1)
```

No recency floor, no saturation before sorting, ties by canonical string. Near-duplicate spellings
(`parse_config`/`parseConfig`/`parse-config`) compete; the best one stays and the others are listed as
`competitors`. At most `MAX_TERMS = 30` (`collect(..., max_terms)`), fewer when there is little context.
`Term.weight = 1 - exp(-1.5·score)` until acoustic calibration exists.

`ContextSnapshot.provenance` has one `TermTrace` per term: score, weight, up to four `Evidence(origin, age, value)`
and the dropped competitors. `ContextSnapshot.prefetched` says whether the slow sources came from the daemon.

## Extraction and aliases (`aliases.py`)

- Numbers beyond 20 (`cardinal()`, up to 999,999) and a natural-first alias order: the fully spoken reading, then
  single-piece variants, then the bounded Cartesian product (8-alias cap kept). Long and number-bearing paths now
  start with a spoken form (`report_v256.json` → "report v two fifty six dot json").
- With the platform word list (`/usr/share/dict/words`): Capitalized non-words anywhere (`Ghostty` at a sentence
  start), acronyms that are no word (`UX`), kebab-case with a non-word part (`ctx-rank`), backticked non-words
  (`kubectl`); inflections stay out (`Configures`, `Running`). Without a word list the old conservative rules apply.
- Directory paths (`~/projects/hark`, `/usr/share/dict`, `client/src/hark_client`).
- Never terms: tool-call/opaque ids, hashes, UUIDs, XML tags, contraction fragments, glob/template spans.
- Spoken forms also come from a pronunciation lexicon (`hyprctl` → "hyper control", `SQLite` → "sequel light",
  `ctl` → "control"), versions said with "point", and spoken path prefixes ("tilde slash").

## Canonicalization (`canon.py`)

After the gate, spoken forms of shortlist terms in the transcript are rewritten to their spelling. Only aliases
already in the transcript are rewritten, whole words, never across a clause boundary, longest first, and only where
the words' confidence allows. A match made only of everyday English words (`each other`, `wait for`; the list is
`common_words.txt`) needs evidence that an identifier was meant: a spoken separator, a capital mid-sentence, or
identifier syntax around it (`called X`, `the X function`). The full rule list is the module docstring.

## Learned vocabulary (`vocab.py`)

Per repository and per user, from the user's own prompts in **ended** main sessions (idle ≥ 15 min; the matched
session is excluded), pi / Claude Code / Codex. Terms only; prompts > 6,000 chars teach nothing. Score
`(0.5 + 0.5·min(1, log1p(sessions)/log 6)) · 2^(-days/30)`; 300 served per scope. Store
`<data dir>/context-vocab.json` (0600, atomic; capped at 600 terms per cwd scope, 2,000 user terms, 100 scopes,
5,000 file marks). Files are read incrementally, ≤ 8 MiB per file and 40 files per pass.

```sh
uv run python -m hark_client.context vocab list   # what is learned, terms served for the current repo
uv run python -m hark_client.context vocab update # read ended sessions now
uv run python -m hark_client.context vocab clear  # delete the store
```

## Prefetch daemon (`prefetch.py`)

`context.start_prefetch(data_dir, learn=True)` / `context.stop_prefetch()`. A daemon thread polls
`focus_token()` every 250 ms (Hyprland: `.socket2.sock` events instead), and every 2 s re-resolves the terminal
and stats cheap validators (git `HEAD`/`index`, session directories, the matched session file); on change it
rebuilds the window's warm entry (repo, agent session, learned vocabulary). The daemon never uses the
Accessibility worker, so it cannot make a key-down query skip. A second thread learns vocabulary in bounded passes.
At key-down `collect()` uses a warm entry validated within 6 s and only reads focus, selection and screen text:
one run on this Mac: 38 ms warm vs 375 ms cold.

The daemon starts it (`daemon.py`) with `cfg.history.dir`; `ContextConfig.max_terms` is 30 and history records
carry `snap.provenance`. Hand-written `[[words]]` (the config's, then `<repo>/.hark/words.toml`) are merged in
front of the ranked terms by the client (`context/words.py`, called from `pipeline.py`), not by the engine; the
terminal profile also gets base shell commands (`git`, `uv`, `kubectl`, …) in the free slots, at most a quarter
of a full list.

## Offline replay (the experiment harness, same cutoff and splits)

Measured when this ranking landed (2026-11-12); recognition with these lists is measured by `hark eval`
([eval.md](eval.md)).

Primary short prompts, exact-match gold, macro recall (micro in parentheses). `old` = task-start engine
(`current_engine`), `recipe` = the experiment's selected configuration (its expanded extraction *is* the gold
extractor), `new` = this implementation driven through `session_candidates`, `repo_candidates`, `Vocab` and `rank`.

| | dev R@30 | dev R@40 | test R@30 | test R@40 |
|---|---|---|---|---|
| old engine | 11.7 (10.2) | 11.9 (11.3) | 5.5 (4.7) | 5.5 (4.7) |
| new | 22.6 (22.0) | 25.5 (24.9) | 14.9 (18.7) | 15.8 (20.0) |
| recipe (gold-based extraction) | 32.8 (27.7) | 36.6 (31.6) | 23.6 (24.7) | 27.4 (28.7) |
| new + the harness's gold extractor | 30.5 (26.6) | 33.7 (29.9) | 19.1 (18.0) | 23.1 (23.3) |

The remaining gap is mostly extraction agreement with the gold definition (its fixed technology lexicon of lower-case
names such as `tmux`, `uv`, and quoting rules), not ranking. Ablations on `new`: near-duplicate competition costs
1.5 points on test (case variants are misses under exact matching) and nothing on dev; repository-wide files are
neutral to slightly positive; learning recurring lower-case non-words lowered dev recall by 3.6 points and was
dropped; path-aware scrubbing gained 0.4 (dev) to 0.9 (test R@30). Alias heuristic pass rate on unique gold:
paths 92 → 102 of 130, identifiers 191 → 192 of 204; first alias fully spoken: paths 75 → 129, identifiers
169 → 201. This is retrieval only: no audio, no false-substitution measurement.

## Open

- `context/_atspi.py` (Collection API focused-text match used for caret and screen text) is untested on hardware.
- macOS focus uses polling, not `NSWorkspace`/`AXObserver` notifications (they need an AppKit run loop thread).
- hark's own dictations are user prompts, so they teach the vocabulary (architecture §7.4 self-contamination).
- Alias collisions are not tracked per list; `canon.py`'s ambiguity guard still applies.
