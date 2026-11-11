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
  word (COMMANDS, or a flag).
- "A slash B ..." -> "A/B...": with 2+ slashes, a final "dot EXT", or a leading
  slash at the start or after a command ("cd slash tmp" -> "cd /tmp"). One slash
  between ordinary words stays a word ("use the slash command").
- "colon" is glued to the previous word as ":".
- ASR-joined forms: "x.by" -> "x.py", "commit-m" -> "commit -m".
- When a flag was produced and the text starts with a capitalized command, it
  is lower-cased ("Git commit -m" -> "git commit -m").
"""

from __future__ import annotations

import re

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
_TRAILING = ".,;:!?"
_WORD = re.compile(r"[\w][\w.-]*\Z")
_JOINED_FLAG = re.compile(r"([A-Za-z]+)-([A-Za-z])\Z")


def _split(token: str) -> tuple[str, str]:
    core = token.rstrip(_TRAILING)
    return (core, token[len(core) :]) if core else (token, "")


class _Rewriter:
    def __init__(self, text: str) -> None:
        self.words = [_split(t) for t in text.split()]
        self.out: list[str] = []
        self.events: list[str] = []
        self.flag = False

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
            i = self.step(i)
        if self.flag and self.out and self.out[0][:1].isupper() and self.out[0].lower() in COMMANDS:
            self.out[0] = self.out[0].lower()
        return " ".join(self.out), self.events

    def step(self, i: int) -> int:  # noqa: PLR0911 - one return per rule
        core, punct = self.words[i]
        c = core.lower()
        if (n := self.path(i)) is not None:
            return n
        if c == "dash" and self.plain(i) and self.low(i + 1) == "dash" and self.plain(i + 1):
            nxt = self.low(i + 2)
            if _WORD.match(nxt) and not nxt.isdigit():
                self.flag = True
                self.emit("--" + nxt + self.words[i + 2][1], f"dash dash {nxt}")
                return i + 3
        if c == "dash" and self.plain(i) and self.commandish(self.prev_core()):
            nxt = self.low(i + 1)
            if len(nxt) == 1 and nxt.isalpha():
                self.flag = True
                self.emit("-" + nxt + self.words[i + 1][1], f"dash {nxt}")
                return i + 2
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
            self.out[-1] += ":" + punct
            self.events.append("colon")
            return i + 1
        self.out.append(self.joined_forms(core) + punct)
        return i + 1

    def joined_forms(self, core: str) -> str:
        """Fix what the ASR already glued: "canon.by", "commit-m"."""
        stem, dot, ext = core.rpartition(".")
        if dot and stem and ext.lower() in MISHEARD_EXTENSIONS and _WORD.match(stem):
            self.events.append(f".{ext}")
            return f"{stem}.{MISHEARD_EXTENSIONS[ext.lower()]}"
        m = _JOINED_FLAG.match(core)
        if m and m.group(1).lower() in COMMANDS:
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
            if not (self.plain(j) and _WORD.match(self.words[j][0])) or self.commandish(
                self.words[j][0]
            ):
                return None
            parts.append(self.words[j][0])
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
        at_command = not self.out or self.commandish(self.prev_core())
        if not (prefix or slashes >= 2 or ext or (leading and at_command)):
            return None
        root = prefix + "/" if (prefix or leading) else ""
        self.emit(root + "/".join(parts) + ext + self.words[j - 1][1], "slash")
        return j


def apply(text: str) -> tuple[str, list[str]]:
    """(text with spoken symbols written as symbols, the rules that fired)."""
    if not text.strip():
        return text, []
    return _Rewriter(text).run()
