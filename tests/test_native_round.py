"""Independent cone/torus dimensions and closed outward display surfaces."""

import math
from collections import Counter

import pytest
from test_native import native, read

import icadkit
from icadkit.viewer import ViewerLimits, _mesh, write_native_viewer


@pytest.mark.parametrize(
    "kind,parameters,volume",
    [
        ("cone", (27, 0, 0, 0, 0, 9, 0), math.pi * 9**2 * 27 / 3),
        ("cone", (27, 0, 0, 0, 0, 9, 3), math.pi * 27 * (81 + 27 + 9) / 3),
        ("torus", (math.tau, 13, 2.5, 0), 2 * math.pi**2 * 13 * 2.5**2),
    ],
)
def test_round_primitives_dimensions_closed_mesh_volume_and_limits(
    kind, parameters, volume, tmp_path
):
    doc, ix = read(
        native(kind, parameters=parameters, origin=(0, 0, 0), axes=(0, 0, 1, 1, 0, 0))
    )
    p = ix.parts[1].entities[0].primitive
    assert p.kind == kind and p.box_dimensions is None
    if kind == "cone":
        assert (p.height, p.radius, p.top_radius) == (27, 9, parameters[-1])
    else:
        assert (p.height, p.radius, p.major_radius, p.minor_radius) == (
            None,
            None,
            13,
            2.5,
        )
    assert doc.source_bytes(p.byte_range) == p.raw_bytes
    mesh = _mesh(p, 48)
    xyz = list(zip(*[iter(mesh["positions"])] * 3, strict=True))
    tri = list(zip(*[iter(mesh["triangles"])] * 3, strict=True))
    edges = Counter()
    directed = Counter()
    signed = 0
    for a, b, c in tri:
        for i, j in [(a, b), (b, c), (c, a)]:
            assert i != j
            edges[tuple(sorted((i, j)))] += 1
            directed[i, j] += 1
        v, w, z = xyz[a], xyz[b], xyz[c]
        signed += (
            sum(
                v[i]
                * (w[(i + 1) % 3] * z[(i + 2) % 3] - w[(i + 2) % 3] * z[(i + 1) % 3])
                for i in range(3)
            )
            / 6
        )
    assert set(edges.values()) == {2}
    assert all(n == 1 and directed[j, i] == 1 for (i, j), n in directed.items())
    assert signed == pytest.approx(volume, rel=0.02)
    with pytest.raises(icadkit.LimitExceededError):
        write_native_viewer(
            doc,
            tmp_path / "limited",
            cylinder_segments=48,
            limits=ViewerLimits(max_triangles=len(tri) - 1),
        )
    assert not (tmp_path / "limited").exists()


@pytest.mark.parametrize(
    "kind,parameters,status",
    [
        ("cone", (-1, 0, 0, 0, 0, 9, 3), "invalid"),
        ("cone", (27, 0, 0, 0, 0, 9, -1), "invalid"),
        ("cone", (27, 1, 0, 0, 0, 9, 3), "unsupported"),
        ("cone", (27, 0, 0, 0, 0, math.nan, 3), "invalid"),
        ("torus", (math.pi, 13, 2.5, 0), "unsupported"),
        ("torus", (math.tau, 13, 2.5, 1), "unsupported"),
        ("torus", (math.tau, 2, 2.5, 0), "unsupported"),
        ("torus", (math.tau, 13, math.inf, 0), "invalid"),
    ],
)
def test_round_invalid_dimensions_and_unqualified_extents(kind, parameters, status):
    _, ix = read(native(kind, parameters=parameters))
    e = ix.parts[1].entities[0]
    assert e.primitive is None and e.geometry_status == status


@pytest.mark.parametrize("kind", ["cone", "torus"])
def test_round_mirrors_do_not_gain_geometry(kind):
    _, ix = read(native(kind, mirror=True))
    e = ix.parts[1].entities[0]
    assert e.primitive is None and e.appearance.status == "complete"
