"""Authored saved-resource bindings, independent units/poses and failure guards."""

import json
import math
import struct
import sys

import pytest
from fixture_builders import association, document_factory, resource_factory
from part_fixtures import ROOT, A, B, part, view_parts
from preview_fixtures import box_payload
from test_csg import RESULT, RIGHT, result

import icadkit
from icadkit.cli import main
from icadkit.viewer import ViewerLimits, write_native_viewer


def resource(
    sid=RESULT, *, version=5, origin=(10, 20, 30), bad_frame=False, payload=None
):
    data, _ = resource_factory()(
        box_payload(embedded=True) if payload is None else payload,
        version=version,
        source_id=sid,
        count=0,
    )
    b = bytearray(data)
    if version in (4, 5):
        struct.pack_into("<9d", b, 32, *origin, 0, 0, 1, 2 if bad_frame else 1, 0, 0)
    return bytes(b)


def document(
    *,
    resources=None,
    markers=None,
    hidden=False,
    root_position=(100, 200, 300),
    profile="v7l7",
):
    records = [
        part(
            ROOT,
            root=True,
            profile=profile,
            position=root_position,
            axes=(0, 1, 0, 1, 0, 0),
        ),
        struct.pack("<I", 0x30010000),
    ]
    records += [result(hidden=hidden)] if markers is None else markers
    view = view_parts(records, profile=profile)
    size = int.from_bytes(view[500:504], "little") * 4
    data = bytearray(
        document_factory()(
            [resource()] if resources is None else resources,
            view_payload=view[504 : 496 + size - 4],
            tail=b"",
        )
    )
    data[12:16] = view[12:16]
    return icadkit.read(bytes(data))


def backend():
    pytest.importorskip("parasolid_kit")
    pytest.importorskip("OCP")


def test_unique_id_binding_and_provenance_not_resource_order():
    doc = document(resources=[resource(RIGHT), resource()])
    index = icadkit.read_saved_bodies(doc)
    assert index.status == "complete" and len(index.bodies) == 1
    b = index.bodies[0]
    assert b.resource_id == doc.resources[1].resource_id and b.source_id == RESULT
    assert b.raw_resource_frame == doc.source_bytes(b.resource_frame_range)
    assert b.appearance.raw_bytes == doc.source_bytes(b.byte_range)
    assert b.world_transform == (
        (1, 0, 0, -90),
        (0, 0, -1, 270),
        (0, 1, 0, -180),
        (0, 0, 0, 1),
    )
    assert b.appearance.color_index == 18 and b.appearance.layer == 9
    assert index.model_status == "partial"


def test_component_marker_does_not_become_a_final_body():
    component = bytearray(result())
    struct.pack_into("<I", component, 12, 0x550109E1)
    struct.pack_into("<I", component, 16, RIGHT)
    struct.pack_into("<I", component, 44, 0x01000044)
    doc = document(
        markers=[bytes(component), result()], resources=[resource(RIGHT), resource()]
    )
    saved = icadkit.read_saved_bodies(doc)
    assert len(saved.bodies) == 1 and saved.bodies[0].source_id == RESULT
    # CSG also distinguishes component markers, even without a usable program.
    assert len(icadkit.read_csg(doc).bodies) == 1


@pytest.mark.parametrize(
    "variant",
    [0x01000044, 0x01800044, 0x03000044, 0x03800044, 0x04000044, 0x04800044],
)
@pytest.mark.parametrize("state", [0, 0x01000000])
@pytest.mark.parametrize("profile", ["v7l7", "v8l1", "v8l2", "v8l3"])
def test_saved_result_variants_keep_exact_binding_and_frame(variant, state, profile):
    doc = document() if profile == "v7l7" else v8_document(profile=profile)
    before = icadkit.read_saved_bodies(doc).bodies[0]
    raw = bytearray(doc.source_bytes(icadkit.ByteRange(0, doc.file_size)))
    struct.pack_into("<I", raw, before.byte_range.start + 28, state)
    struct.pack_into("<I", raw, before.byte_range.start + 44, variant)
    changed = icadkit.read(bytes(raw))
    body = icadkit.read_saved_bodies(changed).bodies[0]
    assert body.status == "complete"
    assert body.resource_id == before.resource_id
    assert body.source_id == before.source_id
    assert body.world_transform == before.world_transform
    assert body.appearance.color_index == before.appearance.color_index
    assert changed.source_bytes(body.byte_range)[44:48] == struct.pack("<I", variant)
    assert changed.source_bytes(body.byte_range)[28:32] == struct.pack("<I", state)
    # Nearby flags remain unqualified; no broad mask is used.
    struct.pack_into("<I", raw, before.byte_range.start + 44, variant | 0x20000000)
    body = icadkit.read_saved_bodies(icadkit.read(bytes(raw))).bodies[0]
    assert body.status == "unsupported" and body.resource_id is None
    assert body.diagnostics[0].code == "saved.result_layout"


@pytest.mark.parametrize("state", [1, 0x02000000, 0x01000001])
def test_unreviewed_saved_marker_states_remain_unsupported(state):
    marker = bytearray(result())
    struct.pack_into("<I", marker, 28, state)
    body = icadkit.read_saved_bodies(document(markers=[marker])).bodies[0]
    assert body.status == "unsupported" and body.resource_id is None
    assert body.diagnostics[0].code == "saved.result_layout"


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"resources": []}, "saved.binding"),
        ({"resources": [resource(), resource()]}, "saved.binding"),
        ({"resources": [resource(RIGHT)]}, "saved.binding"),
        ({"markers": [result(), result()]}, "saved.binding"),
        ({"resources": [resource(version=6)]}, "saved.resource_layout"),
        ({"resources": [resource(bad_frame=True)]}, "saved.frame"),
    ],
)
@pytest.mark.parametrize("state", [0, 0x01000000])
def test_ambiguous_missing_or_unqualified_binding(kwargs, code, state):
    doc = document(**kwargs)
    raw = bytearray(doc.source_bytes(icadkit.ByteRange(0, doc.file_size)))
    for marker in icadkit.read_saved_bodies(doc).bodies:
        struct.pack_into("<I", raw, marker.byte_range.start + 28, state)
    doc = icadkit.read(bytes(raw))
    b = icadkit.read_saved_bodies(doc).bodies[0]
    assert b.status == "unsupported" and b.diagnostics[0].code == code
    with pytest.raises(icadkit.UnsupportedFormatError):
        icadkit.evaluate_saved_body(doc, b)


def test_index_profile_and_body_limit():
    doc = document(markers=[result(), result()])
    with pytest.raises(icadkit.LimitExceededError, match="max_bodies"):
        icadkit.read_saved_bodies(doc, limits=icadkit.SavedBodyLimits(max_bodies=1))
    data = bytearray(doc.source_bytes(icadkit.ByteRange(0, doc.file_size)))
    data[12:16] = b"\0\10\0\3"
    assert icadkit.read_saved_bodies(icadkit.read(bytes(data))).status == "unsupported"


def test_saved_metre_geometry_mm_placement_and_final_mesh(tmp_path):
    backend()
    doc = document(hidden=True)
    body = icadkit.read_saved_bodies(doc).bodies[0]
    mesh = icadkit.evaluate_saved_body(doc, body)
    assert mesh.volume_mm3 == pytest.approx(6000 * 1e9)
    assert mesh.area_mm2 == pytest.approx(2200 * 1e6)
    assert mesh.centroid_mm == pytest.approx((6910, -17730, 8820))
    assert mesh.face_count == 6 and mesh.solid_count == 1 and len(mesh.triangles) == 36
    assert mesh.payload_sha256 == doc.extract(body.resource_id).source.payload_sha256
    assert "restore_declared_body_resolution" in mesh.representation_operations
    result = write_native_viewer(doc, tmp_path / "saved", saved_brep=True)
    assert result.evaluated_saved_bodies == result.rendered_entities == 1
    scene = json.loads((result.directory / "scene.json").read_bytes())
    assert (
        scene["scope"] == "qualified_saved_brep"
        and scene["saved_brep"]["evaluated"] == 1
    )
    assert scene["parts"][0]["entities"][0]["appearance"]["visible"] is False
    with pytest.raises(icadkit.LimitExceededError):
        write_native_viewer(
            doc,
            tmp_path / "limited",
            saved_brep=True,
            limits=ViewerLimits(max_triangles=1),
        )
    assert not (tmp_path / "limited").exists()


def test_source_mismatch_and_limits():
    doc = document()
    b = icadkit.read_saved_bodies(doc).bodies[0]
    with pytest.raises(icadkit.InvalidFormatError, match="another document"):
        icadkit.evaluate_saved_body(document(hidden=True), b)
    with pytest.raises(icadkit.LimitExceededError, match="max_entities"):
        icadkit.evaluate_saved_body(
            doc, b, limits=icadkit.SavedBodyLimits(max_entities=1)
        )
    backend()
    for limits in (
        icadkit.SavedBodyLimits(max_subshapes=1),
        icadkit.SavedBodyLimits(max_vertices=1),
        icadkit.SavedBodyLimits(max_triangles=1),
    ):
        with pytest.raises(icadkit.LimitExceededError):
            icadkit.evaluate_saved_body(doc, b, limits=limits)
    with pytest.raises(icadkit.UnsupportedFormatError, match="source-tolerance"):
        icadkit.evaluate_saved_body(
            doc, b, limits=icadkit.SavedBodyLimits(max_source_tolerance_mm=1e-9)
        )


def test_missing_catalog_retains_diagnostic_without_mesh(tmp_path):
    payload = box_payload(embedded=True).replace(
        b"SCH_3401212_34101_13006", b"SCH_3401212_34101_99999"
    )
    doc = document(resources=[resource(payload=payload)])
    r = write_native_viewer(doc, tmp_path / "missing", saved_brep=True)
    scene = json.loads((r.directory / "scene.json").read_bytes())
    assert not scene["meshes"] and scene["saved_brep"]["omitted"] == 1
    assert (
        scene["parts"][0]["entities"][0]["saved_body"]["diagnostics"][0]["code"]
        == "schema.missing_base_schema"
    )


def test_optional_backend_failure_no_output(tmp_path, monkeypatch):
    # A None entry makes the import of the backend module raise ImportError.
    monkeypatch.setitem(sys.modules, "parasolid_kit.interop.occt", None)
    with pytest.raises(icadkit.UnsupportedFormatError, match=r"icadkit\[preview\]"):
        write_native_viewer(document(), tmp_path / "out", saved_brep=True)
    assert not (tmp_path / "out").exists()


def test_cli_positive_and_schema_arguments(tmp_path, capsys):
    backend()
    doc = document()
    p = tmp_path / "authored.bin"
    p.write_bytes(doc.source_bytes(icadkit.ByteRange(0, doc.file_size)))
    assert (
        main(
            [
                "view",
                str(p),
                "--saved-brep",
                "--write-only",
                "--json",
                "--output",
                str(tmp_path / "view"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["evaluated_saved_bodies"] == 1
    for args in (
        ["--schema", "catalog"],
        ["--schema-id", "26105"],
        ["--schema", "catalog", "--schema-id", "abc"],
        ["--schema", "catalog", "--schema-id", "26105"],
        ["--saved-brep", "--csg"],
        ["--max-bodies", "2"],
        ["--saved-brep", "--max-bodies", "0"],
        ["--saved-brep", "--max-bodies", str(2**31)],
    ):
        with pytest.raises(SystemExit) as exc:
            main(["view", str(p), *args])
        assert exc.value.code == 2


def test_cli_body_limit_is_adjustable(tmp_path, capsys):
    backend()
    doc = v8_document(
        keys=(901, 902),
        references=(901, 902),
        origins=((10, 20, 30), (-10, 50, 90)),
    )
    p = tmp_path / "authored.bin"
    p.write_bytes(doc.source_bytes(icadkit.ByteRange(0, doc.file_size)))
    common = ["view", str(p), "--saved-brep", "--write-only", "--json", "--output"]
    assert main([*common, str(tmp_path / "limited"), "--max-bodies", "1"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "saved.limit_bodies"
    assert not (tmp_path / "limited").exists()
    assert main([*common, str(tmp_path / "view"), "--max-bodies", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["evaluated_saved_bodies"] == 2


@pytest.mark.parametrize("value", [0, -1, True, 2**32])
def test_limits_reject_unbounded_values(value):
    with pytest.raises(ValueError):
        icadkit.SavedBodyLimits(max_bodies=value)


@pytest.mark.parametrize("value", [0, -1, True, math.inf, math.nan])
def test_resolution_limit(value):
    with pytest.raises(ValueError):
        icadkit.SavedBodyLimits(max_source_tolerance_mm=value)


def v8_document(
    *,
    profile="v8l3",
    keys=(901,),
    references=(901,),
    origins=((10, 20, 30),),
    duplicate_native_id=False,
    mirrored_ancestor=False,
    marker_keys=None,
    links=None,
):
    """Authored V8 markers, numbered resources and their association records.

    keys are the resource numbers; references name, per marker, the resource
    number in its association. marker_keys are the unrelated words at marker
    +32, different from every resource number unless given.
    """
    resources = [resource(sid=key, version=6) for key in keys]
    markers = []
    associations = []
    for i, (key, origin) in enumerate(zip(references, origins, strict=True)):
        data = bytearray(152)
        sid = RESULT if duplicate_native_id else RESULT + i
        struct.pack_into(
            "<12I",
            data,
            0,
            152,
            9,
            0,
            0x550108C1,
            sid,
            0,
            0xFD000080,
            0x01000000,
            7000 + i if marker_keys is None else marker_keys[i],
            0,
            0x212,
            0x01000044,
        )
        frame = (*origin, 0, 1, 0, 1, 0, 0)
        struct.pack_into("<9d", data, 48, *frame)
        struct.pack_into("<I", data, 120, 2 + i)
        markers.append(bytes(data))
        associations.append(association(sid, key, frame))
    if links is not None:
        associations = [association(*link) for link in links]
    parents = [part(ROOT, root=True, position=(100, 200, 300))]
    if mirrored_ancestor:
        parents = [
            part(ROOT, root=True, child=A),
            part(A, parent=ROOT, child=B, flags=8),
            part(B, parent=A),
        ]
    view = view_parts(
        [
            *parents,
            struct.pack("<I", 0x30010000),
            *markers,
        ]
    )
    size = int.from_bytes(view[500:504], "little") * 4
    data = bytearray(
        document_factory()(
            [*resources, *associations],
            view_payload=view[504 : 496 + size - 4],
            tail=b"",
        )
    )
    data[12:16] = {"v8l1": b"\0\10\0\1", "v8l2": b"\0\10\0\2", "v8l3": b"\0\10\0\3"}[
        profile
    ]
    return icadkit.read(bytes(data))


@pytest.mark.parametrize("profile", ["v8l1", "v8l2", "v8l3"])
def test_v8_association_supports_shared_instances_and_arbitrary_order(profile):
    doc = v8_document(
        profile=profile,
        keys=(777, 901),
        references=(901, 901),
        origins=((10, 20, 30), (-10, 50, 90)),
    )
    index = icadkit.read_saved_bodies(doc)
    assert index.status == "complete" and len(index.bodies) == 2
    assert len(doc.resource_associations) == 2
    for body in index.bodies:
        assert body.resource_id == doc.resources[1].resource_id
        assert body.resource_source_id == 901 and body.source_id != 901
        assert body.binding_kind == "resource_association"
        assert body.frame_source == "root_relative_native"
        assert body.resource_frame_range.start == body.byte_range.start + 48
        assert body.raw_resource_frame == doc.source_bytes(body.resource_frame_range)
    assert index.bodies[0].world_transform == (
        (1, 0, 0, -90),
        (0, 0, 1, -180),
        (0, -1, 0, -270),
        (0, 0, 0, 1),
    )
    assert index.bodies[1].world_transform[1][3] == -150


@pytest.mark.parametrize("profile", ["v8l1", "v8l2", "v8l3"])
@pytest.mark.parametrize("state", [0, 0x01000000])
def test_v8_marker_word_never_selects_a_resource(profile, state):
    # The words at marker +32 name the other body's resource number. The
    # association records are authoritative, as after a copy within one owner.
    doc = v8_document(
        profile=profile,
        keys=(901, 902),
        references=(902, 901),
        marker_keys=(901, 902),
        origins=((10, 20, 30), (-10, 50, 90)),
    )
    raw = bytearray(doc.source_bytes(icadkit.ByteRange(0, doc.file_size)))
    for marker in icadkit.read_saved_bodies(doc).bodies:
        struct.pack_into("<I", raw, marker.byte_range.start + 28, state)
    doc = icadkit.read(bytes(raw))
    index = icadkit.read_saved_bodies(doc)
    assert index.status == "complete" and len(index.bodies) == 2
    assert [b.resource_source_id for b in index.bodies] == [902, 901]
    assert [b.resource_id for b in index.bodies] == [
        doc.resources[1].resource_id,
        doc.resources[0].resource_id,
    ]
    assert all(len(doc.source_bytes(b.byte_range)) == 152 for b in index.bodies)


FRAME = (10, 20, 30, 0, 1, 0, 1, 0, 0)


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"links": []}, "saved.binding"),
        ({"links": [], "marker_keys": (901,)}, "saved.binding"),
        ({"links": [(RESULT, 901, FRAME), (RESULT, 901, FRAME)]}, "saved.binding"),
        ({"links": [(RESULT + 1, 901, FRAME)]}, "saved.binding"),
        ({"references": (777,)}, "saved.binding"),
        ({"keys": (901, 901)}, "saved.binding"),
        (
            {
                "references": (901, 901),
                "origins": ((0, 0, 0), (1, 2, 3)),
                "duplicate_native_id": True,
            },
            "saved.binding",
        ),
        (
            {"links": [(RESULT, 901, (11, 20, 30, 0, 1, 0, 1, 0, 0))]},
            "saved.association_frame",
        ),
    ],
)
def test_v8_missing_or_ambiguous_association_never_falls_back(kwargs, code):
    doc = v8_document(**kwargs)
    index = icadkit.read_saved_bodies(doc)
    assert index.status == "partial"
    for body in index.bodies:
        assert body.status == "unsupported" and body.world_transform is None
        assert body.diagnostics[0].code == code
        # Only a frame conflict is detected after the resource is identified.
        assert (body.resource_id is None) == (code == "saved.binding")
        with pytest.raises(icadkit.UnsupportedFormatError, match="saved.incomplete"):
            icadkit.evaluate_saved_body(doc, body)


def test_v8_mirrored_ancestor_does_not_reflect_saved_geometry_again():
    doc = v8_document(mirrored_ancestor=True)
    body = icadkit.read_saved_bodies(doc).bodies[0]
    assert body.status == "complete"
    assert body.world_transform == (
        (1, 0, 0, 10),
        (0, 0, 1, 20),
        (0, -1, 0, 30),
        (0, 0, 0, 1),
    )


@pytest.mark.parametrize("value", [2, math.nan, math.inf])
def test_v8_nonrigid_or_nonfinite_marker_frame_never_produces_a_mesh(value):
    doc = v8_document()
    body = icadkit.read_saved_bodies(doc).bodies[0]
    raw = bytearray(doc.source_bytes(icadkit.ByteRange(0, doc.file_size)))
    struct.pack_into("<d", raw, body.byte_range.start + 72, value)
    changed = icadkit.read_saved_bodies(icadkit.read(bytes(raw))).bodies[0]
    assert (
        changed.status == "unsupported" and changed.diagnostics[0].code == "saved.frame"
    )


@pytest.mark.parametrize("profile", ["v8l1", "v8l2", "v8l3"])
def test_v8_saved_root_frame_is_applied_once_and_viewer_retains_ownership(
    tmp_path, profile
):
    backend()
    doc = v8_document(profile=profile)
    body = icadkit.read_saved_bodies(doc).bodies[0]
    mesh = icadkit.evaluate_saved_body(doc, body)
    assert mesh.volume_mm3 == pytest.approx(6000 * 1e9)
    assert mesh.centroid_mm == pytest.approx((6910, 17820, -9270))
    result = write_native_viewer(doc, tmp_path / "v8", saved_brep=True)
    assert result.evaluated_saved_bodies == 1
    scene = json.loads((result.directory / "scene.json").read_bytes())
    assert scene["parts"][0]["entities"][0]["owner_id"] == body.owner_id


def test_v8_shared_resource_viewer_counts_instances_and_resources_separately(tmp_path):
    backend()
    doc = v8_document(references=(901, 901), origins=((0, 0, 0), (10, 20, 30)))
    result = write_native_viewer(doc, tmp_path / "shared", saved_brep=True)
    scene = json.loads((result.directory / "scene.json").read_bytes())
    assert result.evaluated_saved_bodies == result.rendered_entities == 2
    assert len(scene["meshes"]) == 2
    assert scene["saved_brep"]["evaluated"] == 2
    assert scene["saved_brep"]["resources_evaluated"] == scene["resource_count"] == 1


@pytest.mark.parametrize("sphere_first", [True, False])
def test_spherical_bore_shared_seams_are_independent_of_source_face_order(sphere_first):
    backend()
    from parasolid_kit.interop.occt.options import OcctConversionOptions
    from saved_fixtures import spherical_band_model

    from icadkit._csg_occt import _runtime, mesh_shape
    from icadkit._saved_occt import _build, _preflight

    model = spherical_band_model(sphere_first=sphere_first)
    limits = icadkit.SavedBodyLimits()
    _preflight(model, limits)
    shape, operations = _build(
        model,
        OcctConversionOptions(source_unit="m", target_unit="mm"),
        _runtime(),
        limits,
    )
    mesh = mesh_shape(shape, "authored", icadkit.CsgLimits(), 0.05, _runtime())
    z = math.sqrt(11**2 - 3**2)
    assert mesh.volume_mm3 == pytest.approx(4 * math.pi * z**3 / 3, rel=1e-10)
    assert mesh.area_mm2 == pytest.approx(4 * math.pi * z * (11 + 3), rel=1e-10)
    assert mesh.centroid_mm == pytest.approx((0, 0, 0), abs=1e-10)
    assert "generate_periodic_surface_seam" in operations


def _solid(model):
    from parasolid_kit.interop.occt.options import OcctConversionOptions

    from icadkit._csg_occt import _runtime, mesh_shape
    from icadkit._saved_occt import _build, _preflight

    limits = icadkit.SavedBodyLimits()
    _preflight(model, limits)
    shape, operations = _build(
        model,
        OcctConversionOptions(source_unit="m", target_unit="mm"),
        _runtime(),
        limits,
    )
    return mesh_shape(shape, "authored", icadkit.CsgLimits(), 0.05, _runtime()), (
        operations
    )


def test_elliptical_edge_of_an_oblique_cut_keeps_exact_mass_properties():
    backend()
    from saved_fixtures import oblique_cylinder_model

    mesh, _ = _solid(oblique_cylinder_model())
    # r = 7 mm, mean height h = 20 mm, cut plane z = h + x / 2. The kernel
    # integrates the trimmed faces numerically, hence the looser tolerance.
    assert mesh.volume_mm3 == pytest.approx(math.pi * 49 * 20, rel=1e-5)
    assert mesh.area_mm2 == pytest.approx(
        math.pi * 49 + 2 * math.pi * 7 * 20 + math.pi * 49 * math.sqrt(1.25), rel=1e-5
    )
    # Centroid: x = k r^2 / (4 h), z = h / 2 + k^2 r^2 / (8 h).
    assert mesh.centroid_mm == pytest.approx((0.30625, 0, 10.0765625), abs=1e-4)
    assert mesh.solid_count == 1


def test_cone_apex_vertex_loop_becomes_one_degenerated_boundary():
    backend()
    from saved_fixtures import apex_cone_model

    mesh, operations = _solid(apex_cone_model())
    # R = 6 mm, H = 8 mm, slant 10 mm.
    assert mesh.volume_mm3 == pytest.approx(math.pi * 36 * 8 / 3, rel=1e-9)
    assert mesh.area_mm2 == pytest.approx(math.pi * 36 + math.pi * 6 * 10, rel=1e-9)
    assert mesh.centroid_mm == pytest.approx((0, 0, 2), abs=1e-9)
    assert "degenerated_cone_apex_boundary" in operations


def test_vertex_loops_off_the_apex_or_on_other_surfaces_stay_unqualified():
    backend()
    from dataclasses import replace

    from saved_fixtures import apex_cone_model

    from icadkit._saved_occt import _preflight

    limits = icadkit.SavedBodyLimits()
    with pytest.raises(icadkit.UnsupportedFormatError, match="saved.half_edge"):
        _preflight(apex_cone_model(surface="sphere"), limits)
    model = apex_cone_model()
    moved = replace(
        model.points[0], position=replace(model.points[0].position, z=0.007)
    )
    with pytest.raises(icadkit.UnsupportedFormatError, match="saved.half_edge"):
        _solid(replace(model, points=(moved,)))
    # A dummy fin, or a face whose only loop is the vertex, is not an apex.
    dummy = replace(model.half_edges[2], dummy=True)
    with pytest.raises(icadkit.UnsupportedFormatError, match="saved.half_edge"):
        _preflight(replace(model, half_edges=(*model.half_edges[:2], dummy)), limits)
    lone = replace(model.faces[1], loops=(2,))
    with pytest.raises(icadkit.UnsupportedFormatError, match="saved.half_edge"):
        _preflight(replace(model, faces=(model.faces[0], lone)), limits)


def test_spun_face_closes_at_its_axis_end_with_one_degenerated_boundary():
    backend()
    from saved_fixtures import spun_dome_model

    mesh, operations = _solid(spun_dome_model())
    # A dome of radius 6 mm: half a sphere on a disc.
    assert mesh.volume_mm3 == pytest.approx(2 * math.pi * 216 / 3, rel=1e-6)
    assert mesh.area_mm2 == pytest.approx(3 * math.pi * 36, rel=1e-6)
    assert mesh.centroid_mm == pytest.approx((0, 0, 2.25), abs=1e-6)
    assert "degenerated_spun_axis_boundary" in operations
    assert "degenerated_cone_apex_boundary" not in operations


def test_spun_vertex_loops_off_the_axis_or_the_profile_ends_stay_unqualified():
    backend()
    from dataclasses import replace

    from parasolid_kit.brep.topology import Vector3
    from saved_fixtures import spun_dome_model

    from icadkit._saved_occt import _preflight

    limits = icadkit.SavedBodyLimits()
    # A vertex loop on a planar face is never a degeneracy.
    with pytest.raises(icadkit.UnsupportedFormatError, match="saved.half_edge"):
        _preflight(spun_dome_model(planar=True), limits)
    # Off the axis, or on the axis but not at an end of the profile.
    for pole in (Vector3(0.001, 0.0, 0.006), Vector3(0.0, 0.0, 0.004)):
        with pytest.raises(icadkit.UnsupportedFormatError, match="saved.half_edge"):
            _solid(spun_dome_model(pole=pole))
    # Two vertex loops are admitted on a spun face (one per profile end),
    # never a third.
    model = spun_dome_model()
    extra_loops = tuple(replace(model.loops[2], id=i, half_edges=(i,)) for i in (3, 4))
    extra_fins = tuple(
        replace(model.half_edges[2], id=i, loop=i, forward=i, backward=i)
        for i in (3, 4)
    )
    face = replace(model.faces[1], loops=(1, 2, 3, 4))
    with pytest.raises(icadkit.UnsupportedFormatError, match="saved.half_edge"):
        _preflight(
            replace(
                model,
                faces=(model.faces[0], face),
                loops=(*model.loops, *extra_loops),
                half_edges=(*model.half_edges, *extra_fins),
            ),
            limits,
        )


def test_apple_torus_face_closes_at_its_axis_point_with_one_degenerated_boundary():
    backend()
    from saved_fixtures import apple_torus_dome_model

    mesh, operations = _solid(apple_torus_dome_model())
    # R = 1 mm, r = 4 mm: the sheet rises to z = r on its outer side, curls
    # back and meets the axis at z = sqrt(r^2 - R^2). The solid of revolution
    # is the outer profile's volume up to z = r minus the curl under it.
    major, minor = 1.0, 4.0
    top = math.sqrt(minor**2 - major**2)
    angle = math.acos(-major / minor)

    def volume_to(z, sign):
        # integral of pi * (R + sign * sqrt(r^2 - z^2))^2 dz from 0 to z
        arc = z / 2 * math.sqrt(minor**2 - z**2) + minor**2 / 2 * math.asin(z / minor)
        return math.pi * ((major**2 + minor**2) * z - z**3 / 3 + sign * 2 * major * arc)

    volume = volume_to(minor, 1) - (volume_to(minor, -1) - volume_to(top, -1))
    lateral = 2 * math.pi * minor * (major * angle + minor * math.sin(angle))
    assert mesh.volume_mm3 == pytest.approx(volume, rel=1e-6)
    assert mesh.area_mm2 == pytest.approx(
        lateral + math.pi * (major + minor) ** 2, rel=1e-6
    )
    assert "degenerated_torus_axis_boundary" in operations
    # A ring torus has no axis point: its vertex loop stays unqualified.
    with pytest.raises(icadkit.UnsupportedFormatError, match="saved.half_edge"):
        _solid(apple_torus_dome_model(major=0.005, minor=0.004))


def test_bridge_maps_ellipse_and_intersection_for_saved_bodies_only():
    backend()
    from types import SimpleNamespace

    from icadkit._preview_bridge import bridge
    from icadkit.geometry import BrepEntity, NodeSource
    from icadkit.preview import PreviewError

    def node(i):
        return NodeSource(i + 1, 133, "authored", i, icadkit.ByteRange(0, 1))

    ref = {
        "node_index": 9,
        "node_type": 40,
        "type_name": "CHART",
        "node_id": 9,
        "decoded_range": (0, 1),
    }
    curves = [
        BrepEntity(
            0,
            node(0),
            {
                "kind": "ellipse",
                "owner": None,
                "sense": "positive",
                "center": (0.0, 0.0, 1.0),
                "normal": (0.0, 0.0, 1.0),
                "x_axis": (1.0, 0.0, 0.0),
                "major_radius": 3.0,
                "minor_radius": 2.0,
            },
        ),
        BrepEntity(
            1,
            node(1),
            {
                "kind": "intersection",
                "owner": None,
                "sense": "negative",
                "surfaces": (4, 7),
                "chart": ref,
                "start": ref,
                "end": ref,
                "intersection_data": None,
                "chart_points": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 1.0, 0.0)),
                "start_points": ((0.0, 0.0, 0.0),),
                "end_points": ((2.0, 1.0, 0.0),),
            },
        ),
    ]
    names = (
        "bodies", "regions", "shells", "faces", "loops", "half_edges", "edges",
        "vertices", "points", "curves", "surfaces",
    )  # fmt: skip
    brep = SimpleNamespace(
        counts={n: 2 if n == "curves" else 0 for n in names},
        entities=lambda name, start, count: curves if name == "curves" else [],
        vertex_bounds=None,
        source_format="binary",
        schema_key="authored-test",
        complete=True,
        topology_valid=True,
        closed_loop_count=0,
        closed_edge_ring_count=0,
        euler_characteristic=0,
        surface_area=None,
        volume=None,
    )
    model = bridge(brep, saved_surfaces=True)
    ellipse, intersection = (c.definition for c in model.curves)
    assert (ellipse.major_radius, ellipse.minor_radius) == (3.0, 2.0)
    assert ellipse.center.z == 1 and model.curves[0].kind.value == "ellipse"
    assert intersection.surfaces == (4, 7) and intersection.chart.node_type == 40
    assert [p.x for p in intersection.chart_points] == [0, 1, 2]
    assert intersection.end_points[0].y == 1 and intersection.intersection_data is None
    # The resource preview keeps its narrower contract.
    with pytest.raises(PreviewError, match="ellipse"):
        bridge(brep)
