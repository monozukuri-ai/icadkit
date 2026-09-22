"""Native viewer geometry, partial inventory, publication, CLI and local serving."""

import hashlib
import json
import math
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from part_fixtures import ROOT, A, part, view_parts
from test_native import native

import icadkit
from icadkit.cli import main
from icadkit.viewer import ViewerLimits, serve_viewer, write_native_viewer


def document(*entities, parent_position=(1000, 2000, 3000)):
    return icadkit.read(
        view_parts(
            [
                part(ROOT, root=True, child=A, name="Root"),
                part(
                    A,
                    parent=ROOT,
                    position=parent_position,
                    name="台座",
                    comment="鋼板",
                ),
                *entities,
            ]
        )
    )


def load(doc, tmp_path, **kwargs):
    result = write_native_viewer(doc, tmp_path / "view", **kwargs)
    return result, json.loads((result.directory / "scene.json").read_bytes())


def points(scene, mesh):
    values = mesh["positions"]
    return [
        tuple(
            values[i + k] * scene["render_scale_mm"] + scene["render_origin_mm"][k]
            for k in range(3)
        )
        for i in range(0, len(values), 3)
    ]


def volume(scene, mesh):
    # Signed volume in the centered render frame: catches reversed cap winding.
    positions = mesh["positions"]
    vertices = [positions[i : i + 3] for i in range(0, len(positions), 3)]
    answer = 0
    for i in range(0, len(mesh["triangles"]), 3):
        a, b, c = [vertices[j] for j in mesh["triangles"][i : i + 3]]
        answer += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            + a[1] * (b[2] * c[0] - b[0] * c[2])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        ) / 6
    return answer * scene["render_scale_mm"] ** 3


def test_box_uses_saved_bounds_global_frame_and_mm_once(tmp_path):
    doc = document(native())
    result, scene = load(doc, tmp_path)
    mesh = next(iter(scene["meshes"].values()))
    expected = {(x, y, z) for x in (4, 17) for y in (-11, 12) for z in (7, 24)}
    for actual, target in zip(
        sorted(points(scene, mesh)), sorted(expected), strict=True
    ):
        assert actual == pytest.approx(target, abs=1e-12)
    assert volume(scene, mesh) == pytest.approx(13 * 17 * 23)
    assert result.triangle_count == 12 and result.rendered_entities == 1
    assert result.part_count == 2 and scene["parts"][1]["name"] == "台座"
    assert scene["parts"][1]["properties"][0]["value"] == "鋼板"
    assert scene["parts"][1]["world_transform"][0][3] == 1000
    assert scene["coordinate_system"] == "3DGLOBAL" and scene["length_unit"] == "mm"
    assert scene["model_status"] == result.model_status == "partial"
    assert result.source_sha256 == doc.source_sha256
    assert (
        result.scene_sha256
        == hashlib.sha256((result.directory / "scene.json").read_bytes()).hexdigest()
    )
    assert result.output_bytes == sum(
        p.stat().st_size for p in result.directory.iterdir()
    )


def test_cylinder_closed_winding_radius_height_and_rotation(tmp_path):
    _, scene = load(document(native("cylinder")), tmp_path)
    mesh = next(iter(scene["meshes"].values()))
    vertices = points(scene, mesh)
    assert vertices[-2:] == pytest.approx([(7, -11, 19), (7, 18, 19)])
    assert volume(scene, mesh) == pytest.approx(math.pi * 7**2 * 29, rel=0.002)
    assert all(
        math.hypot(x - 7, z - 19) == pytest.approx(7) for x, _, z in vertices[:-2]
    )
    assert {round(y, 8) for _, y, _ in vertices} == {-11, 18}
    # Every undirected edge has two oppositely oriented triangle uses.
    edges = {}
    for i in range(0, len(mesh["triangles"]), 3):
        a, b, c = mesh["triangles"][i : i + 3]
        for x, y in ((a, b), (b, c), (c, a)):
            edges.setdefault(tuple(sorted((x, y))), []).append((x, y))
    assert all(len(uses) == 2 and uses[0] == uses[1][::-1] for uses in edges.values())


def test_partial_unknown_and_hidden_entities_remain_owned(tmp_path):
    _, scene = load(
        document(native(visible=False, color=18), native("sphere", first=False)),
        tmp_path,
    )
    entities = scene["parts"][1]["entities"]
    assert scene["summary"]["rendered"] == scene["summary"]["omitted"] == 1
    assert entities[0]["appearance"]["visible"] is False
    assert entities[0]["appearance"]["color_index"] == 18
    assert entities[0]["entity_id"] in scene["meshes"]
    assert entities[1]["primitive"] is None
    assert entities[1]["entity_id"] not in scene["meshes"]
    assert entities[1]["owner_id"] == scene["parts"][1]["part_id"]
    assert entities[1]["diagnostics"]


def test_root_owned_geometry_empty_and_unsupported_models(tmp_path):
    for i, record in enumerate((native(), native("sphere"), b"")):
        doc = icadkit.read(view_parts([part(ROOT, root=True), record]))
        result = write_native_viewer(doc, tmp_path / str(i))
        assert result.rendered_entities == (1 if i == 0 else 0)
        assert result.omitted_entities == (1 if i == 1 else 0)
        assert result.model_status == "partial"


def test_unqualified_native_profile_is_not_a_successful_empty_view(tmp_path):
    data = bytearray(view_parts([part(ROOT, root=True), native()]))
    data[12:16] = b"\x00\x07\x00\x07"
    destination = tmp_path / "view"
    with pytest.raises(icadkit.UnsupportedFormatError, match="V7L7") as exc:
        write_native_viewer(icadkit.read(bytes(data)), destination)
    assert exc.value.diagnostic.code == "parts.profile"
    assert "not an empty model" in exc.value.diagnostic.message
    assert not destination.exists()


def test_unreadable_native_view_does_not_publish_an_inventory(tmp_path):
    # A valid outer container with a native view missing its required root.
    with pytest.raises(icadkit.InvalidFormatError, match="parts.no_root"):
        write_native_viewer(icadkit.read(view_parts([])), tmp_path / "view")
    assert not (tmp_path / "view").exists()


def test_view_cli_reports_profile_failure_without_starting_browser(
    tmp_path, monkeypatch, capsys
):
    from icadkit import viewer

    data = bytearray(view_parts([part(ROOT, root=True), native()]))
    data[12:16] = b"\x00\x07\x00\x07"
    path = tmp_path / "old-profile.icd"
    path.write_bytes(data)

    def unexpected_server(*args, **kwargs):
        raise AssertionError("An unreadable native profile must not start the viewer")

    monkeypatch.setattr(viewer, "serve_viewer", unexpected_server)
    assert main(["view", str(path), "--no-open"]) == 3
    captured = capsys.readouterr()
    assert captured.out == "" and "V7L7" in captured.err
    assert "V8L3" in captured.err
    destination = tmp_path / "view"
    assert (
        main(
            ["view", str(path), "--write-only", "--output", str(destination), "--json"]
        )
        == 3
    )
    row = json.loads(capsys.readouterr().out)
    assert row["error"]["code"] == "parts.profile"
    assert row["error"]["category"] == "unsupported"
    assert "directory" not in row and not destination.exists()


@pytest.mark.parametrize(
    "limits", [ViewerLimits(max_triangles=11), ViewerLimits(max_output_bytes=100)]
)
def test_limits_publish_nothing(limits, tmp_path):
    with pytest.raises(icadkit.LimitExceededError, match="viewer.limit_exceeded"):
        write_native_viewer(document(native()), tmp_path / "view", limits=limits)
    assert not (tmp_path / "view").exists()


def test_part_limits_and_overflow_publish_nothing(tmp_path):
    with pytest.raises(icadkit.LimitExceededError):
        write_native_viewer(
            document(native()),
            tmp_path / "view",
            part_limits=icadkit.PartLimits(max_parts=1),
        )
    with pytest.raises(icadkit.InvalidFormatError, match="viewer.coordinates"):
        write_native_viewer(
            document(native(origin=(0, 1e308, 0), parameters=(1e308, -3, -5, 10, 12))),
            tmp_path / "view",
        )
    assert not (tmp_path / "view").exists()


def test_existing_destination_is_never_overwritten(tmp_path):
    dest = tmp_path / "existing"
    dest.mkdir()
    (dest / "keep").write_text("unchanged")
    with pytest.raises(FileExistsError):
        write_native_viewer(document(native()), dest)
    assert list(dest.iterdir()) == [dest / "keep"]
    assert (dest / "keep").read_text() == "unchanged"


def test_base_install_contains_all_assets_and_serves_only_snapshot(tmp_path):
    result, _ = load(document(native()), tmp_path)
    with serve_viewer(result.directory) as server:
        with urlopen(server.url) as response:
            html = response.read()
            assert b"viewer.js" in html
            assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        with urlopen(server.url + "scene.json") as response:
            assert json.load(response)["source_sha256"] == result.source_sha256
        for name, mime in (
            ("viewer.js", "text/javascript"),
            ("viewer.css", "text/css"),
        ):
            with urlopen(Request(server.url + name, method="HEAD")) as response:
                assert response.headers["Content-Type"].startswith(mime)
                assert response.read() == b""
        (result.directory / "secret.txt").write_text("private")
        for path in ("secret.txt", "../scene.json", "%2e%2e/scene.json", ".", "sub/"):
            with pytest.raises(HTTPError) as exc:
                urlopen(server.url + path)
            assert exc.value.code == 404
        # Changing disk files cannot turn the running server into a file browser.
        (result.directory / "index.html").write_text("changed")
        with urlopen(server.url) as response:
            assert response.read() == html
    server.close()
    with pytest.raises(RuntimeError, match="closed"):
        server.start()


def test_cli_export_and_error_contract(tmp_path, capsys):
    path = tmp_path / "native.icd"
    path.write_bytes(view_parts([part(ROOT, root=True), native()]))
    args = [
        "view",
        str(path),
        "--output",
        str(tmp_path / "view"),
        "--write-only",
        "--json",
    ]
    assert main(args) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["operation"] == "view" and row["rendered_entities"] == 1
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"]["category"] == "io"
    args[3] = str(tmp_path / "limited")
    assert main([*args, "--max-triangles", "1"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["category"] == "limit_exceeded"


@pytest.mark.parametrize(
    "args",
    [
        ["--write-only"],
        ["--json"],
        ["--port", "-1"],
        ["--cylinder-segments", "7"],
        ["--max-parts", str(1 << 32)],
    ],
)
def test_cli_bad_arguments(args):
    with pytest.raises(SystemExit) as exc:
        main(["view", "unused.icd", *args])
    assert exc.value.code == 2


def test_simple_cli_temporary_output_is_cleaned_on_shutdown(
    tmp_path, monkeypatch, capsys
):
    from icadkit import cli, viewer

    path = tmp_path / "native.icd"
    path.write_bytes(view_parts([part(ROOT, root=True), native()]))
    directories = []
    original = viewer.serve_viewer

    def serving(directory, *, port):
        directories.append(Path(directory))
        return original(directory, port=port)

    def interrupt(_self):
        raise KeyboardInterrupt

    monkeypatch.setattr(viewer, "serve_viewer", serving)
    # Keep threading.Event intact for the actual HTTP server.
    monkeypatch.setattr(cli, "Event", type("Interrupted", (), {"wait": interrupt}))
    assert main(["view", str(path), "--no-open"]) == 0
    assert "http://127.0.0.1:" in capsys.readouterr().out
    assert len(directories) == 1 and not directories[0].exists()
