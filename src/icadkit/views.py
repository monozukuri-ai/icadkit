"""Directory-owned view inventories, including legacy registered parts.

Complete means record framing only, not understood drawing/model semantics.
Names, header bytes and unknown records remain available from the snapshot.
"""

import math
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from . import _core
from .models import ByteRange, Diagnostic, Status

if TYPE_CHECKING:
    from ._core import RawView, RawViewEntry
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
class ViewExtent:
    """Saved view-local extent in millimetres; not recomputed from entities."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    coordinate_space: Literal["view_local"] = "view_local"
    length_unit: Literal["mm"] = "mm"


# The 2D global view lists one fixed-length control record per 2D view after
# its entity group: a `0x40000000` marker, then 264-byte records. Observed
# fields: the record length word, a saved ID at +16, the view origin on the
# sheet (two doubles at +40 and +48; the third double is retained raw), the
# view scale at +64 and the 8-byte view name at +136. The remaining words
# are retained without an assigned meaning.
PLACEMENT_TAG = 0x40000000
PLACEMENT_RECORD_BYTES = 264


@dataclass(frozen=True)
class ViewPlacement:
    """A 2D view's saved placement record in the 2D global view."""

    byte_range: ByteRange
    raw_name: bytes
    name: str | None
    origin: tuple[float, float]
    scale: float | None
    source_id: int
    diagnostics: tuple[Diagnostic, ...]
    status: Status
    coordinate_space: Literal["sheet"] = "sheet"
    length_unit: Literal["mm"] = "mm"


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
    entity_count: int | None = None
    raw_entity_words: int | None = None
    scale: float | None = None
    raw_scale_text: bytes = b""
    extent: ViewExtent | None = None
    extent_kind: Literal["box", "empty", "unqualified"] = "unqualified"
    placement: ViewPlacement | None = None


@dataclass(frozen=True)
class ViewIndex:
    source_sha256: str
    document_kind: DocumentKind
    profile_id: str | None
    views: tuple[View, ...]
    diagnostics: tuple[Diagnostic, ...]
    status: Status
    placements: tuple[ViewPlacement, ...] = ()


def _placement_records(entries: "list[RawViewEntry]") -> list[int]:
    """Record offsets of the control list: the tagged first record, then its
    untagged continuations, in the observed order after the entity group."""
    offsets: list[int] = []
    tagged = False
    for entry in entries:
        if entry["kind"] != "metadata":
            if tagged:
                break
            continue
        start, end = entry["byte_range"]
        length = end - start
        if entry["tag"] == PLACEMENT_TAG and length == PLACEMENT_RECORD_BYTES + 4:
            tagged = True
            offsets.append(start + 4)
        elif tagged and entry["tag"] is None and length == PLACEMENT_RECORD_BYTES:
            offsets.append(start)
        elif tagged:
            break
    return offsets


def _decode_placement(payload: bytes, at: int, prefix: str) -> ViewPlacement:
    diagnostics: list[Diagnostic] = []
    status: Status = "complete"
    (length,) = struct.unpack_from(prefix + "I", payload, 0)
    if length != PLACEMENT_RECORD_BYTES:
        status = "invalid"
        diagnostics.append(
            Diagnostic(
                "invalid",
                "views.placement",
                at,
                "Placement record length word is not 264",
            )
        )
    (source_id,) = struct.unpack_from(prefix + "I", payload, 16)
    x, y = struct.unpack_from(prefix + "2d", payload, 40)
    (scale,) = struct.unpack_from(prefix + "d", payload, 64)
    if not (math.isfinite(x) and math.isfinite(y)):
        status = "invalid"
        diagnostics.append(
            Diagnostic(
                "invalid", "views.placement", at + 40, "Placement origin is not finite"
            )
        )
    qualified_scale: float | None = scale
    if not (math.isfinite(scale) and scale > 0.0):
        qualified_scale = None
        status = "invalid"
        diagnostics.append(
            Diagnostic(
                "invalid",
                "views.placement",
                at + 64,
                "Placement scale is not a positive number",
            )
        )
    raw_name = payload[136:144]
    try:
        name: str | None = raw_name.rstrip(b" \0").decode("cp932")
    except UnicodeDecodeError:
        name = None
    return ViewPlacement(
        ByteRange(at, at + PLACEMENT_RECORD_BYTES),
        raw_name,
        name,
        (x, y),
        qualified_scale,
        source_id,
        tuple(diagnostics),
        status,
    )


def _read_placements(
    document: "Document", raw_views: "list[RawView]"
) -> tuple[dict[bytes, ViewPlacement | None], list[ViewPlacement], list[Diagnostic]]:
    """Decode the control records of every 2D global view and map them by name.

    A name with several records maps to ``None`` (ambiguous); a record that
    names no 2D view and a 2D view without a record add index diagnostics.
    """
    prefix = "<" if document.header.byte_order == "little" else ">"
    placements: list[ViewPlacement] = []
    diagnostics: list[Diagnostic] = []
    for view in raw_views:
        if view["kind"] != "2d_global":
            continue
        for at in _placement_records(view["entries"]):
            payload = document.source_bytes(ByteRange(at, at + PLACEMENT_RECORD_BYTES))
            placements.append(_decode_placement(payload, at, prefix))
    by_name: dict[bytes, ViewPlacement | None] = {}
    for placement in placements:
        key = placement.raw_name.rstrip(b" \0")
        if key in by_name:
            by_name[key] = None
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "views.placement_ambiguous",
                    placement.byte_range.start,
                    "Several placement records share one view name",
                )
            )
        else:
            by_name[key] = placement
    two_d = {v["raw_name"].rstrip(b" \0") for v in raw_views if v["kind"] == "2d_view"}
    for placement in placements:
        if placement.raw_name.rstrip(b" \0") not in two_d:
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "views.placement_view",
                    placement.byte_range.start,
                    "Placement record names no 2D view of the document",
                )
            )
    for view in raw_views:
        if view["kind"] == "2d_view" and view["raw_name"].rstrip(b" \0") not in by_name:
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "views.placement_missing",
                    view["byte_range"][0],
                    "2D view has no placement record in the 2D global view",
                )
            )
    return by_name, placements, diagnostics


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
    by_name, placements, placement_diagnostics = _read_placements(
        document, raw["views"]
    )
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
                entity_count=v["entity_count"],
                raw_entity_words=v["raw_entity_words"],
                scale=v["scale"],
                raw_scale_text=v["raw_scale_text"],
                extent=ViewExtent(*v["extent"]) if v["extent"] else None,
                extent_kind=v["extent_kind"],
                placement=(
                    by_name.get(v["raw_name"].rstrip(b" \0"))
                    if v["kind"] == "2d_view"
                    else None
                ),
            )
        )
    return ViewIndex(
        document.source_sha256,
        raw["document_kind"],
        raw["profile_id"],
        tuple(views),
        tuple(Diagnostic(**d) for d in raw["diagnostics"])
        + tuple(placement_diagnostics),
        raw["status"],
        placements=tuple(placements),
    )
