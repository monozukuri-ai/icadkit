"""V8L4 part records: the 0x61000004 tag and the two record variants observed in
2026 production files (a 916-byte external occurrence and user attribute text
whose second word is 230). Authored framing cases, not files exported from iCAD."""

import struct

import pytest
from part_fixtures import ROOT, A, B, extra_info, part, view_parts

import icadkit


def external_v8l4(source_id, *, parent, previous=0, next_id=0, name="M-2751784-002"):
    """A V8L4 external occurrence: 352 bytes of part record plus a 564-byte
    extension behind the 0x101 marker (counted UTF-16 strings, resource links)."""
    base = bytearray(
        part(
            source_id,
            parent=parent,
            previous=previous,
            next_id=next_id,
            name=name,
            flags=0x50,
            reference=name,
            profile="v8l4",
        )
    )
    struct.pack_into("<I", base, 4, 916)
    struct.pack_into("<I", base, 12, 1)  # every observed extended record sets +12
    # Record offsets 356 / 360 / 364: extension length, the 0x101 marker, 1.
    extension = bytearray(564)
    struct.pack_into("<3I", extension, 0, 564, 0x101, 1)
    text = name.encode("utf-16-le")
    struct.pack_into("<H", extension, 12, len(name))
    extension[14 : 14 + len(text)] = text
    return bytes(base[:356]) + bytes(extension)


def user_attributes(value, *, kind=230, first=True):
    """An attribute text record whose second word is ``kind`` (1 before V8L4)."""
    record = bytearray(extra_info(value, first=first))
    at = 4 if first else 0
    struct.pack_into("<I", record, at + 4, kind)
    return bytes(record)


def test_v8l4_parts_read_with_their_own_tag():
    data = view_parts(
        [
            part(ROOT, child=A, name="Assembly", root=True, profile="v8l4"),
            part(A, parent=ROOT, name="部品", profile="v8l4"),
        ],
        profile="v8l4",
    )
    doc = icadkit.read(data)
    assert doc.header.version_label == "V8L4"
    index = doc.read_parts()
    assert index.status.index == "complete"
    assert index.status.hierarchy == "complete"
    assert index.profile.profile_id == "icad-v8l4-parts-r3"
    assert index.profile.part_tag == 0x61000004
    assert index.profile.entity_policy == "saved_entities"
    assert index.profile.saved_body_layout == "v8_resource_association"
    assert [p.name for p in index.parts] == ["Assembly", "部品"]
    support = icadkit.qualified_versions()
    assert "V8L4" in support.parts and "V8L4" in support.drawing_layout


def test_v8l3_tag_is_not_accepted_under_a_v8l4_header():
    data = view_parts([part(ROOT, root=True, profile="v8l3")], profile="v8l4")
    index = icadkit.read(data).read_parts()
    assert not index.parts
    assert [d.code for d in index.diagnostics][:1] == ["parts.record_profile"]


def test_v8l4_external_occurrence_keeps_the_extension_opaque():
    data = view_parts(
        [
            part(ROOT, child=A, name="Assembly", root=True, profile="v8l4"),
            external_v8l4(A, parent=ROOT, next_id=B),
            external_v8l4(B, parent=ROOT, previous=A, name="M-2751784-003"),
        ],
        profile="v8l4",
    )
    index = icadkit.read(data).read_parts()
    assert index.status.index == "complete"
    assert [d.code for d in index.diagnostics] == [
        "parts.external_unresolved",
        "parts.external_unresolved",
    ]
    a, b = index.parts[1], index.parts[2]
    assert a.is_external and b.is_external
    assert a.name == "M-2751784-002" and b.name == "M-2751784-003"
    assert a.byte_range.end - a.byte_range.start == 920
    # The whole 564-byte extension is accounted for as opaque metadata.
    reasons = {r.reason for r in index.opaque_ranges}
    assert "part_metadata_extension" in reasons


@pytest.mark.parametrize("profile", ["v8l3", "v8l2"])
def test_916_byte_records_stay_unqualified_before_v8l4(profile):
    record = bytearray(external_v8l4(A, parent=ROOT))
    tag = {"v8l3": 0x61000003, "v8l2": 0x61000003}[profile]
    struct.pack_into("<I", record, 0, tag)
    data = view_parts(
        [part(ROOT, child=A, root=True, profile=profile), bytes(record)],
        profile=profile,
    )
    index = icadkit.read(data).read_parts()
    assert index.status.index == "partial"
    assert any(d.code == "parts.record_layout" for d in index.diagnostics)


def test_v8l4_user_attribute_text_is_the_extra_info():
    value = 'User_DNVer,"1",User_Type,"1",User_MNo,"M-0408404-002"'
    data = view_parts(
        [part(ROOT, root=True, profile="v8l4"), user_attributes(value)],
        profile="v8l4",
    )
    index = icadkit.read(data).read_parts()
    assert index.status.index == "complete"
    assert not index.diagnostics
    assert index.parts[0].extra_info == value


def test_user_attribute_kind_230_stays_unqualified_before_v8l4():
    data = view_parts(
        [part(ROOT, root=True, profile="v8l3"), user_attributes('User_Type,"1"')],
        profile="v8l3",
    )
    index = icadkit.read(data).read_parts()
    assert index.parts[0].extra_info is None
    assert any(d.code == "parts.attribute_layout" for d in index.diagnostics)


def test_v8l4_views_are_qualified_and_v8l5_is_not():
    data = view_parts([part(ROOT, root=True, profile="v8l4")], profile="v8l4")
    views = icadkit.read(data).read_views()
    assert views.status != "unsupported"
    assert not [d for d in views.diagnostics if d.code == "views.profile"]
    raw = bytearray(data)
    raw[12:16] = b"\0\x08\0\x05"
    views = icadkit.read(bytes(raw)).read_views()
    assert views.status == "unsupported"
    assert [d.code for d in views.diagnostics] == ["views.profile"]
