"""Framing of the container that follows the directory-indexed records.

The container holds per-entity compressed blocks and, in some files, a named
table. Complete means its record boundaries were traversed; block payloads
beyond the qualified header fields and the table layout remain uninterpreted.
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
class TrailerBlockData:
    """A decoded block with its qualified header fields.

    bounds holds the stored single-precision minimum and maximum corners in
    millimetres, in the saved local frame of the owning entity: the frame that
    places its saved body, not the document frame. The box encloses the body
    but is not always the smallest such box. Bounds are exposed for the
    qualified payload layout only. The face and edge tables that follow remain
    uninterpreted, so a block with bounds is partial, never complete.
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
    return TrailerBlockData(
        block_id,
        raw["source_id"],
        raw["payload"],
        raw["payload_sha256"],
        raw["raw_kind"],
        ((b[0], b[1], b[2]), (b[3], b[4], b[5])) if b is not None else None,
        raw["status"],
        tuple(Diagnostic(**d) for d in raw["diagnostics"]),
    )
