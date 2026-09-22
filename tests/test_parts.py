import json
import math
import struct
from dataclasses import FrozenInstanceError

import pytest
from part_fixtures import ROOT, A, B, nested, part, view_parts

import icadkit
from icadkit.cli import main


def test_inventory_keeps_names_properties_geometry_unknowns_and_provenance():
    source = nested()
    doc = icadkit.read(source)
    index = doc.read_parts()
    assert not doc.resources
    assert (
        index.status.index == index.status.hierarchy == index.status.text == "complete"
    )
    assert index.status.model == index.status.attributes == "partial"
    assert index.status.definitions == index.status.units == "complete"
    assert index.status.stored_attributes == "complete"
    assert index.source_sha256 == doc.source_sha256
    root, a, b = index.parts
    assert root.is_root and root.parent_id is None
    assert a.name == b.name == "同名部品" and a.part_id != b.part_id
    assert index.children(root.part_id) == (a,)
    assert index.children(a.part_id) == (b,)
    assert index.part(b.part_id) == b
    assert a.comment == "鋼板" and b.comment == ""
    assert a.properties[0].owner_id == a.part_id
    assert doc.source_bytes(a.properties[0].byte_range) == a.properties[0].raw_value
    assert b.placement.values[:3] == (100, -20, 30)
    assert doc.source_bytes(b.placement.byte_range) == b.placement.raw_bytes
    assert b.placement.local_transform == b.placement.world_transform
    assert tuple(row[3] for row in b.placement.world_transform[:3]) == (100, -20, 30)
    assert index.status.placements == "complete"
    assert list(index.walk()) == [root, a, b]
    assert list(index.walk(include_root=False)) == [a, b]
    rows = index.to_rows()
    assert [r["name"] for r in rows] == ["同名部品", "同名部品"]
    assert all(r["geometry_status"] == "not_checked" for r in rows)
    assert a.definition_id != b.definition_id
    assert len(index.definitions) == 2
    assert all(r["definition_id"] and r["resource_ids"] is None for r in rows)
    assert len(json.loads(json.dumps(rows, allow_nan=False))) == 2
    assert len(index.to_rows(include_root=True)) == 3
    with pytest.raises(KeyError):
        index.children("invented")
    with pytest.raises(FrozenInstanceError):
        a.name = "changed"


def test_empty_document_has_root_but_no_part_rows():
    index = icadkit.read(view_parts([part(ROOT, root=True)])).read_parts()
    assert index.status.hierarchy == "complete" and index.to_rows() == []


def test_missing_root_is_not_a_successful_empty_document():
    index = icadkit.read(view_parts([])).read_parts()
    assert index.status.index == index.status.hierarchy == "invalid"
    assert any(d.code == "parts.no_root" for d in index.diagnostics)


@pytest.mark.parametrize("offset,value", [(12, b"\0\x07\0\x03"), (496 + 44, b"\0" * 4)])
def test_unqualified_profiles_do_not_scan_for_plausible_parts(offset, value):
    data = bytearray(nested())
    data[offset : offset + 4] = value
    index = icadkit.read(bytes(data)).read_parts()
    assert not index.parts and index.status.index == "unsupported"
    assert index.opaque_ranges and index.diagnostics


def test_unknown_entity_stops_before_embedded_part_signature():
    payload = part(A, parent=ROOT)
    unknown = struct.pack("<2I", 0x99000000, len(payload) + 4) + payload
    index = icadkit.read(view_parts([part(ROOT, root=True), unknown])).read_parts()
    assert len(index.parts) == 1 and index.status.index == "partial"
    assert any(d.code == "parts.entity" for d in index.diagnostics)
    assert any(r.reason == "unparsed_entities" for r in index.opaque_ranges)


def test_geometry_entity_is_opaque_and_does_not_hide_following_part():
    geometry = struct.pack("<2I", 0x30010000, 4 + 356) + part(999)
    index = icadkit.read(
        view_parts([part(ROOT, root=True, child=A), geometry, part(A, parent=ROOT)])
    ).read_parts()
    assert [p.source_id for p in index.parts] == [ROOT, A]
    assert index.status.index == "complete"
    assert any(r.reason == "geometry_entity" for r in index.opaque_ranges)


@pytest.mark.parametrize("length", [0, 3, 355, 0xFFFFFFFC])
def test_corrupt_entity_lengths_are_local_diagnostics(length):
    bad = bytearray(part(A, parent=ROOT))
    struct.pack_into("<I", bad, 4, length)
    index = icadkit.read(view_parts([part(ROOT, root=True), bad])).read_parts()
    assert len(index.parts) == 1
    assert index.status.index == "invalid"
    assert any(d.code == "parts.length" for d in index.diagnostics)


def test_unqualified_part_size_and_missing_end():
    unknown = struct.pack("<2I", 0x61000003, 8) + b"\0" * 4
    index = icadkit.read(view_parts([part(ROOT, root=True), unknown])).read_parts()
    assert index.status.index == "partial"
    assert any(d.code == "parts.record_layout" for d in index.diagnostics)
    index = icadkit.read(view_parts([part(ROOT, root=True)], end=False)).read_parts()
    assert index.status.index == "invalid"
    index = icadkit.read(
        view_parts([part(ROOT, root=True)], after=b"junk")
    ).read_parts()
    assert index.status.index == "partial"


@pytest.mark.parametrize(
    "records,code",
    [
        (
            [
                part(ROOT, root=True, child=A),
                part(A, parent=ROOT),
                part(A, parent=ROOT),
            ],
            "parts.duplicate_id",
        ),
        ([part(ROOT, root=True), part(A, parent=99)], "parts.parent"),
        ([part(ROOT, root=True, child=A)], "parts.child"),
        (
            [part(ROOT, root=True, child=A), part(A, parent=ROOT, next_id=A)],
            "parts.child_chain",
        ),
        (
            [
                part(ROOT, root=True),
                part(A, parent=B, child=B),
                part(B, parent=A, child=A),
            ],
            "parts.cycle",
        ),
        ([part(ROOT, root=True), part(A, parent=ROOT)], "parts.roots"),
    ],
)
def test_invalid_hierarchy_retains_all_rows_and_bounded_traversal(records, code):
    index = icadkit.read(view_parts(records)).read_parts()
    assert index.status.hierarchy == "invalid"
    assert any(d.code == code for d in index.diagnostics)
    assert len(list(index.walk())) == len(records)
    assert len(index.to_rows(include_root=True)) == len(records)


def test_invalid_text_and_nonfinite_placement_are_not_silently_repaired():
    bad = bytearray(part(A, parent=ROOT))
    bad[60:108] = b"\x81".ljust(48, b" ")
    struct.pack_into("<d", bad, 108, math.nan)
    doc = icadkit.read(view_parts([part(ROOT, root=True, child=A), bad]))
    index = doc.read_parts()
    p = index.parts[1]
    assert p.comment is None and p.properties[0].raw_value[0] == 0x81
    assert index.status.text == "partial"
    assert p.placement.status == "invalid" and p.placement.values[0] is None
    assert math.isnan(struct.unpack_from("<d", p.placement.raw_bytes)[0])
    json.dumps(index.to_rows(), allow_nan=False)


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"max_parts": 2}, "limit.parts"),
        ({"max_entities": 2}, "limit.part_entities"),
        ({"max_depth": 2}, "limit.part_depth"),
    ],
)
def test_limits_fail_without_truncating_into_success(kwargs, code):
    doc = icadkit.read(nested())
    with pytest.raises(icadkit.LimitExceededError) as error:
        doc.read_parts(limits=icadkit.PartLimits(**kwargs))
    assert error.value.diagnostic.code == code
    assert len(doc.read_parts().parts) == 3


def test_limits_validate_type_and_depth_boundary():
    for value in (True, 1.1, "1"):
        with pytest.raises(TypeError):
            icadkit.PartLimits(max_parts=value)
    for value in (0, -1, 2**31):
        with pytest.raises(ValueError):
            icadkit.PartLimits(max_parts=value)
    with pytest.raises(TypeError):
        icadkit.read(nested()).read_parts(limits={})
    assert (
        icadkit.read(nested())
        .read_parts(limits=icadkit.PartLimits(max_depth=3))
        .status.hierarchy
        == "complete"
    )


def test_parts_cli_json_and_exit_contract(tmp_path, capsys):
    path = tmp_path / "部品.icd"
    path.write_bytes(nested())
    assert main(["parts", str(path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["part_count"] == len(result["parts"]) == 2
    assert result["status"]["model"] == "partial"
    assert main(["parts", str(path), "--max-parts", "1", "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "limit.parts"
    path.write_bytes(view_parts([part(ROOT, root=True), part(A, parent=99)]))
    assert main(["parts", str(path), "--json"]) == 1
    capsys.readouterr()
    data = bytearray(nested())
    data[12:16] = b"\0" * 4
    path.write_bytes(data)
    assert main(["parts", str(path), "--json"]) == 3
    capsys.readouterr()
    assert main(["parts", str(tmp_path / "missing"), "--json"]) == 1
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        main(["parts", str(path), "--max-parts", str(2**32)])
    assert e.value.code == 2
