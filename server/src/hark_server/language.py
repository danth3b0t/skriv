"""Languages: BCP-47 matching and whether a transcript is plausible for the languages a
client allowed (SessionStart.languages). A multilingual model with automatic detection
(Parakeet-TDT-0.6B-v3) sometimes writes another language or script for short or accented
speech ("Hi" -> "Hej", "Hey" -> "Гри", "sí"); the session then re-decodes with an
English-only model if English is allowed (session.Session, language fallback).

Two checks, deliberately narrow (a false alarm costs one extra decode, not the text):
- script: a letter in a script none of the allowed languages is written in (Cyrillic or
  Greek when only Latin-script languages are allowed). Languages missing from SCRIPTS are
  not judged.
- short: an utterance of at most SHORT_WORDS words with a word that is a common one-word
  reply in a language that is not allowed, and none that is one in an allowed language
  (SHORT_REPLIES). Longer text is left alone: there a stray foreign word is more likely a
  name than a misdetection.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_LATIN = frozenset({"LATIN"})
# Script(s) of each language's standard orthography (Unicode character-name prefixes).
SCRIPTS: dict[str, frozenset[str]] = {
    **dict.fromkeys(
        ("en", "cs", "sk", "pl", "de", "fr", "es", "it", "pt", "nl", "da", "sv", "no", "nb", "nn",
         "fi", "et", "lv", "lt", "hu", "ro", "hr", "sl", "bs", "mt", "ga", "is", "ca", "eu", "gl",
         "tr", "sq", "cy", "lb", "af", "id", "ms", "vi"),
        _LATIN,
    ),
    **dict.fromkeys(("bg", "ru", "uk", "be", "mk"), frozenset({"CYRILLIC"})),
    "sr": frozenset({"CYRILLIC", "LATIN"}),
    "el": frozenset({"GREEK"}),
}  # fmt: skip
# Scripts the check can name; letters of other scripts (modifier letters...) are ignored.
_JUDGED = frozenset((
    "LATIN", "CYRILLIC", "GREEK", "ARABIC", "HEBREW", "ARMENIAN", "GEORGIAN", "DEVANAGARI",
    "THAI", "HANGUL", "HIRAGANA", "KATAKANA", "CJK",
))  # fmt: skip

SHORT_WORDS = 3
# Common whole-utterance replies and greetings, lower case. A word listed for an allowed
# language is never foreign. Czech "hej" is left out on purpose: dictated on its own it is
# far more often a misheard English "hey"/"hi" than Czech.
SHORT_REPLIES: dict[str, frozenset[str]] = {
    lang: frozenset(words.split())
    for lang, words in {
        "en": "hi hey hello yes yeah yep no nope okay ok thanks thank you bye sure right so oh ah "
        "well please sorry what why how wow cool great good fine done stop hmm uh um",
        "cs": "ahoj ano ne jo díky děkuji dobře prosím jasně čau nazdar tak no",
        "sk": "ahoj áno nie hej ďakujem dobre prosím jasné čau",
        "pl": "tak nie cześć dzięki dziękuję hej dobrze proszę",
        "de": "ja nein hallo danke genau tschüss bitte gut doch servus",
        "nl": "ja nee hoi hallo dank dankjewel goed doei",
        "sv": "ja nej hej tack hejsan okej bra",
        "da": "ja nej hej tak godt",
        "no": "ja nei hei takk",
        "nb": "ja nei hei takk",
        "fi": "kyllä ei moi hei kiitos joo",
        "et": "jah ei tere aitäh",
        "es": "sí hola vale gracias bueno adiós claro",
        "it": "sì ciao grazie prego bene",
        "pt": "sim não olá obrigado obrigada tchau",
        "fr": "oui non salut merci bonjour bon",
        "ro": "da nu salut mersi bine",
        "hu": "igen nem szia köszi köszönöm jó",
        "hr": "da ne bok hvala",
        "sl": "da ne živjo hvala",
        "lv": "jā nē sveiki paldies labi",
        "lt": "taip ne labas ačiū gerai",
        "mt": "iva le grazzi",
    }.items()
}
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


def base(tag: str) -> str:
    """Primary language subtag, lower case: "en-US" -> "en"."""
    return tag.split("-", 1)[0].strip().lower()


def bases(tags: Iterable[str]) -> frozenset[str]:
    return frozenset(base(t) for t in tags if t.strip())


def _scripts(text: str) -> set[str]:
    out: set[str] = set()
    for ch in text:
        if ch.isalpha():
            script = unicodedata.name(ch, "").split(" ", 1)[0]
            if script in _JUDGED:
                out.add(script)
    return out


def implausible(text: str, allowed: Iterable[str]) -> str:
    """Why `text` cannot (script) or very probably does not (short) belong to one of the
    `allowed` languages: "script:<SCRIPT>", "short:<lang>", or "" (plausible, or nothing
    to judge: no allowed languages)."""
    langs = bases(allowed)
    if not langs:
        return ""
    if all(lang in SCRIPTS for lang in langs):
        ok = frozenset[str]().union(*(SCRIPTS[lang] for lang in langs))
        foreign = sorted(_scripts(text) - ok)
        if foreign:
            return f"script:{foreign[0]}"
    words = [m.group().lower() for m in _WORD.finditer(text)]
    if not 0 < len(words) <= SHORT_WORDS:
        return ""
    if any(w in SHORT_REPLIES.get(lang, ()) for w in words for lang in langs):
        return ""
    for w in words:
        for lang, replies in SHORT_REPLIES.items():
            if lang not in langs and w in replies:
                return f"short:{lang}"
    return ""
