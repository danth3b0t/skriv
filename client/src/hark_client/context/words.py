"""The hand-written `[[words]]` dictionary as shortlist terms, merged ahead of the context terms."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from hark_client.config import REPO_WORDS, ConfigError, load_words, words_limit
from hark_client.context.aliases import spoken_forms
from hark_client.context.types import Term, TermKind, TermSource

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hark_client.config import WordConfig

log = logging.getLogger("hark.context")

_TOKEN_RE = re.compile(r"[^\W_]+")
# Shell commands said in a terminal that no context may show (``git`` is heard as "get"),
# most useful first; spoken forms from aliases.LEXICON ("cube control").
TERMINAL_COMMANDS = (
    "git", "uv", "npm", "pnpm", "docker", "kubectl", "cargo", "journalctl", "systemctl", "uvx",
)  # fmt: skip
TERMINAL_WEIGHT = 0.3  # below any context term with real evidence


def dictionary(
    user: Sequence[WordConfig], repo_root: str | None, max_terms: int
) -> list[WordConfig]:
    """Your `[[words]]`, then the repository's (``<repo>/.hark/words.toml``) not already there;
    at most a third of ``max_terms``. A broken repository file is logged and skipped."""
    repo: list[WordConfig] = []
    if repo_root is not None:
        try:
            repo = load_words(Path(repo_root) / REPO_WORDS, max_terms)
        except ConfigError as e:
            log.warning("repository words skipped: %s", e)
    taken = {w.word for w in user}
    return [*user, *(w for w in repo if w.word not in taken)][: words_limit(max_terms)]


def _kind(word: str) -> TermKind:
    if any(not (c.isalpha() or c.isspace()) for c in word):
        return TermKind.IDENTIFIER
    return TermKind.NAME if any(c.isupper() for c in word) else TermKind.WORD


def dictionary_terms(words: Sequence[WordConfig]) -> tuple[Term, ...]:
    """`[[words]]` as shortlist terms: the given spoken forms first, then the derived ones."""
    out: list[Term] = []
    for w in words:
        kind = _kind(w.word)
        spoken = [" ".join(_TOKEN_RE.findall(s.lower())) for s in w.spoken]
        out.append(
            Term(w.word, tuple(dict.fromkeys([*spoken, *spoken_forms(w.word, kind)])), 1.0, kind,
                 TermSource.DICTIONARY)
        )  # fmt: skip
    return tuple(out)


def with_terminal_commands(terms: Sequence[Term], max_terms: int) -> tuple[Term, ...]:
    """``terms`` plus the :data:`TERMINAL_COMMANDS` not on it, for the terminal profile. They
    take the free slots, and when the list is full at most a quarter of ``max_terms`` from
    the weakest (last) terms; never more than ``max_terms`` in all."""
    taken = {t.canonical for t in terms}
    missing = [
        Term(c, spoken_forms(c, TermKind.COMMAND), TERMINAL_WEIGHT, TermKind.COMMAND,
             TermSource.DICTIONARY)
        for c in TERMINAL_COMMANDS
        if c not in taken
    ]  # fmt: skip
    room = min(len(missing), max(max_terms - len(terms), max_terms // 4))
    return (*terms[: max_terms - room], *missing[:room])


def merge_terms(
    context_terms: Sequence[Term], first: Sequence[Term], max_terms: int
) -> tuple[Term, ...]:
    """``first`` (dictionary terms) ahead of ``context_terms``, their aliases joined with a
    context term of the same spelling; at most ``max_terms``."""
    if not first:
        return tuple(context_terms)
    by_canonical = {t.canonical: t for t in context_terms}
    merged = [
        replace(t, spoken=tuple(dict.fromkeys([*t.spoken, *by_canonical[t.canonical].spoken])))
        if t.canonical in by_canonical
        else t
        for t in first
    ]
    taken = {t.canonical for t in merged}
    merged.extend(t for t in context_terms if t.canonical not in taken)
    return tuple(merged[:max_terms])
