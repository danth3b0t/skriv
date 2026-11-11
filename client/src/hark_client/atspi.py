"""Linux: the focused text field of a process via AT-SPI (PyGObject ``gi.repository.Atspi``).

**Untested on real hardware.** The search logic (:func:`read_focused`) is unit-tested against
fake nodes; the PyGObject adapter (:class:`_GiNode`) is a thin, untested wrapper. PyGObject is
optional: without it (or without an accessibility bus) :func:`reader` returns None and paste
verification reports ``unknown``. hark does not switch accessibility on for other apps (no
``QT_ACCESSIBILITY``/Chromium flags); it reads what toolkits already expose.

Bounds: only the application whose pid owns the target window, at most ``max_nodes``
accessibles visited (breadth first, only into showing nodes), never a ``password text`` role,
at most ``max_chars`` characters.
"""

from __future__ import annotations

import importlib
from collections import deque
from typing import TYPE_CHECKING, Protocol, cast

from hark_client.fields import FieldText

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

MAX_NODES = 500
ATSPI_TIMEOUT_MS = 200  # per D-Bus call (libatspi's default is 25 s)


class Node(Protocol):
    def pid(self) -> int: ...
    def role(self) -> str: ...  # AT-SPI role name: "text", "entry", "password text", ...
    def states(self) -> frozenset[str]: ...  # state nicks: "focused", "showing", "editable", ...
    def children(self) -> Sequence[Node]: ...
    def char_count(self) -> int | None: ...  # None: no Text interface
    def text(self, end: int) -> str: ...


def read_focused(
    apps: Iterable[Node], pid: int, max_chars: int, max_nodes: int = MAX_NODES
) -> FieldText:
    app = next((a for a in apps if a.pid() == pid), None)
    if app is None:
        return FieldText("unavailable")
    queue: deque[Node] = deque([app])
    seen = 0
    while queue and seen < max_nodes:
        node = queue.popleft()
        seen += 1
        states = node.states()
        if "focused" in states:
            return _field(node, max_chars)
        if node is app or "showing" in states:
            queue.extend(node.children())
    return FieldText("unavailable")


def _field(node: Node, max_chars: int) -> FieldText:
    if node.role() == "password text":
        return FieldText("secure")
    count = node.char_count()
    if count is None:
        return FieldText("not_text")
    if count > max_chars:
        return FieldText("too_long")
    return FieldText("text", node.text(count))


# -- PyGObject adapter (untested on hardware) ------------------------------------------------


class _StateSet(Protocol):
    def get_states(self) -> Sequence[_Nick]: ...


class _Nick(Protocol):
    value_nick: str


class _Accessible(Protocol):
    def get_process_id(self) -> int: ...
    def get_role_name(self) -> str: ...
    def get_state_set(self) -> _StateSet: ...
    def get_child_count(self) -> int: ...
    def get_child_at_index(self, index: int, /) -> _Accessible | None: ...
    def get_text_iface(self) -> object | None: ...


class _TextIface(Protocol):
    def get_character_count(self, text: object, /) -> int: ...
    def get_text(self, text: object, start: int, end: int, /) -> str: ...


class _Atspi(Protocol):
    Text: _TextIface

    def init(self) -> int: ...
    def set_timeout(self, val: int, startup_time: int, /) -> None: ...
    def get_desktop(self, i: int, /) -> _Accessible: ...


class _GiNode:
    def __init__(self, atspi: _Atspi, acc: _Accessible) -> None:
        self._atspi = atspi
        self._acc = acc

    def pid(self) -> int:
        return self._acc.get_process_id()

    def role(self) -> str:
        return self._acc.get_role_name()

    def states(self) -> frozenset[str]:
        return frozenset(s.value_nick for s in self._acc.get_state_set().get_states())

    def children(self) -> list[_GiNode]:
        out: list[_GiNode] = []
        for i in range(self._acc.get_child_count()):
            child = self._acc.get_child_at_index(i)
            if child is not None:
                out.append(_GiNode(self._atspi, child))
        return out

    def char_count(self) -> int | None:
        iface = self._acc.get_text_iface()
        return None if iface is None else self._atspi.Text.get_character_count(iface)

    def text(self, end: int) -> str:
        iface = self._acc.get_text_iface()
        return "" if iface is None else self._atspi.Text.get_text(iface, 0, end)


def reader(max_chars: int) -> Callable[[int], FieldText] | None:
    """pid -> FieldText using the session's accessibility bus, or None without PyGObject."""
    try:
        gi = importlib.import_module("gi")
        cast("_GiModule", gi).require_version("Atspi", "2.0")
        atspi = cast("_Atspi", importlib.import_module("gi.repository.Atspi"))
        atspi.init()
        atspi.set_timeout(ATSPI_TIMEOUT_MS, 1000)
    except (ImportError, ValueError):
        return None

    def read(pid: int) -> FieldText:
        desktop = _GiNode(atspi, atspi.get_desktop(0))
        return read_focused(desktop.children(), pid, max_chars)

    return read


class _GiModule(Protocol):
    def require_version(self, namespace: str, version: str, /) -> None: ...
