"""Observed iCAD SX product versions per reader scope.

The tables mirror the exact version bytes each reader accepts (`crates/icad-core`
``views.rs`` / ``parts.rs`` and ``drawing.py``). They let a caller report which
saved versions a scope has been qualified for without parsing a file; they do
not make another version readable, and a listed version still needs the record
layout to match.
"""

from dataclasses import dataclass

_LITTLE_3D = ("V7L2", "V7L3", "V7L4", "V7L5", "V7L6", "V7L7", "V8L1", "V8L2", "V8L3")
_BIG_2D = (
    "V1L9",
    "V3L1",
    "V3L2",
    "V3L3",
    "V3L4",
    "V3L5",
    "V3L7",
    "V3L8",
    "V3L9",
    "V5L1",
    "V5L2",
    "V5L3",
    "V5L4",
    "V6L1",
    "V6L2",
    "V7L1",
)
_LAYOUT = ("V7L6", "V7L7", "V8L1", "V8L2", "V8L3")


@dataclass(frozen=True)
class VersionSupport:
    """Version labels (``V8L3`` form) each scope has been qualified for."""

    parts: tuple[str, ...]
    """Little-endian part inventories, frames and saved final bodies."""
    views_little: tuple[str, ...]
    """Little-endian view inventories and saved 2D geometry."""
    views_big: tuple[str, ...]
    """Big-endian view inventories and saved 2D geometry."""
    drawing_layout: tuple[str, ...]
    """Text, dimension and hatch layouts (little-endian)."""

    def views(self, byte_order: str) -> tuple[str, ...]:
        if byte_order == "little":
            return self.views_little
        if byte_order == "big":
            return self.views_big
        raise ValueError("byte_order must be 'little' or 'big'")


def qualified_versions() -> VersionSupport:
    """The observed versions per scope; absence means unqualified, not unreadable."""
    return VersionSupport(_LITTLE_3D, _LITTLE_3D, _BIG_2D, _LAYOUT)


def version_label(raw_version: bytes) -> str | None:
    """``V8L3`` for the observed ``00 08 00 03`` layout, else ``None``.

    The four header bytes keep the same ``00 major 00 level`` layout in both
    byte orders; they are not an integer in the file byte order.
    """
    if (
        len(raw_version) != 4
        or raw_version[0] != 0
        or raw_version[2] != 0
        or raw_version[1] == 0
        or raw_version[3] == 0
    ):
        return None
    return f"V{raw_version[1]}L{raw_version[3]}"
