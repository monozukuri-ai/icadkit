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


def test_dimension_records_read_in_every_version_whose_runs_decode():
    # The layout is qualified by its framing, not by the version: a V7L5
    # document with the same record reads it (its real runs are CP932, see
    # test_legacy_dimension_and_text_records_decode_cp932_runs)
    record = bytes(_dimension_record())
    for version in (b"\0\x07\0\x05", b"\0\x07\0\x07"):
        e = (
            icadkit.read(drawing([record], order="little", version=version))
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


# --- ellipses, free curves, leader notes, angle dimensions, symbols (0.3.9) ---


def _sheet_writer(order="little", version=b"\0\x08\0\x03"):
    from drawing_fixtures import sheet

    from icadkit import DrawingWriter

    writer = DrawingWriter(
        icadkit.read(sheet(order=order, version=version, extent=(0, 0, 297, 210)))
    )
    writer.add_view("XY", scale=0.5, origin=(80.0, 60.0))
    writer.delete_view("!XY")
    return writer


def _entities(writer, types):
    doc = icadkit.read(writer.to_bytes())
    return [e for e in doc.read_drawing().entities if e.raw_type in types], doc


@pytest.mark.parametrize(
    "order,version", [("little", b"\0\x08\0\x03"), ("big", b"\0\x05\0\x01")]
)
def test_ellipse_records_round_trip(order, version):
    from icadkit import Ellipse2D, EllipticalArc2D, EntityStyle

    writer = _sheet_writer(order, version)
    writer.add_entities(
        "XY",
        [
            Ellipse2D((40.0, 30.0), 12.0, 6.0, math.radians(30)),
            EllipticalArc2D((80.0, 30.0), 10.0, 4.0, 0.0, math.pi / 2, -math.pi),
        ],
        style=EntityStyle(layer=3, line_width=2, line_style=1, color_index=5),
    )
    (full, arc), doc = _entities(writer, (88, 89))
    assert full.status == arc.status == "complete"
    assert (full.layer, full.line_width, full.line_style, full.color_index) == (
        3,
        2,
        1,
        5,
    )
    p = full.primitive
    assert p.kind == "ellipse" and p.center == (40.0, 30.0)
    assert (p.radius, p.minor_radius) == (12.0, 6.0)
    assert math.isclose(p.rotation, math.radians(30)) and p.start_angle == 0.0
    assert math.isclose(p.sweep_angle, math.tau)
    q = arc.primitive
    assert q.kind == "elliptical_arc" and (q.radius, q.minor_radius) == (10.0, 4.0)
    assert q.start_angle == math.pi / 2 and q.sweep_angle == -math.pi
    # a degenerate axis makes the entity invalid, not a guessed circle
    record = bytearray(doc.source_bytes(full.byte_range))
    struct.pack_into(("<" if order == "little" else ">") + "d", record, 56, 0.0)
    bad = (
        icadkit.read(drawing([bytes(record)], order=order, version=version))
        .read_drawing()
        .entities[0]
    )
    assert bad.status == "invalid" and bad.primitive is None


def test_free_curve_round_trip_and_consistency_checks():
    from icadkit import Curve2D

    knots = ((0.0, 0.0), (10.0, 4.0), (20.0, -3.0), (30.0, 0.0))
    writer = _sheet_writer()
    writer.add_entities("XY", [Curve2D(knots)])
    (e,), doc = _entities(writer, (16,))
    assert e.status == "complete" and e.curve is not None
    c = e.curve
    assert c.knots == knots and len(c.chord_lengths) == 3
    assert math.isclose(c.length, sum(c.chord_lengths))
    assert c.extent == (0.0, -3.0, 30.0, 4.0) and c.box == c.extent
    assert c.segment_words == ((0.0,) * 6,) * 3  # retained, not interpreted
    assert e.byte_range.length == 136 + 72 * 3
    # a chord that disagrees with the knots keeps the curve partial
    record = bytearray(doc.source_bytes(e.byte_range))
    struct.pack_into("<d", record, 120, 99.0)
    partial = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert partial.status == "partial"
    assert [d.code for d in partial.curve.diagnostics] == [
        "drawing.curve_layout",
        "drawing.curve_layout",
    ]
    with pytest.raises(ValueError):
        writer.add_entities("XY", [Curve2D(((0.0, 0.0), (0.0, 0.0)))])


def test_leader_note_round_trip_and_hidden_items():
    from icadkit import LeaderNote2D
    from icadkit.write import _item

    writer = _sheet_writer()
    writer.add_entities(
        "XY",
        [LeaderNote2D(("注記 1", "２行目"), (50.0, 50.0), (70.0, 70.0), (100.0, 70.0))],
    )
    (e,), doc = _entities(writer, (22,))
    assert e.status == "complete" and e.note is not None
    n = e.note
    assert [t.lines for t in n.texts] == [("注記 1",), ("２行目",)]
    assert n.text is n.texts[0]
    assert n.path == ((50.0, 50.0), (70.0, 70.0), (100.0, 70.0))
    assert n.arrow[1] == (50.0, 50.0)  # the tip
    assert math.isclose(
        math.dist(n.arrow[0], n.arrow[1]), math.dist(n.arrow[2], n.arrow[1])
    )
    assert (n.arrow_width, round(n.arrow_angle, 6)) == (1.5, 15.0)
    assert n.box[0] <= 50.0 and n.box[2] >= 100.0
    # a hidden 40-byte vectors item (observed in saved notes) is retained, not drawn
    record = bytearray(doc.source_bytes(e.byte_range))
    record += _item("<", 0x0300, 3, 1, (70.0, 70.0, 10.0, -5.0))
    struct.pack_into("<I", record, 0, len(record))
    again = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert again.status == "complete" and len(again.note.items) == 3
    assert again.note.items[2].points == ((70.0, 70.0), (80.0, 65.0))
    assert again.note.path == n.path


def test_angle_dimension_round_trip():
    from icadkit import AngleDimension2D

    writer = _sheet_writer()
    writer.add_entities(
        "XY", [AngleDimension2D((0.0, 60.0), (30.0, 60.0), (20.0, 85.0), 15.0)]
    )
    (e,), doc = _entities(writer, (26,))
    assert e.status == "complete" and e.dimension is not None
    d = e.dimension
    assert d.kind == "angle" and d.layout_status == "complete"
    assert d.center == (0.0, 60.0) and d.radius == 15.0 and d.arc_start == 0.0
    expected = math.atan2(25.0, 20.0)
    assert math.isclose(d.arc_sweep, expected) and math.isclose(
        d.value, math.degrees(expected)
    )
    assert d.measured == ((30.0, 60.0), (20.0, 85.0))
    assert d.text.lines == ("５１．３４０°",)
    assert [i.kind for i in d.items] == list(
        (
            "vectors",
            "vectors",
            "arc",
            "arc",
            "arc",
            "point",
            "point",
            "vectors",
            "point",
            "vectors",
            "point",
            "point",
        )
    )
    assert [i.raw_marker for i in d.items[2:5]] == [0x0500, 0x0544, 0x0500]
    # paper millimetres are divided by the view scale (1/2)
    assert math.isclose(d.extension, 2.8 / 0.5) and d.aux_offset == 0.0
    assert (d.arrow_width, round(d.arrow_angle, 6)) == (1.5, 15.0)
    # swapping the aux line order (as saved files do) still reads complete
    record = bytearray(doc.source_bytes(e.byte_range))
    items = [i for i in d.items]
    a, b = items[7].byte_range, items[9].byte_range
    first = record[
        a.start - e.byte_range.start : items[8].byte_range.end - e.byte_range.start
    ]
    second = record[
        b.start - e.byte_range.start : items[10].byte_range.end - e.byte_range.start
    ]
    swapped = (
        record[: a.start - e.byte_range.start]
        + second
        + first
        + record[items[10].byte_range.end - e.byte_range.start :]
    )
    again = (
        icadkit.read(drawing([bytes(swapped)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert again.status == "complete" and again.dimension.measured == (
        (20.0, 85.0),
        (30.0, 60.0),
    )


def test_leader_diameter_dimension_is_interpreted():
    from icadkit import DiameterDimension2D
    from icadkit.drawing import _composite
    from icadkit.write import _arrowhead, _item

    writer = _sheet_writer()
    writer.add_entities("XY", [DiameterDimension2D((30.0, 40.0), 12.0, (52.0, 62.0))])
    (e,), doc = _entities(writer, (27,))
    assert e.dimension.layout_status == "complete" and e.dimension.leader is None
    record = bytes(doc.source_bytes(e.byte_range))
    parts = _composite(record, "<", 0, prefix="dimension")
    run_at, _ = parts.runs[0]
    run_end = run_at + int.from_bytes(record[run_at : run_at + 2], "little")
    # keep the attributes, the two arcs, the pick point and the run; rebuild the
    # tail as the observed leader variant
    tip = (
        30.0 + 12.0 * math.cos(math.radians(45)),
        40.0 + 12.0 * math.sin(math.radians(45)),
    )
    elbow, end = (52.0, 62.0), (80.0, 62.0)
    direction = (
        (elbow[0] - tip[0]) / math.dist(tip, elbow),
        (elbow[1] - tip[1]) / math.dist(tip, elbow),
    )
    tail = _arrowhead("<", 0x0344, 3, 1, tip, direction, 1.5, 0.4)
    tail += _item(
        "<",
        0x0344,
        3,
        1,
        (
            tip[0],
            tip[1],
            elbow[0] - tip[0],
            elbow[1] - tip[1],
            end[0] - tip[0],
            end[1] - tip[1],
        ),
    )
    tail += _arrowhead("<", 0x0300, 3, 1, tip, direction, 1.5, 0.4)
    tail += _arrowhead(
        "<",
        0x0300,
        3,
        1,
        (
            30.0 - 12.0 * math.cos(math.radians(45)),
            40.0 - 12.0 * math.sin(math.radians(45)),
        ),
        (-direction[0], -direction[1]),
        1.5,
        0.4,
    )
    tail += _item("<", 0x0200, 3, 1, (elbow[0], elbow[1], 1.0, 0.0, 0.0))
    tail += _item("<", 0x0100, 3, 1, elbow, point=True)
    tail += _item("<", 0x0100, 3, 1, (0.0, 0.0), point=True)
    rebuilt = bytearray(record[:run_end] + tail)
    struct.pack_into("<I", rebuilt, 0, len(rebuilt))
    again = (
        icadkit.read(drawing([bytes(rebuilt)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    d = again.dimension
    assert again.status == "complete" and d.kind == "diameter"
    assert d.leader == (tip, elbow, end) and d.line == (elbow, end)
    assert d.line_point == elbow and d.pick_point == e.dimension.pick_point
    assert d.center == (30.0, 40.0) and d.radius == 12.0


def test_text_with_the_extended_attribute_block_reads_complete():
    record = bytearray(text_entity(lines=("φ10",)))
    block = record[40:88]
    extended = bytearray(88)
    extended[:12] = block[:12]
    extended[3] = 88
    extended[4:6] = (60).to_bytes(2, "little")
    extended[7] = 0x0C
    extended[-8:] = block[-8:]  # the scale factor keeps its place at the end
    record[40:88] = extended
    record[24] = 16 + 88
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x01"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.text.lines == ("φ10",)
    assert e.text.base_point == 1 and e.text.direction == "horizontal"
    assert e.text.anchor is not None and e.line_width is not None


def _symbol_record(order, kind, *, run=None, nested=False, items=True):
    from icadkit.write import _item, _run

    prefix = "<" if order == "little" else ">"
    header = bytearray(32)
    header[4] = 1
    header[12] = 0x40
    header[14] = 1
    header[15] = 212
    struct.pack_into(prefix + "I", header, 16, 0x80000031)
    struct.pack_into(prefix + "H", header, 24, 16)
    header[27] = 0xFD
    header[31] = kind
    body = (
        bytes.fromhex("0100000000000000")
        if order == "little"
        else bytes.fromhex("0001000000000000")
    )
    if items:
        body += _item(prefix, 0x0144, 2, 1, (3.5, 4.75), point=True)
    if run is not None:
        body += _run(prefix, run, (10.0, 12.0), 4.0, 2, 1, flags=0xF1)
    if nested:
        member = b"\0\0\0\x80\x13\0\0\x80"
        body += struct.pack(prefix + "H", 4 + len(member)) + b"\0\xfd" + member
    record = header + body
    struct.pack_into(prefix + "I", record, 0, len(record))
    return bytes(record)


def test_symbol_records_read_their_items_runs_and_groups():
    e = (
        icadkit.read(
            drawing(
                [_symbol_record("little", 1)], order="little", version=b"\0\x08\0\x02"
            )
        )
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.symbol.raw_kind == 1
    assert [i.kind for i in e.symbol.items] == ["point"]
    assert (
        e.symbol.items[0].points == ((3.5, 4.75),)
        and e.symbol.items[0].raw_marker == 0x0144
    )
    assert e.symbol.box == (3.5, 4.75, 3.5, 4.75) and e.line_width == 2
    label = (
        icadkit.read(
            drawing(
                [_symbol_record("little", 50, run="Ｍ１", items=False)],
                order="little",
                version=b"\0\x08\0\x02",
            )
        )
        .read_drawing()
        .entities[0]
    )
    assert label.status == "complete" and [t.lines for t in label.symbol.texts] == [
        ("Ｍ１",)
    ]
    group = (
        icadkit.read(
            drawing(
                [_symbol_record("little", 20, nested=True, items=False)],
                order="little",
                version=b"\0\x08\0\x02",
            )
        )
        .read_drawing()
        .entities[0]
    )
    assert group.status == "complete" and len(group.symbol.nested) == 1
    assert group.symbol.nested[0].length == 12 and group.symbol.items == ()
    # big-endian: the point item reads, the legacy run stays unqualified
    legacy = (
        icadkit.read(
            drawing(
                [_symbol_record("big", 50, run="x")],
                order="big",
                version=b"\0\x01\0\x09",
            )
        )
        .read_drawing()
        .entities[0]
    )
    assert legacy.status == "partial" and legacy.symbol.items[0].points == (
        (3.5, 4.75),
    )
    assert [d.code for d in legacy.symbol.diagnostics] == ["drawing.symbol_text"]
    assert legacy.symbol.texts == ()


# --- legacy (big-endian / CP932) composites, line information, attributes ----


def _legacy_run(order, text, origin=(5.0, 6.0), height=3.0):
    """A CP932 text run (flag byte 0x40) with numbers in the file byte order."""
    prefix = "<" if order == "little" else ">"
    run = bytearray(56)
    run[2:4] = b"\x44\x20"
    run[4], run[5] = 2, 0x11
    struct.pack_into(prefix + "2d", run, 8, *origin)
    struct.pack_into(prefix + "3f", run, 24, height, height, height)
    run[48:52] = b"\x40\x00\x00\x01"
    struct.pack_into(prefix + "I", run, 52, len(text))
    run += text.encode("cp932")
    run += b"\0" * (-len(run) % 8)
    struct.pack_into(prefix + "H", run, 0, len(run))
    return bytes(run)


def _legacy_header(order, record_type, kind_byte, blocks, entity_id=0x80000301):
    prefix = "<" if order == "little" else ">"
    header = bytearray(32)
    header[4] = 1
    header[12] = 0x40
    header[14] = kind_byte
    header[15] = record_type
    struct.pack_into(prefix + "I", header, 16, entity_id)
    struct.pack_into(prefix + "H", header, 24, 16 + blocks)
    header[27] = 0xFD
    header[28:32] = b"\0\0\0\x01"
    return header


def _block(number, length, doubles):
    body = bytearray(length)
    body[0:4] = bytes([0xFF, number, 1, length])
    for at, value, prefix in doubles:
        struct.pack_into(prefix + "d", body, at, value)
    return bytes(body)


def _legacy_length_dimension(order):
    """A length dimension between (0, 0) and (40, 0) with CP932 value text,
    minimal attribute blocks and the observed item sequence."""
    prefix = "<" if order == "little" else ">"
    from icadkit.write import _item

    blocks = (
        _block(1, 56, [(24, 3.0, prefix), (32, 1.0, prefix)])
        + _block(3, 48, [(32, 1.0, prefix), (40, 1.0, prefix)])
        + _block(6, 80, [(48, 40.0, prefix)])
        + _block(4, 48, [(24, 1.5, prefix), (40, math.radians(15), prefix)])
    )
    attributes = b"\0" * 8 + blocks
    header = _legacy_header(order, 25, 11, len(blocks))
    items = _legacy_run(order, "４０", origin=(18.0, -9.0))
    half = math.tan(math.radians(15)) * 1.5
    items += _item(prefix, 0x0344, 3, 1, (1.5, -10.0 - half, -1.5, half, 0.0, 2 * half))
    items += _item(
        prefix, 0x0344, 3, 1, (38.5, -10.0 + half, 1.5, -half, 0.0, -2 * half)
    )
    items += _item(prefix, 0x0244, 3, 1, (0.0, -10.0, 1.0, 0.0, 40.0))
    items += _item(prefix, 0x0100, 3, 1, (20.0, -10.0), point=True)
    items += _item(prefix, 0x0100, 3, 1, (20.0, -9.0), point=True)
    items += _item(prefix, 0x0344, 3, 1, (0.0, 0.0, 0.0, -10.0, 0.0, -12.8))
    items += _item(prefix, 0x0344, 3, 1, (40.0, 0.0, 0.0, -10.0, 0.0, -12.8))
    items += _item(prefix, 0x0100, 3, 1, (0.0, 0.0), point=True)
    items += _item(prefix, 0x0100, 3, 1, (40.0, 0.0), point=True)
    record = bytearray(header) + attributes + items
    struct.pack_into(prefix + "I", record, 0, len(record))
    return bytes(record)


def _legacy_text(order, text="品名"):
    prefix = "<" if order == "little" else ">"
    record = bytearray(88)
    record[4] = 1
    record[12] = 0x40
    record[14] = 2
    record[15] = 21
    struct.pack_into(prefix + "I", record, 16, 0x80000302)
    struct.pack_into(prefix + "H", record, 24, 64)
    record[26:28] = b"\0\xfd"
    record[28:32] = b"\0\0\0\x01"
    struct.pack_into(prefix + "4H", record, 32, 0x3D, 8, 0, 0x28)
    record[40:52] = bytes([0xFF, 9, 1, 0x30, 0x18, 0, 1, 8, 1, 7, 1, 1])
    record[68:72] = b"\x80\0\0\0"
    record[72:80] = bytes([2, 1, 0, 0, 0, 2, 0, 0])
    struct.pack_into(prefix + "d", record, 80, 1.0)
    record += _legacy_run(order, text, origin=(10.0, 20.0), height=4.0)
    struct.pack_into(prefix + "I", record, 0, len(record))
    return bytes(record)


@pytest.mark.parametrize(
    "order,version", [("big", b"\0\x01\0\x09"), ("little", b"\0\x07\0\x02")]
)
def test_legacy_dimension_and_text_records_decode_cp932_runs(order, version):
    entities = (
        icadkit.read(
            drawing(
                [_legacy_length_dimension(order), _legacy_text(order)],
                order=order,
                version=version,
            )
        )
        .read_drawing()
        .entities
    )
    dim, text = entities
    assert dim.status == "complete" and dim.dimension is not None
    d = dim.dimension
    assert (
        d.kind == "length"
        and d.value == 40.0
        and d.measured == ((0.0, 0.0), (40.0, 0.0))
    )
    assert d.text.lines == ("４０",) and d.text.encoding == "cp932"
    assert d.line == ((0.0, -10.0), (40.0, -10.0)) and d.underline == (3.0, 1.0)
    assert (d.arrow_width, round(d.arrow_angle, 6), d.gap, d.scale_factor) == (
        1.5,
        15.0,
        1.0,
        1.0,
    )
    assert text.status == "complete" and text.text is not None
    assert text.text.lines == ("品名",) and text.text.encoding == "cp932"
    assert text.text.base_point == 7 and text.text.direction == "horizontal"
    assert text.text.height == 4.0 and text.text.anchor is not None
    assert text.line_width == 2 and text.color_index == 1


def test_arc_diameter_variant_is_interpreted():
    from icadkit.write import _item

    blocks = _block(6, 80, [(48, 12.0, "<")]) + _block(
        4, 48, [(24, 1.5, "<"), (40, 0.2618, "<")]
    )
    header = _legacy_header("little", 27, 11, len(blocks))
    center, r = (30.0, 40.0), 6.0
    items = _item("<", 0x0500, 3, 1, (*center, r, 0.0, math.pi / 2))
    items += _item("<", 0x0544, 3, 1, (*center, r, 5.3, 2.4))
    items += _item("<", 0x0500, 3, 1, (*center, r, 4.1, 1.2))
    items += _item("<", 0x0100, 3, 1, (50.0, 60.0), point=True)
    items += _legacy_run("little", "φ１２", origin=(52.0, 61.0))
    half = 0.4
    items += _item("<", 0x0344, 3, 1, (34.0, 44.0, 0.24, 0.24, 0.6, -0.6))
    items += _item("<", 0x0344, 3, 1, (26.0, 36.0, -0.24, -0.24, -0.6, 0.6))
    items += _item("<", 0x0244, 3, 1, (26.0, 36.0, 0.7071, 0.7071, 11.3))
    items += _item("<", 0x0100, 3, 1, (50.0, 60.0), point=True)
    items += _item("<", 0x0100, 3, 1, (0.0, 0.0), point=True)
    record = bytearray(header) + b"\0" * 8 + blocks + items
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x02"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.dimension.kind == "diameter"
    assert e.dimension.center == center and e.dimension.radius == r
    assert e.dimension.pick_point == (50.0, 60.0) and e.dimension.line_point == (
        50.0,
        60.0,
    )
    assert e.dimension.line[0] == (26.0, 36.0) and half == 0.4


def test_point_with_attribute_record_reads_as_a_point():
    point = bytearray(entity(1, order="little", values=(0.0, 30.0)))
    struct.pack_into("<H", point, 24, 32)
    nested = bytearray(72)
    struct.pack_into("<H", nested, 0, 72)
    nested[2:4] = b"\0\xfd"
    nested[4:8] = b"\0\0\0\x01"
    record = point + nested
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x01"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.primitive.kind == "point"
    assert e.primitive.points == ((0.0, 30.0),) and e.byte_range.length == 128


def _line_information_record(name, items):
    header = bytearray(32)
    header[4] = 1
    header[12] = 0x40
    header[14] = 7
    header[15] = 224
    struct.pack_into("<I", header, 16, 0x80000303)
    header[24:32] = bytes.fromhex("080200fe00000001")
    body = bytearray(name.encode("utf-16-le")) + b"\0" * (300 - 2 * len(name))
    record = header + body + items
    struct.pack_into("<I", record, 0, len(record))
    return bytes(record)


def test_line_information_records_read_their_items_and_name():
    from icadkit.write import _item

    items = _item("<", 0x0100, 2, 1, (385.0, 310.0), point=True)
    items += _item("<", 0x0200, 2, 1, (386.0, 310.0, 0.0, 1.0, 1.0))
    items += _item("<", 0x0244, 2, 1, (385.0, 310.0, 1.0, 0.0, 42.0))
    items += _item("<", 0x0444, 2, 1, (385.0, 310.0, 0.5, 0.0, math.tau))
    e = (
        icadkit.read(
            drawing(
                [_line_information_record("Line", items)],
                order="little",
                version=b"\0\x08\0\x01",
            )
        )
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.symbol is not None and e.symbol.name == "Line"
    kinds = [(i.kind, i.raw_marker) for i in e.symbol.items]
    assert kinds == [
        ("point", 0x0100),
        ("line", 0x0200),
        ("line", 0x0244),
        ("arc", 0x0444),
    ]
    assert e.symbol.items[2].points == ((385.0, 310.0), (427.0, 310.0))
    assert e.symbol.items[3].radius == 0.5 and e.line_width == 2
    # without a framed item stream the record is an attribute record
    bare = _line_information_record("Cable", b"")
    e = (
        icadkit.read(drawing([bare], order="little", version=b"\0\x08\0\x01"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "unsupported" and e.symbol is None
    assert e.diagnostics[0].code == "drawing.attribute_record"


def test_motion_records_are_attribute_records():
    record = bytearray(216)
    record[4] = 1
    record[12] = 0x40
    record[15] = 230
    struct.pack_into("<I", record, 16, 0x80000304)
    record[24:32] = bytes.fromhex("300000fd00000001")
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x01"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "unsupported" and e.raw_type == 230
    assert [d.code for d in e.diagnostics] == ["drawing.attribute_record"]


# --- legacy hatch, half-arrow dimensions, old notes, text blocks, motion ------


def test_legacy_big_endian_hatch_reads_its_rendered_lines():
    values = [10.0, 20.0, 1.0, 0.0, 0.0, 0.0, 5.0, 0.0, 2.0, 5.0, 0.0, 4.0, 5.0]
    record = bytearray(32 + 8 * len(values))
    struct.pack_into(">I", record, 0, len(record))
    record[4] = 1
    record[12] = 0x40
    record[14] = 1
    record[15] = 92
    struct.pack_into(">I", record, 16, 0x80000501)
    struct.pack_into(">H", record, 24, len(record) - 24)
    record[26:32] = bytes([0x44, 9, 3, 0x16, 0, 0])
    struct.pack_into(f">{len(values)}d", record, 32, *values)
    e = (
        icadkit.read(drawing([bytes(record)], order="big", version=b"\0\x01\0\x09"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.hatch is not None
    h = e.hatch
    assert h.origin == (10.0, 20.0) and h.direction == (1.0, 0.0) and h.angle == 0.0
    assert h.segments == (
        ((10.0, 20.0), (15.0, 20.0)),
        ((10.0, 22.0), (15.0, 22.0)),
        ((10.0, 24.0), (15.0, 24.0)),
    )
    assert h.spacing == 2.0 and h.edges == () and h.raw_points == ()
    assert h.box == (10.0, 20.0, 15.0, 24.0)
    assert (e.line_width, e.line_style, e.color_index) == (3, 1, 6)


def test_chamfer_dimension_reads_its_size_and_leader():
    from icadkit.write import _item

    record = bytearray(_legacy_length_dimension("big"))
    record[15] = 29
    # the observed layout ends with a hidden line after the measured points
    record += _item(">", 0x0200, 3, 1, (0.0, -10.0, 1.0, 0.0, 40.0))
    struct.pack_into(">I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="big", version=b"\0\x01\0\x09"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.dimension.kind == "chamfer"
    assert (
        e.dimension.measured == ((0.0, 0.0), (40.0, 0.0)) and e.dimension.value == 40.0
    )
    assert e.dimension.line == ((0.0, -10.0), (40.0, -10.0))
    assert [i.kind for i in e.dimension.items][-1] == "line"


def test_old_notes_and_leaders_without_text():
    from icadkit.write import _item

    blocks = (
        _block(9, 48, [])
        + _block(11, 48, [])
        + _block(4, 48, [(24, 1.5, ">"), (40, 0.2618, ">")])
    )
    header = _legacy_header("big", 34, 4, len(blocks))
    items = _legacy_run("big", "注記", origin=(30.0, 31.0))
    items += _item(">", 0x0344, 3, 1, (12.0, 11.0, -2.0, -1.0, -1.0, 1.0))
    items += _item(">", 0x0344, 3, 1, (10.0, 10.0, 20.0, 20.0))
    items += _item(">", 0x0444, 3, 1, (34.0, 33.0, 5.0, 0.0, math.tau))  # the balloon
    record = bytearray(header) + b"\0" * 8 + blocks + items
    struct.pack_into(">I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="big", version=b"\0\x03\0\x04"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.note is not None
    assert e.note.circle == ((34.0, 33.0), 5.0)
    assert e.note.text.lines == ("注記",) and e.note.path == (
        (10.0, 10.0),
        (30.0, 30.0),
    )
    assert e.note.arrow is not None and e.note.arrow[1] == (10.0, 10.0)
    # a leader without text (type 44) keeps its lines with a partial status
    header = _legacy_header("big", 44, 4, len(blocks))
    items = _item(">", 0x0344, 3, 1, (12.0, 11.0, -2.0, -1.0, -1.0, 1.0))
    items += _item(">", 0x0344, 3, 1, (10.0, 10.0, 20.0, 20.0))
    record = bytearray(header) + b"\0" * 8 + blocks + items
    struct.pack_into(">I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="big", version=b"\0\x05\0\x04"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "partial" and e.note is not None and e.note.text is None
    assert e.note.path == ((10.0, 10.0), (30.0, 30.0))
    assert [d.code for d in e.note.diagnostics] == ["drawing.note_text"]


def test_text_blocks_and_motion_records_read_their_runs_and_items():
    from icadkit.write import _item

    # type 14: a block of text lines (runs only)
    blocks = _block(26, 48, []) + _block(27, 24, [])
    header = _legacy_header("big", 14, 1, len(blocks))
    runs = _legacy_run("big", "部品表", origin=(0.0, 0.0)) + _legacy_run(
        "big", "No.1", origin=(0.0, -5.0)
    )
    record = bytearray(header) + b"\0" * 8 + blocks + runs
    struct.pack_into(">I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="big", version=b"\0\x01\0\x09"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.symbol is not None
    assert [t.lines for t in e.symbol.texts] == [("部品表",), ("No.1",)]
    # type 230: unframed words, then line items reaching the end of the record
    header = bytearray(32)
    header[4] = 1
    header[12] = 0x40
    header[15] = 230
    struct.pack_into("<I", header, 16, 0x80000601)
    header[24:32] = bytes.fromhex("300000fd00000001")
    body = bytes.fromhex("0100000001000000") + b"\0" * 32
    body += _item("<", 0x0244, 2, 1, (245.0, -35.0, 0.7071, -0.7071, 28.284))
    record = header + body
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x01"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.symbol is not None and e.symbol.name is None
    assert [i.kind for i in e.symbol.items] == ["line"]
    # type 234: a UTF-16 name at +36 and a label run at the end
    header[15] = 234
    header[24:32] = bytes.fromhex("680000fd00000001")
    name = "アーム".encode("utf-16-le")
    body = bytes.fromhex("05000000") + name + b"\0" * (100 - len(name))
    body += _legacy_run("little", "x", origin=(1.0, 2.0))
    body = body[: -len(_legacy_run("little", "x", origin=(1.0, 2.0)))]
    from icadkit.write import _run

    body += _run("<", "アーム", (45.1, -49.0), 2.4, 2, 1, flags=0xF1)
    record = header + body
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x01"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.symbol.name == "アーム"
    assert [t.lines for t in e.symbol.texts] == [("アーム",)]


def test_motion_circle_with_a_data_chunk_reads_as_a_circle():
    circle = bytearray(
        entity(6, order="little", values=(275.0, -55.0, 1.8, 0.0, math.tau))
    )
    circle[15] = 232
    chunk = bytearray(1000)
    struct.pack_into("<H", chunk, 0, 1000)
    chunk[2:4] = b"\0\x09"
    record = circle + chunk
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x01"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.primitive.kind == "circle"
    assert e.primitive.center == (275.0, -55.0) and e.primitive.radius == 1.8


def test_text_with_a_trailing_attribute_chunk_reads_its_runs():
    record = bytearray(text_entity(lines=("ＦＲＯＮＴ",)))
    chunk = bytearray(88)
    struct.pack_into("<H", chunk, 0, 88)
    chunk[2:4] = b"\x44\x20"  # framed like a run, but no run flags
    record += chunk
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x07\0\x05"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.text.lines == ("ＦＲＯＮＴ",)


def test_hidden_runs_inside_composites_are_skipped():
    from icadkit.write import _item, _run

    record = bytearray(_dimension_record())
    hidden = bytearray(_run("<", "0", (1.0, 2.0), 3.0, 2, 1))
    hidden[2:4] = b"\0\x20"
    # a hidden run before the measured points keeps the length layout readable
    tail = _item("<", 0x0100, 3, 1, (0.0, 0.0), point=True) + _item(
        "<", 0x0100, 3, 1, (40.0, 0.0), point=True
    )
    assert record.endswith(tail)
    record = record[: -len(tail)] + hidden + tail
    struct.pack_into("<I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.dimension.measured == ((0.0, 0.0), (40.0, 0.0))


# --- flag variants, three-vector items, old elliptical arcs, vector primitives


def test_utf16_runs_accept_the_flag_variants():
    from icadkit.write import _run

    for flags in (b"\x20\x00\x00\x01", b"\x20\x20\x00\x01", b"\x20\x40\x00\x01"):
        run = bytearray(_run("<", "品番", (1.0, 2.0), 3.0, 2, 1))
        run[48:52] = flags
        decoded = icadkit.drawing._run_chars(bytes(run), "little")
        assert decoded is not None and decoded[0] == "品番"
    run = bytearray(_run("<", "品番", (1.0, 2.0), 3.0, 2, 1))
    run[48:52] = b"\x20\x40\x01\x01"
    assert icadkit.drawing._run_chars(bytes(run), "little") is None


def test_surface_texture_symbols_read_their_three_vector_items():
    from icadkit.write import _item

    blocks = _block(35, 24, [])
    header = _legacy_header("big", 28, 1, len(blocks))
    items = _item(
        ">", 0x0344, 2, 1, (137.857, 112.091, 2.0, 3.464, -2.0, 3.464, 0.0, 0.0)
    )
    items += _item(
        ">", 0x0344, 2, 1, (141.857, 112.091, 2.0, 3.464, -2.0, 3.464, 0.0, 0.0)
    )
    record = bytearray(header) + b"\0" * 8 + blocks + items
    struct.pack_into(">I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="big", version=b"\0\x03\0\x04"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.symbol is not None
    assert [len(i.points) for i in e.symbol.items] == [4, 4]
    first = e.symbol.items[0].points
    assert first[0] == (137.857, 112.091) and first[3] == first[0]
    assert first[1] == pytest.approx((139.857, 115.555)) and first[2] == pytest.approx(
        (135.857, 115.555)
    )


def test_old_elliptical_arcs_read_as_curves_with_their_chunk():
    from icadkit import Curve2D

    writer = _sheet_writer("big", b"\0\x05\0\x01")
    writer.add_entities(
        "XY", [Curve2D(((0.0, 0.0), (10.0, 4.0), (20.0, -3.0), (30.0, 0.0)))]
    )
    (curve,), doc = _entities(writer, (16,))
    record = bytearray(doc.source_bytes(curve.byte_range))
    record[15] = 89
    chunk = bytearray(96)
    struct.pack_into(">H", chunk, 0, 96)
    record += chunk
    struct.pack_into(">I", record, 0, len(record))
    e = (
        icadkit.read(drawing([bytes(record)], order="big", version=b"\0\x05\0\x01"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.curve is not None and e.raw_type == 89
    assert e.curve.knots == ((0.0, 0.0), (10.0, 4.0), (20.0, -3.0), (30.0, 0.0))
    assert e.byte_range.length == 352 + 96


def test_vector_primitives_read_their_two_segments():
    record = bytearray(80)
    struct.pack_into("<I", record, 0, 80)
    record[4] = 1
    record[12] = 0x40
    record[14] = 1
    record[15] = 12
    struct.pack_into("<I", record, 16, 0x80000801)
    struct.pack_into("<H", record, 24, 56)
    record[26:32] = bytes([0x44, 3, 3, 0x11, 0, 0])
    struct.pack_into("<6d", record, 32, 165.0, 466.0, -2.0, -1.5, -1.0, 1.0)
    e = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x07\0\x07"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "complete" and e.primitive.kind == "vectors"
    assert e.primitive.points == ((165.0, 466.0), (163.0, 464.5), (164.0, 467.0))
    assert (e.line_width, e.line_style, e.color_index) == (3, 1, 1)


@pytest.mark.parametrize(
    "order,version", [("big", b"\0\x05\0\x01"), ("little", b"\0\x07\0\x03")]
)
def test_old_elliptical_arcs_with_the_projection_word_read_as_curves(order, version):
    from icadkit import Curve2D

    prefix = "<" if order == "little" else ">"
    writer = _sheet_writer(order, version)
    writer.add_entities(
        "XY", [Curve2D(((0.0, 0.0), (10.0, 4.0), (20.0, -3.0), (30.0, 0.0)))]
    )
    (curve,), doc = _entities(writer, (16,))
    record = bytearray(doc.source_bytes(curve.byte_range))
    record[15] = 89
    # the word 0x0090 at +6 in the file byte order (3D-derived drawings)
    struct.pack_into(prefix + "H", record, 6, 0x90)
    chunk = bytearray(48)
    struct.pack_into(prefix + "H", chunk, 0, 48)
    record += chunk
    struct.pack_into(prefix + "I", record, 0, len(record))

    def read(raw):
        return (
            icadkit.read(drawing([raw], order=order, version=version))
            .read_drawing()
            .entities[0]
        )

    e = read(bytes(record))
    assert e.status == "complete" and e.curve is not None and e.raw_type == 89
    assert e.curve.knots == ((0.0, 0.0), (10.0, 4.0), (20.0, -3.0), (30.0, 0.0))
    # the word is only accepted for that record shape
    record[15] = 16
    assert read(bytes(record)).status == "unsupported"


@pytest.mark.parametrize(
    "order,version", [("big", b"\0\x01\0\x09"), ("little", b"\0\x07\0\x02")]
)
def test_underlined_view_labels_read_their_line_item(order, version):
    from icadkit.write import _item

    prefix = "<" if order == "little" else ">"
    record = bytearray(_legacy_text(order, "Ａ－Ａ"))
    record += _item(prefix, 0x0244, 2, 1, (10.0, 18.0, 1.0, 0.0, 12.0))
    struct.pack_into(prefix + "I", record, 0, len(record))

    def read(raw):
        return (
            icadkit.read(drawing([raw], order=order, version=version))
            .read_drawing()
            .entities[0]
        )

    # a plain text block with a line item appended: the underline is read but
    # the fixed words are those of a plain text, so the layout is partial
    e = read(bytes(record))
    assert e.status == "partial" and e.text is not None
    assert e.text.underline == ((10.0, 18.0), (22.0, 18.0))
    assert [d.code for d in e.text.diagnostics] == ["drawing.text_fields"]
    # the observed words of underlined labels: +14 = 3, +46 counts the run
    # and the line item, +60 = 0x40 and +76 = 03 01
    record[14] = 3
    record[46] = 2
    record[60] = 0x40
    record[76:78] = bytes([3, 1])
    e = read(bytes(record))
    assert e.status == "complete" and e.text is not None
    assert e.text.lines == ("Ａ－Ａ",)
    assert e.text.underline == ((10.0, 18.0), (22.0, 18.0))
    assert e.line_width == 2 and e.color_index == 1
    # a truncated tail still fails the record
    bad = bytearray(record[:-8])
    struct.pack_into(prefix + "I", bad, 0, len(bad))
    e = read(bytes(bad))
    assert e.status == "unsupported" and e.text is None


def test_three_d_projection_frames_are_attribute_records():
    record = bytearray(96)
    struct.pack_into(">I", record, 0, 96)
    record[4] = 1
    record[15] = 211
    struct.pack_into(">I", record, 16, 0x800003D3)
    struct.pack_into(">H", record, 24, 72)
    record[26:28] = b"\0\xfd"
    record[28:32] = b"\0\0\0\xff"
    struct.pack_into(">2I", record, 32, 1, 1)
    struct.pack_into(">6d", record, 48, 0.7071, -0.4082, 0.5774, 0.0, 0.8165, 0.5774)
    e = (
        icadkit.read(drawing([bytes(record)], order="big", version=b"\0\x05\0\x04"))
        .read_drawing()
        .entities[0]
    )
    assert e.status == "unsupported" and e.primitive is None and e.raw_type == 211
    assert any(d.code == "drawing.attribute_record" for d in e.diagnostics)


# --- leader items with more than three points, per-entity decode guard (0.3.12) ----


def test_leader_note_with_an_extra_elbow_is_read_as_a_four_point_path():
    # A 2026 production drawing (iCAD SX 2D, type 22) saved a leader whose drawn item
    # has four points: the tip, two elbows and the end of the shelf. 0.3.11 unpacked
    # exactly three and the whole drawing failed with "too many values to unpack".
    from icadkit import LeaderNote2D
    from icadkit.write import _item

    writer = _sheet_writer()
    writer.add_entities(
        "XY", [LeaderNote2D(("注記",), (50.0, 50.0), (70.0, 70.0), (100.0, 70.0))]
    )
    (e,), doc = _entities(writer, (22,))
    assert e.note.path == ((50.0, 50.0), (70.0, 70.0), (100.0, 70.0))
    record = bytearray(doc.source_bytes(e.byte_range))
    # the drawn leader is the last item: 8 bytes of framing + 6 doubles
    size = struct.unpack_from("<H", record, len(record) - 56)[0]
    marker = struct.unpack_from("<H", record, len(record) - 54)[0]
    assert (size, marker) == (56, 0x0344)
    del record[-56:]
    drawn = e.note.items[-1]
    record += _item(
        "<",
        0x0344,
        drawn.line_width,
        drawn.color_index,
        (50.0, 50.0, 20.0, 20.0, 30.0, 20.0, 50.0, 20.0),
    )
    struct.pack_into("<I", record, 0, len(record))
    again = (
        icadkit.read(drawing([bytes(record)], order="little", version=b"\0\x08\0\x03"))
        .read_drawing()
        .entities[0]
    )
    assert again.status == "complete" and again.note is not None
    assert again.note.path == (
        (50.0, 50.0),
        (70.0, 70.0),
        (80.0, 70.0),
        (100.0, 70.0),
    )
    assert again.note.arrow is not None and again.note.arrow[1] == (50.0, 50.0)
    assert again.note.box[2] >= 100.0


def test_an_entity_record_that_raises_does_not_fail_the_drawing(monkeypatch):
    # One record of an unobserved layout stays in place as ``invalid`` with the error;
    # the other entities of the drawing are still read
    from icadkit import LeaderNote2D
    from icadkit import drawing as drawing_module

    writer = _sheet_writer()
    writer.add_entities(
        "XY",
        [
            LeaderNote2D(("注記",), (50.0, 50.0), (70.0, 70.0), (100.0, 70.0)),
            icadkit.Line2D((0.0, 0.0), (10.0, 0.0)),
        ],
    )
    _, doc = _entities(writer, (22, 1))

    def broken(*args, **kwargs):
        raise ValueError("too many values to unpack (expected 3)")

    monkeypatch.setattr(drawing_module, "_note", broken)
    index = icadkit.read(writer.to_bytes()).read_drawing()
    assert [e.status for e in index.entities] == ["invalid", "complete"]
    bad = index.entities[0]
    assert bad.note is None and bad.primitive is None
    assert [d.code for d in bad.diagnostics] == ["drawing.entity_error"]
    assert "ValueError: too many values to unpack" in bad.diagnostics[0].message
    assert index.status == "invalid"
