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
class ProviderConfig:
    """One ASR server in `[[providers]]`, tried in `priority` order (lower first, then file
    order) at the start of every dictation; see hark_client.providers."""

    address: str = ""  # host:port or unix:/path
    name: str = ""  # shown in `hark doctor` and history; "" => address
    priority: int = 0
    # Privacy: send the context shortlist (bias terms from screen, repo, agent sessions) to
    # this server. false => audio and the transcript only (cleanup gets the text and which
    # spans of it to protect); canonicalization against the shortlist stays local.
    send_context: bool = True
    # A health check slower than this skips the provider for this dictation (and bounds the
    # time spent finding out).
    max_latency_ms: int = 300
    tls_ca: str = ""
    tls_cert: str = ""
    tls_key: str = ""
    tls_server_name: str = ""
    model_id: str = ""  # "" => server default

    @property
    def label(self) -> str:
        return self.name or self.address


@dataclass
class ContextConfig:
    deadline_ms: int = 150  # SessionStart waits at most this long for context
    max_terms: int = 30  # research: short lists; the acoustic gate guards the rest


@dataclass
class GateConfig:
    """Acoustic anchor gate on biased results (see hark_client.gate)."""

    enabled: bool = True
    # Keep a biased span only if the anchor heard something this close to the term
    # (normalized edit distance on letters or a phonetic key, 0..1).
    max_distance: float = 0.5
    # ...and the anchor's least confident word there is below this (> 1: rule off).
    confident_anchor: float = 0.97


@dataclass
class CleanupConfig:
    enabled: bool = True  # global switch; profiles opt in individually
    # Whole Cleanup RPC; timeout => raw text. POC: ~0.6 s for ~200 edit tokens on Qwen3-1.7B.
    deadline_ms: int = 1000


@dataclass
class AudioConfig:
    device: str = ""  # PortAudio input device name or index; "" => default
    preroll_ms: int = 200  # audio kept from before hotkey-down (needs keep_open)
    keep_open: bool = True  # keep the mic stream running between dictations
    min_ms: int = 300  # shorter presses are cancelled
    min_speech_ms: int = 300  # less speech energy than this: discarded (0: off)
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
    # Leading/trailing space and mid-sentence lower case from the text around the caret
    # (hark_client.continuation). join_space is the older name; false turns it off too.
    smart_spacing: bool = True
    join_space: bool = True
    # Without a readable caret: hark's last paste + the keys typed since, forgotten after
    # this long without a key or paste in that window.
    join_window_ms: int = 60_000
    # Read the target field back after pasting (macOS AX, Linux AT-SPI): landed | missing |
    # not_text | unknown in history (recorded only).
    verify: bool = True


@dataclass
class LearnConfig:
    """Correction learning from edits right after a paste (opt-in; see hark_client.learn)."""

    enabled: bool = False
    window_ms: int = 30_000  # watch the pasted span this long (clamped to 30 s)
    poll_ms: int = 500
    promote_after: int = 2  # occurrences before a correction becomes a shortlist term (min 2)
    max_terms: int = 20  # learned terms added to every shortlist


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
class RetryConfig:
    # Audio + context of the last `keep` dictations under <history.dir>/retry (0700/0600),
    # independent of save_recordings, so `hark retry` can re-run a failed one. 0 = off.
    keep: int = 10


@dataclass
class IndicatorConfig:
    """Recording indicator: a small passive overlay (never takes focus or clicks)."""

    enabled: bool = True
    position: str = "bottom"  # bottom | top (centre of the screen with the pointer)


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
            cleanup=True,
            cleanup_profile="terminal",
        ),
        "chat": Profile(
            apps=[
                "com.tinyspeck.slackmacgap", "Slack", "com.hnc.Discord", "discord",
                "org.telegram.desktop", "ru.keepcoder.Telegram", "signal", "*Signal*",
                "net.whatsapp.WhatsApp",
            ],
            cleanup=True,
            cleanup_profile="chat",
        ),
        "prose": Profile(cleanup=True),
    }  # fmt: skip


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    paste: PasteConfig = field(default_factory=PasteConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    learn: LearnConfig = field(default_factory=LearnConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    indicator: IndicatorConfig = field(default_factory=IndicatorConfig)
    profiles: dict[str, Profile] = field(default_factory=default_profiles)
    providers: list[ProviderConfig] = field(default_factory=list[ProviderConfig])
    log_level: str = "INFO"

    def provider_configs(self) -> list[ProviderConfig]:
        """`[[providers]]`, else the single `[server]` (the configuration before providers)."""
        if self.providers:
            return list(self.providers)
        s = self.server
        return [
            ProviderConfig(
                address=s.address, tls_ca=s.tls_ca, tls_cert=s.tls_cert, tls_key=s.tls_key,
                tls_server_name=s.tls_server_name, model_id=s.model_id,
            )
        ]  # fmt: skip

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
INDICATOR_POSITIONS = ("bottom", "top")


def _type_ok(value: object, default: object) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
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
        elif key == "providers" and not where:
            _apply_providers(cast("Config", obj), value)
        elif dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"{path}: expected a table")
            _apply(cast("DataclassInstance", current), cast("dict[str, object]", value), path)
        elif _type_ok(value, current):
            # TOML `0` for a float setting
            setattr(
                obj,
                key,
                float(value) if isinstance(current, float) and isinstance(value, int) else value,
            )
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


def _apply_providers(cfg: Config, value: object) -> None:
    if not isinstance(value, list):
        raise ConfigError("providers: expected an array of tables ([[providers]])")
    for i, table in enumerate(cast("list[object]", value)):
        if not isinstance(table, dict):
            raise ConfigError(f"providers[{i}]: expected a table")
        prov = ProviderConfig()
        _apply(prov, cast("dict[str, object]", table), f"providers[{i}]")
        cfg.providers.append(prov)


def _validate_tls(where: str, ca: str, cert: str, key: str) -> None:
    if bool(cert) != bool(key):
        raise ConfigError(f"{where}tls_cert and {where}tls_key must be set together")
    if cert and not ca:
        raise ConfigError(f"{where}tls_cert needs {where}tls_ca")


def _validate_providers(providers: list[ProviderConfig]) -> None:
    labels: set[str] = set()
    for i, p in enumerate(providers):
        if not p.address:
            raise ConfigError(f"providers[{i}].address is required")
        if p.max_latency_ms < 1:
            raise ConfigError(f"providers[{i}].max_latency_ms must be >= 1")
        _validate_tls(f"providers[{i}].", p.tls_ca, p.tls_cert, p.tls_key)
        if p.label in labels:
            raise ConfigError(f"providers[{i}]: two providers named {p.label!r}")
        labels.add(p.label)


def validate(cfg: Config) -> None:
    if cfg.paste.method not in PASTE_METHODS:
        raise ConfigError(f"paste.method must be one of {', '.join(PASTE_METHODS)}")
    if cfg.hotkey.listener not in LISTENERS:
        raise ConfigError(f"hotkey.listener must be one of {', '.join(LISTENERS)}")
    if cfg.indicator.position not in INDICATOR_POSITIONS:
        raise ConfigError(f"indicator.position must be one of {', '.join(INDICATOR_POSITIONS)}")
    if "prose" not in cfg.profiles:
        raise ConfigError("profiles.prose is the fallback profile and cannot be removed")
    for key, v in (
        ("context.deadline_ms", cfg.context.deadline_ms),
        ("cleanup.deadline_ms", cfg.cleanup.deadline_ms),
        ("server.final_timeout_ms", cfg.server.final_timeout_ms),
        ("audio.preroll_ms", cfg.audio.preroll_ms),
        ("paste.join_window_ms", cfg.paste.join_window_ms),
        ("audio.min_speech_ms", cfg.audio.min_speech_ms),
        ("learn.window_ms", cfg.learn.window_ms),
        ("learn.max_terms", cfg.learn.max_terms),
        ("retry.keep", cfg.retry.keep),
    ):
        if v < 0:
            raise ConfigError(f"{key} must be >= 0")
    _validate_tls("server.", cfg.server.tls_ca, cfg.server.tls_cert, cfg.server.tls_key)
    _validate_providers(cfg.providers)
    if not 0 <= cfg.gate.max_distance <= 1:
        raise ConfigError("gate.max_distance must be in [0, 1]")
    if cfg.gate.confident_anchor < 0:
        raise ConfigError("gate.confident_anchor must be >= 0")
    if cfg.learn.poll_ms < 100:
        raise ConfigError("learn.poll_ms must be >= 100")
    if cfg.learn.promote_after < 2:
        raise ConfigError("learn.promote_after must be >= 2")


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
