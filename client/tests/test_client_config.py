from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from skriv_client import ctl
from skriv_client.__main__ import main
from skriv_client.config import Config, ConfigError, load
from skriv_client.context import FocusInfo

if TYPE_CHECKING:
    from pathlib import Path

FULL = """
log_level = "DEBUG"
[[providers]]
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
    assert [p.address for p in cfg.providers] == ["desktop.tailnet:50051"]  # replaces the default
    assert (cfg.cleanup.enabled, cfg.cleanup.deadline_ms) == (False, 300)
    assert (cfg.audio.preroll_ms, cfg.audio.keep_open, cfg.paste.method) == (0, False, "wtype")
    # floats accept TOML integers
    assert (cfg.gate.max_distance, cfg.gate.confident_anchor) == (0.0, 0.9)
    assert isinstance(cfg.gate.max_distance, float)
    assert cfg.context.deadline_ms == 150  # untouched default
    # Fields not given keep the built-in profile's values; new profiles are appended.
    assert cfg.profiles["terminal"].apps == ["my-term"]
    assert cfg.profiles["terminal"].terminal
    assert list(cfg.profiles) == ["terminal", "prose", "agent"]
    assert cfg.profiles["agent"].cleanup_profile == "commit_message"


@pytest.mark.parametrize(
    ("focus", "profile"),
    [
        (FocusInfo(app_id="com.mitchellh.ghostty"), "terminal"),
        (FocusInfo(app_id="NET.KovidGoyal.Kitty"), "terminal"),  # globs are case-insensitive
        (FocusInfo(app="Slack", app_id=""), "prose"),  # no built-in chat profile
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
        ("[server]\naddress = 'a:1'\n", "unknown config key 'server.address'"),
        ("[[providers]]\naddress = 'a:1'\ntls_cert = 'a'\n", "].tls_key must be set together"),
        ("providers = []\n", "providers: at least one"),
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


WORDS_CONFIG = """\
# mine
[context]
max_terms = 6   # two [[words]] at most

[[words]]   # needs help
word = "Hyprland"
spoken = ["hyper land"]

# --- feedback ---
[feedback]
sounds = false
"""


def test_words_command_edits_the_list_as_text_and_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    sent: list[str] = []

    def send(line: str, timeout: float = 2.0) -> str:
        sent.append(line)
        return "ok reloaded"

    monkeypatch.setattr(ctl, "send", send)
    cfg = tmp_path / "c.toml"
    cfg.write_text(WORDS_CONFIG)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S603, S607
    monkeypatch.chdir(repo)

    def words(*args: str) -> int:
        return main(["--config", str(cfg), "words", *args])

    assert words("add", "hyperland", "Hyprland") == 0  # one more spoken form
    assert words("add", "cube control", "kubectl") == 0
    assert sent == ["reload", "reload"]
    text = cfg.read_text()
    assert text.startswith(
        WORDS_CONFIG.split("[[words]]", maxsplit=1)[0]
    )  # comments and layout kept
    assert "# --- feedback ---\n[feedback]\nsounds = false\n" in text
    assert [(w.word, w.spoken) for w in load(cfg).words] == [
        ("Hyprland", ["hyper land", "hyperland"]),
        ("kubectl", ["cube control"]),
    ]
    capsys.readouterr()
    assert words("add", "pie", "pi") == 1  # 2 of 2
    assert "the list is full (2 of 2" in capsys.readouterr().err
    assert words("add", "--repo", "pie", "pi") == 1  # the repository's share the cap
    assert words("remove", "Hyprland") == 0
    assert words("remove", "Hyprland") == 1
    assert words("add", "--repo", "pie", "pi") == 0
    assert (repo / ".hark/words.toml").read_text() == '[[words]]\nword = "pi"\nspoken = ["pie"]\n'
    assert sent == ["reload"] * 3  # the repository's list is read at each dictation
    assert [w.word for w in load(cfg).words] == ["kubectl"]
    assert "# --- feedback ---" in cfg.read_text()
    capsys.readouterr()
    assert words() == 0
    out = capsys.readouterr().out
    assert "  kubectl  <- 'cube control'" in out
    assert "  pi  <- 'pie'" in out

    cfg.write_text('words = [{word = "x"}]\n')  # not editable as [[words]] tables
    assert words("add", "y") == 1
    assert "not changed" in capsys.readouterr().err
    assert cfg.read_text() == 'words = [{word = "x"}]\n'  # restored
