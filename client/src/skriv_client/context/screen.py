"""Text on screen where the user is about to type, as term candidates (key-down, volatile).

First available source wins; none raises (the engine bounds the time):

- tmux (terminals): the pane shown by the focused terminal's tmux client (the same client the
  caret lookup uses), ``capture-pane -J`` of the grid plus ``HISTORY_LINES`` of scrollback.
  Rows nearer the cursor score higher, ``exp(-rows / 40)``. Origin ``terminal_text``.
- macOS Accessibility: the focused element, read with bounded ``AXStringForRange`` around
  the caret (``MAX_CHARS`` each side; never the whole ``AXValue`` of a big buffer). Text
  nearer the caret scores higher, ``exp(-chars / 1000)``. Terminal.app reports a real caret:
  ``terminal_text``. A terminal whose caret is (0, 0) while it holds text (Ghostty 1.4: its AX
  value is a cached buffer, not the viewport) gives only its last ``BUFFER_CHARS`` as
  ``buffer`` (discounted in ranking). Other apps: ``focused_text``.
- Linux AT-SPI: the focused text field the same way (``focused_text``).

Never read: secure/password fields, deny-listed apps (password managers), panes running a
credential program (sudo, ssh, gpg, pass, ...), text whose last line before the caret is a
password prompt. Text is scrubbed before extraction; only terms leave this module.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from skriv_client.context import caret
from skriv_client.context.aliases import extract_terms, scrub
from skriv_client.context.rank import Candidate, Origin

if TYPE_CHECKING:
    from skriv_client.context.caret import AxApi, TextField, TmuxRun
    from skriv_client.context.types import FocusInfo

if sys.platform == "darwin":
    from skriv_client.context import _macos as mac
else:
    mac = None

MAX_CHARS = 4000  # each side of the caret
BUFFER_CHARS = 3000  # tail of a terminal buffer without a caret
MAX_VALUE_CHARS = 20_000  # AXValue fallback only for elements this small
HISTORY_LINES = 200
MAX_LINE_CHARS = 400
ROWS_PER_CHUNK = 8
CHUNK_CHARS = 400
CHAR_TAU = 1000.0
ROW_TAU = 40.0
AX_TIMEOUT_S = caret.AX_TIMEOUT_S

# Password managers and system credential prompts: no text is read (bundle ids, classes).
DENY_APPS = frozenset(
    {
        "com.1password.1password",
        "com.agilebits.onepassword7",
        "com.bitwarden.desktop",
        "org.keepassxc.keepassxc",
        "com.apple.keychainaccess",
        "com.apple.passwords",
        "com.apple.securityagent",
        "1password",
        "bitwarden",
        "keepassxc",
        "org.keepassxc.keepassxc_browser",
    }
)
CREDENTIAL_COMMANDS = frozenset(
    {"sudo", "su", "ssh", "gpg", "passwd", "op", "pass", "age", "kinit", "doas", "login"}
)
_PROMPT_RE = re.compile(r"(?i)(password|passphrase|passcode|\bpin\b|token)[^:\n]*:\s*$")
_CHUNK_RE = re.compile(rf"\S.{{0,{CHUNK_CHARS}}}(?=\s|\Z)|\S+", re.DOTALL)


def denied(app_id: str) -> bool:
    return app_id.lower() in DENY_APPS


@dataclass(frozen=True, slots=True)
class ScreenText:
    origin: Origin
    chunks: tuple[tuple[str, float], ...] = ()  # (text, proximity in (0, 1])
    source: str = ""  # "tmux" | "ax" | "atspi"
    detail: str = ""  # why there is no text


def candidates(screen: ScreenText) -> list[Candidate]:
    return [
        Candidate(t, k, screen.origin, score)
        for text, score in screen.chunks
        for t, k in extract_terms(scrub(text), prose=True)
    ]


def _none(origin: Origin, source: str, detail: str) -> ScreenText:
    return ScreenText(origin, (), source, f"{source}: {detail}")


def _prompt_before(text: str) -> bool:
    """The last non-empty line asks for a secret (``Password:``)."""
    last = next((ln for ln in reversed(text.splitlines()) if ln.strip()), "")
    return _PROMPT_RE.search(last) is not None


def around_caret(text: str, caret_at: int) -> tuple[tuple[str, float], ...]:
    """``text`` cut at whitespace into ~CHUNK_CHARS pieces, scored by their distance from
    the caret offset ``caret_at``."""
    out: list[tuple[str, float]] = []
    for m in _CHUNK_RE.finditer(text):
        start, end = m.span()
        dist = caret_at - end if end <= caret_at else max(0, start - caret_at)
        out.append((m.group(), round(math.exp(-dist / CHAR_TAU), 4)))
    return tuple(out)


# -- tmux --------------------------------------------------------------------------------

_PANE_FORMAT = "#{client_tty}\t#{pane_id}\t#{cursor_y}\t#{pane_height}\t#{pane_current_command}"


def tmux_screen(socket: list[str], client_tty: str, run: TmuxRun = caret.run_tmux) -> ScreenText:
    """Text of the pane shown by the tmux client on ``client_tty``."""
    origin = Origin.TERMINAL_TEXT
    out = run([*socket, "list-clients", "-F", _PANE_FORMAT])
    row = next(
        (r for r in (ln.split("\t") for ln in (out or "").splitlines()) if r[0] == client_tty),
        None,
    )
    if row is None or len(row) != 5 or not (row[2].isdigit() and row[3].isdigit()):
        return _none(origin, "tmux", f"no client on {client_tty}")
    _, pane, y, height, command = row
    if command.lower() in CREDENTIAL_COMMANDS or command.startswith("pinentry"):
        return _none(origin, "tmux", f"pane runs {command}")
    text = run([*socket, "capture-pane", "-p", "-J", "-t", pane, "-S", f"-{HISTORY_LINES}"])
    if text is None:
        return _none(origin, "tmux", "capture-pane failed")
    lines = [ln[:MAX_LINE_CHARS] for ln in text.rstrip("\n").split("\n")]
    cursor = max(0, min(len(lines) - 1, len(lines) - int(height) + int(y)))
    if _prompt_before("\n".join(lines[: cursor + 1])):
        return _none(origin, "tmux", "password prompt")
    chunks: list[tuple[str, float]] = []
    for i in range(0, len(lines), ROWS_PER_CHUNK):
        block = lines[i : i + ROWS_PER_CHUNK]
        rows = (
            0
            if i <= cursor < i + len(block)
            else min(abs(i - cursor), abs(i + len(block) - 1 - cursor))
        )
        if any(ln.strip() for ln in block):
            chunks.append(("\n".join(block), round(math.exp(-rows / ROW_TAU), 4)))
    return ScreenText(origin, tuple(chunks), "tmux")


# -- macOS Accessibility -----------------------------------------------------------------


def _read(ax: AxApi, element: object, start: int, length: int, value: str | None) -> str | None:
    if length <= 0:
        return ""
    text = ax.ax_string_for_range(element, start, length, AX_TIMEOUT_S)[1]
    if text is None and value is not None:
        data = value.encode("utf-16-le")[2 * start : 2 * (start + length)]
        text = data.decode("utf-16-le", errors="ignore")
    return text


def ax_screen(ax: AxApi, pid: int, *, terminal: bool) -> ScreenText:
    """Text around the caret of the focused element of ``pid`` (see the module docstring)."""
    origin = Origin.TERMINAL_TEXT if terminal else Origin.FOCUSED_TEXT
    element, detail = caret.focused_element(ax, pid)
    if element is None:
        return ScreenText(origin, (), "ax", detail)
    _, role = ax.ax_attribute(element, "AXRole", AX_TIMEOUT_S)
    _, subrole = ax.ax_attribute(element, "AXSubrole", AX_TIMEOUT_S)
    if "AXSecureTextField" in (role, subrole):
        return _none(origin, "ax", caret.SECURE)
    _, count_value = ax.ax_attribute(element, "AXNumberOfCharacters", AX_TIMEOUT_S)
    count = count_value if isinstance(count_value, int) else 0
    _, range_value = ax.ax_attribute(element, "AXSelectedTextRange", AX_TIMEOUT_S)
    rng = ax.ax_range_value(range_value) if range_value is not None else None
    if count <= 0 or rng is None:
        return _none(origin, "ax", "no text with a caret")
    value: str | None = None
    if count <= MAX_VALUE_CHARS:  # fallback for elements without AXStringForRange
        _, v = ax.ax_attribute(element, "AXValue", AX_TIMEOUT_S)
        value = v if isinstance(v, str) else None
    loc, length = rng
    if terminal and loc == 0 and length == 0:  # buffer without a caret (Ghostty)
        n = min(BUFFER_CHARS, count)
        before, after, origin = _read(ax, element, count - n, n, value), "", Origin.BUFFER
    else:
        start, end = max(0, loc - MAX_CHARS), loc + length
        before = _read(ax, element, start, loc - start, value)
        after = _read(ax, element, end, min(MAX_CHARS, count - end), value)
    if before is None or after is None:
        return _none(origin, "ax", "text unreadable")
    if terminal and _prompt_before(before):
        return _none(origin, "ax", "password prompt")
    return ScreenText(origin, around_caret(before + after, len(before)), "ax")


# -- AT-SPI (Linux) ----------------------------------------------------------------------


def field_screen(field: TextField | None) -> ScreenText:
    origin = Origin.FOCUSED_TEXT
    if field is None:
        return _none(origin, "atspi", "no focused text field")
    if field.secure:
        return _none(origin, "atspi", caret.SECURE)
    at, count = field.caret(), field.count()
    if not 0 <= at <= count:
        return _none(origin, "atspi", f"caret {at} outside 0..{count}")
    start, end = max(0, at - MAX_CHARS), min(count, at + MAX_CHARS)
    text = field.text(start, end)
    return ScreenText(origin, around_caret(text, at - start), "atspi")


# -- entry point -------------------------------------------------------------------------


def screen_text(focus: FocusInfo) -> ScreenText | None:
    """Text near where the user types in ``focus``; None when nothing is applicable."""
    pid = focus.pid
    if pid is None or denied(focus.app_id):
        return None
    if focus.is_terminal and (client := caret.tmux_client(pid)) is not None:
        return tmux_screen(*client)  # the exact pane; the emulator's buffer is not it
    if sys.platform == "darwin" and mac is not None:
        return ax_screen(mac, pid, terminal=focus.is_terminal) if mac.ax_trusted() else None
    if sys.platform.startswith("linux"):
        from skriv_client.context import _atspi  # noqa: PLC0415 - optional, Linux only

        return field_screen(_atspi.focused_text(pid))
    return None
