"""Saved native entity appearance and bounded analytic primitive parameters."""

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
class ProfileSegment:
    """One straight or circular segment of a closed extrusion profile.

    Coordinates are XY millimetres in the primitive frame. An arc turns about
    centre by sweep_angle radians from start to end; positive is from +X to +Y.
    """

    kind: Literal["line", "arc"]
    start: tuple[float, float]
    end: tuple[float, float]
    center: tuple[float, float] | None = None
    sweep_angle: float | None = None


@dataclass(frozen=True)
class NativePrimitive:
    """Bounded analytic primitive parameters in qualified native layouts.

    world_transform acts on column vectors in the 3DGLOBAL frame. Its origin is
    the cylinder's bottom centre or the box's saved cross-section reference.
    Box X/Y bounds are in this frame, and Z spans [0, height]. No part transform
    should be applied again. This describes parameters, not an evaluated B-Rep.
    Spheres and tori use a centre frame and height=None. Cones/frusta use
    a bottom-centre frame, radius for the base and top_radius for the top.
    Polygon extrusions use six profile_points in XY and Z in [0, height].
    Signed box/cylinder/polygon heights reverse the exposed Z column; raw bytes
    retain the original direction independently of the entity mirror flag.
    Profile extrusions use a closed loop of line/arc segments in XY and Z in
    [0, height], with the same signed-height rule. A revolution turns the
    (radius, axial) polyline in revolution_profile about the frame Z axis; the
    region is closed along that axis. Both are stored already reflected for a
    mirrored entity: no reflection is applied to their frame or profile.
    """

    kind: Literal[
        "box",
        "cylinder",
        "sphere",
        "cone",
        "torus",
        "polygon_extrusion",
        "profile_extrusion",
        "revolution",
    ]
    world_transform: tuple[tuple[float, ...], ...]
    height: float | None
    radius: float | None
    x_bounds: tuple[float, float] | None
    y_bounds: tuple[float, float] | None
    byte_range: ByteRange
    raw_bytes: bytes
    length_unit: Literal["mm"] = "mm"
    length_unit_source: Literal["qualified_native_profile"] = "qualified_native_profile"
    coordinate_system: Literal["3DGLOBAL"] = "3DGLOBAL"
    top_radius: float | None = None
    major_radius: float | None = None
    minor_radius: float | None = None
    mirror_convention: (
        Literal["signed_height", "symmetric_frame", "stored_profile"] | None
    ) = None
    profile_points: tuple[tuple[float, float], ...] | None = None
    profile: tuple[ProfileSegment, ...] | None = None
    revolution_profile: tuple[tuple[float, float], ...] | None = None

    @property
    def box_dimensions(self) -> tuple[float, float, float] | None:
        if self.x_bounds is None or self.y_bounds is None or self.height is None:
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
        matrix: tuple[tuple[float, ...], ...] = tuple(
            (x[i], y[i], z[i], origin[i]) for i in range(3)
        ) + ((0.0, 0.0, 0.0, 1.0),)
        p = value["parameters"]
        box = value["kind"] == "box"
        kind = value["kind"]
        signed = p[1] if kind == "cylinder" else p[0]
        extruded = ("box", "cone", "cylinder", "polygon_extrusion", "profile_extrusion")
        reversed_height = kind in extruded and signed < 0
        if reversed_height:
            # The stored extrusion direction is independent of mirror parity.
            # Normalize the height and reverse Z once; do not apply part parity.
            matrix = tuple(
                tuple(-v if j == 2 else v for j, v in enumerate(row)) for row in matrix
            )
        source = ByteRange(start + 48, end)
        primitive = NativePrimitive(
            value["kind"],
            matrix,
            abs(signed) if kind in extruded else None,
            p[5]
            if kind == "cone"
            else p[0]
            if kind in ("sphere", "cylinder")
            else None,
            (p[1], p[3]) if box else None,
            (p[2], p[4]) if box else None,
            source,
            doc.source_bytes(source),
            top_radius=p[6] if kind == "cone" else None,
            major_radius=p[1] if kind == "torus" else None,
            minor_radius=p[2] if kind == "torus" else None,
            mirror_convention=(
                "signed_height"
                if reversed_height and raw["is_mirror"]
                else "stored_profile"
                if raw["is_mirror"] and kind == "profile_extrusion"
                else "symmetric_frame"
                if raw["is_mirror"]
                else None
            ),
            profile_points=tuple((p[i], p[i + 1]) for i in range(1, len(p), 2))
            if kind == "polygon_extrusion"
            else None,
            profile=tuple(
                ProfileSegment(
                    "arc" if p[i + 6] else "line",
                    (p[i], p[i + 1]),
                    (p[i + 2], p[i + 3]),
                    (p[i + 4], p[i + 5]) if p[i + 6] else None,
                    p[i + 6] or None,
                )
                for i in range(1, len(p), 7)
            )
            if kind == "profile_extrusion"
            else None,
            revolution_profile=tuple((p[i], p[i + 1]) for i in range(0, len(p), 2))
            if kind == "revolution"
            else None,
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
