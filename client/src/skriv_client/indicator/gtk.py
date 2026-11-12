"""Linux indicator: GTK 4 + gtk4-layer-shell through PyGObject (optional; **untested on real
hardware**).

The pill of design/SPEC.md §4: an overlay-layer surface anchored bottom-centre (or top) of the
output the compositor picks (the focused one on Hyprland), 24 px from the edge,
``KeyboardMode.NONE`` (never gets keyboard focus) and an empty input region (clicks pass
through). Colours and sizes come from the running Omarchy theme (``indicator.theme``), re-read
when it changes; glyphs are Material Design Icons from the Nerd Font Omarchy sets as
``monospace``. Needs the system packages gtk4, gtk4-layer-shell and PyGObject + pycairo
importable by hark's Python (the client's ``gi`` extra; the skriv-git package includes it).
Unavailable pieces => None.

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from skriv_client.indicator import METER_BARS, OMARCHY_THEME, theme

if TYPE_CHECKING:
    from collections.abc import Callable

    from skriv_client.indicator import Glyph, View
    from skriv_client.indicator.theme import Palette

log = logging.getLogger("skriv.indicator")

MARGIN = 24
FADE_MS = 120  # pill in / out; GTK drops the transition when animations are off
USER_SHELL = Path.home() / ".config/omarchy/shell.toml"  # `omarchy display text size`
# nf-md-* (Nerd Fonts) except the plain recording dot
GLYPHS: dict[Glyph, str] = {
    "dot": "●",
    "lock": "\U000f033e",
    "dots": "",
    "check": "\U000f012c",
    "cross": "\U000f0156",
    "copy": "\U000f018f",
    "alert": "\U000f05d6",
    "mic-off": "\U000f036d",
    "pause": "\U000f03e4",
    "info": "\U000f02fd",
}
PIN = "\U000f0931"
TONES = ("fg", "dim", "live", "ok", "warn", "accent")


def css(pal: Palette, base: int) -> bytes:
    """The stylesheet for a theme palette and font base size (Omarchy's type scale)."""

    def px(n: float) -> int:
        return max(1, round(n * base / 12))

    tone = {
        "fg": pal.fg, "dim": pal.text2, "live": pal.live, "ok": pal.ok, "warn": pal.warn,
        "accent": pal.accent,
    }  # fmt: skip
    return f"""
window.skriv-indicator {{ background: transparent; }}
.skriv-pill {{
  background: {pal.bg}; color: {pal.fg}; border: 1px solid {pal.border}; border-radius: 0;
  min-height: {px(28) - 2}px; padding: 0 {px(12)}px; font-family: monospace;
  font-size: {base}px; transition: opacity {FADE_MS}ms ease-out;
}}
.skriv-pill.hidden {{ opacity: 0; }}
.skriv-glyph {{ font-size: {px(14)}px; }}
.skriv-detail, .skriv-pin {{ color: {pal.text2}; }}
.skriv-hint {{ color: {pal.text2}; font-size: {px(10)}px; }}
@keyframes skriv-breathe {{ 0% {{ opacity: 0.35; }} 33% {{ opacity: 1; }} 66% {{ opacity: 0.35; }} }}
.skriv-wdot {{
  color: {pal.accent}; font-size: {px(8)}px; opacity: 0.35;
  animation: skriv-breathe 900ms ease-in-out infinite;
}}
.skriv-wdot.middle {{ opacity: 1; animation-delay: 300ms; }}
.skriv-wdot.last {{ animation-delay: 600ms; }}
{"".join(f".tone-{k} {{ color: {v}; }}" for k, v in tone.items())}
""".encode()


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
        win = gtk.Window()
        self.provider = gtk.CssProvider()
        gtk.StyleContext.add_provider_for_display(
            win.get_display(), self.provider, gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._theme_seen: tuple[float | None, ...] | None = None
        self.pal, self.base = theme.palette({}), theme.BASE_SIZE
        win.add_css_class("skriv-indicator")
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_focusable(False)
        layer.init_for_window(win)
        layer.set_namespace(win, "skriv-indicator")
        layer.set_layer(win, layer.Layer.OVERLAY)
        layer.set_keyboard_mode(win, layer.KeyboardMode.NONE)
        edge = layer.Edge.BOTTOM if position == "bottom" else layer.Edge.TOP
        layer.set_anchor(win, edge, True)
        layer.set_margin(win, edge, MARGIN)
        # a status region: screen readers announce its name (the whole sentence) on change
        box = gtk.Box(
            orientation=gtk.Orientation.HORIZONTAL, spacing=8,
            accessible_role=gtk.AccessibleRole.STATUS,
        )  # fmt: skip
        box.add_css_class("skriv-pill")
        box.add_css_class("hidden")
        self.glyph = self._label("skriv-glyph")
        self.meter = gtk.DrawingArea()
        self.meter.set_valign(gtk.Align.CENTER)
        self.meter.set_draw_func(self._draw_meter)
        self.dots = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=4)
        for cls in ("first", "middle", "last"):
            self.dots.append(self._label("skriv-wdot", cls, text="●"))
        self.pin = self._label("skriv-pin", text=PIN)
        self.pinned = self._label("skriv-detail")
        self.label = self._label("skriv-label")
        self.detail = self._label("skriv-detail")
        self.hint = self._label("skriv-hint")
        self.hint.set_valign(gtk.Align.BASELINE)
        for w in (
            self.glyph, self.meter, self.dots, self.pin, self.pinned, self.label, self.detail,
            self.hint,
        ):  # fmt: skip
            box.append(w)
        win.set_child(box)
        win.connect("realize", self._on_realize)
        self.win, self.box = win, box
        self._levels: tuple[float, ...] = ()
        self._lock = threading.Lock()
        self._pending: View | None = None
        self._scheduled = False
        self._shown: View | None = None
        self._hiding = 0  # bumps on every show: a pending fade-out only hides its own pill
        self._load_theme()

    def _label(self, *classes: str, text: str = "") -> Any:
        w = self.gtk.Label(label=text)
        for c in classes:
            w.add_css_class(c)
        w.set_valign(self.gtk.Align.CENTER)
        return w

    def _load_theme(self) -> None:
        """Re-read the theme when its files changed (`omarchy theme set`, text size)."""
        files = (OMARCHY_THEME / "colors.toml", OMARCHY_THEME / "shell.toml", USER_SHELL)
        seen = tuple(_mtime(f) for f in files)
        if seen == self._theme_seen:
            return
        self._theme_seen = seen
        self.pal, self.base = theme.load(OMARCHY_THEME, USER_SHELL)
        self.provider.load_from_data(css(self.pal, self.base), -1)
        self._size_meter()

    def _size_meter(self) -> None:
        scale = self.base / 12
        self.meter.set_content_width(round((METER_BARS * 4 - 2) * scale))
        self.meter.set_content_height(round(12 * scale))

    def _draw_meter(self, _area: Any, cr: Any, width: int, height: int) -> None:
        """8 bars, 2 px wide, as tall as the level (2..12 px), centred; newest on the right."""
        r, g, b = (int(self.pal.fg[i : i + 2], 16) / 255 for i in (1, 3, 5))
        cr.set_source_rgb(r, g, b)
        bar = max(1.0, width / (METER_BARS * 2 - 1))
        for i, level in enumerate(self._levels[-METER_BARS:]):
            h = max(bar, level * height)
            cr.rectangle(i * bar * 2, (height - h) / 2, bar, h)
        cr.fill()

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
            if self._shown is not None:
                self._fade_out()
            self._shown = None
            return False
        appearing = self._shown is None
        if appearing:
            self._load_theme()
        self._draw(view)
        if appearing:
            self._hiding += 1
            self.win.set_visible(True)  # a layer surface with KeyboardMode.NONE: no focus
            self.glib.idle_add(self._fade_in)
        self._shown = view
        return False  # one-shot idle callback

    def _draw(self, view: View) -> None:
        shown = self._shown
        if shown is None or shown.tone != view.tone:
            for t in TONES:
                self.glyph.remove_css_class(f"tone-{t}")
            self.glyph.add_css_class(f"tone-{view.tone}")
        self.glyph.set_text(GLYPHS[view.glyph])
        self.glyph.set_visible(view.glyph != "dots")
        self.dots.set_visible(view.glyph == "dots")
        recording = view.state == "recording"
        self.meter.set_visible(recording)
        if recording:
            self._levels = view.levels
            self.meter.queue_draw()
        self.pin.set_visible(bool(view.pinned))
        for w, text in (
            (self.pinned, view.pinned), (self.label, view.label), (self.detail, view.detail),
            (self.hint, view.hint),
        ):  # fmt: skip
            w.set_text(text)
            w.set_visible(bool(text))
        if shown is None or shown.name != view.name:
            self.box.update_property([self.gtk.AccessibleProperty.LABEL], [view.name])

    def _fade_in(self) -> bool:
        self.box.remove_css_class("hidden")
        return False

    def _fade_out(self) -> None:
        self.box.add_css_class("hidden")
        token = self._hiding

        def hide() -> bool:
            if token == self._hiding and self._shown is None:
                self.win.set_visible(False)  # unmapped while it has no content
            return False

        self.glib.timeout_add(FADE_MS, hide)

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


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None
