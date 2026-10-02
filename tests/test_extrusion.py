"""Authored concave prisms, signed heights and guarded native layouts."""

import math
import struct
from collections import Counter

import pytest
from part_fixtures import ROOT, A, part, view_parts
from test_mirrors import PROFILES, signed_volume
from test_native import native

import icadkit
from icadkit.viewer import (
    ViewerLimits,
    _mesh,
    _mesh_triangle_count,
    write_native_viewer,
)

# Area 24, perimeter 28; asymmetric profile offset from the frame origin.
POINTS = ((1, 2), (8, 2), (8, 4), (3, 4), (3, 9), (1, 9))


def extrusion(points=POINTS, height=13, *, mirror=False):
    b = bytearray(368)
    b[:120] = native(first=False, mirror=mirror)[:120]
    struct.pack_into("<I", b, 0, len(b))
    b[15] = 76
    struct.pack_into("<I", b, 24, 0x5A440158)
    struct.pack_into("<I", b, 40, 0x1B0)
    values = (
        [height] + [v for point in (*points, points[0]) * 2 for v in point] + [0, 0]
    )
    struct.pack_into("<31d", b, 120, *values)
    return b


def document(record, profile="v8l3"):
    return icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, profile=profile),
                part(A, parent=ROOT, profile=profile),
                struct.pack("<I", 0x30010000) + record,
            ],
            profile=profile,
        )
    )


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("height", [13, -13])
@pytest.mark.parametrize("reverse", [False, True])
def test_concave_polygon_mesh_matches_independent_prism(profile, height, reverse):
    points = POINTS[::-1] if reverse else POINTS
    doc = document(extrusion(points, height), profile)
    entity = doc.read_parts().parts[1].entities[0]
    p = entity.primitive
    assert entity.geometry_status == "complete" and entity.is_mirror is False
    assert p.kind == "polygon_extrusion" and p.profile_points == points
    assert p.height == 13 and p.mirror_convention is None
    assert struct.unpack_from("<d", p.raw_bytes, 72)[0] == height
    mesh = _mesh(p, 24)
    assert _mesh_triangle_count(p, 24) == len(mesh["triangles"]) // 3 == 20
    assert signed_volume(mesh) == pytest.approx(24 * 13)
    coords = list(zip(*[iter(mesh["positions"])] * 3, strict=True))
    assert set(coords) == {
        (7 + x, -11 + z, 19 - y) for x, y in points for z in (0, height)
    }
    edges = Counter()
    area = 0
    for at in range(0, len(mesh["triangles"]), 3):
        a, b, c = mesh["triangles"][at : at + 3]
        edges.update(((a, b), (b, c), (c, a)))
        u = [coords[b][i] - coords[a][i] for i in range(3)]
        v = [coords[c][i] - coords[a][i] for i in range(3)]
        area += (
            math.sqrt(
                sum(
                    (u[(i + 1) % 3] * v[(i + 2) % 3] - u[(i + 2) % 3] * v[(i + 1) % 3])
                    ** 2
                    for i in range(3)
                )
            )
            / 2
        )
    assert area == pytest.approx(2 * 24 + 28 * 13)
    assert all(count == 1 and edges[(b, a)] == 1 for (a, b), count in edges.items())


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("reverse", [False, True])
def test_mirrored_polygon_reflects_signed_height_once(profile, reverse):
    points = POINTS[::-1] if reverse else POINTS
    doc = document(extrusion(points, -13, mirror=True), profile)
    entity = doc.read_parts().parts[1].entities[0]
    p = entity.primitive
    assert entity.geometry_status == "complete" and entity.is_mirror is True
    assert p.kind == "polygon_extrusion" and p.profile_points == points
    assert p.height == 13 and p.mirror_convention == "signed_height"
    assert struct.unpack_from("<d", p.raw_bytes, 72)[0] == -13
    mirrored = _mesh(p, 24)
    original = _mesh(
        document(extrusion(points, 13), profile)
        .read_parts()
        .parts[1]
        .entities[0]
        .primitive,
        24,
    )

    def coords(mesh):
        return set(zip(*[iter(mesh["positions"])] * 3, strict=True))

    # The authored native Z axis is global +Y. Reflect about Y=-11.
    assert coords(mirrored) == {(x, -22 - y, z) for x, y, z in coords(original)}
    assert signed_volume(mirrored) == pytest.approx(24 * 13)
    edges = Counter()
    for at in range(0, len(mirrored["triangles"]), 3):
        a, b, c = mirrored["triangles"][at : at + 3]
        edges.update(((a, b), (b, c), (c, a)))
    assert all(n == 1 and edges[(b, a)] == 1 for (a, b), n in edges.items())


def test_positive_mirrored_polygon_height_remains_unqualified():
    entity = (
        document(extrusion(height=13, mirror=True)).read_parts().parts[1].entities[0]
    )
    assert entity.primitive is None and entity.geometry_status == "invalid"


@pytest.mark.parametrize("defect", ["open", "taper", "trailer", "curve", "extent"])
def test_unknown_extrusion_layout_stays_opaque(defect):
    raw = extrusion()
    offsets = {"open": 224, "taper": 256, "trailer": 360, "curve": 24, "extent": 40}
    raw[offsets[defect]] ^= 1
    entity = document(raw).read_parts().parts[1].entities[0]
    assert entity.primitive is None and entity.geometry_status == "unsupported"


@pytest.mark.parametrize(
    "points",
    [
        (POINTS[0], POINTS[2], POINTS[1], *POINTS[3:]),
        (*POINTS[:3], POINTS[2], *POINTS[4:]),
        ((math.inf, 2), *POINTS[1:]),
        tuple((x * 1e200, y * 1e200) for x, y in POINTS),
    ],
)
def test_invalid_polygons_never_produce_mesh(points):
    entity = document(extrusion(points)).read_parts().parts[1].entities[0]
    assert entity.primitive is None and entity.geometry_status == "invalid"


def test_polygon_viewer_limits_and_metadata(tmp_path):
    doc = document(extrusion())
    with pytest.raises(icadkit.LimitExceededError):
        write_native_viewer(
            doc, tmp_path / "small", limits=ViewerLimits(max_triangles=19)
        )
    result = write_native_viewer(doc, tmp_path / "viewer")
    assert result.triangle_count == 20
