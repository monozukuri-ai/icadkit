"""Pinned optional backend adapter for analytic saved solid boundaries."""

from __future__ import annotations

import math
from dataclasses import fields
from importlib import import_module
from typing import Any

from ._csg_occt import _runtime, _shapes, mesh_shape
from ._preview_bridge import bridge
from .csg import CsgLimits, CsgMesh
from .errors import IcadError, InvalidFormatError, LimitExceededError
from .geometry import Brep
from .models import Diagnostic
from .saved import SavedBody, SavedBodyLimits, SavedBodyMesh, _error


def _preflight(model: Any, limits: SavedBodyLimits) -> None:
    if not model.complete or not model.topology.valid or not model.bodies:
        raise _error("topology", "Saved B-Rep must be complete and topologically valid")
    if any(b.kind.value != "solid" for b in model.bodies):
        raise _error("body_kind", "Only saved solid bodies are qualified")
    for value in (*model.edges, *model.vertices):
        if value.tolerance is not None and (
            not math.isfinite(value.tolerance)
            or not 0 < value.tolerance * 1000 <= limits.max_source_tolerance_mm
        ):
            raise _error(
                "source_tolerance", "Invalid or excessive source edge/vertex tolerance"
            )
    for b in model.bodies:
        if (
            not math.isfinite(b.linear_resolution)
            or not 0 < b.linear_resolution * 1000 <= limits.max_source_tolerance_mm
        ):
            raise _error(
                "source_tolerance",
                "Body resolution exceeds the qualified source-tolerance bound",
            )
    curves = {c.id: c for c in model.curves}
    for c in model.curves:
        if (
            c.kind.value not in ("line", "circle", "trimmed")
            or c.sense.value == "unknown"
        ):
            raise _error(
                "curve", "Only analytic lines/circles and explicit trims are qualified"
            )
        if c.kind.value == "trimmed" and curves[
            c.definition.basis_curve
        ].kind.value not in ("line", "circle"):
            raise _error("curve", "Nested or nonanalytic curve trims are not qualified")
    if any(
        s.kind.value not in ("plane", "cylinder") or s.sense.value == "unknown"
        for s in model.surfaces
    ):
        raise _error("surface", "Only saved planes and cylinders are qualified")
    if any(f.sense.value == "unknown" or not f.loops for f in model.faces):
        raise _error("face", "Saved faces require explicit oriented boundary loops")
    if any(
        h.dummy or h.edge is None or h.sense.value == "unknown"
        for h in model.half_edges
    ):
        raise _error("half_edge", "Unqualified saved half-edge")


def _build(
    model: Any, options: Any, api: dict[str, Any], limits: SavedBodyLimits
) -> Any:
    # These construction classes are deliberately pinned to parasolid-kit 0.2.0.
    # No dependency monkeypatch or source-data mutation is performed.
    factory = import_module("parasolid_kit.interop.occt.geometry").GeometryFactory(
        options
    )
    cls = import_module(
        "parasolid_kit.interop.occt.parametric"
    ).ParametricTopologyBuilder
    builder = cls(model, factory)
    builder._build_vertices()
    for edge in model.edges:
        builder._build_edge(edge)
    for face in model.faces:
        builder._build_face(face)
    for face in model.faces:
        builder._finish_face(face)
    kernel = api["BRep"].BRep_Builder()
    for face in model.faces:
        shape = api["TopoDS"].TopoDS.Face_s(
            builder.context.Apply(builder.face_shapes[face.id])
        )
        # Generated seam edges can lose the input resolution and fall back to
        # OCCT's tighter default. Restore the declared body precision; do not
        # move surfaces/vertices, sew faces or grow the source error budget.
        for edge in _shapes(shape, api["TopAbs"].TopAbs_EDGE, api):
            item = api["TopoDS"].TopoDS.Edge_s(edge)
            kernel.UpdateEdge(item, builder.precision)
            if api["BRep"].BRep_Tool.Tolerance_s(item) > limits.max_source_tolerance_mm:
                raise _error(
                    "output_tolerance", "Kernel edge tolerance exceeds configured bound"
                )
        for vertex in _shapes(shape, api["TopAbs"].TopAbs_VERTEX, api):
            item = api["TopoDS"].TopoDS.Vertex_s(vertex)
            kernel.UpdateVertex(item, builder.precision)
            if api["BRep"].BRep_Tool.Tolerance_s(item) > limits.max_source_tolerance_mm:
                raise _error(
                    "output_tolerance",
                    "Kernel vertex tolerance exceeds configured bound",
                )
        if not api["BRepCheck"].BRepCheck_Analyzer(shape).IsValid():
            raise _error(
                "invalid_face", f"Saved face {face.id} failed kernel validation"
            )
        builder.face_shapes[face.id] = shape
    builder._validate_source_boundaries()
    if builder.diagnostics:
        raise _error(
            "conversion_diagnostics",
            "Saved boundary conversion has unresolved diagnostics",
        )
    shape = builder._make_root(builder._build_bodies())
    count = sum(1 for _ in _shapes(shape, api["TopAbs"].TopAbs_FACE, api))
    if count != len(model.faces):
        raise _error("face_coverage", "Saved face count changed during conversion")
    return shape, tuple(
        dict.fromkeys([*builder.operations, "restore_declared_body_resolution"])
    )


def convert(
    brep: Brep,
    body: SavedBody,
    payload_sha256: str,
    limits: SavedBodyLimits,
    deflection: float,
) -> SavedBodyMesh:
    api = _runtime()
    try:
        model = bridge(brep)
        _preflight(model, limits)
        options = import_module(
            "parasolid_kit.interop.occt.options"
        ).OcctConversionOptions(source_unit="m", target_unit="mm")
        shape, operations = _build(model, options, api, limits)
        transform = api["gp"].gp_Trsf()
        assert body.world_transform is not None
        transform.SetValues(*(v for row in body.world_transform[:3] for v in row))
        shape = (
            import_module("OCP.BRepBuilderAPI")
            .BRepBuilderAPI_Transform(shape, transform, True)
            .Shape()
        )
        bounded = CsgLimits(
            max_subshapes=limits.max_subshapes,
            max_triangles=limits.max_triangles,
            max_vertices=limits.max_vertices,
        )
        mesh = mesh_shape(shape, body.body_id, bounded, deflection, api)
        assert body.resource_id is not None
        return SavedBodyMesh(
            **{f.name: getattr(mesh, f.name) for f in fields(CsgMesh)},
            resource_id=body.resource_id,
            payload_sha256=payload_sha256,
            face_count=len(model.faces),
            representation_operations=operations,
        )
    except (IcadError, LimitExceededError):
        raise
    except Exception as exc:
        raise InvalidFormatError(
            Diagnostic(
                "invalid",
                "saved.kernel",
                body.byte_range.start,
                f"Saved boundary conversion failed: {exc}",
            )
        ) from exc
