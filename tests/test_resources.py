import gc
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace

import pytest

import icadkit as kit


@pytest.mark.parametrize("order", ["little", "big"])
@pytest.mark.parametrize("version", [2, 3, 4, 5, 6])
@pytest.mark.parametrize("count", [0, 1, 2, 9])
def test_structural_ranges_and_provenance(
    order, version, count, resource_factory, document_factory
):
    entity, payload = resource_factory(version=version, count=count, order=order)
    data = document_factory([entity], order=order)
    doc = kit.read(data)
    assert doc.resource_index_status == "complete" and not doc.diagnostics
    assert len(doc.resources) == 1
    resource = doc.resources[0]
    assert resource.owner_range == kit.ByteRange(572, 572 + len(entity))
    assert resource.owner_type == (0x87 if version == 6 else 0x86)
    assert resource.layout_version == version
    assert resource.source_id == 42
    extracted = doc.extract(resource.resource_id)
    assert extracted.payload == payload == doc.extract_bytes(resource.resource_id)
    assert extracted.source.source_sha256 == hashlib.sha256(data).hexdigest()
    assert extracted.source.payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert extracted.source.decoded_range == kit.ByteRange(0, len(payload))
    assert extracted.source.container_range.start == resource.storage_range.start
    assert 0 <= resource.storage_range.end - extracted.source.container_range.end <= 7
    assert doc.status.extraction == "not_checked"
    assert extracted.status.extraction == "complete"
    assert extracted.status.container == "partial"
    assert extracted.status.raw_geometry == extracted.status.model == "not_checked"
    assert doc.source_bytes(resource.owner_range) == entity
    assert doc.source_bytes(doc.unparsed_ranges[-1].byte_range) == b"unparsed tail"


def test_snapshot_duplicates_and_concurrent_lifetime(
    tmp_path, document_factory, resource_factory
):
    entity, payload = resource_factory()
    data = document_factory([entity, entity])
    path = tmp_path / "図面.icd"
    path.write_bytes(data)
    doc = kit.read(path)
    path.unlink()
    assert doc.source_sha256 == kit.read(data).source_sha256
    first, second = doc.resources
    assert first.resource_id != second.resource_id
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(doc.extract, [first.resource_id, second.resource_id] * 8)
        )
    assert len({r.source.payload_sha256 for r in results}) == 1
    assert len({r.source.container_range for r in results}) == 2
    with pytest.raises(FrozenInstanceError):
        doc.source_sha256 = "changed"
    del doc, data
    gc.collect()
    assert all(r.payload == payload for r in results)


def test_signatures_in_opaque_ranges_are_not_resources(
    document_factory, resource_factory
):
    entity, payload = resource_factory()
    data = document_factory(
        with_usr=False,
        tail=entity + payload,
        view_payload=(payload + entity).ljust(512, b"\0"),
    )
    doc = kit.read(data)
    assert not doc.resources and doc.resource_index_status == "complete"
    assert doc.status.model == "not_checked" and doc.status.container == "partial"


def test_unknown_layout_does_not_scan_or_discard_other_owners(
    document_factory, resource_factory
):
    entity, _ = resource_factory()
    unknown = bytearray(entity)
    unknown[1] = 0x7F
    doc = kit.read(document_factory([entity, unknown, entity]))
    assert doc.resource_index_status == "partial" and len(doc.resources) == 2
    assert doc.diagnostics[0].code == "resource.unsupported_layout"
    assert doc.extract_bytes(doc.resources[1].resource_id)
    unknown[0] = 0xFE
    doc = kit.read(document_factory([entity, unknown, entity]))
    assert len(doc.resources) == 1 and doc.resource_index_status == "partial"
    assert doc.diagnostics[0].code == "entity.unsupported_layout"
    assert doc.source_bytes(doc.unparsed_ranges[-2].byte_range).startswith(unknown)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("checksum", "compression.invalid"),
        ("truncated", "compression.truncated"),
        ("oversize", "resource.decoded_size_mismatch"),
        ("undersize", "resource.decoded_size_mismatch"),
        ("trailing", "resource.invalid_padding"),
        ("concatenated", "resource.invalid_padding"),
        ("envelope", "resource.invalid_envelope"),
    ],
)
def test_lazy_local_compression_failures(
    mutation, code, document_factory, resource_factory
):
    import zlib

    good, payload = resource_factory()
    encoded = zlib.compress(payload)
    kwargs = {}
    if mutation == "checksum":
        encoded = encoded[:-1] + bytes([encoded[-1] ^ 1])
    elif mutation == "truncated":
        # Empty storage containing only alignment zeroes has no stream EOF.
        encoded = b"\x78\x9c\x00\x20\x00\xdf\xff"
    elif mutation == "oversize":
        kwargs["declared"] = len(payload) - 1
    elif mutation == "undersize":
        kwargs["declared"] = len(payload) + 1
    elif mutation == "trailing":
        encoded += b"not padding"
    elif mutation == "concatenated":
        encoded += zlib.compress(payload)
    elif mutation == "envelope":
        encoded = zlib.compress(payload[:-4] + b"xxxx")
    bad, _ = resource_factory(encoded=encoded, **kwargs)
    doc = kit.read(document_factory([bad, good]))
    assert len(doc.resources) == 2 and doc.resource_index_status == "complete"
    with pytest.raises(kit.InvalidFormatError) as caught:
        doc.extract(doc.resources[0].resource_id)
    assert caught.value.diagnostic.code == code
    assert caught.value.diagnostic.byte_offset == doc.resources[0].storage_range.start
    assert doc.extract_bytes(doc.resources[1].resource_id) == payload


@pytest.mark.parametrize("version", [2, 6])
def test_resource_limits_and_bombs(version, resource_factory, document_factory):
    payload = b"PS\0\0" + b"a" * 100_000 + b"\0\x01\0\x01"
    entity, _ = resource_factory(payload, version=version)
    doc = kit.read(
        document_factory([entity]), limits=kit.ReadLimits(max_resource_bytes=1024)
    )
    with pytest.raises(kit.LimitExceededError, match="limit.resource_bytes"):
        doc.extract_bytes(doc.resources[0].resource_id)
    # False small declarations cannot smuggle a large decoded allocation.
    entity, _ = resource_factory(payload, version=6, declared=200)
    doc = kit.read(
        document_factory([entity]), limits=kit.ReadLimits(max_resource_bytes=1024)
    )
    with pytest.raises(kit.LimitExceededError):
        doc.extract_bytes(doc.resources[0].resource_id)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("max_file_bytes", 8, "limit.file_bytes"),
        ("max_record_bytes", 8, "limit.record_bytes"),
        ("max_records", 5, "limit.records"),
        ("max_resources", 1, "limit.resources"),
    ],
)
def test_input_index_limits(field, value, code, resource_factory, document_factory):
    entity, _ = resource_factory()
    data = document_factory([entity, entity])
    with pytest.raises(kit.LimitExceededError, match=code):
        kit.read(data, limits=replace(kit.ReadLimits(), **{field: value}))


@pytest.mark.parametrize(
    "location,value,code",
    [
        (276, 496, "directory.overlap_or_missing"),
        (276, 516, "directory.noncontiguous"),
        (288, 0xFFFF_FFFF, "record.out_of_bounds"),
        (500, 5, "directory.length_mismatch"),
    ],
)
def test_directory_boundaries(
    location, value, code, document_factory, resource_factory
):
    entity, _ = resource_factory()
    data = bytearray(document_factory([entity]))
    data[location : location + 4] = value.to_bytes(4, "little")
    with pytest.raises(kit.IcadError, match=code):
        kit.read(bytes(data))


@pytest.mark.parametrize(
    "offset,value,code",
    [
        (4, 0, "entity.invalid_length"),
        (4, 0xFFFF_FFF8, "entity.invalid_length"),
        (24, 0xFFFF_FFFF, "resource.table_out_of_bounds"),
        (16, 0, "resource.invalid_length"),
    ],
)
def test_bad_entity_ranges_preserve_partial_index(
    offset, value, code, document_factory, resource_factory
):
    entity, _ = resource_factory()
    bad = bytearray(entity)
    bad[offset : offset + 4] = value.to_bytes(4, "little")
    doc = kit.read(document_factory([entity, bad]))
    assert len(doc.resources) == 1
    assert doc.resource_index_status == "partial" and doc.diagnostics[0].code == code


def test_truncation_unknown_prefix_and_end(document_factory, resource_factory):
    entity, _ = resource_factory()
    data = document_factory([entity], tail=b"")
    for end in range(len(data)):
        with pytest.raises(kit.IcadError):
            kit.read(data[:end])
    damaged = bytearray(data)
    damaged[532] ^= 1
    doc = kit.read(bytes(damaged))
    assert not doc.resources and doc.diagnostics[0].code == "usr.unsupported_layout"
    doc = kit.read(document_factory([entity], end_entity=False))
    assert doc.diagnostics[0].code == "usr.missing_end"
    assert doc.extract_bytes(doc.resources[0].resource_id)


def test_raw_payload_end_is_declared_and_padding_checked(
    resource_factory, document_factory
):
    entity, _ = resource_factory(version=2, declared=0xFFFF_FFFF)
    doc = kit.read(document_factory([entity]))
    with pytest.raises(kit.LimitExceededError):
        doc.extract_bytes(doc.resources[0].resource_id)
    entity, payload = resource_factory(
        version=2, encoded=b"PS\0\0xxxxx\0\x01\0\x01" + b"bad", declared=13
    )
    doc = kit.read(document_factory([entity]))
    with pytest.raises(kit.InvalidFormatError, match="invalid_padding"):
        doc.extract_bytes(doc.resources[0].resource_id)


def test_opaque_usr_size_word_and_fixed_size_instance(
    document_factory, resource_factory
):
    entity, _ = resource_factory()
    instance = bytearray(96)
    instance[:4] = b"\x88\x06\x60\0"
    instance[4:8] = b"\xff" * 4  # ID, deliberately not an entity length.
    data = bytearray(document_factory([entity, instance]))
    data[516:520] = b"\xff" * 4  # Old USR +4 need not equal indexed physical size.
    doc = kit.read(bytes(data))
    assert len(doc.resources) == 1 and doc.resource_index_status == "complete"


def test_api_input_contract_and_missing_resource(document_factory, tmp_path):
    doc = kit.read(document_factory())
    with pytest.raises(kit.InvalidFormatError, match="resource.not_found"):
        doc.extract("usr:made-up")
    with pytest.raises(kit.InvalidFormatError, match="source.invalid_range"):
        doc.source_bytes(kit.ByteRange(3, 1))
    with pytest.raises(kit.InvalidFormatError, match="source.invalid_range"):
        doc.source_bytes(kit.ByteRange(0, doc.file_size + 1))
    with pytest.raises(FileNotFoundError) as exc:
        kit.read(tmp_path / "missing.icd")
    assert exc.value.filename == str(tmp_path / "missing.icd")
    with pytest.raises(OSError):
        kit.read(tmp_path)
    for source in [bytearray(b"MOD0"), memoryview(b"MOD0"), 42]:
        with pytest.raises(TypeError):
            kit.read(source)
    with pytest.raises(TypeError):
        kit.read(b"", limits=kit.InspectionLimits())
    with pytest.raises(TypeError):
        kit.ReadLimits(max_resources=True)
    with pytest.raises(ValueError):
        kit.ReadLimits(max_resource_bytes=0)


@pytest.mark.parametrize("order", ["little", "big"])
def test_legacy_type_85(order, resource_factory, document_factory):
    entity, payload = resource_factory(version=0, count=0, order=order)
    doc = kit.read(document_factory([entity], order=order))
    assert doc.resource_index_status == "complete"
    assert doc.resources[0].owner_type == 0x85
    assert doc.extract_bytes(doc.resources[0].resource_id) == payload
    entity, _ = resource_factory(version=0, count=1, order=order)
    doc = kit.read(document_factory([entity], order=order))
    assert doc.resource_index_status == "partial" and not doc.resources


def test_arbitrary_ps_signature_is_not_a_valid_xb_header(
    resource_factory, document_factory
):
    entity, _ = resource_factory(b"PS\0\0" + b"invalid" * 5 + b"\0\x01\0\x01")
    doc = kit.read(document_factory([entity]))
    with pytest.raises(kit.InvalidFormatError, match="resource.invalid_header"):
        doc.extract(doc.resources[0].resource_id)


def test_missing_end_is_invalid_even_after_an_unsupported_owner(
    resource_factory, document_factory
):
    entity, _ = resource_factory()
    unknown = bytearray(entity)
    unknown[1] = 99
    doc = kit.read(document_factory([unknown], end_entity=False))
    assert [(d.category, d.code) for d in doc.diagnostics] == [
        ("unsupported", "resource.unsupported_layout"),
        ("invalid", "usr.missing_end"),
    ]
