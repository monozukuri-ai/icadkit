"""Synthetic V7L7 boundary/corruption checks, independent of private CAD inputs."""

import json
import math
import struct

import pytest
from part_fixtures import ROOT, A, B, extra_info, part, view_parts
from test_native import native

import icadkit
from icadkit.cli import main
from icadkit.viewer import write_native_viewer


def v7part(source_id, **kwargs):
    return part(source_id, profile="v7l7", **kwargs)


def document(*records):
    return icadkit.read(view_parts(records, profile="v7l7"))


def metadata(length=392, count=2):
    data = bytearray(length + 4)
    struct.pack_into("<5I", data, 0, 0x21000000, length, count, 0, 0xC9500088)
    # A complete, misleading part record inside metadata must never be indexed.
    if length >= 376:
        data[20:376] = v7part(B, parent=ROOT, name="fake")
    return bytes(data)


def assembly():
    return document(
        v7part(ROOT, root=True, child=A, position=(13, -17, 19)),
        extra_info("root text"),
        v7part(A, parent=ROOT, child=B, name="同名部品", comment="鋼板"),
        metadata(),
        native(),
        extra_info("保存値=12.5mm", first=False),
        v7part(B, parent=A, name="同名部品", position=(100, -20, 30)),
    )


def test_v7_inventory_metadata_hierarchy_and_raw_provenance():
    doc = assembly()
    index = doc.read_parts()
    assert index.status.index == index.status.hierarchy == "complete"
    assert index.status.text == index.status.stored_attributes == "complete"
    root, a, b = index.parts
    assert list(index.walk(include_root=False)) == [a, b]
    assert index.children(a.part_id) == (b,)
    assert root.extra_info == "root text"
    assert a.name == b.name == "同名部品" and a.definition_id != b.definition_id
    assert a.comment == "鋼板" and a.extra_info == "保存値=12.5mm"
    assert b.extra_info is None
    for p in index.parts:
        for prop in p.properties:
            assert doc.source_bytes(prop.byte_range) == prop.raw_value
        assert doc.source_bytes(p.placement.coordinate_byte_range) == (
            p.placement.raw_coordinate_bytes
        )
        assert p.placement.world_transform is not None
        assert p.placement.local_transform is not None
        assert p.placement.status == "complete"
    assert root.placement.coordinate_values[:3] == (13, -17, 19)
    assert index.source_length_unit == "mm"
    assert index.length_unit_source == "qualified_part_profile"
    assert index.status.units == index.status.placements == "complete"
    rows = json.loads(json.dumps(index.to_rows(), allow_nan=False))
    assert len(rows) == 2 and rows[0]["source_length_unit"] == "mm"


def test_v7_standalone_primitive_and_saved_appearance():
    doc = assembly()
    index = doc.read_parts()
    entity = index.parts[1].entities[0]
    assert entity.raw_type == 75 and entity.owner_id == index.parts[1].part_id
    assert entity.primitive.box_dimensions == (13, 17, 23)
    assert entity.source_id == 0x80000003
    assert entity.appearance.visible and entity.appearance.color_index == 1
    assert entity.appearance.layer == 1 and entity.is_mirror is False
    assert entity.geometry_status == entity.appearance.status == "complete"
    assert doc.source_bytes(entity.byte_range) == native()[4:]
    assert index.status.native_geometry == index.status.appearance == "complete"


@pytest.mark.parametrize("length,count", [(176, 1), (392, 2), (704, 2)])
def test_v7_metadata_boundaries_do_not_scan_payloads(length, count):
    raw = metadata(length, count)
    doc = document(v7part(ROOT, root=True, child=A), raw, v7part(A, parent=ROOT))
    index = doc.read_parts()
    assert [p.source_id for p in index.parts] == [ROOT, A]
    assert index.status.index == index.status.hierarchy == "complete"
    opaque = next(r for r in index.opaque_ranges if r.reason == "entity_metadata")
    assert doc.source_bytes(opaque.byte_range) == raw
    with pytest.raises(icadkit.LimitExceededError, match="max_entities"):
        doc.read_parts(limits=icadkit.PartLimits(max_entities=2))


@pytest.mark.parametrize("offset,value", [(4, 0), (4, 3), (4, 0xFFFFFFFC)])
def test_v7_invalid_metadata_lengths_stop_before_later_records(offset, value):
    raw = bytearray(metadata())
    struct.pack_into("<I", raw, offset, value)
    index = document(v7part(ROOT, root=True), raw, v7part(A, parent=ROOT)).read_parts()
    assert len(index.parts) == 1 and index.status.index == "invalid"
    assert any(d.code == "parts.metadata_length" for d in index.diagnostics)


@pytest.mark.parametrize(
    "raw,code,status",
    [
        (metadata(180, 1), "parts.metadata_layout", "partial"),
        (metadata(184, 2), "parts.metadata_layout", "partial"),
        (metadata()[:16], "parts.metadata_length", "invalid"),
        (metadata()[:100], "parts.metadata_length", "invalid"),
    ],
)
def test_v7_unknown_or_truncated_metadata_retains_the_unparsed_tail(raw, code, status):
    # The view terminator is not part of the metadata's allowed byte range.
    doc = document(v7part(ROOT, root=True), raw)
    index = doc.read_parts()
    assert len(index.parts) == 1 and index.status.index == status
    assert any(d.code == code for d in index.diagnostics)
    assert any(r.reason == "unparsed_entities" for r in index.opaque_ranges)


@pytest.mark.parametrize("profile,other", [("v7l7", "v8l3"), ("v8l3", "v7l7")])
def test_version_labels_cannot_enable_another_part_record_layout(profile, other):
    source = view_parts([part(ROOT, root=True, profile=other)], profile=profile)
    index = icadkit.read(source).read_parts()
    assert not index.parts and index.status.index == "unsupported"
    assert index.diagnostics[0].code == "parts.record_profile"


def test_v7_entities_do_not_resynchronize_on_an_embedded_part():
    entity = struct.pack("<2I", 0x30010000, 360) + v7part(B, parent=ROOT)
    index = document(
        v7part(ROOT, root=True, child=A), entity, v7part(A, parent=ROOT)
    ).read_parts()
    assert [p.source_id for p in index.parts] == [ROOT, A]
    assert len(index.parts[0].entities) == 1


def test_v7_external_references_and_mirrors_preserve_saved_information():
    index = document(
        v7part(ROOT, root=True, child=A),
        v7part(A, parent=ROOT, next_id=B, flags=0x50, reference="target"),
        v7part(B, parent=ROOT, previous=A, flags=0x58, reference="target"),
    ).read_parts()
    a, b = index.parts[1:]
    assert a.definition_id == b.definition_id
    assert a.is_external and b.is_external and b.is_mirror
    assert a.external_reference.name == "target"
    assert a.external_reference.status == "not_loaded"
    assert index.status.hierarchy == index.status.references == "partial"


def test_v7_corruption_and_limits_are_not_hidden_by_unsupported_geometry():
    bad = bytearray(v7part(A, parent=ROOT))
    struct.pack_into("<d", bad, 180, math.nan)
    index = document(v7part(ROOT, root=True, child=A), bad).read_parts()
    assert index.parts[1].placement.status == "invalid"
    assert index.parts[1].placement.coordinate_values[0] is None
    assert any(d.code == "parts.placement_nonfinite" for d in index.diagnostics)
    duplicate = document(
        v7part(ROOT, root=True, child=A), v7part(A, parent=ROOT), v7part(A, parent=ROOT)
    ).read_parts()
    assert duplicate.status.hierarchy == "invalid" and len(duplicate.parts) == 3
    with pytest.raises(icadkit.LimitExceededError):
        assembly().read_parts(limits=icadkit.PartLimits(max_property_bytes=4))


def test_v7_cli_returns_qualified_rows(tmp_path, capsys):
    path = tmp_path / "old.icd"
    path.write_bytes(view_parts([v7part(ROOT, root=True)], profile="v7l7"))
    assert main(["parts", str(path), "--include-root", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"]["index"] == "complete" and len(result["parts"]) == 1
    assert result["source_length_unit"] == "mm"
    assert "error" not in result


def test_v7_viewer_publishes_qualified_primitive(tmp_path):
    result = write_native_viewer(assembly(), tmp_path / "view")
    scene = json.loads((tmp_path / "view/scene.json").read_text())
    assert result.rendered_entities == 1 and result.omitted_entities == 0
    assert scene["scope"] == "qualified_native_primitives" and scene["meshes"]
    assert scene["length_unit"] == "mm" and scene["coordinate_system"] == "3DGLOBAL"
    assert len(scene["parts"]) == 3 and scene["model_status"] == "partial"


def test_v7_rotated_root_part_and_native_frames_use_one_root_normalization():
    doc = document(
        v7part(
            ROOT, root=True, child=A, position=(100, 200, 300), axes=(0, 0, 1, 0, 1, 0)
        ),
        v7part(A, parent=ROOT, child=B, position=(80, 230, 340)),
        native(
            "cylinder",
            origin=(80, 230, 340),
            axes=(0, 0, 1, 1, 0, 0),
            color=18,
            visible=False,
            layer=7,
        ),
        v7part(B, parent=A, position=(70, 235, 345)),
    )
    root, a, b = doc.read_parts().parts
    assert root.placement.world_transform == (
        (1, 0, 0, 0),
        (0, 1, 0, 0),
        (0, 0, 1, 0),
        (0, 0, 0, 1),
    )
    expected = ((0, 1, 0, 30), (-1, 0, 0, 20), (0, 0, 1, 40), (0, 0, 0, 1))
    assert a.placement.world_transform == expected
    assert tuple(row[3] for row in b.placement.local_transform[:3]) == (-10, 5, 5)
    e = a.entities[0]
    assert e.primitive.world_transform == expected  # Do not apply the part frame again.
    assert e.primitive.radius == 7 and e.primitive.height == 29
    assert (e.appearance.color_index, e.appearance.visible, e.appearance.layer) == (
        18,
        False,
        7,
    )
    assert doc.source_bytes(e.primitive.byte_range) == e.primitive.raw_bytes


def opaque_entity(*, first=False):
    # A CSG-operation-like entity among primitive-shaped operands.
    b = bytearray(48)
    struct.pack_into("<4I", b, 0, 48, 1, 0, 85 << 24 | 0x10081)
    return (struct.pack("<I", 0x30010000) if first else b"") + b


@pytest.mark.parametrize("before", [True, False])
def test_v7_unknown_owner_entity_never_exposes_primitive_operands(before):
    records = (
        [opaque_entity(first=True), native(first=False)]
        if before
        else [native(), opaque_entity()]
    )
    ix = document(
        v7part(ROOT, root=True, child=A), v7part(A, parent=ROOT), *records
    ).read_parts()
    assert len(ix.parts[1].entities) == 2
    for e in ix.parts[1].entities:
        assert e.primitive is None and e.geometry_status == "unsupported"
        assert e.appearance.status == "unsupported" and e.source_id is None
        assert e.diagnostics[0].code == "native.v7_owner"


def test_v7_unknown_owner_does_not_hide_an_independent_supported_part(tmp_path):
    doc = document(
        v7part(ROOT, root=True, child=A),
        v7part(A, parent=ROOT, next_id=B),
        native(),
        opaque_entity(),
        v7part(B, parent=ROOT, previous=A),
        native(),
    )
    result = write_native_viewer(doc, tmp_path / "mixed")
    assert result.rendered_entities == 1 and result.omitted_entities == 2
    assert doc.read_parts().status.native_geometry == "partial"


@pytest.mark.parametrize(
    "case", ["root_nonfinite", "root_axes", "partial", "duplicate"]
)
def test_v7_unqualified_root_or_index_keeps_geometry_unavailable(case, tmp_path):
    root = bytearray(v7part(ROOT, root=True, child=A))
    if case == "root_nonfinite":
        struct.pack_into("<d", root, 180, math.nan)
    elif case == "root_axes":
        struct.pack_into("<d", root, 228, 2.0)
    records = [root, v7part(A, parent=ROOT), native()]
    if case == "partial":
        records.extend([v7part(B, parent=ROOT), metadata(180, 1)])
    elif case == "duplicate":
        records.append(v7part(A, parent=ROOT))
    doc = document(*records)
    ix = doc.read_parts()
    assert ix.source_length_unit is None
    assert all(p.placement.world_transform is None for p in ix.parts)
    assert all(e.primitive is None for p in ix.parts for e in p.entities)
    if case == "partial":
        write_native_viewer(doc, tmp_path / "view")
        scene = json.loads((tmp_path / "view/scene.json").read_text())
        assert scene["scope"] == "native_part_inventory" and not scene["meshes"]
        assert scene["length_unit"] is None


def test_v7_mirrored_ancestor_does_not_supply_descendant_geometry():
    ix = document(
        v7part(ROOT, root=True, child=A),
        v7part(A, parent=ROOT, child=B, flags=8),
        v7part(B, parent=A),
        native(),
    ).read_parts()
    assert ix.parts[1].placement.status == ix.parts[2].placement.status == "unsupported"
    assert ix.parts[2].entities[0].primitive is None
    assert any(d.code == "parts.placement_ancestor" for d in ix.diagnostics)


def test_v7_root_normalization_overflow_is_invalid():
    ix = document(
        v7part(ROOT, root=True, child=A, position=(-1e308, 0, 0)),
        v7part(A, parent=ROOT, position=(1e308, 0, 0)),
        native(origin=(1e308, 0, 0)),
    ).read_parts()
    assert ix.parts[1].placement.status == "invalid"
    assert ix.parts[1].placement.world_transform is None
    assert ix.parts[1].entities[0].geometry_status == "invalid"
    assert ix.parts[1].entities[0].primitive is None


def test_v7_invalid_parameters_remain_invalid_in_a_recognized_owner():
    ix = document(
        v7part(ROOT, root=True), native(parameters=(-1, -3, -5, 10, 12))
    ).read_parts()
    assert ix.parts[0].entities[0].geometry_status == "invalid"
    assert ix.parts[0].entities[0].diagnostics[0].code == "native.parameters"
