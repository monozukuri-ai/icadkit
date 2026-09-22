"""Saved native entity appearance and bounded box/cylinder parameters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal

from . import _core
from .models import ByteRange, Diagnostic, Status

if TYPE_CHECKING:
    from .document import Document


@dataclass(frozen=True)
class NativeAppearance:
    """Stored entity values, not inherited part or effective face appearance.

    color_index is an iCAD palette index, not RGB. visible is the saved entity
    flag, not a guarantee that layers/views or application settings display it.
    """

    color_index: int | None
    visible: bool | None
    layer: int | None
    status: Status
    byte_range: ByteRange
    raw_bytes: bytes


@dataclass(frozen=True)
class NativePrimitive:
    """Positive box/cylinder parameters in qualified V7L7/V8L3 native layouts.

    world_transform acts on column vectors in the 3DGLOBAL frame. Its origin is
    the cylinder's bottom centre or the box's saved cross-section reference.
    Box X/Y bounds are in this frame, and Z spans [0, height]. No part transform
    should be applied again. This describes parameters, not an evaluated B-Rep.
    """

    kind: Literal["box", "cylinder"]
    world_transform: tuple[tuple[float, ...], ...]
    height: float
    radius: float | None
    x_bounds: tuple[float, float] | None
    y_bounds: tuple[float, float] | None
    byte_range: ByteRange
    raw_bytes: bytes
    length_unit: Literal["mm"] = "mm"
    length_unit_source: Literal["qualified_native_profile"] = "qualified_native_profile"
    coordinate_system: Literal["3DGLOBAL"] = "3DGLOBAL"

    @property
    def box_dimensions(self) -> tuple[float, float, float] | None:
        if self.x_bounds is None or self.y_bounds is None:
            return None
        return (
            self.x_bounds[1] - self.x_bounds[0],
            self.y_bounds[1] - self.y_bounds[0],
            self.height,
        )


@dataclass(frozen=True)
class NativeEntity:
    """One length-framed entity owned by the preceding saved part record.

    Unsupported geometry remains here with primitive=None and a source range.
    IDs are snapshot identifiers. No Parasolid resource ownership is inferred.
    """

    entity_id: str
    owner_id: str
    source_id: int | None
    raw_type: int | None
    is_mirror: bool | None
    byte_range: ByteRange
    appearance: NativeAppearance
    primitive: NativePrimitive | None
    geometry_status: Status
    diagnostics: tuple[Diagnostic, ...]


def _read_entity(doc: Document, raw: _core.RawNativeEntity, owner: str) -> NativeEntity:
    start, end = raw["byte_range"]
    header = ByteRange(start, min(start + 48, end))
    primitive = None
    if value := raw["primitive"]:
        origin, z, x = value["frame"][:3], value["frame"][3:6], value["frame"][6:9]
        y = (
            z[1] * x[2] - z[2] * x[1],
            z[2] * x[0] - z[0] * x[2],
            z[0] * x[1] - z[1] * x[0],
        )
        matrix = tuple((x[i], y[i], z[i], origin[i]) for i in range(3)) + (
            (0.0, 0.0, 0.0, 1.0),
        )
        p = value["parameters"]
        box = value["kind"] == "box"
        source = ByteRange(start + 48, end)
        primitive = NativePrimitive(
            value["kind"],
            matrix,
            p[0] if box else p[1],
            None if box else p[0],
            (p[1], p[3]) if box else None,
            (p[2], p[4]) if box else None,
            source,
            doc.source_bytes(source),
        )
    return NativeEntity(
        f"entity:{start:x}",
        owner,
        raw["source_id"],
        raw["raw_type"],
        raw["is_mirror"],
        ByteRange(start, end),
        NativeAppearance(
            raw["color_index"],
            raw["visible"],
            raw["layer"],
            raw["appearance_status"],
            header,
            doc.source_bytes(header),
        ),
        primitive,
        raw["geometry_status"],
        tuple(Diagnostic(**d) for d in raw["diagnostics"]),
    )


def _entity_row(entity: NativeEntity) -> dict[str, object]:
    appearance = asdict(entity.appearance)
    appearance["raw_bytes_hex"] = appearance.pop("raw_bytes").hex()
    primitive = None
    if entity.primitive:
        primitive = asdict(entity.primitive)
        primitive["raw_bytes_hex"] = primitive.pop("raw_bytes").hex()
        primitive["box_dimensions"] = entity.primitive.box_dimensions
    return {
        "entity_id": entity.entity_id,
        "owner_id": entity.owner_id,
        "source_id": entity.source_id,
        "raw_type": entity.raw_type,
        "is_mirror": entity.is_mirror,
        "byte_range": asdict(entity.byte_range),
        "appearance": appearance,
        "primitive": primitive,
        "geometry_status": entity.geometry_status,
        "diagnostics": [asdict(d) for d in entity.diagnostics],
    }
