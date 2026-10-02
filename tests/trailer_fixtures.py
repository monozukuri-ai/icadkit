"""Authored trailing-container bytes; not produced by a CAD application."""

import struct
import zlib


def record(kind, subtype, header=b"", body=b""):
    size = 8 + len(header) + len(body)
    return bytes([kind, subtype]) + struct.pack("<HI", 8 + len(header), size) + header


def framed(kind, subtype, header=b"", body=b""):
    return record(kind, subtype, header, body) + body


def payload(
    bounds=(-3, -5, 0, 7, 11, 13),
    *,
    kind=0x96,
    marker=52,
    faces=(),
    edges=(),
    extra=b"\x5a" * 20,
    edges_at=None,
    items_at=None,
    shift=0,
):
    """A block with a face table, an edge table and their items.

    faces are (node id, surface code, parameter box, item bytes) and edges are
    (node id, (face, face), item bytes). The packed fields stay little-endian
    in a big-endian document. shift moves every stored item offset.
    """
    table_end = 52 + 36 * len(faces) + 24 * len(edges)
    items = [item for *_, item in faces] + [item for *_, item in edges]
    offsets = [table_end + sum(map(len, items[:i])) + shift for i in range(len(items))]
    head = struct.pack("<I6f", kind, *bounds) + struct.pack(
        "<5I",
        0,
        marker,
        52 + 36 * len(faces) if edges_at is None else edges_at,
        len(faces) | len(edges) << 16,
        table_end if items_at is None else items_at,
    )
    body = b"".join(
        struct.pack("<4BI4BIHH4f", 0x10, 0, 0, 0, node, 0, 0, code, 1, at, 0, 0, *box)
        for (node, code, box, _), at in zip(faces, offsets, strict=False)
    ) + b"".join(
        struct.pack("<4BI4H4BI", 0x10, 0, 0, 0, node, *pair, 0, 0, 1, 1, 0, 2, at)
        for (node, pair, _), at in zip(edges, offsets[len(faces) :], strict=True)
    )
    data = head + body + b"".join(items) + extra
    return struct.pack("<I", 4 + len(data)) + data


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
