"""Fidelity: does a rewrite say what the input said? One definition for cleanup and `skriv eval`.

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
- has fewer question marks: a question became a statement;
- adds an identifier (camelCase, PascalCase, snake_case, dotted), a path or a flag that is
  neither in the input (in any case) nor protected (nor part of a protected string), unless it
  is adjacent input words run together as written ("Hyper LAN" -> "HyperLAN") or with their
  spoken separators ("model dot train" -> "model.train"): "read wave" -> "ReadWave" invents a
  name;
- drops a sentence that has content words;
- drops the first word of a sentence that is a content word not set off by a comma: the
  verb of an imperative ("Use useQuery here" -> "useQuery here");
- adds or drops content words: words (by a crude stem) that are not function, discourse or
  filler words or spoken symbols, appear on one side only, and are not merely merged or split
  ("hyper land" -> "HyperLAN", "3:30 pm" -> "3:30pm"). At most ``len // CONTENT_PER_CHANGE``
  of the input's content words may be added, and as many dropped: short texts get none.

A spoken self-correction the rewrite resolved ("3 seconds, sorry, 5 seconds" -> "5 seconds")
is checked against the corrected text.

A shortlist term the rewrite wrote in place of words that sound like it ("Indo we.conf" ->
"tauri.conf.json", "XDG runtime di" -> "XDG_RUNTIME_DIR") is a repair, not an invention: the
words it replaced and the term itself are left out of the protected-string, content-word and
opening-verb rules (see `_unbacked`). Negations, numbers and question marks still count.

`accept` applies the check sentence by sentence when the whole rewrite fails it, so one bad
change in a long dictation does not cost the other sentences their cleanup.
"""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence

_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
_WORD = re.compile(r"[\w']+")  # identifiers whole: "fetch_rows"
NEGATIONS = frozenset({
    "not", "no", "never", "nothing", "none", "nobody", "nowhere", "neither", "nor", "cannot",
    "without",
})  # fmt: skip
FILLERS = frozenset({
    "um", "umm", "uh", "uhh", "uhm", "er", "erm", "eh", "ah", "hmm", "mm", "mmm", "mhm",
})  # fmt: skip
# Self-correction cues: "<retracted words>, no wait, <correction>". The rewrite resolves them
# (see check).
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
            t.split("'", maxsplit=1)[0] in FUNCTION  # "it's", "let's"
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
    self-correction resolved ("10 MB, no, 20 MB" -> "20 MB"). For `skriv eval`."""
    have = negations(text)
    return have >= negations(said) or any(have >= negations(m) for m in _retractions(said))


_SPLIT_JOIN = ((1, 1), (1, 0), (0, 1), (2, 1), (1, 2), (3, 1), (1, 3))


def _align(said: list[str], wrote: list[str]) -> list[tuple[list[str], list[str]]]:
    """Pair the input's sentences with the rewrite's: one to one, joined (up to three into
    one), split (one into up to three), dropped or added, keeping the order. The pairing
    with the fewest words on one side only wins; a join or a split costs one word more."""
    bags = [[Counter(tokens(s)) for s in side] for side in (said, wrote)]
    inf = float("inf")
    cost = [[inf] * (len(wrote) + 1) for _ in range(len(said) + 1)]
    step: dict[tuple[int, int], tuple[int, int]] = {}
    cost[0][0] = 0
    for i in range(len(said) + 1):
        for j in range(len(wrote) + 1):
            if cost[i][j] == inf:
                continue
            for a, b in _SPLIT_JOIN:
                if i + a > len(said) or j + b > len(wrote):
                    continue
                left = sum(bags[0][i : i + a], Counter[str]())
                right = sum(bags[1][j : j + b], Counter[str]())
                c = cost[i][j] + (left - right).total() + (right - left).total()
                c += a + b > 2  # a join or split
                if c < cost[i + a][j + b]:
                    cost[i + a][j + b] = c
                    step[i + a, j + b] = (a, b)
    groups: list[tuple[list[str], list[str]]] = []
    i, j = len(said), len(wrote)
    while i or j:
        a, b = step[i, j]
        groups.append((said[i - a : i], wrote[j - b : j]))
        i, j = i - a, j - b
    return groups[::-1]


def accept(
    before: str,
    after: str,
    protected: Collection[str] = (),
    *,
    single_line: bool,
    terms: Mapping[str, Sequence[str]] | None = None,
) -> tuple[str, str]:
    """What to keep of the rewrite `after`, and why the rest was refused ("" if nothing
    was). The whole rewrite if `check` passes; else, sentence by sentence (see `_align`), each
    rewritten sentence that passes `check` against the sentences it came from, and the input's
    sentences where it does not. `before` if no sentence passes, or if the mix fails `check`.
    `terms`: shortlist terms the rewrite wrote (canonical -> spoken forms), see `check`."""
    why = check(before, after, protected, single_line=single_line, terms=terms)
    if not why:
        return after, ""
    said, wrote = sentences(before), sentences(after)
    if len(said) < 2 and len(wrote) < 2:
        return before, why
    pieces: list[str] = []
    refused: list[str] = []
    groups = _align(said, wrote)
    for old, new in groups:
        b, a = "".join(old), "".join(new)
        bad = (
            "added a sentence"
            if not b.strip()
            else check(b.strip(), a.strip(), protected, single_line=single_line, terms=terms)
        )
        if bad:
            refused.append(bad)
        piece = b if bad else a
        if pieces and piece and not pieces[-1][-1:].isspace():
            pieces[-1] += " "
        if piece:
            pieces.append(piece)
    if len(refused) == len(groups):
        return before, why
    mixed = "".join(pieces).rstrip() + after[len(after.rstrip()) :]
    if check(before, mixed, protected, single_line=single_line, terms=terms):
        return before, why
    return mixed, f"kept {len(refused)} of {len(groups)} sentences as said: {refused[0]}"


def check(
    before: str,
    after: str,
    protected: Collection[str] = (),
    *,
    single_line: bool,
    terms: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Why `after` does not say what `before` said, or "" if it does (see the module doc).
    `protected`: strings that must keep their exact spelling, case and count. `terms`:
    shortlist terms (canonical -> spoken forms) that may replace words that sound like them.

    A spoken self-correction the rewrite resolved ("port 8080, sorry, I mean port 8081" ->
    "port 8081") passes if the rest of the rewrite passes against the corrected text."""
    terms = terms or {}
    why = _check(before, after, protected, single_line=single_line, terms=terms)
    if why and any(  # the cheap check first: most texts have no cue
        not _check(meant, after, protected, single_line=single_line, terms=terms)
        for meant in _retractions(before)
    ):
        return ""
    return why


_IDENTIFIER = re.compile(
    r"(?<![\w.])(?=[\w.]*(?:[a-z][A-Z]|[^\W_]_[^\W_]|[^\W\d_]\.[^\W\d_]))[\w.]*\w"
    r"|(?<!\S)[~$.]?[\w.-]*/[\w./-]*\w"  # a path: "web/src", "~/bin/x"
    r"|(?<![\w-])--?[A-Za-z][\w-]*\w"  # a flag
)
_SPOKEN_SEPARATORS = {"underscore": "_", "dot": ".", "point": ".", "slash": "/", "dash": "-"}


def _invented(before: str, after: str, protected: Collection[str]) -> list[str]:
    """The identifiers of `after` that `before` neither has nor says (see the module doc)."""
    words: list[str] = re.findall(r"[\w'.]*\w", before)
    joined: set[str] = set()  # as written: "Hyper LAN" -> "HyperLAN"
    spelled: set[str] = set()  # lowercased: "model dot train" -> "model.train"
    for i in range(len(words)):
        for j in range(i + 2, min(len(words), i + 7) + 1):
            run = [_SPOKEN_SEPARATORS.get(w.lower(), w) for w in words[i:j]]
            if any(w in _SPOKEN_SEPARATORS.values() for w in run):
                spelled.add("".join(run).lower())
            else:
                joined.add("".join(run))
    return sorted({
        m.group()
        for m in _IDENTIFIER.finditer(after)
        if not count(m.group(), before, re.IGNORECASE)  # re-cased: "UVSync" -> "uvSync"
        and not any(m.group() in p for p in protected)  # "XDG_DIR" of "$XDG_DIR"
        and m.group() not in joined
        and m.group().lower() not in spelled
    })  # fmt: skip


_SENTENCE = re.compile(r".*?(?:[.?!]+[\"'\u201d)\]]*(?:\s+|$)|$)", re.DOTALL)


def sentences(text: str) -> list[str]:
    """`text` cut after each sentence end (".", "?", "!" before a space); every piece keeps
    its trailing whitespace, so the pieces join back to `text`."""
    return [m.group() for m in _SENTENCE.finditer(text) if m.group()]


def _initial_verbs(text: str) -> list[str]:
    """Stems of the content words that open a sentence, unless a comma sets them off
    ("Use useQuery" -> "us"; not "Honestly, ..."). Fillers and discourse words are skipped; a
    restart ("After after I've done after ...", "m men") and a lone word ("etc.") count for
    nothing."""
    out: list[str] = []
    for s in sentences(text):
        words = list(_TOKEN.finditer(s))
        for k, m in enumerate(words):
            t = m.group().lower()
            if t in FILLERS or t in DISCOURSE:
                continue
            following = [w.group().lower() for w in words[k + 1 : k + 5]]
            restart = t in following or (
                len(t) <= 2 and bool(following) and following[0].startswith(t)
            )
            if following and not restart and not s[m.end() :].lstrip().startswith(","):
                out.extend(content(t))
            break
    return out


_SQUASH = re.compile(r"[^a-z0-9]")
SOUNDS_LIKE = 0.4  # letter similarity of the replaced words to a term or its spoken form
MAX_EXTRA_WORDS = 2  # a term may replace this many more words than it has spoken


def _squash(text: str) -> str:
    return _SQUASH.sub("", text.lower())


def _unbacked(before: str, after: str, terms: Mapping[str, Sequence[str]]) -> tuple[str, str]:
    """`before` and `after` without the spans where `after` writes a shortlist term (more
    often than `before` has it) in place of words of `before` that sound like it: at least one
    replaced word, no more than the term's longest spoken form plus MAX_EXTRA_WORDS, none of
    them a shortlist term (one term never replaces another), not just some of the term's own
    words (that adds the rest), and letters at least SOUNDS_LIKE similar (difflib ratio) to the
    term or one of its spoken forms."""
    if not terms:
        return before, after
    b_toks, a_toks = list(_TOKEN.finditer(before)), list(_TOKEN.finditer(after))
    ops = SequenceMatcher(
        None,
        [t.group().lower() for t in b_toks],
        [t.group().lower() for t in a_toks],
        autojunk=False,
    ).get_opcodes()

    def before_index(j: int, *, end: bool) -> int:
        """The token of `before` aligned with token j of `after` (end: one past it)."""
        for tag, i1, i2, j1, j2 in ops:
            if j1 <= j < j2:
                if tag == "equal":
                    return i1 + (j - j1) + end
                return i2 if end else i1
        return len(b_toks)

    cut_b: list[tuple[int, int]] = []
    cut_a: list[tuple[int, int]] = []
    for term, spoken in terms.items():
        if count(term, after) <= count(term, before):
            continue
        forms = [term, *spoken]
        longest = max(len(tokens(f)) for f in forms)
        for m in re.finditer(r"(?<!\w)" + re.escape(term) + r"(?!\w)", after):
            js = [j for j, t in enumerate(a_toks) if m.start() <= t.start() < m.end()]
            if not js:
                continue
            i1, i2 = before_index(js[0], end=False), before_index(js[-1], end=True)
            if not 0 < i2 - i1 <= longest + MAX_EXTRA_WORDS:
                continue
            said = before[b_toks[i1].start() : b_toks[i2 - 1].end()]
            if any(count(t, said) for t in terms):
                continue
            if set(tokens(said)) < set(tokens(term)):  # "gate" -> "gate.py" adds ".py"
                continue
            if max(SequenceMatcher(None, _squash(said), _squash(f)).ratio() for f in forms) < (
                SOUNDS_LIKE
            ):
                continue
            cut_b.append((b_toks[i1].start(), b_toks[i2 - 1].end()))
            cut_a.append((m.start(), m.end()))

    def cut(text: str, spans: list[tuple[int, int]]) -> str:
        for start, end in sorted(spans, reverse=True):
            text = text[:start] + " " + text[end:]
        return text

    return cut(before, cut_b), cut(after, cut_a)


def _dropped_sentence(before: str, after: str) -> bool:
    """A sentence of `before` with content words that has no counterpart in `after`."""
    said, wrote = sentences(before), sentences(after)
    if len(said) < 2:
        return False
    return any(old and not new and content("".join(old)) for old, new in _align(said, wrote))


def _check(  # noqa: PLR0911 - one return per rule
    before: str,
    after: str,
    protected: Collection[str],
    *,
    single_line: bool,
    terms: Mapping[str, Sequence[str]],
) -> str:
    if not tokens(after) and any(t not in FILLERS for t in tokens(before)):
        return "dropped everything"
    if single_line and "\n" in after.strip() and "\n" not in before:
        return "added a line break"
    if negations(before) != negations(after):
        return f"negations {negations(before)} -> {negations(after)}"
    if numbers(before) != numbers(after):
        return f"numbers {' '.join(numbers(before))} -> {' '.join(numbers(after))}"
    if after.count("?") < before.count("?"):
        return "dropped a question mark"
    if invented := _invented(before, after, protected):
        return f"added {', '.join(invented)}"
    if _dropped_sentence(before, after):
        return "dropped a sentence"
    before, after = _unbacked(before, after, terms)  # the rest: what was not a term repair
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
    old, new = content(before), content(after)
    words_before, words_after = tokens(before), tokens(after)
    respaced = _respaced(words_before, words_after)
    added = sorted(new - old - _respaced(words_after, words_before))
    dropped = sorted(old - new - respaced)
    stems_before = Counter(map(_stem, words_before))
    stems_after = Counter(map(_stem, words_after))
    if verbs := sorted({
        v for v in _initial_verbs(before)
        if stems_after[v] < stems_before[v] and v not in respaced
    }):  # fmt: skip
        return f"dropped {', '.join(verbs)}"
    limit = len(old) // CONTENT_PER_CHANGE
    if len(added) > limit:
        return f"added {', '.join(added)}"
    if len(dropped) > limit:
        return f"dropped {', '.join(dropped)}"
    return ""
