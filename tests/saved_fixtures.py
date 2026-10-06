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


def _assemble(surfaces, curves, faces, loops, fins, edges, vertices=(), points=()):
    """Authored closed solid: one material region, one void region."""
    from parasolid_kit.binary.header import ByteRange
    from parasolid_kit.brep import model as m
    from parasolid_kit.brep import topology as t

    ids = tuple(f.id for f in faces)
    model = m.BrepModel(
        source_format="binary",
        schema_key="authored-test",
        complete=True,
        bodies=(
            t.Body(
                0,
                t.BodyKind.SOLID,
                1e-8,
                1e-8,
                (0, 1),
                tuple(e.id for e in edges),
                tuple(v.id for v in vertices),
                None,
            ),
        ),
        regions=(
            t.Region(0, t.RegionKind.SOLID, 0, (0,), None),
            t.Region(1, t.RegionKind.VOID, 0, (1,), None),
        ),
        shells=(
            t.Shell(0, 0, ids, (), (), None, None),
            t.Shell(1, 1, (), ids, (), None, None),
        ),
        faces=faces,
        loops=loops,
        half_edges=fins,
        edges=edges,
        vertices=vertices,
        points=points,
        curves=curves,
        surfaces=surfaces,
        topology=m.TopologyValidation(True, len(loops), len(edges), 0),
        metrics=m.BrepMetrics(None, None, None),
        diagnostics=(),
    )

    def sourced(row):
        source = t.SourceNodeRef(row.id + 1, 2, "authored", row.id, ByteRange(0, 1))
        return replace(row, source=source)

    names = (
        "bodies",
        "regions",
        "shells",
        "faces",
        "loops",
        "half_edges",
        "edges",
        "vertices",
        "points",
        "curves",
        "surfaces",
    )
    return replace(
        model, **{n: tuple(sourced(r) for r in getattr(model, n)) for n in names}
    )


def oblique_cylinder_model(radius=0.007, height=0.02, slope=0.5):
    """A cylinder cut by the plane z = height + slope * x: one elliptical edge.

    Volume is pi r^2 height; the cut face is an ellipse of semi-axes
    r sqrt(1 + slope^2) and r. Lengths are metres, as in a saved resource.
    """
    from parasolid_kit.brep import geometry as g
    from parasolid_kit.brep import topology as t

    def v(x, y, z):
        return t.Vector3(float(x), float(y), float(z))

    positive, negative = t.Sense.POSITIVE, t.Sense.NEGATIVE
    scale = math.sqrt(1 + slope * slope)
    normal = v(-slope / scale, 0, 1 / scale)
    major = v(1 / scale, 0, slope / scale)
    surfaces = (
        g.SurfaceGeometry(
            0,
            positive,
            None,
            g.SurfaceKind.PLANE,
            g.PlaneSurface(v(0, 0, 0), v(0, 0, 1), v(1, 0, 0)),
            None,
        ),
        g.SurfaceGeometry(
            1,
            positive,
            None,
            g.SurfaceKind.CYLINDER,
            g.CylinderSurface(v(0, 0, 0), v(0, 0, 1), radius, v(1, 0, 0)),
            None,
        ),
        g.SurfaceGeometry(
            2,
            positive,
            None,
            g.SurfaceKind.PLANE,
            g.PlaneSurface(v(0, 0, height), normal, major),
            None,
        ),
    )
    curves = (
        g.CurveGeometry(
            0,
            positive,
            None,
            g.CurveKind.CIRCLE,
            g.CircleCurve(v(0, 0, 0), v(0, 0, 1), v(1, 0, 0), radius),
            None,
        ),
        g.CurveGeometry(
            1,
            positive,
            None,
            g.CurveKind.ELLIPSE,
            g.EllipseCurve(v(0, 0, height), normal, major, radius * scale, radius),
            None,
        ),
    )
    # Bottom plane faces -z; the side and the cut face use their own normals.
    faces = (
        t.Face(0, 0, 1, (0,), 0, negative, None),
        t.Face(1, 0, 1, (1, 2), 1, positive, None),
        t.Face(2, 0, 1, (3,), 2, positive, None),
    )
    loops = (
        t.Loop(0, 0, (0,), None),
        t.Loop(1, 1, (1,), None),
        t.Loop(2, 1, (2,), None),
        t.Loop(3, 2, (3,), None),
    )
    fins = tuple(
        t.HalfEdge(
            i,
            i,
            i,
            i,
            None,
            i ^ 1,
            i // 2,
            None,
            negative if i in (0, 2) else positive,
            False,
            None,
        )
        for i in range(4)
    )
    edges = tuple(
        t.Edge(i, None, (2 * i, 2 * i + 1), None, None, i, None, None) for i in range(2)
    )
    return _assemble(surfaces, curves, faces, loops, fins, edges)


def apex_cone_model(radius=0.006, height=0.008, *, surface="cone"):
    """A cone from a base circle to its apex: the apex bounds the conical face
    with a loop of one vertex-only half-edge, as saved resources store it."""
    from parasolid_kit.brep import geometry as g
    from parasolid_kit.brep import topology as t

    def v(x, y, z):
        return t.Vector3(float(x), float(y), float(z))

    positive, negative = t.Sense.POSITIVE, t.Sense.NEGATIVE
    slant = math.hypot(radius, height)
    side = (
        g.SurfaceGeometry(
            1,
            positive,
            None,
            g.SurfaceKind.CONE,
            g.ConeSurface(
                v(0, 0, 0),
                v(0, 0, 1),
                radius,
                -radius / slant,
                height / slant,
                v(1, 0, 0),
            ),
            None,
        )
        if surface == "cone"
        else g.SurfaceGeometry(
            1,
            positive,
            None,
            g.SurfaceKind.SPHERE,
            g.SphereSurface(v(0, 0, 0), radius, v(0, 0, 1), v(1, 0, 0)),
            None,
        )
    )
    surfaces = (
        g.SurfaceGeometry(
            0,
            positive,
            None,
            g.SurfaceKind.PLANE,
            g.PlaneSurface(v(0, 0, 0), v(0, 0, 1), v(1, 0, 0)),
            None,
        ),
        side,
    )
    curves = (
        g.CurveGeometry(
            0,
            positive,
            None,
            g.CurveKind.CIRCLE,
            g.CircleCurve(v(0, 0, 0), v(0, 0, 1), v(1, 0, 0), radius),
            None,
        ),
    )
    faces = (
        t.Face(0, 0, 1, (0,), 0, negative, None),
        t.Face(1, 0, 1, (1, 2), 1, positive, None),
    )
    loops = (
        t.Loop(0, 0, (0,), None),
        t.Loop(1, 1, (1,), None),
        t.Loop(2, 1, (2,), None),
    )
    fins = (
        t.HalfEdge(0, 0, 0, 0, None, 1, 0, None, negative, False, None),
        t.HalfEdge(1, 1, 1, 1, None, 0, 0, None, positive, False, None),
        # The apex: a vertex, no edge, no curve and no orientation.
        t.HalfEdge(2, 2, 2, 2, 0, None, None, None, t.Sense.UNKNOWN, False, None),
    )
    edges = (t.Edge(0, None, (0, 1), None, None, 0, None, None),)
    vertices = (t.Vertex(0, 0, None, None, None),)
    points = (g.PointGeometry(0, v(0, 0, height), None, None),)
    return _assemble(surfaces, curves, faces, loops, fins, edges, vertices, points)


def spun_dome_model(radius=0.006, *, pole=None, planar=False):
    """A dome: the upper half of a spun sphere, closed at its pole by a loop of
    one vertex-only half-edge, as saved resources store spun faces. The profile
    half circle runs from the lower pole to the upper pole in the xz plane and
    is revolved about z."""
    from parasolid_kit.brep import geometry as g
    from parasolid_kit.brep import topology as t

    def v(x, y, z):
        return t.Vector3(float(x), float(y), float(z))

    positive, negative = t.Sense.POSITIVE, t.Sense.NEGATIVE
    side = (
        g.SurfaceGeometry(
            1,
            positive,
            None,
            g.SurfaceKind.PLANE,
            g.PlaneSurface(v(0, 0, radius), v(0, 0, 1), v(1, 0, 0)),
            None,
        )
        if planar
        else g.SurfaceGeometry(
            1,
            positive,
            None,
            g.SurfaceKind.SPUN,
            g.SpunSurface(
                1,
                v(0, 0, 0),
                v(0, 0, 1),
                v(0, 0, -radius),
                v(0, 0, radius),
                -math.pi / 2,
                math.pi / 2,
                v(1, 0, 0),
            ),
            None,
        )
    )
    surfaces = (
        g.SurfaceGeometry(
            0,
            positive,
            None,
            g.SurfaceKind.PLANE,
            g.PlaneSurface(v(0, 0, 0), v(0, 0, 1), v(1, 0, 0)),
            None,
        ),
        side,
    )
    curves = (
        g.CurveGeometry(
            0,
            positive,
            None,
            g.CurveKind.CIRCLE,
            g.CircleCurve(v(0, 0, 0), v(0, 0, 1), v(1, 0, 0), radius),
            None,
        ),
        # The profile: C(t) = (r cos t, 0, r sin t), so t = -pi/2 and pi/2 are
        # the poles on the spin axis.
        g.CurveGeometry(
            1,
            positive,
            None,
            g.CurveKind.CIRCLE,
            g.CircleCurve(v(0, 0, 0), v(0, -1, 0), v(1, 0, 0), radius),
            None,
        ),
    )
    faces = (
        t.Face(0, 0, 1, (0,), 0, negative, None),
        # The spun normal (profile direction x angle direction) points inward.
        t.Face(1, 0, 1, (1, 2), 1, negative, None),
    )
    loops = (
        t.Loop(0, 0, (0,), None),
        t.Loop(1, 1, (1,), None),
        t.Loop(2, 1, (2,), None),
    )
    fins = (
        t.HalfEdge(0, 0, 0, 0, None, 1, 0, None, negative, False, None),
        t.HalfEdge(1, 1, 1, 1, None, 0, 0, None, positive, False, None),
        # The pole: a vertex, no edge, no curve and no orientation.
        t.HalfEdge(2, 2, 2, 2, 0, None, None, None, t.Sense.UNKNOWN, False, None),
    )
    edges = (t.Edge(0, None, (0, 1), None, None, 0, None, None),)
    vertices = (t.Vertex(0, 0, None, None, None),)
    points = (
        g.PointGeometry(0, v(0, 0, radius) if pole is None else pole, None, None),
    )
    return _assemble(surfaces, curves, faces, loops, fins, edges, vertices, points)
