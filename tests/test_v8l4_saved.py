"""Saved bodies and entities qualified from the 2026 production corpus: the
`0x550108c0` marker state on 352-byte V8L4 parts, reference planes (raw type 2)
and the 0.5 mm source-tolerance bound. Authored fixtures, not iCAD exports."""

import struct
from dataclasses import replace

import pytest
from fixture_builders import association, document_factory
from part_fixtures import ROOT, part, view_parts
from test_csg import RESULT
from test_saved import document, resource

import icadkit

PLANE_HEADER = b"\x50\x00\x4c\x41\x03\x31\x00\x00"  # opaque bytes of production planes


def marker(state=0x550108C1, *, sid=RESULT, origin=(10, 20, 30)):
    data = bytearray(152)
    struct.pack_into(
        "<12I",
        data,
        0,
        152,
        9,
        0,
        state,
        sid,
        0,
        0xFD000080,
        0x01000000,
        7000,
        0,
        0x212,
        0x01000044 if state & 0x40 else 0x01000004,
    )
    frame = (*origin, 0, 1, 0, 1, 0, 0)
    struct.pack_into("<9d", data, 48, *frame)
    struct.pack_into("<I", data, 120, 2)
    return bytes(data), frame


def saved_document(profile, state):
    """One root part of 352 bytes with one final-body marker in ``state``."""
    body, frame = marker(state)
    view = view_parts(
        [
            part(ROOT, root=True, position=(100, 200, 300), profile=profile),
            struct.pack("<I", 0x30010000),
            body,
        ],
        profile=profile,
    )
    size = int.from_bytes(view[500:504], "little") * 4
    data = bytearray(
        document_factory()(
            [resource(sid=901, version=6), association(RESULT, 901, frame)],
            view_payload=view[504 : 496 + size - 4],
            tail=b"",
        )
    )
    data[12:16] = view[12:16]
    return icadkit.read(bytes(data))


def test_c0_marker_binds_on_a_352_byte_v8l4_part():
    doc = saved_document("v8l4", 0x550108C0)
    index = icadkit.read_saved_bodies(doc)
    assert index.status == "complete" and len(index.bodies) == 1
    body = index.bodies[0]
    assert body.status == "complete"
    assert body.resource_id == doc.resources[0].resource_id
    assert body.binding_kind == "resource_association"
    assert body.appearance.visible is True


@pytest.mark.parametrize("profile", ["v8l3", "v8l2"])
def test_c0_marker_stays_unqualified_on_352_byte_parts_before_v8l4(profile):
    doc = saved_document(profile, 0x550108C0)
    index = icadkit.read_saved_bodies(doc)
    assert index.status != "complete"
    assert [b.status for b in index.bodies] == ["unsupported"]
    assert [d.code for b in index.bodies for d in b.diagnostics] == [
        "saved.result_layout"
    ]
    # The ordinary state keeps binding under the same profile.
    assert icadkit.read_saved_bodies(saved_document(profile, 0x550108C1)).status == (
        "complete"
    )


def reference_plane(
    normal=(0.0, 0.0, 1.0), *, origin=(0.0, 0.0, 0.0), layer=1, placed=False
):
    """A default plane (marker byte 0xc0) or a user-placed plane (0xc1)."""
    marker = 0x020100C1 if placed else 0x020100C0
    header = struct.pack("<6I", 104, layer, 0, marker, 0x8002B289, 0)
    retained = (-13.818205, 217.525096, 0.297918) if placed else (-7.3e-12, 0.0, 0.0)
    values = (*origin, *retained[:2], *normal, retained[2])
    return header + PLANE_HEADER + struct.pack("<9d", *values)


@pytest.mark.parametrize("profile", ["v8l4", "v8l3"])
def test_reference_planes_are_complete_entities_without_geometry(profile):
    planes = [
        reference_plane((0.0, 1.0, 0.0)),
        reference_plane((0.0, 0.0, -1.0)),
        reference_plane((1.0, 0.0, 0.0), origin=(0.0, 0.0, -112.0), layer=3),
        reference_plane(
            (0.0, -0.8660254037844386, 0.5), origin=(0.0, 0.0, -35.29), placed=True
        ),
    ]
    data = view_parts(
        [
            part(ROOT, root=True, profile=profile),
            struct.pack("<I", 0x30010000),
            *planes,
        ],
        profile=profile,
    )
    index = icadkit.read(data).read_parts()
    (root,) = index.parts
    assert [e.raw_type for e in root.entities] == [2, 2, 2, 2]
    assert all(e.geometry_status == "complete" for e in root.entities)
    assert all(e.primitive is None and not e.diagnostics for e in root.entities)
    assert root.entities[2].appearance.layer == 3
    assert root.entities[0].appearance.visible is True
    assert root.entities[0].source_id == 0x8002B289
    assert root.native_geometry_status == "complete"
    assert not index.diagnostics


def test_reference_plane_without_a_unit_normal_stays_unqualified():
    data = view_parts(
        [
            part(ROOT, root=True, profile="v8l4"),
            struct.pack("<I", 0x30010000),
            reference_plane((0.0, 0.0, 2.0)),
        ],
        profile="v8l4",
    )
    (root,) = icadkit.read(data).read_parts().parts
    entity = root.entities[0]
    assert entity.geometry_status == "unsupported"
    assert [d.code for d in entity.diagnostics] == ["native.layout"]


def test_reference_planes_stay_opaque_under_v7l7_owners():
    data = view_parts(
        [
            part(ROOT, root=True, profile="v7l7"),
            struct.pack("<I", 0x30010000),
            reference_plane(),
        ],
        profile="v7l7",
    )
    (root,) = icadkit.read(data).read_parts().parts
    assert [d.code for d in root.entities[0].diagnostics] == ["native.v7_owner"]


def test_source_tolerance_bound_admits_half_a_millimetre():
    pytest.importorskip("parasolid_kit")
    from icadkit._preview_bridge import bridge
    from icadkit._saved_occt import _preflight

    assert icadkit.SavedBodyLimits().max_source_tolerance_mm == 0.5
    doc = document()
    body = icadkit.read_saved_bodies(doc).bodies[0]
    model = bridge(doc.read_geometry(body.resource_id).brep, saved_surfaces=True)

    def with_tolerance(metres):
        edges = (replace(model.edges[0], tolerance=metres), *model.edges[1:])
        return replace(model, edges=edges)

    _preflight(with_tolerance(3e-4), icadkit.SavedBodyLimits())  # 0.3 mm
    with pytest.raises(icadkit.UnsupportedFormatError, match="tolerance"):
        _preflight(with_tolerance(6e-4), icadkit.SavedBodyLimits())  # 0.6 mm
    with pytest.raises(icadkit.UnsupportedFormatError, match="tolerance"):
        _preflight(
            with_tolerance(3e-4), icadkit.SavedBodyLimits(max_source_tolerance_mm=0.01)
        )


def reference_point(point=(11.2988, -28.3304, 8.0), *, tail=0.0):
    header = struct.pack("<6I", 80, 1, 0, 0x010100C1, 0x8000014E, 0)
    opaque = struct.pack("<4H", 56, 0x4044, 0x1102, 0)
    return header + opaque + struct.pack("<6d", 0.0, 0.0, *point, tail)


def reference_axis(point=(-47.9, -27.6, 169.4), half=16.0, *, hidden=False):
    marker = 0x05010081 if hidden else 0x050100C1
    header = struct.pack("<6I", 152, 1, 0, marker, 0x800000DE, 0)
    opaque = struct.pack("<4H", 128, 0x4444, 0x1102, 0)
    direction = (0.0, -0.8660254037844386, 0.5)
    ends = (-half, 0.0, 0.0, half, 0.0, 0.0)
    return (
        header + opaque + struct.pack("<15d", 0.0, 0.0, *point, half, *direction, *ends)
    )


def annotation(
    raw_type, marker_subtype, *, tail=b"\0" * 96, origin=(-5.0, 351.0, 125.0)
):
    """A dimension, note, text or table record: header, the 96-byte block of
    two retained values and a rigid frame, then an opaque tail."""
    marker = raw_type << 24 | marker_subtype << 16 | 0x80C0
    length = 24 + 96 + len(tail)
    header = struct.pack("<6I", length, 1, 0, marker, 0x800000B3, 0)
    opaque = struct.pack("<4H", 96, 0x4F00, 0x1302, 0)
    frame = (*origin, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0)
    return header + opaque + struct.pack("<11d", 0.0, 0.0, *frame) + tail


def read_entities(records, profile="v8l4"):
    data = view_parts(
        [
            part(ROOT, root=True, profile=profile),
            struct.pack("<I", 0x30010000),
            *records,
        ],
        profile=profile,
    )
    (root,) = icadkit.read(data).read_parts().parts
    return root


def test_reference_points_and_axes_are_complete_construction_entities():
    root = read_entities(
        [
            reference_point(),
            reference_point(tail=3.5),
            reference_axis(),
            reference_axis(hidden=True),
        ]
    )
    assert [e.kind for e in root.entities] == [
        "reference_point",
        "reference_point",
        "reference_axis",
        "reference_axis",
    ]
    assert all(
        e.geometry_status == "complete" and e.primitive is None for e in root.entities
    )
    assert all(not e.diagnostics for e in root.entities)
    assert root.entities[2].appearance.visible is True
    assert root.entities[3].appearance.visible is False
    assert root.native_geometry_status == "complete"


def test_axis_ends_must_match_the_half_length():
    bad = bytearray(reference_axis())
    struct.pack_into("<d", bad, 32 + 8 * 5, 7.0)  # half-length word, ends stay 16
    (entity,) = read_entities([bytes(bad)]).entities
    assert entity.kind is None and entity.geometry_status == "unsupported"
    assert [d.code for d in entity.diagnostics] == ["native.layout"]


@pytest.mark.parametrize(
    ("raw_type", "subtype", "kind"),
    [
        (15, 0x07, "annotation"),
        (21, 0x03, "text"),
        (22, 0x05, "note"),
        (25, 0x0D, "dimension"),
        (26, 0x10, "angular_dimension"),
        (27, 0x0B, "diameter_dimension"),
        (248, 0x0F, "coordinate_systems"),
    ],
)
def test_annotations_are_complete_entities_with_an_opaque_tail(raw_type, subtype, kind):
    (entity,) = read_entities(
        [annotation(raw_type, subtype, tail=b"\x01" * 240)]
    ).entities
    assert entity.kind == kind and entity.raw_type == raw_type
    assert entity.geometry_status == "complete" and entity.primitive is None
    assert not entity.diagnostics


def test_annotation_without_a_rigid_frame_stays_unqualified():
    bad = bytearray(annotation(25, 0x0D))
    struct.pack_into("<d", bad, 48 + 8 * 3, 0.5)  # Z axis no longer unit
    (entity,) = read_entities([bytes(bad)]).entities
    assert entity.kind is None and entity.geometry_status == "unsupported"
    assert [d.code for d in entity.diagnostics] == ["native.layout"]


def test_primitives_report_their_kind():
    from test_native import native

    root = read_entities(
        [native("box", first=False), native("cylinder", first=False)], profile="v8l3"
    )
    assert [e.kind for e in root.entities] == ["box", "cylinder"]
    assert [e.primitive.kind for e in root.entities] == ["box", "cylinder"]


def test_saved_sheet_bodies_convert_to_open_shells():
    pytest.importorskip("parasolid_kit")
    pytest.importorskip("OCP")
    import math

    from parasolid_kit.brep.topology import BodyKind
    from parasolid_kit.interop.occt.options import OcctConversionOptions
    from saved_fixtures import oblique_cylinder_model

    from icadkit._csg_occt import _runtime, mesh_shape
    from icadkit._saved_occt import _build, _preflight

    model = oblique_cylinder_model()
    # A sheet body: one region owning the one shell of its faces, as saved
    # resources store sheets; the authored solid has a material and a void region.
    sheet = replace(
        model,
        bodies=tuple(
            replace(b, kind=BodyKind.SHEET, regions=(0,)) for b in model.bodies
        ),
        regions=model.regions[:1],
        shells=model.shells[:1],
    )
    limits = icadkit.SavedBodyLimits()
    _preflight(sheet, limits)
    shape, operations = _build(
        sheet,
        OcctConversionOptions(source_unit="m", target_unit="mm"),
        _runtime(),
        limits,
    )
    assert "restore_declared_body_resolution" in operations
    mesh = mesh_shape(
        shape, "sheet", icadkit.CsgLimits(), 0.05, _runtime(), allow_sheet=True
    )
    assert mesh.solid_count == 0 and mesh.volume_mm3 == 0.0
    assert mesh.area_mm2 == pytest.approx(
        math.pi * 49 + 2 * math.pi * 7 * 20 + math.pi * 49 * math.sqrt(1.25), rel=1e-5
    )
    assert len(mesh.triangles) >= 3 and all(math.isfinite(v) for v in mesh.centroid_mm)
    # Without the sheet admission the shell is still an empty CSG result.
    with pytest.raises(icadkit.UnsupportedFormatError, match="csg.empty_result"):
        mesh_shape(shape, "sheet", icadkit.CsgLimits(), 0.05, _runtime())
    # Other body kinds stay unqualified.
    general = replace(
        model, bodies=tuple(replace(b, kind=BodyKind.GENERAL) for b in model.bodies)
    )
    with pytest.raises(icadkit.UnsupportedFormatError, match="solid, sheet and wire"):
        _preflight(general, limits)


def test_saved_wire_bodies_convert_to_their_edges():
    pytest.importorskip("parasolid_kit")
    pytest.importorskip("OCP")
    import math

    from parasolid_kit.brep.topology import BodyKind, RegionKind
    from parasolid_kit.interop.occt.options import OcctConversionOptions
    from saved_fixtures import oblique_cylinder_model

    from icadkit._csg_occt import _runtime, _shapes, mesh_shape
    from icadkit._saved_occt import _build, _preflight

    model = oblique_cylinder_model()
    # A wire body: its edges hang off the shell of a void region and there is
    # no face, as saved resources store construction curves.
    wire = replace(
        model,
        bodies=tuple(
            replace(b, kind=BodyKind.WIRE, regions=(0,)) for b in model.bodies
        ),
        regions=(replace(model.regions[0], kind=RegionKind.VOID, shells=(0,)),),
        shells=(
            replace(model.shells[0], back_faces=(), front_faces=(), wire_edges=(0, 1)),
        ),
        faces=(),
        loops=(),
        half_edges=tuple(replace(h, loop=None) for h in model.half_edges),
    )
    limits = icadkit.SavedBodyLimits()
    _preflight(wire, limits)
    api = _runtime()
    shape, operations = _build(
        wire, OcctConversionOptions(source_unit="m", target_unit="mm"), api, limits
    )
    assert "wire_body_from_edges" in operations
    wires = sum(1 for _ in _shapes(shape, api["TopAbs"].TopAbs_WIRE, api))
    edges = sum(1 for _ in _shapes(shape, api["TopAbs"].TopAbs_EDGE, api))
    assert (wires, edges) == (2, 2)
    mesh = mesh_shape(
        shape, "wire", icadkit.CsgLimits(), 0.05, api, allow_sheet=True, allow_wire=True
    )
    assert mesh.solid_count == 0 and mesh.volume_mm3 == 0.0 and mesh.area_mm2 == 0.0
    assert mesh.triangles == () and len(mesh.edges) >= 8
    assert all(math.isfinite(v) for v in mesh.centroid_mm)
    # Without the wire admission the edges are still an empty result.
    with pytest.raises(icadkit.InvalidFormatError, match="csg.empty_result"):
        mesh_shape(shape, "wire", icadkit.CsgLimits(), 0.05, api, allow_sheet=True)
