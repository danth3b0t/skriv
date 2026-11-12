"""Last-line text guardrails before anything reaches the clipboard / focused app.

- All profiles: drop escape sequences (CSI incl. bracketed-paste markers ESC[200~ /
  ESC[201~, OSC, two-byte ESC), C0/C1 control characters and bidi overrides.
- Terminal profile: one line only (every whitespace run, newlines included, becomes one
  space) and no trailing newline, so a paste can never press Enter.
- Other profiles keep newlines and tabs; CR/CRLF/LS/PS become LF.

`degenerate` recognizes recognizer output that is not text at all (a run of `<unk>`
tokens, one character or a short pattern repeated many times); the pipeline refuses it
right after recognition, as a failed transcription that `hark retry` can re-run.
"""

from __future__ import annotations

import re
import unicodedata

_ESCAPES = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI (bracketed paste markers, colours, cursor moves)
    r"|\x9b[0-?]*[ -/]*[@-~]"  # 8-bit CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC (titles, hyperlinks, clipboard writes)
    r"|\x1b[@-Z\\-_]"  # other two-byte escapes
)
_BIDI = {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F)}
_LINE_BREAKS = re.compile(r"\r\n|[\r\u2028\u2029\x85]")
_SPECIAL_TOKEN = re.compile(r"<unk>|<\|[^|<>]*\|>")  # tokenizer specials: <unk>, <|nospeech|>
# The whole text (whitespace dropped) is a unit of 1-4 characters at least 12 times, then
# maybe a final punctuation mark: "ÌÌÌ…", "ha ha ha …"; not "no no no no" or "ffffffff".
_REPEATED = re.compile(r"(.{1,4}?)\1{11,}[.!?…]?", re.DOTALL)


def degenerate(text: str) -> str:
    """Why `text` (a recognizer's output) is not speech made text, or "" if it may be."""
    compact = "".join(text.split())
    specials = _SPECIAL_TOKEN.findall(compact)
    if specials and 2 * sum(map(len, specials)) >= len(compact):
        return f"{len(specials)} unknown tokens"
    m = _REPEATED.fullmatch(compact)
    if m:
        return f"{m.group(1)!r} repeated {len(compact) // len(m.group(1))} times"
    return ""


def _drop_controls(text: str, keep: str) -> str:
    return "".join(
        c for c in text if c in keep or (unicodedata.category(c) != "Cc" and c not in _BIDI)
    )


def apply(text: str, *, terminal: bool) -> tuple[str, list[str]]:
    """Returns (safe text, events) where events name what was changed."""
    events: list[str] = []
    out = _ESCAPES.sub("", text)
    if out != text:
        events.append("stripped_escape_sequences")
    out = _LINE_BREAKS.sub("\n", out)
    cleaned = _drop_controls(out, keep="\n\t")
    if cleaned != out:
        events.append("stripped_control_chars")
    out = cleaned
    if terminal:
        single = " ".join(out.split())
        if "\n" in out:
            events.append("collapsed_newlines")
        out = single
    else:
        out = "\n".join(line.rstrip() for line in out.strip().split("\n"))
    return out, events
