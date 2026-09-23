"""Authored saved-resource bindings, independent units/poses and failure guards."""

import json
import math
import struct

import pytest
from fixture_builders import document_factory, resource_factory
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
    if version == 5:
        struct.pack_into("<9d", b, 32, *origin, 0, 0, 1, 2 if bad_frame else 1, 0, 0)
    return bytes(b)


def document(
    *, resources=None, markers=None, hidden=False, root_position=(100, 200, 300)
):
    records = [
        part(
            ROOT,
            root=True,
            profile="v7l7",
            position=root_position,
            axes=(0, 1, 0, 1, 0, 0),
        ),
        struct.pack("<I", 0x30010000),
    ]
    records += [result(hidden=hidden)] if markers is None else markers
    view = view_parts(records, profile="v7l7")
    size = int.from_bytes(view[500:504], "little") * 4
    data = bytearray(
        document_factory()(
            [resource()] if resources is None else resources,
            view_payload=view[504 : 496 + size - 4],
            tail=b"",
        )
    )
    data[12:16] = b"\0\7\0\7"
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
def test_ambiguous_missing_or_unqualified_binding(kwargs, code):
    b = icadkit.read_saved_bodies(document(**kwargs)).bodies[0]
    assert b.status == "unsupported" and b.diagnostics[0].code == code
    with pytest.raises(icadkit.UnsupportedFormatError):
        icadkit.evaluate_saved_body(document(**kwargs), b)


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
    import icadkit._csg_occt as backend

    def missing(_):
        raise ModuleNotFoundError("missing optional backend")

    monkeypatch.setattr(backend.metadata, "version", missing)
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
    ):
        with pytest.raises(SystemExit) as exc:
            main(["view", str(p), *args])
        assert exc.value.code == 2


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
    keys=(901,),
    references=(901,),
    origins=((10, 20, 30),),
    duplicate_native_id=False,
    mirrored_ancestor=False,
):
    resources = [resource(sid=key, version=6) for key in keys]
    markers = []
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
            key,
            0,
            0x212,
            0x01000044,
        )
        struct.pack_into("<9d", data, 48, *origin, 0, 1, 0, 1, 0, 0)
        struct.pack_into("<I", data, 120, 2 + i)
        markers.append(bytes(data))
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
        document_factory()(resources, view_payload=view[504 : 496 + size - 4], tail=b"")
    )
    data[12:16] = b"\0\10\0\3"
    return icadkit.read(bytes(data))


def test_v8_explicit_resource_key_supports_shared_instances_and_arbitrary_order():
    doc = v8_document(
        keys=(777, 901), references=(901, 901), origins=((10, 20, 30), (-10, 50, 90))
    )
    index = icadkit.read_saved_bodies(doc)
    assert index.status == "complete" and len(index.bodies) == 2
    for body in index.bodies:
        assert body.resource_id == doc.resources[1].resource_id
        assert body.resource_source_id == 901 and body.source_id != 901
        assert body.binding_kind == "saved_resource_key"
        assert body.frame_source == "native_global"
        assert body.resource_frame_range.start == body.byte_range.start + 48
        assert body.raw_resource_frame == doc.source_bytes(body.resource_frame_range)
    assert index.bodies[0].world_transform == (
        (1, 0, 0, 10),
        (0, 0, 1, 20),
        (0, -1, 0, 30),
        (0, 0, 0, 1),
    )
    assert index.bodies[1].world_transform[1][3] == 50


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keys": (777,)},
        {"keys": (901, 901)},
        {
            "references": (901, 901),
            "origins": ((0, 0, 0), (1, 2, 3)),
            "duplicate_native_id": True,
        },
    ],
)
def test_v8_ambiguous_or_missing_resources_never_fall_back_to_order(kwargs):
    index = icadkit.read_saved_bodies(v8_document(**kwargs))
    assert index.status == "partial"
    assert all(
        b.status == "unsupported" and b.diagnostics[0].code == "saved.binding"
        for b in index.bodies
    )


def test_v8_mirrored_ancestor_prevents_saved_geometry():
    doc = v8_document(mirrored_ancestor=True)
    body = icadkit.read_saved_bodies(doc).bodies[0]
    assert body.status == "unsupported" and body.diagnostics[0].code == "saved.owner"


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


def test_v8_saved_global_frame_is_applied_once_and_viewer_retains_ownership(tmp_path):
    backend()
    doc = v8_document()
    body = icadkit.read_saved_bodies(doc).bodies[0]
    mesh = icadkit.evaluate_saved_body(doc, body)
    assert mesh.volume_mm3 == pytest.approx(6000 * 1e9)
    assert mesh.centroid_mm == pytest.approx((7010, 18020, -8970))
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
