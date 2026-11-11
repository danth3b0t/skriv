"""Correction learning from the user's edits right after a hark paste (opt-in, local only).

Privacy scope (ADR-002 "X"):

- Off unless ``[learn] enabled = true``.
- Watches only the paste target: the same window (``focus_token()``) for at most ``window_ms``
  (clamped to 30 s) after the paste; a new dictation, a focus change, a secure/unreadable field
  or the pasted span disappearing (message sent) ends the watch.
- Terminals have no readable field: there the keys typed after the paste (the daemon's hotkey
  listener, shared with the smart-continuation shadow buffer) are replayed on the pasted text
  (``hark_client.keyedits``) until Enter, a key reaching another window, a new dictation or
  ``window_ms``.
- Field text and key events live in memory during the watch only. What is persisted is the derived
  ``heard -> written`` pair (<= 3 words a side, secrets scrubbed) and a count, in
  ``<history.dir>/learned.json`` (0600). ``hark learned list|forget|clear`` inspects it.
- A pair becomes a shortlist term (``TermSource.LEARNED``, the heard words as its spoken alias)
  only after ``promote_after`` (>= 2) occurrences. Learned terms bias decoding through
  SessionStart like any context term and feed canonicalization (whose own guards still apply).

What counts as a correction: a run of up to 3 words replaced by up to 3 words that look or sound
alike (``cube control`` -> ``kubectl``, ``Kerry`` -> ``Carrie``), or a re-casing that adds inner
capitals (``github`` -> ``GitHub``). Appends, deletions, style rewrites (``big`` -> ``large``),
negation changes, corrections into ordinary English words, and edits that change more than 3
runs or half the words are ignored.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, cast

from hark_client import keyedits
from hark_client.context import Term, TermKind, TermSource
from hark_client.context.aliases import COMMON_WORDS, spoken_forms
from hark_client.context.engine import scrub
from hark_client.continuation import PASTE_ECHO_S

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from hark_client.context import FocusInfo
    from hark_client.continuation import KeyPress
    from hark_client.fields import FieldReader

log = logging.getLogger("hark.learn")

MAX_WINDOW_S = 30.0
MAX_WORDS = 3
MAX_RUNS = 3
MAX_CHARS = 48
MAX_ENTRIES = 1000
STORE_VERSION = 1

_EDGE = "\"'()[]{}<>.,;:!?\u201c\u201d\u2018\u2019*"
_TOKEN_RE = re.compile(r"[^\W_]+")
_NEGATIONS = frozenset({"not", "no", "never", "cannot", "nothing", "none", "nor"})


def _strip(word: str) -> str:
    return word.strip(_EDGE)


def letters(s: str) -> str:
    return "".join(_TOKEN_RE.findall(s.lower()))


_PHONETIC = (("ph", "f"), ("ck", "k"), ("c", "k"), ("q", "k"), ("x", "ks"), ("z", "s"))


def phonetic_key(s: str) -> str:
    """Crude sound key: c/k/q merge, vowels after the first letter dropped, repeats collapsed."""
    s = letters(s)
    for a, b in _PHONETIC:
        s = s.replace(a, b)
    if not s:
        return ""
    key = s[0] + "".join(c for c in s[1:] if c not in "aeiouy")
    return re.sub(r"(.)\1+", r"\1", key)


def _alike(heard: str, written: str) -> bool:
    spelled = SequenceMatcher(None, letters(heard), letters(written), autojunk=False).ratio()
    sound = SequenceMatcher(
        None, phonetic_key(heard), phonetic_key(written), autojunk=False
    ).ratio()
    return spelled >= 0.5 or sound >= 0.75


def _inner_capitals(word: str) -> bool:
    return any(c.isupper() for c in word[1:])


def _is_vocabulary(written: str) -> bool:
    """Worth learning: some token is not ordinary English, or it has inner capitals/digits/symbols."""
    words = written.split()
    return any(
        w.lower() not in COMMON_WORDS or _inner_capitals(w) or not w.isalpha() for w in words
    )


def _pair_ok(heard: str, written: str) -> bool:
    if not heard or not written or heard == written:
        return False
    if len(heard) > MAX_CHARS or len(written) > MAX_CHARS:
        return False
    if not any(c.isalpha() for c in written) or scrub(written) != written:
        return False
    words = {m.group().lower() for m in _TOKEN_RE.finditer(f"{heard} {written}")}
    if words & _NEGATIONS or "n't" in f"{heard} {written}".lower():
        return False
    return _is_vocabulary(written)


def corrections(pasted: str, edited: str) -> list[tuple[str, str]]:
    """(heard, written) pairs from what was pasted and what the user turned it into."""
    a = [_strip(w) for w in pasted.split()]
    b = [_strip(w) for w in edited.split()]
    if not a or not b:
        return []
    sm = SequenceMatcher(None, [w.casefold() for w in a], [w.casefold() for w in b], autojunk=False)
    runs = [op for op in sm.get_opcodes() if op[0] != "equal"]
    replaced = sum(i2 - i1 for tag, i1, i2, _, _ in runs if tag != "insert")
    if len([r for r in runs if r[0] == "replace"]) > MAX_RUNS or replaced * 2 > len(a):
        return []
    pairs: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs.extend(
                (x, y)
                for x, y in zip(a[i1:i2], b[j1:j2], strict=True)
                if x != y and _inner_capitals(y) and _pair_ok(x, y)
            )
        elif tag == "replace" and i2 - i1 <= MAX_WORDS and j2 - j1 <= MAX_WORDS:
            heard, written = " ".join(a[i1:i2]), " ".join(b[j1:j2])
            if _alike(heard, written) and _pair_ok(heard, written):
                pairs.append((heard, written))
    return pairs[:MAX_RUNS]


@dataclass(frozen=True, slots=True)
class Anchor:
    """The pasted span inside the field right after the paste: text before and after it."""

    prefix: str
    suffix: str

    @classmethod
    def find(cls, field_value: str, pasted: str) -> Anchor | None:
        needle = pasted.strip()
        if not needle or field_value.count(needle) != 1:
            return None  # absent or ambiguous
        i = field_value.index(needle)
        return cls(field_value[:i], field_value[i + len(needle) :])

    def span(self, field_value: str) -> str | None:
        """What the pasted span became; None if the text around it changed or it was deleted."""
        p, s = len(self.prefix), len(self.suffix)
        if len(field_value) < p + s:
            return None
        if not (field_value.startswith(self.prefix) and field_value.endswith(self.suffix)):
            return None
        span = field_value[p : len(field_value) - s]
        return span if span.strip() else None


# -- store -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Entry:
    heard: str
    written: str
    count: int
    first: str  # UTC timestamps, second resolution
    last: str


def store_path(history_dir: Path) -> Path:
    return history_dir / "learned.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _kind(written: str) -> TermKind:
    if any(not (c.isalpha() or c.isspace()) for c in written):
        return TermKind.IDENTIFIER
    return TermKind.NAME if any(c.isupper() for c in written) else TermKind.WORD


class LearnedStore:
    """``learned.json``: {"version": 1, "entries": [Entry...]}. Thread-safe, rewritten atomically."""

    def __init__(self, path: Path, promote_after: int = 2, max_terms: int = 20) -> None:
        self.path = path
        self.promote_after = max(2, promote_after)
        self.max_terms = max_terms
        self._lock = threading.Lock()
        self._cache: tuple[float, list[Entry]] | None = None

    def entries(self) -> list[Entry]:
        with self._lock:
            return list(self._load())

    def _load(self) -> list[Entry]:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return []
        if self._cache is not None and self._cache[0] == mtime:
            return self._cache[1]
        try:
            data = cast("object", json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable %s: %s", self.path, e)
            return []
        entries: list[Entry] = []
        raw = cast("dict[str, object]", data).get("entries") if isinstance(data, dict) else None
        for item in cast("list[object]", raw) if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            d = cast("dict[str, object]", item)
            heard, written, count = d.get("heard"), d.get("written"), d.get("count")
            first, last = d.get("first", ""), d.get("last", "")
            if (
                isinstance(heard, str)
                and isinstance(written, str)
                and isinstance(count, int)
                and isinstance(first, str)
                and isinstance(last, str)
            ):
                entries.append(Entry(heard, written, count, first, last))
        self._cache = (mtime, entries)
        return entries

    def _save(self, entries: list[Entry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        payload = {
            "version": STORE_VERSION,
            "entries": [
                {"heard": e.heard, "written": e.written, "count": e.count, "first": e.first,
                 "last": e.last}
                for e in entries
            ],
        }  # fmt: skip
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.write("\n")
        tmp.replace(self.path)
        self._cache = None

    def record(self, pairs: Sequence[tuple[str, str]]) -> list[Entry]:
        """Count the pairs; returns the entries that reached ``promote_after`` just now."""
        if not pairs:
            return []
        promoted: list[Entry] = []
        with self._lock:
            entries = list(self._load())
            now = _now()
            for heard, written in pairs:
                key = (heard.casefold(), written)
                i = next(
                    (i for i, e in enumerate(entries) if (e.heard.casefold(), e.written) == key),
                    None,
                )
                if i is None:
                    entries.append(Entry(heard, written, 1, now, now))
                    e = entries[-1]
                else:
                    e = entries[i] = replace(entries[i], count=entries[i].count + 1, last=now)
                if e.count == self.promote_after:
                    promoted.append(e)
            entries.sort(key=lambda e: (e.count, e.last), reverse=True)
            self._save(entries[:MAX_ENTRIES])
        return promoted

    def forget(self, text: str) -> int:
        """Delete entries whose heard or written form equals ``text`` (case-insensitive)."""
        key = text.strip().casefold()
        with self._lock:
            entries = list(self._load())
            kept = [e for e in entries if key not in (e.heard.casefold(), e.written.casefold())]
            if len(kept) != len(entries):
                self._save(kept)
            return len(entries) - len(kept)

    def clear(self) -> int:
        with self._lock:
            n = len(self._load())
            self.path.unlink(missing_ok=True)
            self._cache = None
            return n

    def terms(self) -> tuple[Term, ...]:
        """Promoted entries as shortlist terms, most frequent first, at most ``max_terms``."""
        with self._lock:
            entries = [e for e in self._load() if e.count >= self.promote_after]
        by_written: dict[str, list[str]] = {}
        for e in entries:  # already sorted by count, then recency
            alias = " ".join(_TOKEN_RE.findall(e.heard.lower()))
            by_written.setdefault(e.written, []).append(alias)
        out: list[Term] = []
        for written, heard in list(by_written.items())[: self.max_terms]:
            kind = _kind(written)
            spoken = tuple(dict.fromkeys([*heard, *spoken_forms(written, kind)]))
            out.append(Term(written, spoken, 1.0, kind, TermSource.LEARNED))
        return tuple(out)


def merge_terms(
    context_terms: Sequence[Term], learned: Sequence[Term], max_terms: int
) -> tuple[Term, ...]:
    """Learned terms first (their aliases joined with a context term of the same spelling)."""
    if not learned:
        return tuple(context_terms)
    by_canonical = {t.canonical: t for t in context_terms}
    merged = [
        replace(t, spoken=tuple(dict.fromkeys([*t.spoken, *by_canonical[t.canonical].spoken])))
        if t.canonical in by_canonical
        else t
        for t in learned
    ]
    taken = {t.canonical for t in merged}
    merged.extend(t for t in context_terms if t.canonical not in taken)
    return tuple(merged[:max_terms])


# -- watching a paste ------------------------------------------------------------------------


@dataclass(slots=True)
class _KeyReplay:
    """A terminal paste being watched through key events."""

    window_id: str
    focus_token: Callable[[], str]
    armed_at: float
    events: list[keyedits.Key]


class Learner:
    """One watch at a time, on its own thread; ``cancel()`` when a new dictation starts.

    ``reader`` None: no field read-back on this platform, only terminal key replay."""

    def __init__(  # noqa: PLR0913 - keyword-only tuning knobs
        self,
        store: LearnedStore,
        reader: FieldReader | None,
        *,
        window_s: float,
        poll_s: float = 0.5,
        on_promoted: Callable[[Entry], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.reader = reader
        self.window_s = min(MAX_WINDOW_S, max(0.0, window_s))
        self.poll_s = poll_s
        self.on_promoted = on_promoted
        self.clock = clock
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._replay: _KeyReplay | None = None

    def cancel(self) -> None:
        with self._lock:
            if self._stop is not None:
                self._stop.set()

    @property
    def watching(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def after_paste(
        self, focus: FocusInfo, pasted: str, field_value: str, focus_token: Callable[[], str]
    ) -> bool:
        """Start watching the pasted span; False if it cannot be located unambiguously."""
        anchor = Anchor.find(field_value, pasted)
        if anchor is None or not focus.window_id or self.reader is None:
            return False
        text = pasted.strip()
        self._start(lambda stop: self._watch(stop, focus, text, anchor, focus_token), None)
        return True

    def after_terminal_paste(
        self, focus: FocusInfo, pasted: str, focus_token: Callable[[], str]
    ) -> bool:
        """Start replaying the keys :meth:`key` receives on ``pasted`` (as pasted, cursor at
        its end); False without a window id."""
        if not focus.window_id or not pasted.strip():
            return False
        replay = _KeyReplay(focus.window_id, focus_token, self.clock(), [])
        self._start(lambda stop: self._watch_keys(stop, pasted, replay), replay)
        return True

    def _start(self, watch: Callable[[threading.Event], None], replay: _KeyReplay | None) -> None:
        self.cancel()
        stop = threading.Event()
        thread = threading.Thread(target=watch, args=(stop,), daemon=True, name="hark-learn")
        with self._lock:
            self._stop, self._thread, self._replay = stop, thread, replay
        thread.start()

    def key(self, press: KeyPress) -> None:
        """A key press or click from the daemon's listener (any thread); recorded only while a
        terminal paste is watched and the key goes to that window."""
        with self._lock:
            replay, stop = self._replay, self._stop
        if replay is None or stop is None or stop.is_set():
            return
        if self.clock() - replay.armed_at < PASTE_ECHO_S and press.char.lower() in {"v", "\x16"}:
            return  # hark's own paste chord
        if replay.focus_token() != replay.window_id:
            stop.set()  # focus moved: what was typed so far counts
            return
        key = keyedits.from_press(press)
        with self._lock:
            if self._replay is not replay:
                return
            replay.events.append(key)
        if key.keysym in keyedits.SUBMIT and not key.mods - {"shift"}:
            stop.set()

    def _watch_keys(self, stop: threading.Event, pasted: str, replay: _KeyReplay) -> None:
        stop.wait(self.window_s)
        with self._lock:
            events = list(replay.events)
            if self._replay is replay:
                self._replay = None
        edited = keyedits.reconstruct(pasted, events)
        self._learn(corrections(pasted, edited) if edited is not None else [])

    def _watch(
        self,
        stop: threading.Event,
        focus: FocusInfo,
        pasted: str,
        anchor: Anchor,
        focus_token: Callable[[], str],
    ) -> None:
        reader = self.reader
        if reader is None:
            return
        deadline = self.clock() + self.window_s
        latest: str | None = None
        while not stop.wait(self.poll_s) and self.clock() < deadline:
            if focus_token() != focus.window_id:
                break
            field = reader.read(focus)
            span = anchor.span(field.value) if field.kind == "text" else None
            if span is None:
                break  # sent, cleared, edited around it, or unreadable
            latest = span
        self._learn(corrections(pasted, latest) if latest is not None else [])

    def _learn(self, pairs: list[tuple[str, str]]) -> None:
        if not pairs:
            return
        log.info("learned %d correction(s)", len(pairs))
        try:
            promoted = self.store.record(pairs)
        except OSError as e:
            log.warning("could not write %s: %s", self.store.path, e)
            return
        for entry in promoted:
            if self.on_promoted is not None:
                self.on_promoted(entry)
