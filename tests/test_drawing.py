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


def view_control(order):
    control = bytearray(264)
    control[:4] = (264).to_bytes(4, order)
    control[12:16] = b"\x40\0\x01\xcb"
    control[16:20] = (0x80000123).to_bytes(4, order)
    control[24:26] = (240).to_bytes(2, order)
    control[26:28] = b"\x40\xfd"
    # Bytes within the framed payload never supply traversal or ownership.
    control[32:40] = bytes.fromhex("6100000100000070")
    return bytes(control)


@pytest.mark.parametrize(
    "order,version", [("big", b"\0\x05\0\x01"), ("little", b"\0\x07\0\x07")]
)
def test_counted_view_control_preserves_following_geometry_and_raw_bytes(
    order, version
):
    control = view_control(order)
    stream = (0x40000000).to_bytes(4, order) + control * 3
    stream += (0x30010000).to_bytes(4, order) + entity(order=order)
    doc = icadkit.read(drawing(stream=stream, order=order, version=version, count=1))
    vi = doc.read_views()
    assert vi.status == "complete"
    first, record, repeated, line = vi.views[0].entries
    assert first.tag == 0x40000000 and repeated.kind == "metadata"
    assert record.kind == "metadata" and record.owner_offset is None
    assert doc.source_bytes(record.byte_range) == control
    result = doc.read_drawing()
    assert result.entities[0].primitive.points == ((13, -7), (37, 11))
    assert line.owner_offset is None
    with pytest.raises(icadkit.LimitExceededError):
        doc.read_views(limits=icadkit.ViewLimits(max_records=1))


@pytest.mark.parametrize(
    "defect", ["type", "length", "source_id", "subrecord", "position"]
)
def test_unqualified_view_control_never_resynchronizes(defect):
    control = bytearray(view_control("big"))
    if defect == "type":
        control[15] = 0xCA
    elif defect == "length":
        control[:4] = (268).to_bytes(4, "big")
    elif defect == "source_id":
        control[16] = 0x90
    elif defect == "subrecord":
        control[24:26] = (236).to_bytes(2, "big")
    stream = bytes.fromhex("40000000") + view_control("big") + bytes(control)
    stream += (0x30010000).to_bytes(4, "big") + entity()
    if defect == "position":
        stream = bytes(control) + bytes.fromhex("30010000") + entity()
    doc = icadkit.read(drawing(stream=stream))
    assert doc.read_views().status == "partial"
    assert not doc.read_drawing().entities


def reference_envelope(size=112, source=17):
    raw = bytearray(size)
    struct.pack_into(">5I", raw, 0, size, source, 3, 29, 0x40000000)
    # Opaque payload may resemble record tags. Only the declared boundary counts.
    raw[40:48] = bytes.fromhex("6100000130010000")
    if size == 192:
        raw[112:] = b"authored reference".ljust(80, b" ")
    return bytes(raw)


@pytest.mark.parametrize("version", [b"\0\x05\0\x01", b"\0\x05\0\x03"])
def test_legacy_reference_group_preserves_bounds_and_following_owner(version):
    first, long, last = (
        reference_envelope(),
        reference_envelope(192),
        reference_envelope(source=31),
    )
    owner = struct.pack(">II", 0x60000002, 240) + b"\0" * 236
    stream = bytes.fromhex("50000001") + first + long + last + owner
    stream += bytes.fromhex("30010000") + entity()
    doc = icadkit.read(drawing(stream=stream, name=b"3DGLOBAL", version=version))
    vi = doc.read_views()
    assert vi.status == "complete"
    a, b, c, part, shape = vi.views[0].entries
    assert a.tag == 0x50000001 and b.tag is None and c.tag is None
    assert all(r.kind == "metadata" and r.owner_offset is None for r in (a, b, c))
    assert doc.source_bytes(a.byte_range) == bytes.fromhex("50000001") + first
    assert doc.source_bytes(b.byte_range) == long
    assert doc.source_bytes(c.byte_range) == last
    assert part.kind == "legacy_owner" and doc.source_bytes(part.byte_range) == owner
    assert shape.kind == "entity" and shape.owner_offset == part.byte_range.start
    assert doc.source_bytes(shape.byte_range) == entity()
    assert all(
        left.byte_range.end == right.byte_range.start
        for left, right in ((a, b), (b, c), (c, part))
    )
    assert not doc.read_parts().parts  # Framing does not qualify old part semantics.
    with pytest.raises(icadkit.LimitExceededError):
        doc.read_views(limits=icadkit.ViewLimits(max_records=2))


@pytest.mark.parametrize(
    "defect",
    [
        "first_type",
        "type",
        "size",
        "zero_id",
        "untagged",
        "reset",
        "truncated",
        "version",
        "2d",
        "endian",
    ],
)
def test_legacy_reference_group_fails_closed(defect):
    record = bytearray(reference_envelope())
    if defect in ("first_type", "type"):
        struct.pack_into(">I", record, 16, 0x40000001)
    elif defect == "size":
        struct.pack_into(">I", record, 0, 116)
    elif defect == "zero_id":
        struct.pack_into(">I", record, 4, 0)
    elif defect == "truncated":
        record = record[:28]
    stream = bytes.fromhex("50000001") + reference_envelope()
    if defect == "first_type":
        stream = bytes.fromhex("50000001")
    elif defect == "untagged":
        stream = b""
    elif defect == "reset":
        stream += struct.pack(">II", 0x20000000, 4)
    stream += record
    stream += bytes.fromhex("30010000") + entity()
    doc = icadkit.read(
        drawing(
            stream=stream,
            name=b"!!GLOBAL" if defect == "2d" else b"3DGLOBAL",
            version=b"\0\x05\0\x02"
            if defect == "version"
            else b"\0\x08\0\x03"
            if defect == "endian"
            else b"\0\x05\0\x03",
            order="little" if defect == "endian" else "big",
        )
    )
    vi = doc.read_views()
    assert vi.status == "partial"
    assert vi.views[0].diagnostics[0].code == "views.record"
    assert not any(e.kind == "entity" for e in vi.views[0].entries)
    assert vi.views[0].opaque_ranges[-1].end == vi.views[0].byte_range.end - 4


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
    assert text.status == "complete" and text.primitive is None
    assert text.text.lines == ("試験", "A12")
    assert text.text.layout_status == "complete"
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
    assert main(["drawing", str(path), "--json"]) == 0
    text = json.loads(capsys.readouterr().out)["result"]["entities"][0]["text"]
    assert text["layout_status"] == "complete" and text["anchor"] == [10, 24]
    rotated = text_entity(angle_words=(0, 161061273))
    path.write_bytes(drawing([rotated], order="little", version=b"\0\x08\0\x03"))
    assert main(["drawing", str(path), "--json"]) == 3
    capsys.readouterr()


@pytest.mark.parametrize("order", ["little", "big"])
def test_two_d_view_fields_are_qualified(order):
    version = b"\0\x08\0\x03" if order == "little" else b"\0\x03\0\x04"
    doc = icadkit.read(
        drawing(
            [entity(2, order=order), entity(6, order=order)],
            order=order,
            version=version,
            extent=(-26, -7, 37, 30),
        )
    )
    view = doc.read_views().views[0]
    assert view.status == "complete" and view.kind == "2d_global"
    assert view.entity_count == 2
    assert view.raw_entity_words == (4 + 72 * 2) // 4
    assert view.scale == 1.0 and view.raw_scale_text == b"1/1     "
    assert view.extent_kind == "box"
    assert view.extent == icadkit.ViewExtent(-26, -7, 37, 30)
    assert view.extent.length_unit == "mm"
    assert view.extent.coordinate_space == "view_local"


def test_empty_view_extent_sentinel_and_zero_count():
    view = icadkit.read(drawing()).read_views().views[0]
    assert view.status == "complete"
    assert view.entity_count == 0 and view.raw_entity_words == 0
    assert view.extent is None and view.extent_kind == "empty"


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"count": 5}, "views.entity_count"),
        ({"scale": 0.0}, "views.scale"),
        ({"scale": float("nan")}, "views.scale"),
        ({"extent": (1, 2, 0, 5)}, "views.extent"),
        ({"extent": (float("inf"), 0, 0, 0)}, "views.extent"),
    ],
)
def test_unobserved_two_d_header_values_keep_the_view_partial(kwargs, code):
    view = icadkit.read(drawing([entity()], **kwargs)).read_views().views[0]
    assert view.status == "partial"
    assert [d.code for d in view.diagnostics] == [code]
    assert len(view.entries) == 1  # Traversal is unaffected.
    field = {"views.entity_count": "entity_count", "views.scale": "scale"}.get(code)
    if field:
        assert getattr(view, field) is None
    else:
        assert view.extent is None and view.extent_kind == "unqualified"


def test_three_d_views_keep_the_fields_unqualified():
    doc = icadkit.read(
        drawing(name=b"3DGLOBAL", version=b"\0\x06\0\x02", stream=big_part())
    )
    view = doc.read_views().views[0]
    assert view.kind == "3d_global" and view.status == "complete"
    assert view.entity_count is None and view.scale is None
    assert view.raw_entity_words is None and view.raw_scale_text == b""
    assert view.extent is None and view.extent_kind == "unqualified"


def test_text_layout_fields_are_qualified():
    doc = icadkit.read(
        drawing([text_entity()], order="little", version=b"\0\x08\0\x03")
    )
    e = doc.read_drawing().entities[0]
    t = e.text
    assert (
        e.status == "complete" and t.layout_status == "complete" and not t.diagnostics
    )
    assert (t.base_point, t.direction, t.raw_way) == (1, "horizontal", 1)
    assert (t.height, t.char_width, t.char_pitch) == (4, 4, 4)
    assert t.rotation == 0 and t.scale_factor == 1 and not t.glyph_cache
    assert t.line_origins == ((10, 20), (10, 13.5))
    assert t.box == (10, 13.5, 18, 24) and t.anchor == (10, 24)
    assert (e.line_width, e.line_style, e.color_index) == (2, 1, 1)


def test_vertical_text_box_and_centre_anchor():
    record = text_entity(("検証", "材質"), origin=(16.5, 12), direction=2, base_point=5)
    t = (
        icadkit.read(drawing([record], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
        .text
    )
    assert t.layout_status == "complete" and t.direction == "vertical"
    assert t.line_origins == ((16.5, 12), (10, 12))
    assert t.box == (10, 8, 20.5, 16) and t.anchor == (15.25, 12)


def test_rotated_text_keeps_its_angle_without_anchor():
    record = text_entity(("A",), angle_words=(0, 161061273))
    e = (
        icadkit.read(drawing([record], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "partial" and e.text.layout_status == "partial"
    assert e.text.rotation == pytest.approx(27.0, abs=1e-6)
    assert e.text.anchor is None and e.text.box is None
    assert [d.code for d in e.diagnostics] == [
        "drawing.text_layout",
        "drawing.text_rotation",
    ]


def test_glyph_cache_is_framed_not_interpreted():
    cache = (b"\x04\x00\x80\x10\x20\x30" + b"\x02\x00\x80\x00", b"\x02\x00\x80\x00" * 3)
    record = text_entity(glyphs=cache)
    t = (
        icadkit.read(drawing([record], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
        .text
    )
    assert t.layout_status == "complete" and t.glyph_cache
    broken = text_entity(glyphs=(b"\x09\x00\x80", b"\x02\x00\x80\x00" * 3))
    t = (
        icadkit.read(drawing([broken], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
        .text
    )
    assert t.layout_status == "partial" and [d.code for d in t.diagnostics] == [
        "drawing.text_glyphs"
    ]


@pytest.mark.parametrize(
    "kwargs,patch,code",
    [
        ({}, (47, 9), "drawing.text_fields"),
        ({"base_point": 0}, None, "drawing.text_base_point"),
        ({"direction": 3}, None, "drawing.text_direction"),
        ({"lines": ("a", "b", "c")}, None, "drawing.text_fields"),
        ({}, (88 + 44, 0xF1), "drawing.text_fields"),
    ],
)
def test_unobserved_text_fields_keep_the_layout_partial(kwargs, patch, code):
    record = bytearray(text_entity(**kwargs))
    if patch:
        record[patch[0]] = patch[1]
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "partial" and e.text is not None
    assert code in [d.code for d in e.text.diagnostics]
    assert e.text.anchor is None and e.text.lines == tuple(
        kwargs.get("lines", ("試験", "A12"))
    )


def _dimension_record():
    from icadkit import DrawingWriter, LengthDimension2D

    writer = DrawingWriter(
        icadkit.read(
            drawing(
                [entity(2, order="little")], order="little", version=b"\0\x08\0\x03"
            )
        )
    )
    writer.add_entities("!!GLOBAL", [LengthDimension2D((0, 0), (40, 0), (20, -10))])
    doc = icadkit.read(writer.to_bytes())
    return bytearray(doc.source_bytes(doc.read_drawing().entities[1].byte_range))


@pytest.mark.parametrize(
    "patch,codes",
    [
        ((15, 27), ["drawing.dimension_layout"]),
        ((442, 0x99), ["drawing.dimension_item"]),
        ((376 + 36, 0x01), ["drawing.dimension_text"]),
    ],
)
def test_unobserved_dimension_layouts_keep_the_entity_partial(patch, codes):
    record = _dimension_record()
    record[patch[0]] = patch[1]
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "partial" and e.dimension is not None
    assert [d.code for d in e.dimension.diagnostics] == codes
    assert e.diagnostics[0].code == "drawing.dimension_layout"
    if patch == (15, 27):
        assert e.dimension.kind == "diameter" and e.dimension.measured is None
        assert len(e.dimension.items) == 9 and e.dimension.text.lines == ("４０",)


def test_dimension_records_are_only_read_for_qualified_versions():
    record = bytes(_dimension_record())
    e = (
        icadkit.read(drawing([record], order="little", version=b"\0\x07\0\x05"))
        .read_drawing()
        .entities[0]
    )
    assert e.dimension is None and e.status == "unsupported"
    e = (
        icadkit.read(drawing([record], order="little", version=b"\0\x07\0\x07"))
        .read_drawing()
        .entities[0]
    )
    assert e.dimension is not None and e.status == "complete"


def _hatch_record():
    from icadkit import DrawingWriter, Hatch2D

    writer = DrawingWriter(
        icadkit.read(
            drawing(
                [entity(2, order="little")], order="little", version=b"\0\x08\0\x03"
            )
        )
    )
    writer.add_entities("!!GLOBAL", [Hatch2D(((0, 0), (40, 0), (40, 30), (0, 30)))])
    doc = icadkit.read(writer.to_bytes())
    return bytearray(doc.source_bytes(doc.read_drawing().entities[1].byte_range))


@pytest.mark.parametrize(
    "patch,codes",
    [
        ((48 + 16 * 7, 1), ["drawing.hatch_boundary"]),  # The list no longer closes.
        ((176 + 8 + 32, 1), ["drawing.hatch_fields"]),  # A nonzero reserved word.
    ],
)
def test_unobserved_hatch_layouts_keep_the_entity_partial(patch, codes):
    record = _hatch_record()
    record[patch[0]] = patch[1]
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "partial" and e.hatch is not None
    assert [d.code for d in e.hatch.diagnostics] == codes
    assert e.diagnostics[0].code == "drawing.hatch_layout"
    assert len(e.hatch.segments) == 17


def test_hatch_records_need_the_item_at_the_declared_offset():
    record = _hatch_record()
    record[178] = 0x45  # Not the 44 09 marker.
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert e.hatch is None and e.status == "unsupported"
