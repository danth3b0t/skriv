"""Recording indicator: the pill per fact (design/SPEC.md §4), the state machine (fake backend +
clock), daemon hooks, the theme palette, macOS panel focus safety."""
# pyright: reportAny=false, reportExplicitAny=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from skriv_client.cleanup import CleanupOutcome
from skriv_client.indicator import WORKING, Indicator, View, outcome_view, theme
from skriv_client.pipeline import Result

from .conftest import pcm, snapshot, wait_idle

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from skriv_client.daemon import Controller
    from skriv_client.inject import DryRunInjector

    from .conftest import FakeContext

TOKENS = Path(__file__).parents[2] / "design" / "tokens" / "omarchy-skriv-tokens.json"
KEY = "Super Alt D"


class FakeBackend:
    def __init__(self) -> None:
        self.rendered: list[View | None] = []

    def render(self, view: View | None) -> None:
        self.rendered.append(view)

    def run(self, stop: threading.Event, tick: Callable[[], None]) -> None:
        raise AssertionError("not used")

    def states(self) -> list[str | None]:
        """Rendered states, consecutive repeats (level redraws) collapsed."""
        out: list[str | None] = []
        for v in self.rendered:
            s = None if v is None else v.state
            if not out or out[-1] != s:
                out.append(s)
        return out


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def make(*, recovery: str = "", limit_s: float = 0.0) -> tuple[Indicator, FakeBackend, Clock]:
    backend, clock = FakeBackend(), Clock()
    ind = Indicator(backend, recovery=recovery, limit_s=limit_s, clock=clock)
    return ind, backend, clock


def sent(**facts: Any) -> Result:
    """A pasted result (terminal paste sent, cleanup applied) with ``facts`` changed."""
    res = Result("u", "pasted", profile="prose", delivery="sent", provider="desktop")
    res.cleanup = CleanupOutcome("x", "applied")
    for k, v in facts.items():
        setattr(res, k, v)
    return res


def pill(view: View) -> tuple[str, str, str, str, str]:
    return view.glyph, view.tone, view.label, view.detail, view.hint


@pytest.mark.parametrize(
    ("res", "expected", "shown", "linger"),
    [
        (sent(), True, ("check", "ok", "", "", ""), 0.5),
        (sent(fallback=True, provider="laptop"), True, ("check", "ok", "", "via laptop", ""), 0.8),
        (sent(cleanup=CleanupOutcome("x", "timeout")), True,
         ("check", "ok", "", "cleanup skipped", ""), 0.8),
        (sent(cleanup=None), True, ("check", "ok", "", "cleanup skipped", ""), 0.8),
        (sent(cleanup=CleanupOutcome("x", "no_edits")), True, ("check", "ok", "", "", ""), 0.5),
        # the profile does not clean up: nothing was skipped
        (sent(cleanup=CleanupOutcome("x", "skipped")), False, ("check", "ok", "", "", ""), 0.5),
        # tags are exclusive: mic dropped, then the length limit, then cleanup, then provider
        (sent(mic_interrupted=True, stop_reason="limit", fallback=True,
              cleanup=CleanupOutcome("x", "error")), True,
         ("check", "ok", "", "mic dropped", ""), 1.5),
        (sent(stop_reason="limit", timings={"held_ms": 120_040.0}, fallback=True), True,
         ("check", "ok", "Stopped at 2:00", "", ""), 1.5),
        (sent(fallback=True, provider="laptop", cleanup=CleanupOutcome("x", "rejected")), True,
         ("check", "ok", "", "cleanup skipped", ""), 0.8),
        (sent(delivery="copied"), True, ("copy", "fg", "Copied", "", ""), 1.0),  # clipboard mode
        (Result("u", "focus_changed", delivery="copied"), False,
         ("copy", "warn", "Copied · focus moved", "", ""), 2.5),
        (Result("u", "focus_changed", delivery="failed"), False,
         ("copy", "warn", "Focus moved", "", KEY), 4.0),
        (Result("u", "paste_failed", delivery="copied"), False,
         ("copy", "warn", "Paste failed · copied", "", KEY), 4.0),
        (Result("u", "paste_failed", delivery="failed"), False,
         ("copy", "warn", "Paste failed", "", KEY), 4.0),
        (Result("u", "error", error="asr: no FinalResult within 4.0 s"), False,
         ("alert", "live", "Failed · server timeout", "", KEY), 4.0),
        (Result("u", "error", error="asr: UNAVAILABLE 14: connection refused"), False,
         ("alert", "live", "Failed · server down", "", KEY), 4.0),
        (Result("u", "error", error="asr: degenerate output (12 unknown tokens)"), False,
         ("alert", "live", "Failed · garbled transcript", "", KEY), 4.0),
        (Result("u", "error", error="asr: something odd"), False,
         ("alert", "live", "Failed", "", KEY), 4.0),
        (None, False, ("alert", "live", "Failed", "", KEY), 4.0),  # the finish itself crashed
        (Result("u", "no_speech"), True, ("cross", "dim", "No speech", "", ""), 0.6),
        (Result("u", "no_speech", input_device="USB Mic"), True,
         ("cross", "dim", "No speech", "USB Mic", "skriv doctor"), 1.5),  # quiet: not an error
        (Result("u", "no_speech", input_device="USB Mic", flat_signal=True), True,
         ("mic-off", "live", "No audio from mic", "USB Mic", "skriv doctor"), 4.0),
        (Result("u", "too_short"), True, ("cross", "dim", "Too short", "", ""), 0.6),
        (Result("u", "cancelled"), True, ("cross", "dim", "Cancelled", "", ""), 0.6),
    ],
)  # fmt: skip
def test_each_outcome_has_its_pill_and_linger(
    res: Result | None, expected: bool, shown: tuple[str, ...], linger: float
):
    view, seconds = outcome_view(res, cleanup_expected=expected, recovery=KEY)
    assert (pill(view), seconds) == (shown, linger)
    assert view.name  # every pill has its sentence (accessibility, macOS)


def test_without_a_panel_key_a_failure_names_skriv_retry_when_audio_is_kept():
    failed = Result("u", "error", error="boom")
    assert outcome_view(failed, cleanup_expected=False, retry_hint=True)[0].hint == "skriv retry"
    assert outcome_view(failed, cleanup_expected=False)[0].hint == ""
    pasted = Result("u", "paste_failed", delivery="copied")
    assert outcome_view(pasted, cleanup_expected=False, retry_hint=True)[0].hint == ""


def test_an_outcome_lingers_then_hides_and_stages_show_working():
    ind, backend, clock = make()
    ind.recording("a")
    ind.stage("a", "transcribing")
    ind.stage("a", "cleaning")
    assert ind.view == WORKING
    ind.outcome("a", sent())
    clock.t += 0.4
    ind.tick()
    assert ind.view is not None
    assert ind.view.glyph == "check"
    clock.t += 0.2
    ind.tick()
    assert ind.view is None
    assert backend.states() == ["recording", "working", "done", None]
    with pytest.raises(ValueError, match="unknown indicator stage"):
        ind.stage("a", "pasting")
    with pytest.raises(ValueError, match="needs a Result"):
        ind.finished("a", "pasted")


def test_a_new_recording_wins_over_an_older_dictation_still_finishing():
    ind, _, clock = make(recovery=KEY)
    ind.recording("a")
    ind.stage("a", "transcribing")
    ind.recording("b")
    ind.stage("a", "cleaning")
    ind.outcome("a", sent())
    assert ind.view is not None
    assert ind.view.state == "recording"
    clock.t += 10
    ind.tick()  # nothing scheduled to hide while recording
    assert ind.view is not None
    ind.outcome("b", Result("b", "error"))
    assert ind.view is not None
    assert (ind.view.label, ind.view.hint) == ("Failed", KEY)
    ind.finished("a", "cancelled")  # b is final: a late update may show
    assert ind.view is not None
    assert ind.view.label == "Cancelled"


def test_notices_never_cover_a_dictation_and_hide_by_themselves():
    ind, _, clock = make(recovery=KEY)
    ind.recording("a")
    ind.notice("Profile: terminal")
    ind.mic_lost()  # the watchdog flags it mid-recording: the recording stays on display
    ind.refused("paused")
    assert ind.view is not None
    assert ind.view.state == "recording"
    ind.outcome("a", sent())
    ind.notice("Profile: terminal")
    assert ind.view == View("info", "info", "fg", "Profile: terminal", "Profile: terminal")
    clock.t += 1.0
    ind.tick()
    assert ind.view is None
    ind.mic_lost()
    assert ind.view is not None
    assert (ind.view.glyph, ind.view.tone, ind.view.label, ind.view.hint) == (
        "mic-off", "live", "Mic lost", KEY,
    )  # fmt: skip
    clock.t += 4.0
    ind.tick()
    assert ind.view is None


def test_a_refused_press_shows_why():
    ind, _, clock = make(recovery=KEY)
    ind.refused("paused")
    assert ind.view is not None
    assert (ind.view.glyph, ind.view.label) == ("pause", "Paused")
    ind.refused("mic")
    assert ind.view is not None
    assert (ind.view.label, ind.view.hint) == ("Mic lost", KEY)
    # busy: the dictation still finishing is on display and stays as it is
    ind.recording("a")
    ind.stage("a", "transcribing")
    ind.refused("busy")
    clock.t += 5
    ind.tick()
    assert ind.view == WORKING
    # ...or working shows again briefly (its pill was covered), and the outcome still lands
    ind.outcome("a", sent())
    ind.refused("busy")
    assert ind.view == WORKING
    clock.t += 1.0
    ind.tick()
    assert ind.view is None
    ind.refused("busy")
    ind.outcome("a", Result("a", "no_speech"))
    assert ind.view is not None
    assert ind.view.label == "No speech"


def test_the_meter_shows_the_newest_levels_scaled_and_rate_limited():
    ind, backend, clock = make()
    loud = np.full(320, 32767, dtype="<i2").tobytes()  # 0 dBFS
    ind.level(loud)  # not recording: ignored
    assert backend.rendered == []
    ind.recording("a")
    assert ind.view is not None
    assert ind.view.levels == (0.0,) * 8
    ind.level(loud)
    ind.level(loud)  # same instant: dropped
    clock.t += 0.06
    ind.level(np.zeros(320, dtype="<i2").tobytes())
    assert ind.view is not None
    assert ind.view.levels[-2:] == (pytest.approx(1.0, abs=1e-4), 0.0)
    assert len(ind.view.levels) == 8


def test_recording_shows_hands_free_the_pinned_profile_and_time_after_a_minute():
    ind, _, clock = make()
    ind.recording("a", "Listening · terminal")
    assert ind.view is not None
    assert (ind.view.glyph, ind.view.pinned, ind.view.detail) == ("dot", "terminal", "")
    clock.t += 72
    ind.recording("a", "Hands-free · terminal")  # same recording: the clock keeps running
    assert ind.view is not None
    assert (ind.view.glyph, ind.view.detail, ind.view.name) == (
        "lock", "1:12", "Hands-free · terminal · 1:12",
    )  # fmt: skip
    clock.t += 0.1
    ind.level(pcm(0.02))
    assert ind.view is not None
    assert (ind.view.glyph, ind.view.pinned) == ("lock", "terminal")


def test_recording_counts_down_the_last_seconds_before_the_length_limit():
    ind, _, clock = make(limit_s=300)
    ind.recording("a")
    clock.t += 269.5  # 30.5 s left: still the elapsed time
    ind.level(pcm(0.02))
    assert ind.view is not None
    assert ind.view.detail == "4:30"
    clock.t += 5.5
    ind.level(pcm(0.02))
    assert ind.view is not None
    assert (ind.view.detail, ind.view.name) == ("0:25 left", "Recording · 0:25 left")


def test_daemon_drives_the_indicator_through_a_dictation_and_a_cancel(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, _ = controller
    fake_context.snapshots = [snapshot("com.apple.Notes")]  # prose: cleaned up
    c.svc.router.refresh()  # learns the server's cleanup profiles
    backend = FakeBackend()
    c.indicator = ind = Indicator(backend)
    c.svc.on_stage = ind.stage
    c.handle("start")
    c.on_pcm(pcm(0.6))
    c.handle("stop")
    wait_idle(c)
    assert backend.states() == ["recording", "working", "done"]
    assert ind.view is not None
    assert (ind.view.glyph, ind.view.detail) == ("check", "")
    assert any(v is not None and v.level > 0 for v in backend.rendered)
    c.handle("start")
    c.handle("cancel")
    assert ind.view is not None
    assert ind.view.label == "Cancelled"


def test_daemon_tags_cleanup_skipped_only_when_the_profile_cleans_up(
    controller: tuple[Controller, DryRunInjector], fake_context: FakeContext
):
    c, _ = controller
    fake_context.snapshots = [snapshot("com.apple.Notes")]
    c.indicator = ind = Indicator(FakeBackend())  # no refresh: the server's cleanup is unknown
    for enabled, tag in ((True, "cleanup skipped"), (False, "")):
        c.svc.cfg.cleanup.enabled = enabled
        c.handle("start")
        c.on_pcm(pcm(0.6))
        c.handle("stop")
        wait_idle(c)
        assert ind.view is not None
        assert (ind.view.glyph, ind.view.detail) == ("check", tag)


def test_daemon_shows_a_press_refused_while_paused(
    controller: tuple[Controller, DryRunInjector],
):
    c, _ = controller
    c.indicator = ind = Indicator(FakeBackend())
    c.set_paused(True)
    assert c.handle("start") == "error paused"
    assert ind.view is not None
    assert ind.view.label == "Paused"


def test_a_hotkey_tap_shows_hands_free_and_the_listener_follows_recordings(
    controller: tuple[Controller, DryRunInjector],
):
    c, _ = controller
    c.indicator = ind = Indicator(FakeBackend())
    seen: list[bool] = []
    c.on_recording = seen.append
    c.hands_free()  # nothing recording: ignored
    assert ind.view is None
    c.handle("start")
    assert ind.view is not None
    assert ind.view.glyph == "dot"
    c.hands_free()
    assert ind.view.glyph == "lock"
    c.handle("cancel")
    c.handle("cancel")
    assert seen == [True, False]


@pytest.mark.parametrize("name", ["tokyo-night", "catppuccin-latte", "flexoki-light"])
def test_the_palette_matches_the_design_tokens(name: str):
    tok: dict[str, str] = json.loads(TOKENS.read_text())[name]
    colors = {
        "background": tok["bg"], "foreground": tok["fg"], "accent": tok["accent"],
        "red": tok["urgent"], "green": tok["green"], "yellow": tok["yellow"],
    }  # fmt: skip
    pal = theme.palette(colors)
    assert (pal.text2, pal.border, pal.accent, pal.live, pal.ok, pal.warn) == (
        tok["text_secondary"], tok["border_hover"], tok["accent_ink"], tok["live_ink"],
        tok["ok_ink"], tok["warn_ink"],
    )  # fmt: skip
    assert theme.contrast(pal.text2, pal.bg) >= 4.5
    assert min(theme.contrast(c, pal.bg) for c in (pal.live, pal.ok, pal.warn)) >= 3.0


def test_the_theme_loads_with_the_users_text_size_and_survives_a_broken_file(tmp_path: Path):
    theme_dir, user = tmp_path / "theme", tmp_path / "shell.toml"
    theme_dir.mkdir()
    pal, size = theme.load(theme_dir, user)  # no theme at all: Omarchy's fallback colours
    assert (pal.bg, pal.fg, size) == ("#101315", "#cacccc", 12)
    (theme_dir / "colors.toml").write_text('background = "#1A1B26"\nforeground = "#a9b1d6"\n')
    (theme_dir / "shell.toml").write_text("[font]\nbase-size = 13\n")
    user.write_text("[font]\nbase-size = 15\n")
    pal, size = theme.load(theme_dir, user)
    assert (pal.bg, size) == ("#1a1b26", 15)
    (theme_dir / "colors.toml").write_text('background = "blue"\n')
    user.write_text("[font\n")
    pal, size = theme.load(theme_dir, user)
    assert (pal.bg, size) == ("#101315", 13)


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit panel")
def test_macos_panel_cannot_take_focus():
    import importlib  # noqa: PLC0415

    from skriv_client.indicator import macos  # noqa: PLC0415

    appkit: Any = importlib.import_module("AppKit")
    appkit.NSApplication.sharedApplication().setActivationPolicy_(
        appkit.NSApplicationActivationPolicyAccessory
    )
    panel = macos.make_panel()
    assert panel.styleMask() & appkit.NSWindowStyleMaskNonactivatingPanel
    assert panel.ignoresMouseEvents()
    assert not panel.canBecomeKeyWindow()
    assert not panel.canBecomeMainWindow()
    assert not panel.hidesOnDeactivate()
    assert macos.is_passive(panel)
    panel.setStyleMask_(appkit.NSWindowStyleMaskTitled)  # an ordinary window: rejected
    assert not macos.is_passive(panel)
