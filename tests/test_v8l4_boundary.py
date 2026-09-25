"""Unknown-version rejection only; these are NOT real V8L4 files.

The version bytes are deliberately changed in authored synthetic fixtures.
Passing these tests qualifies rejection, never V8L4 compatibility.
"""

import json

import pytest
from drawing_fixtures import drawing, entity
from parameter_fixtures import template
from part_fixtures import nested
from test_csg import document as csg_document
from test_references import host, solid
from test_saved import document as saved_document

import icadkit
from icadkit.cli import main
from icadkit.viewer import write_native_viewer


def unsupported_input(kind):
    value = {
        "primitive": solid,
        "hierarchy": nested,
        "parameters": template,
        "reference": host,
        "saved": saved_document,
        "csg": csg_document,
        "drawing-little": lambda: drawing(
            [entity(order="little")], order="little", version=b"\0\x08\0\x03"
        ),
        "drawing-big": lambda: drawing([entity()]),
    }[kind]()
    if isinstance(value, icadkit.Document):
        value = value.source_bytes(icadkit.ByteRange(0, value.file_size))
    raw = bytearray(value)
    raw[12:16] = b"\0\x08\0\x04"
    return bytes(raw)


@pytest.mark.parametrize(
    "kind",
    [
        "primitive",
        "hierarchy",
        "parameters",
        "reference",
        "saved",
        "csg",
        "drawing-little",
        "drawing-big",
    ],
)
def test_unknown_version_never_becomes_a_successful_empty_model(kind):
    doc = icadkit.read(unsupported_input(kind))
    assert doc.header.raw_version == b"\0\x08\0\x04"
    parts = doc.read_parts()
    assert parts.profile is None and not parts.parts
    assert parts.status.index == parts.status.hierarchy == "unsupported"
    assert parts.source_length_unit is None
    assert any(d.code == "parts.profile" for d in parts.diagnostics)
    views = doc.read_views()
    assert views.status == "unsupported" and views.profile_id is None
    assert views.document_kind == "unknown"
    assert all(not v.entries and v.opaque_ranges for v in views.views)
    result = doc.read_drawing()
    assert result.status == "unsupported" and not result.entities
    parameters = doc.read_parameters()
    assert parameters.status == "unsupported" and not parameters.parameters
    for result in (icadkit.read_saved_bodies(doc), icadkit.read_csg(doc)):
        assert result.status == "unsupported" and not result.bodies


def test_unknown_host_does_not_invoke_reference_resolver():
    def resolver(_):
        pytest.fail("Unqualified host must not load external dependencies")

    doc = icadkit.read(unsupported_input("reference"))
    with pytest.raises(icadkit.UnsupportedFormatError, match="references.profile"):
        doc.read_assembly(resolver=resolver)


@pytest.mark.parametrize("mode", [{}, {"saved_brep": True}, {"csg": True}])
def test_unknown_model_does_not_publish_viewer(tmp_path, mode):
    target = tmp_path / "unqualified-viewer"
    with pytest.raises(icadkit.UnsupportedFormatError, match="parts.profile"):
        write_native_viewer(icadkit.read(unsupported_input("saved")), target, **mode)
    assert not target.exists()


def test_resource_extraction_does_not_qualify_native_version():
    original = saved_document()
    doc = icadkit.read(unsupported_input("saved"))
    assert len(doc.resources) == len(original.resources) > 0
    for a, b in zip(doc.resources, original.resources, strict=True):
        assert doc.extract_bytes(a.resource_id) == original.extract_bytes(b.resource_id)
    assert icadkit.read_saved_bodies(doc).status == "unsupported"
    assert doc.read_parts().profile is None


@pytest.mark.parametrize("operation", ["parts", "views", "drawing", "assembly"])
def test_cli_reports_unsupported_with_nonzero_exit(tmp_path, capsys, operation):
    source = tmp_path / "synthetic-unknown-version.icd"
    source.write_bytes(unsupported_input("reference"))
    args = [operation, str(source), "--json"]
    if operation == "assembly":
        args += ["--reference-root", str(tmp_path)]
    assert main(args) == 3
    result = json.loads(capsys.readouterr().out)
    if operation == "parts":
        assert result["status"]["index"] == "unsupported"
    elif operation in ("views", "drawing"):
        assert result["result"]["status"] == "unsupported"
    else:
        assert result["error"]["category"] == "unsupported"


@pytest.mark.parametrize("mode", [[], ["--saved-brep"], ["--csg"]])
def test_cli_view_does_not_create_empty_output(tmp_path, capsys, mode):
    source = tmp_path / "synthetic-unknown-version.icd"
    source.write_bytes(unsupported_input("saved"))
    target = tmp_path / "viewer"
    assert (
        main(
            [
                "view",
                str(source),
                "--write-only",
                "--output",
                str(target),
                "--json",
                *mode,
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "parts.profile"
    assert not target.exists()
