# Accuracy baseline: `hark eval`

A small set of **real** dictations, re-run through the whole pipeline (recognition with the key-down context,
canonicalization, symbols, self-corrections, cleanup, guardrails) after every change that can affect text. It is
regression coverage for the people who use hark, not a benchmark and not proof that cleanup "never" changes a
meaning.

## The promise it checks

The final text keeps the claims and instructions that were said (explicit self-corrections applied). Cleanup may
reword. So a run checks mechanically only what can be checked mechanically, and leaves wording to a human:

| check | fails the run |
|---|---|
| every `critical` string (identifier, path, flag, number) is in the final text, exactly | yes; reports whether recognition or a later stage lost it |
| no `forbidden` string is in it (a dictionary word where the ordinary word was meant) | yes |
| it has at least as many negations (`not`, `no`, `never`, `n't`, ...) as `said` | yes, unless a human accepted that exact text |
| it is one of the `accepted` texts | no: a new wording is **unreviewed**; read it, then `hark eval accept ID` |
| recognition word error rate of the raw text against `said` | no, reported |

## Building the set

1. After a dictation worth keeping (a failure, or an ordinary prompt), `hark eval add` copies the newest kept
   recording (or `hark eval add ID`, an id from `hark history`) with its key-down context into
   `<history.dir>/eval/<id>/` and pre-fills `label.json` from its history record.
2. Edit `label.json`: `said` = what you said, verbatim; `critical` = the strings that must come out exactly;
   `forbidden` = strings that must not appear. Leave `accepted` empty.
3. `hark eval run`, read every **new** line, and `hark eval accept ID` the texts that say what you said.

Aim for 40-60 clips from every person who uses hark: spontaneous prompts, short and long, your real microphones,
dictated through the desktop GPU server and through the CPU fallback (`hark --server ADDR eval run` picks one),
and some ordinary speech that contains a `[[words]]` alias where the plain words were meant. Keep failures from
history, but not only failures. One person owns the set: adds clips when something goes wrong, retires duplicates.

## Running it

```sh
hark eval run                 # exit 1 on a missing critical / forbidden string / lost negation
hark eval run --set DIR       # another set
```

Each clip keeps its last outcome in `last.json` (raw, canonicalized and final text, cleanup status, provider).
Nothing is pasted and nothing is written to the history. The audio is private: the set lives in the history
directory, not in the repository.
