from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from hark_client.config import Config, ConfigError, load
from hark_client.context import FocusInfo

if TYPE_CHECKING:
    from pathlib import Path

FULL = """
log_level = "DEBUG"
[server]
address = "desktop.tailnet:50051"
tls_ca = "~/ca.pem"
tls_cert = "~/me.pem"
tls_key = "~/me.key"
[cleanup]
enabled = false
deadline_ms = 300
[audio]
preroll_ms = 0
keep_open = false
[paste]
method = "wtype"
[history]
save_recordings = true
[gate]
max_distance = 0
confident_anchor = 0.9
[profiles.terminal]
apps = ["my-term"]
[profiles.agent]
apps = ["*pi-agent*"]
terminal = true
cleanup = true
cleanup_profile = "commit_message"
"""


def test_full_config_overrides_defaults_and_merges_profiles(tmp_path: Path):
    p = tmp_path / "c.toml"
    p.write_text(FULL)
    cfg = load(p)
    assert cfg.server.address == "desktop.tailnet:50051"
    assert (cfg.cleanup.enabled, cfg.cleanup.deadline_ms) == (False, 300)
    assert (cfg.audio.preroll_ms, cfg.audio.keep_open, cfg.paste.method) == (0, False, "wtype")
    assert cfg.history.save_recordings
    # floats accept TOML integers
    assert (cfg.gate.max_distance, cfg.gate.confident_anchor) == (0.0, 0.9)
    assert isinstance(cfg.gate.max_distance, float)
    assert cfg.context.deadline_ms == 150  # untouched default
    # Fields not given keep the built-in profile's values; new profiles are appended.
    assert cfg.profiles["terminal"].apps == ["my-term"]
    assert cfg.profiles["terminal"].terminal
    assert list(cfg.profiles) == ["terminal", "chat", "prose", "agent"]
    assert cfg.profiles["agent"].cleanup_profile == "commit_message"


@pytest.mark.parametrize(
    ("focus", "profile"),
    [
        (FocusInfo(app_id="com.mitchellh.ghostty"), "terminal"),
        (FocusInfo(app="Slack", app_id=""), "chat"),
        (FocusInfo(app_id="org.Telegram.Desktop"), "chat"),  # globs are case-insensitive
        (FocusInfo(app_id="org.example.unknown", is_terminal=True), "terminal"),
        (FocusInfo(app_id="com.apple.Notes"), "prose"),
        (FocusInfo(), "prose"),
    ],
)
def test_profile_selection(focus: FocusInfo, profile: str):
    assert Config().profile_for(focus) == profile


@pytest.mark.parametrize(
    ("toml", "error"),
    [
        ("[server]\nadress = 'x'\n", "unknown config key 'server.adress'"),
        ("[audio]\nmin_ms = true\n", "audio.min_ms: expected int, got bool"),
        ("[profiles.terminal]\napps = 'kitty'\n", "profiles.terminal.apps: expected list"),
        ("[paste]\nmethod = 'xdotool'\n", "paste.method must be one of"),
        ("[gate]\nmax_distance = 'near'\n", "gate.max_distance: expected float, got str"),
        ("[gate]\nmax_distance = 1.5\n", "gate.max_distance must be in"),
        ("[server]\ntls_cert = 'a'\n", "tls_cert and server.tls_key"),
        ("server = 1\n", "server: expected a table"),
        ("words = 'Hyprland'\n", "words: expected an array of tables"),
        ("[[words]]\nspoken = ['hyper land']\n", "word is required"),
        ("[[words]]\nword = 'Hyprland'\nspoken = [' ']\n", "empty spoken form"),
        ("[[words]]\nword = 'kubectl'\n[[words]]\nword = 'kubectl'\n", "'kubectl' is listed twice"),
        ("[[words]]\nword = 'Hyprland'\nsaid = ['x']\n", "0].said'"),
        (
            "[context]\nmax_terms = 6\n"
            + "[[words]]\nword = 'a1'\n[[words]]\nword = 'b2'\n[[words]]\nword = 'c3'\n",
            "3 entries, at most 2",
        ),
        ("[server\n", "c.toml"),
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, toml: str, error: str):
    p = tmp_path / "c.toml"
    p.write_text(toml)
    with pytest.raises(ConfigError, match=error.replace(".", r"\.")):
        load(p)


def test_missing_explicit_config_is_an_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="no such file"):
        load(tmp_path / "nope.toml")
