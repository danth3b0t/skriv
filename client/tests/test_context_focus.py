"""macOS focus logic against a fake pyobjc layer (runs on any platform)."""

import threading
import time
from dataclasses import dataclass

import pytest

from hark_client.context import focus, focus_token


@dataclass(frozen=True)
class Window:
    pid: int
    number: int
    title: str


@dataclass(frozen=True)
class App:
    name: str
    bundle_id: str
    pid: int


class FakeMac:
    """Window server answers at once; Accessibility hangs until ``release`` is set."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def front_window(self) -> Window:
        return Window(501, 77, "Inbox")

    def front_app(self, pid: int | None) -> App:
        return App("Helium", "net.imput.helium", pid or 0)

    def ax_trusted(self) -> bool:
        return True

    def window_title_and_selection(self, pid: int, timeout_s: float) -> tuple[str, str]:
        self.release.wait(5)
        return "AX title", "selected words"


@pytest.fixture
def fake_mac(monkeypatch: pytest.MonkeyPatch):
    fake = FakeMac()
    monkeypatch.setattr(focus, "mac", fake)
    monkeypatch.setattr(focus, "_AX", focus.AxWorker())
    monkeypatch.setattr(focus.sys, "platform", "darwin")
    yield fake
    fake.release.set()


def test_hung_accessibility_costs_only_title_and_selection(fake_mac: FakeMac):
    started = time.monotonic()
    res = focus.get_focus(0.05)
    assert time.monotonic() - started < 0.3
    assert (res.focus.app_id, res.focus.title, res.focus.window_id) == (
        "net.imput.helium",
        "Inbox",
        "501:77",
    )
    assert res.selection is None
    assert [e.split("(")[0].split(" after")[0] for e in res.errors] == [
        "focus: accessibility timeout"
    ]
    # the stuck query is not stacked up behind
    assert focus.get_focus(0.05).errors == (
        "focus: accessibility busy with a previous query (skipped)",
    )


def test_accessibility_title_and_selection_are_used_when_they_arrive(fake_mac: FakeMac):
    fake_mac.release.set()
    res = focus.get_focus(0.05)
    assert (res.focus.title, res.selection, res.errors) == ("AX title", "selected words", ())


@pytest.mark.usefixtures("fake_mac")
def test_focus_token_matches_window_id():
    assert focus_token() == focus.get_focus(0.05).focus.window_id == "501:77"


def test_focus_token_is_empty_when_unknown(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(focus.sys, "platform", "sunos5")
    assert focus_token() == ""
