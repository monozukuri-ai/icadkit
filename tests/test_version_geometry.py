"""Version profiles, root transforms and owner guards using authored bytes."""

import json
import struct

import pytest
from fixture_builders import document_factory
from parameter_fixtures import parametric_part
from part_fixtures import ROOT, A, B, part, view_parts
from test_csg import RESULT
from test_native import native
from test_saved import backend, resource, v8_document

import icadkit
from icadkit.viewer import write_native_viewer


@pytest.mark.parametrize("profile", ["v7l6", "v8l1", "v8l2", "v8l3"])
def test_nonidentity_root_normalizes_parts_and_primitives_once(profile, tmp_path):
    doc = icadkit.read(
        view_parts(
            [
                part(
                    ROOT,
                    root=True,
                    child=A,
                    position=(100, 200, 300),
                    axes=(0, 1, 0, 0, 0, 1),
                    profile=profile,
                ),
                part(A, parent=ROOT, child=B, position=(10, 20, 30), profile=profile),
                part(B, parent=A, position=(13, 27, 41), profile=profile),
                native(origin=(17, 31, 53)),
            ],
            profile=profile,
        )
    )
    index = doc.read_parts()
    root, parent, child = index.parts
    assert root.placement.world_transform == (
        (1, 0, 0, 0),
        (0, 1, 0, 0),
        (0, 0, 1, 0),
        (0, 0, 0, 1),
    )
    assert tuple(r[3] for r in parent.placement.world_transform[:3]) == (
        -270,
        -90,
        -180,
    )
    assert tuple(r[3] for r in child.placement.local_transform[:3]) == (3, 7, 11)
    primitive = child.entities[0].primitive
    assert tuple(r[3] for r in primitive.world_transform[:3]) == (-247, -83, -169)
    result = write_native_viewer(doc, tmp_path / profile)
    scene = json.loads((result.directory / "scene.json").read_bytes())
    assert result.rendered_entities == 1 and scene["length_unit"] == "mm"
    assert scene["parts"][2]["entities"][0]["primitive"]["world_transform"] == [
        list(r) for r in primitive.world_transform
    ]


@pytest.mark.parametrize("profile", ["v7l6", "v8l1", "v8l2", "v8l3"])
@pytest.mark.parametrize("flags", [0x50, 0x58])
def test_external_owners_and_descendants_never_supply_native_meshes(
    profile, flags, tmp_path
):
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, profile=profile),
                part(A, parent=ROOT, child=B, flags=flags, profile=profile),
                native(),
                part(B, parent=A, profile=profile),
                native(),
            ],
            profile=profile,
        )
    )
    parts = doc.read_parts().parts
    assert parts[2].placement.world_transform is None
    assert all(e.primitive is None for p in parts for e in p.entities)
    assert write_native_viewer(doc, tmp_path / profile).rendered_entities == 0


@pytest.mark.parametrize("profile", ["v7l6", "v8l1", "v8l2"])
@pytest.mark.parametrize("tail", ["unknown", "component", "unparsed"])
def test_older_v8_owner_must_be_complete_before_promoting_primitives(profile, tail):
    extra = struct.pack("<I", 0x20000000)
    if tail == "unknown":
        extra = struct.pack("<6I", 24, 1, 0, 0x4C0100C1, RESULT, 0)
    elif tail == "component":
        extra = bytearray(native(first=False))
        struct.pack_into("<I", extra, 12, 0x4B0109E1)
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, profile=profile),
                native(),
                extra,
            ],
            profile=profile,
        )
    )
    assert all(e.primitive is None for e in doc.read_parts().parts[0].entities)


def legacy_template(*, profile="v8l1", defect=None, origin=(0, 0, 0), flags=0x550108C1):
    marker = bytearray(
        v8_document().source_bytes(
            icadkit.read_saved_bodies(v8_document()).bodies[0].byte_range
        )
    )
    struct.pack_into("<9d", marker, 48, *origin, 0, 0, 1, 1, 0, 0)
    struct.pack_into("<I", marker, 12, flags)
    root = parametric_part(root=True, profile=profile)
    resources = [resource(sid=RESULT, origin=origin)]
    if defect == "competing_key":
        resources.append(resource(sid=901, version=6))
    if defect == "duplicate":
        resources += resources
    if defect == "resource_frame":
        resources = [resource(sid=RESULT, origin=(1, 0, 0))]
    if defect == "marker_frame":
        struct.pack_into("<d", marker, 48, 1)
    if defect == "root_frame":
        root = parametric_part(root=True, position=(1, 0, 0), profile=profile)
    if defect == "plain_owner":
        root = part(ROOT, root=True, profile=profile)
    view = view_parts([root, struct.pack("<I", 0x30010000), marker], profile=profile)
    span = icadkit.read(view).read_parts().views[0].byte_range
    data = bytearray(
        document_factory()(
            resources, view_payload=view[span.start + 8 : span.end - 4], tail=b""
        )
    )
    data[12:16] = view[12:16]
    return icadkit.read(bytes(data))


def test_v8l1_original_template_source_id_binding_and_evaluation():
    doc = legacy_template()
    (body,) = icadkit.read_saved_bodies(doc).bodies
    assert body.status == "complete" and body.binding_kind == "native_source_id"
    assert body.resource_source_id == body.source_id == RESULT
    assert body.world_transform[0][3] == 0
    backend()
    mesh = icadkit.evaluate_saved_body(doc, body)
    assert mesh.volume_mm3 == pytest.approx(6000e9)
    assert mesh.centroid_mm == pytest.approx((7000, 9000, 18000))


@pytest.mark.parametrize(
    "defect",
    [
        "competing_key",
        "duplicate",
        "resource_frame",
        "marker_frame",
        "root_frame",
        "plain_owner",
    ],
)
def test_v8l1_template_binding_rejects_ambiguous_or_unproven_layouts(defect):
    (body,) = icadkit.read_saved_bodies(legacy_template(defect=defect)).bodies
    assert body.status == "unsupported"
    assert body.diagnostics[0].code in ("saved.binding", "saved.legacy_template")


def test_v8l2_does_not_inherit_v8l1_source_id_template_policy():
    (body,) = icadkit.read_saved_bodies(legacy_template(profile="v8l2")).bodies
    assert body.status == "unsupported" and body.diagnostics[0].code == "saved.binding"


@pytest.mark.parametrize("flags", [0x550108C1, 0x550108C0])
def test_legacy_template_agreeing_nonzero_frames_are_not_dropped(flags):
    doc = legacy_template(origin=(7, -11, 19), flags=flags)
    (body,) = icadkit.read_saved_bodies(doc).bodies
    assert body.status == "complete"
    assert tuple(r[3] for r in body.world_transform[:3]) == (7, -11, 19)
