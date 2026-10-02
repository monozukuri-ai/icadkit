"""Authored line/arc extrusions and revolved profiles with independent geometry."""

import json
import math
import struct
from collections import Counter

import pytest
from part_fixtures import ROOT, A, part, view_parts
from test_mirrors import PROFILES, signed_volume
from test_native import native

import icadkit
from icadkit.parts import _determinant
from icadkit.viewer import _mesh, _mesh_triangle_count, write_native_viewer

PI = math.pi
# A slot of radius 3 around centres (10, 5) and (10, 15), offset from the
# frame origin. Area 60 + 9*pi, perimeter 20 + 6*pi. The last arc closes it.
SLOT = [(13, 5), (13, 15), (10, 15), (PI, PI), (7, 5), (10, 5), (PI, 0)]
SLOT_ARCS = (2, 3, 5, 6)
SLOT_AREA, SLOT_PERIMETER = 60 + 9 * PI, 20 + 6 * PI


def extrusion(elements, arcs=(), height=13, *, mirror=False):
    """Authored record: frame, height, XY elements and a flag trailer."""
    b = bytearray(136 + 16 * len(elements))
    b[:120] = native(first=False, mirror=mirror)[:120]
    struct.pack_into("<I", b, 0, len(b))
    b[15] = 76
    struct.pack_into("<I", b, 24, 0x59440000 | (len(b) - 24))
    flat = [v for element in elements for v in element]
    struct.pack_into(f"<{1 + len(flat)}d", b, 120, height, *flat)
    for i in arcs:
        b[len(b) - 8 + i // 8] |= 0x80 >> (i % 8)
    return b


def revolution(points, *, mirror=False):
    """Authored record: frame, then (radius, axial) profile points."""
    b = bytearray(120 + 16 * len(points))
    b[:120] = native(first=False, mirror=mirror)[:120]
    struct.pack_into("<I", b, 0, len(b))
    b[15] = 67
    struct.pack_into("<I", b, 24, 0x52440000 | (len(b) - 24))
    flat = [v for point in points for v in point]
    struct.pack_into(f"<{len(flat)}d", b, 120, *flat)
    return b


def entity(record, profile="v8l3"):
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, profile=profile),
                part(A, parent=ROOT, profile=profile),
                struct.pack("<I", 0x30010000) + bytes(record),
            ],
            profile=profile,
        )
    )
    return doc, doc.read_parts().parts[1].entities[0]


def closed_and_outward(mesh):
    edges = Counter()
    for at in range(0, len(mesh["triangles"]), 3):
        a, b, c = mesh["triangles"][at : at + 3]
        edges.update(((a, b), (b, c), (c, a)))
    return all(n == 1 and edges[(b, a)] == 1 for (a, b), n in edges.items())


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("height", [13, -13])
@pytest.mark.parametrize("mirror", [False, True])
def test_slot_profile_arcs_signed_height_and_stored_reflection(profile, height, mirror):
    doc, e = entity(extrusion(SLOT, SLOT_ARCS, height, mirror=mirror), profile)
    p = e.primitive
    assert e.geometry_status == "complete" and e.is_mirror is mirror
    assert p.kind == "profile_extrusion" and p.height == 13
    assert p.profile_points is None and p.revolution_profile is None
    # The saved profile is never reflected again; only a negative height
    # reverses the exposed Z column, with or without the mirror flag.
    assert p.mirror_convention == (
        None if not mirror else "signed_height" if height < 0 else "stored_profile"
    )
    assert _determinant(p.world_transform) == (1 if height > 0 else -1)
    assert struct.unpack_from("<d", p.raw_bytes, 72)[0] == height
    assert [s.kind for s in p.profile] == ["line", "arc", "line", "arc"]
    line, arc, back, close = p.profile
    assert (line.start, line.end, line.center) == ((13, 5), (13, 15), None)
    assert arc.center == (10, 15) and arc.sweep_angle == PI
    assert arc.start == (13, 15) and arc.end == pytest.approx((7, 15))
    assert back.start == pytest.approx((7, 15)) and back.end == (7, 5)
    assert close.center == (10, 5) and close.end == pytest.approx((13, 5))
    mesh = _mesh(p, 256)
    assert _mesh_triangle_count(p, 256) == len(mesh["triangles"]) // 3
    assert closed_and_outward(mesh)
    # Arcs are chords of a 256-gon: half turns use 128 chords.
    chord_area = 60 + 128 * 9 * math.sin(PI / 128)
    assert signed_volume(mesh) == pytest.approx(chord_area * 13)
    assert chord_area == pytest.approx(SLOT_AREA, rel=2e-4)
    ys = mesh["positions"][1::3]
    assert (min(ys), max(ys)) == ((-11, 2) if height > 0 else (-24, -11))
    xs, zs = mesh["positions"][0::3], mesh["positions"][2::3]
    assert (min(xs), max(xs)) == pytest.approx((14, 20))
    assert (min(zs), max(zs)) == pytest.approx((1, 17))


def test_clockwise_rounded_square_closed_by_an_explicit_vertex():
    # A 10 x 10 square with corner radius 2, traversed clockwise from (2, 0).
    # Each arc starts at the preceding vertex: its centre, then its sweep.
    quarter = -PI / 2
    elements = [
        (2, 0), (2, 2), (quarter, 1e250),
        (0, 8), (2, 8), (quarter, math.nan),
        (8, 10), (8, 8), (quarter, 0),
        (10, 2), (8, 2), (quarter, quarter),
        (2, 0),
    ]  # fmt: skip
    _, e = entity(extrusion(elements, (1, 2, 4, 5, 7, 8, 10, 11), 4))
    p = e.primitive
    assert e.geometry_status == "complete"
    assert [s.kind for s in p.profile] == ["arc", "line"] * 4
    # Only the first value of a sweep element is used.
    assert all(s.sweep_angle == quarter for s in p.profile[::2])
    assert p.profile[0].end == pytest.approx((0, 2))
    assert p.profile[-1].start == pytest.approx((8, 0))
    assert p.profile[-1].end == (2, 0)
    mesh = _mesh(p, 256)
    assert closed_and_outward(mesh)
    assert signed_volume(mesh) == pytest.approx((84 + 4 * PI) * 4, rel=1e-4)
    assert _mesh_triangle_count(p, 256) == len(mesh["triangles"]) // 3


@pytest.mark.parametrize("reverse", [False, True])
def test_line_only_profile_keeps_collinear_and_repeated_vertices(reverse):
    points = [(0, 0), (6, 0), (12, 0), (12, 5), (12, 5), (4, 5), (4, 9), (0, 9), (0, 0)]
    if reverse:
        points = points[::-1]
    _, e = entity(extrusion(points, (), -7))
    p = e.primitive
    assert e.geometry_status == "complete" and p.height == 7
    assert all(s.kind == "line" and s.center is None for s in p.profile)
    # The zero-length repeat is dropped; the collinear vertex is kept.
    assert len(p.profile) == 7
    mesh = _mesh(p, 16)
    assert signed_volume(mesh) == pytest.approx((12 * 5 + 4 * 4) * 7)
    assert _mesh_triangle_count(p, 16) == len(mesh["triangles"]) // 3 == 24


def test_full_turn_profile_is_a_disc():
    _, e = entity(extrusion([(9, 4), (5, 4), (math.tau, math.tau)], (1, 2), 2))
    p = e.primitive
    assert e.geometry_status == "complete" and len(p.profile) == 1
    assert p.profile[0].end == pytest.approx((9, 4))
    mesh = _mesh(p, 64)
    assert signed_volume(mesh) == pytest.approx(0.5 * 64 * 16 * math.sin(PI / 32) * 2)
    assert closed_and_outward(mesh)


@pytest.mark.parametrize(
    "elements,arcs,height,code,status",
    [
        (SLOT, (0, 1, 2, 3), 13, "native.profile_layout", "unsupported"),
        (SLOT, (2, 5, 6), 13, "native.profile_layout", "unsupported"),
        (SLOT, (2, 3, 5, 6, 9), 13, "native.profile_layout", "unsupported"),
        (SLOT[:6] + [(0, 0)], (2, 3, 6), 13, "native.profile_layout", "unsupported"),
        (SLOT, SLOT_ARCS, 0, "native.profile", "invalid"),
        (SLOT[:3] + [(0, 0)] + SLOT[4:], SLOT_ARCS, 13, "native.profile", "invalid"),
        (SLOT[:2] + [(13, 15)] + SLOT[3:], SLOT_ARCS, 13, "native.profile", "invalid"),
        (SLOT[:3] + [(7, 7)] + SLOT[4:], SLOT_ARCS, 13, "native.profile", "invalid"),
        (SLOT[:6] + [(3, 3)], SLOT_ARCS, 13, "native.profile", "invalid"),
        ([(0, 0), (4, 4), (4, 0), (0, 4), (0, 0)], (), 13, "native.profile", "invalid"),
        ([(0, 0), (4, 0), (2, 0), (0, 0)], (), 13, "native.profile", "invalid"),
        ([(0, 0), (4, 0), (4, 3)], (), 13, "native.profile", "invalid"),
        (SLOT[:4] + [(math.nan, 5)] + SLOT[5:], SLOT_ARCS, 13, "native.parameters",
         "invalid"),
    ],
)  # fmt: skip
def test_unqualified_or_invalid_profiles_never_produce_a_primitive(
    elements, arcs, height, code, status
):
    doc, e = entity(extrusion(elements, arcs, height))
    assert e.primitive is None and e.geometry_status == status
    assert e.diagnostics[0].code == code
    assert e.appearance.status == "complete" and e.raw_type == 76
    assert len(doc.source_bytes(e.byte_range)) == 136 + 16 * len(elements)


@pytest.mark.parametrize(
    "offset,value", [(24, 0x59440001), (24, 0x5A4400C8), (40, 0x1B0), (44, 1)]
)
def test_unknown_extrusion_header_keeps_the_range(offset, value):
    record = extrusion(SLOT, SLOT_ARCS)
    struct.pack_into("<I", record, offset, value)
    _, e = entity(record)
    assert e.primitive is None and e.geometry_status == "unsupported"
    assert e.diagnostics[0].code == "native.layout"
    # Fewer than three profile elements are not a qualified layout either.
    _, e = entity(extrusion(SLOT[:2]))
    assert e.diagnostics[0].code == "native.layout"


# Radius/axial steps: a chamfer, a shoulder and a thinner end.
SHAFT = [(5, 0), (8, 3), (8, 20), (4, 20), (4, 32)]
SHAFT_VOLUME = PI * (3 * (25 + 40 + 64) / 3 + 64 * 17 + 16 * 12)


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_revolved_profile_is_closed_along_the_axis(profile, mirror, reverse):
    points = SHAFT[::-1] if reverse else SHAFT
    doc, e = entity(revolution(points, mirror=mirror), profile)
    p = e.primitive
    assert e.geometry_status == "complete" and p.kind == "revolution"
    assert p.revolution_profile == tuple(points)
    assert p.height is None and p.radius is None and p.profile is None
    assert p.mirror_convention == ("symmetric_frame" if mirror else None)
    assert _determinant(p.world_transform) == 1
    assert p.raw_bytes == doc.source_bytes(p.byte_range)
    mesh = _mesh(p, 128)
    assert _mesh_triangle_count(p, 128) == len(mesh["triangles"]) // 3
    assert closed_and_outward(mesh)
    # A 128-gon cross-section scales every disc area by the same factor.
    factor = 128 * math.sin(math.tau / 128) / math.tau
    assert signed_volume(mesh) == pytest.approx(SHAFT_VOLUME * factor)
    # The frame Z axis is global +Y: the shaft spans Y = -11..21.
    ys = mesh["positions"][1::3]
    assert (min(ys), max(ys)) == (-11, 21)
    xs = mesh["positions"][0::3]
    assert (min(xs), max(xs)) == pytest.approx((-1, 15))


def test_revolution_with_apex_and_undercut():
    # A cone tip on the axis, then a groove that returns toward the axis.
    points = [(0, -4), (6, 2), (6, 5), (3, 5), (3, 7), (6, 7), (6, 10)]
    _, e = entity(revolution(points))
    p = e.primitive
    assert e.geometry_status == "complete"
    volume = PI * (36 * 6 / 3 + 36 * 3 + 9 * 2 + 36 * 3)
    mesh = _mesh(p, 64)
    assert closed_and_outward(mesh)
    assert signed_volume(mesh) == pytest.approx(
        volume * 64 * math.sin(math.tau / 64) / math.tau
    )
    assert _mesh_triangle_count(p, 64) == len(mesh["triangles"]) // 3


@pytest.mark.parametrize(
    "points,code",
    [
        ([(5, 0), (-1, 3), (4, 6)], "native.revolution"),
        ([(5, 0), (5, 0), (4, 6)], "native.revolution"),
        ([(0, 0), (0, 5)], "native.revolution"),
        ([(5, 0), (5, 8), (2, 4), (9, 4)], "native.revolution"),
        ([(5, 0), (math.inf, 3)], "native.parameters"),
    ],
)
def test_invalid_revolution_profiles(points, code):
    _, e = entity(revolution(points))
    assert e.primitive is None and e.geometry_status == "invalid"
    assert e.diagnostics[0].code == code
    _, e = entity(revolution(SHAFT[:1]))
    assert e.primitive is None and e.diagnostics[0].code == "native.layout"


@pytest.mark.parametrize("profile", ["v7l6", "v7l7", "v8l1", "v8l2"])
def test_standalone_owner_can_mix_new_and_existing_primitives(profile, tmp_path):
    records = [
        part(ROOT, root=True, profile=profile),
        native(),
        extrusion(SLOT, SLOT_ARCS),
        revolution(SHAFT),
    ]
    doc = icadkit.read(view_parts(records, profile=profile))
    index = doc.read_parts()
    assert [e.primitive.kind for e in index.parts[0].entities] == [
        "box",
        "profile_extrusion",
        "revolution",
    ]
    assert index.status.native_geometry == "complete"
    rows = index.to_rows(include_root=True)
    json.dumps(rows, allow_nan=False)
    first = rows[0]["entities"][1]["primitive"]["profile"][1]
    assert first["kind"] == "arc" and first["center"] == (10, 15)
    result = write_native_viewer(doc, tmp_path / profile)
    scene = json.loads((result.directory / "scene.json").read_bytes())
    assert result.rendered_entities == len(scene["meshes"]) == 3
    # An unknown sibling still keeps the whole owner opaque in these profiles.
    unknown = bytearray(extrusion(SLOT, SLOT_ARCS))
    struct.pack_into("<I", unknown, 40, 0x1B0)
    doc = icadkit.read(view_parts([*records, unknown], profile=profile))
    assert all(e.primitive is None for e in doc.read_parts().parts[0].entities)
