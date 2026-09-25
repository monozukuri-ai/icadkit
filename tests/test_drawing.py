import dataclasses
import json
import math
import struct

import pytest
from drawing_fixtures import big_part, drawing, entity, text_entity

import icadkit
from icadkit.cli import main

BIG_VERSIONS = (
    [(1, 9)]
    + [(3, x) for x in (1, 2, 3, 4, 5, 7, 8, 9)]
    + [(5, x) for x in range(1, 5)]
    + [(6, 1), (6, 2), (7, 1)]
)
LITTLE_VERSIONS = [(7, x) for x in range(2, 8)] + [(8, x) for x in range(1, 4)]


@pytest.mark.parametrize(
    "order,version",
    [("big", bytes((0, a, 0, b))) for a, b in BIG_VERSIONS]
    + [("little", bytes((0, a, 0, b))) for a, b in LITTLE_VERSIONS],
)
@pytest.mark.parametrize("extension", [0, 132])
def test_versioned_local_geometry(order, version, extension):
    d = icadkit.read(
        drawing(
            [entity(t, order=order) for t in (1, 2, 5, 6)],
            order=order,
            version=version,
            extension=extension,
        )
    )
    v = d.read_views()
    assert v.status == "complete" and v.document_kind == "document"
    assert len(v.views) == 1 and len(v.views[0].entries) == 4
    result = d.read_drawing()
    assert result.status == "complete"
    assert result.coordinate_space == "view_local" and result.length_unit == "mm"
    point, line, arc, circle = result.entities
    assert point.primitive.points == ((-11, -17),)
    assert line.primitive.points == ((13, -7), (37, 11))
    assert line.primitive.direction == (0.8, 0.6)
    assert line.primitive.length == 30
    assert arc.primitive.sweep_angle == pytest.approx(math.radians(-133))
    assert circle.primitive.center == (-19, 23) and circle.primitive.radius == 7
    assert line.source_id == 0x80000123
    assert (line.layer, line.visible) == (17, True)
    assert (line.line_width, line.line_style, line.color_index) == (2, 3, 5)
    assert all(e.view_id == v.views[0].view_id for e in result.entities)
    assert d.source_bytes(line.byte_range) == entity(order=order)
    assert not d.read_parts().parts
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.document_kind = "unknown"


@pytest.mark.parametrize("number", range(3, 9))
def test_registered_original_without_resave(number):
    d = icadkit.read(drawing([entity()], name=b"@BUHIN", number=number))
    assert d.read_views().document_kind == "registered_part"
    assert len(d.read_drawing().entities) == 1
    assert d.read_drawing().entities[0].owner_offset is None


@pytest.mark.parametrize(
    "order,version",
    [
        ("big", b"\0\x03\0\x06"),
        ("little", b"\0\x08\0\x04"),
        ("little", b"\0\x06\0\x02"),
        ("big", b"\0\x08\0\x03"),
    ],
)
def test_unknown_profiles_fail_closed(order, version):
    d = icadkit.read(drawing([entity(order=order)], order=order, version=version))
    assert d.read_views().status == d.read_drawing().status == "unsupported"
    assert d.read_views().profile_id is None
    assert not d.read_drawing().entities


def test_hidden_and_degenerate_line():
    d = icadkit.read(
        drawing([entity(1, visible=False, flags=0), entity(2, values=(5, 7, 0, -1, 0))])
    )
    point, line = d.read_drawing().entities
    assert point.visible is False and point.primitive.points == ((-11, -17),)
    assert line.primitive.points == ((5, 7), (5, 7))
    assert line.primitive.direction == (0, -1) and line.primitive.length == 0


@pytest.mark.parametrize(
    "values",
    [
        (0, 0, 2, 0, 10),
        (0, 0, 1, 0, -1),
        (math.nan, 0, 1, 0, 10),
        (0, 0, 1, 0, math.inf),
    ],
)
def test_invalid_geometry(values):
    r = icadkit.read(drawing([entity(values=values)])).read_drawing()
    assert r.status == "invalid" and r.entities[0].primitive is None


def test_annotations_are_not_decomposed_and_unknowns_do_not_resync():
    dim = bytearray(entity())
    dim[15] = 25
    d = icadkit.read(drawing([bytes(dim), entity(6)]))
    r = d.read_drawing()
    assert r.status == "partial" and r.entities[0].status == "unsupported"
    assert r.entities[1].primitive.kind == "circle"
    stream = b"\x50\0\0\x01" + entity()
    d = icadkit.read(drawing(stream=stream))
    assert not d.read_drawing().entities
    assert d.read_views().status == "partial"
    assert d.read_views().views[0].diagnostics[0].code == "views.record"


@pytest.mark.parametrize(
    "stream,end,code",
    [
        (b"\x30\x01\0\0\0\0\0\0", True, "views.record_length"),
        (b"\x30\x01\0\0\xff\xff\xff\xfc", True, "views.record_length"),
        (b"", False, "views.missing_end"),
        (b"\xfe\0\0\0\0\0\0\0", True, "views.after_end"),
    ],
)
def test_record_bounds(stream, end, code):
    result = icadkit.read(drawing(stream=stream, end=end)).read_views()
    assert result.status in ("partial", "invalid")
    assert result.views[0].diagnostics[0].code == code


def test_header_bounds_and_unknown_name():
    b = bytearray(drawing())
    struct.pack_into(">I", b, 496 + 16, 0xFFFFFFFF)
    assert icadkit.read(bytes(b)).read_views().status == "invalid"
    r = icadkit.read(drawing(name=b"UNKNOWN", number=1)).read_drawing()
    assert r.status == "unsupported"


def test_text_content_layout_boundary_and_limits():
    d = icadkit.read(drawing([text_entity()], order="little", version=b"\0\x08\0\x03"))
    text = d.read_drawing().entities[0]
    assert text.status == "partial" and text.primitive is None
    assert text.text.lines == ("試験", "A12")
    assert text.text.layout_status == "unsupported"
    with pytest.raises(icadkit.LimitExceededError, match="max_text_bytes"):
        d.read_drawing(limits=icadkit.DrawingLimits(max_text_bytes=2))
    for at, value in [(88, b"\xff\xff"), (88 + 52, b"\xff" * 4), (88 + 48, b"\0" * 4)]:
        raw = bytearray(text_entity())
        raw[at : at + len(value)] = value
        e = (
            icadkit.read(drawing([bytes(raw)], order="little", version=b"\0\x08\0\x03"))
            .read_drawing()
            .entities[0]
        )
        assert e.text is None and e.status == "unsupported"


def test_limits_and_wrong_types():
    d = icadkit.read(drawing([entity(), entity()]))
    with pytest.raises(icadkit.LimitExceededError):
        d.read_views(limits=icadkit.ViewLimits(max_records=1))
    with pytest.raises(icadkit.LimitExceededError):
        d.read_drawing(limits=icadkit.DrawingLimits(max_entity_bytes=32))
    for cls in (icadkit.ViewLimits, icadkit.DrawingLimits):
        field = next(iter(cls.__dataclass_fields__))
        with pytest.raises(ValueError):
            cls(**{field: 0})
        with pytest.raises(TypeError):
            cls(**{field: True})
    with pytest.raises(TypeError):
        d.read_views(limits=object())
    with pytest.raises(TypeError):
        d.read_drawing(limits=object())


@pytest.mark.parametrize("version", [b"\0\x06\0\x01", b"\0\x06\0\x02", b"\0\x07\0\x01"])
def test_big_part_hierarchy_preserves_unqualified_placement(version):
    stream = big_part(child=0xA0000002) + big_part(
        0xA0000002, root=False, parent=0xE0000001
    )
    d = icadkit.read(
        drawing(name=b"3DGLOBAL", stream=stream, version=version, extension=132)
    )
    p = d.read_parts()
    assert len(p.parts) == 2 and p.status.hierarchy == "complete"
    assert p.parts[1].parent_id == p.parts[0].part_id
    assert p.parts[1].name == "試作部品"
    assert p.profile.byte_order == "big" and p.profile.entity_policy == "inventory_only"
    assert p.parts[0].placement.values[:3] == (13, -7, 5)
    assert all(x.placement.world_transform is None for x in p.parts)
    assert p.source_length_unit is None and p.status.native_geometry == "unsupported"
    assert not d.read_drawing().entities
    with pytest.raises(icadkit.LimitExceededError):
        d.read_parts(limits=icadkit.PartLimits(max_parts=1))
    with pytest.raises(icadkit.LimitExceededError):
        d.read_parts(limits=icadkit.PartLimits(max_depth=1))


def test_big_part_bad_links_and_unknown_flags():
    p = icadkit.read(
        drawing(
            name=b"3DGLOBAL", version=b"\0\x06\0\x02", stream=big_part(child=0xA0000002)
        )
    ).read_parts()
    assert p.status.hierarchy == "invalid"
    p = icadkit.read(
        drawing(name=b"3DGLOBAL", version=b"\0\x06\0\x02", stream=big_part(flags=0x58))
    ).read_parts()
    assert not p.parts and p.status.index == "unsupported"
    p = icadkit.read(
        drawing(
            name=b"3DGLOBAL", version=b"\0\x06\0\x02", stream=big_part() + big_part()
        )
    ).read_parts()
    assert p.status.hierarchy == "invalid"
    assert any(d.code == "parts.duplicate_id" for d in p.diagnostics)


def test_cli_json_and_exit_codes(tmp_path, capsys):
    path = tmp_path / "authored.icd"
    path.write_bytes(drawing([entity()]))
    assert main(["views", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["document_kind"] == "document"
    assert main(["drawing", str(path), "--json"]) == 0
    assert (
        json.loads(capsys.readouterr().out)["result"]["entities"][0]["primitive"][
            "kind"
        ]
        == "line"
    )
    assert main(["drawing", str(path), "--json", "--max-entity-bytes", "32"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["category"] == "limit_exceeded"
    path.write_bytes(drawing([text_entity()], order="little", version=b"\0\x08\0\x03"))
    assert main(["drawing", str(path), "--json"]) == 3
    capsys.readouterr()
