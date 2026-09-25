"""Synthetic native records: malformed inputs and independently specified geometry."""

import json
import math
import struct
from dataclasses import FrozenInstanceError

import pytest
from part_fixtures import ROOT, A, B, part, view_parts

import icadkit
from icadkit.cli import main


def native(
    kind="box",
    *,
    color=1,
    visible=True,
    layer=1,
    mirror=False,
    origin=(7, -11, 19),
    axes=(0, 1, 0, 1, 0, 0),
    parameters=None,
    first=True,
):
    size, code, marker = {
        "box": (160, 75, 0x56440088),
        "cylinder": (136, 71, 0x50440070),
        "sphere": (144, 72, 0x53440078),
        "cone": (176, 68, 0x57440098),
        "torus": (152, 74, 0x54440080),
    }[kind]
    b = bytearray(size)
    struct.pack_into(
        "<7I",
        b,
        0,
        size,
        layer,
        0,
        code << 24 | 0x10081 | (0x40 if visible else 0) | (0x1000 if mirror else 0),
        0x80000003,
        0,
        marker,
    )
    b[28] = 2 | (color & 16)
    b[29] = 0x10 | (color & 15)
    b[36:40] = bytes((b[28], color & 15, b[28], color & 15))
    struct.pack_into("<I", b, 40, 0x180)
    struct.pack_into("<9d", b, 48, *origin, *axes)
    if parameters is None:
        parameters = {
            "box": (23, -3, -5, 10, 12),
            "cylinder": (7, 29),
            "sphere": (9, 9, math.pi),
            "cone": (27, 0, 0, 0, 0, 9, 3),
            "torus": (math.tau, 13, 2.5, 0),
        }[kind]
    struct.pack_into("<" + str(len(parameters)) + "d", b, 120, *parameters)
    return (struct.pack("<I", 0x30010000) if first else b"") + b


def read(record):
    doc = icadkit.read(
        view_parts([part(ROOT, root=True, child=A), part(A, parent=ROOT), record])
    )
    return doc, doc.read_parts()


def test_box_parameters_global_frame_and_exact_provenance():
    doc, index = read(native())
    owner = index.parts[1]
    e = owner.entities[0]
    p = e.primitive
    assert isinstance(p, icadkit.NativePrimitive)
    assert p.kind == "box" and p.box_dimensions == (13, 17, 23)
    assert p.radius is None and p.x_bounds == (-3, 10) and p.y_bounds == (-5, 12)
    assert p.world_transform == (
        (1, 0, 0, 7),
        (0, 0, 1, -11),
        (0, -1, 0, 19),
        (0, 0, 0, 1),
    )
    assert p.coordinate_system == "3DGLOBAL" and p.length_unit == "mm"
    assert p.length_unit_source == "qualified_native_profile"
    assert e.owner_id == owner.part_id and e.source_id == 0x80000003
    assert e.raw_type == 75 and e.geometry_status == "complete"
    assert doc.source_bytes(p.byte_range) == p.raw_bytes
    assert doc.source_bytes(e.appearance.byte_range) == e.appearance.raw_bytes
    assert index.status.native_geometry == index.status.appearance == "complete"
    assert owner.geometry_status == "not_checked" and index.status.model == "partial"
    with pytest.raises(FrozenInstanceError):
        e.primitive = None
    row = json.loads(json.dumps(index.to_rows(), allow_nan=False))[0]
    assert row["entities"][0]["primitive"]["box_dimensions"] == [13, 17, 23]
    assert row["resource_ids"] is None


def test_cylinder_radius_height_and_axis_do_not_use_part_frame():
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A),
                part(A, parent=ROOT, position=(100, 200, 300)),
                native("cylinder"),
            ]
        )
    )
    p = doc.read_parts().parts[1].entities[0].primitive
    assert p.kind == "cylinder" and p.radius == 7 and p.height == 29
    assert p.box_dimensions is None
    assert tuple(row[3] for row in p.world_transform[:3]) == (7, -11, 19)
    assert tuple(row[2] for row in p.world_transform[:3]) == (0, 1, 0)


@pytest.mark.parametrize("color", [1, 2, 5, 15, 16, 17, 18, 31])
@pytest.mark.parametrize("visible", [True, False])
def test_palette_high_bit_and_visibility_are_independent(color, visible):
    _, index = read(native(color=color, visible=visible, layer=37))
    a = index.parts[1].entities[0].appearance
    assert (a.color_index, a.visible, a.layer) == (color, visible, 37)
    assert a.status == "complete"


def test_zero_color_remains_unknown_without_dropping_primitive():
    _, index = read(native(color=0))
    e = index.parts[1].entities[0]
    assert e.appearance.color_index is None and e.appearance.status == "unsupported"
    assert e.primitive is not None and e.geometry_status == "complete"
    assert any(d.code == "native.color" for d in e.diagnostics)


def test_opaque_high_metadata_word_does_not_change_dimensions():
    b = bytearray(native())
    struct.pack_into("<I", b, 4 + 40, 0xACBD0180)
    _, index = read(b)
    assert index.parts[1].entities[0].primitive.box_dimensions == (13, 17, 23)
    assert index.parts[1].entities[0].appearance.raw_bytes[42:44] == b"\xbd\xac"


@pytest.mark.parametrize(
    "kind,mirror", [("sphere", True), ("box", True), ("cylinder", True)]
)
def test_mirror_layout_validation_keeps_appearance_and_ownership(kind, mirror):
    _, index = read(native(kind, mirror=mirror, color=18, visible=False))
    owner = index.parts[1]
    e = owner.entities[0]
    if kind == "box":
        assert e.primitive is None and e.geometry_status == "invalid"
    else:
        assert e.primitive is not None and e.geometry_status == "complete"
    assert e.is_mirror == mirror and e.owner_id == owner.part_id
    assert e.appearance.color_index == 18 and not e.appearance.visible
    assert len(index.to_rows()) == 1
    assert index.status.native_geometry == ("invalid" if kind == "box" else "complete")


@pytest.mark.parametrize(
    "offset,value",
    [
        (48, math.nan),
        (72, math.inf),
        (88, 2),
        (96, 0),
        (120, 0),
        (120, -1),
        (128, 10),
        (136, 12),
    ],
)
def test_invalid_box_parameters_never_produce_a_primitive(offset, value):
    b = bytearray(native())
    struct.pack_into("<d", b, 4 + offset, value)
    _, index = read(b)
    e = index.parts[1].entities[0]
    assert e.primitive is None and e.geometry_status == "invalid"
    assert e.appearance.status == "complete"
    assert index.status.native_geometry == "invalid"
    assert any(d.code == "native.parameters" for d in e.diagnostics)
    json.dumps(index.to_rows(), allow_nan=False)


def test_overflowing_box_width_and_bad_cylinder_dimensions():
    for b in [
        native(parameters=(23, -1e308, -5, 1e308, 12)),
        native("cylinder", parameters=(0, 29)),
        native("cylinder", parameters=(7, -29)),
    ]:
        _, index = read(b)
        assert index.parts[1].entities[0].geometry_status == "invalid"


@pytest.mark.parametrize(
    "offset,value", [(8, 1), (12, 0x990100C1), (24, 0x50440088), (40, 0x181), (44, 1)]
)
def test_unknown_header_keeps_range_without_reinterpreting_parameters(offset, value):
    b = bytearray(native())
    struct.pack_into("<I", b, 4 + offset, value)
    doc, index = read(b)
    e = index.parts[1].entities[0]
    assert e.primitive is None and e.geometry_status == "unsupported"
    assert e.appearance.color_index is None
    assert doc.source_bytes(e.byte_range) == bytes(b[4:])


def metadata(length=440, count=2, *, unknown=False):
    b = bytearray(length + 4)
    struct.pack_into("<5I", b, 0, 0x21000000, length, count, 0, 0xC9500088)
    # A plausible part signature inside opaque metadata must never be read.
    b[24:32] = part(B, parent=A)[:8]
    at = 160
    for i in range(count):
        size = 16 if i < count - 1 else len(b) - at
        struct.pack_into("<I", b, at, (0x7D if unknown else 0x79 + i % 4) << 24 | size)
        at += size
    return b


@pytest.mark.parametrize("length", [440, 504])
def test_qualified_metadata_is_bounded_and_not_scanned(length):
    doc, index = read(metadata(length) + native())
    assert len(index.parts) == 2 and len(index.parts[1].entities) == 1
    assert index.status.index == "complete"
    opaque = next(r for r in index.opaque_ranges if r.reason == "entity_metadata")
    assert doc.source_bytes(opaque.byte_range) == metadata(length)
    with pytest.raises(icadkit.LimitExceededError):
        doc.read_parts(limits=icadkit.PartLimits(max_entities=2))


def test_unknown_and_truncated_metadata_do_not_resynchronize():
    for b, status in (
        (metadata(444, unknown=True) + native(), "partial"),
        (metadata()[:100] + native(), "invalid"),
    ):
        _, index = read(b)
        assert index.status.index == status
        assert not index.parts[1].entities


def test_multiple_entities_preserve_independent_colors_and_visibility():
    _, index = read(
        native(color=2) + native("cylinder", color=18, visible=False, first=False)
    )
    entities = index.parts[1].entities
    assert len(entities) == 2 and entities[0].entity_id != entities[1].entity_id
    assert [e.appearance.color_index for e in entities] == [2, 18]
    assert [e.appearance.visible for e in entities] == [True, False]


def test_parent_entity_appearance_does_not_create_inherited_child_values():
    index = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A),
                native(color=2, visible=False),
                part(A, parent=ROOT),
                native(color=5, visible=True),
            ]
        )
    ).read_parts()
    assert index.parts[1].entities[0].appearance.color_index == 5
    assert index.parts[1].entities[0].appearance.visible is True


def test_every_aligned_primitive_truncation_fails_closed_without_losing_owner():
    b = native()
    for n in range(4, len(b), 4):
        _, index = read(b[:n])
        assert len(index.parts) == 2
        assert index.status.index != "complete"
        assert index.parts[1].native_geometry_status != "complete"


@pytest.mark.parametrize(
    "record,code",
    [
        (native(), 0),
        (native("sphere", parameters=(9, 0, 0)), 3),
        (native(parameters=(-1, -3, -5, 10, 12)), 1),
    ],
)
def test_cli_native_requirement_is_explicit(record, code, tmp_path, capsys):
    file = tmp_path / "native.icd"
    file.write_bytes(view_parts([part(ROOT, root=True), record]))
    assert main(["parts", str(file), "--include-root", "--json"]) == 0
    capsys.readouterr()
    assert (
        main(["parts", str(file), "--require-native", "--include-root", "--json"])
        == code
    )
    result = json.loads(capsys.readouterr().out)
    assert len(result["parts"][0]["entities"]) == 1


def test_full_sphere_radius_centre_and_provenance():
    doc, index = read(native("sphere", origin=(17, -23, 9), parameters=(7, 7, math.pi)))
    sphere = index.parts[1].entities[0].primitive
    assert sphere.kind == "sphere" and sphere.radius == 7
    assert sphere.height is None and sphere.box_dimensions is None
    assert tuple(row[3] for row in sphere.world_transform[:3]) == (17, -23, 9)
    assert sphere.raw_bytes == doc.source_bytes(sphere.byte_range)
    assert index.status.native_geometry == "complete"


@pytest.mark.parametrize(
    "parameters,status",
    [
        ((7, 0, 0), "unsupported"),
        ((7, 7, math.pi / 2), "unsupported"),
        ((0, 0, math.pi), "invalid"),
        ((-7, -7, math.pi), "invalid"),
        ((7, math.nan, math.pi), "invalid"),
    ],
)
def test_unknown_sphere_extents_and_invalid_dimensions_are_not_full_spheres(
    parameters, status
):
    _, index = read(native("sphere", parameters=parameters))
    entity = index.parts[1].entities[0]
    assert entity.primitive is None and entity.geometry_status == status
    assert entity.appearance.status == "complete"
