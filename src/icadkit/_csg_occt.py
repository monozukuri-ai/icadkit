"""Optional OCCT evaluation of already qualified native CSG programs."""

from __future__ import annotations

import math
from collections import Counter
from importlib import import_module, metadata
from typing import Any

from .csg import CsgBody, CsgLimits, CsgMesh, _fail
from .errors import IcadError
from .native import NativePrimitive
from .parts import _frame


def _runtime() -> dict[str, Any]:
    try:
        if metadata.version("parasolid-kit") != "0.2.0":
            _fail("csg.backend_version", "CSG evaluation requires parasolid-kit 0.2.0")
        import_module("parasolid_kit.interop.occt").load_runtime()
    except IcadError:
        raise
    except Exception as exc:
        _fail(
            "csg.missing_dependency",
            f'Install "icadkit[preview]" for CSG evaluation: {exc}',
        )
    return {
        name: import_module("OCP." + name)
        for name in (
            "gp",
            "BRepPrimAPI",
            "BRepAlgoAPI",
            "BRepCheck",
            "BRepGProp",
            "GProp",
            "TopAbs",
            "TopExp",
            "TopoDS",
            "BRep",
            "BRepMesh",
            "TopLoc",
        )
    }


def _shapes(shape: Any, kind: Any, api: dict[str, Any]) -> Any:
    iterator = api["TopExp"].TopExp_Explorer(shape, kind)
    while iterator.More():
        yield iterator.Current()
        iterator.Next()


def _check(shape: Any, limits: CsgLimits, api: dict[str, Any]) -> int:
    if shape.IsNull() or not api["BRepCheck"].BRepCheck_Analyzer(shape).IsValid():
        _fail(
            "csg.invalid_shape",
            "CSG kernel result is invalid; no repair applied",
            category="invalid",
        )
    count = 0
    for kind in (
        api["TopAbs"].TopAbs_FACE,
        api["TopAbs"].TopAbs_EDGE,
        api["TopAbs"].TopAbs_VERTEX,
    ):
        for _ in _shapes(shape, kind, api):
            count += 1
            if count > limits.max_subshapes:
                _fail(
                    "csg.limit_subshapes",
                    "CSG result exceeds max_subshapes",
                    category="limit_exceeded",
                )
    solids = sum(1 for _ in _shapes(shape, api["TopAbs"].TopAbs_SOLID, api))
    if not solids:
        _fail(
            "csg.empty_result",
            "Empty or lower-dimensional CSG results are not qualified",
        )
    for shell in _shapes(shape, api["TopAbs"].TopAbs_SHELL, api):
        if not api["BRep"].BRep_Tool.IsClosed_s(shell):
            _fail(
                "csg.open_result",
                "CSG result contains an open shell",
                category="invalid",
            )
    return solids


def _primitive(p: NativePrimitive, api: dict[str, Any]) -> Any:
    m = p.world_transform
    if (
        len(m) != 4
        or any(len(row) != 4 for row in m)
        or not all(math.isfinite(v) for row in m for v in row)
    ):
        _fail("csg.operand_frame", "Invalid operand matrix", category="invalid")
    rigid = _frame([m[i][j] for j in (3, 2, 0) for i in range(3)])
    if rigid is None or any(
        abs(m[i][j] - rigid[i][j]) > 1e-8 for i in range(4) for j in range(4)
    ):
        _fail("csg.operand_frame", "Non-rigid operand matrix", category="invalid")
    if not math.isfinite(p.height) or p.height <= 0:
        _fail("csg.operand_dimensions", "Invalid operand height", category="invalid")
    if p.kind == "box":
        if p.x_bounds is None or p.y_bounds is None:
            _fail("csg.operand_dimensions", "Missing box bounds", category="invalid")
        local = (p.x_bounds[0], p.y_bounds[0], 0.0)
    else:
        local = (0.0, 0.0, 0.0)
    origin = tuple(
        sum(m[i][j] * local[j] for j in range(3)) + m[i][3] for i in range(3)
    )
    if not all(math.isfinite(v) for v in origin):
        _fail("csg.operand_frame", "Operand position overflowed", category="invalid")
    gp = api["gp"]
    axes = gp.gp_Ax2(
        gp.gp_Pnt(*origin),
        gp.gp_Dir(*(m[i][2] for i in range(3))),
        gp.gp_Dir(*(m[i][0] for i in range(3))),
    )
    if p.kind == "box":
        dimensions = p.box_dimensions
        if dimensions is None or not all(
            math.isfinite(v) and v > 0 for v in dimensions
        ):
            _fail(
                "csg.operand_dimensions", "Invalid box dimensions", category="invalid"
            )
        return api["BRepPrimAPI"].BRepPrimAPI_MakeBox(axes, *dimensions).Shape()
    if (
        p.kind != "cylinder"
        or p.radius is None
        or not math.isfinite(p.radius)
        or p.radius <= 0
    ):
        _fail("csg.operand_dimensions", "Invalid cylinder radius", category="invalid")
    return api["BRepPrimAPI"].BRepPrimAPI_MakeCylinder(axes, p.radius, p.height).Shape()


def evaluate(body: CsgBody, limits: CsgLimits, deflection: float) -> CsgMesh:
    api = _runtime()
    try:
        operands = {o.source_id: o.primitive for o in body.operands}
        refs = {t for t in body.tokens if t & 0xF0000000 == 0x80000000}
        if set(operands) != refs or len(operands) != len(body.operands):
            _fail(
                "csg.references",
                "Operand set differs from the CSG program",
                category="invalid",
            )
        stack: list[Any] = []
        constructors = {
            1: api["BRepAlgoAPI"].BRepAlgoAPI_Fuse,
            2: api["BRepAlgoAPI"].BRepAlgoAPI_Cut,
            3: api["BRepAlgoAPI"].BRepAlgoAPI_Common,
        }
        for token in body.tokens:
            if token in operands:
                shape = _primitive(operands[token], api)
                _check(shape, limits, api)
                stack.append(shape)
            elif token in constructors:
                right, left = stack.pop(), stack.pop()
                operation = constructors[token](left, right)
                operation.SetRunParallel(False)
                operation.Build()
                if not operation.IsDone():
                    _fail(
                        "csg.boolean_failed",
                        "CSG boolean did not complete; no partial result",
                        category="invalid",
                    )
                result = operation.Shape()
                _check(result, limits, api)
                stack.append(result)
            # Validated 0/255 saved checkpoints leave the accumulated result unchanged.
        shape = stack[0]
        solids = _check(shape, limits, api)
        volume = api["GProp"].GProp_GProps()
        area = api["GProp"].GProp_GProps()
        api["BRepGProp"].BRepGProp.VolumeProperties_s(shape, volume)
        api["BRepGProp"].BRepGProp.SurfaceProperties_s(shape, area)
        center = volume.CentreOfMass()
        centroid = (center.X(), center.Y(), center.Z())
        if (
            not all(math.isfinite(v) for v in (*centroid, volume.Mass(), area.Mass()))
            or volume.Mass() <= 0
            or area.Mass() <= 0
        ):
            _fail(
                "csg.mass_properties", "Invalid CSG mass properties", category="invalid"
            )
        mesher = api["BRepMesh"].BRepMesh_IncrementalMesh(
            shape, deflection, False, 0.3, False
        )
        if not mesher.IsDone():
            _fail(
                "csg.tessellation",
                "CSG tessellation did not complete",
                category="invalid",
            )
        positions: list[float] = []
        triangles: list[int] = []
        edges: list[int] = []
        for item in _shapes(shape, api["TopAbs"].TopAbs_FACE, api):
            face = api["TopoDS"].TopoDS.Face_s(item)
            location = api["TopLoc"].TopLoc_Location()
            mesh = api["BRep"].BRep_Tool.Triangulation_s(face, location)
            if mesh is None or mesh.NbTriangles() <= 0 or mesh.NbNodes() <= 0:
                _fail(
                    "csg.tessellation",
                    "CSG face has no triangles; no partial mesh",
                    category="invalid",
                )
            offset = len(positions) // 3
            if (
                offset + mesh.NbNodes() > limits.max_vertices
                or len(triangles) // 3 + mesh.NbTriangles() > limits.max_triangles
            ):
                _fail(
                    "csg.limit_mesh",
                    "CSG mesh exceeds vertex/triangle limits",
                    category="limit_exceeded",
                )
            transform = location.Transformation()
            for i in range(1, mesh.NbNodes() + 1):
                point = mesh.Node(i).Transformed(transform)
                positions.extend((point.X(), point.Y(), point.Z()))
            reverse = face.Orientation() == api["TopAbs"].TopAbs_REVERSED
            boundary: Counter[tuple[int, int]] = Counter()
            for i in range(1, mesh.NbTriangles() + 1):
                a, b, c = (int(v) - 1 + offset for v in mesh.Triangle(i).Get())
                if reverse:
                    b, c = c, b
                triangles.extend((a, b, c))
                for first, second in ((a, b), (b, c), (c, a)):
                    boundary[min(first, second), max(first, second)] += 1
            for (a, b), count in boundary.items():
                if count == 1:
                    edges.extend((a, b))
        if not triangles or not all(math.isfinite(v) for v in positions):
            _fail("csg.tessellation", "Invalid or empty CSG mesh", category="invalid")
        return CsgMesh(
            body.body_id,
            tuple(positions),
            tuple(triangles),
            tuple(edges),
            volume.Mass(),
            area.Mass(),
            centroid,
            solids,
            deflection,
        )
    except IcadError:
        raise
    except Exception as exc:
        _fail(
            "csg.kernel",
            f"CSG kernel rejected this program: {exc}",
            body.byte_range.start,
            "invalid",
        )
