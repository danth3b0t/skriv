"""Fidelity: does a rewrite say what the input said? One definition for cleanup and `hark eval`.

Cleanup may rewrite for clarity (drop fillers, fix casing and punctuation, smooth wording,
write spoken numbers as digits) but never add, drop or reverse a claim. `check` approximates
that mechanically. A rewrite fails when it

- drops everything that was not a filler;
- adds a line break where the text must stay on one line (terminal);
- loses a protected string (an identifier, path, flag or command): each must appear as often,
  spelled and cased the same (a new one is checked as content). Two ways out: a plain word
  merged into another protected string ("Super Whisper" -> "Superwhisper"), and a camelCase or
  snake_case identifier made of words written back as those words ("goBack" -> "go back"):
  canonicalization also turns plain speech into identifiers, and the rewrite is the better
  judge of prose;
- has a different number of negations ("not", "never", "n't", "without", ...);
- has different numbers, once spoken numbers are read as digits ("seven" == "7");
- adds or drops content words: words (by a crude stem) that are not function, discourse or
  filler words or spoken symbols, appear on one side only, and are not merely merged or split
  ("hyper land" -> "HyperLAN", "3:30 pm" -> "3:30pm"). At most ``len // CONTENT_PER_CHANGE``
  of the input's content words may be added, and as many dropped: short texts get none.

A spoken self-correction the rewrite resolved ("3 seconds, sorry, 5 seconds" -> "5 seconds")
is checked against the corrected text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator

_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
_WORD = re.compile(r"[\w']+")  # identifiers whole: "fetch_rows"
NEGATIONS = frozenset({
    "not", "no", "never", "nothing", "none", "nobody", "nowhere", "neither", "nor", "cannot",
    "without",
})  # fmt: skip
FILLERS = frozenset({
    "um", "umm", "uh", "uhh", "uhm", "er", "erm", "eh", "ah", "hmm", "mm", "mmm", "mhm",
})  # fmt: skip
# Self-correction cues: "<retracted words>, no wait, <correction>". corrections.py resolves the
# restarts before cleanup; the rewrite may resolve the rest (see check).
CORRECTION_CUES = (
    ("no", "wait"), ("wait", "no"), ("no", "sorry"), ("sorry", "no"), ("i", "mean"),
    ("scratch", "that"), ("actually", "no"), ("no", "actually"), ("or", "rather"),
    ("make", "that"),
)  # fmt: skip
BARE_CUES = (("no",), ("sorry",), ("actually",))
MAX_RETRACTED_WORDS = 6  # not counting fillers
# May go as fillers or be added as connectives by a rewrite: not counted as content.
DISCOURSE = frozenset({
    "like", "basically", "literally", "actually", "just", "really", "so", "well", "okay", "ok",
    "yeah", "right", "now", "then", "also", "please", "kind", "sort", "know", "mean", "anyway",
    "quite", "very", "pretty", "oh", "hey", "hi",
})  # fmt: skip
# Spoken symbols a rewrite may write as the symbol ("dash n" -> "-n").
SYMBOL_WORDS = frozenset({
    "dash", "slash", "underscore", "dot", "comma", "colon", "period", "point", "hyphen", "tilde",
})  # fmt: skip
# Function words: a rewrite may add or drop them ("gonna" -> "going to", "it's" -> "it is").
# Not here because they carry meaning: up/down/out/off/over, all/some/any/each, should/must.
FUNCTION = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at", "for", "with",
    "from", "by", "as", "into", "onto", "about", "this", "that", "these", "those", "it",
    "its", "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his", "she", "her",
    "they", "them", "their", "there", "here", "is", "are", "was", "were", "be", "been",
    "being", "am", "do", "does", "did", "have", "has", "had", "will", "would", "can", "could",
    "what", "which", "who", "how", "when", "where", "why", "one", "ones", "let", "going",
    "gonna", "wanna", "gotta", "want", "got", "get", "go", "thing", "things", "something",
    "too", "than", "because", "cause",
})  # fmt: skip
CONTENT_PER_CHANGE = 8  # one content word may be added (or dropped) per this many in the input
_UNITS = {w: i for i, w in enumerate((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
))}  # fmt: skip
_TENS = {w: 10 * i for i, w in enumerate(
    ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"), 2
)}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9}
_DIGITS = re.compile(r"\d+(?:\.\d+)*")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def tokens(text: str) -> list[str]:
    """Lowercased words: letters and digits, with an inner apostrophe ("don't")."""
    return [m.group().lower().replace("\u2019", "'") for m in _TOKEN.finditer(text)]


def _negation(token: str) -> bool:
    return token in NEGATIONS or token.endswith("n't")


def negations(text: str) -> int:
    """Negation words; a stutter ("no no") counts once."""
    words = tokens(text)
    return sum(_negation(t) and (i == 0 or words[i - 1] != t) for i, t in enumerate(words))


def missing(strings: Iterable[str], text: str) -> list[str]:
    """The strings that do not occur in `text` (exact, case-sensitive)."""
    return [s for s in strings if s not in text]


def numbers(text: str) -> list[str]:
    """The numbers in `text`, sorted, with runs of spoken number words read as digits
    ("twenty three" -> "23", "a hundred" -> "100", "one two" -> "1", "2")."""
    out = _DIGITS.findall(_THOUSANDS.sub("", text))
    total = current = 0
    last = prev = ""  # last: "" | unit | teen | ten | hundred | scale

    def flush() -> None:
        nonlocal total, current, last
        if last:
            out.append(str(total + current))
        total = current = 0
        last = ""

    for t in tokens(text):
        if t in _UNITS:
            v = _UNITS[t]
            if last in ("unit", "teen") or (last == "ten" and v >= 10) or (last and v == 0):
                flush()
            current += v
            last = "teen" if v >= 10 else "unit"
        elif t in _TENS:
            if last in ("unit", "teen", "ten"):
                flush()
            current += _TENS[t]
            last = "ten"
        elif t == "hundred" and (
            (last in ("unit", "teen", "ten") and current < 100) or prev == "a"
        ):
            current = (current or 1) * 100
            last = "hundred"
        elif t in _SCALES and (last in ("unit", "teen", "ten", "hundred") or prev == "a"):
            total += (current or 1) * _SCALES[t]
            current = 0
            last = "scale"
        elif t == "and" and last == "hundred":
            continue  # "three hundred and five"
        else:
            flush()
        prev = t
    flush()
    return sorted(out)


def _stem(token: str) -> str:
    t = token.split("'", maxsplit=1)[0]
    for suffix in ("ing", "ed", "es", "s", "ly"):
        if len(t) > len(suffix) + 2 and t.endswith(suffix):
            t = t[: -len(suffix)]
            break
    return t.removesuffix("e")  # "ride" == "riding"


def content(text: str) -> set[str]:
    """Stems of the words that carry content: not function, discourse, filler or symbol
    words, negations or numbers (those are checked on their own)."""
    return {
        _stem(t)
        for t in tokens(text)
        if not (
            t in FUNCTION
            or t in DISCOURSE
            or t in FILLERS
            or t in SYMBOL_WORDS
            or _negation(t)
            or t in _UNITS
            or t in _TENS
            or t in _SCALES
            or t[0].isdigit()
        )
    }


_HUMP = re.compile(r"(?<=[a-z])(?=[A-Z])|_")
_WORDY = re.compile(r"[A-Za-z]+(?:_[A-Za-z]+)+|[a-z]+(?:[A-Z][a-z]+)+|(?:[A-Z][a-z]+){2,}")


def _spoken(identifier: str) -> str | None:
    """ "goBack" -> "go back", "each_other" -> "each other"; None if `identifier` is not a
    camelCase, PascalCase or snake_case run of letters."""
    if not _WORDY.fullmatch(identifier):
        return None
    return " ".join(_HUMP.split(identifier)).lower()


def count(s: str, text: str, flags: re.RegexFlag = re.NOFLAG) -> int:
    """Occurrences of `s` in `text` as a whole word (not inside a longer word)."""
    return len(re.findall(r"(?<!\w)" + re.escape(s) + r"(?!\w)", text, flags))


def _respaced(words: list[str], other: list[str]) -> set[str]:
    """The stems of `words` that the rewrite only merged with or split from their neighbours
    ("hyper land" <-> "HyperLAN", "3:30 pm" <-> "3:30pm"): two or three adjacent words that
    are one word on the other side, or one word that is two or three adjacent ones there."""

    def runs(ws: list[str]) -> dict[str, list[str]]:
        return {
            "".join(ws[i : i + n]): ws[i : i + n] for n in (2, 3) for i in range(len(ws) - n + 1)
        }

    mine, theirs = runs(words), set(other)
    out = {w for joined, run in mine.items() if joined in theirs for w in run}
    out |= set(words) & set(runs(other))
    return {_stem(w) for w in out}


def _retractions(text: str) -> Iterator[str]:
    """`text` without the words a spoken correction would retract: 1..MAX_RETRACTED_WORDS
    words right before a cue that punctuation sets off from them ("3 seconds, sorry 5
    seconds" -> "5 seconds"), no sentence end among them and no "?" or "!" at the cue."""
    toks = list(_WORD.finditer(text))
    words = [t.group().lower() for t in toks]
    for i in range(1, len(words)):
        for cue in (*CORRECTION_CUES, *BARE_CUES):
            c = i + len(cue)  # the correction's first word, after fillers
            if tuple(words[i:c]) != cue:
                continue
            while c < len(words) and words[c] in FILLERS:
                c += 1
            if c == len(words):
                continue
            kept = [k for k in range(i) if words[k] not in FILLERS]  # before the cue
            if not kept:
                continue
            gap = text[toks[kept[-1]].end() : toks[c].start()]
            if not re.match(r"\s*[,.;:]", gap) or re.search(r"[?!]", gap):
                continue
            for k in kept[::-1][:MAX_RETRACTED_WORDS]:
                start = toks[k].start()
                if re.search(r"[.?!]\s", text[start : toks[kept[-1]].end()]):
                    break
                yield text[:start] + text[toks[c].start() :]


def negations_kept(said: str, text: str) -> bool:
    """`text` has no fewer negations than `said`, or than `said` with a spoken
    self-correction resolved ("10 MB, no, 20 MB" -> "20 MB"). For `hark eval`."""
    have = negations(text)
    return have >= negations(said) or any(have >= negations(m) for m in _retractions(said))


def check(before: str, after: str, protected: Collection[str] = (), *, single_line: bool) -> str:
    """Why `after` does not say what `before` said, or "" if it does (see the module doc).
    `protected`: strings that must keep their exact spelling, case and count.

    A spoken self-correction the rewrite resolved ("port 8080, sorry, I mean port 8081" ->
    "port 8081") passes if the rest of the rewrite passes against the corrected text."""
    why = _check(before, after, protected, single_line=single_line)
    if why and any(  # the cheap check first: most texts have no cue
        not _check(meant, after, protected, single_line=single_line)
        for meant in _retractions(before)
    ):
        return ""
    return why


def _check(  # noqa: PLR0911 - one return per rule
    before: str, after: str, protected: Collection[str], *, single_line: bool
) -> str:
    if not tokens(after) and any(t not in FILLERS for t in tokens(before)):
        return "dropped everything"
    if single_line and "\n" in after.strip() and "\n" not in before:
        return "added a line break"
    gained = [q for q in protected if count(q, after) > count(q, before)]
    for p in sorted(protected):
        lost = count(p, before) - count(p, after)
        spoken = _spoken(p)
        if lost > 0 and not (
            (  # written back as words: "goBack" -> "go back"
                spoken
                and count(spoken, after, re.IGNORECASE) - count(spoken, before, re.IGNORECASE)
                == lost
            )  # merged into another protected string: "Super Whisper" -> "Superwhisper"
            or (p.isalnum() and any(p.lower() in q.lower() for q in gained))
        ):
            return f"changed {p!r}"
    if negations(before) != negations(after):
        return f"negations {negations(before)} -> {negations(after)}"
    if numbers(before) != numbers(after):
        return f"numbers {' '.join(numbers(before))} -> {' '.join(numbers(after))}"
    old, new = content(before), content(after)
    words_before, words_after = tokens(before), tokens(after)
    added = sorted(new - old - _respaced(words_after, words_before))
    dropped = sorted(old - new - _respaced(words_before, words_after))
    limit = len(old) // CONTENT_PER_CHANGE
    if len(added) > limit:
        return f"added {', '.join(added)}"
    if len(dropped) > limit:
        return f"dropped {', '.join(dropped)}"
    return ""
