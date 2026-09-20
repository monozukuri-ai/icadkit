import errno
import os
import sys
from dataclasses import FrozenInstanceError

import pytest

import icadkit


@pytest.mark.parametrize("order", ["little", "big"])
def test_bytes_path_and_string_have_identical_results(order, icd_factory, tmp_path):
    payload = icd_factory(order)
    path = tmp_path / "日本語 名称.icd"
    path.write_bytes(payload)
    result = icadkit.inspect(path)
    assert result == icadkit.inspect(str(path)) == icadkit.inspect(payload)
    assert result.header.byte_order == order
    assert result.header.raw_version == b"\0\x08\0\x03"
    assert result.header.raw_mod == payload[:256]
    assert result.header.raw_name == payload[16:56]
    assert result.header.name_cp932_candidate == "試験図面"
    assert result.bytes_read == 280
    assert result.file_size == len(payload)
    assert [r.tag for r in result.leading_records] == ["MOD", "DRW", "RES"]
    assert [r.byte_range.length for r in result.leading_records] == [256, 48, 192]
    assert result.status.header == "complete"
    assert result.status.container == "partial"
    assert result.status.model == result.status.brep == "not_checked"
    assert not result.diagnostics
    assert [(r.byte_range.start, r.byte_range.end) for r in result.unparsed_ranges] == [
        (8, 12),
        (56, 252),
        (264, 300),
        (312, 492),
        (496, len(payload)),
    ]
    with pytest.raises(FrozenInstanceError):
        result.header.raw_name = b"changed"


def test_name_decode_failure_keeps_original_bytes(icd_factory):
    result = icadkit.inspect(icd_factory(name=b"\x81"))
    assert result.header.name_cp932_candidate is None
    assert result.header.raw_name == b"\x81" + b" " * 39
    assert result.status.header == "complete"
    assert result.diagnostics[0].code == "header.name_decode_failed"
    assert result.diagnostics[0].byte_offset == 16


@pytest.mark.parametrize("name", [b"", b"a\0b", b"\xfa\x40", b"a" * 40, b"a\0 \0"])
def test_name_is_only_trimmed_at_the_right_edge(name, icd_factory):
    result = icadkit.inspect(icd_factory(name=name))
    assert result.header.name_cp932_candidate == name.rstrip(b" \0").decode("cp932")
    assert result.header.raw_name == name.ljust(40, b" ")


@pytest.mark.parametrize(
    "offset,code",
    [
        (0, "header.invalid_magic"),
        (252, "record.end_tag_mismatch"),
        (256, "record.start_tag_mismatch"),
        (300, "record.end_tag_mismatch"),
        (304, "record.start_tag_mismatch"),
        (492, "record.end_tag_mismatch"),
    ],
)
def test_structured_format_diagnostics(offset, code, icd_factory):
    data = bytearray(icd_factory())
    data[offset] ^= 255
    with pytest.raises(icadkit.InvalidFormatError) as failure:
        icadkit.inspect(bytes(data))
    assert isinstance(failure.value, icadkit.IcadError)
    assert failure.value.diagnostic.code == code
    assert failure.value.diagnostic.category == "invalid"
    assert failure.value.diagnostic.byte_offset == offset


def test_unknown_mod_length_is_unsupported(icd_factory):
    data = bytearray(icd_factory())
    data[4:8] = (65).to_bytes(4, "little")
    with pytest.raises(icadkit.UnsupportedFormatError) as failure:
        icadkit.inspect(bytes(data))
    assert failure.value.diagnostic.code == "header.unsupported_layout"
    assert failure.value.diagnostic.byte_offset == 4


@pytest.mark.parametrize("source", [None, 7, bytearray(8), memoryview(b"12345678")])
def test_rejects_ambiguous_or_mutable_input(source):
    with pytest.raises(TypeError):
        icadkit.inspect(source)


def test_pathlike_bytes_is_not_treated_as_data():
    class BytePath:
        def __fspath__(self):
            return b"not a text path"

    with pytest.raises(TypeError, match="must return str"):
        icadkit.inspect(BytePath())
    with pytest.raises(icadkit.InvalidFormatError):
        icadkit.inspect(b"literal bytes are never a filename")


@pytest.mark.parametrize(
    "value,error",
    [
        (0, ValueError),
        (-1, ValueError),
        (2**64, ValueError),
        (1.5, TypeError),
        (True, TypeError),
    ],
)
def test_limits_validation(value, error):
    with pytest.raises(error):
        icadkit.InspectionLimits(max_file_bytes=value)


def test_file_and_record_limits_are_distinct(icd_factory):
    payload = icd_factory()
    for limits, code in [
        (icadkit.InspectionLimits(max_file_bytes=len(payload) - 1), "limit.file_bytes"),
        (icadkit.InspectionLimits(max_record_bytes=255), "limit.record_bytes"),
    ]:
        with pytest.raises(icadkit.LimitExceededError) as failure:
            icadkit.inspect(payload, limits=limits)
        assert failure.value.diagnostic.code == code
    assert icadkit.inspect(
        payload, limits=icadkit.InspectionLimits(max_file_bytes=len(payload))
    )
    with pytest.raises(TypeError, match="InspectionLimits"):
        icadkit.inspect(payload, limits={})


def test_os_errors_remain_os_errors(tmp_path):
    path = tmp_path / "missing.icd"
    with pytest.raises(FileNotFoundError) as failure:
        icadkit.inspect(path)
    assert failure.value.filename == str(path)
    with pytest.raises(OSError):
        icadkit.inspect(tmp_path)


@pytest.mark.parametrize("drw_size,res_size,offset", [(512, 192, 260), (48, 512, 308)])
def test_record_limit_applies_to_drw_and_res(drw_size, res_size, offset, icd_factory):
    data = icd_factory(drw_size=drw_size, res_size=res_size)
    with pytest.raises(icadkit.LimitExceededError) as failure:
        icadkit.inspect(data, limits=icadkit.InspectionLimits(max_record_bytes=256))
    assert failure.value.diagnostic.code == "limit.record_bytes"
    assert failure.value.diagnostic.byte_offset == offset


def test_default_file_limit_rejects_without_reading_body(tmp_path):
    path = tmp_path / "over-limit.icd"
    # A sparse logical file; the body is neither allocated nor read by inspect.
    with path.open("wb") as file:
        file.truncate(512 * 1024 * 1024 + 1)
    with pytest.raises(icadkit.LimitExceededError) as failure:
        icadkit.inspect(path)
    assert failure.value.diagnostic.code == "limit.file_bytes"


def test_large_sparse_tail_is_not_read(tmp_path, icd_factory):
    path = tmp_path / "sparse.icd"
    with path.open("wb") as file:
        file.write(icd_factory(tail=b""))
        file.truncate(64 * 1024 * 1024)
    result = icadkit.inspect(path)
    assert result.file_size == 64 * 1024 * 1024
    assert result.bytes_read == 280
    assert result.unparsed_ranges[-1].byte_range == icadkit.ByteRange(
        496, result.file_size
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Unix filesystem byte names")
def test_surrogate_escaped_file_name(tmp_path, icd_factory):
    path = os.fsencode(tmp_path) + b"/name_\xff.icd"
    try:
        with open(path, "wb") as file:
            file.write(icd_factory())
    except OSError as exc:
        if exc.errno == errno.EILSEQ:
            pytest.skip("Filesystem rejects non-UTF-8 filename bytes (EILSEQ)")
        raise
    assert icadkit.inspect(os.fsdecode(path)).bytes_read == 280
