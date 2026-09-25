"""Explicitly bound qualified saved bodies, independent of feature replay."""

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
    resource_source_id: int | None = None
    binding_kind: str | None = None
    frame_source: str | None = None


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
    profile = parts.profile
    v7 = profile is not None and profile.saved_body_layout in (
        "v7l6_source_id",
        "v7_source_id",
    )
    if (
        profile is None
        or profile.saved_body_layout
        not in ("v7l6_source_id", "v7_source_id", "v8_resource_key", "v8l1_saved_body")
        or parts.status.index != "complete"
        or parts.source_length_unit != "mm"
        or parts.status.hierarchy not in ("complete", "partial")
        or doc.resource_index_status != "complete"
    ):
        return SavedBodyIndex(
            doc.source_sha256,
            (),
            "unsupported",
            (
                _error(
                    "profile",
                    "Saved bodies require complete qualified indexes "
                    "and qualified units",
                ).diagnostic,
            ),
        )
    root = next(p for p in parts.parts if p.is_root)
    root_frame = _frame(list(struct.unpack("<9d", root.placement.raw_coordinate_bytes)))
    if root_frame is None:
        return SavedBodyIndex(
            doc.source_sha256,
            (),
            "unsupported",
            (_error("frame", "Saved root frame is not rigid").diagnostic,),
        )
    source_counts: Counter[int] = Counter()
    owners = {p.part_id: p for p in parts.parts}

    def qualified_owner(owner_id: str) -> bool:
        seen: set[str] = set()
        while owner_id not in seen:
            seen.add(owner_id)
            owner = owners.get(owner_id)
            if (
                owner is None
                or owner.is_external
                or (owner.is_mirror and profile.mirror_policy != "stored_parity")
                or owner.placement.world_transform is None
            ):
                return False
            if owner.is_root:
                return True
            if owner.parent_id is None:
                return False
            owner_id = owner.parent_id
        return False

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
        # V8 copies within one owner can reorder resource payloads while leaving
        # distinct marker keys unchanged. A unique numeric key alone does not
        # qualify that association. Repeated occurrences of one key are bounded.
        owner_keys: set[int] = set()
        if not v7:
            for entity in part.entities:
                raw = doc.source_bytes(entity.byte_range)
                if (
                    entity.raw_type == 85
                    and len(raw) == 152
                    and struct.unpack_from("<I", raw, 12)[0] & 0xFFFFFF
                    not in (0x109E1, 0x109A1)
                ):
                    owner_keys.add(struct.unpack_from("<I", raw, 32)[0])
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
            resource_source_id = None
            binding_kind = None
            frame_source = None
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
                if not qualified_owner(part.part_id):
                    raise _error(
                        "owner", "Unqualified saved body owner", part.byte_range.start
                    )
                if (
                    len(b) != (48 if v7 else 152)
                    or words[0] != len(b)
                    or not words[1]
                    or words[2]
                    or (
                        words[3] not in (0x550108C1, 0x55010881)
                        and not (
                            not v7
                            and part.byte_range.length == 704
                            and words[3] == 0x550108C0
                        )
                    )
                    or sid & 0xF0000000 != 0x80000000
                    or words[5]
                    or words[6] != (0xFD000018 if v7 else 0xFD000080)
                    or words[7] != 0x01000000
                    or not words[8]
                    or words[9]
                    or words[11]
                    not in (
                        0x01800004 | (words[3] & 0x40),
                        0x01000004 | (words[3] & 0x40),
                    )
                ):
                    raise _error(
                        "result_layout",
                        "Unqualified saved final-body marker",
                        entity.byte_range.start,
                    )
                # V8 layouts can store an explicit resource key at +32. Repeated
                # occurrences may share that key, while their native IDs and
                # frames remain independent. Never use resource enumeration order.
                legacy_template = (
                    profile.saved_body_layout == "v8l1_saved_body"
                    and any(by_id[r].owner_type == 134 for r in resources.get(sid, []))
                )
                resource_source_id = sid if v7 or legacy_template else words[8]
                if not v7 and not legacy_template and len(owner_keys) > 1:
                    raise _error(
                        "owner_resource_keys",
                        "Multiple distinct V8 resource keys in one owner require "
                        "an unqualified association table",
                        entity.byte_range.start,
                    )
                matches = resources.get(resource_source_id, [])
                if source_counts[sid] != 1 or len(matches) != 1:
                    raise _error(
                        "binding",
                        "Final body requires one unique source-ID match",
                        entity.byte_range.start,
                    )
                resource_id = matches[0]
                resource = by_id[resource_id]
                expected_layout = (
                    (134, 4)
                    if profile.saved_body_layout == "v7l6_source_id"
                    else (134, 5)
                    if v7 or legacy_template
                    else (135, 6)
                )
                if (resource.owner_type, resource.layout_version) != expected_layout:
                    raise _error(
                        "resource_layout",
                        "Resource layout does not match the saved body profile",
                        resource.owner_range.start,
                    )
                frame_start = (
                    resource.owner_range.start + 32
                    if v7
                    else entity.byte_range.start + 48
                )
                frame_range = ByteRange(frame_start, frame_start + 72)
                raw_frame = doc.source_bytes(frame_range)
                frame = _frame(list(struct.unpack("<9d", raw_frame)))
                if frame is None:
                    raise _error(
                        "frame", "Non-rigid saved resource frame", frame_range.start
                    )
                if legacy_template:
                    # Original V8L1 parametric templates use native IDs for
                    # layout 134/5. Only the observed root-owned template
                    # with an identity root and identical marker/resource
                    # frames is qualified. Do not choose between conflicting frames.
                    identity = _frame([0, 0, 0, 0, 0, 1, 1, 0, 0])
                    resource_frame = doc.source_bytes(
                        ByteRange(
                            resource.owner_range.start + 32,
                            resource.owner_range.start + 104,
                        )
                    )
                    if (
                        not part.is_root
                        or part.byte_range.length != 704
                        or root_frame != identity
                        or resource_frame != raw_frame
                        or resources.get(words[8])
                    ):
                        raise _error(
                            "legacy_template",
                            "Unqualified V8L1 source-ID template",
                            entity.byte_range.start,
                        )
                world = (
                    _relative(root_frame, frame)
                    if profile.coordinate_convention == "root_relative"
                    else frame
                )
                if world is None:
                    raise _error(
                        "frame", "Saved global frame overflow", frame_range.start
                    )
                binding_kind = (
                    "native_source_id"
                    if v7 or legacy_template
                    else "saved_resource_key"
                )
                frame_source = (
                    "root_relative_resource"
                    if v7
                    else "root_relative_native"
                    if profile.coordinate_convention == "root_relative"
                    else "native_global"
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
                    resource_source_id,
                    binding_kind,
                    frame_source,
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
