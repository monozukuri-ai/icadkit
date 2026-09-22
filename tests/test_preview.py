import hashlib
import json
import struct
import subprocess
import sys
from dataclasses import FrozenInstanceError
from importlib import metadata
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from geometry_fixtures import wire_payload
from preview_fixtures import box_payload

import icadkit as ic
from icadkit.preview import (
    PreviewError,
    PreviewLimits,
    PreviewOptions,
    serve_preview,
    write_preview,
)


@pytest.fixture
def preview_doc(document_factory, resource_factory):
    def make(payload=None):
        entity, _ = resource_factory(box_payload() if payload is None else payload)
        return ic.read(document_factory([entity]))

    return make


@pytest.fixture
def optional_backend():
    pytest.importorskip("parasolid_kit")
    pytest.importorskip("OCP")


def mesh_data(path):
    """Read GLB buffer data independently of the exporter/validator."""
    data = path.read_bytes()
    magic, version, length, size, kind = struct.unpack_from("<4sIIII", data)
    assert (magic, version, length, kind) == (b"glTF", 2, len(data), 0x4E4F534A)
    gltf = json.loads(data[20 : 20 + size])
    binary = data[28 + size :]
    assert all(set(n) <= {"mesh", "name"} for n in gltf["nodes"])

    def accessor(index):
        a = gltf["accessors"][index]
        view = gltf["bufferViews"][a["bufferView"]]
        width = {"SCALAR": 1, "VEC3": 3}[a["type"]]
        code = {5123: "H", 5125: "I", 5126: "f"}[a["componentType"]]
        fmt = "<" + code * width
        offset = view.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = view.get("byteStride", struct.calcsize(fmt))
        return [
            struct.unpack_from(fmt, binary, offset + i * stride)
            for i in range(a["count"])
        ]

    positions, triangles = [], []
    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            vertices = accessor(primitive["attributes"]["POSITION"])
            positions.extend(vertices)
            if primitive["mode"] == 4:
                indices = [i[0] for i in accessor(primitive["indices"])]
                triangles.extend(
                    tuple(vertices[i] for i in indices[j : j + 3])
                    for j in range(0, len(indices), 3)
                )
    return gltf, positions, triangles


def signed_volume(triangles):
    return (
        sum(
            a[0] * (b[1] * c[2] - b[2] * c[1])
            + a[1] * (b[2] * c[0] - b[0] * c[2])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
            for a, b, c in triangles
        )
        / 6
    )


def test_preview_import_is_lazy():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys,icadkit,icadkit.preview; "
            "assert not any(n == 'OCP' or n.startswith(('OCP.', 'parasolid_kit')) "
            "for n in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_authored_box_is_complete_without_optional_backend(preview_doc):
    doc = preview_doc()
    brep = doc.read_geometry(doc.resources[0].resource_id).require_complete().brep
    assert brep.volume == 6000
    assert brep.surface_area == 2200
    assert brep.vertex_bounds == ((2, -1, 3), (12, 19, 33))


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf"), "1"])
def test_preview_option_bounds(value):
    with pytest.raises(ValueError):
        PreviewOptions(linear_deflection=value)
    with pytest.raises(ValueError):
        PreviewLimits(max_triangles=value)


def test_preview_immutable_options():
    with pytest.raises(ValueError):
        PreviewOptions(angular_deflection=4)
    with pytest.raises(TypeError):
        PreviewOptions(include_edges=1)
    with pytest.raises(FrozenInstanceError):
        PreviewLimits().max_triangles = 1


def test_dependency_failure_is_explicit(preview_doc, tmp_path, monkeypatch):
    def missing(_):
        raise metadata.PackageNotFoundError("parasolid-kit")

    monkeypatch.setattr("icadkit.preview.metadata.version", missing)
    doc = preview_doc()
    with pytest.raises(PreviewError, match="icadkit\\[preview\\]") as exc:
        write_preview(
            doc, doc.resources[0].resource_id, tmp_path / "out", source_unit="mm"
        )
    assert exc.value.diagnostic.code == "preview.missing_dependency"
    assert not (tmp_path / "out").exists()
    monkeypatch.setattr("icadkit.preview.metadata.version", lambda _: "0.1.0")
    with pytest.raises(PreviewError, match="qualified parasolid-kit"):
        write_preview(
            doc, doc.resources[0].resource_id, tmp_path / "out", source_unit="mm"
        )


@pytest.mark.parametrize(
    "unit,scale", [("mm", 0.001), ("cm", 0.01), ("m", 1), ("in", 0.0254)]
)
def test_box_mesh_units_provenance_and_winding(
    optional_backend, preview_doc, tmp_path, unit, scale
):
    doc = preview_doc()
    rid = doc.resources[0].resource_id
    result = write_preview(doc, rid, tmp_path / "view", source_unit=unit)
    gltf, positions, triangles = mesh_data(result.glb_path)
    assert len(triangles) == result.triangle_count == 12
    assert result.face_count == 6 and result.edge_count == 12
    assert tuple(min(p[i] for p in positions) for i in range(3)) == pytest.approx(
        tuple(v * scale for v in (2, -1, 3)), rel=1e-6
    )
    assert tuple(max(p[i] for p in positions) for i in range(3)) == pytest.approx(
        tuple(v * scale for v in (12, 19, 33)), rel=1e-6
    )
    assert signed_volume(triangles) == pytest.approx(6000 * scale**3, rel=1e-6)
    manifest = json.loads(result.manifest_path.read_bytes())
    context = manifest["icadkit"]
    assert gltf["asset"]["version"] == "2.0"
    assert context["source"]["source_sha256"] == doc.source_sha256
    assert (
        context["source"]["payload_sha256"] == hashlib.sha256(box_payload()).hexdigest()
    )
    assert (
        context["scope"] == "resource_local" and context["placement"] == "not_applied"
    )
    assert context["units"] == {
        "source": unit,
        "source_basis": "caller_supplied",
        "output": "m",
    }
    assert context["status"]["model"] == "not_checked"
    assert manifest["conversion_report"]["metrics"]["volume"] == pytest.approx(
        6000 * scale**3
    )
    assert manifest["conversion_report"]["healing"]["performed"] is False
    assert result.output_bytes == sum(
        p.stat().st_size for p in result.directory.iterdir()
    )
    for artifact in [manifest["glb"], *manifest["asset_bundle"]["assets"]]:
        payload = (result.directory / artifact["filename"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
        assert len(payload) == artifact["byte_size"]
    raw = doc.read_geometry(rid).raw
    for primitive in manifest["primitives"]:
        for source in primitive["source_entities"]:
            assert (
                source["byte_range"]["start"]
                == raw.node(source["node_index"]).decoded_range.start
            )


def test_v34_bridge_does_not_reparse(
    optional_backend, preview_doc, tmp_path, monkeypatch
):
    import parasolid_kit

    def forbidden(*args, **kwargs):
        pytest.fail("optional backend must not parse the resource")

    for name in ("parse_xb", "read_brep", "parse"):
        monkeypatch.setattr(parasolid_kit, name, forbidden, raising=False)
    doc = preview_doc(box_payload(embedded=True))
    result = write_preview(
        doc, doc.resources[0].resource_id, tmp_path / "view", source_unit="mm"
    )
    assert result.triangle_count == 12 and result.edge_count == 12
    manifest = json.loads(result.manifest_path.read_bytes())
    assert manifest["icadkit"]["schema"]["schema_key"] == "SCH_3401212_34101_13006"
    _, _, triangles = mesh_data(result.glb_path)
    assert signed_volume(triangles) == pytest.approx(6e-6, rel=1e-6)


def test_wire_is_explicitly_unsupported(optional_backend, preview_doc, tmp_path):
    doc = preview_doc(wire_payload(embedded=True))
    with pytest.raises(PreviewError, match="unsupported_body"):
        write_preview(
            doc, doc.resources[0].resource_id, tmp_path / "out", source_unit="mm"
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "limit",
    [
        {"max_entities": 1},
        {"max_triangles": 1},
        {"max_output_bytes": 100},
        {"max_occt_subshapes": 1},
    ],
)
def test_failed_preview_leaves_no_output(
    optional_backend, preview_doc, tmp_path, limit
):
    doc = preview_doc()
    with pytest.raises(PreviewError) as exc:
        write_preview(
            doc,
            doc.resources[0].resource_id,
            tmp_path / "out",
            source_unit="mm",
            limits=PreviewLimits(**limit),
        )
    assert exc.value.diagnostic.category == "limit_exceeded"
    assert list(tmp_path.iterdir()) == []


def test_reject_partial_existing_and_unknown_unit(preview_doc, tmp_path):
    doc = preview_doc(wire_payload(bad_reference=True))
    with pytest.raises(ic.IcadError):
        write_preview(
            doc, doc.resources[0].resource_id, tmp_path / "out", source_unit="mm"
        )
    assert not (tmp_path / "out").exists()
    with pytest.raises(ValueError, match="source_unit"):
        write_preview(
            doc, doc.resources[0].resource_id, tmp_path / "out", source_unit="auto"
        )
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "keep").write_text("unchanged")
    with pytest.raises(FileExistsError):
        write_preview(
            doc, doc.resources[0].resource_id, tmp_path / "out", source_unit="mm"
        )
    assert (tmp_path / "out" / "keep").read_text() == "unchanged"


def test_local_server_and_faces_only(optional_backend, preview_doc, tmp_path):
    doc = preview_doc()
    result = write_preview(
        doc,
        doc.resources[0].resource_id,
        tmp_path / "view",
        source_unit="mm",
        options=PreviewOptions(include_edges=False),
    )
    assert result.edge_count == 0
    with serve_preview(result.directory) as server:
        assert server.url.startswith("http://127.0.0.1:")
        with urlopen(server.url, timeout=5) as response:
            assert b"assembly placement unknown" in response.read()
            assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        (result.directory / "private.txt").write_text("not served")
        with pytest.raises(HTTPError) as exc:
            urlopen(server.url + "private.txt", timeout=5)
        assert exc.value.code == 404


def test_preview_cli(optional_backend, preview_doc, tmp_path):
    doc = preview_doc()
    source = tmp_path / "input.icd"
    source.write_bytes(doc.source_bytes(ic.ByteRange(0, doc.file_size)))
    command = [
        sys.executable,
        "-m",
        "icadkit",
        "preview",
        str(source),
        "--resource",
        doc.resources[0].resource_id,
        "--source-unit",
        "mm",
        "--output",
        str(tmp_path / "view"),
        "--write-only",
        "--json",
    ]
    success = subprocess.run(command, capture_output=True, text=True)
    assert success.returncode == 0, success.stdout + success.stderr
    assert json.loads(success.stdout)["triangle_count"] == 12
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["error"]["category"] == "io"
    command[command.index(str(tmp_path / "view"))] = str(tmp_path / "limited")
    failed = subprocess.run(
        command + ["--max-triangles", "1"], capture_output=True, text=True
    )
    assert failed.returncode == 4
    assert json.loads(failed.stdout)["error"]["category"] == "limit_exceeded"


def test_preview_cli_requires_explicit_scope_and_unit():
    for args in (
        ["input.icd"],
        ["input.icd", "--resource", "id", "--output", "out"],
        [
            "input.icd",
            "--resource",
            "id",
            "--output",
            "out",
            "--source-unit",
            "m",
            "--json",
        ],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "icadkit", "preview", *args],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
