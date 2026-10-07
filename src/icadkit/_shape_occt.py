"""OCCT helpers for part shapes: placement, mass properties, compounds, export.

Everything here needs the optional ``preview`` extra. Functions take the
runtime module table of :func:`icadkit._csg_occt._runtime` and raise plain
exceptions; callers scope them to a body or a file.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from ._csg_occt import _runtime, _shapes
from .parts import Matrix4


def runtime() -> dict[str, Any]:
    return _runtime()


def place(shape: Any, matrix: Matrix4, api: dict[str, Any]) -> Any:
    """Apply a rigid 4-by-4 row matrix to a copy of the shape."""
    transform = api["gp"].gp_Trsf()
    transform.SetValues(*(v for row in matrix[:3] for v in row))
    return (
        api["BRepBuilderAPI"].BRepBuilderAPI_Transform(shape, transform, True).Shape()
    )


def mass_properties(
    shape: Any, api: dict[str, Any]
) -> tuple[float, float, tuple[float, float, float]]:
    """Exact kernel volume, surface area and centre of mass of the shape."""
    volume = api["GProp"].GProp_GProps()
    area = api["GProp"].GProp_GProps()
    api["BRepGProp"].BRepGProp.VolumeProperties_s(shape, volume)
    api["BRepGProp"].BRepGProp.SurfaceProperties_s(shape, area)
    centre = volume.CentreOfMass()
    return volume.Mass(), area.Mass(), (centre.X(), centre.Y(), centre.Z())


def solid_count(shape: Any, api: dict[str, Any]) -> int:
    return sum(1 for _ in _shapes(shape, api["TopAbs"].TopAbs_SOLID, api))


def compound(shapes: list[Any], api: dict[str, Any]) -> Any:
    builder = api["BRep"].BRep_Builder()
    result = api["TopoDS"].TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def write_step(shape: Any, path: Path, api: dict[str, Any]) -> None:
    """Write the shape as AP214 STEP in millimetres."""
    step = import_module("OCP.STEPControl")
    done = import_module("OCP.IFSelect").IFSelect_RetDone
    writer = step.STEPControl_Writer()
    import_module("OCP.Interface").Interface_Static.SetCVal_s("write.step.unit", "MM")
    if writer.Transfer(shape, step.STEPControl_AsIs) != done:
        raise RuntimeError("STEP transfer did not complete")
    if writer.Write(str(path)) != done:
        raise RuntimeError("STEP write did not complete")


def write_brep(shape: Any, path: Path, api: dict[str, Any]) -> None:
    """Write the shape in OCCT's native BREP text format."""
    if not import_module("OCP.BRepTools").BRepTools.Write_s(shape, str(path)):
        raise RuntimeError("BREP write did not complete")
