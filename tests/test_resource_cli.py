import json
import subprocess

import pytest


def command(*args):
    return subprocess.run(
        ["icadkit", *map(str, args)], encoding="utf-8", capture_output=True
    )


def test_list_extract_json_and_never_overwrite(
    tmp_path, document_factory, resource_factory
):
    entity, payload = resource_factory()
    source = tmp_path / "合成.icd"
    source.write_bytes(document_factory([entity]))
    result = command("resources", source, "--json")
    assert result.returncode == 0 and not result.stderr
    listing = json.loads(result.stdout)
    resource_id = listing["resources"][0]["resource_id"]
    assert listing["resource_index_status"] == "complete"
    assert listing["status"]["extraction"] == "not_checked"
    text = command("resources", source)
    assert text.returncode == 0 and resource_id in text.stdout
    output = tmp_path / "抽出.x_b"
    result = command("extract", source, resource_id, "--output", output, "--json")
    assert result.returncode == 0 and output.read_bytes() == payload
    extraction = json.loads(result.stdout)
    assert extraction["provenance"]["source_sha256"] == listing["source_sha256"]
    assert extraction["status"]["extraction"] == "complete"
    assert extraction["status"]["raw_geometry"] == "not_checked"
    result = command("extract", source, resource_id, "--output", output, "--json")
    assert result.returncode == 1 and output.read_bytes() == payload
    assert json.loads(result.stdout)["error"]["code"] == "io.already_exists"
    assert not list(tmp_path.glob(".icadkit-*"))
    other = tmp_path / "text.x_b"
    result = command("extract", source, resource_id, "--output", other)
    assert result.returncode == 0 and "SHA-256:" in result.stdout


@pytest.mark.parametrize(
    "mode,exit_code", [("unknown", 3), ("invalid", 1), ("limit", 4)]
)
def test_partial_index_exit_status(
    mode, exit_code, tmp_path, document_factory, resource_factory
):
    entity, _ = resource_factory()
    data = bytearray(entity)
    if mode == "unknown":
        data[0] = 0x99
    elif mode == "invalid":
        data[4:8] = b"\0" * 4
    source = tmp_path / "source.icd"
    source.write_bytes(document_factory([entity, data]))
    args = ["--max-resources", "1"] if mode == "limit" else []
    result = command("resources", source, "--json", *args)
    assert result.returncode == exit_code and not result.stderr
    content = json.loads(result.stdout)
    if mode != "limit":
        assert content["resource_index_status"] == "partial"
        assert len(content["resources"]) == 1
        # An unrelated unknown entity does not prevent this verified extraction.
        output = tmp_path / "known.x_b"
        result = command(
            "extract",
            source,
            content["resources"][0]["resource_id"],
            "--output",
            output,
        )
        assert result.returncode == 0


def test_failed_extraction_leaves_no_output(
    tmp_path, document_factory, resource_factory
):
    entity, _ = resource_factory(encoded=b"not zlib")
    source = tmp_path / "source.icd"
    source.write_bytes(document_factory([entity]))
    listed = json.loads(command("resources", source, "--json").stdout)
    resource = listed["resources"][0]["resource_id"]
    output = tmp_path / "failed.x_b"
    result = command("extract", source, resource, "--output", output, "--json")
    assert result.returncode == 1
    assert not output.exists() and not list(tmp_path.glob(".icadkit-*"))
    result = command("extract", source, "made-up", "--output", output, "--json")
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "resource.not_found"
