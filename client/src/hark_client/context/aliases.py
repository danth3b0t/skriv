"""Spoken forms ("aliases") for written terms, and term extraction from free text.

An alias is lower-case words separated by single spaces, the way an ASR engine is
expected to transcribe someone *saying* the term:

- ``useSessionStore`` -> ``use session store``
- ``parse_args`` -> ``parse args``, ``parse underscore args``
- ``--dry-run`` -> ``dash dash dry run``, ``double dash dry run``, ...
- ``store.ts`` -> ``store dot ts``, ``store dot t s``
- ``HTTPServer`` -> ``http server``, ``h t t p server``
- names: ``Qwen3-ASR`` -> ``qwen three asr``; ``RTX 3060`` -> ``rtx thirty sixty``,
  ``rtx three zero six zero``; ``Terminal.app`` -> ``terminal dot app``; ``NeMo`` -> ``nemo``
- commands: ``hark doctor`` -> ``hark doctor``, ``harkdoctor`` (ASR sometimes glues words)
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
    *("eighteen", "nineteen"),
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
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


def _two_digits(n: int) -> str:
    if n < len(_NUMBER_WORDS):
        return _NUMBER_WORDS[n]
    tens, unit = divmod(n, 10)
    return _TENS[tens] if unit == 0 else f"{_TENS[tens]} {_NUMBER_WORDS[unit]}"


def _pair(n: int) -> str:
    """Second half of a number read in pairs: 5 -> "oh five", 60 -> "sixty"."""
    return f"oh {_NUMBER_WORDS[n]}" if n < 10 else _two_digits(n)


def number_forms(digits: str) -> tuple[str, ...]:
    """How a digit run is said: as written, naturally, digit by digit.

    ``3060`` -> 3060, thirty sixty, three zero six zero, three oh six oh;
    ``100`` -> 100, one hundred, one zero zero, one oh oh; ``007`` -> 007, zero zero seven, ...
    """
    out = [digits]
    n = int(digits)
    if not (len(digits) > 1 and digits[0] == "0"):
        if len(digits) <= 2:
            out.append(_two_digits(n))
        elif len(digits) == 3:
            hundreds, rest = divmod(n, 100)
            out.append(f"{_NUMBER_WORDS[hundreds]} {'hundred' if rest == 0 else _pair(rest)}")
        elif len(digits) == 4:
            high, low = divmod(n, 100)
            if n % 1000 == 0:
                out.append(f"{_NUMBER_WORDS[n // 1000]} thousand")
            else:
                out.append(f"{_two_digits(high)} {'hundred' if low == 0 else _pair(low)}")
    if 3 <= len(digits) <= 6 or digits[0] == "0":
        spelled = [_NUMBER_WORDS[int(d)] for d in digits]
        out.append(" ".join(spelled))
        if "0" in digits:
            out.append(
                " ".join("oh" if d == "0" else w for d, w in zip(digits, spelled, strict=True))
            )
    return tuple(dict.fromkeys(out))


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
        return number_forms(word)
    if _is_acronym(word, shouting=shouting):
        return (low, " ".join(low))
    return (low,)


def _word_kind(word: str) -> TermKind:
    """Kind of one word of a multi-word command."""
    if word.startswith("-"):
        return TermKind.FLAG
    if "/" in word or "." in word:
        return TermKind.PATH
    return TermKind.IDENTIFIER


def _pieces(canonical: str, kind: TermKind) -> list[tuple[str, ...]]:
    """Sequence of alternatives (words and separator spellings) for ``canonical``."""
    if kind is TermKind.COMMAND and " " in canonical.strip():
        return [p for w in canonical.split() for p in _pieces(w, _word_kind(w))]
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
            # names: hyphens are silent (Qwen3-ASR), dots are said (Terminal.app)
            silent = kind is TermKind.NAME and sep.strip("-_ ") == ""
            pieces.append(("",) if silent else _separator_options(sep))
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


def _joined(canonical: str, kind: TermKind) -> str | None:
    """All words glued (``harkdoctor``, ``nemo``), the way ASR sometimes writes a name.

    Only for names/commands and identifiers that start upper-case: gluing ``setUp`` or
    ``set_up`` would give the plain word ``setup``.
    """
    words = split_words(canonical)
    if len(words) < 2 or kind in {TermKind.PATH, TermKind.FLAG}:
        return None
    if kind is TermKind.IDENTIFIER and not canonical[0].isupper():
        return None
    glued = "".join(words).lower()
    return None if glued in COMMON_WORDS else glued


def spoken_forms(canonical: str, kind: TermKind) -> tuple[str, ...]:
    """Lower-case spoken aliases for ``canonical``, most natural first, at most MAX_ALIASES."""
    pieces = _pieces(canonical, kind)
    if not pieces:
        return ()
    joined = _joined(canonical, kind)
    limit = MAX_ALIASES - 1 if joined else MAX_ALIASES
    out: list[str] = []
    for combo in itertools.islice(itertools.product(*pieces), 64):
        alias = " ".join(" ".join(combo).split())
        if alias and alias not in out:
            out.append(alias)
            if len(out) >= limit:
                break
    if joined and joined not in out:
        out.append(joined)
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


# Ordinary English words that show up capitalized (sentence starts, headings, emphasis).
_COMMON_TEXT = """
    a about above after again against all almost also although always am among an and
    another any anything are around as at back be because been before being below best
    better between both but by can cannot could did do does doing done down during each
    either else even ever every everything few first for from further had has have having
    he her here hers him his how however i if in instead into is it its itself just last
    least less let like many may maybe me might more most much must my neither never next
    no none nor not nothing now of off often on once one only or other others otherwise
    our ours out over own perhaps please rather same second she should since so some
    something still such than thanks that the their theirs them then there therefore these
    they third this those though through thus to too under unless until up upon us very
    was we well were what whatever when where whether which while who whom whose why will
    with within without would yes yet you your yours
    add added adding check checked done fix fixed keep kept make made note notes open read
    remove removed run running see set start stop test tests tested try update updated use
    used using verify verified write wrote ok okay sure great good right wrong true false
    null none self cls hello hey hi yeah new old next previous final finally summary
    status result results change changes file files step steps todo warning error errors
    example examples important overview context task tasks done why
    monday tuesday wednesday thursday friday saturday sunday january february march april
    june july august september october november december
"""
COMMON_WORDS = frozenset(_COMMON_TEXT.split())
_FENCE_RE = re.compile(r"^[ \t]*```.*?(?:^[ \t]*```|\Z)", re.MULTILINE | re.DOTALL)
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+")
# word-ish tokens; "/" may separate two names (Hyprland/Omarchy)
_NAME_TOKEN_RE = re.compile(r"(?<![\w.@-])[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*(?![\w@-])")
_ACRONYM_NUMBER_RE = re.compile(r"(?<![\w.-])[A-Z]{2,} \d{2,5}(?!\w|\.\d)")  # RTX 3060
_SENTENCE_END = frozenset(".!?:;|")  # | = table cell
_COMMAND_RE = re.compile(r"[A-Za-z0-9.~/_-][\w./:@=+~-]*(?: [\w./:@=+~-]+){1,5}")


def _mask(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group()), text)


def _title_lines(text: str) -> set[int]:
    """Start offsets of headings (``#`` lines, or every long word Capitalized), where capitals
    mean nothing."""
    out: set[int] = set()
    pos = 0
    for line in text.splitlines(keepends=True):
        words = [w for m in re.finditer(r"[A-Za-z]+", line) if len(w := m.group()) > 3]
        if line.lstrip().startswith("#") or (
            len(words) >= 3 and all(w[0].isupper() for w in words)
        ):
            out.add(pos)
        pos += len(line)
    return out


def _sentence_start(text: str, start: int) -> bool:
    """No word before ``start`` in the same sentence (lines count as sentences)."""
    i = start - 1
    while i >= 0 and text[i] in " \t*_#>\"'([`~-+•":
        i -= 1
    if i < 0 or text[i] == "\n" or text[i] in _SENTENCE_END:
        return True
    # numbered list item: "1. Parakeet"
    j = i
    while j >= 0 and text[j].isdigit():
        j -= 1
    return text[i] in ".)" and (j < 0 or text[j] == "\n")


def _name_kind(token: str) -> str | None:
    """ "name" for tokens that are names by shape, "capitalized" for plain Capitalized words."""
    if token.lower().endswith(".app"):
        return "name" if token[0].isupper() and len(token) > 5 else None
    if "." in token or not any(c.isupper() for c in token):
        return None
    if "-" in token:
        parts = token.split("-")
        # acronym (ASR), letters+digits (Qwen3) or internal capitals (ScreenASR) in some part
        shaped = any(
            (p.isupper() and len(p) >= 2)
            or (any(c.isdigit() for c in p) and p[0].isalpha())
            or any(c.isupper() for c in p[1:])
            for p in parts
        )
        return "name" if shaped else None
    if any(c.isdigit() for c in token):
        # M5, H100, Qwen3; lower-case ones (utf8) are identifiers
        return "name" if token[0].isupper() and token[1:].isalnum() else None
    if token[0].isupper() and token[1:].islower() and len(token) > 1:
        return "capitalized"
    return None  # acronyms and mixed case are identifiers


def _names(text: str) -> list[str]:
    """Product/proper names in prose, in first-seen order.

    Shape alone decides for ``Qwen3-ASR``, ``M5``, ``H100``, ``Terminal.app``, ``RTX 3060``.
    A plain Capitalized word counts when it is not a common English word and either appears
    mid-sentence, or appears at least twice and never in lower case.
    """
    for pattern in (_FENCE_RE, _BACKTICK_RE, _URL_RE, _PATH_RE):
        text = _mask(text, pattern)
    words_lower = {m.group() for m in re.finditer(r"\b[a-z][a-z]+\b", text)}
    titles = _title_lines(text)
    found: dict[str, None] = {}
    capitalized: dict[str, list[bool]] = {}  # word -> [mid-sentence?] per occurrence
    for m in _ACRONYM_NUMBER_RE.finditer(text):
        found[m.group()] = None
    for m in _NAME_TOKEN_RE.finditer(text):
        token = m.group()
        candidates: list[tuple[str, int]] = [(token, m.start())]
        if "-" in token and _name_kind(token) is None:
            candidates = []
            at = m.start()
            for part in token.split("-"):
                candidates.append((part, at))
                at += len(part) + 1
        for raw, start in candidates:
            tok = raw[:-1] if re.fullmatch(r"[A-Z]+\d+s", raw) else raw  # H100s
            kind = _name_kind(tok)
            if kind == "name":
                found[tok] = None
            elif kind == "capitalized" and tok.lower() not in COMMON_WORDS:
                line_start = text.rfind("\n", 0, start) + 1
                mid = line_start not in titles and not _sentence_start(text, start)
                capitalized.setdefault(tok, []).append(mid)
                found[tok] = None
    return [
        name
        for name in found
        if name not in capitalized
        or any(capitalized[name])
        or (len(capitalized[name]) >= 2 and name.lower() not in words_lower)
    ]


def _backtick_kind(span: str, *, prose: bool) -> TermKind | None:
    """Kind of a backticked span; multi-word spans are names or commands (prose only)."""
    if " " in span:
        if not prose:
            return None
        if _ACRONYM_NUMBER_RE.fullmatch(span):
            return TermKind.NAME
        return TermKind.COMMAND if len(span) <= 60 and _COMMAND_RE.fullmatch(span) else None
    checks = (
        (_FLAG_RE.fullmatch(span) is not None, TermKind.FLAG),
        ("/" in span or _PATH_RE.fullmatch(span) is not None, TermKind.PATH),
        (prose and _name_kind(span) == "name", TermKind.NAME),
        (_KEBAB_RE.match(span) is not None or is_interesting_identifier(span), TermKind.IDENTIFIER),
    )
    return next((kind for ok, kind in checks if ok), None)


def extract_terms(text: str, *, prose: bool = False) -> list[tuple[str, TermKind]]:
    """Identifiers, paths and flags in ``text``, in first-seen order, deduplicated.

    With ``prose=True`` (agent replies, selections of prose) kebab-case words are only taken
    from backticked spans, since ``well-known`` style words are ordinary English; instead
    names (see :func:`_names`) and backticked commands (``hark doctor``) are added.
    """
    found: dict[str, TermKind] = {}

    def add(term: str, kind: TermKind) -> None:
        term = term.strip(".,;:'\"()[]{}<>")
        if term and term not in found:
            found[term] = kind

    for m in _BACKTICK_RE.finditer(text):
        span = " ".join(m.group(1).split())
        if (kind := _backtick_kind(span, prose=prose)) is not None:
            add(span, kind)
    for m in _PATH_RE.finditer(text):
        add(m.group(), TermKind.PATH)
    for m in _FLAG_RE.finditer(text):
        if m.group().startswith("--") or len(m.group()) == 2:
            add(m.group(), TermKind.FLAG)
    for m in _IDENT_RE.finditer(text):
        tok = m.group()
        # file names such as ``types.py`` were captured as paths above
        # names (Terminal.app, Qwen3, H100s) are collected by _names below
        # shouted words (WHERE, WHICH) are not acronyms
        if prose and (_name_kind(tok) is not None or tok.lower() in COMMON_WORDS):
            continue
        if not _PATH_RE.fullmatch(tok) and is_interesting_identifier(tok):
            add(tok, TermKind.IDENTIFIER)
    if prose:
        for name in _names(text):
            add(name, TermKind.NAME)
    else:
        for k in _KEBAB_WORD_RE.finditer(text):
            add(k.group(), TermKind.IDENTIFIER)
    return list(found.items())
