"""View qualified native primitives, optional CSG results and part metadata.

CSG evaluation explicitly opts into the optional preview runtime.
Unknown entities and unloaded references remain in the inventory.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import tempfile
from dataclasses import asdict, dataclass, replace
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from ._viewer_io import ViewerServer, publish
from .csg import CsgLimits, _read_csg, evaluate_csg
from .document import Document
from .errors import (
    IcadError,
    InvalidFormatError,
    LimitExceededError,
    UnsupportedFormatError,
)
from .geometry import GeometryLimits
from .models import Diagnostic, ErrorCategory
from .native import NativePrimitive
from .parts import PartIndex, PartLimits, _determinant, _relative
from .references import AssemblyIndex, _point, _reference_dict
from .saved import SavedBodyLimits, _read_saved_bodies, evaluate_saved_body
from .schema import SchemaCatalog

__all__ = [
    "ViewerLimits",
    "ViewerResult",
    "ViewerServer",
    "write_native_viewer",
    "write_assembly_viewer",
    "serve_viewer",
]


@dataclass(frozen=True)
class ViewerLimits:
    max_triangles: int = 1_000_000
    max_output_bytes: int = 128 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if not 0 < value <= (1 << 63) - 1:
                raise ValueError(f"{name} must be between 1 and 2**63 - 1")


@dataclass(frozen=True)
class ViewerResult:
    directory: Path
    source_sha256: str
    scene_sha256: str
    part_count: int
    entity_count: int
    rendered_entities: int
    omitted_entities: int
    triangle_count: int
    output_bytes: int
    model_status: str = "partial"
    evaluated_csg_bodies: int = 0
    evaluated_saved_bodies: int = 0


def _limit(message: str) -> LimitExceededError:
    return LimitExceededError(
        Diagnostic("limit_exceeded", "viewer.limit_exceeded", None, message)
    )


def _mesh(primitive: NativePrimitive, segments: int) -> dict[str, Any]:
    """Generate outward triangles in the saved global frame (millimetres)."""
    vertices: list[tuple[float, ...]] = []
    triangles: list[int] = []
    edges: list[int] = []
    if primitive.kind == "box":
        assert primitive.x_bounds is not None and primitive.y_bounds is not None
        assert primitive.height is not None
        x0, x1 = primitive.x_bounds
        y0, y1 = primitive.y_bounds
        vertices = [
            (x0, y0, 0),
            (x1, y0, 0),
            (x1, y1, 0),
            (x0, y1, 0),
            (x0, y0, primitive.height),
            (x1, y0, primitive.height),
            (x1, y1, primitive.height),
            (x0, y1, primitive.height),
        ]
        for a, b, c, d in (
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        ):
            triangles.extend((a, b, c, a, c, d))
        edges = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7]
    elif primitive.kind == "sphere":
        assert primitive.radius is not None
        radius = primitive.radius
        rings = segments // 2
        vertices.append((0, 0, radius))
        for ring in range(1, rings):
            latitude = math.pi * ring / rings
            for i in range(segments):
                longitude = math.tau * i / segments
                vertices.append(
                    (
                        radius * math.sin(latitude) * math.cos(longitude),
                        radius * math.sin(latitude) * math.sin(longitude),
                        radius * math.cos(latitude),
                    )
                )
        bottom = len(vertices)
        vertices.append((0, 0, -radius))
        for i in range(segments):
            j = (i + 1) % segments
            triangles.extend((0, 1 + i, 1 + j))
            for ring in range(rings - 2):
                a, b = 1 + ring * segments + i, 1 + ring * segments + j
                c, d = a + segments, b + segments
                triangles.extend((a, c, b, b, c, d))
            last = 1 + (rings - 2) * segments
            triangles.extend((last + i, bottom, last + j))
            middle = 1 + (rings // 2 - 1) * segments
            edges.extend((middle + i, middle + j))
        for i in range(0, segments, max(1, segments // 4)):
            line = [0, *(1 + ring * segments + i for ring in range(rings - 1)), bottom]
            for a, b in zip(line, line[1:], strict=False):
                edges.extend((a, b))
    elif primitive.kind == "cone":
        assert primitive.radius is not None and primitive.top_radius is not None
        assert primitive.height is not None
        for i in range(segments):
            angle = math.tau * i / segments
            vertices.append(
                (
                    primitive.radius * math.cos(angle),
                    primitive.radius * math.sin(angle),
                    0.0,
                )
            )
        pointed = primitive.top_radius == 0
        if not pointed:
            for i in range(segments):
                angle = math.tau * i / segments
                vertices.append(
                    (
                        primitive.top_radius * math.cos(angle),
                        primitive.top_radius * math.sin(angle),
                        primitive.height,
                    )
                )
        bottom = len(vertices)
        vertices.extend(((0.0, 0.0, 0.0), (0.0, 0.0, primitive.height)))
        top = bottom + 1
        for i in range(segments):
            j = (i + 1) % segments
            triangles.extend((bottom, j, i))
            edges.extend((i, j))
            if pointed:
                triangles.extend((i, j, top))
            else:
                triangles.extend(
                    (
                        i,
                        j,
                        i + segments,
                        j,
                        j + segments,
                        i + segments,
                        top,
                        i + segments,
                        j + segments,
                    )
                )
                edges.extend((i + segments, j + segments))
            if i % max(1, segments // 4) == 0:
                edges.extend((i, top if pointed else i + segments))
    elif primitive.kind == "torus":
        assert primitive.major_radius is not None and primitive.minor_radius is not None
        bands = max(8, segments // 2)
        for i in range(segments):
            u = math.tau * i / segments
            for j in range(bands):
                v = math.tau * j / bands
                radial = primitive.major_radius + primitive.minor_radius * math.cos(v)
                vertices.append(
                    (
                        radial * math.cos(u),
                        radial * math.sin(u),
                        primitive.minor_radius * math.sin(v),
                    )
                )
        for i in range(segments):
            for j in range(bands):
                a = i * bands + j
                b = ((i + 1) % segments) * bands + j
                c = i * bands + (j + 1) % bands
                d = ((i + 1) % segments) * bands + (j + 1) % bands
                triangles.extend((a, b, c, b, d, c))
                if j in (0, bands // 2):
                    edges.extend((a, b))
                if i % max(1, segments // 4) == 0:
                    edges.extend((a, c))
    else:
        assert primitive.kind == "cylinder" and primitive.radius is not None
        assert primitive.height is not None
        for z in (0.0, primitive.height):
            for i in range(segments):
                angle = i * math.tau / segments
                vertices.append(
                    (
                        primitive.radius * math.cos(angle),
                        primitive.radius * math.sin(angle),
                        z,
                    )
                )
        vertices.extend(((0, 0, 0), (0, 0, primitive.height)))
        for i in range(segments):
            j = (i + 1) % segments
            a, b = i + segments, j + segments
            triangles.extend(
                (i, j, b, i, b, a, 2 * segments, j, i, 2 * segments + 1, a, b)
            )
            edges.extend((i, j, a, b))
        for i in range(0, segments, max(1, segments // 4)):
            edges.extend((i, i + segments))
    from .parts import _determinant

    m = primitive.world_transform
    if _determinant(m) < 0:
        for at in range(0, len(triangles), 3):
            triangles[at + 1], triangles[at + 2] = triangles[at + 2], triangles[at + 1]
    positions = [
        sum(m[row][k] * vertex[k] for k in range(3)) + m[row][3]
        for vertex in vertices
        for row in range(3)
    ]
    if not all(math.isfinite(value) for value in positions):
        raise InvalidFormatError(
            Diagnostic(
                "invalid",
                "viewer.coordinates",
                primitive.byte_range.start,
                "Native primitive overflows world coordinates",
            )
        )
    return {"positions": positions, "triangles": triangles, "edges": edges}


def _mesh_triangle_count(primitive: NativePrimitive, segments: int) -> int:
    if primitive.kind == "box":
        return 12
    if primitive.kind == "sphere":
        return 2 * segments * (segments // 2 - 1)
    if primitive.kind == "torus":
        return 2 * segments * max(8, segments // 2)
    if primitive.kind == "cone" and primitive.top_radius == 0:
        return 2 * segments
    return 4 * segments


def _native_scene(
    document: Document,
    *,
    part_limits: PartLimits | None = None,
    limits: ViewerLimits | None = None,
    cylinder_segments: int = 64,
    csg: bool = False,
    csg_limits: CsgLimits | None = None,
    saved_brep: bool = False,
    saved_limits: SavedBodyLimits | None = None,
    schema: SchemaCatalog | None = None,
    geometry_limits: GeometryLimits | None = None,
    _index: PartIndex | None = None,
) -> dict[str, Any]:
    """Build an unnormalized single-file scene without opening references.

    Native global frames are applied exactly once. Saved-hidden entities start
    hidden; unknown visibility is shown and labelled unknown. Colors are
    illustrative. Unsupported geometry is listed, never synthesized. A result
    with zero rendered entities is valid when a native part inventory was read.
    Unreadable native inventories raise instead of presenting an empty model.
    """
    if not isinstance(document, Document):
        raise TypeError("document must be an icadkit.Document")
    if not isinstance(saved_brep, bool):
        raise TypeError("saved_brep must be a bool")
    if csg and saved_brep:
        raise ValueError("Choose csg or saved_brep evaluation")
    if schema is not None and not saved_brep:
        raise ValueError("schema requires saved_brep=True")
    saved_limits = SavedBodyLimits() if saved_limits is None else saved_limits
    if not isinstance(saved_limits, SavedBodyLimits):
        raise TypeError("saved_limits must be SavedBodyLimits")
    if not isinstance(csg, bool):
        raise TypeError("csg must be a bool")
    csg_limits = CsgLimits() if csg_limits is None else csg_limits
    if not isinstance(csg_limits, CsgLimits):
        raise TypeError("csg_limits must be CsgLimits")
    if isinstance(cylinder_segments, bool) or not isinstance(cylinder_segments, int):
        raise TypeError("cylinder_segments must be an integer")
    if not 8 <= cylinder_segments <= 256:
        raise ValueError("cylinder_segments must be between 8 and 256")
    limits = ViewerLimits() if limits is None else limits
    if not isinstance(limits, ViewerLimits):
        raise TypeError("limits must be ViewerLimits")
    index = document.read_parts(limits=part_limits) if _index is None else _index
    if not index.parts:
        # No indexed entities can mean the native reader could not enter this
        # format at all. Keep that distinct from a qualified, empty root part.
        category: ErrorCategory = (
            "invalid" if index.status.index == "invalid" else "unsupported"
        )
        issue = next(
            (d for d in index.diagnostics if d.category == category),
            next(iter(index.diagnostics), None),
        )
        message = issue.message if issue else "No native part inventory could be read"
        if issue is not None and issue.code in (
            "parts.profile",
            "parts.record_profile",
        ):
            raw = document.header.raw_version
            known_profiles = {
                b"\x00\x07\x00\x07": "V7L7",
                b"\x00\x08\x00\x01": "V8L1",
                b"\x00\x08\x00\x02": "V8L2",
                b"\x00\x08\x00\x03": "V8L3",
            }
            profile = known_profiles.get(raw, "0x" + raw.hex())
            message = f"File profile {profile}: {message}"
        message += ". No native parts were read; this is not an empty model."
        error = InvalidFormatError if category == "invalid" else UnsupportedFormatError
        raise error(
            Diagnostic(
                category,
                issue.code if issue else "viewer.native_index",
                issue.byte_offset if issue else None,
                message,
            )
        )
    count = sum(len(p.entities) for p in index.parts)
    triangles = sum(
        _mesh_triangle_count(e.primitive, cylinder_segments)
        for p in index.parts
        for e in p.entities
        if e.primitive is not None
    )
    if triangles > limits.max_triangles:
        raise _limit("Native tessellation exceeds max_triangles")
    meshes = {
        e.entity_id: _mesh(e.primitive, cylinder_segments)
        for p in index.parts
        for e in p.entities
        if e.primitive is not None
    }
    rows = cast(list[dict[str, Any]], index.to_rows(include_root=True))
    csg_diagnostics: list[dict[str, Any]] = []
    csg_summary = {"enabled": csg, "bodies": 0, "evaluated": 0, "omitted": 0}
    if csg:
        programs = _read_csg(document, index, csg_limits)
        csg_diagnostics = [asdict(d) for d in programs.diagnostics]
        entities = {e["entity_id"]: e for p in rows for e in p["entities"]}
        csg_summary["bodies"] = len(programs.bodies)
        for body in programs.bodies:
            row = entities[body.body_id]
            detail: dict[str, Any] = {
                "status": body.status,
                "tree_id": body.tree_id,
                "program_range": asdict(body.program_range)
                if body.program_range
                else None,
                "diagnostics": [asdict(d) for d in body.diagnostics],
            }
            row["csg"] = detail
            if body.status != "complete":
                continue
            remaining = limits.max_triangles - triangles
            if remaining <= 0:
                raise _limit("CSG tessellation exceeds max_triangles")
            try:
                result = evaluate_csg(
                    body,
                    limits=replace(
                        csg_limits,
                        max_triangles=min(csg_limits.max_triangles, remaining),
                    ),
                )
            except IcadError as exc:
                if isinstance(exc, LimitExceededError) or exc.diagnostic.code in (
                    "csg.missing_dependency",
                    "csg.backend_version",
                ):
                    raise
                detail.update(
                    status=exc.diagnostic.category, diagnostics=[asdict(exc.diagnostic)]
                )
                continue
            meshes[body.body_id] = {
                "positions": result.positions,
                "triangles": result.triangles,
                "edges": result.edges,
            }
            triangles += len(result.triangles) // 3
            csg_summary["evaluated"] += 1
            detail.update(
                volume_mm3=result.volume_mm3,
                area_mm2=result.area_mm2,
                centroid_mm=result.centroid_mm,
                solid_count=result.solid_count,
                linear_deflection_mm=result.linear_deflection_mm,
            )
            row["geometry_status"] = "complete"
            row["appearance"] = {
                "color_index": body.appearance.color_index,
                "visible": body.appearance.visible,
                "layer": body.appearance.layer,
                "status": body.appearance.status,
            }
            for operand in body.operands:
                if operand.entity_id != body.body_id:
                    entities[operand.entity_id]["csg_role"] = "operand"
                    entities[operand.entity_id]["csg_body_id"] = body.body_id
        csg_summary["omitted"] = len(programs.bodies) - csg_summary["evaluated"]
    saved_summary = {
        "enabled": saved_brep,
        "bodies": 0,
        "evaluated": 0,
        "omitted": 0,
        "resources_evaluated": 0,
    }
    if saved_brep:
        evaluated_resources: set[str] = set()
        saved = _read_saved_bodies(document, index, saved_limits)
        csg_diagnostics.extend(asdict(d) for d in saved.diagnostics)
        saved_summary["bodies"] = len(saved.bodies)
        entities = {e["entity_id"]: e for p in rows for e in p["entities"]}
        for saved_body in saved.bodies:
            row = entities[saved_body.body_id]
            detail = {
                "status": saved_body.status,
                "resource_id": saved_body.resource_id,
                "resource_source_id": saved_body.resource_source_id,
                "binding_kind": saved_body.binding_kind,
                "frame_source": saved_body.frame_source,
                "diagnostics": [asdict(d) for d in saved_body.diagnostics],
            }
            row["saved_body"] = detail
            if saved_body.status != "complete":
                continue
            remaining = limits.max_triangles - triangles
            if remaining <= 0:
                raise _limit("Saved body tessellation exceeds max_triangles")
            try:
                result = evaluate_saved_body(
                    document,
                    saved_body,
                    schema=schema,
                    geometry_limits=geometry_limits,
                    limits=replace(
                        saved_limits,
                        max_triangles=min(saved_limits.max_triangles, remaining),
                    ),
                )
            except IcadError as exc:
                if isinstance(exc, LimitExceededError) or exc.diagnostic.code in (
                    "csg.missing_dependency",
                    "csg.backend_version",
                ):
                    raise
                detail.update(
                    status=exc.diagnostic.category, diagnostics=[asdict(exc.diagnostic)]
                )
                continue
            meshes[saved_body.body_id] = {
                "positions": result.positions,
                "triangles": result.triangles,
                "edges": result.edges,
            }
            triangles += len(result.triangles) // 3
            saved_summary["evaluated"] += 1
            evaluated_resources.add(result.resource_id)
            detail.update(
                volume_mm3=result.volume_mm3,
                area_mm2=result.area_mm2,
                centroid_mm=result.centroid_mm,
                solid_count=result.solid_count,
                face_count=result.face_count,
                payload_sha256=result.payload_sha256,
                linear_deflection_mm=result.linear_deflection_mm,
                representation_operations=result.representation_operations,
            )
            row["geometry_status"] = "complete"
            row["appearance"] = {
                "color_index": saved_body.appearance.color_index,
                "visible": saved_body.appearance.visible,
                "layer": saved_body.appearance.layer,
                "status": saved_body.appearance.status,
            }
        saved_summary["omitted"] = len(saved.bodies) - saved_summary["evaluated"]
        saved_summary["resources_evaluated"] = len(evaluated_resources)
        for part in index.parts:
            for entity in part.entities:
                raw = document.source_bytes(entity.byte_range)
                if len(raw) >= 16 and raw[13] & 1:
                    entities[entity.entity_id]["saved_role"] = "component"
    metadata_only = index.source_length_unit is None
    scene = {
        "schema_version": 1,
        "source_sha256": document.source_sha256,
        "model_status": "partial",
        "scope": "native_part_inventory"
        if metadata_only
        else "qualified_saved_brep"
        if saved_brep
        else "qualified_native_csg"
        if csg
        else "qualified_native_primitives",
        "coordinate_system": None if metadata_only else "3DGLOBAL",
        "length_unit": None if metadata_only else "mm",
        "render_origin_mm": [0.0] * 3,
        "render_scale_mm": 1.0,
        "bounds_mm": None,
        "appearance": (
            "unavailable"
            if metadata_only
            else "illustrative_colors_saved_entity_visibility"
        ),
        "part_status": asdict(index.status),
        "resource_count": len(document.resources),
        "resource_index_status": document.resource_index_status,
        "diagnostics": [asdict(d) for d in (*document.diagnostics, *index.diagnostics)]
        + csg_diagnostics,
        "opaque_ranges": [asdict(r) for r in index.opaque_ranges],
        "parts": rows,
        "csg": csg_summary,
        "saved_brep": saved_summary,
        "meshes": meshes,
        "summary": {
            "parts": len(index.parts),
            "entities": count,
            "rendered": len(meshes),
            "omitted": count - len(meshes),
            "triangles": triangles,
            "cylinder_segments": cylinder_segments,
        },
    }
    return scene


def _destination(destination: str | Path) -> Path:
    if not isinstance(destination, (str, Path)):
        raise TypeError("destination must be a path")
    output = Path(destination)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Viewer destination already exists: {output}")
    return output


def _write_scene(
    scene: dict[str, Any], destination: str | Path, limits: ViewerLimits
) -> ViewerResult:
    output = _destination(destination)
    meshes = scene["meshes"]
    lower, upper = [math.inf] * 3, [-math.inf] * 3
    for mesh in meshes.values():
        for axis in range(3):
            lower[axis] = min(lower[axis], min(mesh["positions"][axis::3]))
            upper[axis] = max(upper[axis], max(mesh["positions"][axis::3]))
    # Normalize before WebGL float32 conversion, retaining the original frame.
    origin = [lower[i] / 2 + upper[i] / 2 for i in range(3)] if meshes else [0.0] * 3
    scale = (
        max((upper[i] / 2 - lower[i] / 2 for i in range(3)), default=0)
        if meshes
        else 1.0
    )
    if not math.isfinite(scale) or scale <= 0:
        raise InvalidFormatError(
            Diagnostic(
                "invalid",
                "viewer.extent",
                None,
                "Native scene extent cannot be represented for display",
            )
        )
    for mesh in meshes.values():
        mesh["positions"] = [
            (value / scale - origin[i % 3] / scale)
            if abs(value - origin[i % 3]) == math.inf
            else (value - origin[i % 3]) / scale
            for i, value in enumerate(mesh["positions"])
        ]
    scene.update(
        render_origin_mm=origin,
        render_scale_mm=scale,
        bounds_mm=[lower, upper] if meshes else None,
    )
    # iterencode enforces the byte limit while writing, before publishing output.
    with tempfile.TemporaryDirectory(prefix="icadkit-native-viewer-") as temporary:
        staged = Path(temporary)
        total = 0
        assets = files("icadkit").joinpath("_viewer")
        for name in ("index.html", "viewer.js", "viewer.css"):
            content = assets.joinpath(name).read_bytes()
            total += len(content)
            if total > limits.max_output_bytes:
                raise _limit("Viewer assets exceed max_output_bytes")
            (staged / name).write_bytes(content)
        digest = hashlib.sha256()
        with (staged / "scene.json").open("wb") as stream:
            for chunk in json.JSONEncoder(
                ensure_ascii=True, allow_nan=False, separators=(",", ":")
            ).iterencode(scene):
                encoded = chunk.encode("utf-8")
                total += len(encoded)
                if total > limits.max_output_bytes:
                    raise _limit("Viewer scene exceeds max_output_bytes")
                digest.update(encoded)
                stream.write(encoded)
        publish(staged, output)
    return ViewerResult(
        output,
        scene["source_sha256"],
        digest.hexdigest(),
        scene["summary"]["parts"],
        scene["summary"]["entities"],
        len(meshes),
        scene["summary"]["omitted"],
        scene["summary"]["triangles"],
        total,
        evaluated_csg_bodies=int(scene["csg"]["evaluated"]),
        evaluated_saved_bodies=int(scene["saved_brep"]["evaluated"]),
    )


def write_native_viewer(
    document: Document,
    destination: str | Path,
    *,
    part_limits: PartLimits | None = None,
    limits: ViewerLimits | None = None,
    cylinder_segments: int = 64,
    csg: bool = False,
    csg_limits: CsgLimits | None = None,
    saved_brep: bool = False,
    saved_limits: SavedBodyLimits | None = None,
    schema: SchemaCatalog | None = None,
    geometry_limits: GeometryLimits | None = None,
) -> ViewerResult:
    """Write a single-file partial viewer; external files are never opened."""
    _destination(destination)
    scene = _native_scene(
        document,
        part_limits=part_limits,
        limits=limits,
        cylinder_segments=cylinder_segments,
        csg=csg,
        csg_limits=csg_limits,
        saved_brep=saved_brep,
        saved_limits=saved_limits,
        schema=schema,
        geometry_limits=geometry_limits,
    )
    return _write_scene(
        scene, destination, ViewerLimits() if limits is None else limits
    )


def write_assembly_viewer(
    assembly: AssemblyIndex,
    destination: str | Path,
    *,
    limits: ViewerLimits | None = None,
    cylinder_segments: int = 64,
    csg: bool = False,
    csg_limits: CsgLimits | None = None,
    saved_brep: bool = False,
    saved_limits: SavedBodyLimits | None = None,
    schema: SchemaCatalog | None = None,
    geometry_limits: GeometryLimits | None = None,
) -> ViewerResult:
    """Render a resolved snapshot without reopening any reference files.

    Geometry is evaluated once per source document, then placed per occurrence.
    Saved B-Reps and CSG results keep analytic metrics under rigid reflection.
    The usual triangle/output limits apply to all placed occurrences together.
    """
    if not isinstance(assembly, AssemblyIndex):
        raise TypeError("assembly must be AssemblyIndex")
    _destination(destination)
    limits = ViewerLimits() if limits is None else limits
    if not isinstance(limits, ViewerLimits):
        raise TypeError("limits must be ViewerLimits")
    sources: dict[str, dict[str, Any]] = {}
    source_rows: dict[tuple[str, str], dict[str, Any]] = {}
    source_entities: dict[tuple[str, str], dict[str, Any]] = {}
    unique_triangles = 0
    for doc in assembly.documents:
        # Every loaded document contributes at least one placed definition.
        remaining = limits.max_triangles - unique_triangles
        if remaining <= 0 and any(p.entities for p in doc.parts.parts):
            raise _limit("Assembly source tessellation exceeds max_triangles")
        source = _native_scene(
            doc.document,
            limits=replace(limits, max_triangles=max(1, remaining)),
            cylinder_segments=cylinder_segments,
            csg=csg,
            csg_limits=csg_limits,
            saved_brep=saved_brep,
            saved_limits=saved_limits,
            schema=schema,
            geometry_limits=geometry_limits,
            _index=doc.parts,
        )
        unique_triangles += source["summary"]["triangles"]
        sources[doc.document_id] = source
        for part in source["parts"]:
            source_rows[doc.document_id, part["part_id"]] = part
            for entity in part["entities"]:
                source_entities[doc.document_id, entity["entity_id"]] = entity
    meshes: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    triangles = count = 0
    cs = {"enabled": csg, "bodies": 0, "evaluated": 0, "omitted": 0}
    ss = {
        "enabled": saved_brep,
        "bodies": 0,
        "evaluated": 0,
        "omitted": 0,
        "resources_evaluated": 0,
    }
    resources: set[tuple[str, str]] = set()
    owners = {o.occurrence_id: o for o in assembly.occurrences}
    for occurrence in assembly.occurrences:
        oid = occurrence.occurrence_id
        row = copy.deepcopy(
            source_rows[occurrence.source_document_id, occurrence.source_part.part_id]
        )
        row.update(
            part_id=oid,
            parent_id=occurrence.parent_id,
            is_root=occurrence.parent_id is None,
            source_document_id=occurrence.source_document_id,
            source_part_id=occurrence.source_part.part_id,
            source_is_mirror=occurrence.source_part.is_mirror,
            definition_document_id=occurrence.definition_document_id,
            definition_id=(
                f"{occurrence.definition_document_id}/{occurrence.content_part_id}"
                if occurrence.definition_document_id
                else None
            ),
            world_transform=occurrence.world_transform,
            orientation_world_transform=occurrence.orientation_world_transform,
            placement_status="complete"
            if occurrence.world_transform is not None
            else "unsupported",
            hierarchy_status=assembly.status,
            is_mirror=_determinant(occurrence.orientation_world_transform) < 0
            if occurrence.orientation_world_transform
            else None,
            assembly_reference=_reference_dict(occurrence.reference)
            if occurrence.reference
            else None,
            entities=[],
        )
        parent = owners.get(occurrence.parent_id or "")
        row["local_transform"] = (
            _relative(parent.world_transform, occurrence.world_transform)
            if parent and parent.world_transform and occurrence.world_transform
            else occurrence.world_transform
            if parent is None
            else None
        )
        row["orientation_local_transform"] = (
            _relative(
                parent.orientation_world_transform,
                occurrence.orientation_world_transform,
            )
            if parent
            and parent.orientation_world_transform
            and occurrence.orientation_world_transform
            else occurrence.orientation_world_transform
            if parent is None
            else None
        )
        for attribute in [*row["properties"], *row["opaque_attributes"]]:
            attribute["source_owner_id"] = attribute["owner_id"]
            attribute["source_document_id"] = occurrence.source_document_id
            attribute["owner_id"] = oid
        if occurrence.reference and row["external_reference"]:
            row["external_reference"].update(
                status=occurrence.reference.status,
                path=str(occurrence.reference.resolved_path)
                if occurrence.reference.resolved_path
                else None,
            )
        for placed in occurrence.entities:
            count += 1
            source_id = placed.source_entity.entity_id
            entity = copy.deepcopy(
                source_entities[placed.source_document_id, source_id]
            )
            entity.update(
                entity_id=placed.entity_id,
                owner_id=oid,
                source_entity_id=source_id,
                source_document_id=placed.source_document_id,
                source_owner_id=placed.source_entity.owner_id,
            )
            if placed.primitive:
                entity["primitive"]["world_transform"] = (
                    placed.primitive.world_transform
                )
            for key in ("csg_body_id", "saved_body_id"):
                if key in entity:
                    entity[key] = f"{oid}/{entity[key]}"
            src = sources[placed.source_document_id]
            mesh = src["meshes"].get(source_id)
            if mesh:
                triangles += len(mesh["triangles"]) // 3
                if triangles > limits.max_triangles:
                    raise _limit("Assembly occurrences exceed max_triangles")
                coords = mesh["positions"]
                positions = [
                    v
                    for i in range(0, len(coords), 3)
                    for v in _point(placed.document_transform, coords[i : i + 3])
                ]
                if not all(math.isfinite(v) for v in positions):
                    raise InvalidFormatError(
                        Diagnostic(
                            "invalid",
                            "references.placement",
                            None,
                            "Placed mesh overflowed",
                        )
                    )
                indices = list(mesh["triangles"])
                if _determinant(placed.document_transform) < 0:
                    for i in range(0, len(indices), 3):
                        indices[i + 1], indices[i + 2] = indices[i + 2], indices[i + 1]
                meshes[placed.entity_id] = {
                    "positions": positions,
                    "triangles": indices,
                    "edges": mesh["edges"],
                }
            for key, summary in (("saved_body", ss), ("csg", cs)):
                detail = entity.get(key)
                if detail is None:
                    continue
                summary["bodies"] += 1
                if mesh:
                    summary["evaluated"] += 1
                    detail["centroid_mm"] = _point(
                        placed.document_transform, detail["centroid_mm"]
                    )
                    if key == "saved_body":
                        resources.add(
                            (placed.source_document_id, detail["resource_id"])
                        )
                        detail["representation_operations"] = [
                            *detail["representation_operations"],
                            "rigid external occurrence placement; "
                            "reverse winding for reflection",
                        ]
            row["entities"].append(entity)
        rows.append(row)
    cs["omitted"] = cs["bodies"] - cs["evaluated"]
    ss["omitted"] = ss["bodies"] - ss["evaluated"]
    ss["resources_evaluated"] = len(resources)
    scene = copy.copy(sources[assembly.documents[0].document_id])
    statuses = {}
    for key in scene["part_status"]:
        values = {source["part_status"][key] for source in sources.values()}
        statuses[key] = (
            next(iter(values))
            if len(values) == 1
            else "invalid"
            if "invalid" in values
            else "partial"
        )
    scene.update(
        scope="qualified_assembly",
        assembly=assembly.to_dict(),
        parts=rows,
        meshes=meshes,
        csg=cs,
        saved_brep=ss,
        resource_count=sum(len(d.document.resources) for d in assembly.documents),
        resource_index_status="complete"
        if all(
            d.document.resource_index_status == "complete" for d in assembly.documents
        )
        else "partial",
        diagnostics=[
            {**d, "source_document_id": docid}
            for docid, source in sources.items()
            for d in source["diagnostics"]
        ]
        + [asdict(d) for d in assembly.diagnostics],
        opaque_ranges=[
            {**r, "source_document_id": docid}
            for docid, source in sources.items()
            for r in source["opaque_ranges"]
        ],
        part_status={
            **statuses,
            "references": assembly.status,
            "hierarchy": assembly.status,
            "definitions": assembly.status,
            "placements": "complete"
            if all(o.world_transform is not None for o in assembly.occurrences)
            else "partial",
        },
        summary={
            "parts": len(rows),
            "entities": count,
            "rendered": len(meshes),
            "omitted": count - len(meshes),
            "triangles": triangles,
            "cylinder_segments": cylinder_segments,
        },
    )
    return _write_scene(scene, destination, limits)


def serve_viewer(directory: str | Path, *, port: int = 0) -> ViewerServer:
    """Prepare a server; use its context manager to start and stop it."""
    return ViewerServer(directory, port=port)
