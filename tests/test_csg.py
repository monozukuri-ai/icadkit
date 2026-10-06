"""Synthetic program corruption and independent analytic solid checks."""

import json
import math
import struct
import sys
from dataclasses import replace

import pytest
from part_fixtures import ROOT, A, B, part, view_parts
from test_native import native
from test_profile_shapes import SLOT, SLOT_ARCS, SLOT_AREA, SLOT_PERIMETER

import icadkit
from icadkit.cli import main
from icadkit.viewer import ViewerLimits, write_native_viewer

LEFT, RIGHT, THIRD, RESULT = (0x80000003, 0x80000004, 0x80000005, 0x80000009)


def component(sid, *, linked=False, kind="box", origin=(0, 0, 0), parameters=None):
    b = bytearray(
        native(
            kind,
            origin=origin,
            axes=(0, 0, 1, 1, 0, 0),
            parameters=parameters or (10, 0, 0, 10, 10),
            first=False,
        )
    )
    struct.pack_into("<I", b, 12, b[15] << 24 | 0x101E1)
    struct.pack_into("<I", b, 16, sid)
    struct.pack_into("<I", b, 32, RESULT if linked else 0)
    return bytes(b)


def prism(
    sid, *, linked=False, origin=(0, 0, 0), height=5.0, points=None, flags=0x101E1
):
    # Six XY vertices after a signed height. The default is a regular hexagon
    # of circumradius 10: area 150 * sqrt(3), perimeter 60.
    if points is None:
        points = [
            (10 * math.cos(k * math.pi / 3), 10 * math.sin(k * math.pi / 3))
            for k in range(6)
        ]
    b = bytearray(224)
    struct.pack_into("<7I", b, 0, 224, 1, 0, 70 << 24 | flags, sid, 0, 0x584400C8)
    struct.pack_into("<I", b, 32, RESULT if linked else 0)
    struct.pack_into("<I", b, 40, 0x180)
    struct.pack_into(
        "<22d",
        b,
        48,
        *origin,
        0,
        0,
        1,
        1,
        0,
        0,
        height,
        *(v for q in points for v in q),
    )
    return bytes(b)


def extrusion(
    sid,
    elements=None,
    arcs=None,
    *,
    height=13.0,
    flags=0x100E1,
    linked=False,
    origin=(0, 0, 0),
    tag=0x59,
):
    """Profile component: frame, signed height, XY elements and a flag trailer."""
    if elements is None:
        elements = SLOT
    if arcs is None:
        arcs = SLOT_ARCS
    b = bytearray(136 + 16 * len(elements))
    struct.pack_into(
        "<7I",
        b,
        0,
        len(b),
        1,
        0,
        76 << 24 | flags,
        sid,
        0,
        tag << 24 | 0x440000 | (len(b) - 24),
    )
    struct.pack_into("<I", b, 32, RESULT if linked else 0)
    struct.pack_into("<I", b, 40, 0x180)
    flat = [v for element in elements for v in element]
    struct.pack_into(
        f"<{10 + len(flat)}d", b, 48, *origin, 0, 0, 1, 1, 0, 0, height, *flat
    )
    for i in arcs or ():
        b[len(b) - 8 + i // 8] |= 0x80 >> (i % 8)
    return bytes(b)


def program(tokens, *, hidden=False, attributes=False):
    pos = 376 if attributes else 160
    b = bytearray(pos + 16 + 4 * len(tokens))
    struct.pack_into(
        "<5I",
        b,
        0,
        0x21000000,
        len(b) - 4,
        2 if attributes else 1,
        0,
        0xC9580088 if hidden else 0xC9500088,
    )
    struct.pack_into("<I", b, 152, 0xC0000001)
    if attributes:
        struct.pack_into("<I", b, 160, 0x7A0000D8)
    struct.pack_into(
        "<4I", b, pos, 0x79000000 | (16 + 4 * len(tokens)), 0, len(tokens), 0
    )
    struct.pack_into(f"<{len(tokens)}I", b, pos + 16, *tokens)
    return bytes(b)


def result(*, hidden=False):
    return struct.pack(
        "<12I",
        48,
        9,
        0,
        0x55010881 if hidden else 0x550108C1,
        RESULT,
        0,
        0xFD000018,
        0x01000000,
        1,
        0,
        0x212,
        0x01800004 if hidden else 0x01800044,
    )


def document(
    tokens=(LEFT, RIGHT, 2),
    *,
    operands=None,
    metadata=None,
    marker=None,
    root_position=(0, 0, 0),
    hidden=False,
    attributes=False,
):
    if operands is None:
        operands = [component(LEFT, linked=True), component(RIGHT, origin=(5, 0, 0))]
    records = [
        part(ROOT, child=A, root=True, profile="v7l7", position=root_position),
        part(A, parent=ROOT, profile="v7l7", position=(123, 456, 789)),
        program(tokens, hidden=hidden, attributes=attributes)
        if metadata is None
        else metadata,
        struct.pack("<I", 0x30010000),
        *operands,
        result(hidden=hidden) if marker is None else marker,
    ]
    return icadkit.read(view_parts(records, profile="v7l7"))


def body(doc=None, **kwargs):
    ix = icadkit.read_csg(document(**kwargs) if doc is None else doc)
    assert len(ix.bodies) == 1
    return ix.bodies[0]


def runtime():
    pytest.importorskip("OCP")
    pytest.importorskip("parasolid_kit")


def test_program_provenance_owner_and_operands_stay_separate():
    doc = document()
    b = body(doc)
    assert b.status == "complete" and b.tokens == (LEFT, RIGHT, 2)
    assert b.appearance.color_index == 18 and b.appearance.layer == 9
    assert b.appearance.visible
    assert len(b.operands) == 2
    assert all(e.primitive is None for p in doc.read_parts().parts for e in p.entities)
    assert b.owner_id == doc.read_parts().parts[1].part_id
    assert doc.source_bytes(b.byte_range) == result()
    for operand in b.operands:
        assert (
            doc.source_bytes(operand.primitive.byte_range)
            == operand.primitive.raw_bytes
        )
    metadata = next(
        r for r in doc.read_parts().opaque_ranges if r.reason == "entity_metadata"
    )
    assert b.raw_program == doc.source_bytes(metadata.byte_range)
    assert icadkit.read_csg(doc).model_status == "partial"


def test_unmirrored_csg_owner_below_mirrored_parent_stays_unsupported():
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, profile="v7l7"),
                part(A, parent=ROOT, child=B, flags=8, profile="v7l7"),
                part(B, parent=A, profile="v7l7"),
                program((LEFT, RIGHT, 2)),
                struct.pack("<I", 0x30010000),
                component(LEFT, linked=True),
                component(RIGHT, origin=(5, 0, 0)),
                result(),
            ],
            profile="v7l7",
        )
    )
    owner = doc.read_parts().parts[-1]
    assert not owner.is_mirror and owner.placement.world_transform is not None
    assert body(doc).diagnostics[0].code == "csg.owner"
    assert body(doc).status == "unsupported"


def test_hidden_header_and_saved_attributes():
    assert body(hidden=True).appearance.visible is False
    assert body(attributes=True).status == "complete"


def test_single_leaf_saved_tree_and_ambiguous_binding():
    leaf = bytearray(component(LEFT))
    struct.pack_into("<I", leaf, 12, 0x4B0100E1)
    b = body(tokens=(LEFT,), operands=[bytes(leaf)], marker=b"")
    assert b.status == "complete" and len(b.operands) == 1
    assert b.operands[0].entity_id == b.body_id
    assert b.appearance.status == "unsupported"
    wrong = body(tokens=(RIGHT,), operands=[bytes(leaf)], marker=b"")
    assert wrong.status == "invalid"
    assert wrong.diagnostics[0].code == "csg.result_binding"


def test_independent_body_failures_and_body_limit(tmp_path):
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, profile="v7l7"),
                part(A, parent=ROOT, next_id=B, profile="v7l7"),
                program((LEFT, RIGHT, 2)),
                struct.pack("<I", 0x30010000),
                component(LEFT, linked=True),
                component(RIGHT, origin=(5, 0, 0)),
                result(),
                part(B, parent=ROOT, previous=A, profile="v7l7"),
                program((LEFT, RIGHT, 20)),
                struct.pack("<I", 0x30010000),
                component(LEFT, linked=True),
                component(RIGHT),
                result(),
            ],
            profile="v7l7",
        )
    )
    index = icadkit.read_csg(doc)
    assert [b.status for b in index.bodies] == ["complete", "unsupported"]
    assert index.status == "partial"
    with pytest.raises(icadkit.LimitExceededError, match="max_bodies"):
        icadkit.read_csg(doc, limits=icadkit.CsgLimits(max_bodies=1))
    runtime()
    view = write_native_viewer(doc, tmp_path / "view", csg=True)
    assert view.evaluated_csg_bodies == view.rendered_entities == 1
    scene = json.loads((view.directory / "scene.json").read_bytes())
    assert scene["csg"]["omitted"] == 1


@pytest.mark.parametrize(
    "tokens,status,code",
    [
        ((LEFT, RIGHT, 20), "unsupported", "csg.operation"),
        ((LEFT, 2, 2), "invalid", "csg.stack"),
        ((LEFT, RIGHT, LEFT), "invalid", "csg.stack"),
        ((LEFT, RESULT, 2), "invalid", "csg.cycle"),
        ((LEFT, THIRD, 2), "unsupported", "csg.references"),
        ((LEFT, RIGHT, 0), "unsupported", "csg.operation"),
        ((LEFT, RIGHT, 255), "unsupported", "csg.operation"),
    ],
)
def test_bad_program_never_exposes_a_partial_result(tokens, status, code):
    b = body(tokens=tokens)
    assert b.status == status and b.operands == ()
    assert b.diagnostics[0].code == code
    with pytest.raises(icadkit.UnsupportedFormatError, match="unqualified"):
        icadkit.evaluate_csg(b)


@pytest.mark.parametrize(
    "offset,value", [(156, 7), (160, 0x79000020), (168, 4), (164, 1)]
)
def test_program_framing_never_searches_for_a_replacement(offset, value):
    meta = bytearray(program((LEFT, RIGHT, 2)))
    struct.pack_into("<I", meta, offset, value)
    index = icadkit.read_csg(document(metadata=bytes(meta)))
    assert index.status != "complete"
    assert all(b.status != "complete" for b in index.bodies)


@pytest.mark.parametrize(
    "change,code",
    [
        ("duplicate", "csg.duplicate_id"),
        ("extra", "csg.references"),
        ("mirror", "csg.operand_layout"),
        ("negative", "csg.signed_height"),
        ("nan", "csg.operand_frame"),
        ("backlink", "csg.result_binding"),
        ("foreign_tree", "csg.result_binding"),
    ],
)
def test_component_ownership_and_geometry_guards(change, code):
    left = component(LEFT, linked=True)
    right = bytearray(component(RIGHT, origin=(5, 0, 0)))
    marker = bytearray(result())
    if change == "duplicate":
        struct.pack_into("<I", right, 16, LEFT)
    if change == "mirror":
        struct.pack_into("<I", right, 12, 0x4B0111E1)
    if change == "negative":
        struct.pack_into("<d", right, 120, -10)
    if change == "nan":
        struct.pack_into("<d", right, 48, math.nan)
    if change == "backlink":
        struct.pack_into("<I", right, 32, RESULT)
    if change == "foreign_tree":
        struct.pack_into("<I", marker, 32, 9)
    operands = [left, bytes(right)] + ([component(THIRD)] if change == "extra" else [])
    b = body(operands=operands, marker=bytes(marker))
    assert b.status != "complete" and b.operands == ()
    assert b.diagnostics[0].code == code


def test_profile_and_program_limits():
    v8 = icadkit.read(view_parts([part(ROOT, root=True)]))
    assert icadkit.read_csg(v8).status == "unsupported"
    for limits in (
        icadkit.CsgLimits(max_operands=1),
        icadkit.CsgLimits(max_stack_depth=1),
    ):
        with pytest.raises(icadkit.LimitExceededError):
            icadkit.read_csg(document(), limits=limits)
    tokens = (LEFT, RIGHT, 2, 0, 255, THIRD, 1)
    operands = [component(LEFT, linked=True), component(RIGHT), component(THIRD)]
    doc = document(tokens, operands=operands)
    assert body(doc).status == "complete"
    with pytest.raises(icadkit.LimitExceededError):
        icadkit.read_csg(doc, limits=icadkit.CsgLimits(max_operations=1))
    for value in (0, -1, True, 2**32):
        with pytest.raises(ValueError):
            icadkit.CsgLimits(max_bodies=value)


@pytest.mark.parametrize(
    "operator,volume,area,center",
    [
        (1, 1500, 800, (7.5, 5, 5)),
        (2, 500, 400, (2.5, 5, 5)),
        (3, 500, 400, (7.5, 5, 5)),
    ],
)
def test_analytic_final_solids(operator, volume, area, center):
    runtime()
    mesh = icadkit.evaluate_csg(body(tokens=(LEFT, RIGHT, operator)))
    assert mesh.volume_mm3 == pytest.approx(volume)
    assert mesh.area_mm2 == pytest.approx(area)
    assert mesh.centroid_mm == pytest.approx(center)
    assert mesh.solid_count == 1
    assert len(mesh.triangles) >= 36
    # Independent signed triangle volume verifies winding and units.
    points = [mesh.positions[i : i + 3] for i in range(0, len(mesh.positions), 3)]
    signed = 0
    for i in range(0, len(mesh.triangles), 3):
        a, b, c = [points[j] for j in mesh.triangles[i : i + 3]]
        signed += (
            sum(
                a[k]
                * (b[(k + 1) % 3] * c[(k + 2) % 3] - b[(k + 2) % 3] * c[(k + 1) % 3])
                for k in range(3)
            )
            / 6
        )
    assert signed == pytest.approx(volume)


def test_through_hole_and_root_frame_applied_once():
    runtime()
    operands = [
        component(LEFT, linked=True),
        component(RIGHT, kind="cylinder", origin=(5, 5, -2), parameters=(2, 14)),
    ]
    b = body(operands=operands, root_position=(13, -17, 19))
    mesh = icadkit.evaluate_csg(b)
    assert mesh.volume_mm3 == pytest.approx(1000 - 40 * math.pi)
    assert mesh.area_mm2 == pytest.approx(600 + 32 * math.pi)
    assert mesh.centroid_mm == pytest.approx((-8, 22, -14))
    assert mesh.solid_count == 1
    # A ray down the hole axis must hit no tessellated cap or uncut box.
    positions = [mesh.positions[i : i + 3] for i in range(0, len(mesh.positions), 3)]
    for i in range(0, len(mesh.triangles), 3):
        points = [positions[j] for j in mesh.triangles[i : i + 3]]
        signs = [
            (b[0] - a[0]) * (22 - a[1]) - (b[1] - a[1]) * (-8 - a[0])
            for a, b in zip(points, points[1:] + points[:1], strict=True)
        ]
        assert not (min(signs) > 1e-8 or max(signs) < -1e-8)


@pytest.mark.parametrize("height", [5.0, -5.0])
def test_prism_operand_keeps_profile_and_moves_base_for_negative_height(height):
    b = body(operands=[prism(LEFT, linked=True, height=height), component(RIGHT)])
    assert b.status == "complete" and not b.diagnostics
    p = b.operands[0].primitive
    assert p.kind == "polygon_extrusion" and p.height == 5
    assert len(p.profile_points) == 6
    assert p.profile_points[0] == (10, 0)
    assert p.profile_points[3] == pytest.approx((-10, 0))
    # The root sits at the origin; only the base moves, and no axis is reversed.
    assert [row[3] for row in p.world_transform[:3]] == [0, 0, min(height, 0)]
    assert [row[:3] for row in p.world_transform[:3]] == [
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
    ]
    assert p.raw_bytes == prism(LEFT, height=height)[48:]
    assert p.x_bounds is None and p.radius is None


@pytest.mark.parametrize(
    "change,status,code",
    [
        ({"height": 0.0}, "invalid", "csg.operand_dimensions"),
        ({"height": math.nan}, "invalid", "csg.operand_frame"),
        (
            {"points": [(10, 0), (10, 0), (-5, 9), (-10, 0), (-5, -9), (5, -9)]},
            "invalid",
            "csg.operand_dimensions",
        ),
        # A notch, a twice-wound triangle and a straight corner are not convex.
        (
            {"points": [(10, 0), (5, 9), (0, 2), (-10, 0), (-5, -9), (5, -9)]},
            "unsupported",
            "csg.operand_layout",
        ),
        (
            {"points": [(10, 0), (-5, 9), (-5, -9), (10, 0.5), (-5, 9.5), (-5.5, -9)]},
            "unsupported",
            "csg.operand_layout",
        ),
        (
            {"points": [(10, 0), (10, 5), (10, 9), (-10, 9), (-10, -9), (5, -9)]},
            "unsupported",
            "csg.operand_layout",
        ),
        ({"flags": 0x111E1}, "unsupported", "csg.operand_layout"),
        ({"flags": 0x100E1}, "unsupported", "csg.operand_layout"),
    ],
)
def test_prism_operand_guards(change, status, code):
    b = body(operands=[prism(LEFT, linked=True, **change), component(RIGHT)])
    assert b.status == status and not b.operands
    assert b.diagnostics[0].code == code


@pytest.mark.parametrize("height", [5.0, -5.0])
@pytest.mark.parametrize("reverse", [False, True])
def test_prism_with_through_hole_has_independent_mass(height, reverse):
    runtime()
    points = [
        (10 * math.cos(k * math.pi / 3), 10 * math.sin(k * math.pi / 3))
        for k in range(6)
    ]
    operands = [
        prism(
            LEFT,
            linked=True,
            origin=(4, -3, 2),
            height=height,
            points=points[::-1] if reverse else points,
        ),
        component(RIGHT, kind="cylinder", origin=(4, -3, -20), parameters=(3, 40)),
    ]
    mesh = icadkit.evaluate_csg(body(operands=operands))
    hexagon = 150 * math.sqrt(3)
    assert mesh.volume_mm3 == pytest.approx(5 * (hexagon - 9 * math.pi))
    assert mesh.area_mm2 == pytest.approx(
        2 * (hexagon - 9 * math.pi) + 60 * 5 + 6 * math.pi * 5
    )
    assert mesh.centroid_mm == pytest.approx((4, -3, 2 + height / 2))
    assert mesh.solid_count == 1


@pytest.mark.parametrize("height", [13.0, -13.0])
def test_single_leaf_profile_extrusion_keeps_segments_and_moves_base(height):
    record = extrusion(LEFT, height=height, origin=(4, -3, 2))
    b = body(tokens=(LEFT,), operands=[record], marker=b"")
    assert b.status == "complete" and not b.diagnostics
    assert b.operands[0].entity_id == b.body_id
    p = b.operands[0].primitive
    assert p.kind == "profile_extrusion" and p.height == 13
    assert [s.kind for s in p.profile] == ["line", "arc", "line", "arc"]
    assert p.profile[0].start == (13, 5) and p.profile[0].end == (13, 15)
    assert p.profile[1].center == (10, 15)
    assert p.profile[1].sweep_angle == pytest.approx(math.pi)
    assert p.profile[1].end == pytest.approx((7, 15))
    assert p.profile[3].end == pytest.approx((13, 5))
    # The root sits at the origin; only the base moves, and no axis is reversed.
    assert [row[3] for row in p.world_transform[:3]] == [4, -3, 2 + min(height, 0)]
    assert [row[:3] for row in p.world_transform[:3]] == [
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
    ]
    assert p.raw_bytes == record[48:]
    assert p.profile_points is None and p.radius is None and p.x_bounds is None
    assert p.mirror_convention is None


# The same slot traced clockwise, closed by an explicit final vertex.
CLOCKWISE_SLOT = [
    (13, 5),
    (10, 5),
    (-math.pi, 0),
    (7, 15),
    (10, 15),
    (-math.pi, 0),
    (13, 5),
]


@pytest.mark.parametrize("height", [13.0, -13.0])
@pytest.mark.parametrize(
    "elements,arcs", [(SLOT, SLOT_ARCS), (CLOCKWISE_SLOT, (1, 2, 4, 5))]
)
def test_single_leaf_profile_extrusion_mass_and_root_frame(height, elements, arcs):
    runtime()
    record = extrusion(LEFT, elements, arcs, height=height, origin=(4, -3, 2))
    b = body(tokens=(LEFT,), operands=[record], marker=b"", root_position=(13, -17, 19))
    mesh = icadkit.evaluate_csg(b)
    assert mesh.volume_mm3 == pytest.approx(SLOT_AREA * 13)
    assert mesh.area_mm2 == pytest.approx(2 * SLOT_AREA + SLOT_PERIMETER * 13)
    assert mesh.centroid_mm == pytest.approx((1, 24, -17 + height / 2))
    assert mesh.solid_count == 1


def test_profile_disc_component_cuts_a_through_hole():
    runtime()
    disc = extrusion(
        RIGHT,
        [(8, 5), (5, 5), (math.tau, 0)],
        (1, 2),
        height=14,
        origin=(0, 0, -2),
        flags=0x101E1,
    )
    mesh = icadkit.evaluate_csg(body(operands=[component(LEFT, linked=True), disc]))
    assert mesh.volume_mm3 == pytest.approx(1000 - 90 * math.pi)
    assert mesh.area_mm2 == pytest.approx(600 + 42 * math.pi)
    assert mesh.centroid_mm == pytest.approx((5, 5, 5))


@pytest.mark.parametrize(
    "change,status,code",
    [
        ({"height": 0.0}, "invalid", "csg.operand_dimensions"),
        ({"height": math.nan}, "invalid", "csg.operand_frame"),
        # An open profile, a nonfinite vertex and a flagged first element.
        (
            {"elements": [(0, 0), (10, 0), (10, 5), (0, 5)], "arcs": ()},
            "invalid",
            "csg.operand_dimensions",
        ),
        (
            {"elements": [(0, 0), (10, math.inf), (10, 5), (0, 0)], "arcs": ()},
            "invalid",
            "csg.operand_dimensions",
        ),
        ({"arcs": (0, 1, 2, 3, 5, 6)}, "unsupported", "csg.operand_layout"),
        # Mirror, standalone and explicit-component headers are not a leaf.
        ({"flags": 0x110E1}, "unsupported", "csg.operand_layout"),
        ({"flags": 0x100C1}, "unsupported", "csg.operand_layout"),
        ({"flags": 0x101E1}, "unsupported", "csg.operand_layout"),
        ({"tag": 0x5A}, "unsupported", "csg.operand_layout"),
    ],
)
def test_profile_operand_guards(change, status, code):
    b = body(tokens=(LEFT,), operands=[extrusion(LEFT, **change)], marker=b"")
    assert b.status == status and not b.operands
    assert b.diagnostics[0].code == code


def test_checkpoint_disjoint_empty_and_limits():
    runtime()
    operands = [
        component(LEFT, linked=True),
        component(RIGHT, origin=(5, 0, 0)),
        component(THIRD, origin=(30, 0, 0)),
    ]
    mesh = icadkit.evaluate_csg(
        body(tokens=(LEFT, RIGHT, 2, 0, 255, THIRD, 1), operands=operands)
    )
    assert mesh.volume_mm3 == pytest.approx(1500) and mesh.solid_count == 2
    empty = body(
        tokens=(LEFT, RIGHT, 3),
        operands=operands[:1] + [component(RIGHT, origin=(30, 0, 0))],
    )
    with pytest.raises(icadkit.UnsupportedFormatError, match="Empty"):
        icadkit.evaluate_csg(empty)
    for limits in (
        icadkit.CsgLimits(max_triangles=1),
        icadkit.CsgLimits(max_vertices=1),
        icadkit.CsgLimits(max_subshapes=1),
    ):
        with pytest.raises(icadkit.LimitExceededError):
            icadkit.evaluate_csg(body(), limits=limits)
    b = body()
    primitive = replace(
        b.operands[0].primitive,
        world_transform=((2, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
    )
    bad = replace(
        b, operands=(replace(b.operands[0], primitive=primitive), b.operands[1])
    )
    with pytest.raises(icadkit.InvalidFormatError, match="Non-rigid"):
        icadkit.evaluate_csg(bad)


def test_viewer_opt_in_failure_and_cli(tmp_path, capsys):
    runtime()
    d = document(hidden=True)
    plain = write_native_viewer(d, tmp_path / "plain")
    assert plain.rendered_entities == 0
    viewed = write_native_viewer(d, tmp_path / "csg", csg=True)
    assert viewed.rendered_entities == viewed.evaluated_csg_bodies == 1
    scene = json.loads((viewed.directory / "scene.json").read_bytes())
    assert scene["scope"] == "qualified_native_csg"
    assert scene["csg"] == dict(enabled=True, bodies=1, evaluated=1, omitted=0)
    entities = scene["parts"][1]["entities"]
    assert len(scene["meshes"]) == 1 and entities[-1]["appearance"]["visible"] is False
    assert [e["csg_role"] for e in entities[:-1]] == ["operand", "operand"]
    source = tmp_path / "synthetic.bin"
    source.write_bytes(d.source_bytes(icadkit.ByteRange(0, d.file_size)))
    assert (
        main(
            [
                "view",
                str(source),
                "--csg",
                "--write-only",
                "--json",
                "--output",
                str(tmp_path / "cli"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["evaluated_csg_bodies"] == 1
    with pytest.raises(icadkit.LimitExceededError):
        write_native_viewer(
            d, tmp_path / "limited", csg=True, limits=ViewerLimits(max_triangles=1)
        )
    assert not (tmp_path / "limited").exists()
    failed = write_native_viewer(
        document(tokens=(LEFT, RIGHT, 20)), tmp_path / "unknown", csg=True
    )
    scene = json.loads((failed.directory / "scene.json").read_bytes())
    assert not scene["meshes"] and scene["csg"]["omitted"] == 1


def test_missing_optional_runtime_never_publishes(tmp_path, monkeypatch):
    # A None entry makes the import of the backend module raise ImportError.
    monkeypatch.setitem(sys.modules, "parasolid_kit.interop.occt", None)
    with pytest.raises(icadkit.UnsupportedFormatError, match=r"icadkit\[preview\]"):
        write_native_viewer(document(), tmp_path / "out", csg=True)
    assert not (tmp_path / "out").exists()
