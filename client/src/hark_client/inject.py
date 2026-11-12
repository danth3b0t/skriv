"""Text injection: clipboard + synthetic paste shortcut.

- macOS: NSPasteboard (saved and restored) + CGEvent Cmd+V from a private event source.
- Linux Wayland: wl-copy, then on Hyprland a shortcut sent to the window captured at key-down: with a Lua
  config (>= 0.55) one `hyprctl repl` chunk re-checks the active window inside the compositor and sends
  the shortcut to that address; with hyprlang (<= 0.54) `dispatch sendshortcut MODS, V, address:...`.
  Without a known target: an untargeted shortcut. Else wtype.
- Linux X11: xclip + xdotool.
- Terminals get Ctrl+Shift+V on Linux. Nothing ever sends Enter.
- A paste command that may have been delivered (it timed out) is never followed by another attempt: the
  text stays in the clipboard. Only a command that certainly did nothing falls through to the next one.
- The clipboard is restored `restore_delay_s` after the paste (the target reads it asynchronously). A newer
  paste before then takes over the saved content, so back-to-back dictations restore the user's clipboard,
  not hark's previous text.
- dry-run prints instead of touching the clipboard; `clipboard` copies without pasting.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from hark_client.config import PasteConfig
    from hark_client.macos import PasteboardSnapshot

log = logging.getLogger("hark.inject")

TIMED_OUT = 124  # Runner exit code for a command that did not finish in time (as timeout(1))


class Runner(Protocol):
    def __call__(
        self, argv: Sequence[str], *, stdin: str | None = None, capture: bool = True
    ) -> tuple[int, str]:
        """Run argv; returns (exit code, stdout or ""); TIMED_OUT when it did not finish."""
        ...


def run_cmd(
    argv: Sequence[str], *, stdin: str | None = None, capture: bool = True
) -> tuple[int, str]:
    try:
        p = subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(argv),
            input=stdin,
            text=True,
            # wl-copy/xclip fork a child that keeps serving the selection; a captured
            # stdout pipe would stay open and block until the selection changes.
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return TIMED_OUT, str(e)
    except OSError as e:
        return 127, str(e)
    return p.returncode, p.stdout if capture else ""


@dataclass(frozen=True, slots=True)
class PasteResult:
    ok: bool
    method: str
    detail: str = ""
    focus_changed: bool = False  # the target lost focus; the text is left in the clipboard
    in_clipboard: bool = False  # not pasted (or not known to be), but the text is in the clipboard


class Injector(Protocol):
    name: str

    def paste(self, text: str, *, terminal: bool, target: str = "") -> PasteResult:
        """target: FocusInfo.window_id captured at key-down ("" = unknown)."""
        ...

    def copy(self, text: str) -> bool: ...


class DryRunInjector:
    name = "dry-run"

    def __init__(self, out: Callable[[str], None] = print) -> None:
        self.out = out
        self.pasted: list[tuple[str, bool]] = []
        self.copied: list[str] = []

    def paste(self, text: str, *, terminal: bool, target: str = "") -> PasteResult:
        self.pasted.append((text, terminal))
        keys = "terminal paste" if terminal else "paste"
        self.out(f"[dry-run] would {keys}: {text!r}")
        return PasteResult(ok=True, method=self.name, detail="dry run")

    def copy(self, text: str) -> bool:
        self.copied.append(text)
        self.out(f"[dry-run] would copy: {text!r}")
        return True


class ClipboardOnlyInjector:
    """paste.method = "clipboard": copy, let the user paste."""

    name = "clipboard"

    def __init__(self, inner: Injector) -> None:
        self.inner = inner

    def paste(self, text: str, *, terminal: bool, target: str = "") -> PasteResult:
        ok = self.inner.copy(text)
        return PasteResult(
            ok=ok, method=self.name, detail="copied" if ok else "copy failed", in_clipboard=ok
        )

    def copy(self, text: str) -> bool:
        return self.inner.copy(text)


_HYPR_ADDRESS_RE = re.compile(r"0x([0-9a-fA-F]{1,16})")


def hypr_lua_paste(mods: str, address_hex: str) -> str:
    """Lua expression for `hyprctl repl`: re-check the active window, then send the shortcut to it.

    Returns 'paste_sent', 'dispatch_refused' or 'focus_changed:<active address>'. Addresses are
    compared as lower-case hex without 0x (a number is formatted as hex), so a format change
    between Hyprland versions shows up as a mismatch in the log, not as a silent wrong paste.
    """
    return (
        "(function() local w = hl.get_active_window(); local a = w and w.address; "
        "if type(a) == 'number' then a = string.format('%x', a) end; "
        "a = tostring(a or ''):lower():gsub('^0x', ''); "
        f"if a ~= '{address_hex}' then return 'focus_changed:' .. a end; "
        f"local r = hl.dispatch(hl.dsp.send_shortcut({{ mods = '{mods}', key = 'V', "
        f"window = 'address:0x{address_hex}' }})); "
        "if type(r) == 'table' and not r.ok then return 'dispatch_refused' end; "
        "return 'paste_sent' end)()"
    )


class _Restorer[T]:
    """Restores the clipboard after a paste; a newer paste takes over the saved content."""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self._lock = threading.Lock()
        self._gen = 0
        self._saved: T | None = None  # saved by a paste whose restore is still pending

    def begin(self, save: Callable[[], T | None]) -> tuple[int, T | None]:
        """A paste starts: (its generation, the user's clipboard: pending one or `save()`)."""
        with self._lock:
            self._gen += 1
            gen, pending = self._gen, self._saved
            self._saved = None
        return gen, pending if pending is not None else save()

    def schedule(
        self, gen: int, saved: T, still_ours: Callable[[], bool], put: Callable[[T], object]
    ) -> None:
        with self._lock:
            if gen != self._gen:
                return
            self._saved = saved

        def restore() -> None:
            time.sleep(self.delay_s)
            with self._lock:
                if gen != self._gen:
                    return  # a newer paste owns the saved content now
                self._saved = None
            if still_ours():  # the user has not copied something else
                put(saved)

        threading.Thread(target=restore, daemon=True, name="hark-clip-restore").start()


class LinuxInjector:
    """mode: "hyprland" | "wtype" (Wayland) | "x11"."""

    def __init__(
        self,
        mode: str,
        *,
        restore: bool = True,
        restore_delay_s: float = 0.8,
        run: Runner = run_cmd,
    ) -> None:
        self.name = mode
        self.wayland = mode != "x11"
        self.restore = restore
        self.restore_delay_s = restore_delay_s
        self.run = run
        self._hypr_syntax: str | None = None  # "lua" | "hyprlang" once one has worked
        self._restorer: _Restorer[str] = _Restorer(restore_delay_s)

    def copy(self, text: str) -> bool:
        argv = (
            ["wl-copy", "--type", "text/plain;charset=utf-8"]
            if self.wayland
            else ["xclip", "-selection", "clipboard", "-in"]
        )
        return self.run(argv, stdin=text, capture=False)[0] == 0

    def read_clipboard(self) -> str | None:
        argv = (
            ["wl-paste", "--no-newline", "--type", "text/plain"]
            if self.wayland
            else ["xclip", "-selection", "clipboard", "-out"]
        )
        code, out = self.run(argv)
        return out if code == 0 else None

    def _hypr_targeted(self, *, terminal: bool, address_hex: str) -> tuple[str, str]:
        """(outcome, detail): "sent" | "focus_changed" | "unknown" (may have been sent) |
        "unsupported" (neither syntax did anything)."""
        order = ["lua", "hyprlang"]
        if self._hypr_syntax == "hyprlang":
            order.reverse()
        for syntax in order:
            if syntax == "lua":
                mods = "CTRL + SHIFT" if terminal else "CTRL"
                how = f"hyprctl repl send_shortcut {mods}+V to 0x{address_hex}"
                code, out = self.run(["hyprctl", "repl", hypr_lua_paste(mods, address_hex)])
                verdict = out.strip().strip("\"'")
                if code == 0 and verdict == "paste_sent":
                    self._hypr_syntax = "lua"
                    return "sent", how
                if code == 0 and verdict.startswith("focus_changed:"):
                    self._hypr_syntax = "lua"
                    active = verdict.split(":", 1)[1]
                    return (
                        "focus_changed",
                        f"focus changed before the paste (active window 0x{active})",
                    )
                log.debug("hyprctl repl paste: %s", out.strip()[:200])
            else:
                mods = "CTRL SHIFT" if terminal else "CTRL"
                how = f"hyprctl sendshortcut {mods}+V to 0x{address_hex}"
                arg = f"{mods}, V, address:0x{address_hex}"
                code, out = self.run(["hyprctl", "dispatch", "sendshortcut", arg])
                if code == 0 and out.strip() == "ok":
                    self._hypr_syntax = "hyprlang"
                    return "sent", how
                log.debug("hyprctl sendshortcut paste: %s", out.strip()[:200])
            if code == TIMED_OUT:
                return "unknown", how
        return "unsupported", ""

    def _send_keys(self, *, terminal: bool) -> tuple[str, str]:
        """(outcome, detail): "sent" | "unknown" (may have been sent) | "failed"."""
        if self.name == "hyprland":
            mods = "CTRL SHIFT" if terminal else "CTRL"
            # Hyprland <= 0.54 (hyprlang dispatcher syntax)
            code, out = self.run(["hyprctl", "dispatch", "sendshortcut", f"{mods}, V,"])
            if code == 0 and out.strip() == "ok":
                return "sent", f"hyprctl sendshortcut {mods}+V"
            if code == TIMED_OUT:
                return "unknown", f"hyprctl sendshortcut {mods}+V"
            # Hyprland >= 0.55 (Lua dispatchers)
            lua_mods = "CTRL + SHIFT" if terminal else "CTRL"
            lua = f'hl.dsp.send_shortcut({{ mods = "{lua_mods}", key = "V" }})'
            code, out = self.run(["hyprctl", "dispatch", lua])
            if code == 0 and out.strip() == "ok":
                return "sent", f"hyprctl send_shortcut {lua_mods}+V"
            if code == TIMED_OUT:
                return "unknown", f"hyprctl send_shortcut {lua_mods}+V"
            log.debug("hyprctl sendshortcut failed (%s); trying wtype", out.strip())
        if self.wayland:
            shift_down = ["-M", "shift"] if terminal else []
            shift_up = ["-m", "shift"] if terminal else []
            argv = ["wtype", "-M", "ctrl", *shift_down, "-k", "v", *shift_up, "-m", "ctrl"]
        else:
            argv = ["xdotool", "key", "--clearmodifiers", "ctrl+shift+v" if terminal else "ctrl+v"]
        code, _ = self.run(argv, capture=False)
        return {0: "sent", TIMED_OUT: "unknown"}.get(code, "failed"), " ".join(argv)

    def paste(self, text: str, *, terminal: bool, target: str = "") -> PasteResult:
        gen, previous = self._restorer.begin(self.read_clipboard if self.restore else lambda: None)
        if not self.copy(text):
            return PasteResult(ok=False, method=self.name, detail="clipboard write failed")
        outcome, how = "unsupported", ""
        m = _HYPR_ADDRESS_RE.fullmatch(target)
        if self.name == "hyprland" and m is not None:
            outcome, how = self._hypr_targeted(terminal=terminal, address_hex=m.group(1).lower())
        if outcome == "focus_changed":
            return PasteResult(
                ok=False, method=self.name, detail=f"{how}; text left in clipboard",
                focus_changed=True, in_clipboard=True,
            )  # fmt: skip
        if outcome == "unsupported":
            outcome, how = self._send_keys(terminal=terminal)
        if outcome == "unknown":
            return PasteResult(
                ok=False, method=self.name,
                detail=f"{how} timed out (may have pasted; not retried); text left in clipboard",
                in_clipboard=True,
            )  # fmt: skip
        if outcome != "sent":
            return PasteResult(
                ok=False, method=self.name, detail=f"{how} failed; text left in clipboard",
                in_clipboard=True,
            )  # fmt: skip
        if previous is not None:
            self._restorer.schedule(gen, previous, lambda: self.read_clipboard() == text, self.copy)
        return PasteResult(ok=True, method=self.name, detail=how)


class MacInjector:
    name = "macos"

    def __init__(self, *, restore: bool = True, restore_delay_s: float = 0.8) -> None:
        self.restore = restore
        self.restore_delay_s = restore_delay_s
        self._restorer: _Restorer[PasteboardSnapshot] = _Restorer(restore_delay_s)

    def copy(self, text: str) -> bool:
        if sys.platform != "darwin":
            return False
        from hark_client import macos  # noqa: PLC0415 - pyobjc only on macOS

        macos.set_clipboard_text(text)
        return True

    def paste(self, text: str, *, terminal: bool, target: str = "") -> PasteResult:
        if sys.platform != "darwin":
            return PasteResult(ok=False, method=self.name, detail="not macOS")
        from hark_client import macos  # noqa: PLC0415

        gen, saved = self._restorer.begin(macos.save_pasteboard if self.restore else lambda: None)
        change = macos.set_clipboard_text(text)
        if not macos.can_post_events():
            return PasteResult(
                ok=False, method=self.name,
                detail="no Accessibility permission to post Cmd+V; text left in clipboard",
                in_clipboard=True,
            )  # fmt: skip
        macos.send_cmd_v()
        if saved is not None:
            self._restorer.schedule(
                gen, saved, lambda: macos.change_count() == change, macos.restore_pasteboard
            )
        return PasteResult(ok=True, method=self.name, detail="Cmd+V")


def detect_method(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> str:
    env = os.environ if env is None else env
    if platform == "darwin":
        return "macos"
    if env.get("WAYLAND_DISPLAY"):
        return "hyprland" if env.get("HYPRLAND_INSTANCE_SIGNATURE") else "wtype"
    if env.get("DISPLAY"):
        return "x11"
    return "dry-run"


def make_injector(
    cfg: PasteConfig, *, dry_run: bool = False, out: Callable[[str], None] = print
) -> Injector:
    method = "dry-run" if dry_run else cfg.method
    if method == "dry-run":
        return DryRunInjector(out)
    platform_method = detect_method()
    if method == "auto":
        method = platform_method
    if platform_method == "dry-run" and method in ("dry-run", "clipboard"):
        log.warning("no display server found; paste is a dry run")
        return DryRunInjector(out)
    delay = cfg.restore_delay_ms / 1000

    def platform_injector(m: str) -> Injector:
        if m == "macos":
            return MacInjector(restore=cfg.restore_clipboard, restore_delay_s=delay)
        return LinuxInjector(m, restore=cfg.restore_clipboard, restore_delay_s=delay)

    if method == "clipboard":
        return ClipboardOnlyInjector(platform_injector(platform_method))
    return platform_injector(method)
