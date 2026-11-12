"""Linux indicator: GTK 4 + gtk4-layer-shell through PyGObject (optional; **untested on real
hardware**).

An overlay-layer surface anchored to the bottom (or top) edge of the output the compositor
picks (the focused one on Hyprland), ``KeyboardMode.NONE`` (never gets keyboard focus) and an
empty input region (clicks pass through). Needs the system packages gtk4, gtk4-layer-shell and
PyGObject + pycairo importable by hark's Python (``uv pip install PyGObject pycairo`` with the
introspection headers, or a venv with system site packages). Unavailable pieces => None.

GTK runs on the main thread (:meth:`LayerShellBackend.run`); :meth:`LayerShellBackend.render`
hands views over with ``GLib.idle_add`` (thread-safe). PyGObject is typed ``Any`` here.
"""
# pyright: reportAny=false, reportExplicitAny=false
# ruff: noqa: ANN401, FBT003 - PyGObject is untyped and takes positional booleans

from __future__ import annotations

import ctypes
import importlib
import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from hark_client.indicator import View

log = logging.getLogger("hark.indicator")

MARGIN = 72
CSS = b"""
window.hark-indicator { background: transparent; }
.hark-pill { background: rgba(20, 20, 20, 0.88); border-radius: 17px; padding: 7px 14px; color: white; }
.hark-dot { font-size: 10px; }
.hark-dot.recording, .hark-dot.error { color: #ff453a; }
.hark-dot.transcribing { color: #ffbf33; }
.hark-dot.cleaning { color: #8c99ff; }
.hark-dot.done { color: #4dd966; }
.hark-dot.cancelled { color: #999999; }
.hark-dot.info { color: #66b3ff; }
levelbar.hark-level { min-width: 54px; }
"""


def create(position: str) -> LayerShellBackend | None:
    """The backend if GTK 4, gtk4-layer-shell and a layer-shell compositor are available."""
    try:
        # must be loaded before libwayland-client (i.e. before GTK) to hook its surfaces
        ctypes.CDLL("libgtk4-layer-shell.so.0")
    except OSError:
        log.info("no recording indicator: libgtk4-layer-shell not found")
        return None
    try:
        gi: Any = importlib.import_module("gi")
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        gtk: Any = importlib.import_module("gi.repository.Gtk")
        glib: Any = importlib.import_module("gi.repository.GLib")
        layer: Any = importlib.import_module("gi.repository.Gtk4LayerShell")
        cairo: Any = importlib.import_module("cairo")
    except (ImportError, ValueError) as e:
        log.info(
            "no recording indicator: PyGObject/pycairo with Gtk 4 + Gtk4LayerShell needed (%s)", e
        )
        return None
    if not gtk.init_check():
        log.info("no recording indicator: no display for GTK")
        return None
    if not layer.is_supported():
        log.info("no recording indicator: the compositor has no wlr-layer-shell")
        return None
    return LayerShellBackend(gtk, glib, layer, cairo, position)


class LayerShellBackend:
    def __init__(self, gtk: Any, glib: Any, layer: Any, cairo: Any, position: str) -> None:
        self.gtk, self.glib, self.layer, self.cairo = gtk, glib, layer, cairo
        provider = gtk.CssProvider()
        provider.load_from_data(CSS, -1)
        win = gtk.Window()
        gtk.StyleContext.add_provider_for_display(
            win.get_display(), provider, gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        win.add_css_class("hark-indicator")
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_focusable(False)
        layer.init_for_window(win)
        layer.set_namespace(win, "hark-indicator")
        layer.set_layer(win, layer.Layer.OVERLAY)
        layer.set_keyboard_mode(win, layer.KeyboardMode.NONE)
        edge = layer.Edge.BOTTOM if position == "bottom" else layer.Edge.TOP
        layer.set_anchor(win, edge, True)
        layer.set_margin(win, edge, MARGIN)
        box = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("hark-pill")
        self.dot = gtk.Label(label="●")
        self.dot.add_css_class("hark-dot")
        self.label = gtk.Label(label="")
        self.meter = gtk.LevelBar(min_value=0.0, max_value=1.0)
        self.meter.add_css_class("hark-level")
        self.meter.set_valign(gtk.Align.CENTER)
        for w in (self.dot, self.label, self.meter):
            box.append(w)
        win.set_child(box)
        win.connect("realize", self._on_realize)
        self.win = win
        self._lock = threading.Lock()
        self._pending: View | None = None
        self._scheduled = False
        self._shown: View | None = None

    def _on_realize(self, win: Any) -> None:
        surface = win.get_surface()
        self._click_through(surface)
        surface.connect("layout", self._on_layout)  # GTK may reset the region on resize

    def _on_layout(self, surface: Any, _width: int, _height: int) -> None:
        self._click_through(surface)

    def _click_through(self, surface: Any) -> None:
        surface.set_input_region(self.cairo.Region())  # empty: pointer events pass through

    def render(self, view: View | None) -> None:
        with self._lock:
            self._pending = view
            if self._scheduled:
                return
            self._scheduled = True
        self.glib.idle_add(self._apply)

    def _apply(self) -> bool:
        with self._lock:
            view, self._scheduled = self._pending, False
        if view is None:
            self.win.set_visible(False)
            self._shown = None
            return False
        if self._shown is None or self._shown.state != view.state:
            if self._shown is not None:
                self.dot.remove_css_class(self._shown.state)
            self.dot.add_css_class(view.state)
            self.meter.set_visible(view.state == "recording")
        self.label.set_text(view.label)
        self.meter.set_value(view.level)
        if self._shown is None:
            self.win.set_visible(True)  # a layer surface with KeyboardMode.NONE: no focus
        self._shown = view
        return False  # one-shot idle callback

    def run(self, stop: threading.Event, tick: Callable[[], None]) -> None:
        loop = self.glib.MainLoop()

        def poll() -> bool:
            tick()
            if stop.is_set():
                loop.quit()
                return False
            return True

        self.glib.timeout_add(100, poll)
        loop.run()
        self.win.set_visible(False)
