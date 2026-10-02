"""Authored binary attributes and legacy inventory boundaries; no vendor inputs."""

import json
import struct

import pytest
from part_fixtures import ROOT, A, part, view_parts
from test_native import native

import icadkit
from icadkit.viewer import write_native_viewer


def attribute(subtype=0x02000000, size=408, *, padded=False):
    b = bytearray(size)
    struct.pack_into(
        "<8I",
        b,
        0,
        size,
        1,
        0,
        0xCF010081,
        0x80000007,
        0,
        0xFD000000 | (size - 24),
        subtype,
    )
    b[32:36] = b"\1   " if padded else b"\1\0\0\0"
    if size >= 408:
        b[48:404] = part(A, parent=ROOT, name="not a real part")
    return bytes(b)


@pytest.mark.parametrize("profile", ["v7l7", "v8l1", "v8l2", "v8l3"])
@pytest.mark.parametrize(
    "subtype,size",
    [
        (0x02000000, 408),
        (0x02000000, 472),
        (0x02000000, 616),
        (0x03000000, 56),
        (0x04000000, 56),
        (0x06000000, 48),
        (0x19000000, 88),
    ],
)
def test_binary_attributes_preserve_owner_and_bytes_without_text_semantics(
    profile, subtype, size
):
    raw = attribute(subtype, size, padded=True)
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, profile=profile),
                struct.pack("<I", 0x30010000),
                raw,
            ],
            profile=profile,
        )
    )
    index = doc.read_parts()
    assert index.status.index == index.status.hierarchy == "complete"
    assert index.status.stored_attributes == "partial"
    assert len(index.parts) == 1 and not index.parts[0].entities
    owner = index.parts[0]
    assert owner.extra_info is None and len(owner.properties) == 1
    (a,) = owner.opaque_attributes
    assert isinstance(a, icadkit.PartOpaqueAttribute)
    assert a.owner_id == owner.part_id and a.subtype == subtype
    assert a.raw_bytes == raw == doc.source_bytes(a.byte_range)
    assert (
        index.to_rows(include_root=True)[0]["opaque_attributes"][0]["raw_bytes_hex"]
        == raw.hex()
    )
    with pytest.raises(icadkit.LimitExceededError, match="max_property_bytes"):
        doc.read_parts(limits=icadkit.PartLimits(max_property_bytes=size - 33))


@pytest.mark.parametrize(
    "offset,value", [(24, 0xFD000001), (28, 0x99000000), (32, 0), (4, 0)]
)
def test_unknown_attribute_layout_does_not_gain_index_completeness(offset, value):
    raw = bytearray(attribute())
    struct.pack_into("<I", raw, offset, value)
    index = icadkit.read(
        view_parts([part(ROOT, root=True), struct.pack("<I", 0x30010000), raw])
    ).read_parts()
    assert index.status.index == "partial"
    assert not index.parts[0].opaque_attributes
    assert any(d.code == "parts.attribute_layout" for d in index.diagnostics)


RECORD = 0x80000007


def fragments(parts, *, flags=0x0081, layer=1, declared=None):
    """Authored attribute record made of several length-framed fragments."""
    body = b"".join(
        struct.pack("<II", 0xFD000000 | (len(payload) + 8), subtype) + payload
        for subtype, payload in parts
    )
    count = len(parts) if declared is None else declared
    head = struct.pack(
        "<6I", 24 + len(body), layer, 0, 0xCF000000 | count << 16 | flags, RECORD, 0
    )
    return head + body


TEXT = 'User_Type,"調達"'.encode("utf-16-le")
PAIR = [(0x06000000, bytes(range(16))), (0x10000000, TEXT)]


@pytest.mark.parametrize("profile", ["v7l6", "v7l7", "v8l1", "v8l2", "v8l3"])
@pytest.mark.parametrize(
    "flags,parts",
    [
        (0x0081, PAIR),
        (0x00C1, PAIR[::-1]),
        (0x1081, PAIR + [(0x02000000, bytes(40))]),
        (0x10C1, PAIR),
        # One fragment with the saved visibility bit; the plain single-fragment
        # record keeps its own exact layouts.
        (0x00C1, [(0x03000000, b"\1\0\0\0")]),
    ],
)
def test_fragmented_attributes_keep_ownership_without_hiding_primitives(
    profile, flags, parts
):
    raw = fragments(parts, flags=flags)
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, profile=profile),
                struct.pack("<I", 0x30010000),
                raw,
                native(first=False),
            ],
            profile=profile,
        )
    )
    index = doc.read_parts()
    owner = index.parts[0]
    assert index.status.index == index.status.hierarchy == "complete"
    assert index.status.stored_attributes == "partial"
    (a,) = owner.opaque_attributes
    assert a.subtype == parts[0][0] and a.source_id == RECORD
    assert a.raw_bytes == raw == doc.source_bytes(a.byte_range)
    # A text fragment inside such a record is not promoted to extended text.
    assert owner.extra_info is None and len(owner.properties) == 1
    # The attribute is not a geometry entity and no longer hides the primitive.
    (entity,) = owner.entities
    assert entity.raw_type == 75 and entity.primitive.box_dimensions == (13, 17, 23)
    with pytest.raises(icadkit.LimitExceededError, match="max_property_bytes"):
        doc.read_parts(limits=icadkit.PartLimits(max_property_bytes=len(raw) - 25))


def overrun():
    # The last fragment claims four bytes beyond its record.
    raw = bytearray(fragments(PAIR))
    struct.pack_into("<I", raw, 24 + 24, 0xFD000000 | (len(TEXT) + 12))
    return bytes(raw)


@pytest.mark.parametrize(
    "raw,profile",
    [
        (fragments(PAIR, declared=3), "v8l3"),
        (fragments(PAIR, declared=1, flags=0x00C1), "v8l3"),
        (fragments(PAIR, layer=2), "v8l3"),
        (fragments(PAIR, flags=0x0083), "v8l3"),
        (fragments(PAIR, flags=0x1081), "v7l4"),
        (overrun(), "v8l3"),
    ],
)
def test_unqualified_fragment_framing_keeps_the_previous_opaque_entity(raw, profile):
    index = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, profile=profile),
                struct.pack("<I", 0x30010000),
                raw,
            ],
            profile=profile,
        )
    ).read_parts()
    owner = index.parts[0]
    assert index.status.index == "complete" and not owner.opaque_attributes
    (entity,) = owner.entities
    assert entity.raw_type == 0xCF and entity.primitive is None


@pytest.mark.parametrize("profile", ["v8l1", "v8l2"])
def test_qualified_legacy_parts_expose_placement_appearance_and_native_geometry(
    profile, tmp_path
):
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, profile=profile),
                part(
                    A,
                    parent=ROOT,
                    name="旧部品",
                    comment="保管値",
                    position=(7, -11, 19),
                    profile=profile,
                ),
                native("cone"),
            ],
            profile=profile,
        )
    )
    ix = doc.read_parts()
    child = ix.children(ix.parts[0].part_id)[0]
    assert ix.status.index == ix.status.hierarchy == "complete"
    assert child.name == "旧部品" and child.comment == "保管値"
    assert child.placement.coordinate_values[:3] == (7, -11, 19)
    assert child.placement.world_transform == child.placement.local_transform
    assert tuple(row[3] for row in child.placement.world_transform[:3]) == (7, -11, 19)
    assert ix.source_length_unit == "mm" and ix.status.units == "complete"
    assert (
        child.entities[0].primitive.kind == "cone"
        and child.entities[0].appearance.status == "complete"
    )
    assert icadkit.read_saved_bodies(doc).bodies == ()
    result = write_native_viewer(doc, tmp_path / profile)
    scene = json.loads((result.directory / "scene.json").read_bytes())
    assert scene["scope"] == "qualified_native_primitives" and len(scene["meshes"]) == 1
    assert scene["length_unit"] == "mm" and scene["coordinate_system"] == "3DGLOBAL"


@pytest.mark.parametrize("profile", ["v8l1", "v8l2"])
@pytest.mark.parametrize("length", [700, 22364])
def test_legacy_part_metadata_extension_is_length_checked_and_not_scanned(
    profile, length
):
    raw = bytearray(part(ROOT, root=True, profile=profile))
    raw += bytes(length + 4 - len(raw))
    struct.pack_into("<I", raw, 4, length)
    struct.pack_into("<I", raw, 12, 1)
    struct.pack_into("<3I", raw, 356, length - 352, 1, 1)
    raw[380:388] = part(A, parent=ROOT)[:8]
    doc = icadkit.read(view_parts([raw], profile=profile))
    index = doc.read_parts()
    assert index.status.index == "complete" and len(index.parts) == 1
    assert any(r.reason == "part_metadata_extension" for r in index.opaque_ranges)
    with pytest.raises(icadkit.LimitExceededError):
        doc.read_parts(limits=icadkit.PartLimits(max_property_bytes=length - 353))
    struct.pack_into("<I", raw, 356, length - 356)
    bad = icadkit.read(view_parts([raw], profile=profile)).read_parts()
    assert bad.status.index != "complete" and not bad.parts


@pytest.mark.parametrize("profile", ["v8l1", "v8l2"])
def test_legacy_header_does_not_accept_v7_records(profile):
    ix = icadkit.read(
        view_parts([part(ROOT, root=True, profile="v7l7")], profile=profile)
    ).read_parts()
    assert not ix.parts and ix.diagnostics[0].code == "parts.record_profile"
