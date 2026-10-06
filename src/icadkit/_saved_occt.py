"""Pinned optional backend adapter for analytic saved solid boundaries."""

from __future__ import annotations

import math
from dataclasses import fields, replace
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
    basis = ("line", "circle", "ellipse", "intersection", "nurbs", "surface_parametric")
    for c in model.curves:
        if c.kind.value not in (*basis, "trimmed") or c.sense.value == "unknown":
            raise _error(
                "curve",
                "Only lines, circles, ellipses, source-identified surface "
                "intersections, NURBS, surface-parametric curves and their "
                "explicit trims are qualified",
            )
        if c.kind.value == "trimmed" and (
            curves[c.definition.basis_curve].kind.value not in basis
        ):
            raise _error("curve", "Nested curve trims are not qualified")
    if any(
        s.kind.value
        not in (
            "plane",
            "cylinder",
            "sphere",
            "cone",
            "torus",
            "nurbs",
            "offset",
            "spun",
        )
        or s.sense.value == "unknown"
        for s in model.surfaces
    ):
        raise _error(
            "surface",
            "Only analytic, spun, NURBS and offset saved surfaces are supported",
        )
    if any(f.sense.value == "unknown" or not f.loops for f in model.faces):
        raise _error("face", "Saved faces require explicit oriented boundary loops")
    apex = {h for _, _, loop in _apex_loops(model) for h in loop.half_edges}
    if any(
        h.dummy or h.edge is None or h.sense.value == "unknown"
        for h in model.half_edges
        if h.id not in apex
    ):
        raise _error("half_edge", "Unqualified saved half-edge")


def _apex_loops(model: Any) -> list[tuple[Any, int, Any]]:
    """Vertex-only loops on conical and spun faces: (face, vertex id, loop).

    A saved cone that ends in its apex, or a spun profile that ends on its
    axis, bounds the face there with a loop of one half-edge that has a vertex
    and no edge; a spun face may close at both ends of its profile. Other
    edge-less half-edges, other surfaces and faces left without an edge loop
    are not qualified.
    """
    half_edges = {h.id: h for h in model.half_edges}
    loops = {loop.id: loop for loop in model.loops}
    surfaces = {s.id: s for s in model.surfaces}
    found: list[tuple[Any, int, Any]] = []
    for face in model.faces:
        vertex_loops = [
            loops[i]
            for i in face.loops
            if len(loops[i].half_edges) == 1
            and half_edges[loops[i].half_edges[0]].edge is None
        ]
        if not vertex_loops:
            continue
        kind = surfaces[face.surface].kind.value
        singular = [half_edges[loop.half_edges[0]] for loop in vertex_loops]
        if (
            kind not in ("cone", "spun")
            or len(vertex_loops) > (2 if kind == "spun" else 1)
            or len(face.loops) <= len(vertex_loops)
            or any(only.dummy or only.vertex is None for only in singular)
        ):
            return []
        found.extend(
            (face, only.vertex, loop)
            for only, loop in zip(singular, vertex_loops, strict=True)
        )
    return found


def _add_apex(builder: Any, face: Any, vertex_id: int, api: dict[str, Any]) -> None:
    """Close a conical or spun face at its singular vertex with one degenerated edge."""
    geom2d = import_module("OCP.Geom2d")
    top = api["TopoDS"]
    surface = builder._surface_geometry(face.surface)
    vertex = builder.vertex_shapes[vertex_id]
    point = api["BRep"].BRep_Tool.Pnt_s(vertex)
    tolerance = api["BRep"].BRep_Tool.Tolerance_s(vertex)
    if builder.surfaces[face.surface].kind.value == "cone":
        if surface.Apex().Distance(point) > tolerance:
            raise _error("half_edge", "Vertex loop is not at the apex of its cone")
        # At the apex the radius R + v*sin(a) vanishes for every angle u.
        v = -surface.RefRadius() / math.sin(surface.SemiAngle())
    else:
        # A spun profile end on the axis degenerates that ring of the revolution:
        # the vertex must be on the axis and at a finite end of the profile.
        basis = surface.BasisCurve()
        ends = [
            t
            for t in (basis.FirstParameter(), basis.LastParameter())
            if math.isfinite(t)
        ]
        if not ends or api["gp"].gp_Lin(surface.Axis()).Distance(point) > tolerance:
            raise _error(
                "half_edge", "Vertex loop is not on the axis of its spun surface"
            )
        v = min(ends, key=lambda t: basis.Value(t).Distance(point))
        if basis.Value(v).Distance(point) > tolerance:
            raise _error(
                "half_edge", "Vertex loop is not at a profile end of its spun surface"
            )
    kernel = api["BRep"].BRep_Builder()
    edge = top.TopoDS_Edge()
    kernel.MakeEdge(edge)
    kernel.Degenerated(edge, True)
    forward, reversed_ = api["TopAbs"].TopAbs_FORWARD, api["TopAbs"].TopAbs_REVERSED
    kernel.Add(edge, vertex.Oriented(forward))
    kernel.Add(edge, vertex.Oriented(reversed_))
    line = geom2d.Geom2d_Line(api["gp"].gp_Pnt2d(0.0, v), api["gp"].gp_Dir2d(1.0, 0.0))
    shape = builder.face_shapes[face.id]
    kernel.UpdateEdge(edge, line, shape, tolerance)
    kernel.Range(edge, 0.0, math.tau)
    wire = top.TopoDS_Wire()
    kernel.MakeWire(wire)
    kernel.Add(wire, edge)
    kernel.Add(shape, wire)
    builder.operations.append(
        "degenerated_cone_apex_boundary"
        if builder.surfaces[face.surface].kind.value == "cone"
        else "degenerated_spun_axis_boundary"
    )


def _build(
    model: Any, options: Any, api: dict[str, Any], limits: SavedBodyLimits
) -> Any:
    # These construction classes are deliberately pinned to parasolid-kit 0.3.6.
    # No dependency monkeypatch or source-data mutation is performed.
    factory = import_module("parasolid_kit.interop.occt.geometry").GeometryFactory(
        options
    )
    cls = import_module(
        "parasolid_kit.interop.occt.parametric"
    ).ParametricTopologyBuilder
    # The pinned builder has no vertex loops. Build the edge loops with it,
    # then close each qualified cone apex with a degenerated edge.
    apexes = _apex_loops(model)
    if apexes:
        dropped = {loop.id for _, _, loop in apexes}
        fins = {h for _, _, loop in apexes for h in loop.half_edges}
        model = replace(
            model,
            faces=tuple(
                replace(f, loops=tuple(i for i in f.loops if i not in dropped))
                for f in model.faces
            ),
            loops=tuple(loop for loop in model.loops if loop.id not in dropped),
            half_edges=tuple(h for h in model.half_edges if h.id not in fins),
        )
    builder = cls(model, factory)
    builder._build_vertices()
    for edge in model.edges:
        builder._build_edge(edge)
    for face in model.faces:
        builder._build_face(face)
    for face, vertex_id, _ in apexes:
        _add_apex(builder, face, vertex_id, api)
    # Generate cylindrical/other seams before spherical seams. The shared
    # replacement context must see the adjacent ring splits before it closes
    # a spherical band; source face enumeration is not a construction order.
    for face in sorted(
        model.faces, key=lambda f: builder.surfaces[f.surface].kind.value == "sphere"
    ):
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
        model = bridge(brep, saved_surfaces=True)
        _preflight(model, limits)
        # Source points are compared with their curves within the body's own
        # declared linear resolution (in mm), never below the backend default.
        # The preflight has bounded that resolution; nothing is healed.
        resolution = min(b.linear_resolution for b in model.bodies) * 1000
        occt = import_module("parasolid_kit.interop.occt.options")
        options = occt.OcctConversionOptions(
            source_unit="m",
            target_unit="mm",
            validation=occt.ValidationTolerances(linear_absolute=max(1e-6, resolution)),
        )
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
