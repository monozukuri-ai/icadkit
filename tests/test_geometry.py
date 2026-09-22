import gc
import hashlib
import json
import math
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest
from geometry_fixtures import catalog_bytes, header, wire_payload

import icadkit as ic


@pytest.fixture
def geometry_doc(document_factory, resource_factory):
    def make(payload=None, **kwargs):
        entity, _ = resource_factory(
            wire_payload() if payload is None else payload, **kwargs
        )
        return ic.read(document_factory([entity]))

    return make


def test_wire_provenance_and_independent_measurements(geometry_doc):
    doc = geometry_doc()
    r = doc.resources[0]
    g = doc.read_geometry(r.resource_id).require_complete()
    assert g.status.raw_geometry == g.status.brep == g.status.topology == "complete"
    assert g.status.container == "partial" and g.status.model == "not_checked"
    assert g.schema.profile_id == "onshape-sch30000-r3"
    assert g.schema.kind == "builtin" and g.schema.coverage == "verified_subset"
    assert g.source.payload_sha256 == hashlib.sha256(wire_payload()).hexdigest()
    assert g.raw.to_bytes() == doc.extract_bytes(r.resource_id)
    assert g.raw.node_count == 11
    assert g.raw.terminator_range.end == len(wire_payload())
    assert dict(g.brep.counts) == dict(
        bodies=1,
        regions=1,
        shells=1,
        faces=0,
        loops=0,
        half_edges=2,
        edges=1,
        vertices=2,
        points=2,
        curves=1,
        surfaces=0,
    )
    assert g.brep.vertex_bounds == ((2.0, -1.0, 3.0), (5.0, 3.0, 3.0))
    points = [p.attributes["position"] for p in g.brep.entities("points")]
    assert math.dist(*points) == 5
    curve = g.brep.entities("curves")[0]
    assert curve.attributes["direction"] == (0.6, 0.8, 0.0)
    assert curve.attributes["point"] == points[0]
    assert g.brep.volume is None
    for name in g.brep.counts:
        for e in g.brep.entities(name):
            assert (
                e.source.decoded_range == g.raw.node(e.source.node_index).decoded_range
            )
    with pytest.raises(TypeError):
        curve.attributes["direction"] = (0, 0, 0)
    with pytest.raises(FrozenInstanceError):
        g.raw.node_count = 0


@pytest.mark.parametrize("version", [0, 4])
def test_icad_v34_without_catalog(geometry_doc, version):
    payload = wire_payload(embedded=True)
    doc = geometry_doc(payload, version=version, count=0 if version == 0 else 3)
    g = doc.read_geometry(doc.resources[0].resource_id).require_complete()
    assert g.schema.kind == "builtin"
    assert g.schema.profile_id == "icad-sch34101-13006-r1"
    assert g.schema.profile_revision == 1
    assert g.schema.profile_sha256 == (
        "a516a515d3d0c0866a001cf148e7e2e066e9741912c26d45c6ba4fc232179d6e"
    )
    assert g.raw.schema_key == "SCH_3401212_34101_13006"
    assert g.raw.to_bytes() == payload
    assert g.raw.node_count == 11 and g.brep.counts["edges"] == 1
    assert g.brep.topology_valid
    assert g.brep.vertex_bounds == ((2.0, -1.0, 3.0), (5.0, 3.0, 3.0))
    assert (
        math.dist(*(p.attributes["position"] for p in g.brep.entities("points"))) == 5
    )
    assert g.status.container == "partial" and g.status.model == "not_checked"


def test_icad_v34_rejects_nearby_key_and_unknown_base(geometry_doc):
    payload = wire_payload(embedded=True)
    nearby = payload.replace(b"SCH_3401212_34101_13006", b"SCH_3401213_34101_13006")
    unknown = bytearray(payload)
    offset = len(header(b"SCH_3401212_34101_13006"))
    unknown[offset : offset + 2] = b"\0\x6e"  # Unreviewed base type 110.
    for data, code in (
        (nearby, "schema.missing_base_schema"),
        (bytes(unknown), "schema.unknown_base_type"),
    ):
        doc = geometry_doc(data)
        g = doc.read_geometry(doc.resources[0].resource_id)
        assert g.raw is None and g.brep is None
        assert g.diagnostics[0].code == code


def test_icad_v34_cli_uses_builtin(geometry_doc, tmp_path):
    doc = geometry_doc(wire_payload(embedded=True))
    source = tmp_path / "v34.icd"
    source.write_bytes(doc.source_bytes(ic.ByteRange(0, doc.file_size)))
    result = subprocess.run(
        [
            "icadkit",
            "check",
            str(source),
            "--target",
            "geometry",
            "--resource",
            doc.resources[0].resource_id,
            "--json",
        ],
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)
    assert row["schema"]["profile_id"] == "icad-sch34101-13006-r1"
    assert row["status"]["brep"] == "complete"
    assert row["status"]["model"] == "not_checked"


def test_raw_pages_lifetime_and_threads(geometry_doc):
    doc = geometry_doc()
    g = doc.read_geometry(doc.resources[0].resource_id)
    raw, brep = g.raw, g.brep
    del doc, g
    gc.collect()
    assert raw.nodes(10, 100) == (raw.node(11),)
    assert raw.nodes(100, 1) == () and raw.nodes(0, 0) == ()
    assert raw.fields(11)[7].value_count == 1
    assert raw.field_values(11, 7) == ((2.0, -1.0, 3.0),)
    assert raw.field_values(6, 2) == (None,)
    assert raw.user_fields(1) == ()
    with ThreadPoolExecutor(4) as pool:
        assert all(
            pool.map(lambda _: raw.field_values(11, 8) == ((0.6, 0.8, 0.0),), range(50))
        )
    assert (
        brep.entities("edges")[0].attributes["curve"] == brep.entities("curves")[0].id
    )
    for call in (
        lambda: raw.nodes(count=1001),
        lambda: brep.entities("points", -1),
        lambda: raw.field_values(1, -1),
        lambda: brep.entities("bad"),
    ):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(TypeError):
        raw.nodes(True)
    with pytest.raises(KeyError):
        raw.node(999)
    with pytest.raises(KeyError):
        raw.field_values(1, 999)


@pytest.mark.parametrize("version", [0, 2, 3, 4, 5, 6])
def test_geometry_encodings(geometry_doc, version):
    doc = geometry_doc(version=version, count=0 if version == 0 else 3)
    g = doc.read_geometry(doc.resources[0].resource_id).require_complete()
    assert g.source.encoding == ("raw" if version < 4 else "zlib")


def test_bad_brep_preserves_raw_and_decoded_location(geometry_doc):
    doc = geometry_doc(wire_payload(bad_reference=True))
    g = doc.read_geometry(doc.resources[0].resource_id)
    assert g.status.raw_geometry == "complete"
    assert g.status.brep == "invalid" and g.raw is not None and g.brep is None
    d = g.diagnostics[0]
    assert d.byte_offset is None and d.decoded_offset is not None
    assert d.container_range == g.source.container_range
    assert d.node_index is not None and d.backend_code == d.code
    g.require_complete("raw_geometry")
    with pytest.raises(ic.IncompleteGeometryError):
        g.require_complete()


def test_unknown_schema_no_environment_fallback(geometry_doc, monkeypatch, tmp_path):
    monkeypatch.setenv("ICAD_SCHEMA_DIR", str(tmp_path))
    doc = geometry_doc(header(b"SCH_9999999_99999") + b"\0\1\0\1")
    g = doc.read_geometry(doc.resources[0].resource_id)
    assert g.status.raw_geometry == "unsupported" and g.raw is None
    assert g.diagnostics[0].code == "schema.missing_base_schema"
    assert g.schema.kind == "unavailable"


def test_explicit_catalog_snapshot_and_mismatch(geometry_doc, tmp_path):
    content = catalog_bytes()
    path = tmp_path / "SCH_30000.SCH_TXT"
    path.write_bytes(content)
    catalog = ic.SchemaCatalog.from_file(
        path,
        expected_id="30000",
        expected_sha256=hashlib.sha256(content).hexdigest().upper(),
    )
    path.unlink()
    assert catalog.schema_id == "30000" and catalog.definition_count == 1
    doc = geometry_doc(header() + b"\0\1\0\1")
    g = doc.read_geometry(doc.resources[0].resource_id, schema=catalog)
    assert g.raw.node_count == 0 and g.status.raw_geometry == "complete"
    assert g.status.brep == "unsupported"
    assert g.schema.catalog_sha256 == catalog.sha256
    wrong = ic.SchemaCatalog.from_bytes(catalog_bytes("12345"), expected_id="12345")
    g = doc.read_geometry(doc.resources[0].resource_id, schema=wrong)
    assert g.raw is None and g.diagnostics[0].code == "schema.catalog_mismatch"
    assert doc.read_geometry(doc.resources[0].resource_id).schema.kind == "builtin"
    with pytest.raises(ic.InvalidFormatError):
        ic.SchemaCatalog.from_bytes(content, expected_id="12345")
    with pytest.raises(ic.InvalidFormatError):
        ic.SchemaCatalog.from_bytes(
            content, expected_id="30000", expected_sha256="0" * 64
        )
    with pytest.raises(ic.LimitExceededError):
        ic.SchemaCatalog.from_bytes(
            content, expected_id="30000", limits=ic.CatalogLimits(max_file_bytes=5)
        )
    with pytest.raises(ValueError):
        ic.SchemaCatalog.from_bytes(content, expected_id="٣٠٠٠٠")


@pytest.mark.parametrize(
    "policy",
    [
        ic.GeometryLimits(max_payload_bytes=10),
        ic.GeometryLimits(max_nodes=1),
        ic.GeometryLimits(max_fields_per_type=1),
        ic.GeometryLimits(max_schema_types=1),
        ic.GeometryLimits(max_string_bytes=3),
    ],
)
def test_limits_are_explicit_errors(geometry_doc, policy):
    doc = geometry_doc()
    with pytest.raises(ic.LimitExceededError) as e:
        doc.read_geometry(doc.resources[0].resource_id, limits=policy)
    assert isinstance(e.value.diagnostic, ic.GeometryDiagnostic)
    assert e.value.diagnostic.resource_id == doc.resources[0].resource_id
    assert doc.read_geometry(doc.resources[0].resource_id).status.brep == "complete"


def test_malformed_nodes_and_local_extraction_failure(
    geometry_doc, document_factory, resource_factory
):
    doc = geometry_doc(header() + b"\0\14\0\2\0\1\0\1")
    g = doc.read_geometry(doc.resources[0].resource_id)
    assert g.status.extraction == "complete" and g.status.raw_geometry == "invalid"
    bad, _ = resource_factory(wire_payload(), encoded=b"broken zlib")
    good, _ = resource_factory(wire_payload())
    doc = ic.read(document_factory([bad, good]))
    g = doc.read_geometry(doc.resources[0].resource_id)
    assert g.status.extraction == "invalid" and g.source is None
    assert g.diagnostics[0].decoded_offset is None
    assert g.diagnostics[0].byte_offset is not None
    doc.read_geometry(doc.resources[1].resource_id).require_complete()


def test_check_cli(geometry_doc, tmp_path):
    doc = geometry_doc()
    path = tmp_path / "wire.icd"
    path.write_bytes(doc.source_bytes(ic.ByteRange(0, doc.file_size)))

    def run(*args):
        p = subprocess.run(
            ["icadkit", "check", str(path), "--json", *args],
            capture_output=True,
            encoding="utf-8",
        )
        return p.returncode, json.loads(p.stdout)

    code, out = run("--target", "geometry", "--resource", doc.resources[0].resource_id)
    assert (
        code == 0
        and out["node_count"] == 11
        and out["status"]["model"] == "not_checked"
    )
    assert run("--target", "model")[0] == 3
    assert run("--target", "container")[0] == 3
    assert (
        run(
            "--target",
            "geometry",
            "--resource",
            doc.resources[0].resource_id,
            "--max-nodes",
            "1",
        )[0]
        == 4
    )
    assert run("--target", "geometry", "--resource", "missing")[0] == 1
    p = subprocess.run(
        ["icadkit", "check", str(path), "--target", "geometry"], capture_output=True
    )
    assert p.returncode == 2


def test_variable_array_pages_and_limit(geometry_doc):
    import struct

    # Authored INT_ARRAY, with 1200 transmitted values and an explicit null.
    values = list(range(1200))
    values[8] = -32764
    payload = (
        header()
        + struct.pack(">HiH", 82, len(values), 2)
        + struct.pack(">1200i", *values)
        + b"\0\1\0\1"
    )
    doc = geometry_doc(payload)
    rid = doc.resources[0].resource_id
    result = doc.read_geometry(rid).require_complete("raw_geometry")
    assert result.raw.node(1).variable_length == 1200
    assert result.raw.fields(1)[0].value_count == 1200
    page = result.raw.field_values(1, 0, count=1000)
    assert len(page) == 1000 and page[8] is None
    assert result.raw.field_values(1, 0, start=1198) == (1198, 1199)
    with pytest.raises(ic.LimitExceededError) as exc:
        doc.read_geometry(rid, limits=ic.GeometryLimits(max_variable_elements=100))
    assert exc.value.diagnostic.decoded_offset is not None
    assert exc.value.diagnostic.byte_offset is None


def test_concurrent_parses_and_policy_validation(geometry_doc):
    doc = geometry_doc()
    rid = doc.resources[0].resource_id
    with ThreadPoolExecutor(4) as pool:
        results = tuple(pool.map(lambda _: doc.read_geometry(rid), range(16)))
    assert all(r.brep.complete for r in results)
    assert len({r.source.payload_sha256 for r in results}) == 1
    assert doc.status.raw_geometry == "not_checked"
    for limits in (ic.GeometryLimits, ic.CatalogLimits):
        for name in limits.__dataclass_fields__:
            with pytest.raises(TypeError):
                limits(**{name: True})
            with pytest.raises(ValueError):
                limits(**{name: 0})
            with pytest.raises(ValueError):
                limits(**{name: 1 << 63})
    with pytest.raises(TypeError):
        doc.read_geometry(rid, schema="auto")
    with pytest.raises(TypeError):
        doc.read_geometry(rid, limits=ic.ReadLimits())
    with pytest.raises(ValueError):
        results[0].require_complete("model")
