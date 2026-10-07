"""Part shapes: the qualified solids of one part as an OCCT compound.

A part shape collects the saved final bodies of a part, placed by their saved
frames; for a part without a qualified saved body it falls back to the
qualified CSG results, and for a part without either to its standalone native
primitives. The solids are expressed in the part coordinate frame
(``frame="part"``) or in the document root frame (``frame="world"``). The
optional ``preview`` extra supplies the kernel. Nothing is healed or
approximated: a body that does not convert keeps its diagnostics and the part
becomes ``partial`` or ``unsupported``, which is the per-part answer to "can
this part be drawn".
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .csg import CsgBody, CsgIndex, CsgLimits, _read_csg, _validate_tokens
from .errors import IcadError, LimitExceededError, UnsupportedFormatError
from .geometry import GeometryLimits
from .models import ByteRange, Diagnostic, Status
from .native import NativeEntity
from .parts import Matrix4, Part, PartIndex, PartLimits, _relative
from .saved import (
    SavedBody,
    SavedBodyIndex,
    SavedBodyLimits,
    _qualified_geometry,
    _read_saved_bodies,
)
from .schema import SchemaCatalog

if TYPE_CHECKING:
    from .document import Document

ShapeFrame = Literal["part", "world"]
ShapeSource = Literal["saved", "csg", "native"]

_IDENTITY: Matrix4 = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


@dataclass(frozen=True)
class PartShapeLimits:
    """Bounds for one call; the nested limits bound each body's conversion."""

    max_parts: int = 10_000
    max_bodies: int = 256
    saved: SavedBodyLimits = field(default_factory=SavedBodyLimits)
    csg: CsgLimits = field(default_factory=CsgLimits)
    geometry: GeometryLimits = field(default_factory=GeometryLimits)

    def __post_init__(self) -> None:
        for name in ("max_parts", "max_bodies"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 < value <= 2**31 - 1
            ):
                raise ValueError(f"{name} must be a positive bounded integer")
        if (
            not isinstance(self.saved, SavedBodyLimits)
            or not isinstance(self.csg, CsgLimits)
            or not isinstance(self.geometry, GeometryLimits)
        ):
            raise TypeError("saved, csg and geometry must be the matching limit types")


@dataclass(frozen=True)
class ShapeBody:
    """One solid of a part shape, its origin and its exact mass properties."""

    body_id: str
    owner_id: str
    source: ShapeSource
    resource_id: str | None
    byte_range: ByteRange
    status: Status
    diagnostics: tuple[Diagnostic, ...] = ()
    payload_sha256: str | None = None
    face_count: int | None = None
    solid_count: int | None = None
    volume_mm3: float | None = None
    area_mm2: float | None = None
    centroid_mm: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class PartShape:
    """The converted solids of one part in the requested frame.

    ``shape`` is the OCCT ``TopoDS_Compound`` of every converted body, or
    ``None`` when nothing converted. ``frame_transform`` is the rigid matrix
    applied to document-root millimetre coordinates: the identity for
    ``frame="world"`` and the inverse of the part frame for ``frame="part"``.
    ``status`` is ``complete`` when every body converted, ``partial`` when
    some did and ``unsupported`` when none did; ``reason`` names the first
    diagnostic code behind a status other than ``complete``.
    """

    part_id: str
    name: str | None
    source_sha256: str
    frame: ShapeFrame
    frame_transform: Matrix4 | None
    bodies: tuple[ShapeBody, ...]
    status: Status
    reason: str | None
    diagnostics: tuple[Diagnostic, ...]
    volume_mm3: float | None = None
    area_mm2: float | None = None
    centroid_mm: tuple[float, float, float] | None = None
    length_unit: Literal["mm"] = "mm"
    shape: Any = field(default=None, repr=False, compare=False)

    @property
    def converted(self) -> tuple[ShapeBody, ...]:
        return tuple(b for b in self.bodies if b.status == "complete")

    @property
    def solid_count(self) -> int:
        return sum(b.solid_count or 0 for b in self.converted)

    def write_step(self, path: str | Path) -> Path:
        """Write the compound as STEP in millimetres to a new path."""
        return self._write(path, "step")

    def write_brep(self, path: str | Path) -> Path:
        """Write the compound in OCCT BREP format to a new path."""
        return self._write(path, "brep")

    def _write(self, path: str | Path, kind: str) -> Path:
        target = Path(path)
        if self.shape is None:
            raise UnsupportedFormatError(
                Diagnostic(
                    "unsupported",
                    "shape.empty",
                    None,
                    "The part has no converted solid to write",
                )
            )
        if target.exists():
            raise FileExistsError(f"{target} exists; shapes are never written in place")
        from ._shape_occt import runtime, write_brep, write_step

        api = runtime()
        (write_step if kind == "step" else write_brep)(self.shape, target, api)
        return target

    def to_row(self) -> dict[str, Any]:
        """JSON-compatible summary without the kernel shape."""
        return {
            "part_id": self.part_id,
            "name": self.name,
            "frame": self.frame,
            "frame_transform": (
                None
                if self.frame_transform is None
                else [list(row) for row in self.frame_transform]
            ),
            "status": self.status,
            "reason": self.reason,
            "length_unit": self.length_unit,
            "body_count": len(self.bodies),
            "converted_count": len(self.converted),
            "solid_count": self.solid_count,
            "sources": sorted({b.source for b in self.bodies}),
            "volume_mm3": self.volume_mm3,
            "area_mm2": self.area_mm2,
            "centroid_mm": None if self.centroid_mm is None else list(self.centroid_mm),
            "bodies": [
                {
                    "body_id": b.body_id,
                    "source": b.source,
                    "resource_id": b.resource_id,
                    "payload_sha256": b.payload_sha256,
                    "byte_range": asdict(b.byte_range),
                    "status": b.status,
                    "face_count": b.face_count,
                    "solid_count": b.solid_count,
                    "volume_mm3": b.volume_mm3,
                    "area_mm2": b.area_mm2,
                    "centroid_mm": None
                    if b.centroid_mm is None
                    else list(b.centroid_mm),
                    "diagnostics": [asdict(d) for d in b.diagnostics],
                }
                for b in self.bodies
            ],
            "diagnostics": [asdict(d) for d in self.diagnostics],
        }


@dataclass(frozen=True)
class PartShapeIndex:
    """Part shapes of selected parts, with the per-part drawability verdicts.

    ``status`` is ``complete`` when every part that stores bodies converted
    completely, ``partial`` when at least one part converted and others did
    not, and ``unsupported`` when no part converted. Parts without bodies
    (assembly nodes, external occurrences) do not lower the index status.
    """

    source_sha256: str
    frame: ShapeFrame
    shapes: tuple[PartShape, ...]
    status: Status
    diagnostics: tuple[Diagnostic, ...]

    def shape(self, part_id: str) -> PartShape:
        for item in self.shapes:
            if item.part_id == part_id:
                return item
        raise KeyError(part_id)

    def to_rows(self) -> list[dict[str, Any]]:
        return [item.to_row() for item in self.shapes]


@dataclass
class _Context:
    document: Document
    parts: PartIndex
    saved: SavedBodyIndex
    csg: CsgIndex | None
    csg_diagnostics: tuple[Diagnostic, ...]
    schema: SchemaCatalog | None
    limits: PartShapeLimits
    api: dict[str, Any] | None
    api_error: Diagnostic | None
    base_shapes: dict[str, tuple[Any, tuple[str, ...], int, str] | IcadError]


def _rigid(matrix: Matrix4 | None) -> bool:
    if matrix is None or len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        return False
    if not all(math.isfinite(v) for row in matrix for v in row):
        return False
    if any(abs(matrix[3][j] - (1.0 if j == 3 else 0.0)) > 1e-12 for j in range(4)):
        return False
    columns = [[matrix[i][j] for i in range(3)] for j in range(3)]
    for a in range(3):
        for b in range(3):
            dot = sum(columns[a][k] * columns[b][k] for k in range(3))
            if abs(dot - (1.0 if a == b else 0.0)) > 1e-8:
                return False
    x, y, z = columns
    det = sum(
        x[i] * (y[(i + 1) % 3] * z[(i + 2) % 3] - y[(i + 2) % 3] * z[(i + 1) % 3])
        for i in range(3)
    )
    return abs(det - 1.0) <= 1e-8


def _compose(a: Matrix4, b: Matrix4) -> Matrix4:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4)
    )


def _context(
    document: Document,
    schema: SchemaCatalog | None,
    part_limits: PartLimits | None,
    limits: PartShapeLimits | None,
) -> _Context:
    from .document import Document

    if not isinstance(document, Document):
        raise TypeError("document must be an icadkit.Document")
    limits = PartShapeLimits() if limits is None else limits
    if not isinstance(limits, PartShapeLimits):
        raise TypeError("limits must be PartShapeLimits")
    if schema is not None and not isinstance(schema, SchemaCatalog):
        raise TypeError("schema must be a SchemaCatalog or None")
    parts = document.read_parts(limits=part_limits)
    saved = _read_saved_bodies(document, parts, limits.saved)
    csg: CsgIndex | None
    csg_diagnostics: tuple[Diagnostic, ...] = ()
    try:
        csg = _read_csg(document, parts, limits.csg)
    except IcadError as exc:
        csg = None
        csg_diagnostics = (exc.diagnostic,)
    api: dict[str, Any] | None = None
    api_error: Diagnostic | None = None
    try:
        from ._shape_occt import runtime

        api = runtime()
    except IcadError as exc:
        api_error = exc.diagnostic
    return _Context(
        document,
        parts,
        saved,
        csg,
        csg_diagnostics,
        schema,
        limits,
        api,
        api_error,
        {},
    )


def _base_shape(
    context: _Context, body: SavedBody
) -> tuple[Any, tuple[str, ...], int, str]:
    """The unplaced millimetre shape of the body's resource, built once."""
    assert body.resource_id is not None and context.api is not None
    cached = context.base_shapes.get(body.resource_id)
    if cached is None:
        try:
            geometry = _qualified_geometry(
                context.document,
                body,
                context.schema,
                context.limits.geometry,
                context.limits.saved,
            )
            assert geometry.brep is not None and geometry.source is not None
            from ._saved_occt import build_base

            shape, operations, faces = build_base(
                geometry.brep, context.limits.saved, context.api
            )
            cached = (shape, operations, faces, geometry.source.payload_sha256)
        except IcadError as exc:
            cached = exc
        except Exception as exc:
            cached = UnsupportedFormatError(
                Diagnostic(
                    "invalid",
                    "saved.kernel",
                    body.byte_range.start,
                    f"Saved boundary conversion failed: {exc}",
                )
            )
        context.base_shapes[body.resource_id] = cached
    if isinstance(cached, IcadError):
        raise cached
    return cached


_Body = SavedBody | CsgBody | NativeEntity


def _identity(body: _Body) -> tuple[str, str, ByteRange]:
    if isinstance(body, NativeEntity):
        return body.entity_id, body.owner_id, body.byte_range
    return body.body_id, body.owner_id, body.byte_range


def _failed(
    source: ShapeSource,
    body: _Body,
    diagnostics: tuple[Diagnostic, ...],
    resource_id: str | None,
) -> ShapeBody:
    status: Status = (
        "invalid"
        if diagnostics and diagnostics[0].category == "invalid"
        else "unsupported"
    )
    body_id, owner_id, byte_range = _identity(body)
    return ShapeBody(
        body_id, owner_id, source, resource_id, byte_range, status, diagnostics
    )


def _convert(
    context: _Context,
    source: ShapeSource,
    body: _Body,
    transform: Matrix4,
) -> tuple[ShapeBody, Any]:
    resource_id = body.resource_id if isinstance(body, SavedBody) else None
    body_id, owner_id, byte_range = _identity(body)
    if isinstance(body, NativeEntity):
        if body.primitive is None:
            return _failed(source, body, body.diagnostics, None), None
    elif body.status != "complete":
        return _failed(source, body, body.diagnostics, resource_id), None
    if context.api is None:
        assert context.api_error is not None
        return _failed(source, body, (context.api_error,), resource_id), None
    from ._shape_occt import mass_properties, place, solid_count

    payload: str | None = None
    faces: int | None = None
    try:
        if isinstance(body, SavedBody):
            assert body.world_transform is not None
            base, _, faces, payload = _base_shape(context, body)
            shape = place(base, _compose(transform, body.world_transform), context.api)
        elif isinstance(body, CsgBody):
            from ._csg_occt import build

            _validate_tokens(body.tokens, context.limits.csg)
            shape = build(body, context.limits.csg, context.api)
            if transform != _IDENTITY:
                shape = place(shape, transform, context.api)
        else:
            from ._csg_occt import _check, _primitive

            assert body.primitive is not None
            shape = _primitive(body.primitive, context.api)
            _check(shape, context.limits.csg, context.api)
            if transform != _IDENTITY:
                shape = place(shape, transform, context.api)
        volume, area, centroid = mass_properties(shape, context.api)
        if (
            not all(math.isfinite(v) for v in (volume, area, *centroid))
            or volume <= 0
            or area <= 0
        ):
            raise UnsupportedFormatError(
                Diagnostic(
                    "invalid",
                    "shape.mass_properties",
                    byte_range.start,
                    "Converted solid has no positive finite mass properties",
                )
            )
        solids = solid_count(shape, context.api)
    except IcadError as exc:
        return _failed(source, body, (exc.diagnostic,), resource_id), None
    except Exception as exc:
        diagnostic = Diagnostic(
            "invalid",
            "shape.kernel",
            byte_range.start,
            f"Kernel placement or measurement failed: {exc}",
        )
        return _failed(source, body, (diagnostic,), resource_id), None
    return (
        ShapeBody(
            body_id,
            owner_id,
            source,
            resource_id,
            byte_range,
            "complete",
            (),
            payload,
            faces,
            solids,
            volume,
            area,
            centroid,
        ),
        shape,
    )


def _unsupported(
    part: Part,
    frame: ShapeFrame,
    transform: Matrix4 | None,
    diagnostics: tuple[Diagnostic, ...],
    source_sha256: str,
) -> PartShape:
    return PartShape(
        part.part_id,
        part.name,
        source_sha256,
        frame,
        transform,
        (),
        "unsupported",
        diagnostics[0].code if diagnostics else None,
        diagnostics,
    )


def _part_shape(context: _Context, part: Part, frame: ShapeFrame) -> PartShape:
    sha = context.document.source_sha256
    transform: Matrix4 = _IDENTITY
    if frame == "part":
        world = part.placement.world_transform
        inverse = (
            _relative(world, _IDENTITY) if world is not None and _rigid(world) else None
        )
        if world is None or inverse is None:
            return _unsupported(
                part,
                frame,
                None,
                (
                    Diagnostic(
                        "unsupported",
                        "shape.frame",
                        part.byte_range.start,
                        "The part frame is not an evaluated rigid frame",
                    ),
                ),
                sha,
            )
        transform = inverse
    saved = [b for b in context.saved.bodies if b.owner_id == part.part_id]
    csg = (
        [b for b in context.csg.bodies if b.owner_id == part.part_id]
        if context.csg is not None
        else []
    )
    native = [
        e
        for e in part.entities
        if e.primitive is not None or e.geometry_status in ("unsupported", "invalid")
    ]
    saved_ok = any(b.status == "complete" for b in saved)
    csg_ok = any(b.status == "complete" for b in csg)
    chosen: list[tuple[ShapeSource, _Body]]
    if saved_ok or (saved and not csg_ok):
        chosen = [("saved", b) for b in saved]
    elif csg:
        chosen = [("csg", b) for b in csg]
    elif native:
        chosen = [("native", e) for e in native]
    else:
        code = "shape.external_reference" if part.is_external else "shape.no_bodies"
        message = (
            "External occurrences store their bodies in the referenced file"
            if part.is_external
            else "The part stores no saved final body, CSG result or primitive"
        )
        extra = (
            context.saved.diagnostics
            if context.saved.status == "unsupported"
            else context.csg_diagnostics
        )
        return _unsupported(
            part,
            frame,
            transform,
            (Diagnostic("unsupported", code, part.byte_range.start, message), *extra),
            sha,
        )
    if len(chosen) > context.limits.max_bodies:
        raise LimitExceededError(
            Diagnostic(
                "limit_exceeded",
                "shape.limit_bodies",
                part.byte_range.start,
                "Part body count exceeds max_bodies",
            )
        )
    bodies: list[ShapeBody] = []
    shapes: list[Any] = []
    for source, body in chosen:
        converted, shape = _convert(context, source, body, transform)
        bodies.append(converted)
        if shape is not None:
            shapes.append(shape)
    done = [b for b in bodies if b.status == "complete"]
    status: Status = (
        "complete" if len(done) == len(bodies) else "partial" if done else "unsupported"
    )
    failed = [b for b in bodies if b.status != "complete"]
    reason = failed[0].diagnostics[0].code if failed and failed[0].diagnostics else None
    volume = area = None
    centroid = None
    compound = None
    if done:
        from ._shape_occt import compound as make_compound

        assert context.api is not None
        compound = make_compound(shapes, context.api)
        volume = sum(b.volume_mm3 or 0.0 for b in done)
        area = sum(b.area_mm2 or 0.0 for b in done)
        centroid = tuple(
            sum(
                (b.volume_mm3 or 0.0) * (b.centroid_mm or (0.0, 0.0, 0.0))[i]
                for b in done
            )
            / volume
            for i in range(3)
        )
    return PartShape(
        part.part_id,
        part.name,
        sha,
        frame,
        transform,
        tuple(bodies),
        status,
        reason,
        (),
        volume,
        area,
        None if centroid is None else (centroid[0], centroid[1], centroid[2]),
        "mm",
        compound,
    )


def _check_frame(frame: str) -> ShapeFrame:
    if frame == "part":
        return "part"
    if frame == "world":
        return "world"
    raise ValueError('frame must be "part" or "world"')


def read_part_shape(
    document: Document,
    part_id: str,
    *,
    frame: ShapeFrame = "part",
    schema: SchemaCatalog | None = None,
    part_limits: PartLimits | None = None,
    limits: PartShapeLimits | None = None,
) -> PartShape:
    """Convert one part's qualified solids into a compound in the given frame.

    Saved final bodies are preferred; CSG results are used only for a part
    without a qualified saved body. Unknown part IDs raise ``KeyError``.
    """
    if not isinstance(part_id, str):
        raise TypeError("part_id must be a string")
    kind = _check_frame(frame)
    context = _context(document, schema, part_limits, limits)
    for part in context.parts.parts:
        if part.part_id == part_id:
            return _part_shape(context, part, kind)
    raise KeyError(part_id)


def read_part_shapes(
    document: Document,
    *,
    part_ids: Iterable[str] | None = None,
    frame: ShapeFrame = "part",
    schema: SchemaCatalog | None = None,
    part_limits: PartLimits | None = None,
    limits: PartShapeLimits | None = None,
) -> PartShapeIndex:
    """Convert every selected part (all parts by default) in one pass.

    Shared resources are built once and placed per occurrence. The index
    reports, per part, whether it can be drawn and why not.
    """
    kind = _check_frame(frame)
    context = _context(document, schema, part_limits, limits)
    if part_ids is None:
        selected = list(context.parts.parts)
    else:
        wanted = list(part_ids)
        if not all(isinstance(p, str) for p in wanted):
            raise TypeError("part_ids must be strings")
        by_id = {p.part_id: p for p in context.parts.parts}
        missing = [p for p in wanted if p not in by_id]
        if missing:
            raise KeyError(missing[0])
        selected = [by_id[p] for p in wanted]
    if len(selected) > context.limits.max_parts:
        raise LimitExceededError(
            Diagnostic(
                "limit_exceeded",
                "shape.limit_parts",
                None,
                "Selected part count exceeds max_parts",
            )
        )
    shapes = tuple(_part_shape(context, part, kind) for part in selected)
    with_bodies = [s for s in shapes if s.bodies]
    converted = [s for s in with_bodies if s.status != "unsupported"]
    status: Status = (
        "complete"
        if all(s.status == "complete" for s in with_bodies)
        else "partial"
        if converted
        else "unsupported"
    )
    diagnostics: tuple[Diagnostic, ...] = ()
    if context.api_error is not None:
        diagnostics += (context.api_error,)
    if context.saved.status == "unsupported":
        diagnostics += context.saved.diagnostics
    diagnostics += context.csg_diagnostics
    return PartShapeIndex(document.source_sha256, kind, shapes, status, diagnostics)
