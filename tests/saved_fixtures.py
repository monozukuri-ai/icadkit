"""Independently authored analytic topology; no CAD bytes or vendor schema."""

import math
from dataclasses import replace


def spherical_band_model(radius=0.011, bore=0.003, *, sphere_first=True):
    from parasolid_kit.brep import geometry as g
    from parasolid_kit.brep import model as m
    from parasolid_kit.brep import topology as t

    def v(x, y, z):
        return t.Vector3(float(x), float(y), float(z))

    positive, negative = t.Sense.POSITIVE, t.Sense.NEGATIVE
    z = math.sqrt(radius * radius - bore * bore)
    faces = (
        t.Face(0, 0, 1, (0, 1), 0, positive, None),
        t.Face(1, 0, 1, (2, 3), 1, negative, None),
    )
    # Each closed circle is used by both the spherical band and the bore.
    loops = tuple(t.Loop(i, i // 2, (i,), None) for i in range(4))
    fins = tuple(
        t.HalfEdge(
            i,
            i,
            i,
            i,
            None,
            (i + 2) % 4,
            i % 2,
            None,
            negative if i < 2 else positive,
            False,
            None,
        )
        for i in range(4)
    )
    curves = tuple(
        g.CurveGeometry(
            i,
            negative,
            None,
            g.CurveKind.CIRCLE,
            g.CircleCurve(v(0, 0, height), v(0, 0, normal), v(normal, 0, 0), bore),
            None,
        )
        for i, (height, normal) in enumerate(((z, -1), (-z, 1)))
    )
    model = m.BrepModel(
        source_format="binary",
        schema_key="authored-test",
        complete=True,
        bodies=(t.Body(0, t.BodyKind.SOLID, 1e-8, 1e-8, (0, 1), (0, 1), (), None),),
        regions=(
            t.Region(0, t.RegionKind.SOLID, 0, (0,), None),
            t.Region(1, t.RegionKind.VOID, 0, (1,), None),
        ),
        shells=(
            t.Shell(0, 0, (0, 1), (), (), None, None),
            t.Shell(1, 1, (), (0, 1), (), None, None),
        ),
        faces=faces if sphere_first else faces[::-1],
        loops=loops,
        half_edges=fins,
        edges=tuple(
            t.Edge(i, None, (i, i + 2), None, None, i, None, None) for i in range(2)
        ),
        vertices=(),
        points=(),
        curves=curves,
        surfaces=(
            g.SurfaceGeometry(
                0,
                positive,
                None,
                g.SurfaceKind.SPHERE,
                g.SphereSurface(v(0, 0, 0), radius, v(0, 0, 1), v(1, 0, 0)),
                None,
            ),
            g.SurfaceGeometry(
                1,
                positive,
                None,
                g.SurfaceKind.CYLINDER,
                g.CylinderSurface(v(0, 0, -radius), v(0, 0, 1), bore, v(1, 0, 0)),
                None,
            ),
        ),
        topology=m.TopologyValidation(True, 4, 2, 0),
        metrics=m.BrepMetrics(None, None, None),
        diagnostics=(),
    )

    from parasolid_kit.binary.header import ByteRange

    def sourced(row):
        source = t.SourceNodeRef(row.id + 1, 2, "authored", row.id, ByteRange(0, 1))
        return replace(row, source=source)

    return replace(
        model,
        **{
            name: tuple(sourced(row) for row in getattr(model, name))
            for name in (
                "bodies",
                "regions",
                "shells",
                "faces",
                "loops",
                "half_edges",
                "edges",
                "curves",
                "surfaces",
            )
        },
    )
