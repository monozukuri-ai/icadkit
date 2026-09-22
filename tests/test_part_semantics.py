"""Synthetic corruption and composition checks for the iCAD-qualified part profile."""

import json
import math
import struct

import pytest
from part_fixtures import ROOT, A, B, extra_info, part, view_parts

import icadkit
from icadkit.cli import main


def read(*records):
    return icadkit.read(view_parts(records)).read_parts()


def multiply(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4)
    )


def test_world_frames_are_not_composed_twice_and_local_frames_use_inverse_parent():
    index = read(
        part(ROOT, root=True, child=A),
        part(
            A, parent=ROOT, child=B, position=(10, 20, 30), axes=(0, 0, 1, 0, 1, 0)
        ),  # parent Z90
        part(B, parent=A, position=(7, 22, 34), axes=(1, 0, 0, 0, 1, 0)),
    )
    root, parent, child = index.parts
    expected_world = ((0, 0, 1, 7), (1, 0, 0, 22), (0, 1, 0, 34), (0, 0, 0, 1))
    expected_local = ((1, 0, 0, 2), (0, 0, -1, 3), (0, 1, 0, 4), (0, 0, 0, 1))
    assert child.placement.world_transform == expected_world
    assert child.placement.local_transform == expected_local
    assert multiply(parent.placement.world_transform, expected_local) == expected_world
    assert multiply(parent.placement.world_transform, expected_world) != expected_world
    assert root.placement.world_transform == root.placement.local_transform
    assert index.source_length_unit == "mm"
    assert index.length_unit_source == "qualified_part_profile"


def test_original_opaque_transform_block_is_not_used_as_part_coordinate_frame():
    record = bytearray(part(A, parent=ROOT, position=(7, 8, 9)))
    struct.pack_into("<9d", record, 108, *range(9))
    doc = icadkit.read(view_parts([part(ROOT, root=True, child=A), record]))
    p = doc.read_parts().parts[1]
    assert p.placement.values == tuple(range(9))
    assert tuple(r[3] for r in p.placement.world_transform[:3]) == (7, 8, 9)
    assert (
        doc.source_bytes(p.placement.coordinate_byte_range)
        == p.placement.raw_coordinate_bytes
    )


@pytest.mark.parametrize(
    "axes",
    [
        (0, 0, 2, 1, 0, 0),
        (0, 0, 1, 0, 0, 1),
        (0, 0, 1, 0, 0, 0),
        (0, 0, math.inf, 1, 0, 0),
    ],
)
def test_invalid_axes_do_not_get_normalized(axes):
    index = read(part(ROOT, root=True, child=A), part(A, parent=ROOT, axes=axes))
    p = index.parts[1]
    assert p.placement.status == "invalid"
    assert p.placement.world_transform is p.placement.local_transform is None
    assert index.status.placements == "invalid"
    assert len(index.to_rows()) == 1
    json.dumps(index.to_rows(), allow_nan=False)


def test_relative_transform_overflow_preserves_world_and_reports_invalid():
    index = read(
        part(ROOT, root=True, child=A, position=(1e308, 0, 0)),
        part(A, parent=ROOT, position=(-1e308, 0, 0)),
    )
    p = index.parts[1]
    assert p.placement.world_transform is not None
    assert p.placement.local_transform is None
    assert p.placement.status == "invalid"
    assert any(d.code == "parts.local_nonfinite" for d in index.diagnostics)


def test_broken_graph_keeps_world_but_never_guesses_relative_frames():
    index = read(part(ROOT, root=True), part(A, parent=B))
    assert index.status.hierarchy == "invalid"
    assert all(p.placement.local_transform is None for p in index.parts)
    assert all(p.placement.world_transform is not None for p in index.parts)


def test_mirror_keeps_rows_and_blocks_unqualified_relative_transform():
    index = read(
        part(ROOT, root=True, child=A),
        part(A, parent=ROOT, child=B, flags=8),
        part(B, parent=A),
    )
    assert index.parts[1].is_mirror is True
    assert index.parts[1].placement.world_transform is None
    assert index.parts[2].placement.world_transform is not None
    assert index.parts[2].placement.local_transform is None
    assert len(index.to_rows()) == 2
    assert index.status.placements == "partial"


def test_externalized_model_root_uses_a_prefix_and_still_requires_valid_graph():
    index = read(part(A, root=True, child=B), part(B, parent=A))
    assert index.status.index == index.status.hierarchy == "complete"
    assert index.parts[0].is_root and not index.parts[0].is_external
    invalid = read(part(A, root=True, parent=B), part(B, parent=A))
    assert invalid.status.hierarchy == "invalid"


def test_extended_information_is_ordered_owned_utf16_text_not_inherited_or_typed():
    value = "材質=鋼板;数値=12.5mm\n空欄=\x00埋込文字 "
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A),
                extra_info("親の属性"),
                part(A, parent=ROOT, child=B),
                extra_info(value),
                part(B, parent=A),
            ]
        )
    )
    index = doc.read_parts()
    root, a, b = index.parts
    assert root.extra_info == "親の属性" and a.extra_info == value
    assert b.extra_info is None and len(b.properties) == 1
    prop = a.properties[1]
    assert prop.owner_id == a.part_id and prop.owner_kind == "occurrence"
    assert prop.value_type == "text" and prop.encoding == "utf-16-le"
    assert prop.origin == "stored"
    assert doc.source_bytes(prop.byte_range) == prop.raw_value
    assert index.status.stored_attributes == "complete"
    assert index.status.attributes == "partial"


@pytest.mark.parametrize(
    "value,expected", [("", ""), ("   ", "   "), (b"\x00\xd8\x00\x00", None)]
)
def test_empty_versus_undecodable_extended_information(value, expected):
    index = read(part(ROOT, root=True), extra_info(value))
    p = index.parts[0]
    assert len(p.properties) == 2 and p.extra_info == expected
    assert index.status.stored_attributes == (
        "partial" if expected is None else "complete"
    )


def test_multiple_entities_in_one_group_preserve_properties_and_skip_fake_parts():
    geometry = struct.pack("<I", 360) + part(B, parent=A)
    index = read(
        part(ROOT, root=True, child=A),
        extra_info("root"),
        geometry,
        part(A, parent=ROOT),
        extra_info("first"),
        extra_info("second", first=False),
    )
    assert len(index.parts) == 2 and index.status.index == "complete"
    assert index.parts[0].extra_info == "root"
    assert [p.value for p in index.parts[1].properties[1:]] == ["first", "second"]
    assert index.parts[1].extra_info is None
    assert index.status.stored_attributes == "partial"
    assert any(d.code == "parts.multiple_extra_info" for d in index.diagnostics)


def test_unknown_attribute_layout_and_truncated_entity_are_not_silently_read():
    attr = bytearray(extra_info("value"))
    struct.pack_into("<I", attr, 4 + 28, 0)
    index = read(part(ROOT, root=True), attr)
    assert index.status.index == index.status.stored_attributes == "partial"
    assert len(index.parts[0].properties) == 1
    assert any(r.reason == "unknown_attribute" for r in index.opaque_ranges)
    truncated = struct.pack("<2I", 0x30010000, 10000)
    index = read(part(ROOT, root=True), truncated)
    assert index.status.index == "invalid"


def test_property_byte_limit_is_checked_before_decoding():
    doc = icadkit.read(view_parts([part(ROOT, root=True), extra_info("日本語")]))
    with pytest.raises(icadkit.LimitExceededError) as error:
        doc.read_parts(limits=icadkit.PartLimits(max_property_bytes=4))
    assert error.value.diagnostic.code == "limit.part_property_bytes"
    assert (
        doc.read_parts(limits=icadkit.PartLimits(max_property_bytes=8))
        .parts[0]
        .extra_info
        == "日本語"
    )


def external_pair(reference="missing-model", flags=0x50):
    return [
        part(ROOT, root=True, child=A),
        part(A, parent=ROOT, next_id=B, flags=flags, reference=reference, name="first"),
        part(
            B, parent=ROOT, previous=A, flags=flags, reference=reference, name="second"
        ),
    ]


def test_external_reference_groups_saved_name_without_loading_files(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    index = read(*external_pair())
    assert index.status.index == "complete"
    assert (
        index.status.hierarchy
        == index.status.definitions
        == index.status.references
        == "partial"
    )
    a, b = index.parts[1:]
    assert a.definition_id == b.definition_id
    definition = index.definition(a.definition_id)
    assert definition.occurrence_ids == (a.part_id, b.part_id)
    assert definition.kind == "external" and definition.status == "partial"
    assert a.external_reference.name == "missing-model"
    assert (
        a.external_reference.path is None
        and a.external_reference.status == "not_loaded"
    )
    assert index.children(a.part_id) == ()
    assert a.name != b.name
    with pytest.raises(KeyError):
        index.definition("not-present")


def test_same_internal_names_are_independent_definitions():
    records = external_pair()
    records[1] = part(A, parent=ROOT, next_id=B, name="same")
    records[2] = part(B, parent=ROOT, previous=A, name="same")
    index = read(*records)
    assert len(index.definitions) == 2
    assert index.parts[1].definition_id != index.parts[2].definition_id


@pytest.mark.parametrize("reference", ["", "bad"])
def test_unknown_external_reference_is_retained_without_grouping(reference):
    records = external_pair(reference)
    if reference:
        for i in (1, 2):
            record = bytearray(records[i])
            record[276:316] = b"\x81".ljust(40, b" ")
            records[i] = record
    index = read(*records)
    assert all(p.definition_id is None for p in index.parts[1:])
    assert all(p.external_reference is not None for p in index.parts[1:])
    assert any(d.code == "parts.reference_name" for d in index.diagnostics)


def test_unknown_part_kind_keeps_raw_record_without_inventing_semantics():
    index = read(part(ROOT, root=True, child=A), part(A, parent=ROOT, flags=0x100))
    p = index.parts[1]
    assert p.is_external is p.is_mirror is p.definition_id is None
    assert p.placement.world_transform is None and index.status.index == "partial"


@pytest.mark.parametrize(
    "records",
    [
        external_pair(),
        external_pair(flags=0x58),
        [part(ROOT, root=True, child=A), part(A, parent=ROOT, flags=8)],
    ],
)
def test_cli_reports_unresolved_semantics_with_rows(records, tmp_path, capsys):
    path = tmp_path / "partial.icd"
    path.write_bytes(view_parts(records))
    assert main(["parts", str(path), "--json"]) == 3
    data = json.loads(capsys.readouterr().out)
    assert data["part_count"] == len(records) - 1
    assert data["definition_count"] == len(data["definitions"])
    assert data["source_length_unit"] == "mm"
