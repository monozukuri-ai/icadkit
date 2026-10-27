"""View placement records, saved version labels and the qualified-version table."""

import json
import struct

import pytest
from drawing_fixtures import drawing, placement, sheet

import icadkit
from icadkit.cli import main


def _patch(data, at, fmt, *values):
    b = bytearray(data)
    struct.pack_into(fmt, b, at, *values)
    return bytes(b)


def test_sheet_placement_is_decoded_and_attached():
    doc = icadkit.read(sheet())
    vi = doc.read_views()
    assert vi.status == "complete"
    assert len(vi.placements) == 1
    (record,) = vi.placements
    assert record.name == "!XY" and record.raw_name == b"!XY     "
    assert record.origin == (0.0, 0.0) and record.scale == 1.0
    assert record.status == "complete" and record.diagnostics == ()
    assert record.coordinate_space == "sheet" and record.length_unit == "mm"
    assert record.byte_range.length == 264
    assert doc.source_bytes(record.byte_range)[136:144] == b"!XY     "
    by_kind = {v.kind: v for v in vi.views}
    assert by_kind["2d_view"].placement is record
    assert by_kind["2d_global"].placement is None
    assert not [d for d in vi.diagnostics if d.code.startswith("views.placement")]


def test_origin_and_scale_are_read_in_the_file_byte_order():
    data = sheet()
    at = icadkit.read(data).read_views().placements[0].byte_range.start
    data = _patch(data, at + 40, "<2d", 210.5, -42.25)
    data = _patch(data, at + 64, "<d", 0.5)
    record = icadkit.read(data).read_views().placements[0]
    assert record.origin == (210.5, -42.25) and record.scale == 0.5


def test_big_endian_sheet_placement():
    version = b"\0\x05\0\x03"
    stream = placement("!XY", origin=(12.0, 34.0), scale=2.0, order="big", first=True)
    data = drawing(order="big", version=version, stream=stream)
    vi = icadkit.read(data).read_views()
    assert vi.status == "complete"
    (record,) = vi.placements
    assert record.name == "!XY" and record.origin == (12.0, 34.0)
    assert record.scale == 2.0 and record.status == "complete"
    # The global view is the only view here, so the record names no 2D view.
    assert [d.code for d in vi.diagnostics] == ["views.placement_view"]


def test_record_that_names_no_view_and_view_without_record():
    data = sheet()
    at = icadkit.read(data).read_views().placements[0].byte_range.start
    b = bytearray(data)
    b[at + 136 : at + 144] = b"!ZZ     "
    vi = icadkit.read(bytes(b)).read_views()
    assert vi.status == "complete"
    assert vi.placements[0].name == "!ZZ"
    assert [d.code for d in vi.diagnostics] == [
        "views.placement_view",
        "views.placement_missing",
    ]
    assert all(v.placement is None for v in vi.views)


def test_invalid_scale_and_origin_keep_the_record_invalid():
    data = sheet()
    at = icadkit.read(data).read_views().placements[0].byte_range.start
    data = _patch(data, at + 64, "<d", -1.0)
    data = _patch(data, at + 40, "<d", float("nan"))
    vi = icadkit.read(data).read_views()
    (record,) = vi.placements
    assert record.status == "invalid" and record.scale is None
    assert [d.code for d in record.diagnostics] == [
        "views.placement",
        "views.placement",
    ]
    assert [d.byte_offset for d in record.diagnostics] == [at + 40, at + 64]
    # An invalid record is still the view's record.
    assert next(v for v in vi.views if v.kind == "2d_view").placement is record


def test_placement_list_is_bounded_by_the_control_sequence():
    # A second, untagged record continues the list; a non-control record ends it.
    stream = placement("!XY", order="little", first=True) + placement(
        "!XZ", origin=(5.0, 6.0), order="little"
    )
    data = drawing(order="little", version=b"\0\x08\0\x03", stream=stream)
    vi = icadkit.read(data).read_views()
    assert [p.name for p in vi.placements] == ["!XY", "!XZ"]
    assert vi.placements[1].origin == (5.0, 6.0)


@pytest.mark.parametrize(
    ("raw", "label", "version"),
    [
        (b"\0\x08\0\x03", "V8L3", (8, 3)),
        (b"\0\x01\0\x09", "V1L9", (1, 9)),
        (b"\0\x08\0\x04", "V8L4", (8, 4)),
        (b"\x01\x08\0\x03", None, None),
        (b"\0\x08\0\0", None, None),
        (b"\0\x08", None, None),
    ],
)
def test_version_label(raw, label, version):
    assert icadkit.version_label(raw) == label
    if len(raw) == 4:
        info = icadkit.inspect(drawing(order="little", version=raw))
        assert info.header.version_label == label
        assert info.header.version == version


def test_qualified_versions_match_the_native_readers():
    support = icadkit.qualified_versions()
    assert support.views("little") == support.views_little
    with pytest.raises(ValueError):
        support.views("middle")
    for order, labels in (("little", support.views_little), ("big", support.views_big)):
        for label in labels:
            major, level = label[1:].split("L")
            raw = bytes([0, int(major), 0, int(level)])
            vi = icadkit.read(drawing(order=order, version=raw)).read_views()
            assert not [d for d in vi.diagnostics if d.code == "views.profile"], label
    for order, raw in (("little", b"\0\x08\0\x04"), ("big", b"\0\x03\0\x06")):
        vi = icadkit.read(drawing(order=order, version=raw)).read_views()
        assert vi.status == "unsupported"
        assert [d.code for d in vi.diagnostics] == ["views.profile"]
    assert "V8L4" not in support.parts and "V8L3" in support.parts
    parts = icadkit.read(drawing(order="little", version=b"\0\x08\0\x04")).read_parts()
    assert [d.code for d in parts.diagnostics][:1] == ["parts.profile"]
    assert support.drawing_layout == ("V7L6", "V7L7", "V8L1", "V8L2", "V8L3")


def test_cli_json_reports_version_label_and_placements(tmp_path, capsys):
    path = tmp_path / "sheet.icd"
    path.write_bytes(sheet())
    assert main(["inspect", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    header = (payload.get("result") or payload)["header"]
    assert header["version_label"] == "V8L3"
    assert main(["views", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    (record,) = payload["result"]["placements"]
    assert record["name"] == "!XY" and record["origin"] == [0.0, 0.0]
    views = {v["kind"]: v for v in payload["result"]["views"]}
    assert views["2d_view"]["placement"]["scale"] == 1.0
    assert views["2d_global"]["placement"] is None
