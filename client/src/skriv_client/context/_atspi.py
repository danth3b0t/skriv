"""AT-SPI access to the focused text field of an application (Linux; optional).

Needs PyGObject (``gi``) with the ``Atspi`` 2.0 typelib, which skriv does not depend on:
without it :func:`focused_text` returns None. **Untested on real hardware**; the caret
logic using it (``skriv_client.context.caret.text_field_caret``) is unit-tested with fakes.

PyGObject ships no type information, so the unknown-type rules are relaxed for this module
only; the exported names have precise signatures.
"""
# pyright: reportMissingTypeStubs=false, reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAny=false, reportExplicitAny=false

from __future__ import annotations

import functools
import importlib
from dataclasses import dataclass
from typing import Any

_TIMEOUT_MS = 50  # D-Bus method call timeout for AT-SPI requests


@functools.cache
def _api() -> Any | None:  # noqa: ANN401 - the gi module is untyped
    """The Atspi module, loaded once (it connects to the accessibility bus); None if missing."""
    try:
        gi = importlib.import_module("gi")
        gi.require_version("Atspi", "2.0")
        atspi = importlib.import_module("gi.repository.Atspi")
    except (ImportError, ValueError):
        return None
    atspi.set_timeout(_TIMEOUT_MS, _TIMEOUT_MS)
    return atspi


@dataclass(frozen=True, slots=True)
class AtspiTextField:
    """``caret.TextField`` over an Atspi.Accessible with the Text interface."""

    acc: Any
    atspi: Any
    secure: bool

    def caret(self) -> int:
        return int(self.atspi.Text.get_caret_offset(self.acc))

    def count(self) -> int:
        return int(self.atspi.Text.get_character_count(self.acc))

    def text(self, start: int, end: int) -> str:
        return str(self.atspi.Text.get_text(self.acc, start, end))


def focused_text(pid: int) -> AtspiTextField | None:
    """The focused accessible of the application ``pid`` if it has the Text interface."""
    atspi = _api()
    if atspi is None:
        return None
    desktop = atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None or app.get_process_id() != pid:
            continue
        collection = app.get_collection_iface()
        if collection is None:
            return None
        any_ = atspi.CollectionMatchType.ANY
        rule = atspi.MatchRule.new(
            atspi.StateSet.new([atspi.StateType.FOCUSED]), atspi.CollectionMatchType.ALL,
            None, any_, None, any_, None, any_, False,  # noqa: FBT003 - gi positional API
        )  # fmt: skip
        matches = collection.get_matches(rule, atspi.CollectionSortOrder.CANONICAL, 1, True)  # noqa: FBT003
        if not matches:
            return None
        acc = matches[0]
        if acc.get_text_iface() is None:
            return None
        secure = acc.get_role() == atspi.Role.PASSWORD_TEXT
        return AtspiTextField(acc, atspi, secure)
    return None
