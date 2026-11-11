"""Text around the caret of the focused field, read right before pasting (smart spacing).

``caret_context(focus) -> CaretContext`` never raises and waits at most ``deadline_s``.
Sources, first trusted one wins:

- ``ax`` (macOS Accessibility): the focused element of ``focus.pid`` (else the system-wide
  focused element when it belongs to that pid), ``AXSelectedTextRange`` and
  ``AXStringForRange`` around it (``AXValue`` sliced as a fallback). Secure text fields are
  never read. A terminal reporting the caret at (0, 0) while it holds text (Ghostty 1.4
  exposes its text but not its cursor) is *untrusted*.
- ``atspi`` (Linux AT-SPI via optional PyGObject, ``_atspi``): caret offset and text of the
  focused accessible of ``focus.pid``. Password fields are never read. Untested on hardware.
- ``tmux``: a terminal whose most recently used tty runs a tmux client: that client's pane
  cursor and the cursor row from ``capture-pane``. A hidden cursor (TUIs such as coding
  agents draw their own) or copy mode makes it untrusted.

Untrusted or missing context leaves the decision to the daemon's shadow buffer
(``hark_client.continuation``).
"""

from __future__ import annotations

import subprocess
import sys
import threading
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import psutil

from hark_client.context import terminal

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.context.types import FocusInfo

if sys.platform == "darwin":
    from hark_client.context import _macos as mac
else:
    mac = None

type CaretSource = Literal["ax", "atspi", "tmux", "shadow", "none"]

BEFORE_CHARS = 64
AFTER_CHARS = 2
AX_TIMEOUT_S = 0.05  # per Accessibility round trip
TMUX_TIMEOUT_S = 0.1
DEADLINE_S = 0.2  # whole lookup; a slower app gets no caret context

AX_ERROR_API_DISABLED = -25211  # no Accessibility permission
AX_ERROR_ATTRIBUTE_UNSUPPORTED = -25205
AX_ERROR_NO_VALUE = -25212
SECURE = "secure field"  # detail suffix: never read, and no other source is tried


@dataclass(frozen=True, slots=True)
class CaretContext:
    before: str | None = None  # up to BEFORE_CHARS before the caret/selection; "" = field start
    after: str | None = None  # up to AFTER_CHARS after it; "" = field end
    source: CaretSource = "none"
    trusted: bool = False
    detail: str = ""  # why it is missing/untrusted (history, debugging)


def _none(detail: str) -> CaretContext:
    return CaretContext(detail=detail)


# -- macOS Accessibility ---------------------------------------------------------------


class AxApi(Protocol):
    """The Accessibility calls used here (``_macos`` on macOS, fakes in tests)."""

    def ax_focused_element(self, pid: int | None, timeout_s: float) -> tuple[int, object | None]:
        """Focused element of the app ``pid`` (system-wide if None): (AXError, element)."""
        ...

    def ax_element_pid(self, element: object) -> int | None: ...
    def ax_attribute(
        self, element: object, name: str, timeout_s: float
    ) -> tuple[int, object | None]: ...
    def ax_range_value(self, value: object) -> tuple[int, int] | None: ...
    def ax_string_for_range(
        self, element: object, location: int, length: int, timeout_s: float
    ) -> tuple[int, str | None]: ...


def _utf16_slice(text: str, start: int, end: int) -> str:
    """``text[start:end]`` in UTF-16 code units, as Accessibility counts them."""
    data = text.encode("utf-16-le")
    return data[2 * max(0, start) : 2 * max(0, end)].decode("utf-16-le", errors="ignore")


def _focused(ax: AxApi, pid: int) -> tuple[object | None, str]:
    err, element = ax.ax_focused_element(pid, AX_TIMEOUT_S)
    if err == AX_ERROR_API_DISABLED:
        return None, "ax: no Accessibility permission"
    if element is not None:
        return element, ""
    # Chromium apps often answer the app-level query with no value; the system-wide one works.
    err2, element = ax.ax_focused_element(None, AX_TIMEOUT_S)
    if element is None:
        return None, f"ax: no focused element (app {err}, system-wide {err2})"
    owner = ax.ax_element_pid(element)
    if owner != pid:
        return None, f"ax: system-wide focused element belongs to pid {owner}, not {pid}"
    return element, ""


def ax_caret(ax: AxApi, pid: int, *, terminal: bool) -> CaretContext:
    element, detail = _focused(ax, pid)
    if element is None:
        return _none(detail)
    _, subrole = ax.ax_attribute(element, "AXSubrole", AX_TIMEOUT_S)
    if subrole == "AXSecureTextField":
        return _none(f"ax: {SECURE}")
    err, value = ax.ax_attribute(element, "AXSelectedTextRange", AX_TIMEOUT_S)
    rng = ax.ax_range_value(value) if value is not None else None
    if rng is None:
        return _none(f"ax: focused element has no caret ({err})")
    loc, length = rng
    _, count_value = ax.ax_attribute(element, "AXNumberOfCharacters", AX_TIMEOUT_S)
    count = count_value if isinstance(count_value, int) else None
    if terminal and loc == 0 and length == 0 and count:
        return CaretContext(source="ax", detail=f"ax: terminal caret at (0, 0) with {count} chars")
    start = max(0, loc - BEFORE_CHARS)
    end = loc + length
    n_after = AFTER_CHARS if count is None else max(0, min(AFTER_CHARS, count - end))
    before = ax.ax_string_for_range(element, start, loc - start, AX_TIMEOUT_S)[1] if loc else ""
    after = ax.ax_string_for_range(element, end, n_after, AX_TIMEOUT_S)[1] if n_after else ""
    if before is None or after is None:
        _, full = ax.ax_attribute(element, "AXValue", AX_TIMEOUT_S)
        if isinstance(full, str):
            before = _utf16_slice(full, start, loc) if before is None else before
            after = _utf16_slice(full, end, end + n_after) if after is None else after
    if before is None:
        return CaretContext(source="ax", detail="ax: text before the caret unreadable")
    return CaretContext(before, after, "ax", trusted=True)


# -- AT-SPI (Linux) ----------------------------------------------------------------------


class TextField(Protocol):
    """A focused text field with character offsets (AT-SPI Text; fakes in tests)."""

    @property
    def secure(self) -> bool: ...
    def caret(self) -> int: ...
    def count(self) -> int: ...
    def text(self, start: int, end: int) -> str: ...


def text_field_caret(field: TextField | None, source: CaretSource = "atspi") -> CaretContext:
    if field is None:
        return _none(f"{source}: no focused text field")
    if field.secure:
        return _none(f"{source}: {SECURE}")
    caret, count = field.caret(), field.count()
    if not 0 <= caret <= count:
        return CaretContext(source=source, detail=f"{source}: caret {caret} outside 0..{count}")
    before = field.text(max(0, caret - BEFORE_CHARS), caret) if caret else ""
    after = field.text(caret, min(count, caret + AFTER_CHARS)) if caret < count else ""
    return CaretContext(before, after, source, trusted=True)


def _atspi_caret(pid: int) -> CaretContext:
    from hark_client.context import _atspi  # noqa: PLC0415 - optional, Linux only

    return text_field_caret(_atspi.focused_text(pid))


# -- tmux ------------------------------------------------------------------------------

type TmuxRun = Callable[[list[str]], str | None]

_CLIENT_FORMAT = (
    "#{client_tty}\t#{pane_id}\t#{cursor_flag}\t#{cursor_x}\t#{cursor_y}\t#{pane_in_mode}"
)


def run_tmux(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv
            ["tmux", *args],  # noqa: S607 - tmux from PATH is intended
            capture_output=True,
            text=True,
            timeout=TMUX_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def split_at_column(line: str, column: int) -> tuple[str, str]:
    """(text left of screen column ``column``, the next AFTER_CHARS); wide chars count 2."""
    width = 0
    for i, ch in enumerate(line):
        if width >= column:
            return line[max(0, i - BEFORE_CHARS) : i], line[i : i + AFTER_CHARS]
        if not unicodedata.combining(ch):
            width += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    # capture-pane drops trailing blanks: the cursor may sit past the end of the text
    return (line + " " * (column - width))[-BEFORE_CHARS:], ""


def tmux_pane_caret(socket: list[str], client_tty: str, run: TmuxRun = run_tmux) -> CaretContext:
    """Caret of the pane shown by the tmux client on ``client_tty``."""
    out = run([*socket, "list-clients", "-F", _CLIENT_FORMAT])
    row = next(
        (r for r in (line.split("\t") for line in (out or "").splitlines()) if r[0] == client_tty),
        None,
    )
    if row is None or len(row) != 6 or not (row[3].isdigit() and row[4].isdigit()):
        return _none(f"tmux: no client on {client_tty}")
    _, pane, visible, x, y, in_mode = row
    if visible != "1":
        return CaretContext(source="tmux", detail="tmux: cursor hidden (the app draws its own)")
    if in_mode != "0":
        return CaretContext(source="tmux", detail="tmux: pane in copy mode")
    line = run([*socket, "capture-pane", "-p", "-t", pane, "-S", y, "-E", y])
    if line is None:
        return CaretContext(source="tmux", detail="tmux: capture-pane failed")
    before, after = split_at_column(line.rstrip("\n"), int(x))
    return CaretContext(before, after, "tmux", trusted=True)


def tmux_client(terminal_pid: int) -> tuple[list[str], str] | None:
    """(socket args, client tty) of a tmux client on the terminal's most recently used tty."""
    try:
        procs = psutil.Process(terminal_pid).children(recursive=True)
    except psutil.Error:
        return None
    groups = terminal.by_tty(procs)
    if not groups:
        return None
    tty = max(groups, key=terminal.tty_activity)
    client = next((p for p in groups[tty] if terminal.process_name(p) == "tmux"), None)
    if client is None:
        return None
    return terminal.tmux_socket_args(client), tty


def _tmux_caret(terminal_pid: int) -> CaretContext | None:
    found = tmux_client(terminal_pid)
    return tmux_pane_caret(*found) if found is not None else None


# -- entry point -----------------------------------------------------------------------


def _lookup(focus: FocusInfo, pid: int) -> CaretContext:
    if sys.platform == "darwin" and mac is not None:
        ctx = ax_caret(mac, pid, terminal=focus.is_terminal)
    elif sys.platform.startswith("linux"):
        ctx = _atspi_caret(pid)
    else:
        ctx = _none("no caret source on this platform")
    if ctx.trusted or not focus.is_terminal or ctx.detail.endswith(SECURE):
        return ctx
    tmux = _tmux_caret(pid)
    if tmux is None or tmux.trusted:
        return tmux or ctx
    return CaretContext(source="tmux", detail=f"{ctx.detail}; {tmux.detail}")


class _Worker:
    """One lookup at a time; a hung app makes later lookups skip instead of piling up."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hark-caret")
        self._lock = threading.Lock()
        self._job: Future[CaretContext] | None = None

    def run(self, fn: Callable[[], CaretContext], deadline_s: float) -> CaretContext:
        with self._lock:
            if self._job is not None and not self._job.done():
                return _none("caret: previous lookup still running (skipped)")
            self._job = job = self._pool.submit(fn)
        try:
            return job.result(timeout=deadline_s)
        except FutureTimeout:
            return _none(f"caret: no answer within {deadline_s * 1000:.0f} ms")
        except Exception as e:  # noqa: BLE001 - spacing must never break pasting
            return _none(f"caret: {type(e).__name__}: {e}")


_WORKER = _Worker()


def caret_context(focus: FocusInfo, deadline_s: float = DEADLINE_S) -> CaretContext:
    """Text around the caret in ``focus`` (see the module docstring). Never raises."""
    pid = focus.pid
    if pid is None:
        return _none("caret: focused app unknown")
    return _WORKER.run(lambda: _lookup(focus, pid), deadline_s)
