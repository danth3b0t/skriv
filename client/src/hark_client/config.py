"""Client configuration: ``~/.config/hark/config.toml`` (or ``--config``).

Every table and key is optional; unknown keys and wrong types are errors so typos
do not silently fall back to defaults. ``[profiles.<name>]`` tables are merged over
the built-in profiles (same name => replaced field by field).
"""

from __future__ import annotations

import dataclasses
import fnmatch
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from _typeshed import DataclassInstance

    from hark_client.context import FocusInfo


class ConfigError(ValueError):
    pass


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "hark" / "config.toml"


def default_data_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / "hark")


@dataclass
class ServerConfig:
    address: str = "127.0.0.1:50051"  # host:port or unix:/path
    tls_ca: str = ""  # CA bundle; set => TLS
    tls_cert: str = ""  # client cert (mTLS)
    tls_key: str = ""
    tls_server_name: str = ""  # override the expected server name
    model_id: str = ""  # "" => server default
    language: str = ""  # BCP-47; "" => server detects
    finalize_budget_ms: int = 0  # 0 => server default
    final_timeout_ms: int = 5000  # key-up -> FinalResult, client-side give-up


@dataclass
class ContextConfig:
    deadline_ms: int = 150  # SessionStart waits at most this long for context
    max_terms: int = 60


@dataclass
class CleanupConfig:
    enabled: bool = True  # global switch; profiles opt in individually
    deadline_ms: int = 600  # whole Cleanup RPC; timeout => raw text


@dataclass
class AudioConfig:
    device: str = ""  # PortAudio input device name or index; "" => default
    preroll_ms: int = 200  # audio kept from before hotkey-down (needs keep_open)
    keep_open: bool = True  # keep the mic stream running between dictations
    min_ms: int = 300  # shorter presses are cancelled
    max_ms: int = 120_000


@dataclass
class HotkeyConfig:
    key: str = "alt_r"  # pynput Key name (alt_r = Right Option) or a single character
    listener: str = "auto"  # auto | pynput | none (the ctl socket is always on)


@dataclass
class PasteConfig:
    method: str = "auto"  # auto | macos | hyprland | wtype | x11 | clipboard | dry-run
    restore_clipboard: bool = True
    restore_delay_ms: int = 250
    check_focus: bool = True


@dataclass
class FeedbackConfig:
    sounds: bool = True
    notify: bool = True


@dataclass
class HistoryConfig:
    dir: str = field(default_factory=default_data_dir)
    enabled: bool = True
    save_recordings: bool = False  # opt-in: WAV + context snapshot + texts per utterance


@dataclass
class Profile:
    apps: list[str] = field(
        default_factory=list[str]
    )  # globs on app_id / app name, case-insensitive
    terminal: bool = False  # single line, Ctrl+Shift+V on Linux, never Enter
    cleanup: bool = False
    cleanup_profile: str = "prose"  # server CleanupProfile.id


def default_profiles() -> dict[str, Profile]:
    return {
        "terminal": Profile(
            apps=[
                "com.mitchellh.ghostty", "com.apple.Terminal", "com.googlecode.iterm2",
                "net.kovidgoyal.kitty", "kitty", "org.alacritty", "Alacritty", "foot",
                "footclient", "org.wezfurlong.wezterm", "com.github.wez.wezterm",
                "dev.warp.Warp-Stable", "*ghostty*", "*terminal*",
            ],
            terminal=True,
            cleanup=False,
        ),
        "chat": Profile(
            apps=[
                "com.tinyspeck.slackmacgap", "Slack", "com.hnc.Discord", "discord",
                "org.telegram.desktop", "ru.keepcoder.Telegram", "signal", "*Signal*",
                "net.whatsapp.WhatsApp",
            ],
            cleanup=True,
        ),
        "prose": Profile(cleanup=True),
    }  # fmt: skip


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    paste: PasteConfig = field(default_factory=PasteConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    profiles: dict[str, Profile] = field(default_factory=default_profiles)
    log_level: str = "INFO"

    def profile_for(self, focus: FocusInfo) -> str:
        """First profile (in table order) whose `apps` glob matches the focused app id or
        name; else "terminal" if the context engine says it is a terminal; else "prose"."""
        for name, prof in self.profiles.items():
            for pattern in prof.apps:
                p = pattern.casefold()
                if any(
                    v and fnmatch.fnmatchcase(v.casefold(), p) for v in (focus.app_id, focus.app)
                ):
                    return name
        if focus.is_terminal and "terminal" in self.profiles:
            return "terminal"
        return "prose"

    def profile(self, name: str) -> Profile:
        try:
            return self.profiles[name]
        except KeyError:
            raise ConfigError(
                f"unknown profile {name!r} (have: {', '.join(self.profiles)})"
            ) from None


PASTE_METHODS = ("auto", "macos", "hyprland", "wtype", "x11", "clipboard", "dry-run")
LISTENERS = ("auto", "pynput", "none")


def _type_ok(value: object, default: object) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        items = cast("list[object]", value) if isinstance(value, list) else None
        return items is not None and all(isinstance(v, str) for v in items)
    return False


def _apply(obj: DataclassInstance, data: Mapping[str, object], where: str) -> None:
    names = {f.name for f in dataclasses.fields(obj)}
    for key, value in data.items():
        path = f"{where}.{key}" if where else key
        if key not in names:
            raise ConfigError(f"unknown config key {path!r}")
        current = cast("object", getattr(obj, key))
        if key == "profiles" and not where:
            _apply_profiles(cast("Config", obj), value)
        elif dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"{path}: expected a table")
            _apply(cast("DataclassInstance", current), cast("dict[str, object]", value), path)
        elif _type_ok(value, current):
            setattr(obj, key, value)
        else:
            raise ConfigError(
                f"{path}: expected {type(current).__name__}, got {type(value).__name__}"
            )


def _apply_profiles(cfg: Config, value: object) -> None:
    if not isinstance(value, dict):
        raise ConfigError("profiles: expected a table of tables")
    for name, table in cast("dict[str, object]", value).items():
        if not isinstance(table, dict):
            raise ConfigError(f"profiles.{name}: expected a table")
        prof = cfg.profiles.setdefault(name, Profile())
        _apply(prof, cast("dict[str, object]", table), f"profiles.{name}")


def validate(cfg: Config) -> None:
    if cfg.paste.method not in PASTE_METHODS:
        raise ConfigError(f"paste.method must be one of {', '.join(PASTE_METHODS)}")
    if cfg.hotkey.listener not in LISTENERS:
        raise ConfigError(f"hotkey.listener must be one of {', '.join(LISTENERS)}")
    if "prose" not in cfg.profiles:
        raise ConfigError("profiles.prose is the fallback profile and cannot be removed")
    for key, v in (
        ("context.deadline_ms", cfg.context.deadline_ms),
        ("cleanup.deadline_ms", cfg.cleanup.deadline_ms),
        ("server.final_timeout_ms", cfg.server.final_timeout_ms),
        ("audio.preroll_ms", cfg.audio.preroll_ms),
    ):
        if v < 0:
            raise ConfigError(f"{key} must be >= 0")
    if bool(cfg.server.tls_cert) != bool(cfg.server.tls_key):
        raise ConfigError("server.tls_cert and server.tls_key must be set together")
    if cfg.server.tls_cert and not cfg.server.tls_ca:
        raise ConfigError("server.tls_cert needs server.tls_ca")


def load(path: str | Path | None = None) -> Config:
    """Load `path`, or the default path if it exists, else built-in defaults."""
    cfg = Config()
    explicit = path is not None
    p = Path(path) if path is not None else default_config_path()
    if p.exists():
        try:
            data = tomllib.loads(p.read_text())
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{p}: {e}") from e
        _apply(cfg, data, "")
    elif explicit:
        raise ConfigError(f"{p}: no such file")
    validate(cfg)
    return cfg


def expand(path: str) -> Path:
    return Path(os.path.expandvars(path)).expanduser()
