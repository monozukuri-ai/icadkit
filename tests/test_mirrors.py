"""Authored reflection mathematics and saved-state guards; no vendor fixtures."""

import math
import struct

import pytest
from part_fixtures import ROOT, A, B, extra_info, part, view_parts
from test_native import native

import icadkit
from icadkit.parts import _determinant
from icadkit.viewer import _mesh

PROFILES = ["v7l6", "v7l7", "v8l1", "v8l2", "v8l3"]


def signed_volume(mesh):
    points = list(zip(*[iter(mesh["positions"])] * 3, strict=True))
    volume = 0
    for i in range(0, len(mesh["triangles"]), 3):
        a, b, c = [points[j] for j in mesh["triangles"][i : i + 3]]
        volume += (
            sum(
                a[k]
                * (b[(k + 1) % 3] * c[(k + 2) % 3] - b[(k + 2) % 3] * c[(k + 1) % 3])
                for k in range(3)
            )
            / 6
        )
    return volume


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("child_mirror", [False, True])
def test_occurrence_parity_is_absolute_and_local_parity_is_composed(
    profile, child_mirror
):
    ix = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, profile=profile),
                part(A, parent=ROOT, child=B, flags=8, profile=profile),
                part(B, parent=A, flags=8 if child_mirror else 0, profile=profile),
            ],
            profile=profile,
        )
    ).read_parts()
    parent, child = ix.parts[1:]
    assert ix.profile.mirror_policy == "stored_parity"
    assert _determinant(parent.placement.world_transform) == 1
    assert _determinant(parent.placement.orientation_world_transform) == -1
    assert _determinant(child.placement.orientation_world_transform) == (
        -1 if child_mirror else 1
    )
    assert _determinant(child.placement.orientation_local_transform) == (
        1 if child_mirror else -1
    )


@pytest.mark.parametrize("profile", PROFILES)
def test_asymmetric_box_uses_signed_height_once_and_keeps_outward_winding(profile):
    # Reflect an off-centre box across global Z=19: (x,y,z) -> (x,y,38-z).
    params = (-13, 2, 3, 7, 11)
    raw = native(
        mirror=True, origin=(7, -11, 19), axes=(0, 0, 1, 1, 0, 0), parameters=params
    )
    doc = icadkit.read(
        view_parts([part(ROOT, root=True, profile=profile), raw], profile=profile)
    )
    entity = doc.read_parts().parts[0].entities[0]
    shape = entity.primitive
    assert entity.is_mirror and shape.height == 13
    assert shape.mirror_convention == "signed_height"
    assert _determinant(shape.world_transform) == -1
    assert struct.unpack_from("<d", shape.raw_bytes, 72)[0] == -13
    mesh = _mesh(shape, 24)
    points = list(zip(*[iter(mesh["positions"])] * 3, strict=True))
    assert set(points) == {(x, y, z) for x in (9, 14) for y in (-8, 0) for z in (6, 19)}
    assert signed_volume(mesh) == pytest.approx(520)


@pytest.mark.parametrize(
    "kind,parameters",
    [
        ("cylinder", (7, 29)),
        ("sphere", (9, 9, math.pi)),
        ("cone", (-27, 0, 0, 0, 0, 9, 3)),
        ("torus", (-math.tau, 13, 2.5, 0)),
    ],
)
def test_mirrored_analytic_primitives_have_positive_outward_mesh_volume(
    kind, parameters
):
    doc = icadkit.read(
        view_parts(
            [part(ROOT, root=True), native(kind, mirror=True, parameters=parameters)]
        )
    )
    shape = doc.read_parts().parts[0].entities[0].primitive
    assert shape is not None and signed_volume(_mesh(shape, 48)) > 0


@pytest.mark.parametrize("profile", PROFILES)
def test_mirrored_extended_text_keeps_owner_and_does_not_hide_primitives(profile):
    text = bytearray(extra_info("鏡映属性"))
    struct.pack_into("<I", text, 16, 0xCF011081)
    doc = icadkit.read(
        view_parts(
            [part(ROOT, root=True, profile=profile), text, native(first=False)],
            profile=profile,
        )
    )
    owner = doc.read_parts().parts[0]
    assert owner.extra_info == "鏡映属性" and len(owner.entities) == 1
    assert owner.entities[0].primitive is not None
    with pytest.raises(icadkit.LimitExceededError):
        doc.read_parts(limits=icadkit.PartLimits(max_property_bytes=2))


@pytest.mark.parametrize("profile", ["v7l2", "v7l3", "v7l4"])
def test_unqualified_older_mirror_keeps_transforms_unavailable(profile):
    ix = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, profile=profile),
                part(A, parent=ROOT, flags=8, profile=profile),
            ],
            profile=profile,
        )
    ).read_parts()
    assert ix.profile.mirror_policy is None
    assert ix.parts[1].placement.world_transform is None
    assert ix.parts[1].placement.orientation_world_transform is None


@pytest.mark.parametrize("mirror,height", [(True, 13), (False, -13)])
def test_inconsistent_box_mirror_and_signed_height_remain_invalid(mirror, height):
    doc = icadkit.read(
        view_parts(
            [
                part(ROOT, root=True),
                native(mirror=mirror, parameters=(height, 2, 3, 7, 11)),
            ]
        )
    )
    entity = doc.read_parts().parts[0].entities[0]
    assert entity.primitive is None and entity.geometry_status == "invalid"
