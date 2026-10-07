"""Byte-exact container serialization on authored framing fixtures."""

import pytest

import icadkit as kit


@pytest.mark.parametrize("order", ["little", "big"])
@pytest.mark.parametrize("with_usr", [True, False])
def test_unmodified_document_serializes_byte_for_byte(
    order, with_usr, document_factory, resource_factory
):
    entity, _ = resource_factory(order=order)
    data = document_factory(
        [entity], order=order, with_usr=with_usr, tail=b"opaque tail\0"
    )
    doc = kit.read(data)
    assert doc.to_bytes() == data
    assert doc.to_bytes() is not doc.to_bytes()  # Fresh bytes per call.


def test_stored_total_is_recomputed_not_copied(document_factory):
    data = document_factory()
    mutated = bytearray(data)
    mutated[236:240] = (7).to_bytes(4, "little")
    doc = kit.read(bytes(mutated))
    out = doc.to_bytes()
    assert out == data and out != bytes(mutated)


def test_inspect_only_fixture_is_not_a_container(icd_factory):
    with pytest.raises(kit.IcadError):
        kit.read(icd_factory())


@pytest.mark.parametrize("order", ["little", "big"])
def test_container_model_round_trips_and_edits(
    order, document_factory, resource_factory
):
    entity, _ = resource_factory(order=order)
    data = document_factory([entity], order=order, tail=b"tail")
    doc = kit.read(data)
    container = doc.container()
    assert container.byte_order == order and not container.diagnostics
    assert [r.tag for r in container.records] == ["RES", "V/W", "USR"]
    assert container.view_names == (b"3DGLOBAL",)
    assert container.view_records == (1,)
    assert container.tail == b"tail"
    assert container.to_bytes() == data == doc.to_bytes()
    assert [r.words for r in container.records] == [
        (r.byte_range.end - r.byte_range.start) // 4 for r in doc.records[2:]
    ]
    view = container.records[1]
    edited = container.with_record(1, view.with_body(view.body + b"\x07" * 8))
    out = edited.to_bytes()
    assert len(out) == len(data) + 8 and out.endswith(b"tail")
    again = kit.read(out)
    assert [r.byte_range.length for r in again.records] == [
        r.byte_range.length + (8 if r.tag == "V/W" else 0) for r in doc.records
    ]
    assert again.container().to_bytes() == out


def test_container_model_rejects_inconsistent_parts(document_factory):
    container = kit.read(document_factory()).container()
    view = container.records[1]
    with pytest.raises(kit.InvalidFormatError) as info:
        container.with_record(1, view.with_body(view.body + b"\x01")).to_bytes()
    assert info.value.diagnostic.code == "container.alignment"
    with pytest.raises(ValueError):
        kit.ContainerRecord("XYZ", 0, b"")
    with pytest.raises(ValueError):
        dataclasses_replace = __import__("dataclasses").replace
        dataclasses_replace(container, view_names=(b"short",))
    with pytest.raises(kit.InvalidFormatError) as info:
        dataclasses_replace(container, records=container.records[::-1]).to_bytes()
    assert info.value.diagnostic.code == "container.record_order"
