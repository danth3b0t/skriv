"""Read the focused text field of the paste target, and judge whether a paste landed.

Used after a paste only (verification, correction learning), never on the key-down path.

- macOS: Accessibility (``AXFocusedUIElement`` of the target pid -> ``AXValue``), needs the
  Accessibility permission hark already asks for.
- Linux: AT-SPI via PyGObject (``hark_client.atspi``); optional, **untested on real hardware**.

Secure/password fields are never read. Values longer than ``max_chars`` are not read either
(``too_long``). Every read runs on one worker thread with a hard wait, so a hung app costs at
most ``timeout_s`` and later reads are skipped instead of piling up.

Verdicts follow "absence of evidence is not failure": ``landed`` (the pasted text is in the
field), ``missing`` (the field is readable, non-empty and the text is not there, twice),
``not_text`` (the focused element has no text value), ``unknown`` (could not look). For now a
verdict is only recorded: web/Electron fields may expose stale values over AX, so "missing" must
first be measured from history before hark acts on it (e.g. re-copying the text).
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from hark_client.context import FocusInfo

log = logging.getLogger("hark.fields")

type FieldKind = Literal["text", "secure", "not_text", "too_long", "unavailable"]
type Verdict = Literal["landed", "missing", "not_text", "unknown"]

MAX_CHARS = 65_536
VERIFY_DELAYS_S = (0.15, 0.35)  # first read, then a second one before saying "missing"


@dataclass(frozen=True, slots=True)
class FieldText:
    kind: FieldKind
    value: str = ""  # only for kind == "text"; held in memory, never logged or stored


class FieldReader(Protocol):
    def read(self, focus: FocusInfo) -> FieldText: ...


class DeadlineReader:
    """Runs ``read_fn(pid)`` on one worker thread; ``unavailable`` on timeout or while busy."""

    def __init__(self, read_fn: Callable[[int], FieldText], timeout_s: float = 0.3) -> None:
        self._read_fn = read_fn
        self._timeout_s = timeout_s
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hark-field")
        self._lock = threading.Lock()
        self._busy = False

    def read(self, focus: FocusInfo) -> FieldText:
        if focus.pid is None:
            return FieldText("unavailable")
        pid = focus.pid
        with self._lock:
            if self._busy:
                return FieldText("unavailable")
            self._busy = True

        def job() -> FieldText:
            try:
                return self._read_fn(pid)
            finally:
                with self._lock:
                    self._busy = False

        try:
            return self._pool.submit(job).result(timeout=self._timeout_s)
        except FutureTimeout:
            log.debug("field read timed out after %.0f ms", self._timeout_s * 1000)
        except Exception as e:  # noqa: BLE001 - a failed read must never break dictation
            log.debug("field read failed: %s: %s", type(e).__name__, e)
        return FieldText("unavailable")


_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2212": "-"})
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Spacing, typographic quotes and dashes don't count as differences."""
    return _SPACE_RE.sub(" ", text.translate(_QUOTES).translate(_DASHES)).strip()


def verdict_for(field: FieldText, pasted: str) -> Verdict:
    if field.kind == "not_text":
        return "not_text"
    if field.kind != "text":
        return "unknown"
    needle, value = normalize(pasted), normalize(field.value)
    if not needle or not value:
        return "unknown"  # an empty read-back after a paste: the field hides its content
    return "landed" if needle in value else "missing"


def verify(
    reader: FieldReader,
    focus: FocusInfo,
    pasted: str,
    *,
    delays_s: Sequence[float] = VERIFY_DELAYS_S,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Verdict, FieldText]:
    """Read after each delay until the text is seen; ``missing`` only if every read says so."""
    verdict: Verdict = "unknown"
    field = FieldText("unavailable")
    for delay in delays_s:
        sleep(delay)
        field = reader.read(focus)
        verdict = verdict_for(field, pasted)
        if verdict != "missing":
            break
    return verdict, field


def make_reader(max_chars: int = MAX_CHARS) -> FieldReader | None:
    """The platform's reader, or None when the platform offers none (or lacks permission)."""
    if sys.platform == "darwin":
        from hark_client.context import _macos as mac  # noqa: PLC0415 - pyobjc only on macOS

        if not mac.ax_trusted():
            return None
        return DeadlineReader(lambda pid: mac.focused_field(pid, 0.1, max_chars))
    if sys.platform.startswith("linux"):
        from hark_client import atspi  # noqa: PLC0415

        read = atspi.reader(max_chars)
        return DeadlineReader(read) if read is not None else None
    return None
