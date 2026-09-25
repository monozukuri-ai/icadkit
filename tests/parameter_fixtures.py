"""Independent synthetic parametric records, never copied from vendor files."""

import struct

from part_fixtures import ROOT, part, view_parts


def preamble():
    data = bytearray(240)
    struct.pack_into("<5I", data, 0, 0x20000000, 236, 1, 0, 0xC9000088)
    data[80:104] = b"#-PARAMETRIC-CONDITION-#"
    struct.pack_into("<I", data, 160, 0x81000050)
    return bytes(data)


def parametric_part(source_id=ROOT, **kwargs):
    data = bytearray(part(source_id, **kwargs)) + bytearray(348)
    struct.pack_into("<I", data, 4, 700)
    struct.pack_into("<I", data, 12, 1)
    struct.pack_into("<3I", data, 356, 348, 1, 1)
    return bytes(data)


def value_row(name="width", value=19.125, *, kind=1, equation="", explain="説明"):
    row = bytearray(376)
    struct.pack_into("<I", row, 0, kind)
    row[4:68] = name.encode("cp932").ljust(64, b"\0")
    struct.pack_into("<I6d", row, 68, bool(equation), 7.5, 0, 0, value, 0, 0)
    row[120:248] = equation.encode("cp932").ljust(128, b"\0")
    row[248:376] = explain.encode("cp932").ljust(128, b"\0")
    return bytes(row)


def condition_row(index=0, *, revision=2, enabled=True, comment="長さ条件"):
    row = bytearray(296 if revision == 2 else 160)
    struct.pack_into("<I", row, 0, 1 if enabled else 0x101)
    row[4:132] = comment.encode("cp932").ljust(128, b"\0")
    struct.pack_into("<I", row, 132, 0x80000021)
    struct.pack_into("<I", row, 140, index)
    return bytes(row)


def table(kind, rows, *, revision=2, split=None, source_id=None):
    payload = b"".join(rows)
    chunks = [payload] if split is None else [payload[:split], payload[split:]]
    header = struct.pack(
        "<4I", 0xFD000010, 0x01000000, 0x10000 | revision << 8 | kind, len(rows)
    )
    contents = header + b"".join(
        struct.pack("<2I", 0xFD000000 | (len(chunk) + 8), 0x01000000) + chunk
        for chunk in chunks
    )
    return (
        struct.pack(
            "<6I",
            24 + len(contents),
            1,
            0,
            0x4D002081 | (1 + len(chunks)) << 16,
            source_id or 0x80000010 + kind,
            0,
        )
        + contents
    )


def template(*, profile="v8l3", values=None, conditions=None, revision=2, split=None):
    if values is None:
        values = [value_row()]
    if conditions is None:
        conditions = [condition_row(revision=revision)]
    return view_parts(
        [
            preamble(),
            parametric_part(root=True, name="Template", profile=profile),
            struct.pack("<I", 0x30010000),
            table(1, conditions, revision=revision),
            table(2, values, revision=revision, split=split),
            table(3, [bytes(16)], revision=revision),
        ],
        profile=profile,
    )
