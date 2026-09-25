"""Directory-owned view inventories, including legacy registered parts.

Complete means record framing only, not understood drawing/model semantics.
Names, header bytes and unknown records remain available from the snapshot.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from . import _core
from .models import ByteRange, Diagnostic, Status

if TYPE_CHECKING:
    from .document import Document

ViewKind = Literal["unknown", "3d_global", "2d_global", "2d_view", "registered_part"]
DocumentKind = Literal["unknown", "document", "registered_part"]


@dataclass(frozen=True)
class ViewLimits:
    max_views: int = 10_000
    max_records: int = 500_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 < value <= (1 << 31) - 1:
                raise ValueError(f"{name} must be between 1 and 2**31 - 1")


@dataclass(frozen=True)
class ViewEntry:
    byte_range: ByteRange
    kind: Literal["entity", "part", "legacy_owner", "metadata"]
    tag: int | None
    owner_offset: int | None


@dataclass(frozen=True)
class View:
    view_id: str
    byte_range: ByteRange
    header_range: ByteRange | None
    raw_name: bytes
    name: str | None
    kind: ViewKind
    raw_view_number: int | None
    entries: tuple[ViewEntry, ...]
    opaque_ranges: tuple[ByteRange, ...]
    diagnostics: tuple[Diagnostic, ...]
    status: Status


@dataclass(frozen=True)
class ViewIndex:
    source_sha256: str
    document_kind: DocumentKind
    profile_id: str | None
    views: tuple[View, ...]
    diagnostics: tuple[Diagnostic, ...]
    status: Status


def _read_views(document: "Document", limits: ViewLimits | None) -> ViewIndex:
    from .document import _raise_native

    if limits is None:
        limits = ViewLimits()
    elif not isinstance(limits, ViewLimits):
        raise TypeError("limits must be a ViewLimits instance")
    try:
        raw = document._handle.read_views((limits.max_views, limits.max_records))
    except _core.InspectionError as exc:
        _raise_native(exc)
    views = []
    for v in raw["views"]:
        try:
            name = v["raw_name"].rstrip(b" \0").decode("cp932")
        except UnicodeDecodeError:
            name = None
        views.append(
            View(
                f"view@{v['byte_range'][0]}",
                ByteRange(*v["byte_range"]),
                ByteRange(*v["header_range"]) if v["header_range"] else None,
                v["raw_name"],
                name,
                v["kind"],
                v["raw_view_number"],
                tuple(
                    ViewEntry(
                        ByteRange(*e["byte_range"]),
                        e["kind"],
                        e["tag"],
                        e["owner_offset"],
                    )
                    for e in v["entries"]
                ),
                tuple(ByteRange(*r) for r in v["opaque_ranges"]),
                tuple(Diagnostic(**d) for d in v["diagnostics"]),
                v["status"],
            )
        )
    return ViewIndex(
        document.source_sha256,
        raw["document_kind"],
        raw["profile_id"],
        tuple(views),
        tuple(Diagnostic(**d) for d in raw["diagnostics"]),
        raw["status"],
    )
