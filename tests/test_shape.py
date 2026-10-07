"""Part shapes: saved bodies and CSG results in the part or world frame."""

import json
import math
import struct

import pytest
from fixture_builders import document_factory
from part_fixtures import ROOT, A, B, part, view_parts
from test_csg import LEFT, RIGHT, component, program, result
from test_saved import resource

import icadkit
from icadkit.shape import PartShapeLimits

# The authored box payload spans 10 x 20 x 30 m at (2, -1, 3): in mm its
# centre is (7000, 9000, 18000), volume 6e12 and area 2.2e9.
CENTRE = (7000.0, 9000.0, 18000.0)
VOLUME = 6000 * 1e9
AREA = 2200 * 1e6


def backend():
    pytest.importorskip("parasolid_kit")
    pytest.importorskip("OCP")


def saved_document(
    *,
    position=(100, 0, 0),
    axes=(0, 0, 1, 0, 1, 0),
    profile="v7l7",
    reference="",
    flags=None,
    with_csg=False,
    owner_records=None,
):
    """Root plus child A, whose entity list holds a saved final marker."""
    records = [
        part(ROOT, root=True, child=A, profile=profile),
        part(
            A,
            parent=ROOT,
            profile=profile,
            position=position,
            axes=axes,
            reference=reference,
            flags=flags,
        ),
    ]
    if with_csg:
        records += [
            program((LEFT, RIGHT, 2)),
            struct.pack("<I", 0x30010000),
            component(LEFT, linked=True),
            component(RIGHT, origin=(5, 0, 0)),
            result(),
        ]
    elif owner_records is None:
        records += [struct.pack("<I", 0x30010000), result()]
    else:
        records += owner_records
    view = view_parts(records, profile=profile)
    size = int.from_bytes(view[500:504], "little") * 4
    data = bytearray(
        document_factory()(
            [resource(origin=(0, 0, 0))],
            view_payload=view[504 : 496 + size - 4],
            tail=b"",
        )
    )
    data[12:16] = view[12:16]
    return icadkit.read(bytes(data))


def child(doc):
    return next(p for p in doc.read_parts().parts if not p.is_root)


def test_saved_body_in_the_part_frame_and_in_the_world_frame():
    backend()
    doc = saved_document()
    part_id = child(doc).part_id
    shape = icadkit.read_part_shape(doc, part_id)
    assert shape.status == "complete" and shape.reason is None
    assert shape.frame == "part" and shape.length_unit == "mm"
    (body,) = shape.bodies
    assert body.source == "saved" and body.status == "complete"
    assert body.face_count == 6 and body.solid_count == 1
    assert body.volume_mm3 == pytest.approx(VOLUME)
    assert body.area_mm2 == pytest.approx(AREA)
    # Part A sits at (100, 0, 0) with X along +Y, so Y is along -X.
    assert body.centroid_mm == pytest.approx((9000, -6900, 18000))
    assert shape.centroid_mm == pytest.approx((9000, -6900, 18000))
    assert shape.volume_mm3 == pytest.approx(VOLUME) and shape.solid_count == 1
    assert shape.shape is not None and not shape.shape.IsNull()
    world = icadkit.read_part_shape(doc, part_id, frame="world")
    assert world.centroid_mm == pytest.approx(CENTRE)
    assert world.frame_transform == (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    mesh = icadkit.evaluate_saved_body(doc, icadkit.read_saved_bodies(doc).bodies[0])
    assert mesh.centroid_mm == pytest.approx(world.centroid_mm)
    assert body.payload_sha256 == mesh.payload_sha256
    # The part frame transform is the inverse of the saved part frame.
    placement = child(doc).placement.world_transform
    for i in range(3):
        assert shape.frame_transform[i][3] == pytest.approx(
            -sum(placement[k][i] * placement[k][3] for k in range(3))
        )


def test_csg_result_is_used_only_without_a_qualified_saved_body():
    backend()
    doc = saved_document(with_csg=True)
    part_id = child(doc).part_id
    assert icadkit.read_csg(doc).bodies[0].status == "complete"
    shape = icadkit.read_part_shape(doc, part_id, frame="world")
    assert [b.source for b in shape.bodies] == ["saved"]
    assert shape.centroid_mm == pytest.approx(CENTRE)
    # Without the resource the saved marker cannot bind; the CSG result remains.
    records = [
        part(ROOT, root=True, child=A, profile="v7l7"),
        part(A, parent=ROOT, profile="v7l7", position=(123, 456, 789)),
        program((LEFT, RIGHT, 2)),
        struct.pack("<I", 0x30010000),
        component(LEFT, linked=True),
        component(RIGHT, origin=(5, 0, 0)),
        result(),
    ]
    doc = icadkit.read(view_parts(records, profile="v7l7"))
    part_id = child(doc).part_id
    world = icadkit.read_part_shape(doc, part_id, frame="world")
    (body,) = world.bodies
    assert body.source == "csg" and body.status == "complete"
    mesh = icadkit.evaluate_csg(icadkit.read_csg(doc).bodies[0])
    assert body.volume_mm3 == pytest.approx(mesh.volume_mm3)
    assert body.centroid_mm == pytest.approx(mesh.centroid_mm)
    local = icadkit.read_part_shape(doc, part_id)
    assert local.status == "complete"
    assert local.centroid_mm == pytest.approx(
        tuple(c - o for c, o in zip(mesh.centroid_mm, (123, 456, 789), strict=True))
    )


def test_parts_without_bodies_and_external_occurrences_are_classified():
    backend()
    doc = saved_document(owner_records=[])
    shape = icadkit.read_part_shape(doc, child(doc).part_id)
    assert shape.status == "unsupported" and shape.reason == "shape.no_bodies"
    assert shape.shape is None and shape.bodies == ()
    with pytest.raises(icadkit.UnsupportedFormatError, match="shape.empty"):
        shape.write_step("unused.step")
    external = saved_document(reference="EXT", flags=0x50, owner_records=[])
    occurrence = child(external)
    assert occurrence.is_external
    shape = icadkit.read_part_shape(external, occurrence.part_id)
    assert shape.reason == "shape.external_reference"


def test_index_reports_every_part_and_serializes_rows():
    backend()
    doc = saved_document()
    index = icadkit.read_part_shapes(doc)
    assert index.status == "complete" and index.frame == "part"
    assert [s.status for s in index.shapes] == ["unsupported", "complete"]
    assert index.shapes[0].reason == "shape.no_bodies"
    rows = index.to_rows()
    json.dumps(rows)
    assert rows[1]["converted_count"] == 1 and rows[1]["sources"] == ["saved"]
    assert rows[1]["bodies"][0]["face_count"] == 6
    selected = icadkit.read_part_shapes(doc, part_ids=[child(doc).part_id])
    assert len(selected.shapes) == 1 and selected.shape(child(doc).part_id)
    with pytest.raises(KeyError):
        selected.shape("part:missing")
    with pytest.raises(KeyError):
        icadkit.read_part_shapes(doc, part_ids=["part:missing"])
    with pytest.raises(KeyError):
        icadkit.read_part_shape(doc, "part:missing")
    with pytest.raises(icadkit.LimitExceededError):
        icadkit.read_part_shapes(doc, limits=PartShapeLimits(max_parts=1))


def test_step_and_brep_export_never_overwrite(tmp_path):
    backend()
    doc = saved_document()
    shape = icadkit.read_part_shape(doc, child(doc).part_id)
    step = shape.write_step(tmp_path / "part.step")
    brep = shape.write_brep(tmp_path / "part.brep")
    assert step.stat().st_size > 0 and brep.stat().st_size > 0
    assert b"ISO-10303-21" in step.read_bytes()[:64]
    with pytest.raises(FileExistsError):
        shape.write_step(step)
    from OCP.BRep import BRep_Builder
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepTools import BRepTools
    from OCP.GProp import GProp_GProps
    from OCP.TopoDS import TopoDS_Shape

    reread = TopoDS_Shape()
    assert BRepTools.Read_s(reread, str(brep), BRep_Builder())
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(reread, props)
    assert props.Mass() == pytest.approx(VOLUME)
    centre = props.CentreOfMass()
    assert (centre.X(), centre.Y(), centre.Z()) == pytest.approx((9000, -6900, 18000))


def test_unbound_marker_keeps_its_diagnostic_and_the_part_is_unsupported():
    backend()
    doc = saved_document(owner_records=[struct.pack("<I", 0x30010000), result()])
    # Replace the resource frame with a non-rigid one: binding fails.
    records = [
        part(ROOT, root=True, child=A, profile="v7l7"),
        part(A, parent=ROOT, profile="v7l7"),
        struct.pack("<I", 0x30010000),
        result(),
    ]
    view = view_parts(records, profile="v7l7")
    size = int.from_bytes(view[500:504], "little") * 4
    data = bytearray(
        document_factory()(
            [resource(bad_frame=True)],
            view_payload=view[504 : 496 + size - 4],
            tail=b"",
        )
    )
    data[12:16] = view[12:16]
    bad = icadkit.read(bytes(data))
    shape = icadkit.read_part_shape(bad, child(bad).part_id)
    assert shape.status == "unsupported" and shape.reason == "saved.frame"
    (body,) = shape.bodies
    assert body.status == "unsupported" and body.source == "saved"
    assert icadkit.read_part_shapes(bad).status == "unsupported"
    assert doc.read_parts().status.index == "complete"


def test_missing_backend_is_reported_per_body(monkeypatch):
    import sys

    doc = saved_document()
    monkeypatch.setitem(sys.modules, "parasolid_kit.interop.occt", None)
    index = icadkit.read_part_shapes(doc)
    assert index.status == "unsupported"
    assert index.diagnostics[0].code == "csg.missing_dependency"
    shape = index.shape(child(doc).part_id)
    assert shape.reason == "csg.missing_dependency"
    assert shape.bodies[0].status == "unsupported"


def test_argument_validation():
    doc = saved_document()
    with pytest.raises(ValueError, match="frame"):
        icadkit.read_part_shape(doc, child(doc).part_id, frame="sheet")
    with pytest.raises(TypeError):
        icadkit.read_part_shape(doc, 7)
    with pytest.raises(TypeError):
        icadkit.read_part_shapes(doc, part_ids=[7])
    with pytest.raises(TypeError):
        icadkit.read_part_shape(doc, child(doc).part_id, limits=object())
    with pytest.raises(TypeError):
        icadkit.read_part_shape(b"", child(doc).part_id)
    with pytest.raises(ValueError):
        PartShapeLimits(max_bodies=0)
    with pytest.raises(TypeError):
        PartShapeLimits(saved=object())
    assert PartShapeLimits().saved == icadkit.SavedBodyLimits()


def test_mirrored_owner_without_an_evaluated_frame_is_unsupported_in_part_frame():
    backend()
    records = [
        part(ROOT, root=True, child=A, profile="v7l7"),
        part(A, parent=ROOT, child=B, flags=8, profile="v7l7"),
        part(B, parent=A, profile="v7l7"),
        struct.pack("<I", 0x30010000),
        result(),
    ]
    doc = icadkit.read(view_parts(records, profile="v7l7"))
    mirrored = doc.read_parts().parts[1]
    assert mirrored.is_mirror
    if mirrored.placement.world_transform is None:
        shape = icadkit.read_part_shape(doc, mirrored.part_id)
        assert shape.status == "unsupported" and shape.reason == "shape.frame"
    else:
        assert math.isfinite(mirrored.placement.world_transform[0][0])


def native_document(record, *, position=(0, 0, 0)):
    from test_native import ROOT, A, part, view_parts

    return icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A),
                part(A, parent=ROOT, position=position),
                record,
            ]
        )
    )


@pytest.mark.parametrize(
    "kind,parameters,volume,centre",
    [
        ("sphere", (7, 7, math.pi), 4 / 3 * math.pi * 343, (17, -23, 9)),
        # A frustum of height 27, base radius 9 and top radius 3 along +Y.
        ("cone", (27, 0, 0, 0, 0, 9, 3), math.pi * 27 * (81 + 27 + 9) / 3, None),
        ("torus", (math.tau, 13, 2.5, 0), 2 * math.pi**2 * 13 * 2.5**2, (17, -23, 9)),
        ("cylinder", (7, 29), math.pi * 49 * 29, (17, -23 + 14.5, 9)),
    ],
)
def test_standalone_native_primitives_are_the_third_source(
    kind, parameters, volume, centre
):
    backend()
    from test_native import native

    doc = native_document(
        native(kind, origin=(17, -23, 9), parameters=parameters), position=(100, 0, 0)
    )
    world = icadkit.read_part_shape(doc, child(doc).part_id, frame="world")
    assert world.status == "complete"
    (body,) = world.bodies
    assert body.source == "native" and body.solid_count == 1
    assert body.volume_mm3 == pytest.approx(volume, rel=1e-9)
    if centre is not None:
        assert world.centroid_mm == pytest.approx(centre)
    local = icadkit.read_part_shape(doc, child(doc).part_id)
    assert local.centroid_mm[0] == pytest.approx(world.centroid_mm[0] - 100)
    assert local.volume_mm3 == pytest.approx(volume, rel=1e-9)


def test_revolution_profile_is_revolved_about_the_frame_axis():
    backend()
    from test_profile_shapes import revolution

    # A cylinder of radius 5 between axial 0 and 10, as a revolved polyline.
    doc = native_document(
        struct.pack("<I", 0x30010000) + bytes(revolution([(5, 0), (5, 10)]))
    )
    shape = icadkit.read_part_shape(doc, child(doc).part_id, frame="world")
    assert shape.status == "complete" and shape.bodies[0].source == "native"
    assert shape.volume_mm3 == pytest.approx(math.pi * 25 * 10, rel=1e-9)
    assert shape.area_mm2 == pytest.approx(2 * math.pi * 5 * 10 + 2 * math.pi * 25)


def test_unqualified_native_entity_is_a_failed_native_body():
    backend()
    from test_native import native

    doc = native_document(native("sphere", parameters=(7, 0, 0)))
    shape = icadkit.read_part_shape(doc, child(doc).part_id)
    assert shape.status == "unsupported" and shape.bodies[0].source == "native"
    assert shape.reason == shape.bodies[0].diagnostics[0].code


def test_negative_height_box_uses_the_reversed_axis_and_mirrored_bounds():
    backend()
    from test_native import native

    doc = native_document(native("box", parameters=(-23, -3, -5, 10, 12)))
    box = doc.read_parts().parts[1].entities[0].primitive
    assert box.height == 23 and box.y_bounds == (-5, 12)
    m = box.world_transform
    local = ((-3 + 10) / 2, (-5 + 12) / 2, 23 / 2)
    expected = tuple(
        sum(m[i][j] * local[j] for j in range(3)) + m[i][3] for i in range(3)
    )
    shape = icadkit.read_part_shape(doc, child(doc).part_id, frame="world")
    assert shape.status == "complete"
    assert shape.volume_mm3 == pytest.approx(13 * 17 * 23)
    assert shape.centroid_mm == pytest.approx(expected)
