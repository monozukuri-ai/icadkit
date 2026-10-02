"""Authored trailing-container bytes; not produced by a CAD application."""

import struct
import zlib


def record(kind, subtype, header=b"", body=b""):
    size = 8 + len(header) + len(body)
    return bytes([kind, subtype]) + struct.pack("<HI", 8 + len(header), size) + header


def framed(kind, subtype, header=b"", body=b""):
    return record(kind, subtype, header, body) + body


def payload(bounds=(-3, -5, 0, 7, 11, 13), *, kind=0x96, marker=52, extra=b"\x5a" * 20):
    # The packed header stays little-endian in a big-endian document.
    head = struct.pack("<I6f", kind, *bounds) + struct.pack("<3I", 0, marker, 0)
    return struct.pack("<I", 4 + len(head) + len(extra)) + head + extra


def block(source_id, decoded, *, encoded=None, declared=None, pad=None, zero=0):
    encoded = zlib.compress(decoded) if encoded is None else encoded
    filler = -(24 + len(encoded)) % 16 if pad is None else pad
    header = struct.pack(
        "<4I",
        source_id,
        len(encoded),
        len(decoded) if declared is None else declared,
        zero,
    )
    return framed(0x23, 8, header, encoded + b"\0" * filler)


def view(name, blocks, *, count=None, largest=None):
    sizes = [struct.unpack_from("<I", b, 16)[0] for b in blocks]
    header = name.ljust(8).encode("ascii") + struct.pack(
        "<II",
        len(blocks) if count is None else count,
        max(sizes, default=0) if largest is None else largest,
    )
    return framed(0x22, 0, header, b"".join(blocks))


def trailer(views, *, revision=8, table=None, view_count=None, extra=b""):
    count = len(views) if view_count is None else view_count
    group = framed(
        0x20,
        0,
        b"",
        framed(0x21, revision, struct.pack("<II", count, 0), b"".join(views)),
    )
    tables = b""
    if table is not None:
        tables = framed(0x40, 0, b"", framed(0x03, 0, struct.pack("<II", 1, 0), table))
    return framed(0x10, 0, b"", tables + group + extra)
