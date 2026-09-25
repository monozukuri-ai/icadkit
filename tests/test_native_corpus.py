"""Offline evidence checks use synthetic inputs, not vendor CAD or SDK files."""

import copy
import hashlib
import json
import runpy
from pathlib import Path

import pytest
from parameter_fixtures import template
from part_fixtures import ROOT, A, B, part, view_parts

import icadkit

RUNNER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/verify_native_corpus.py")
)


def test_saved_parameter_comparison_checks_each_field_and_sdk_rounding():
    doc = icadkit.read(template())
    oracle = {
        "parameters": [
            {
                "Name": "width",
                "Type": 1,
                "dvalue": 19.125,
                "Equation": "",
                "Comment": "長さ条件",
                "Explain": "説明",
                "status": True,
            }
        ]
    }
    compare = RUNNER["compare_parameters"]
    assert compare(doc, oracle)["status"] == "matched"
    for field, value in {
        "Name": "different",
        "Type": 2,
        "dvalue": 19.126,
        "Equation": "2*width",
        "Comment": "",
        "Explain": "",
        "status": False,
    }.items():
        changed = copy.deepcopy(oracle)
        changed["parameters"][0][field] = value
        assert compare(doc, changed)["status"] == "mismatch"
    assert compare(doc, {"parameters": None})["status"] == "not_comparable"
    assert compare(doc, {"parameters": []})["status"] == "mismatch"


def test_parameter_oracle_is_hash_checked_and_mismatch_fails_manifest(corpus):
    manifest, roots = corpus
    row = manifest["files"][0]
    source = roots["test"] / "input.icd"
    source.write_bytes(template())
    row["input"]["sha256"] = sha(source)
    doc = icadkit.read(source)
    row["baseline"] = RUNNER["snapshot"](doc, doc.read_parts())
    row.pop("oracle")
    oracle = roots["test"] / "parameters.json"
    oracle.write_text('{"parameters": []}')
    row["parameter_oracle"] = {
        "scope": "root_saved_parameters",
        "file": {"root": "test", "path": oracle.name, "sha256": sha(oracle)},
    }
    result = RUNNER["verify"](manifest, roots)
    assert result["parameters"] == {"mismatch": 1} and not result["passed"]
    oracle.write_text('{"parameters": null}')
    with pytest.raises(ValueError, match="changed input"):
        RUNNER["verify"](manifest, roots)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "input.icd"
    path.write_bytes(view_parts([part(ROOT, root=True, child=A), part(A, parent=ROOT)]))
    doc = icadkit.read(path)
    cs = dict(
        zip(
            ("xvec", "yvec", "zvec", "org"),
            [
                dict(zip("xyz", p, strict=True))
                for p in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0))
            ],
            strict=True,
        )
    )
    info = {
        "name": "部品",
        "comment": "",
        "is_mirror": False,
        "is_external": False,
        "cs": cs,
    }
    oracle = tmp_path / "oracle.json"
    oracle.write_text(
        json.dumps(
            {
                "capture_mode": "document",
                "tree": {"info": info, "children": [{"info": info, "children": []}]},
            }
        )
    )

    def spec(p):
        return {"root": "test", "path": p.name, "sha256": sha(p)}

    row = {
        "id": "authored",
        "input": spec(path),
        "family_id": "family-a",
        "partition": "development",
        "baseline": RUNNER["snapshot"](doc, doc.read_parts()),
        "oracle": {"file": spec(oracle), "scope": "saved_document"},
    }
    return {"schema_version": 1, "files": [row]}, {"test": tmp_path}


def test_offline_inventory_and_profile_report(corpus):
    manifest, roots = corpus
    result = RUNNER["verify"](manifest, roots)
    assert result["passed"] and result["baseline_equal"] == 1
    assert result["sdk"] == {"matched": 1}
    assert result["rows"][0]["sdk"]["frames_checked"] == 1
    assert result["rows"][0]["document_kind"] == "unknown"


@pytest.mark.parametrize("which", ["input", "oracle"])
@pytest.mark.parametrize("change", ["missing", "changed"])
def test_required_evidence_is_never_skipped(corpus, which, change):
    manifest, roots = corpus
    path = roots["test"] / ("input.icd" if which == "input" else "oracle.json")
    if change == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="required input missing|changed input"):
        RUNNER["verify"](manifest, roots)


def test_insertion_oracle_cannot_certify_raw_library_input(corpus):
    manifest, roots = corpus
    spec = manifest["files"][0]["oracle"]
    path = roots["test"] / "oracle.json"
    data = json.loads(path.read_text())
    data["capture_mode"] = "library_part_insertion_into_new_document"
    path.write_text(json.dumps(data))
    spec["file"]["sha256"] = sha(path)
    with pytest.raises(ValueError, match="scope contradicts"):
        RUNNER["verify"](manifest, roots)
    spec["scope"] = "library_part_insertion"
    result = RUNNER["verify"](manifest, roots)
    assert result["sdk"] == {"not_comparable": 1}


def test_partition_and_shared_payload_leakage_rejected(corpus):
    manifest, _ = corpus
    second = copy.deepcopy(manifest["files"][0])
    second["id"] = "resaved"
    second["partition"] = "validation"
    manifest["files"].append(second)
    with pytest.raises(ValueError, match="family split leakage"):
        RUNNER["validate_manifest"](manifest)
    second["family_id"] = "pretend-independent"
    with pytest.raises(ValueError, match="identical input"):
        RUNNER["validate_manifest"](manifest)


def test_baseline_or_sdk_difference_is_a_failure(corpus):
    manifest, roots = corpus
    manifest["files"][0]["baseline"]["snapshot_sha256"] = "wrong"
    assert not RUNNER["verify"](manifest, roots)["passed"]
    path = roots["test"] / "oracle.json"
    data = json.loads(path.read_text())
    data["tree"]["children"][0]["info"]["cs"]["org"]["x"] = 100
    path.write_text(json.dumps(data))
    manifest["files"][0]["oracle"]["file"]["sha256"] = sha(path)
    assert RUNNER["verify"](manifest, roots)["sdk"] == {"mismatch": 1}


def test_missing_sdk_values_remain_unverified(corpus):
    manifest, roots = corpus
    path = roots["test"] / "oracle.json"
    data = json.loads(path.read_text())
    data["tree"]["children"][0]["info"]["cs"] = {"error": "unavailable"}
    path.write_text(json.dumps(data))
    manifest["files"][0]["oracle"]["file"]["sha256"] = sha(path)
    row = RUNNER["verify"](manifest, roots)["rows"][0]["sdk"]
    assert row["frames_checked"] == 0 and row["frames_unverified"] == 1
    assert row["status"] == "partial"


def test_unloaded_placeholder_checks_frame_without_inventing_saved_properties(corpus):
    manifest, roots = corpus
    path = roots["test"] / "oracle.json"
    data = json.loads(path.read_text())
    info = data["tree"]["children"][0]["info"]
    info.update(is_unloaded=True, comment="placeholder", is_external=True)
    path.write_text(json.dumps(data))
    manifest["files"][0]["oracle"]["file"]["sha256"] = sha(path)
    row = RUNNER["verify"](manifest, roots)["rows"][0]["sdk"]
    assert row["frames_checked"] == 1 and row["saved_properties_unverified"] == 2
    assert row["status"] == "partial"
    info["cs"]["org"]["x"] = 10
    path.write_text(json.dumps(data))
    manifest["files"][0]["oracle"]["file"]["sha256"] = sha(path)
    assert RUNNER["verify"](manifest, roots)["sdk"] == {"mismatch": 1}


def test_root_escape_and_unexpected_dependency_are_rejected(corpus):
    manifest, roots = corpus
    manifest["files"][0]["absent_files"] = [{"root": "test", "path": "input.icd"}]
    with pytest.raises(ValueError, match="expected missing"):
        RUNNER["verify"](manifest, roots)
    manifest["files"][0]["input"]["path"] = "../input.icd"
    with pytest.raises(ValueError, match="unsafe manifest path"):
        RUNNER["verify"](manifest, roots)


def test_repeated_parent_paths_do_not_certify_occurrence_ownership(corpus):
    manifest, roots = corpus
    row = manifest["files"][0]
    path = roots["test"] / "input.icd"
    c, d = 0xA0000010, 0xA0000011
    path.write_bytes(
        view_parts(
            [
                part(ROOT, root=True, child=A),
                part(A, parent=ROOT, child=c, next_id=B),
                part(c, parent=A, name="nested"),
                part(B, parent=ROOT, previous=A, child=d),
                part(d, parent=B, name="nested"),
            ]
        )
    )
    doc = icadkit.read(path)
    row["input"]["sha256"] = sha(path)
    row["baseline"] = RUNNER["snapshot"](doc, doc.read_parts())
    oracle = roots["test"] / "oracle.json"
    data = json.loads(oracle.read_text())
    node = data["tree"]["children"][0]
    child = copy.deepcopy(node)
    child["info"]["name"] = "nested"
    node["children"] = [child]
    data["tree"]["children"].append(copy.deepcopy(node))
    oracle.write_text(json.dumps(data))
    row["oracle"]["file"]["sha256"] = sha(oracle)
    sdk = RUNNER["verify"](manifest, roots)["rows"][0]["sdk"]
    assert sdk["frames_checked"] == 4
    assert sdk["ambiguous_parent_paths"] == 1
    assert sdk["status"] == "partial"
