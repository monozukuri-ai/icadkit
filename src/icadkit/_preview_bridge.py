"""Explicit mapping to the pinned optional backend's public B-Rep types.

No stream is reparsed by the optional backend and no schema key is substituted.
Only the geometry variants qualified for this adapter are mapped.
"""

from __future__ import annotations

from dataclasses import asdict
from importlib import import_module
from typing import Any, cast

from .geometry import Brep, BrepCollection, BrepEntity, NodeSource
from .models import Diagnostic


def bridge(brep: Brep, *, saved_surfaces: bool = False) -> Any:
    from .preview import PreviewError

    top = import_module("parasolid_kit.brep.topology")
    geom = import_module("parasolid_kit.brep.geometry")
    model = import_module("parasolid_kit.brep.model")
    ranges = import_module("parasolid_kit.binary.header")

    def source(value: Any) -> Any:
        if value is None:
            return None
        data = asdict(value) if isinstance(value, NodeSource) else dict(value)
        raw = data.pop("decoded_range")
        start, end = (raw["start"], raw["end"]) if isinstance(raw, dict) else raw
        return top.SourceNodeRef(**data, byte_range=ranges.ByteRange(start, end))

    def vector(value: Any) -> Any:
        return top.Vector3(*value)

    topology = {
        "bodies": top.Body,
        "regions": top.Region,
        "shells": top.Shell,
        "faces": top.Face,
        "loops": top.Loop,
        "half_edges": top.HalfEdge,
        "edges": top.Edge,
        "vertices": top.Vertex,
    }
    curves = {
        "line": geom.LineCurve,
        "circle": geom.CircleCurve,
        "trimmed": geom.TrimmedCurve,
    }
    surfaces = {
        "plane": geom.PlaneSurface,
        "cylinder": geom.CylinderSurface,
        "sphere": geom.SphereSurface,
    }
    if saved_surfaces:
        surfaces.update(
            cone=geom.ConeSurface,
            torus=geom.TorusSurface,
            nurbs=geom.NurbsSurface,
            offset=geom.OffsetSurface,
            spun=geom.SpunSurface,
            blended_edge=geom.BlendedEdgeSurface,
            blend_boundary=geom.BlendBoundarySurface,
        )
        curves.update(
            ellipse=geom.EllipseCurve,
            intersection=geom.IntersectionCurve,
            nurbs=geom.NurbsCurve,
            surface_parametric=geom.SurfaceParametricCurve,
        )

    def entity(collection: str, value: BrepEntity) -> Any:
        attrs: dict[str, Any] = dict(value.attributes)
        common = {"id": value.id, "source": source(value.source)}
        if "owner" in attrs:
            attrs["owner"] = source(attrs["owner"])
        if "sense" in attrs:
            attrs["sense"] = top.Sense(attrs["sense"])
        if collection in topology:
            if collection == "bodies":
                attrs["kind"] = top.BodyKind(attrs["kind"])
            elif collection == "regions":
                attrs["kind"] = top.RegionKind(attrs["kind"])
            elif collection == "half_edges":
                attrs["loop"] = attrs.pop("loop_id")
            return topology[collection](**common, **attrs)
        if collection == "points":
            attrs["position"] = vector(attrs["position"])
            return geom.PointGeometry(**common, **attrs)
        kind = str(attrs.pop("kind"))
        constructors = curves if collection == "curves" else surfaces
        if kind not in constructors:
            raise PreviewError(
                Diagnostic(
                    "unsupported",
                    "preview.geometry_kind",
                    None,
                    f"Preview does not support {collection} kind {kind!r} "
                    f"(ID {value.id})",
                )
            )
        common.update(owner=attrs.pop("owner"), sense=attrs.pop("sense"))
        for name in (
            "point",
            "center",
            "direction",
            "normal",
            "axis",
            "x_axis",
            "start_point",
            "end_point",
        ):
            if name in attrs:
                attrs[name] = vector(attrs[name])
        if kind == "intersection":
            # Source-identified branch: support surfaces, chart and limit points.
            attrs["surfaces"] = tuple(attrs["surfaces"])
            for name in ("chart", "start", "end", "intersection_data"):
                attrs[name] = source(attrs[name])
            for name in ("chart_points", "start_points", "end_points"):
                attrs[name] = tuple(vector(p) for p in attrs[name])
        if kind == "blended_edge":
            # Rolling-ball or cliff-edge blend: supports, spine, offsets and
            # the optional degenerate limits.
            attrs["blend_type"] = geom.BlendType(attrs["blend_type"])
            for name in (
                "supporting_surfaces",
                "ranges",
                "thumb_weights",
                "boundary_surfaces",
            ):
                attrs[name] = tuple(attrs[name])
            for name in ("start", "end"):
                attrs[name] = source(attrs[name])
        if kind == "spun":
            # Spin axis point and direction, with optional degeneracy points.
            for name in ("base", "start", "end"):
                if attrs.get(name) is not None:
                    attrs[name] = vector(attrs[name])
        if kind == "nurbs":
            # Control vertices, knots and multiplicities are passed as stored.
            attrs["sources"] = tuple(source(item) for item in attrs["sources"])
            for name in attrs:
                if isinstance(attrs[name], tuple):
                    attrs[name] = tuple(
                        tuple(row) if isinstance(row, tuple) else row
                        for row in attrs[name]
                    )
        definition = constructors[kind](**attrs)
        if collection == "curves":
            return geom.CurveGeometry(
                **common, kind=geom.CurveKind(kind), definition=definition
            )
        return geom.SurfaceGeometry(
            **common, kind=geom.SurfaceKind(kind), definition=definition
        )

    collections = {}
    for name, count in brep.counts.items():
        collections[name] = tuple(
            entity(name, row)
            for start in range(0, count, 1000)
            for row in brep.entities(cast(BrepCollection, name), start, 1000)
        )
    bounds = brep.vertex_bounds
    return model.BrepModel(
        source_format=brep.source_format,
        schema_key=brep.schema_key,
        complete=brep.complete,
        **collections,
        topology=model.TopologyValidation(
            brep.topology_valid,
            brep.closed_loop_count,
            brep.closed_edge_ring_count,
            brep.euler_characteristic,
        ),
        metrics=model.BrepMetrics(
            None
            if bounds is None
            else top.BoundingBox(vector(bounds[0]), vector(bounds[1])),
            brep.surface_area,
            brep.volume,
        ),
        diagnostics=(),
    )
