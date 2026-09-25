"""Profile evidence must not make a header label or unknown view executable."""

import struct
from dataclasses import replace

import pytest
from fixture_builders import document_factory
from part_fixtures import ROOT, A, part, view_parts
from test_native import native

import icadkit


@pytest.mark.parametrize(
    "version,convention,policy,saved,csg",
    [
        ("v7l2", "root_relative", "opaque_entities", None, None),
        ("v7l3", "root_relative", "opaque_entities", None, None),
        ("v7l4", "root_relative", "opaque_entities", None, None),
        ("v7l5", "root_relative", "opaque_entities", None, None),
        ("v7l6", "root_relative", "standalone_owner", "v7l6_source_id", None),
        ("v7l7", "root_relative", "standalone_owner", "v7_source_id", "v7_postfix"),
        ("v8l1", "root_relative", "standalone_owner", "v8l1_saved_body", None),
        ("v8l2", "root_relative", "standalone_owner", "v8_resource_key", None),
        ("v8l3", "root_relative", "saved_entities", "v8_resource_key", None),
    ],
)
def test_policies_have_view_and_record_evidence(
    version, convention, policy, saved, csg
):
    records = [
        part(ROOT, root=True, child=A, profile=version),
        part(A, parent=ROOT, profile=version),
        native(),
    ]
    if version == "v7l5":
        records = [part(ROOT, root=True, profile=version), native()]
    doc = icadkit.read(view_parts(records, profile=version))
    index = doc.read_parts()
    profile = index.profile
    assert isinstance(profile, icadkit.PartProfile)
    assert profile.coordinate_convention == convention
    assert profile.entity_policy == policy
    assert profile.saved_body_layout == saved
    assert profile.csg_layout == csg
    assert profile.raw_version == doc.header.raw_version
    assert profile.byte_order == "little"
    assert profile.part_byte_range == index.parts[0].byte_range
    assert struct.unpack("<I", doc.source_bytes(profile.part_byte_range)[:4])[0] == (
        profile.part_tag
    )
    assert index.views[0].kind == "3d_global"
    assert profile.view_byte_range == index.views[0].byte_range
    assert index.document_kind == "unknown"
    if policy == "inventory_only":
        assert index.source_length_unit is None
        assert index.parts[-1].entities[0].primitive is None
    elif policy == "opaque_entities":
        assert index.source_length_unit == "mm"
        assert index.parts[-1].entities[0].primitive is None
        assert index.status.native_geometry == "unsupported"
    else:
        assert index.source_length_unit == "mm"
        assert index.parts[-1].entities[0].primitive.box_dimensions == (13, 17, 23)


@pytest.mark.parametrize("defect", ["version", "view", "tag", "length", "rootless"])
def test_header_and_view_name_alone_never_supply_profile(defect):
    raw = bytearray(view_parts([part(ROOT, root=True)]))
    view = icadkit.read(bytes(raw)).read_parts().views[0].byte_range.start
    if defect == "version":
        raw[12:16] = b"\0\x08\0\x04"
    elif defect == "view":
        struct.pack_into("<I", raw, view + 16, 999)
    elif defect == "tag":
        struct.pack_into("<I", raw, view + 796, 0x61000002)
    elif defect == "length":
        struct.pack_into("<I", raw, view + 800, 348)
    else:
        raw = view_parts([])
    index = icadkit.read(bytes(raw)).read_parts()
    assert index.profile is None
    assert index.document_kind == "unknown"
    assert index.source_length_unit is None
    if defect in ("version", "view"):
        assert index.views[0].kind == "unknown"


def test_recognized_prefix_does_not_promote_unknown_tail():
    raw = view_parts([part(ROOT, root=True), struct.pack("<I", 0x20000000)])
    index = icadkit.read(raw).read_parts()
    assert index.profile is not None
    assert index.status.index != "complete"
    assert index.opaque_ranges


def test_byte_order_and_view_classification_do_not_guess_file_kind(tmp_path):
    little = view_parts([part(ROOT, root=True)])
    span = icadkit.read(little).read_parts().views[0].byte_range
    big = bytearray(
        document_factory()(
            order="big",
            with_usr=False,
            tail=b"",
            view_payload=little[span.start + 8 : span.end - 4],
        )
    )
    big[12:16] = b"\0\x08\0\x03"
    misleading = tmp_path / "part@3d.icd"
    misleading.write_bytes(big)
    index = icadkit.read(misleading).read_parts()
    assert index.profile is None
    assert index.document_kind == "unknown"
    assert index.views[0].kind == "unknown"


def test_new_part_index_fields_preserve_previous_constructor():
    index = icadkit.read(view_parts([part(ROOT, root=True)])).read_parts()
    old = icadkit.PartIndex(
        index.source_sha256,
        index.parts,
        index.status,
        index.diagnostics,
        index.opaque_ranges,
        index.source_length_unit,
        index.definitions,
        index.length_unit_source,
    )
    assert old.profile is None and old.views == () and old.document_kind == "unknown"
    assert old.to_rows(include_root=True) == index.to_rows(include_root=True)
    assert replace(index, profile=None).to_rows() == index.to_rows()
