# Accuracy baseline: `skriv eval`

A small set of **real** dictations, re-run through the whole pipeline (recognition with the key-down context,
canonicalization, symbols, cleanup, guardrails) after every change that can affect text. It is
regression coverage for the people who use skriv, not a benchmark and not proof that cleanup "never" changes a
meaning.

## The promise it checks

The final text keeps the claims and instructions that were said (explicit self-corrections applied). Cleanup may
reword. So a run checks mechanically only what can be checked mechanically, and leaves wording to a human:

| check | fails the run |
|---|---|
| every `critical` string (identifier, path, flag, number) is in the final text, exactly | yes; reports whether recognition or a later stage lost it |
| no `forbidden` string is in it (a dictionary word where the ordinary word was meant) | yes |
| it has at least as many negations (`not`, `no`, `never`, `n't`, ...) as `said`, or as `said` with a spoken self-correction resolved (`skriv_client/fidelity.py`) | yes, unless a human accepted that exact text |
| it is one of the `accepted` texts | no: a new wording is **unreviewed**; read it, then `skriv eval accept ID` |
| recognition word error rate of the raw text against `said` | no, reported |

## Building the set

1. After a dictation worth keeping (a failure, or an ordinary prompt), `skriv eval add` copies the newest kept
   recording (or `skriv eval add ID`, an id from `skriv history`) with its key-down context into
   `<history.dir>/eval/<id>/` and pre-fills `label.json` from its history record.
2. Edit `label.json`: `said` = what you said, verbatim; `critical` = the strings that must come out exactly;
   `forbidden` = strings that must not appear. Leave `accepted` empty.
3. `skriv eval run`, read every **new** line, and `skriv eval accept ID` the texts that say what you said.

Aim for 40-60 clips from every person who uses skriv: spontaneous prompts, short and long, your real microphones,
dictated through the desktop GPU server and through the CPU fallback (`skriv --server ADDR eval run` picks one),
and some ordinary speech that contains a `[[words]]` alias where the plain words were meant. Keep failures from
history, but not only failures. One person owns the set: adds clips when something goes wrong, retires duplicates.

## Running it

```sh
skriv eval run                 # exit 1 on a missing critical / forbidden string / lost negation
skriv eval run --set DIR       # another set
skriv eval run --report r.json # also every clip's stages (raw, canonical, uncleaned, final), score, timings
skriv --config no-context.toml eval run   # ablation: [context] max_terms = 0 replays without the shortlist
```

Each clip keeps its last outcome in `last.json` (raw, canonicalized and final text, cleanup status, provider).
Nothing is pasted and nothing is written to the history. The audio is private: the set lives in the history
directory, not in the repository. The replayed shortlist is cut to `context.max_terms`, so a config with
`max_terms = 0` measures what the context adds; `[cleanup] enabled = false` and `[gate] enabled = false` are the
other ablations.

## The shared development set: `eval/dev-v1`

Until the per-person sets exist, `eval/dev-v1/` (in the repository, 122 clips, 24 MB) stands in: 96 developer
prompts to coding agents synthesized with many voices, accents, rates, rooms and noises, and 26 clips of real
speech (EdACC conversational English in ten accents, LibriSpeech) paired with a dense skriv shortlist whose
identifiers collide with ordinary words (`wait for` / `wait_for`). `scripts/make-eval-set` rebuilds it
reproducibly (macOS: `say`, `afconvert`; downloads are pinned and cached in `models/eval-cache`); the prompts and
their labels are in `scripts/eval_prompts.py`, and each clip's `note` records its voice, noise, context and, for
real speech, the source and license. Regenerating keeps the reviewed texts of unchanged clips. Synthetic speech
is cleaner and more regular than a person at a desk: use it to find failure classes, not to quote accuracy.
Baseline and ablations: `docs/bench/2026-11-13-eval-baseline.md`.
