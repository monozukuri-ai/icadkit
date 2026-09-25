"""Synthetic resolution graphs and independent placement expectations."""

import json
import math
import os
from pathlib import Path

import pytest
from part_fixtures import ROOT, A, B, part, view_parts
from test_mirrors import signed_volume
from test_native import native

import icadkit
from icadkit.parts import _determinant
from icadkit.viewer import ViewerLimits, _mesh, write_assembly_viewer


def host(*, names=("child", "child"), profile="v8l3", mirror=False):
    records = [part(ROOT, root=True, child=A, profile=profile)]
    for i, name in enumerate(names):
        records.append(
            part(
                A if i == 0 else B,
                parent=ROOT,
                previous=A if i else 0,
                next_id=B if i == 0 and len(names) == 2 else 0,
                name=f"instance{i}",
                flags=0x58 if mirror else 0x50,
                reference=name,
                position=(100 + 50 * i, 200, 300),
                axes=(0, 0, 1, 0, 1, 0),
                profile=profile,
            )
        )
    return view_parts(records, profile=profile)


def solid(*, root=(0, 0, 0), position=(1, 2, 3), mirrored=False, profile="v8l3"):
    return view_parts(
        [
            part(ROOT, root=True, position=root, profile=profile),
            native(
                origin=position,
                axes=(0, 0, 1, 1, 0, 0),
                mirror=mirrored,
                parameters=(-7 if mirrored else 7, 2, 3, 9, 13),
            ),
        ],
        profile=profile,
    )


def write(folder, name, data):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def assemble(folder, data=None, **kwargs):
    return icadkit.read_assembly(
        host() if data is None else data, search_roots=[folder], **kwargs
    )


def test_policy_is_explicit_and_single_file_default_never_opens_references(tmp_path):
    write(tmp_path, "child.icd", solid())
    doc = icadkit.read(host())
    assert all(
        p.external_reference.status == "not_loaded"
        for p in doc.read_parts().parts
        if p.is_external
    )
    with pytest.raises(ValueError, match="Supply"):
        icadkit.read_assembly(doc)
    with pytest.raises(ValueError, match="exclusively"):
        icadkit.read_assembly(doc, search_roots=[tmp_path], resolver=lambda _: None)
    assert doc.read_assembly(search_roots=[tmp_path]).status == "complete"
    assert all(
        p.external_reference.path is None
        for p in doc.read_parts().parts
        if p.is_external
    )


@pytest.mark.parametrize("profile", ["v7l6", "v7l7", "v8l1", "v8l2", "v8l3"])
@pytest.mark.parametrize("mirror", [False, True])
def test_shared_definition_distinct_occurrence_ids_and_independent_geometry(
    tmp_path, profile, mirror
):
    write(
        tmp_path,
        "child.icd",
        solid(root=(10, 20, 30), position=(11, 22, 33), profile=profile),
    )
    a = assemble(tmp_path, host(profile=profile, mirror=mirror))
    assert a.status == "complete" and len(a.documents) == 2
    assert a.document_attempts == 2
    assert [r.status for r in a.references] == ["resolved", "resolved"]
    first, second = a.occurrences[1:]
    assert first.definition_document_id == second.definition_document_id
    assert first.source_document_id != first.definition_document_id
    e1, e2 = first.entities[0], second.entities[0]
    assert e1.entity_id != e2.entity_id and e1.owner_id != e2.owner_id
    assert e1.source_entity is e2.source_entity
    assert e1.primitive.raw_bytes == e2.primitive.raw_bytes
    # The target reader normalizes its root; a reference restores that basis.
    # The stored origin (11,22,33) undergoes host Rz=90 and translation.
    # Mirror flips stored target Y before Rz, not the normalized target Y.
    assert [r[3] for r in e1.primitive.world_transform[:3]] == pytest.approx(
        [122 if mirror else 78, 211, 333]
    )
    assert e2.primitive.world_transform[0][3] - e1.primitive.world_transform[0][3] == 50
    assert _determinant(first.world_transform) == pytest.approx(1)
    assert _determinant(first.orientation_world_transform) == pytest.approx(
        -1 if mirror else 1
    )
    assert signed_volume(_mesh(e1.primitive, 24)) == pytest.approx(490)
    assert (
        json.loads(json.dumps(a.to_dict()))["references"][0]["target_sha256"]
        == a.documents[1].source_sha256
    )


def test_double_reflection_preserves_native_signed_height_and_source_bytes(tmp_path):
    write(tmp_path, "child.icd", solid(mirrored=True))
    a = assemble(tmp_path, host(mirror=True))
    p = a.occurrences[1].entities[0].primitive
    assert _determinant(p.world_transform) == pytest.approx(1)
    assert p.height == 7 and p.mirror_convention == "signed_height"
    assert signed_volume(_mesh(p, 24)) == pytest.approx(490)


def test_missing_grandchild_retained_twice_and_update_reloads_transitive_file(tmp_path):
    child = write(tmp_path, "child.icd", host(names=("grand",)))
    a = assemble(tmp_path)
    assert [r.status for r in a.references].count("missing") == 2
    assert len(a.occurrences) == 5 and len(a.documents) == 2
    grand = write(tmp_path, "grand.icd", solid())
    b = assemble(tmp_path)
    assert b.status == "complete" and len(b.documents) == 3
    grand.write_bytes(solid(position=(7, 11, 19)))
    c = assemble(tmp_path)
    assert b.assembly_sha256 != c.assembly_sha256
    assert b.documents[1].source_sha256 == c.documents[1].source_sha256
    assert b.documents[2].source_sha256 != c.documents[2].source_sha256
    assert (
        b.occurrences[2].entities[0].primitive != c.occurrences[2].entities[0].primitive
    )
    child.unlink()
    d = assemble(tmp_path)
    assert len(d.occurrences) == 3 and [r.status for r in d.references] == [
        "missing",
        "missing",
    ]
    assert len(d.documents) == 1


def test_case_and_windows_relative_separators_without_basename_fallback(tmp_path):
    write(tmp_path, "Dir/CHILD.ICD", solid())
    a = assemble(tmp_path, host(names=(r"dir\child",)))
    assert a.status == "complete" and a.references[0].resolved_path.name == "CHILD.ICD"
    assert assemble(tmp_path, host(names=("child",))).references[0].status == "missing"


@pytest.mark.parametrize(
    "name",
    [
        r"C:\Dir\child.icd",
        r"\\server\child",
        "../child",
        "https://a/child",
        "child.txt",
    ],
)
def test_root_search_does_not_follow_absolute_parent_or_network_names(tmp_path, name):
    assert assemble(tmp_path, host(names=(name,))).references[0].status == "unsupported"


def test_ambiguity_is_path_based_even_for_identical_payloads(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    write(a, "child.icd", solid())
    write(b, "child.icd", solid())
    r = icadkit.read_assembly(host(), search_roots=[a, b]).references[0]
    assert (
        r.status == "ambiguous" and len(r.candidates) == 2 and r.target_sha256 is None
    )
    assert icadkit.read_assembly(host(), search_roots=[a, a]).status == "complete"


def test_case_collision_is_ambiguous_including_exact_match(tmp_path):
    first = write(tmp_path, "child.icd", solid())
    second = tmp_path / "CHILD.ICD"
    if second.exists():
        pytest.skip("case insensitive filesystem")
    second.write_bytes(first.read_bytes())
    assert assemble(tmp_path).references[0].status == "ambiguous"


def test_symlink_escape_and_alias_cycle(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = write(tmp_path, "outside.icd", solid())
    try:
        (root / "child.icd").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert assemble(root).references[0].diagnostics[0].code == "references.outside_root"
    (root / "child.icd").unlink()
    hostpath = write(root, "model.icd", host(names=("child",)))
    (root / "child.icd").symlink_to(hostpath)
    r = icadkit.read_assembly(hostpath, search_roots=[root]).references[0]
    assert r.status == "cycle" and r.target_document_id is None


def test_callback_context_absolute_paths_and_no_name_based_identity(tmp_path):
    p = write(tmp_path, "special.icd", solid())
    requests = []

    def resolver(request):
        requests.append(request)
        return p

    a = icadkit.read_assembly(host(names=("arbitrary",)), resolver=resolver)
    assert a.status == "complete" and requests[0].reference_name == "arbitrary"
    assert requests[0].source_path is None
    assert (
        icadkit.read_assembly(host(), resolver=lambda _: Path("child.icd"))
        .references[0]
        .status
        == "unsupported"
    )
    assert (
        icadkit.read_assembly(host(), resolver=lambda _: None).references[0].status
        == "missing"
    )


@pytest.mark.parametrize(
    "limit,value,reason",
    [
        ("max_documents", 1, "references.document_limit"),
        ("max_total_bytes", 1, "references.byte_limit"),
        ("max_depth", 1, "references.depth_limit"),
        ("max_search_entries", 1, "references.search_limit"),
    ],
)
def test_bounded_reference_failures_remain_diagnostic(tmp_path, limit, value, reason):
    source = host(names=("child",))
    write(tmp_path, "child.icd", host(names=("grand",)))
    write(tmp_path, "grand.icd", solid())
    write(tmp_path, "unrelated.txt", b"x")
    if limit == "max_total_bytes":
        value = len(source) + 1
    a = assemble(tmp_path, source, limits=icadkit.AssemblyLimits(**{limit: value}))
    assert a.status == "partial" and any(
        r.status == "limit" and r.diagnostics[0].code == reason for r in a.references
    )


@pytest.mark.parametrize("limit,value", [("max_occurrences", 2), ("max_entities", 1)])
def test_aggregate_allocation_limits_raise_before_partial_publication(
    tmp_path, limit, value
):
    write(tmp_path, "child.icd", solid())
    with pytest.raises(icadkit.LimitExceededError, match=limit):
        assemble(tmp_path, limits=icadkit.AssemblyLimits(**{limit: value}))


def test_corrupt_and_unqualified_targets_are_not_empty_models(tmp_path):
    p = write(tmp_path, "child.icd", b"MOD0bad")
    assert assemble(tmp_path).references[0].status == "invalid"
    p.write_bytes(view_parts([part(ROOT, root=True, profile="v7l2")], profile="v7l2"))
    assert assemble(tmp_path).references[0].status == "unsupported"
    with pytest.raises(icadkit.UnsupportedFormatError, match="references.profile"):
        icadkit.read_assembly(p, search_roots=[tmp_path])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires a FIFO")
def test_nonregular_callback_target_does_not_block(tmp_path):
    p = tmp_path / "fifo"
    os.mkfifo(p)
    r = icadkit.read_assembly(host(), resolver=lambda _: p).references[0]
    assert r.status == "unsupported" and r.diagnostics[0].code == "references.file_type"


def test_viewer_keeps_instance_ownership_and_source_provenance(tmp_path):
    write(tmp_path, "child.icd", solid())
    a = assemble(tmp_path, host(mirror=True))
    v = write_assembly_viewer(a, tmp_path / "viewer")
    scene = json.loads((v.directory / "scene.json").read_text())
    assert v.rendered_entities == 2 and len(scene["meshes"]) == 2
    assert scene["assembly"]["status"] == "complete"
    for row in scene["parts"][1:]:
        entity = row["entities"][0]
        assert (
            entity["owner_id"] == row["part_id"]
            and entity["source_owner_id"] != row["part_id"]
        )
        assert entity["source_document_id"] == row["definition_document_id"]
        assert row["assembly_reference"]["status"] == "resolved"
        mesh = scene["meshes"][entity["entity_id"]]
        assert signed_volume(mesh) > 0
    with pytest.raises(icadkit.LimitExceededError):
        write_assembly_viewer(
            a, tmp_path / "limited", limits=ViewerLimits(max_triangles=23)
        )
    assert not (tmp_path / "limited").exists()
    write(tmp_path, "child.icd", solid(position=(900, 900, 900)))
    v2 = write_assembly_viewer(a, tmp_path / "snapshot")
    assert v.scene_sha256 == v2.scene_sha256  # Viewer uses the resolved snapshot.


@pytest.mark.parametrize("value", [True, 0, -1, math.inf])
def test_assembly_limit_validation(value):
    with pytest.raises((ValueError, TypeError)):
        icadkit.AssemblyLimits(max_depth=value)


def test_assembly_cli_and_viewer_opt_in(tmp_path, capsys):
    from icadkit.cli import main

    path = write(tmp_path, "model.icd", host())
    args = ["assembly", str(path), "--reference-root", str(tmp_path), "--json"]
    assert main(args) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "partial"
    assert [r["status"] for r in result["references"]] == ["missing", "missing"]
    write(tmp_path, "child.icd", solid())
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    for enabled in (False, True):
        dest = tmp_path / ("resolved" if enabled else "single")
        cli = ["view", str(path), "--write-only", "--json", "--output", str(dest)]
        if enabled:
            cli += ["--reference-root", str(tmp_path)]
        assert main(cli) == 0
        response = json.loads(capsys.readouterr().out)
        assert response["rendered_entities"] == (2 if enabled else 0)
        assert ("assembly_status" in response) == enabled
    assert (
        main(
            [
                "assembly",
                str(path),
                "--reference-root",
                str(tmp_path / "absent"),
                "--json",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["error"]["category"] == "io"


def test_csg_source_result_can_be_instanced_without_replaying_mirrored_history(
    tmp_path,
):
    from test_csg import document, runtime

    runtime()
    source = document()
    write(
        tmp_path,
        "child.icd",
        source.source_bytes(icadkit.ByteRange(0, source.file_size)),
    )
    assembly = assemble(tmp_path, host(mirror=True))
    result = write_assembly_viewer(assembly, tmp_path / "csg", csg=True)
    scene = json.loads((result.directory / "scene.json").read_text())
    assert result.evaluated_csg_bodies == result.rendered_entities == 2
    bodies = [e["csg"] for p in scene["parts"] for e in p["entities"] if e.get("csg")]
    assert all(b["volume_mm3"] == pytest.approx(500) for b in bodies)
    assert bodies[0]["centroid_mm"] == pytest.approx((105, 202.5, 305))
    assert bodies[1]["centroid_mm"] == pytest.approx((155, 202.5, 305))
    assert all(signed_volume(mesh) > 0 for mesh in scene["meshes"].values())


def test_unsupported_target_retains_hash_of_bytes_actually_read(tmp_path):
    import hashlib

    data = b"MOD0bad"
    path = write(tmp_path, "child.icd", data)
    a = assemble(tmp_path)
    assert a.bytes_read == len(host()) + len(data) and a.document_attempts == 2
    for r in a.references:
        assert r.status == "invalid" and r.resolved_path == path
        assert r.target_sha256 == hashlib.sha256(data).hexdigest()
        assert r.target_document_id is None


def test_invalid_search_name_is_diagnostic(tmp_path):
    assert assemble(tmp_path, host(names=(".",))).references[0].status == "unsupported"


def test_ancestor_cycle_does_not_reject_shared_sibling_definitions(tmp_path):
    path = write(tmp_path, "model.icd", host())
    write(tmp_path, "child.icd", host(names=("model",)))
    a = icadkit.read_assembly(path, search_roots=[tmp_path])
    assert [r.status for r in a.references] == [
        "resolved",
        "cycle",
        "resolved",
        "cycle",
    ]
    assert len(a.documents) == 2 and len(a.occurrences) == 5
