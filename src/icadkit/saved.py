"""Explicitly bound V7L7 saved final bodies, independent of feature replay."""

from __future__ import annotations

import math
import struct
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .csg import CsgMesh
from .errors import (
    IcadError,
    InvalidFormatError,
    LimitExceededError,
    UnsupportedFormatError,
)
from .geometry import GeometryLimits
from .models import ByteRange, Diagnostic, Status
from .native import NativeAppearance
from .parts import PartIndex, PartLimits, _frame, _relative
from .schema import SchemaCatalog

if TYPE_CHECKING:
    from .document import Document


@dataclass(frozen=True)
class SavedBodyLimits:
    max_bodies: int = 256
    max_entities: int = 100_000
    max_subshapes: int = 20_000
    max_triangles: int = 250_000
    max_vertices: int = 500_000
    max_source_tolerance_mm: float = 0.01

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "max_source_tolerance_mm":
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value <= 0
                ):
                    raise ValueError(f"{name} must be positive and finite")
            elif (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 < value <= 2**31 - 1
            ):
                raise ValueError(f"{name} must be a positive bounded integer")


@dataclass(frozen=True)
class SavedBody:
    body_id: str
    owner_id: str
    source_id: int
    source_sha256: str
    resource_id: str | None
    byte_range: ByteRange
    resource_frame_range: ByteRange | None
    raw_resource_frame: bytes
    world_transform: tuple[tuple[float, ...], ...] | None
    appearance: NativeAppearance
    status: Status
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class SavedBodyIndex:
    source_sha256: str
    bodies: tuple[SavedBody, ...]
    status: Status
    diagnostics: tuple[Diagnostic, ...]
    model_status: str = "partial"


@dataclass(frozen=True)
class SavedBodyMesh(CsgMesh):
    resource_id: str
    payload_sha256: str
    face_count: int
    representation_operations: tuple[str, ...]


def _error(
    code: str, message: str, offset: int | None = None
) -> UnsupportedFormatError:
    return UnsupportedFormatError(
        Diagnostic("unsupported", "saved." + code, offset, message)
    )


def _read_saved_bodies(
    doc: Document, parts: PartIndex, limits: SavedBodyLimits
) -> SavedBodyIndex:
    if (
        doc.header.raw_version != b"\0\7\0\7"
        or parts.status.index != "complete"
        or parts.source_length_unit != "mm"
        or doc.resource_index_status != "complete"
    ):
        return SavedBodyIndex(
            doc.source_sha256,
            (),
            "unsupported",
            (
                _error(
                    "profile",
                    "Saved bodies require complete V7L7 indexes and a rigid root frame",
                ).diagnostic,
            ),
        )
    root = next(p for p in parts.parts if p.is_root)
    root_frame = _frame(list(struct.unpack("<9d", root.placement.raw_coordinate_bytes)))
    assert root_frame is not None
    source_counts: Counter[int] = Counter()
    for part in parts.parts:
        for entity in part.entities:
            b = doc.source_bytes(entity.byte_range)
            if len(b) >= 20:
                source_counts[struct.unpack_from("<I", b, 16)[0]] += 1
    resources: dict[int, list[str]] = {}
    for r in doc.resources:
        resources.setdefault(r.source_id, []).append(r.resource_id)
    by_id = {r.resource_id: r for r in doc.resources}
    bodies: list[SavedBody] = []
    for part in parts.parts:
        for entity in part.entities:
            b = doc.source_bytes(entity.byte_range)
            # Component markers (0x109e1/0x109a1) are not final results.
            if (
                entity.raw_type != 85
                or len(b) < 48
                or struct.unpack_from("<I", b, 12)[0] & 0xFFFFFF in (0x109E1, 0x109A1)
            ):
                continue
            words = struct.unpack("<12I", b[:48])
            sid = words[4]
            resource_id = None
            frame_range = None
            raw_frame = b""
            world = None
            appearance = entity.appearance
            status: Status = "complete"
            diagnostics: tuple[Diagnostic, ...] = ()
            if len(bodies) >= limits.max_bodies:
                raise LimitExceededError(
                    Diagnostic(
                        "limit_exceeded",
                        "saved.limit_bodies",
                        entity.byte_range.start,
                        "Saved body count exceeds max_bodies",
                    )
                )
            try:
                if (
                    part.is_external
                    or part.is_mirror
                    or part.placement.world_transform is None
                ):
                    raise _error(
                        "owner", "Unqualified saved body owner", part.byte_range.start
                    )
                if (
                    len(b) != 48
                    or words[0] != 48
                    or not words[1]
                    or words[2]
                    or words[3] not in (0x550108C1, 0x55010881)
                    or sid & 0xF0000000 != 0x80000000
                    or words[5]
                    or words[6] != 0xFD000018
                    or words[7] != 0x01000000
                    or words[9]
                    or words[11] != (0x01800004 | (words[3] & 0x40))
                ):
                    raise _error(
                        "result_layout",
                        "Unqualified saved final-body marker",
                        entity.byte_range.start,
                    )
                matches = resources.get(sid, [])
                if source_counts[sid] != 1 or len(matches) != 1:
                    raise _error(
                        "binding",
                        "Final body requires one unique source-ID match",
                        entity.byte_range.start,
                    )
                resource_id = matches[0]
                resource = by_id[resource_id]
                if resource.owner_type != 134 or resource.layout_version != 5:
                    raise _error(
                        "resource_layout",
                        "Saved placement requires a type-134/version-5 resource header",
                        resource.owner_range.start,
                    )
                frame_range = ByteRange(
                    resource.owner_range.start + 32, resource.owner_range.start + 104
                )
                raw_frame = doc.source_bytes(frame_range)
                frame = _frame(list(struct.unpack("<9d", raw_frame)))
                if frame is None:
                    raise _error(
                        "frame", "Non-rigid saved resource frame", frame_range.start
                    )
                world = _relative(root_frame, frame)
                if world is None:
                    raise _error(
                        "frame", "Saved global frame overflow", frame_range.start
                    )
                color = (b[41] & 15) | (b[40] & 16)
                appearance = NativeAppearance(
                    color or None,
                    bool(words[3] & 0x40),
                    words[1],
                    "complete" if color else "unsupported",
                    entity.byte_range,
                    b,
                )
            except IcadError as exc:
                status = "unsupported"
                diagnostics = (exc.diagnostic,)
            bodies.append(
                SavedBody(
                    entity.entity_id,
                    part.part_id,
                    sid,
                    doc.source_sha256,
                    resource_id,
                    entity.byte_range,
                    frame_range,
                    raw_frame,
                    world,
                    appearance,
                    status,
                    diagnostics,
                )
            )
    return SavedBodyIndex(
        doc.source_sha256,
        tuple(bodies),
        "complete" if all(b.status == "complete" for b in bodies) else "partial",
        (),
    )


def read_saved_bodies(
    document: Document,
    *,
    part_limits: PartLimits | None = None,
    limits: SavedBodyLimits | None = None,
) -> SavedBodyIndex:
    """Index explicit final markers and unique resources without converting geometry."""
    from .document import Document

    if not isinstance(document, Document):
        raise TypeError("document must be an icadkit.Document")
    limits = SavedBodyLimits() if limits is None else limits
    if not isinstance(limits, SavedBodyLimits):
        raise TypeError("limits must be SavedBodyLimits")
    return _read_saved_bodies(document, document.read_parts(limits=part_limits), limits)


def evaluate_saved_body(
    document: Document,
    body: SavedBody,
    *,
    schema: SchemaCatalog | None = None,
    geometry_limits: GeometryLimits | None = None,
    limits: SavedBodyLimits | None = None,
    linear_deflection_mm: float = 0.05,
) -> SavedBodyMesh:
    """Convert a saved final solid from resource metres to placed native mm.

    Catalog selection is explicit. Limits bound input/output complexity, not
    native-kernel memory or execution time. Feature history is not evaluated.
    """
    from .document import Document

    if not isinstance(document, Document) or not isinstance(body, SavedBody):
        raise TypeError("document/body must be Document/SavedBody")
    limits = SavedBodyLimits() if limits is None else limits
    if not isinstance(limits, SavedBodyLimits):
        raise TypeError("limits must be SavedBodyLimits")
    if (
        isinstance(linear_deflection_mm, bool)
        or not isinstance(linear_deflection_mm, (int, float))
        or not math.isfinite(linear_deflection_mm)
        or linear_deflection_mm <= 0
    ):
        raise ValueError("linear_deflection_mm must be positive and finite")
    if body.source_sha256 != document.source_sha256:
        raise InvalidFormatError(
            Diagnostic(
                "invalid",
                "saved.source_mismatch",
                None,
                "Saved body belongs to another document",
            )
        )
    if (
        body.status != "complete"
        or body.resource_id is None
        or body.world_transform is None
    ):
        raise _error("incomplete", "Cannot evaluate an unqualified saved body")
    geometry = document.read_geometry(
        body.resource_id, schema=schema, limits=geometry_limits
    )
    geometry.require_complete("brep").require_complete("topology")
    if geometry.diagnostics:
        raise _error(
            "geometry_diagnostics", "Saved resource has unresolved geometry diagnostics"
        )
    assert geometry.brep is not None and geometry.source is not None
    if sum(geometry.brep.counts.values()) > limits.max_entities:
        raise LimitExceededError(
            Diagnostic(
                "limit_exceeded",
                "saved.limit_entities",
                None,
                "Saved B-Rep exceeds max_entities",
            )
        )
    from ._saved_occt import convert

    return convert(
        geometry.brep,
        body,
        geometry.source.payload_sha256,
        limits,
        linear_deflection_mm,
    )
