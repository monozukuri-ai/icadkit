"""Authored saved-resource bindings, independent units/poses and failure guards."""

import json
import math
import struct

import pytest
from fixture_builders import document_factory, resource_factory
from part_fixtures import ROOT, part, view_parts
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
