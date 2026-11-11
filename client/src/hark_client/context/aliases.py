"""Spoken forms ("aliases") for written terms, and term extraction from free text.

An alias is lower-case words separated by single spaces, the way an ASR engine is
expected to transcribe someone *saying* the term:

- ``useSessionStore`` -> ``use session store``
- ``parse_args`` -> ``parse args``, ``parse underscore args``
- ``--dry-run`` -> ``dash dash dry run``, ``double dash dry run``, ...
- ``store.ts`` -> ``store dot ts``, ``store dot t s``
- ``HTTPServer`` -> ``http server``, ``h t t p server``
"""

from __future__ import annotations

import itertools
import re

from hark_client.context.types import TermKind

MAX_ALIASES = 8

# One "word" inside an alphanumeric run: acronym before a capitalised word, capitalised or
# lower-case word, trailing acronym, or digits.
_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+")
_SEP_RE = re.compile(r"[^A-Za-z0-9]+")

_NUMBER_WORDS = (
    *("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
    *("eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen"),
    *("eighteen", "nineteen", "twenty"),
)
_VOWELS = frozenset("aeiouy")
# Separator spellings. Empty string = separator not pronounced.
_SEPARATORS: dict[str, tuple[str, ...]] = {
    "_": ("", "underscore"),
    "-": ("", "dash"),
    ".": ("dot",),
    "/": ("slash",),
    ":": ("colon",),
    "@": ("at",),
    "+": ("plus",),
    "#": ("hash",),
    "~": ("tilde",),
}
_FLAG_PREFIX: dict[str, tuple[str, ...]] = {
    "--": ("dash dash", "double dash"),
    "-": ("dash",),
}
# Extensions that are said as a word rather than spelled out.
_EXT_WORDS: dict[str, tuple[str, ...]] = {
    "json": ("json", "jason"),
    "yaml": ("yaml", "yamel"),
    "yml": ("yml", "y m l", "yamel"),
    "md": ("md", "m d", "markdown"),
    "py": ("py", "p y", "pie"),
    "toml": ("toml", "tommel"),
}


def split_words(text: str) -> list[str]:
    """Split an identifier/path into its words: ``HTTPServer2_v`` -> HTTP, Server, 2, v."""
    return [w.group() for run in _ALNUM_RE.finditer(text) for w in _WORD_RE.finditer(run.group())]


def _is_acronym(word: str, *, shouting: bool) -> bool:
    if word.isupper() and not shouting and 2 <= len(word) <= 5:
        return True
    # short lower-case tokens without vowels are usually spelled: ts, js, md, cfg
    return len(word) <= 3 and not (set(word.lower()) & _VOWELS)


def _word_options(word: str, *, is_ext: bool, shouting: bool) -> tuple[str, ...]:
    low = word.lower()
    if is_ext and low in _EXT_WORDS:
        return _EXT_WORDS[low]
    if word.isdigit():
        n = int(word)
        return (word, _NUMBER_WORDS[n]) if n < len(_NUMBER_WORDS) else (word,)
    if _is_acronym(word, shouting=shouting):
        return (low, " ".join(low))
    return (low,)


def _pieces(canonical: str, kind: TermKind) -> list[tuple[str, ...]]:
    """Sequence of alternatives (words and separator spellings) for ``canonical``."""
    pieces: list[tuple[str, ...]] = []
    body = canonical
    if kind is TermKind.FLAG:
        prefix = "--" if body.startswith("--") else "-" if body.startswith("-") else ""
        if prefix:
            pieces.append(_FLAG_PREFIX[prefix])
            body = body[len(prefix) :]
    last_dot = body.rfind(".") if kind is TermKind.PATH else -1
    # ALL_CAPS constants: the words are words (MAX_TERMS), not acronyms to spell
    shouting = body.upper() == body and len(split_words(body)) > 1
    pos = 0
    for m in _ALNUM_RE.finditer(body):
        sep = body[pos : m.start()]
        if sep and pieces:
            pieces.append(_separator_options(sep))
        is_ext = last_dot >= 0 and m.start() == last_dot + 1
        words = [w.group() for w in _WORD_RE.finditer(m.group())]
        pieces.extend(_word_options(w, is_ext=is_ext, shouting=shouting) for w in words)
        pos = m.end()
    return pieces


def _separator_options(sep: str) -> tuple[str, ...]:
    if len(sep) == 1:
        return _SEPARATORS.get(sep, ("",))
    # "__" / "::": repeat the spelling; "__." (as in __init__.py): underscores are silent
    if len(set(sep)) > 1:
        sep = sep.replace("_", "").replace("-", "")
        if len(set(sep)) != 1:
            return ("",)
    if sep[0] in _SEPARATORS:
        opts = _SEPARATORS[sep[0]]
        return tuple(" ".join([o] * len(sep)).strip() for o in opts)
    return ("",)


def spoken_forms(canonical: str, kind: TermKind) -> tuple[str, ...]:
    """Lower-case spoken aliases for ``canonical``, most natural first, at most MAX_ALIASES."""
    pieces = _pieces(canonical, kind)
    if not pieces:
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for combo in itertools.islice(itertools.product(*pieces), 64):
        alias = " ".join(" ".join(combo).split())
        if alias and alias not in seen:
            seen.add(alias)
            out.append(alias)
            if len(out) >= MAX_ALIASES:
                break
    return tuple(out)


# --------------------------------------------------------------------------- extraction

_BACKTICK_RE = re.compile(r"`([^`\n]{1,120})`")
_PATH_RE = re.compile(
    r"(?<![\w/.~-])(?:~|\.{1,2})?/?(?:[\w.@+-]+/)+[\w.@+-]*\w\.[A-Za-z][A-Za-z0-9]{0,9}(?![\w/])"
    r"|(?<![\w/.~-])[\w@+-]*[A-Za-z0-9]\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|rs|go|md|json|jsonl|toml|yaml|yml|"
    r"proto|sh|zsh|fish|lua|nix|c|h|cc|cpp|hpp|java|kt|swift|rb|css|scss|html|sql|txt|lock|cfg|ini|env)\b"
)
_FLAG_RE = re.compile(r"(?<![\w-])--?[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*(?![\w-])")
_IDENT_RE = re.compile(r"(?<![\w.-])[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?![\w-])")
_KEBAB_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
_KEBAB_WORD_RE = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?![\w-])")

_STOP = frozenset(
    {
        "TODO",
        "FIXME",
        "XXX",
        "NOTE",
        "OK",
        "TYPE_CHECKING",
        "NULL",
        "TRUE",
        "FALSE",
        "AND",
        "NOT",
        "THE",
        "GET",
        "PUT",
        "POST",
        "e.g",
        "i.e",
        "etc",
    }
)


def is_interesting_identifier(token: str) -> bool:
    """True for compound or mixed-case identifiers; plain words (``store``, ``Hello``) are not."""
    if token in _STOP or len(token) > 60 or len(token) < 2:
        return False
    if token.startswith("__") and token.endswith("__"):
        return False
    words = split_words(token)
    if not words:
        return False
    if len(words) >= 2:
        # "Hello." style prose and single letter + digits ("x1") are noise
        return not (len(words) == 2 and len(words[0]) == 1 and words[1].isdigit())
    word = words[0]
    if word.isupper():
        return len(word) >= 3  # acronyms: API, HTTP; skip "OK", "ID"
    # internal capitals such as "iPhone"; plain lower/Capitalised single words are prose
    return word not in {word.lower(), word.capitalize()}


def extract_terms(text: str, *, prose: bool = False) -> list[tuple[str, TermKind]]:
    """Identifiers, paths and flags in ``text``, in first-seen order, deduplicated.

    With ``prose=True`` (agent replies, selections of prose) kebab-case words are only taken
    from backticked spans, since ``well-known`` style words are ordinary English.
    """
    found: dict[str, TermKind] = {}

    def add(term: str, kind: TermKind) -> None:
        term = term.strip(".,;:'\"()[]{}<>")
        if term and term not in found:
            found[term] = kind

    for m in _BACKTICK_RE.finditer(text):
        span = m.group(1).strip()
        if " " not in span:
            if _FLAG_RE.fullmatch(span):
                add(span, TermKind.FLAG)
            elif "/" in span or _PATH_RE.fullmatch(span):
                add(span, TermKind.PATH)
            elif _KEBAB_RE.match(span) or is_interesting_identifier(span):
                add(span, TermKind.IDENTIFIER)
    for m in _PATH_RE.finditer(text):
        add(m.group(), TermKind.PATH)
    for m in _FLAG_RE.finditer(text):
        if m.group().startswith("--") or len(m.group()) == 2:
            add(m.group(), TermKind.FLAG)
    for m in _IDENT_RE.finditer(text):
        tok = m.group()
        # file names such as ``types.py`` were captured as paths above
        if not _PATH_RE.fullmatch(tok) and is_interesting_identifier(tok):
            add(tok, TermKind.IDENTIFIER)
    if not prose:
        for k in _KEBAB_WORD_RE.finditer(text):
            add(k.group(), TermKind.IDENTIFIER)
    return list(found.items())
