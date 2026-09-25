import struct
from dataclasses import FrozenInstanceError

import pytest
from parameter_fixtures import (
    condition_row,
    parametric_part,
    preamble,
    table,
    template,
    value_row,
)
from part_fixtures import ROOT, A, B, part, view_parts
from test_native import native

import icadkit


@pytest.mark.parametrize("profile", ["v8l1", "v8l2", "v8l3"])
@pytest.mark.parametrize("revision", [0, 1, 2])
def test_root_parameters_and_physical_provenance(profile, revision):
    doc = icadkit.read(template(profile=profile, revision=revision, split=252))
    parts = doc.read_parts()
    assert parts.status.index == parts.status.hierarchy == "complete"
    assert len(parts.parts) == 1 and parts.parts[0].is_root
    assert parts.to_rows() == []
    assert len(parts.parts[0].entities) == 3
    assert all(e.primitive is None for e in parts.parts[0].entities)
    result = doc.read_parameters()
    assert result.status == "complete"
    (p,) = result.parameters
    assert p.name == "width" and p.value == 19.125 and p.kind == "length"
    assert p.raw_values[0] == 7.5  # prior saved dimension is a different field
    assert p.explain == "説明" and p.comment == "長さ条件"
    assert p.enabled and p.status == "complete"
    assert p.owner_part_id == parts.parts[0].part_id
    assert len(p.byte_ranges) == 2
    assert b"".join(doc.source_bytes(s) for s in p.byte_ranges) == p.raw_bytes
    for t in result.tables:
        assert b"".join(doc.source_bytes(s) for s in t.payload_ranges) == t.raw_payload
    (c,) = p.conditions
    assert b"".join(doc.source_bytes(s) for s in c.byte_ranges) == c.raw_bytes
    with pytest.raises(FrozenInstanceError):
        p.value = 1


@pytest.mark.parametrize("profile", ["v7l7", "v8l3"])
def test_repeated_names_in_separate_owners_are_not_shared(profile):
    records = [part(ROOT, root=True, child=A, profile=profile)]
    for sid, previous, next_id, enabled in [(A, 0, B, True), (B, A, 0, False)]:
        records.extend(
            [
                (part if profile == "v7l7" else parametric_part)(
                    sid,
                    parent=ROOT,
                    previous=previous,
                    next_id=next_id,
                    profile=profile,
                ),
                struct.pack("<I", 0x30010000),
                table(2, [value_row(equation="width*2")]),
                table(1, [condition_row(enabled=enabled)]),
            ]
        )
    doc = icadkit.read(view_parts(records, profile=profile))
    result = doc.read_parameters()
    assert result.status == "complete"
    a, b = result.parameters
    assert a.name == b.name == "width"
    assert a.owner_part_id != b.owner_part_id
    assert a.parameter_id != b.parameter_id
    assert a.enabled is True and b.enabled is False
    assert a.value == b.value == 19.125  # expressions are not evaluated


@pytest.mark.parametrize(
    "offset,value", [(4, 232), (8, 2), (12, 1), (16, 0xC9500088), (160, 0x8100004C)]
)
def test_bad_preamble_never_scans_for_embedded_root(offset, value):
    meta = bytearray(preamble())
    struct.pack_into("<I", meta, offset, value)
    doc = icadkit.read(view_parts([bytes(meta), parametric_part(root=True)]))
    parts = doc.read_parts()
    assert parts.status.index != "complete" and not parts.parts
    assert doc.read_parameters().status != "complete"


def test_preamble_is_not_a_root_and_is_only_allowed_at_view_start():
    for records in ([preamble()], [part(ROOT, root=True), preamble(), part(A)]):
        index = icadkit.read(view_parts(records)).read_parts()
        assert index.status.index != "complete"
    assert icadkit.read(view_parts([preamble()])).read_parts().parts == ()


def test_template_primitive_shaped_operand_is_never_promoted():
    doc = icadkit.read(view_parts([preamble(), parametric_part(root=True), native()]))
    (entity,) = doc.read_parts().parts[0].entities
    assert entity.primitive is None and entity.geometry_status == "unsupported"


@pytest.mark.parametrize("offset,value", [(0, 99), (68, 9)])
def test_unknown_value_type_or_state_preserves_raw_row(offset, value):
    row = bytearray(value_row())
    struct.pack_into("<I", row, offset, value)
    result = icadkit.read(template(values=[bytes(row)])).read_parameters()
    assert result.status == "partial"
    (p,) = result.parameters
    assert p.raw_bytes == bytes(row) and p.diagnostics
    if offset == 0:
        assert p.kind is None and p.value is None and p.raw_type == 99


@pytest.mark.parametrize(
    "defect", ["count", "chunk_length", "chunk_count", "chunk_type", "revision"]
)
def test_bad_table_is_retained_without_guessing_rows(defect):
    raw = bytearray(template())
    doc = icadkit.read(bytes(raw))
    t = next(t for t in doc.read_parameters().tables if t.raw_kind & 255 == 2)
    at = t.byte_range.start
    offset, value = {
        "count": (36, 0xFFFFFFFF),
        "chunk_length": (40, 0xFD000004),
        "chunk_count": (12, 0x4D032081),
        "chunk_type": (44, 0x02000000),
        "revision": (32, 0x00010302),
    }[defect]
    struct.pack_into("<I", raw, at + offset, value)
    result = icadkit.read(bytes(raw)).read_parameters()
    assert result.status == "partial" and not result.parameters
    assert t.byte_range in result.opaque_ranges


@pytest.mark.parametrize(
    "conditions", [[condition_row(1)], [condition_row(), condition_row()]]
)
def test_bad_or_ambiguous_condition_link_cannot_report_complete(conditions):
    result = icadkit.read(template(conditions=conditions)).read_parameters()
    assert result.status in ("invalid", "partial")
    assert result.parameters[0].enabled is None


def test_duplicate_value_tables_are_not_joined_by_row_number():
    doc = icadkit.read(
        view_parts(
            [
                parametric_part(root=True),
                struct.pack("<I", 0x30010000),
                table(1, [condition_row()]),
                table(2, [value_row()], source_id=0x80000077),
                table(2, [value_row()]),
            ]
        )
    )
    result = doc.read_parameters()
    assert result.status == "partial" and result.parameters == ()


def test_duplicate_table_identity_is_invalid():
    doc = icadkit.read(
        view_parts(
            [
                parametric_part(root=True),
                struct.pack("<I", 0x30010000),
                table(1, [condition_row()], source_id=0x80000010),
                table(2, [value_row()], source_id=0x80000010),
            ]
        )
    )
    result = doc.read_parameters()
    assert result.status == "invalid" and not result.parameters
    assert any(d.code == "parameters.duplicate_id" for d in result.diagnostics)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_values_are_invalid_and_raw_is_available(value):
    result = icadkit.read(template(values=[value_row(value=value)])).read_parameters()
    assert result.status == "invalid" and result.parameters[0].value is None


def test_bad_text_duplicate_name_and_number_without_condition():
    row = bytearray(value_row())
    row[4:6] = b"\x81\0"
    bad = icadkit.read(template(values=[bytes(row)])).read_parameters()
    assert bad.status != "complete" and bad.parameters[0].name is None
    dupe = icadkit.read(
        template(
            values=[value_row(), value_row()],
            conditions=[condition_row(), condition_row(1)],
        )
    ).read_parameters()
    assert dupe.status == "invalid"
    number = icadkit.read(
        template(values=[value_row(kind=0)], conditions=[])
    ).read_parameters()
    assert number.status == "complete" and number.parameters[0].comment == ""


def test_limits_are_checked_before_materializing_table_payloads():
    doc = icadkit.read(template(values=[value_row(), value_row(name="second")]))
    for limits in (
        icadkit.ParameterLimits(max_bytes=1),
        icadkit.ParameterLimits(max_parameters=1),
    ):
        with pytest.raises(icadkit.LimitExceededError):
            doc.read_parameters(limits=limits)
    with pytest.raises(icadkit.LimitExceededError):
        doc.read_parameters(part_limits=icadkit.PartLimits(max_entities=1))
    with pytest.raises(TypeError):
        doc.read_parameters(limits={})
    for bad in (0, -1, 1 << 32):
        with pytest.raises(ValueError):
            icadkit.ParameterLimits(max_bytes=bad)
    with pytest.raises(TypeError):
        icadkit.ParameterLimits(max_parameters=True)


def test_unsupported_profile_is_not_successful_empty_parameter_list():
    raw = bytearray(template())
    raw[12:16] = b"\0\x08\0\x04"
    result = icadkit.read(bytes(raw)).read_parameters()
    assert result.status == "unsupported" and result.parameters == ()
