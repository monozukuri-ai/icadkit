"""Synthetic checks of the 2D writer; nothing here has been opened by iCAD."""

import math
import struct

import pytest
from drawing_fixtures import drawing, entity, sheet, text_entity

import icadkit
from icadkit import (
    Arc2D,
    Circle2D,
    DiameterDimension2D,
    DrawingWriter,
    EntityStyle,
    Hatch2D,
    LengthDimension2D,
    Line2D,
    Point2D,
    Text2D,
)

ORDERS = [("little", b"\0\x08\0\x03"), ("big", b"\0\x03\0\x04")]


@pytest.mark.parametrize("order,version", ORDERS)
def test_entities_round_trip_through_the_reader(order, version):
    doc = icadkit.read(
        drawing(
            [entity(2, order=order)],
            order=order,
            version=version,
            extent=(13, -7, 37, 11),
        )
    )
    writer = DrawingWriter(doc)
    assert writer.views == ("!!GLOBAL",)
    style = EntityStyle(layer=3, line_width=1, line_style=2, color_index=7)
    ids = writer.add_entities(
        "!!GLOBAL",
        [
            Point2D((1, 2)),
            Line2D((0, 0), (3, 4)),
            Circle2D((5, 5), 2),
            Arc2D((0, 0), 1, 0, math.pi / 2),
        ],
        style=style,
    )
    assert ids == (0x80000124, 0x80000125, 0x80000126, 0x80000127)
    out = writer.to_bytes()
    again = icadkit.read(out)
    view = again.read_views().views[0]
    result = again.read_drawing()
    assert view.status == "complete" and result.status == "complete"
    assert [e.primitive.kind for e in result.entities] == [
        "line",
        "point",
        "line",
        "circle",
        "arc",
    ]
    assert view.entity_count == 5
    assert view.raw_entity_words == (4 + 72 + 56 + 72 * 3) // 4
    assert view.extent == icadkit.ViewExtent(0, -7, 37, 11)
    point, line, circle, arc = result.entities[1:]
    assert point.primitive.points == ((1, 2),)
    assert line.primitive.points == ((0, 0), (3, 4)) and line.primitive.length == 5
    assert line.primitive.direction == (0.6, 0.8)
    assert circle.primitive.center == (5, 5) and circle.primitive.radius == 2
    assert arc.primitive.sweep_angle == pytest.approx(math.pi / 2)
    for e in (point, line, circle, arc):
        assert (e.layer, e.visible) == (3, True)
        assert (e.line_width, e.line_style, e.color_index) == (1, 2, 7)
        assert e.source_id in ids
    assert again.container().to_bytes() == out  # The copy is itself a container.
    assert len(out) == len(doc.to_bytes()) + 56 + 72 * 3  # The marker existed.


@pytest.mark.parametrize("order,version", ORDERS)
def test_new_view_is_cloned_from_a_two_d_view(order, version):
    doc = icadkit.read(
        drawing(
            [entity(6, order=order)],
            order=order,
            version=version,
            name=b"!XY",
            number=2,
        )
    )
    writer = DrawingWriter(doc)
    assert writer.add_view("FRONT", scale=0.5) == "FRONT"
    writer.add_entities("FRONT", [Line2D((-1, -2), (3, 4))])
    with pytest.raises(ValueError, match="already exists"):
        writer.add_view("FRONT")
    out = writer.to_bytes()
    again = icadkit.read(out)
    views = again.read_views()
    assert views.status == "complete"
    xy, front = views.views
    assert (xy.name, xy.raw_view_number, xy.entity_count) == ("!XY", 2, 1)
    assert (front.name, front.kind, front.raw_view_number) == ("FRONT", "2d_view", 3)
    assert front.scale == 0.5 and front.raw_scale_text == b"1/2     "
    assert front.entity_count == 1 and front.raw_entity_words == (4 + 72) // 4
    assert front.extent == icadkit.ViewExtent(-1, -2, 3, 4)
    assert again.container().view_names == (b"!XY     ", b"FRONT   ")
    assert [r.tag for r in again.records] == ["MOD", "DRW", "RES", "V/W", "V/W"]
    result = writer.result()
    assert result.added_views == ("FRONT",) and result.added_entities == 1
    assert result.verification == "corpus_consistent"
    # The fixture's lone view has no 2D global view with a placement list.
    assert [d.code for d in result.diagnostics] == ["write.placement"]


def test_global_view_template_and_empty_new_view():
    writer = DrawingWriter(icadkit.read(drawing()))
    writer.add_view("A")
    again = icadkit.read(writer.to_bytes())
    new = again.read_views().views[1]
    assert new.kind == "2d_view" and new.raw_view_number == 2
    assert new.entity_count == 0 and new.extent_kind == "empty"
    assert new.scale == 1.0 and new.raw_scale_text == b"1/1     "
    assert again.read_drawing().status == "complete"


def test_unchanged_writer_reproduces_its_input():
    data = drawing([entity(2), entity(5)], extent=(1, 2, 3, 4))
    assert DrawingWriter(icadkit.read(data)).to_bytes() == data


def test_raw_record_keeps_the_stored_extent_and_may_keep_its_id():
    data = drawing(
        [entity(2, order="little")],
        order="little",
        version=b"\0\x08\0\x03",
        extent=(13, -7, 37, 11),
    )
    writer = DrawingWriter(icadkit.read(data))
    dim = bytearray(entity(order="little"))
    dim[15] = 25  # An annotation record the reader does not decompose.
    struct.pack_into("<I", dim, 16, 0x80000456)
    kept = writer.add_raw_entity("!!GLOBAL", bytes(dim), keep_id=True)
    fresh = writer.add_raw_entity("!!GLOBAL", bytes(dim))
    assert kept == 0x80000456 and fresh == 0x80000457
    result = writer.result()
    assert [d.code for d in result.diagnostics] == ["write.extent_kept"]
    again = icadkit.read(result.payload)
    view = again.read_views().views[0]
    assert view.entity_count == 3 and view.extent == icadkit.ViewExtent(13, -7, 37, 11)
    raw = [e for e in again.read_drawing().entities if e.raw_type == 25]
    assert [e.source_id for e in raw] == [kept, fresh]
    writer.set_extent("!!GLOBAL", (0, 0, 50, 50))
    assert icadkit.read(writer.to_bytes()).read_views().views[0].extent == (
        icadkit.ViewExtent(0, 0, 50, 50)
    )
    assert not writer.result().diagnostics


def test_raw_text_record_contributes_its_box():
    data = drawing(
        [entity(2, order="little")],
        order="little",
        version=b"\0\x08\0\x03",
        extent=(13, -7, 37, 11),
    )
    writer = DrawingWriter(icadkit.read(data))
    writer.add_raw_entity("!!GLOBAL", text_entity(origin=(50, 50)))
    assert not writer.result().diagnostics
    view = icadkit.read(writer.to_bytes()).read_views().views[0]
    assert view.extent == icadkit.ViewExtent(13, -7, 58, 54)


def test_text_round_trips_through_the_reader():
    data = drawing(
        [entity(2, order="little")],
        order="little",
        version=b"\0\x08\0\x03",
        extent=(13, -7, 37, 11),
    )
    doc = icadkit.read(data)
    writer = DrawingWriter(doc)
    writer.add_entities(
        "!!GLOBAL",
        [
            Text2D(
                ("検証用プレート", "材質未指定"),
                (0, 40),
                4,
                direction="vertical",
                row_space=2.5,
            ),
            Text2D("ABC", (5, 5), 2.0, base_point=7, way=2),
        ],
    )
    out = writer.to_bytes()
    again = icadkit.read(out)
    result = again.read_drawing()
    assert result.status == "complete"
    vertical, horizontal = result.entities[1].text, result.entities[2].text
    assert vertical.layout_status == "complete" and vertical.direction == "vertical"
    assert vertical.lines == ("検証用プレート", "材質未指定")
    assert vertical.line_origins == ((6.5, 36), (0, 36))
    assert vertical.anchor == (0, 40) and vertical.box == (0, 12, 10.5, 40)
    assert (vertical.height, vertical.char_width, vertical.char_pitch) == (4, 4, 4)
    assert (
        vertical.scale_factor == 1
        and vertical.base_point == 1
        and not vertical.glyph_cache
    )
    assert horizontal.anchor == (5, 5) and horizontal.box == (5, 5, 8, 7)
    assert horizontal.raw_way == 2 and horizontal.line_origins == ((5, 5),)
    assert again.read_views().views[0].extent == icadkit.ViewExtent(0, -7, 37, 40)
    assert len(out) == len(data) + (88 + 72 + 72) + (88 + 64)
    assert not writer.result().diagnostics


@pytest.mark.parametrize(
    "text,error",
    [
        (Text2D(("a", "b", "c"), (0, 0), 4), ValueError),
        (Text2D(("a", ""), (0, 0), 4), ValueError),
        (Text2D("a\nb", (0, 0), 4), ValueError),
        (Text2D("\U0001f600", (0, 0), 4), ValueError),
        (Text2D("a", (0, 0), 0), ValueError),
        (Text2D("a", (0, 0), 4, base_point=10), ValueError),
        (Text2D("a", (0, 0), 4, direction="diagonal"), ValueError),
        (Text2D("a", (0, 0), 4, way=5), ValueError),
        (Text2D("a", (0, 0), 4, row_space=-1), ValueError),
    ],
)
def test_text_requests_are_validated(text, error):
    writer = DrawingWriter(
        icadkit.read(
            drawing(
                [entity(2, order="little")], order="little", version=b"\0\x08\0\x03"
            )
        )
    )
    with pytest.raises(error):
        writer.add_entities("!!GLOBAL", [text])


def test_text_needs_a_little_endian_document():
    writer = DrawingWriter(icadkit.read(drawing([entity()])))
    with pytest.raises(icadkit.UnsupportedFormatError) as info:
        writer.add_entities("!!GLOBAL", [Text2D("a", (0, 0), 4)])
    assert info.value.diagnostic.code == "write.text_order"


@pytest.mark.parametrize(
    "call,error",
    [
        (lambda w: w.add_entities("!!GLOBAL", [Line2D((0, 0), (0, 0))]), ValueError),
        (lambda w: w.add_entities("!!GLOBAL", [Circle2D((0, 0), 0)]), ValueError),
        (lambda w: w.add_entities("!!GLOBAL", [Arc2D((0, 0), 1, 0, 0)]), ValueError),
        (lambda w: w.add_entities("!!GLOBAL", [Arc2D((0, 0), 1, 0, 7)]), ValueError),
        (lambda w: w.add_entities("!!GLOBAL", [Point2D((math.nan, 0))]), ValueError),
        (lambda w: w.add_entities("!!GLOBAL", [Point2D(("1", 0))]), TypeError),
        (lambda w: w.add_entities("!!GLOBAL", ["line"]), TypeError),
        (lambda w: w.add_entities("missing", [Point2D((0, 0))]), KeyError),
        (lambda w: w.add_view("too long name"), ValueError),
        (lambda w: w.add_view(""), ValueError),
        (lambda w: w.add_view("A", scale=0.3), ValueError),
        (lambda w: w.add_raw_entity("!!GLOBAL", b"\0" * 20), ValueError),
        (lambda w: w.set_extent("!!GLOBAL", (1, 0, 0, 0)), ValueError),
        (lambda w: EntityStyle(layer=256), ValueError),
        (lambda w: EntityStyle(color_index=-1), ValueError),
        (lambda w: EntityStyle(visible=1), TypeError),
    ],
)
def test_invalid_requests_are_rejected_before_writing(call, error):
    writer = DrawingWriter(icadkit.read(drawing([entity(2)])))
    with pytest.raises(error):
        call(writer)
    assert writer.to_bytes() == drawing([entity(2)])


def test_unobserved_view_numbers_and_incomplete_indexes_are_refused():
    writer = DrawingWriter(icadkit.read(drawing(name=b"!XY", number=6)))
    with pytest.raises(icadkit.UnsupportedFormatError) as info:
        writer.add_view("SEVEN")
    assert info.value.diagnostic.code == "write.view_number"
    partial = icadkit.read(drawing([entity(2)], count=9))
    with pytest.raises(icadkit.UnsupportedFormatError) as info:
        DrawingWriter(partial)
    assert info.value.diagnostic.code == "write.views"


def test_write_refuses_existing_paths(tmp_path):
    writer = DrawingWriter(icadkit.read(drawing([entity(2)])))
    target = tmp_path / "図面.icd"
    result = writer.write(target)
    assert target.read_bytes() == result.payload and result.path == target
    with pytest.raises(FileExistsError):
        writer.write(target)


def test_entity_ids_continue_after_every_known_record_id():
    control = bytearray(264)
    control[:4] = (264).to_bytes(4, "big")
    control[12:16] = b"\x40\0\x01\xcb"
    control[16:20] = (0x80000777).to_bytes(4, "big")
    control[24:26] = (240).to_bytes(2, "big")
    control[26:28] = b"\x40\xfd"
    stream = (0x40000000).to_bytes(4, "big") + bytes(control)
    stream += (0x30010000).to_bytes(4, "big") + entity()
    writer = DrawingWriter(icadkit.read(drawing(stream=stream, count=1)))
    assert writer.add_entities("!!GLOBAL", [Point2D((0, 0))]) == (0x80000778,)


def test_length_dimension_round_trips_through_the_reader():
    data = drawing(
        [entity(2, order="little")],
        order="little",
        version=b"\0\x08\0\x03",
        extent=(13, -7, 37, 11),
    )
    writer = DrawingWriter(icadkit.read(data))
    (entity_id,) = writer.add_entities(
        "!!GLOBAL", [LengthDimension2D((0, 0), (40, 0), (20, -10))]
    )
    out = writer.to_bytes()
    again = icadkit.read(out)
    result = again.read_drawing()
    assert result.status == "complete"
    e = result.entities[1]
    d = e.dimension
    assert e.raw_type == 25 and e.source_id == entity_id and e.byte_range.length == 840
    assert d.kind == "length" and d.layout_status == "complete" and not d.diagnostics
    assert d.value == 40 and d.text.lines == ("４０",)
    assert d.text.line_origins == ((16, -9),) and d.text.rotation == 0
    assert d.measured == ((0, 0), (40, 0)) and d.line == ((0, -10), (40, -10))
    assert d.line_point == (20, -10) and d.extension == pytest.approx(2.8)
    assert (
        d.arrow_width == 1.5
        and d.arrow_angle == pytest.approx(15)
        and d.gap == 1
        and d.scale_factor == 1
    )
    assert d.underline == (3, 1)
    assert [i.kind for i in d.items] == [
        "vectors",
        "vectors",
        "line",
        "point",
        "point",
        "vectors",
        "vectors",
        "point",
        "point",
    ]
    corner, tip, other = d.items[0].points
    assert tip == (0, -10) and corner == pytest.approx((1.5, -10.401924))
    assert other == pytest.approx((1.5, -9.598076))
    assert d.items[5].points == ((0, 0), (0, -10), (0, pytest.approx(-12.8)))
    assert d.items[4].points == ((20, -9),)
    assert (e.line_width, e.line_style, e.color_index) == (3, 1, 1)
    assert d.box == (0, pytest.approx(-12.8), 40, 0)
    assert again.read_views().views[0].extent == icadkit.ViewExtent(
        0, pytest.approx(-12.8), 40, 11
    )
    record = again.source_bytes(e.byte_range)
    assert (
        record[32:376]
        == bytes.fromhex(
            "0a0008000000b400ff01013800020004e40020040000000000000000000000000000"
        )
        + record[66:376]
    )


def test_vertical_and_scaled_length_dimensions():
    data = drawing(
        [entity(2, order="little")],
        order="little",
        version=b"\0\x08\0\x03",
        name=b"!XY",
        number=2,
        scale=0.5,
    )
    writer = DrawingWriter(icadkit.read(data))
    writer.add_entities(
        "!XY",
        [
            LengthDimension2D((0, 0), (0, 30), (-10, 15), orientation="vertical"),
            LengthDimension2D(
                (0, 0), (30, 40), (15, 20), orientation="horizontal", text="Ｌ"
            ),
        ],
    )
    result = icadkit.read(writer.to_bytes()).read_drawing()
    vertical, horizontal = result.entities[1].dimension, result.entities[2].dimension
    assert vertical.layout_status == "complete" and vertical.value == 30
    assert vertical.line == ((-10, 0), (-10, 30)) and vertical.text.rotation == 90
    assert vertical.text.line_origins == ((-12, 7),)  # anchor (-12, 15), width 16
    assert vertical.scale_factor == 0.5 and vertical.text.height == 8
    assert vertical.extension == pytest.approx(5.6)
    assert horizontal.value == 30 and horizontal.text.lines == ("Ｌ",)
    assert horizontal.line == ((0, 20), (30, 20))
    assert horizontal.measured == ((0, 0), (30, 40))


@pytest.mark.parametrize(
    "dim,error",
    [
        (LengthDimension2D((0, 0), (0, 0), (0, 1)), ValueError),
        (
            LengthDimension2D((0, 0), (0, 5), (1, 1), orientation="horizontal"),
            ValueError,
        ),
        (LengthDimension2D((0, 0), (5, 0), (0, 1), text=""), ValueError),
        (LengthDimension2D((0, 0), (5, 0), (0, 1), arrow_angle=90), ValueError),
        (LengthDimension2D((0, 0), (5, 0), (0, 1), height=0), ValueError),
        (LengthDimension2D((0, 0), (5, 0), (0, 1), orientation="diagonal"), ValueError),
        (LengthDimension2D((0, 0), (5, 0), (0, 1), line_width=300), ValueError),
        (LengthDimension2D((0, 0), (5, 0), (2.5, 1)), ValueError),  # "５" does not fit.
        (LengthDimension2D((0, 0), (9, 0), (4.5, 1), aux_offset=-1), ValueError),
    ],
)
def test_dimension_requests_are_validated(dim, error):
    writer = DrawingWriter(
        icadkit.read(
            drawing(
                [entity(2, order="little")], order="little", version=b"\0\x08\0\x03"
            )
        )
    )
    with pytest.raises(error):
        writer.add_entities("!!GLOBAL", [dim])


def test_raw_dimension_record_contributes_its_box():
    data = drawing(
        [entity(2, order="little")],
        order="little",
        version=b"\0\x08\0\x03",
        extent=(13, -7, 37, 11),
    )
    source = DrawingWriter(icadkit.read(data))
    source.add_entities("!!GLOBAL", [LengthDimension2D((50, 50), (90, 50), (70, 40))])
    record = icadkit.read(source.to_bytes()).read_drawing().entities[1]
    raw = icadkit.read(source.to_bytes()).source_bytes(record.byte_range)
    writer = DrawingWriter(icadkit.read(data))
    writer.add_raw_entity("!!GLOBAL", raw)
    assert not writer.result().diagnostics
    view = icadkit.read(writer.to_bytes()).read_views().views[0]
    assert view.extent == icadkit.ViewExtent(13, -7, 90, 50)


def test_dimension_text_is_centred_between_the_arrowheads():
    data = drawing([entity(2, order="little")], order="little", version=b"\0\x08\0\x03")
    writer = DrawingWriter(icadkit.read(data))
    writer.add_entities(
        "!!GLOBAL", [LengthDimension2D((0, 0), (40, 0), (30, 5), aux_offset=1.0)]
    )
    d = icadkit.read(writer.to_bytes()).read_drawing().entities[1].dimension
    assert d.line_point == (30, 5) and d.items[4].points == ((20, 6),)
    assert d.text.line_origins == ((16, 6),)
    assert d.line == ((0, 5), (40, 5)) and d.measured == ((0, 0), (40, 0))
    assert d.items[5].points == ((0, 1), (0, 5), (0, pytest.approx(7.8)))


def test_hatch_round_trips_through_the_reader():
    data = drawing(
        [entity(2, order="little")],
        order="little",
        version=b"\0\x08\0\x03",
        extent=(13, -7, 37, 11),
    )
    writer = DrawingWriter(icadkit.read(data))
    (entity_id,) = writer.add_entities(
        "!!GLOBAL",
        [Hatch2D(((0, 0), (40, 0), (40, 30), (0, 30)))],
        style=EntityStyle(line_width=3),
    )
    out = writer.to_bytes()
    again = icadkit.read(out)
    e = again.read_drawing().entities[1]
    h = e.hatch
    assert e.raw_type == 92 and e.source_id == entity_id and e.status == "complete"
    assert e.byte_range.length == 624 and (e.line_width, e.color_index) == (3, 1)
    assert h.layout_status == "complete" and not h.diagnostics
    assert h.anchor == (0, 0) and h.angle == 45 and h.spacing == pytest.approx(3)
    assert [edge.kind for edge in h.edges] == ["line"] * 4
    assert h.edges[1] == icadkit.HatchEdge("line", (40, 0), (40, 15), (40, 30))
    assert h.raw_points[:3] == ((20, 0), (40, 0), (40, 15)) and h.raw_points[-1] == (
        0,
        0,
    )
    assert len(h.segments) == 17 and h.origin == pytest.approx((38.183766, 0))
    assert h.segments[0][0] == pytest.approx((38.183766, 0))
    assert h.segments[0][1] == pytest.approx((40, 1.816234))
    assert h.segments[9] == (pytest.approx((0, 0)), pytest.approx((30, 30)))
    assert h.box == pytest.approx((0, 0, 40, 30))
    assert again.read_views().views[0].extent == pytest.approx(
        icadkit.ViewExtent(0, -7, 40, 30)
    )


def test_hatch_handles_concave_polygons_and_rejects_bad_input():
    data = drawing([entity(2, order="little")], order="little", version=b"\0\x08\0\x03")
    writer = DrawingWriter(icadkit.read(data))
    concave = ((0, 0), (10, 0), (10, 10), (5, 10), (5, 5), (0, 5))
    writer.add_entities(
        "!!GLOBAL", [Hatch2D(concave, angle=0, spacing=2.5, anchor=(0, 0))]
    )
    h = icadkit.read(writer.to_bytes()).read_drawing().entities[1].hatch
    assert h.layout_status == "complete" and h.angle == 0 and h.spacing == 2.5
    # Lines at y = 2.5 cross the full width; at y = 7.5 only the right arm.
    rounded = [tuple(tuple(round(v, 9) for v in p) for p in seg) for seg in h.segments]
    assert ((0, 2.5), (10, 2.5)) in rounded and ((5, 7.5), (10, 7.5)) in rounded
    assert sorted({seg[0][1] for seg in rounded}) == [2.5, 5, 7.5]
    for bad in (
        Hatch2D(((0, 0), (1, 1))),
        Hatch2D(((0, 0), (1, 1), (2, 2))),
        Hatch2D(((0, 0), (1, 0), (1, 1)), spacing=0),
        Hatch2D(((0, 0), (1, 0), (1, float("nan")))),
    ):
        with pytest.raises(ValueError):
            writer.add_entities("!!GLOBAL", [bad])


def test_raw_hatch_record_contributes_its_box():
    data = drawing(
        [entity(2, order="little")],
        order="little",
        version=b"\0\x08\0\x03",
        extent=(13, -7, 37, 11),
    )
    source = DrawingWriter(icadkit.read(data))
    source.add_entities("!!GLOBAL", [Hatch2D(((50, 50), (60, 50), (60, 58), (50, 58)))])
    produced = icadkit.read(source.to_bytes())
    raw = produced.source_bytes(produced.read_drawing().entities[1].byte_range)
    writer = DrawingWriter(icadkit.read(data))
    writer.add_raw_entity("!!GLOBAL", raw)
    assert not writer.result().diagnostics
    assert icadkit.read(writer.to_bytes()).read_views().views[0].extent == (
        icadkit.ViewExtent(13, -7, 60, 58)
    )


def ident(record, entity_id, order="big"):
    data = bytearray(record)
    struct.pack_into((">" if order == "big" else "<") + "I", data, 16, entity_id)
    return bytes(data)


def test_delete_entities_updates_count_words_and_extent():
    records = [
        ident(entity(2), 0x80000201),
        ident(entity(5), 0x80000202),
        ident(entity(1), 0x80000203),
    ]
    doc = icadkit.read(drawing(records, extent=(-20, -40, 56, 11)))
    writer = DrawingWriter(doc)
    assert writer.entity_ids("!!GLOBAL") == (0x80000201, 0x80000202, 0x80000203)
    with pytest.raises(KeyError):
        writer.delete_entities("!!GLOBAL", [0x80000999])
    with pytest.raises(TypeError):
        writer.delete_entities("!!GLOBAL", [True])
    assert writer.delete_entities("!!GLOBAL", [0x80000202, 0x80000203]) == 2
    assert writer.entity_ids("!!GLOBAL") == (0x80000201,)
    again = icadkit.read(writer.to_bytes())
    view = again.read_views().views[0]
    assert view.entity_count == 1 and view.raw_entity_words == (4 + 72) // 4
    assert view.extent == icadkit.ViewExtent(13, -7, 37, 11)
    (line,) = again.read_drawing().entities
    assert line.primitive.kind == "line"
    result = writer.result()
    assert result.deleted_entities == 2 and result.added_entities == 0
    assert not result.diagnostics


def test_deleting_every_entity_reproduces_an_empty_view():
    records = [ident(entity(2), 0x80000201), ident(entity(6), 0x80000202)]
    writer = DrawingWriter(icadkit.read(drawing(records, extent=(-26, -7, 37, 30))))
    assert writer.clear_view("!!GLOBAL") == 2
    assert writer.to_bytes() == drawing()
    writer.add_entities("!!GLOBAL", [Point2D((1, 1))])
    again = icadkit.read(writer.to_bytes())
    assert again.read_views().views[0].entity_count == 1
    assert again.read_drawing().entities[0].primitive.points == ((1, 1),)


def test_deleting_beside_an_undecoded_entity_keeps_the_stored_extent():
    rotated = text_entity(("A",), angle_words=(0x20000000, 0x20000000))
    records = [ident(entity(2, order="little"), 0x80000201, "little"), rotated]
    doc = icadkit.read(
        drawing(records, order="little", version=b"\0\x08\0\x03", extent=(0, 0, 50, 50))
    )
    writer = DrawingWriter(doc)
    assert writer.delete_entities("!!GLOBAL", [0x80000201]) == 1
    again = icadkit.read(writer.to_bytes())
    assert again.read_views().views[0].extent == icadkit.ViewExtent(0, 0, 50, 50)
    assert [d.code for d in writer.result().diagnostics] == ["write.extent_kept"]


def test_delete_view_round_trips_and_refuses_the_global_view():
    data = drawing([entity(6)], name=b"!XY", number=2)
    writer = DrawingWriter(icadkit.read(data))
    writer.add_view("FRONT")
    writer.add_entities("FRONT", [Line2D((0, 0), (1, 1))])
    writer.delete_view("FRONT")
    assert writer.to_bytes() == data
    assert writer.result().deleted_views == () and writer.result().added_views == ()
    writer.add_view("FRONT")
    writer.delete_view("!XY")
    again = icadkit.read(writer.to_bytes())
    (front,) = again.read_views().views
    assert front.name == "FRONT" and front.raw_view_number == 3
    assert again.container().view_names == (b"FRONT   ",)
    assert writer.result().deleted_views == ("!XY",)
    with pytest.raises(KeyError):
        writer.delete_view("!XY")
    global_writer = DrawingWriter(icadkit.read(drawing()))
    with pytest.raises(icadkit.UnsupportedFormatError, match="write.view_kind"):
        global_writer.delete_view("!!GLOBAL")


def test_add_view_clones_a_placement_record_and_delete_view_removes_it():
    data = sheet([entity(6, order="little")])
    doc = icadkit.read(data)
    views = doc.read_views().views
    assert [v.kind for v in views] == ["2d_global", "2d_view"]
    writer = DrawingWriter(doc)
    assert writer.views == ("!!GLOBAL", "!XY") and writer.view_capacity == 4
    assert writer.MAX_VIEW_NUMBER == 6 and writer.MAX_VIEW_NAME_BYTES == 8
    writer.add_view("FRONT", scale=0.5, origin=(80, -10))
    assert writer.view_capacity == 3
    writer.add_entities("!!GLOBAL", [Point2D((1, 2))])
    out = writer.to_bytes()
    again = icadkit.read(out)
    global_view = again.read_views().views[0]
    assert [e.kind for e in global_view.entries] == ["entity", "metadata", "metadata"]
    assert global_view.entity_count == 1 and global_view.raw_entity_words == 15
    metadata = [e for e in global_view.entries if e.kind == "metadata"]
    assert [m.byte_range.length for m in metadata] == [268, 264]
    record = again.source_bytes(metadata[1].byte_range)
    assert record[136:144] == b"FRONT   "
    assert struct.unpack_from("<3d", record, 40) == (80, -10, 0)
    assert struct.unpack_from("<d", record, 64) == (0.5,)
    assert struct.unpack_from("<I", record, 16) == (0x80000302,)
    assert struct.unpack_from("<6f", record, 176)[:2] == pytest.approx((1e38, 1e38))
    # MOD +232 counts the non-3D views; the global header +2 the placements.
    assert struct.unpack_from("<H", out, 232) == (1,)
    assert struct.unpack_from("<H", out, global_view.header_range.start + 2) == (2,)
    assert not writer.result().diagnostics
    writer.delete_entities("!!GLOBAL", writer.entity_ids("!!GLOBAL"))
    writer.delete_view("FRONT")
    assert writer.to_bytes() == data
    with pytest.raises(icadkit.UnsupportedFormatError, match="write.view_last"):
        writer.delete_view("!XY")
    writer.add_view("SIDE")
    writer.delete_view("!XY")
    again = icadkit.read(writer.to_bytes())
    sheet_view, side = again.read_views().views
    assert sheet_view.kind == "2d_global" and side.name == "SIDE"
    metadata = [e for e in sheet_view.entries if e.kind == "metadata"]
    assert [m.byte_range.length for m in metadata] == [268]
    assert again.source_bytes(metadata[0].byte_range)[140:148] == b"SIDE    "
    result = writer.result()
    assert result.deleted_views == ("!XY",) and not result.diagnostics
    with pytest.raises(ValueError, match="origin"):
        writer.add_view("TOP", origin=(1, 2, 3))


def test_views_without_a_placement_list_report_it():
    writer = DrawingWriter(icadkit.read(drawing(name=b"!XY", number=2)))
    writer.add_view("FRONT")
    writer.delete_view("!XY")
    assert [d.code for d in writer.result().diagnostics] == ["write.placement"] * 2


def test_diameter_dimension_round_trips_through_the_reader():
    doc = icadkit.read(drawing(order="little", version=b"\0\x08\0\x03"))
    writer = DrawingWriter(doc)
    (entity_id,) = writer.add_entities(
        "!!GLOBAL", [DiameterDimension2D((15, 15), 5, (18, 25))]
    )
    again = icadkit.read(writer.to_bytes())
    (entity,) = again.read_drawing().entities
    assert entity.raw_type == 27 and entity.status == "complete"
    dim = entity.dimension
    assert dim.kind == "diameter" and dim.layout_status == "complete"
    assert dim.center == (15, 15) and dim.radius == 5
    assert dim.value == pytest.approx(10) and dim.text.lines == ("１０",)
    assert dim.pick_point == (18, 25)
    assert dim.line_point == pytest.approx((17.44245702731394, 23.141523424379784))
    assert dim.line[0] == pytest.approx((20.60328376854374, 33.67761256181245))
    assert dim.line[1] == pytest.approx((12.844890858252409, 7.816302860841365))
    assert [i.kind for i in dim.items] == list(
        ("arc", "arc", "point", "vectors", "vectors", "line", "point", "point")
    )
    assert dim.items[1].radius == 5 and dim.items[1].sweep_angle == pytest.approx(
        math.tau
    )
    assert dim.arrow_width == 1.5 and dim.arrow_angle == pytest.approx(15)
    assert dim.underline == (3, 1) and dim.gap == 1
    assert dim.text.rotation == pytest.approx(73.3, abs=0.01)
    assert again.read_views().views[0].extent is not None
    radius_text = DiameterDimension2D((0, 0), 4, (10, 0), text="φ８")
    writer.add_entities("!!GLOBAL", [radius_text])
    final = icadkit.read(writer.to_bytes()).read_drawing().entities[-1].dimension
    assert final.text.lines == ("φ８",) and final.kind == "diameter"


@pytest.mark.parametrize(
    "dim,error",
    [
        (DiameterDimension2D((0, 0), 5, (0, 0)), "text point"),
        (DiameterDimension2D((0, 0), 0, (10, 0)), "radius"),
        (DiameterDimension2D((0, 0), 5, (-10, -1)), "readable"),
        (DiameterDimension2D((0, 0), 5, (10, 0), text=""), "text"),
        (DiameterDimension2D((0, 0), 5, (10, 0), height=0), "metrics"),
    ],
)
def test_diameter_dimension_requests_are_validated(dim, error):
    writer = DrawingWriter(
        icadkit.read(drawing(order="little", version=b"\0\x08\0\x03"))
    )
    with pytest.raises(ValueError, match=error):
        writer.add_entities("!!GLOBAL", [dim])
