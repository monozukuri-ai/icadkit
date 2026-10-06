"""Framing of the container that follows the directory-indexed records.

The container holds per-entity compressed blocks and, in some files, a named
table. Complete means its record boundaries were traversed. A qualified block
exposes its stored bounds and the identity and adjacency of its face and edge
entries; their geometry and the table layout remain uninterpreted.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from . import _core
from .models import ByteRange, Diagnostic, Status

if TYPE_CHECKING:
    from .document import Document

Point3 = tuple[float, float, float]


@dataclass(frozen=True)
class TrailerLimits:
    """Record count, and encoded/decoded bytes of one requested block."""

    max_records: int = 500_000
    max_block_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 < value <= (1 << 31) - 1:
                raise ValueError(f"{name} must be between 1 and 2**31 - 1")


@dataclass(frozen=True)
class TrailerBlock:
    """One compressed block stored under a saved entity identifier.

    source_id is the stored identifier of a native entity in the named view.
    It is not a persistent CAD identity and does not assign part ownership.
    storage_range is the exact encoded payload, without header or padding.
    """

    block_id: str
    source_id: int
    view_name: str | None
    raw_view_name: bytes
    byte_range: ByteRange
    storage_range: ByteRange
    declared_decoded_bytes: int


@dataclass(frozen=True)
class TrailerTable:
    """A framed table whose linked payload layout is not qualified."""

    byte_range: ByteRange
    payload_range: ByteRange
    status: Literal["unsupported"] = "unsupported"


@dataclass(frozen=True)
class TrailerIndex:
    """Record framing after the indexed views; no block is decompressed.

    byte_range is None when the file ends with its last indexed record. This
    is common in files saved without an interactive display and is complete.
    revision is the stored subtype of the block set; it selects the block
    payload layout and is not an iCAD product version.
    """

    source_sha256: str
    byte_range: ByteRange | None
    revision: int | None
    blocks: tuple[TrailerBlock, ...]
    tables: tuple[TrailerTable, ...]
    opaque_ranges: tuple[ByteRange, ...]
    diagnostics: tuple[Diagnostic, ...]
    status: Status

    def blocks_for(self, source_id: int) -> tuple[TrailerBlock, ...]:
        """Blocks stored under one entity identifier, in source order."""
        return tuple(b for b in self.blocks if b.source_id == source_id)


@dataclass(frozen=True)
class TrailerSurface:
    """Surface parameters stored with a face entry, in the entity's local frame.

    Lengths are millimetres. point lies on a plane, on the axis of a cylinder
    or cone, or at the centre of a sphere or torus. axis is the plane normal or
    the axis of a cylinder, cone or torus; it is None for a sphere. A cone's
    stored axis points along the growth of its radius, opposite to the axis of
    the saved CONE node, with a positive half_angle_tangent. A torus axis equals
    the saved axis or its reverse, which describe the same surface. radius is
    the cylinder radius, the cone radius at point or the sphere radius.
    """

    kind: Literal["plane", "cylinder", "cone", "sphere", "torus"]
    point: Point3
    axis: Point3 | None
    x_axis: Point3 | None
    radius: float | None = None
    half_angle_tangent: float | None = None
    major_radius: float | None = None
    minor_radius: float | None = None


@dataclass(frozen=True)
class TrailerParameterLine:
    """The straight image of an edge entry in its first face entry's parameters.

    The first face entry is a plane or a cylinder. The segment runs from start
    to start + extent * direction. For a line on a plane, extent is a length in
    millimetres; for a circle on a cylinder it is the swept angle in radians
    along the angular parameter. An edge that crosses the face's seam is
    stored as several entries.
    """

    start: tuple[float, float]
    direction: tuple[float, float]
    extent: float


@dataclass(frozen=True)
class TrailerFace:
    """One face entry of a block.

    source_node_id is the node identifier of a face in the saved body bound to
    the same entity. A face that is closed in a parameter direction is stored
    as several entries with one identifier. index is the one-based entry
    number that edges refer to. surface_code is the stored surface type: 1
    plane, 2 cylinder, 3 cone, 4 sphere and 5 torus in the checked inputs;
    other values are not qualified. parameter_bounds is (u_min, v_min, u_max,
    v_max) on that surface, with lengths in millimetres; it is None when the
    stored values are not finite and ordered. surface is the decoded analytic
    surface of a qualified item layout; it is None for other layouts, whose
    bytes stay at item_offset in the payload. The other bytes of the entry are
    not interpreted.
    """

    index: int
    source_node_id: int
    surface_code: int
    parameter_bounds: tuple[float, float, float, float] | None
    raw_bytes: bytes
    item_offset: int
    surface: TrailerSurface | None = None


@dataclass(frozen=True)
class TrailerEdge:
    """One edge entry of a block.

    source_node_id is the node identifier of an edge in the saved body. An
    edge that only separates two entries of one saved face carries that face's
    identifier instead. faces holds the one-based numbers of the adjacent face
    entries, 0 for none. parameter_line is the decoded item of an entry whose
    image in the first face entry is straight; other items and the link fields
    are not interpreted.
    """

    index: int
    source_node_id: int
    faces: tuple[int, int]
    raw_bytes: bytes
    item_offset: int
    parameter_line: TrailerParameterLine | None = None


@dataclass(frozen=True)
class TrailerBlockData:
    """A decoded block with its qualified header fields and tables.

    bounds holds the stored single-precision minimum and maximum corners in
    millimetres, in the saved local frame of the owning entity: the frame that
    places its saved body, not the document frame. The box encloses the body
    but is not always the smallest such box. Bounds, faces and edges are
    exposed for the qualified payload layout only. The tables give identity
    and adjacency, not geometry, so such a block is partial, never complete.
    """

    block_id: str
    source_id: int
    payload: bytes
    payload_sha256: str
    raw_kind: int | None
    bounds: tuple[Point3, Point3] | None
    status: Status
    diagnostics: tuple[Diagnostic, ...]
    length_unit: Literal["mm"] = "mm"
    coordinate_space: Literal["entity_local"] = "entity_local"
    faces: tuple[TrailerFace, ...] = ()
    edges: tuple[TrailerEdge, ...] = ()


def _policy(limits: TrailerLimits | None) -> tuple[int, int]:
    if limits is None:
        limits = TrailerLimits()
    elif not isinstance(limits, TrailerLimits):
        raise TypeError("limits must be a TrailerLimits instance")
    return limits.max_records, limits.max_block_bytes


def _read_trailer(document: "Document", limits: TrailerLimits | None) -> TrailerIndex:
    from .document import _raise_native

    policy = _policy(limits)
    try:
        raw = document._handle.read_trailer(policy)
    except _core.InspectionError as exc:
        _raise_native(exc)
    names: list[tuple[bytes, str | None]] = []
    for view in raw["views"]:
        try:
            name = view["raw_name"].rstrip(b" \0").decode("cp932")
        except UnicodeDecodeError:
            name = None
        names.append((view["raw_name"], name))
    return TrailerIndex(
        document.source_sha256,
        ByteRange(*raw["byte_range"]) if raw["byte_range"] else None,
        raw["revision"],
        tuple(
            TrailerBlock(
                f"trailer:{span[0]:016x}",
                source_id,
                names[view][1],
                names[view][0],
                ByteRange(*span),
                ByteRange(*storage),
                decoded,
            )
            for span, storage, source_id, decoded, view in raw["blocks"]
        ),
        tuple(
            TrailerTable(ByteRange(*span), ByteRange(*payload))
            for span, payload in raw["tables"]
        ),
        tuple(ByteRange(*r) for r in raw["opaque_ranges"]),
        tuple(Diagnostic(**d) for d in raw["diagnostics"]),
        raw["status"],
    )


def _surface(raw: "_core.RawTrailerSurface | None") -> TrailerSurface | None:
    if raw is None:
        return None
    point = raw["point"]
    axis = raw["axis"]
    x_axis = raw["x_axis"]
    return TrailerSurface(
        raw["kind"],
        (point[0], point[1], point[2]),
        (axis[0], axis[1], axis[2]) if axis is not None else None,
        (x_axis[0], x_axis[1], x_axis[2]) if x_axis is not None else None,
        raw["radius"],
        raw["half_angle_tangent"],
        raw["major_radius"],
        raw["minor_radius"],
    )


def _read_trailer_block(
    document: "Document", block_id: str, limits: TrailerLimits | None
) -> TrailerBlockData:
    from .document import _raise_native

    if not isinstance(block_id, str):
        raise TypeError("block_id must be a string from this document's trailer index")
    policy = _policy(limits)
    prefix, _, digits = block_id.partition(":")
    if prefix != "trailer" or len(digits) != 16:
        raise ValueError("block_id must come from this document's trailer index")
    try:
        start = int(digits, 16)
    except ValueError:
        raise ValueError(
            "block_id must come from this document's trailer index"
        ) from None
    try:
        raw = document._handle.read_trailer_block(start, policy)
    except _core.InspectionError as exc:
        _raise_native(exc)
    b = raw["bounds"]
    payload = raw["payload"]
    return TrailerBlockData(
        block_id,
        raw["source_id"],
        payload,
        raw["payload_sha256"],
        raw["raw_kind"],
        ((b[0], b[1], b[2]), (b[3], b[4], b[5])) if b is not None else None,
        raw["status"],
        tuple(Diagnostic(**d) for d in raw["diagnostics"]),
        faces=tuple(
            TrailerFace(
                number,
                node_id,
                code,
                (box[0], box[1], box[2], box[3]) if box is not None else None,
                payload[at : at + 36],
                item,
                _surface(surface),
            )
            for number, (node_id, code, box, at, item, surface) in enumerate(
                raw["faces"], 1
            )
        ),
        edges=tuple(
            TrailerEdge(
                number,
                node_id,
                (pair[0], pair[1]),
                payload[at : at + 24],
                item,
                TrailerParameterLine(
                    (line[0][0], line[0][1]), (line[1][0], line[1][1]), line[2]
                )
                if line is not None
                else None,
            )
            for number, (node_id, pair, at, item, line) in enumerate(raw["edges"], 1)
        ),
    )
