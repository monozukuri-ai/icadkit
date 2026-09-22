"""Authored part framing cases; not files exported from a CAD application."""

import struct

from fixture_builders import document_factory

ROOT = 0xE0000001
A = 0xA0000004
B = 0xA0000007


def part(
    source_id,
    *,
    parent=0,
    child=0,
    previous=0,
    next_id=0,
    name="部品",
    comment="",
    root=False,
    position=(0, 0, 0),
    axes=(0, 0, 1, 1, 0, 0),
    flags=None,
    reference="",
    profile="v8l3",
):
    data = bytearray(356)
    tag = {"v7l7": 0x61000002, "v8l3": 0x61000003}[profile]
    struct.pack_into("<5I", data, 0, tag, 352, 64 if root else 0, 0, source_id)
    data[20:60] = name.encode("cp932").ljust(40, b" ")
    data[60:108] = comment.encode("cp932").ljust(48, b" ")
    struct.pack_into("<9d", data, 108, *position, 0, 0, 1, 1, 0, 0)
    struct.pack_into("<9d", data, 180, *position, *axes)
    struct.pack_into("<4I", data, 260, parent, child, previous, next_id)
    if flags is not None:
        struct.pack_into("<I", data, 8, flags)
    data[276:316] = reference.encode("cp932").ljust(40, b" ")
    return bytes(data)


def extra_info(value, *, first=True):
    raw = value.encode("utf-16-le") if isinstance(value, str) else value
    raw += b"\0" * (-len(raw) % 4)
    record = (
        struct.pack(
            "<8I",
            len(raw) + 32,
            1,
            0,
            0xCF010081,
            0x80000001,
            0,
            0xFD000000 | (len(raw) + 8),
            0x10000000,
        )
        + raw
    )
    return (struct.pack("<I", 0x30010000) if first else b"") + record


def view_parts(records, *, end=True, prefix=None, after=b"", profile="v8l3"):
    header = bytearray(796)
    for at, value in [
        (16, 132),
        (20, 60),
        (28, 0xFF12),
        (44, 0x02000110),
        (556, 0x00800011),
    ]:
        struct.pack_into("<I", header, at, value)
    header[564:572] = b"3DGLOBAL"
    if prefix is not None:
        header = bytearray(prefix)
    payload = bytes(header[8:]) + b"".join(records)
    payload += (b"\0\0\0\xfe" if end else b"") + after
    data = bytearray(document_factory()(view_payload=payload, with_usr=False, tail=b""))
    data[12:16] = {"v7l7": b"\0\x07\0\x07", "v8l3": b"\0\x08\0\x03"}[profile]
    return bytes(data)


def nested():
    return view_parts(
        [
            part(ROOT, child=A, name="Assembly", root=True),
            part(A, parent=ROOT, child=B, name="同名部品", comment="鋼板"),
            part(B, parent=A, name="同名部品", position=(100, -20, 30)),
        ]
    )
