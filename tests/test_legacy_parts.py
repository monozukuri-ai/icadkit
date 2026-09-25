"""Independently authored old V7 layouts and fail-closed boundaries."""

import struct

import pytest
from parameter_fixtures import parametric_part, preamble
from part_fixtures import ROOT, A, part, view_parts
from test_native import native
from test_saved import document as saved_document

import icadkit

LEGACY = [f"v7l{i}" for i in range(2, 7)]


def metadata():
    raw = bytearray(260)
    struct.pack_into("<5I", raw, 0, 0x21000000, 256, 2, 0, 0xC9500088)
    struct.pack_into("<3I", raw, 160, 0x81000050, 0, 1)
    # Plausible entity/part signatures inside an opaque envelope are ignored.
    raw[180:188] = part(A)[:8]
    struct.pack_into("<I", raw, 240, 0x79000014)
    return raw


@pytest.mark.parametrize("profile", LEGACY)
def test_counted_legacy_metadata_and_preamble_preserve_raw_ranges(profile):
    doc = icadkit.read(
        view_parts(
            [preamble(), part(ROOT, root=True, profile=profile), metadata()],
            profile=profile,
        )
    )
    ix = doc.read_parts()
    assert ix.status.index == ix.status.hierarchy == "complete"
    assert len(ix.parts) == 1 and not ix.parts[0].entities
    opaque = next(r for r in ix.opaque_ranges if r.reason == "entity_metadata")
    assert doc.source_bytes(opaque.byte_range) == metadata()
    with pytest.raises(icadkit.LimitExceededError):
        doc.read_parts(limits=icadkit.PartLimits(max_entities=1))


@pytest.mark.parametrize("profile", LEGACY)
@pytest.mark.parametrize(
    "offset,value", [(8, 1), (160, 0x8100004C), (164, 1), (168, 0), (240, 0x79000018)]
)
def test_unqualified_nested_metadata_stops_without_resynchronizing(
    profile, offset, value
):
    raw = metadata()
    struct.pack_into("<I", raw, offset, value)
    ix = icadkit.read(
        view_parts(
            [part(ROOT, root=True, profile=profile), raw, part(A, profile=profile)],
            profile=profile,
        )
    ).read_parts()
    assert ix.status.index == "partial" and len(ix.parts) == 1
    assert any(d.code == "parts.metadata_layout" for d in ix.diagnostics)


@pytest.mark.parametrize("profile", LEGACY)
def test_wrong_tag_extended_owner_and_non3d_view_never_gain_a_profile(profile):
    for records, prefix in (
        ([part(ROOT, root=True, profile="v8l3")], None),
        ([parametric_part(root=True, profile=profile)], None),
        ([part(ROOT, root=True, profile=profile)], bytes(796)),
    ):
        ix = icadkit.read(
            view_parts(records, prefix=prefix, profile=profile)
        ).read_parts()
        assert ix.profile is None and not ix.parts
        assert ix.source_length_unit is None


@pytest.mark.parametrize("profile", LEGACY[:3])
def test_old_index_qualifies_frames_but_retains_geometry_as_opaque(profile):
    doc = icadkit.read(
        view_parts(
            [
                part(
                    ROOT, root=True, child=A, position=(100, 200, 300), profile=profile
                ),
                part(A, parent=ROOT, position=(107, 211, 313), profile=profile),
                native(),
            ],
            profile=profile,
        )
    )
    ix = doc.read_parts()
    assert ix.status.index == ix.status.placements == "complete"
    child = ix.parts[1]
    assert tuple(row[3] for row in child.placement.world_transform[:3]) == (7, 11, 13)
    assert child.entities[0].primitive is None
    assert child.entities[0].diagnostics[0].code == "native.legacy_inventory"
    assert ix.status.native_geometry == ix.status.appearance == "unsupported"
    assert icadkit.read_saved_bodies(doc).status == "unsupported"


@pytest.mark.parametrize(
    "defect", [None, "missing", "duplicate", "frame", "wrong_version"]
)
def test_v7l6_saved_binding_retains_existing_resource_guards(defect):
    from test_saved import resource

    resources = [resource(version=4)]
    if defect == "missing":
        resources = []
    elif defect == "duplicate":
        resources = [resource(version=4), resource(version=4)]
    elif defect == "frame":
        resources = [resource(version=4, bad_frame=True)]
    elif defect == "wrong_version":
        resources = [resource(version=5)]
    source = saved_document(resources=resources)
    raw = bytearray(source.source_bytes(icadkit.ByteRange(0, source.file_size)))
    raw[12:16] = b"\0\7\0\6"
    doc = icadkit.read(bytes(raw))
    bodies = icadkit.read_saved_bodies(doc)
    assert bodies.bodies[0].status == ("complete" if defect is None else "unsupported")
    assert icadkit.read_csg(doc).status == "unsupported"


@pytest.mark.parametrize("offset", [8, 260, 264, 268, 272])
def test_v7l5_child_layout_is_not_inferred_from_other_versions(offset):
    raw = bytearray(part(ROOT, root=True, profile="v7l5"))
    struct.pack_into("<I", raw, offset, A if offset != 8 else 0)
    ix = icadkit.read(view_parts([raw], profile="v7l5")).read_parts()
    assert ix.profile is None and not ix.parts
    assert any(d.code == "parts.legacy_root_only" for d in ix.diagnostics)
