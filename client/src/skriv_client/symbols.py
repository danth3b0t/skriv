"""Spoken symbols for the terminal profile: "canon dot py" -> "canon.py",
"commit dash m fix colon" -> "commit -m fix:", "dash dash verbose" -> "--verbose",
"src slash main dot rs" -> "src/main.rs". Deterministic and conservative: a rule
fires only on an unambiguous spoken form, and trailing punctuation on a word
(a comma after "canon") always blocks joining it with the next one.

Rules, on whitespace-separated words:
- "dot EXT" (EXT from EXTENSIONS) is glued to the previous word, unless that word
  is a command ("cat dot env" -> "cat .env" only for DOTFILES).
- "dot dot" -> "..", "dot slash X" -> "./X", "dot dot slash X" -> "../X".
- "dash dash WORD" -> "--word"; "dash L" (one letter) -> "-l" after a command-like
  word (COMMANDS, or a flag), or after any word but a function word once a command
  that is no everyday word (docker, uv; not add, make) was said in the sentence
  ("docker compose up dash d").
- "A slash B ..." -> "A/B...": with 2+ slashes, a final "dot EXT", or a leading
  slash at the start or after a command ("cd slash tmp" -> "cd /tmp"). One slash
  between ordinary words stays a word ("use the slash command"). A function word
  ("to", "in") is never the first part: "to slash data slash models" -> "to /data/models".
  A leading-slash path right after a host:port is glued to it ("localhost:8000/health").
- "colon" is glued to the previous word as ":", and a number after it too ("localhost:8000").
- "A underscore B" -> "A_B" between two words that are not function words.
- "chmod plus x" -> "chmod +x".
- "issue/PR/port/ticket" + number words -> digits ("PR four thirty seven" -> "PR 437").
- "zero comma one" -> "0,1" between two numbers.
- ASR-joined forms: "x.by" -> "x.py", "commit-m" -> "commit -m", and once such a command
  was said: "up-d" -> "up -d", "dash-fix" -> "--fix"; "dash-dash fix" -> "--fix" always;
  "dash-fix" -> "--fix" also without a command when ``--fix`` is a flag on the shortlist.
- "Get" + a git subcommand starting the text is ``git`` (the recognizer's "get"), when the
  subcommand ends the text or a clause, or a flag or a branch/remote follows ("Get status."
  -> "git status.", "Get push --force" -> "git push --force"; not "Get commit messages").
- When a flag was produced and the text starts with a capitalized command, it
  is lower-cased ("Git commit -m" -> "git commit -m"); so is a capitalized command
  followed by one of its subcommands ("Git status" -> "git status").
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from skriv_client.context.canon import common_words

if TYPE_CHECKING:
    from collections.abc import Iterable

EXTENSIONS = frozenset({
    "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs", "rs", "go", "rb", "java", "kt", "swift",
    "c", "h", "cc", "cpp", "hpp", "cs", "sh", "zsh", "fish", "md", "txt", "json", "jsonl", "toml",
    "yaml", "yml", "ini", "cfg", "conf", "lock", "env", "sql", "csv", "html", "css", "scss",
    "xml", "proto", "app", "log",
})  # fmt: skip
MISHEARD_EXTENSIONS = {"by": "py", "jason": "json"}
DOTFILES = frozenset({
    "env", "gitignore", "github", "git", "vscode", "venv", "bashrc", "zshrc", "profile", "config",
    "local", "cache",
})  # fmt: skip
COMMANDS = frozenset({
    "git", "commit", "push", "pull", "add", "checkout", "switch", "status", "log", "diff",
    "rebase", "merge", "stash", "clone", "fetch", "reset", "restore", "branch", "tag", "show",
    "ls", "cd", "rm", "cp", "mv", "mkdir", "rmdir", "cat", "less", "more", "grep", "rg", "find",
    "fd", "sed", "awk", "head", "tail", "chmod", "chown", "curl", "wget", "ssh", "scp", "rsync",
    "tar", "zip", "unzip", "docker", "kubectl", "npm", "npx", "pnpm", "yarn", "node", "bun",
    "deno", "python", "python3", "pip", "uv", "uvx", "cargo", "rustc", "go", "make", "just",
    "pytest", "ruff", "hark", "brew", "sudo", "echo", "touch", "open", "vim", "nvim", "code",
    "man", "which", "kill", "pkill", "ps", "top", "du", "df", "ln", "systemctl", "journalctl",
    "sort", "uniq", "wc", "xargs", "export", "source", "tmux",
})  # fmt: skip
_PACKAGE = frozenset({"install", "add", "remove", "run", "test", "build", "dev", "exec", "dlx"})
# Commands that are no English word, with their subcommands: "Git status" is a command line.
SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({
        "status", "commit", "push", "pull", "add", "checkout", "switch", "log", "diff", "rebase",
        "merge", "stash", "clone", "fetch", "reset", "restore", "branch", "tag", "show", "init",
        "remote", "bisect", "blame",
    }),
    "docker": frozenset({"compose", "run", "build", "ps", "exec", "logs", "pull", "push", "images"}),
    "kubectl": frozenset({"get", "apply", "describe", "logs", "delete", "exec", "rollout"}),
    "uv": frozenset({"run", "sync", "add", "remove", "lock", "pip", "tool", "venv", "init"}),
    "npm": _PACKAGE, "pnpm": _PACKAGE, "yarn": _PACKAGE, "bun": _PACKAGE,
    "cargo": frozenset({"build", "run", "test", "check", "add", "clippy", "fmt", "install"}),
    "systemctl": frozenset({"start", "stop", "restart", "status", "enable", "disable"}),
    "brew": frozenset({"install", "upgrade", "uninstall", "services"}),
    "hark": frozenset({
        "run", "app", "ctl", "once", "retry", "history", "last", "doctor", "eval", "service",
    }),
}  # fmt: skip
# Never the first part of a path or of an underscore identifier.
FUNCTION_WORDS = frozenset({
    "to", "from", "in", "into", "on", "onto", "at", "of", "for", "with", "within", "under",
    "over", "via", "by", "the", "a", "an", "and", "or", "is", "are", "as", "than", "then", "no",
    "not", "it", "this", "that",
})  # fmt: skip
_NUMBER_CUES = frozenset({"issue", "pr", "port", "ticket"})
_UNITS = {
    w: i
    for i, w in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen")
    )
}  # fmt: skip
_TENS = {
    w: 10 * i
    for i, w in enumerate(
        ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"), 2
    )
}
_TRAILING = ".,;:!?"
_SENTENCE_END = ".!?"
_WORD = re.compile(r"[\w][\w.-]*\Z")
_JOINED_FLAG = re.compile(r"([A-Za-z]+)-([A-Za-z])\Z")
_HOST_PORT = re.compile(r"[\w.-]+:\d+\Z")
# "Get" + one of these at the start: the recognizer's "git"
GET_GIT = frozenset({
    "status", "push", "pull", "commit", "diff", "log", "checkout", "rebase", "stash", "fetch",
    "merge", "switch", "restore", "add",
})  # fmt: skip
_GIT_ARGS = frozenset({"origin", "upstream", "main", "master", "head"})


def _split(token: str) -> tuple[str, str]:
    core = token.rstrip(_TRAILING)
    return (core, token[len(core) :]) if core else (token, "")


def _number_value(word: str) -> int | None:
    """0-99 from one word: "seven", "forty", "twenty-one", "42"."""
    if word.isdigit():
        return int(word)
    if word in _UNITS:
        return _UNITS[word]
    if word in _TENS:
        return _TENS[word]
    tens, _, unit = word.partition("-")
    if tens in _TENS and _UNITS.get(unit, 0) in range(1, 10):
        return _TENS[tens] + _UNITS[unit]
    return None


class _Rewriter:
    def __init__(self, text: str, flags: Iterable[str]) -> None:
        self.known_flags = {f.lower(): f for f in flags}
        self.words = [_split(t) for t in text.split()]
        self.out: list[str] = []
        self.events: list[str] = []
        self.flag = False
        self.command_seen = False  # a command that is no everyday word, in this sentence

    def low(self, i: int) -> str:
        return self.words[i][0].lower() if i < len(self.words) else ""

    def plain(self, i: int) -> bool:
        """Word i exists and has no trailing punctuation."""
        return i < len(self.words) and not self.words[i][1]

    def prev_core(self) -> str | None:
        return _split(self.out[-1])[0] if self.out else None

    def prev_open(self) -> bool:
        """The previous output word can be glued to (no trailing punctuation)."""
        return bool(self.out) and _split(self.out[-1])[1] == ""

    def commandish(self, word: str | None) -> bool:
        return word is not None and (word.lower() in COMMANDS or word.startswith("-"))

    def emit(self, text: str, event: str) -> None:
        self.out.append(text)
        self.events.append(event)

    def run(self) -> tuple[str, list[str]]:
        i = 0
        while i < len(self.words):
            n = self.step(i)
            for core, punct in self.words[i:n]:
                if core.lower() in COMMANDS and core.lower() not in common_words():
                    self.command_seen = True  # docker, uv; not add, make, find
                if any(c in _SENTENCE_END for c in punct):
                    self.command_seen = False
            i = n
        if self.get_git():
            self.out[0] = "git"
            self.events.append("get git")
        if self.out and self.out[0][:1].isupper() and self.leading_command():
            self.out[0] = self.out[0].lower()
        return " ".join(self.out), self.events

    def get_git(self) -> bool:
        """The text starts with "Get" + a git subcommand used as a command line."""
        if len(self.out) < 2 or self.out[0].lower() != "get":
            return False
        sub, punct = _split(self.out[1])
        if sub.lower() not in GET_GIT:
            return False
        if punct or len(self.out) == 2:
            return True
        nxt = _split(self.out[2])[0]
        return nxt.startswith("-") or nxt.lower() in _GIT_ARGS

    def leading_command(self) -> bool:
        first = self.out[0].lower()
        if self.flag and first in COMMANDS:
            return True
        second = _split(self.out[1])[0].lower() if len(self.out) > 1 else ""
        return second in SUBCOMMANDS.get(first, ())

    def step(self, i: int) -> int:  # noqa: PLR0911 - one return per rule
        core, punct = self.words[i]
        c = core.lower()
        if (n := self.path(i)) is not None:
            return n
        if (n := self.flags(i)) is not None:
            return n
        if (n := self.numbers(i)) is not None:
            return n
        if c == "dot" and self.plain(i):
            nxt = self.low(i + 1)
            if nxt == "dot" and not (self.plain(i + 1) and self.low(i + 2) == "slash"):
                self.emit(".." + self.words[i + 1][1], "dot dot")
                return i + 2
            ext = MISHEARD_EXTENSIONS.get(nxt, nxt)
            if ext in EXTENSIONS and self.prev_open() and not self.commandish(self.prev_core()):
                self.out[-1] += "." + ext + self.words[i + 1][1]
                self.events.append(f"dot {nxt}")
                return i + 2
            if nxt in DOTFILES and (not self.out or self.commandish(self.prev_core())):
                self.emit("." + nxt + self.words[i + 1][1], f"dot {nxt}")
                return i + 2
        if c == "colon" and self.prev_open():
            if not punct and self.low(i + 1).isdigit():  # localhost colon 8000
                self.out[-1] += ":" + self.words[i + 1][0] + self.words[i + 1][1]
                self.events.append("colon")
                return i + 2
            self.out[-1] += ":" + punct
            self.events.append("colon")
            return i + 1
        if c == "underscore" and not punct and self.prev_open() and self.joinable(i - 1, i + 1):
            self.out[-1] += "_" + self.words[i + 1][0] + self.words[i + 1][1]
            self.events.append("underscore")
            return i + 2
        if c == "plus" and not punct and self.prev_core() == "chmod" and self.prev_open():
            nxt = self.low(i + 1)
            if len(nxt) == 1 and nxt in "rwx":
                self.emit("+" + nxt + self.words[i + 1][1], "plus " + nxt)
                return i + 2
        self.out.append(self.joined_forms(core) + punct)
        return i + 1

    def joinable(self, a: int, b: int) -> bool:
        """Words a and b (around "underscore") are words, not function words."""
        if a < 0 or b >= len(self.words):
            return False
        return all(
            _WORD.match(self.words[k][0]) and self.low(k) not in FUNCTION_WORDS for k in (a, b)
        )

    def flags(self, i: int) -> int | None:
        """ "dash dash WORD", "dash L" after a command, and the recognizer's "dash-dash WORD",
        "dash-WORD"."""
        core, punct = self.words[i]
        c = core.lower()
        nxt = self.low(i + 1)
        if c == "dash" and self.plain(i) and nxt == "dash" and self.plain(i + 1):
            word = self.low(i + 2)
            if _WORD.match(word) and not word.isdigit():
                return self.flag_out("--" + word + self.words[i + 2][1], f"dash dash {word}", i + 3)
        prev = self.prev_core()
        after_command = self.commandish(prev) or (
            self.command_seen
            and prev is not None
            and self.prev_open()
            and prev.lower() not in FUNCTION_WORDS
        )
        one_letter = len(nxt) == 1 and nxt.isalpha()
        if c == "dash" and self.plain(i) and after_command and one_letter:
            return self.flag_out("-" + nxt + self.words[i + 1][1], f"dash {nxt}", i + 2)
        if c == "dash-dash" and not punct and _WORD.match(nxt) and not nxt.isdigit():
            return self.flag_out("--" + nxt + self.words[i + 1][1], f"{c} {nxt}", i + 2)
        rest = c.removeprefix("dash-")
        if rest == c or not (_WORD.match(rest) and rest.isascii()):
            return None
        if self.command_seen:
            dashes = "-" if len(rest) == 1 else "--"
            return self.flag_out(dashes + rest + punct, c, i + 1)
        known = self.known_flags.get("--" + rest) or self.known_flags.get("-" + rest)
        return self.flag_out(known + punct, c, i + 1) if known else None

    def flag_out(self, text: str, event: str, nxt: int) -> int:
        self.flag = True
        self.emit(text, event)
        return nxt

    def numbers(self, i: int) -> int | None:
        """Number words after issue/PR/port/ticket, and "zero comma one"."""
        core, punct = self.words[i]
        c = core.lower()
        if c in _NUMBER_CUES and not punct:
            digits, n = self.read_number(i + 1)
            if n:
                self.out.append(core)
                self.emit(digits + self.words[i + n][1], f"{c} number")
                return i + n + 1
        if _number_value(c) is not None and punct in {"", ","} and self.low(i + 1) == "comma":
            parts = [str(_number_value(c))]
            j = i + 1
            while self.low(j) == "comma" and self.plain(j):
                if (v := _number_value(self.low(j + 1))) is None:
                    break
                parts.append(str(v))
                j += 2
                if not self.plain(j - 1):
                    break
            if len(parts) > 1:
                self.emit(",".join(parts) + self.words[j - 1][1], "comma")
                return j
        return None

    def read_number(self, i: int) -> tuple[str, int]:
        """(digits, words used) for number words from word i: "four twenty one" -> 421,
        "eight zero eight zero" -> 8080, "fifty thousand fifty one" -> 50051."""
        out, j = "", i
        while j < len(self.words) and (v := _number_value(self.low(j))) is not None:
            if self.low(j).isdigit():
                break
            j += 1
            if v in _TENS.values() and self.plain(j - 1) and 0 < _UNITS.get(self.low(j), 0) < 10:
                v, j = v + _UNITS[self.low(j)], j + 1
            if self.plain(j - 1) and self.low(j) == "hundred":
                v, j = v * 100, j + 1
                if self.plain(j - 1) and (rest := self.small(j))[1]:
                    v, j = v + rest[0], j + rest[1]
            if self.plain(j - 1) and self.low(j) == "thousand":
                v, j = v * 1000, j + 1
                if self.plain(j - 1) and (rest := self.small(j))[1]:
                    v, j = v + rest[0], j + rest[1]
            out += str(v)
            if not self.plain(j - 1):
                break
        return out, j - i

    def small(self, j: int) -> tuple[int, int]:
        """(value, words) for 1-99 at word j: "fifty one", "seven", "twelve"."""
        v = _number_value(self.low(j))
        if v is None or v == 0 or self.low(j).isdigit():
            return 0, 0
        if v in _TENS.values() and self.plain(j) and 0 < _UNITS.get(self.low(j + 1), 0) < 10:
            return v + _UNITS[self.low(j + 1)], 2
        return v, 1

    def joined_forms(self, core: str) -> str:
        """Fix what the ASR already glued: "canon.by", "commit-m", "up-d"."""
        stem, dot, ext = core.rpartition(".")
        if dot and stem and ext.lower() in MISHEARD_EXTENSIONS and _WORD.match(stem):
            self.events.append(f".{ext}")
            return f"{stem}.{MISHEARD_EXTENSIONS[ext.lower()]}"
        m = _JOINED_FLAG.match(core)
        if m and (m.group(1).lower() in COMMANDS or (self.command_seen and m.group(2).islower())):
            self.flag = True
            self.events.append(f"{m.group(1)}-{m.group(2)}")
            return f"{m.group(1)} -{m.group(2).lower()}"
        return core

    def path(self, i: int) -> int | None:  # noqa: PLR0912 - prefix, head, run, extension
        """A path run starting at word i; returns the next index if one was emitted."""
        c = self.low(i)
        prefix, j, leading = "", i, False
        if c == "dot" and self.plain(i):
            if self.low(i + 1) == "slash":
                prefix, j = ".", i + 1
            elif self.low(i + 1) == "dot" and self.plain(i + 1) and self.low(i + 2) == "slash":
                prefix, j = "..", i + 2
            else:
                return None
        parts: list[str] = []
        if self.low(j) != "slash":  # "A slash B": A must be an ordinary word
            head = self.words[j][0]
            if (
                not (self.plain(j) and _WORD.match(head))
                or self.commandish(head)
                or head.lower() in FUNCTION_WORDS
            ):
                return None
            parts.append(head)
            j += 1
        else:
            leading = True
        slashes = 0
        while self.low(j) == "slash" and self.plain(j) and j + 1 < len(self.words):
            word = self.words[j + 1][0]
            if not _WORD.match(word):
                break
            parts.append(word)
            slashes += 1
            j += 2
            if not self.plain(j - 1):
                break
        if slashes == 0:
            return None
        ext = ""
        if self.plain(j - 1) and self.low(j) == "dot" and self.plain(j):
            spoken = self.low(j + 1)
            if MISHEARD_EXTENSIONS.get(spoken, spoken) in EXTENSIONS:
                ext = "." + MISHEARD_EXTENSIONS.get(spoken, spoken)
                j += 2
        path = "/".join(parts) + ext + self.words[j - 1][1]
        prev = self.prev_core()
        if leading and not prefix and prev and self.prev_open() and _HOST_PORT.match(prev):
            self.out[-1] += "/" + path  # localhost:8000 slash health
            self.events.append("slash")
            return j
        at_command = not self.out or self.commandish(prev)
        if not (prefix or slashes >= 2 or ext or (leading and at_command)):
            return None
        root = prefix + "/" if (prefix or leading) else ""
        self.emit(root + path, "slash")
        return j


def apply(text: str, flags: Iterable[str] = ()) -> tuple[str, list[str]]:
    """(text with spoken symbols written as symbols, the rules that fired); ``flags``: the
    shortlist's flags (``--fix``), recognized as "dash-fix" without a command."""
    if not text.strip():
        return text, []
    return _Rewriter(text, flags).run()
