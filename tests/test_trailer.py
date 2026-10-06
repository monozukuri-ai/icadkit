"""Authored trailing containers: framing, bounded block decoding and limits."""

import dataclasses
import hashlib
import json
import struct
import zlib

import pytest
from trailer_fixtures import block, framed, payload, trailer, view

import icadkit
from icadkit.cli import main

A, B, C = 0x80000021, 0x80000022, 0x80000105


def document(document_factory, tail, order="little"):
    return icadkit.read(document_factory(order=order, tail=tail))


@pytest.mark.parametrize("order", ["little", "big"])
@pytest.mark.parametrize("revision", [7, 8])
def test_framing_ownership_and_local_bounds(document_factory, order, revision):
    first, second = payload(), payload((-1.5, 0, -2, 1.5, 4, 2), kind=0x99)
    tail = trailer(
        [
            view("3DGLOBAL", [block(A, first), block(B, second)]),
            view("!3DB0000", [block(C, first)]),
        ],
        revision=revision,
    )
    doc = document(document_factory, tail, order)
    index = doc.read_trailer()
    assert index.status == "complete" and not index.diagnostics
    assert index.revision == revision and not index.opaque_ranges
    start = doc.file_size - len(tail)
    assert index.byte_range == icadkit.ByteRange(start, doc.file_size)
    assert [b.source_id for b in index.blocks] == [A, B, C]
    assert [b.view_name for b in index.blocks] == ["3DGLOBAL", "3DGLOBAL", "!3DB0000"]
    assert index.blocks_for(B) == (index.blocks[1],)
    one = index.blocks[0]
    assert one.declared_decoded_bytes == len(first)
    assert zlib.decompress(doc.source_bytes(one.storage_range)) == first
    assert one.block_id == f"trailer:{one.byte_range.start:016x}"
    data = doc.read_trailer_block(one.block_id)
    assert data.payload == first and data.source_id == A and data.raw_kind == 0x96
    assert data.payload_sha256 == hashlib.sha256(first).hexdigest()
    assert data.bounds == ((-3, -5, 0), (7, 11, 13))
    assert (data.length_unit, data.coordinate_space) == ("mm", "entity_local")
    # Empty tables are still tables: geometry is never claimed complete.
    assert data.status == "partial" and not data.diagnostics
    assert data.faces == () and data.edges == ()
    assert doc.read_trailer_block(index.blocks[1].block_id).bounds[1] == (1.5, 4, 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        index.revision = 1


def test_absent_container_is_complete_without_a_range(document_factory):
    index = document(document_factory, b"").read_trailer()
    assert index.status == "complete" and index.byte_range is None
    assert index.revision is None and not index.blocks and not index.tables


def test_unknown_container_and_table_remain_opaque(document_factory):
    doc = document(document_factory, b"unparsed tail!!!")
    index = doc.read_trailer()
    assert index.status == "unsupported" and not index.blocks
    assert index.diagnostics[0].code == "trailer.root"
    assert doc.source_bytes(index.opaque_ranges[0]) == b"unparsed tail!!!"
    table = b"linked table payload".ljust(24, b"\0")
    doc = document(document_factory, trailer([view("3DGLOBAL", [])], table=table))
    index = doc.read_trailer()
    assert index.status == "complete" and len(index.tables) == 1
    assert index.tables[0].status == "unsupported"
    assert doc.source_bytes(index.tables[0].payload_range) == table


@pytest.mark.parametrize(
    "data,code",
    [
        (payload(kind=0x18, marker=44), "trailer.block_layout"),
        (payload(marker=56), "trailer.block_layout"),
        (payload()[:40], "trailer.block_length"),
    ],
)
def test_unqualified_block_layout_keeps_decoded_bytes(document_factory, data, code):
    doc = document(document_factory, trailer([view("3DGLOBAL", [block(A, data)])]))
    result = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert result.payload == data and result.bounds is None
    assert result.diagnostics[0].code == code
    assert result.status == ("invalid" if code.endswith("length") else "unsupported")


# A capped cylinder as two half faces of saved face 113, two caps, the two
# seam edges that carry the face identifier, and each saved circle in halves.
HALF = 3.5
FACES = [
    (113, 2, (0, 6, HALF, 19), b"cylinder-a"),
    (9, 1, (-4.5, -3.5, 7.5, 8.5), b"plane-low"),
    (113, 2, (HALF, 6, 2 * HALF, 19), b"cylinder-b"),
    (36, 1, (-7.5, -3.5, 4.5, 8.5), b"plane-high"),
]
EDGES = [
    (113, (1, 3), b"seam-a"),
    (113, (3, 1), b"seam-b"),
    (95, (1, 4), b"top-a"),
    (95, (3, 4), b"top-b"),
    (98, (3, 2), b"low-a"),
    (98, (1, 0), b"open"),
]


@pytest.mark.parametrize("order", ["little", "big"])
def test_tables_expose_identity_adjacency_and_parameter_boxes(document_factory, order):
    data = payload(faces=FACES, edges=EDGES, extra=b"")
    tail = trailer([view("3DGLOBAL", [block(A, data)])])
    doc = document(document_factory, tail, order)
    result = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert result.status == "partial" and not result.diagnostics
    assert result.bounds == ((-3, -5, 0), (7, 11, 13))
    assert [f.index for f in result.faces] == [1, 2, 3, 4]
    assert [f.source_node_id for f in result.faces] == [113, 9, 113, 36]
    assert [f.surface_code for f in result.faces] == [2, 1, 2, 1]
    assert result.faces[1].parameter_bounds == (-4.5, -3.5, 7.5, 8.5)
    assert result.faces[2].parameter_bounds == (HALF, 6, 2 * HALF, 19)
    assert [e.index for e in result.edges] == [1, 2, 3, 4, 5, 6]
    assert [e.source_node_id for e in result.edges] == [113, 113, 95, 95, 98, 98]
    assert [e.faces for e in result.edges] == [pair for _, pair, _ in EDGES]
    # Entries and their uninterpreted items are exact slices of the payload.
    at = 52
    for face, (_, _, _, item) in zip(result.faces, FACES, strict=True):
        assert face.raw_bytes == data[at : at + 36]
        assert data[face.item_offset : face.item_offset + len(item)] == item
        at += 36
    for edge, (_, _, item) in zip(result.edges, EDGES, strict=True):
        assert edge.raw_bytes == data[at : at + 24]
        assert data[edge.item_offset : edge.item_offset + len(item)] == item
        at += 24
    # Two entries of one saved face meet only at edges that carry its identifier.
    seams = [e for e in result.edges if e.source_node_id == 113]
    assert {frozenset(e.faces) for e in seams} == {frozenset((1, 3))}
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.faces[0].index = 2


def floats(*values):
    return struct.pack(f"<{len(values)}f", *values)


BOX = (0, 0, 1, 1)
# Authored items: aligned layouts code the axes in the entry tag bytes
# (0..2 = +x/+y/+z, 4..6 = -x/-y/-z); general layouts store the vectors.
ITEM_FACES = [
    (1, 1, BOX, floats(10, 15, 23), (0x10, 0, 2, 0)),
    (2, 1, BOX, floats(1, 2, 3, 0, 0.6, 0.8, 1, 0, 0), (0x00, 0, 0, 0)),
    (3, 2, BOX, floats(-1.5, 2.5, -6, 6), (0x12, 0, 2, 0)),
    (4, 2, BOX, floats(10, -40, -165, -1, 0, 0, 0, 0.6, -0.8, 27.5), (0x02, 0, 0, 0)),
    (5, 3, BOX, floats(0, 5.5, 0, 4, 1.75), (0x16, 0, 6, 0)),
    (6, 4, BOX, floats(1, 2, 3, 7, 7), (0x18, 0, 2, 0)),
    (7, 5, BOX, floats(0, 0, 0, 19, 4), (0x10, 0, 2, 0)),
    (
        8,
        5,
        BOX,
        floats(-13, -32, 0, 0, 0, -1, -0.8, -0.6, 0, 32.5, 25.25),
        (0x06, 0, 0, 0),
    ),
]
ITEM_EDGES = [
    (21, (1, 2), floats(-23, 30, 40), (0x10, 0, 5, 0), (1, 1, 0, 2)),
    (22, (2, 1), floats(0, 20, -0.6, -0.8, 20.5), (0x00, 0, 0, 0), (1, 1, 2, 0)),
    (23, (3, 4), floats(3.1416, 6, 1.5708), (0x12, 0, 4, 0), (1, 3, 0, 2)),
]


def test_qualified_items_decode_surfaces_and_parameter_lines(document_factory):
    data = payload(faces=ITEM_FACES, edges=ITEM_EDGES, extra=b"")
    doc = document(document_factory, trailer([view("3DGLOBAL", [block(A, data)])]))
    result = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert result.status == "partial"
    surfaces = [f.surface for f in result.faces]
    assert [s.kind for s in surfaces] == ["plane"] * 2 + ["cylinder"] * 2 + [
        "cone",
        "sphere",
        "torus",
        "torus",
    ]
    assert surfaces[0] == icadkit.TrailerSurface(
        "plane", (10, 15, 23), (0, 0, 1), (1, 0, 0)
    )
    assert surfaces[1].point == (1, 2, 3)
    assert surfaces[1].axis == pytest.approx((0, 0.6, 0.8))
    assert surfaces[1].x_axis == (1, 0, 0)
    assert surfaces[2] == icadkit.TrailerSurface(
        "cylinder", (-1.5, 2.5, -6), (0, 0, 1), (1, 0, 0), radius=6
    )
    assert surfaces[3].axis == (-1, 0, 0)
    assert surfaces[3].x_axis == pytest.approx((0, 0.6, -0.8))
    assert surfaces[3].radius == 27.5
    assert surfaces[4] == icadkit.TrailerSurface(
        "cone", (0, 5.5, 0), (0, 0, -1), (1, 0, 0), radius=4, half_angle_tangent=1.75
    )
    assert surfaces[5] == icadkit.TrailerSurface("sphere", (1, 2, 3), None, None, 7)
    assert surfaces[6] == icadkit.TrailerSurface(
        "torus", (0, 0, 0), (0, 0, 1), (1, 0, 0), major_radius=19, minor_radius=4
    )
    assert surfaces[7].axis == (0, 0, -1) and surfaces[7].major_radius == 32.5
    assert surfaces[7].x_axis == pytest.approx((-0.8, -0.6, 0))
    lines = [e.parameter_line for e in result.edges]
    assert lines[0] == icadkit.TrailerParameterLine((-23, 30), (0, -1), 40)
    assert lines[1].start == (0, 20) and lines[1].extent == 20.5
    assert lines[1].direction == pytest.approx((-0.6, -0.8))
    assert lines[2].start == pytest.approx((3.1416, 6))
    assert lines[2].direction == (-1, 0) and lines[2].extent == pytest.approx(1.5708)
    # Raw entry bytes and items stay available next to the decoded values.
    assert result.faces[0].raw_bytes[:4] == bytes((0x10, 0, 2, 0))
    assert data[result.faces[0].item_offset :][:12] == floats(10, 15, 23)


@pytest.mark.parametrize(
    "face",
    [
        (1, 1, BOX, floats(10, 15, 23), (0x10, 0, 3, 0)),  # no such axis code
        (1, 1, BOX, floats(10, 15, 23), (0x10, 0, 2, 2)),  # axes not perpendicular
        (1, 1, BOX, floats(10, 15, 23, 0), (0x10, 0, 2, 0)),  # wrong item size
        (1, 1, BOX, floats(1, 2, 3, 0, 0.6, 0.9, 1, 0, 0), (0x00, 0, 0, 0)),  # not unit
        (3, 2, BOX, floats(0, 0, 0, -6), (0x12, 0, 2, 0)),  # negative radius
        (5, 3, BOX, floats(0, 5.5, 0, 4, -1.75), (0x16, 0, 6, 0)),  # negative tangent
        (6, 4, BOX, floats(1, 2, 3, 7, 8), (0x18, 0, 2, 0)),  # radii disagree
        (7, 5, BOX, floats(0, 0, 0, 19, 0), (0x10, 0, 2, 0)),  # zero minor radius
        (9, 10, BOX, floats(0, 0, 0), (0x20, 0, 0, 0)),  # free-form code
        (1, 1, BOX, floats(float("nan"), 15, 23), (0x10, 0, 2, 0)),  # nonfinite
    ],
)
def test_unqualified_surface_items_stay_raw(document_factory, face):
    data = payload(faces=[face], extra=b"")
    doc = document(document_factory, trailer([view("3DGLOBAL", [block(A, data)])]))
    result = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert result.status == "partial"
    assert result.faces[0].surface is None
    assert result.faces[0].surface_code == face[1]


@pytest.mark.parametrize(
    "edge",
    [
        (
            21,
            (1, 0),
            floats(-23, 30, 40),
            (0x10, 0, 5, 0),
            (3, 1, 0, 2),
        ),  # curved image
        (
            21,
            (1, 0),
            floats(-23, 30, 40),
            (0x30, 0, 5, 0),
            (1, 1, 0, 2),
        ),  # other family
        (
            21,
            (0, 1),
            floats(-23, 30, 40),
            (0x10, 0, 5, 0),
            (1, 1, 0, 2),
        ),  # no first face
        (21, (1, 0), floats(-23, 30, 40), (0x10, 0, 2, 0), (1, 1, 0, 2)),  # no 2D code
        (21, (1, 0), floats(-23, 30, 0), (0x10, 0, 5, 0), (1, 1, 0, 2)),  # zero extent
        (21, (1, 0), floats(0, 20, -0.6, -0.9, 20.5), (0x00, 0, 0, 0), (1, 1, 2, 0)),
        (21, (1, 0), floats(0, 20, -0.6, -0.8), (0x00, 0, 0, 0), (1, 1, 2, 0)),  # size
    ],
)
def test_unqualified_edge_items_stay_raw(document_factory, edge):
    data = payload(faces=ITEM_FACES[:1], edges=[edge], extra=b"")
    doc = document(document_factory, trailer([view("3DGLOBAL", [block(A, data)])]))
    result = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert result.status == "partial"
    assert result.edges[0].parameter_line is None
    assert result.faces[0].surface is not None


def test_edge_items_on_other_first_faces_stay_raw(document_factory):
    cone = ITEM_FACES[4]
    edge = (21, (1, 0), floats(0, 3.9, 0.72), (0x10, 0, 1, 0), (1, 1, 0, 2))
    data = payload(faces=[cone], edges=[edge], extra=b"")
    doc = document(document_factory, trailer([view("3DGLOBAL", [block(A, data)])]))
    result = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert result.faces[0].surface.kind == "cone"
    assert result.edges[0].parameter_line is None


@pytest.mark.parametrize(
    "box",
    [(1, 0, 0, 1), (0, 1, 1, 0), (0, 0, float("nan"), 1), (float("-inf"), 0, 1, 1)],
)
def test_unordered_or_nonfinite_parameter_box_is_withheld(document_factory, box):
    data = payload(faces=[(7, 1, box, b"item")])
    doc = document(document_factory, trailer([view("3DGLOBAL", [block(A, data)])]))
    result = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert result.status == "partial"
    assert result.faces[0].parameter_bounds is None
    assert result.faces[0].raw_bytes == data[52:88]


@pytest.mark.parametrize(
    "change",
    [
        {"edges_at": 52},
        {"items_at": 52 + 36 * 4 + 24 * 6 + 4},
        {"shift": -1},
        {"shift": 4096},
        {"edges": [*EDGES[:-1], (98, (1, 5), b"open")]},
        {"faces": FACES * 40, "edges": [], "extra": b"", "items_at": 2**20},
    ],
)
def test_inconsistent_tables_expose_nothing(document_factory, change):
    data = payload(**{"faces": FACES, "edges": EDGES, **change})
    doc = document(document_factory, trailer([view("3DGLOBAL", [block(A, data)])]))
    result = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert result.status == "invalid" and result.payload == data
    assert result.bounds is None and result.faces == () and result.edges == ()
    assert [d.code for d in result.diagnostics] == ["trailer.block_tables"]


@pytest.mark.parametrize("revision", [1, 2, 3, 4, 6])
def test_older_revisions_are_framed_without_qualified_bounds(
    document_factory, revision
):
    tail = trailer(
        [view("3DGLOBAL", [block(A, payload(faces=FACES, edges=EDGES))])],
        revision=revision,
    )
    doc = document(document_factory, tail)
    index = doc.read_trailer()
    assert index.status == "complete" and index.revision == revision
    data = doc.read_trailer_block(index.blocks[0].block_id)
    assert data.bounds is None and data.status == "unsupported"
    assert data.faces == () and data.edges == ()


@pytest.mark.parametrize(
    "bounds",
    [
        (1, 0, 0, 0, 1, 1),
        (0, 0, 0, float("nan"), 1, 1),
        (0, float("-inf"), 0, 1, 1, 1),
    ],
)
def test_reversed_or_nonfinite_bounds_are_invalid(document_factory, bounds):
    tail = trailer([view("3DGLOBAL", [block(A, payload(bounds))])])
    doc = document(document_factory, tail)
    data = doc.read_trailer_block(doc.read_trailer().blocks[0].block_id)
    assert data.status == "invalid" and data.bounds is None
    assert data.diagnostics[0].code == "trailer.block_bounds"


def test_inconsistent_framing_stops_without_searching(document_factory):
    good = block(A, payload())
    cases = {
        "trailer.count": [
            trailer([view("3DGLOBAL", [good], count=2)]),
            trailer([view("3DGLOBAL", [good], largest=1)]),
            trailer([view("3DGLOBAL", [good])], view_count=2),
        ],
        "trailer.block": [
            trailer([view("3DGLOBAL", [block(A, payload(), zero=1)])]),
            trailer([view("3DGLOBAL", [block(0x70000001, payload())])]),
            trailer([view("3DGLOBAL", [block(A, payload(), pad=16 + 9)])]),
        ],
        "trailer.record": [
            trailer([view("3DGLOBAL", [good])], revision=5),
            trailer([view("3DGLOBAL", [good])], extra=framed(0x30, 0)),
            trailer([framed(0x24, 0, b"\0" * 16)]),
        ],
    }
    for code, tails in cases.items():
        for tail in tails:
            index = document(document_factory, tail).read_trailer()
            assert index.diagnostics[0].code == code, tail.hex()
            assert index.status == (
                "partial" if code == "trailer.record" else "invalid"
            )
            assert index.opaque_ranges
    # A nonzero padding byte and a truncated root are never repaired.
    padded = next(b for n in range(1, 17) if (b := block(A, bytes(n)))[-1:] == b"\0")
    raw = bytearray(trailer([view("3DGLOBAL", [padded])]))
    raw[-1] = 1
    index = document(document_factory, bytes(raw)).read_trailer()
    assert index.status == "invalid" and index.diagnostics[0].code == "trailer.block"
    truncated = document(document_factory, trailer([view("3DGLOBAL", [good])])[:-8])
    assert truncated.read_trailer().diagnostics[0].code == "trailer.root"


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"encoded": zlib.compress(b"other bytes")}, "trailer.compression"),
        ({"declared": 9}, "trailer.compression"),
        ({"encoded": b"\x78\x01 not a stream"}, "trailer.compression"),
    ],
)
def test_block_decoding_is_exact(document_factory, kwargs, code):
    tail = trailer([view("3DGLOBAL", [block(A, payload(), **kwargs)])])
    doc = document(document_factory, tail)
    index = doc.read_trailer()
    assert index.status == "complete"
    with pytest.raises(icadkit.InvalidFormatError) as exc:
        doc.read_trailer_block(index.blocks[0].block_id)
    assert exc.value.diagnostic.code == code


def test_limits_and_block_identifiers(document_factory):
    blocks = [block(A + i, payload()) for i in range(4)]
    doc = document(document_factory, trailer([view("3DGLOBAL", blocks)]))
    index = doc.read_trailer(limits=icadkit.TrailerLimits(max_records=8))
    assert len(index.blocks) == 4
    with pytest.raises(icadkit.LimitExceededError, match="max_records"):
        doc.read_trailer(limits=icadkit.TrailerLimits(max_records=7))
    block_id = index.blocks[0].block_id
    with pytest.raises(icadkit.LimitExceededError, match="max_block_bytes"):
        doc.read_trailer_block(block_id, limits=icadkit.TrailerLimits(1000, 16))
    with pytest.raises(icadkit.InvalidFormatError) as exc:
        doc.read_trailer_block(f"trailer:{index.blocks[0].byte_range.start + 8:016x}")
    assert exc.value.diagnostic.code == "trailer.block_not_found"
    for value in ("usr:0000000000000000", "trailer:12", "trailer:" + "g" * 16):
        with pytest.raises(ValueError):
            doc.read_trailer_block(value)
    with pytest.raises(TypeError):
        doc.read_trailer_block(7)
    with pytest.raises(TypeError):
        doc.read_trailer(limits=object())
    for value in (0, -1, True, 2**31):
        with pytest.raises((TypeError, ValueError)):
            icadkit.TrailerLimits(max_records=value)


def test_cli_reports_framing_and_optional_bounds(document_factory, tmp_path, capsys):
    path = tmp_path / "authored.bin"
    data = payload(faces=FACES, edges=EDGES)
    path.write_bytes(
        document_factory(tail=trailer([view("3DGLOBAL", [block(A, data)])]))
    )
    assert main(["trailer", str(path), "--json", "--bounds"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["operation"] == "trailer" and out["result"]["status"] == "complete"
    row = out["decoded_blocks"][0]
    assert row["bounds"] == [[-3, -5, 0], [7, 11, 13]] and "payload" not in row
    assert row["payload_bytes"] == len(data)
    assert (row["face_count"], row["edge_count"]) == (4, 6)
    assert "faces" not in row and "edges" not in row
    assert main(["trailer", str(path)]) == 0
    assert "entity=0x80000021" in capsys.readouterr().out
    path.write_bytes(document_factory(tail=b"unknown remainder"))
    assert main(["trailer", str(path), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["result"]["status"] == "unsupported"
    assert main(["trailer", str(path), "--max-records", "1", "--json"]) == 3
    path.write_bytes(
        document_factory(tail=trailer([view("3DGLOBAL", [block(A, payload())])]))
    )
    assert main(["trailer", str(path), "--max-records", "1", "--json"]) == 4
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["error"]["code"] == (
        "limit.trailer_records"
    )
    with pytest.raises(SystemExit):
        main(["trailer", str(path), "--max-records", str(2**31)])


def test_association_records_keep_ids_and_frames(resource_factory, document_factory):
    from fixture_builders import association

    for order in ("little", "big"):
        resource, _ = resource_factory(version=6, order=order, source_id=3)
        frame = (10, -20, 30, 0, 1, 0, 1, 0, 0)
        link = association(A, 3, frame, order=order)
        doc = icadkit.read(document_factory([resource, link], order=order))
        assert doc.resource_index_status == "complete" and len(doc.resources) == 1
        (item,) = doc.resource_associations
        assert (item.entity_source_id, item.resource_source_id) == (A, 3)
        assert item.frame == frame and doc.source_bytes(item.byte_range) == link
        assert (
            struct.unpack_from(
                ("<" if order == "little" else ">") + "9d",
                doc.source_bytes(item.byte_range),
                24,
            )
            == frame
        )
