"""Independently authored framing/geometry fixtures; no vendor file bytes."""

import math
import struct
from dataclasses import replace

from fixture_builders import document_factory

import icadkit


def entity(kind=2, *, order="big", values=None, visible=True, layer=17, flags=0x44):
    prefix = ">" if order == "big" else "<"
    size, geom = {1: (56, 1), 2: (72, 2), 5: (72, 5), 6: (72, 4)}[kind]
    b = bytearray(size)
    struct.pack_into(prefix + "I", b, 0, size)
    b[4] = layer
    b[12] = 0x40 if visible else 0
    b[15] = kind
    struct.pack_into(prefix + "I", b, 16, 0x80000123)
    struct.pack_into(prefix + "H", b, 24, size - 24)
    b[26:32] = bytes([flags, geom, 0x82, 0x35, 0, 0])
    if values is None:
        values = {
            1: (-11, -17),
            2: (13, -7, 0.8, 0.6, 30),
            5: (47, -31, 9, math.radians(37), math.radians(-133)),
            6: (-19, 23, 7, 0, math.tau),
        }[kind]
    struct.pack_into(prefix + str(len(values)) + "d", b, 32, *values)
    return bytes(b)


TEXT_CONSTANTS = bytes.fromhex("400000fd000000013d00080000002800ff0901301800")
TEXT_LINE_WORDS = {
    1: (2, bytes.fromhex("000000020000")),
    2: (3, bytes.fromhex("030100020002")),
}


def text_entity(
    lines=("試験", "A12"),
    *,
    origin=(10.0, 20.0),
    height=4.0,
    width_ratio=1.0,
    space_ratio=1.0,
    row_space=2.5,
    base_point=1,
    direction=1,
    way=1,
    entity_id=0x80000456,
    scale=1.0,
    angle_words=None,
    glyphs=None,
    layer=1,
    width=2,
    style=1,
    color=1,
):
    """A text record in the observed layout; ``glyphs`` appends per-line cache bytes."""
    n = len(lines)
    b = bytearray(88)
    b[4], b[12], b[14], b[15] = layer, 0x40, n + 1, 21
    struct.pack_into("<I", b, 16, entity_id)
    b[24:46] = TEXT_CONSTANTS
    b[46], b[47], b[48], b[49], b[50], b[51] = n, 8, n, base_point, direction, way
    b[68:72] = b"\x80\0\0\0"
    b[72], b[73] = width, color
    b[74:80] = TEXT_LINE_WORDS.get(n, (0, bytes(6)))[1]
    struct.pack_into("<d", b, 80, scale)
    for i, line in enumerate(lines):
        x, y = origin
        if direction == 2:
            x -= i * (height + row_space)
        else:
            y -= i * (height + row_space)
        run = bytearray(56)
        run[2:4] = b"\x44\x20"
        run[4], run[5] = width, (style << 4) | color
        struct.pack_into("<2d", run, 8, x, y)
        struct.pack_into(
            "<3f", run, 24, height, height * width_ratio, height * space_ratio
        )
        words = angle_words or ((0x20000000, 0x60000000) if direction == 2 else (0, 0))
        struct.pack_into("<2I", run, 36, *words)
        run[48:52] = b"\x20\0\0\x01"
        struct.pack_into("<I", run, 52, len(line))
        run += line.encode("utf-16-le")
        if glyphs:
            run += b"\0" * (-len(run) % 4) + glyphs[i]
        run += b"\0" * (-len(run) % 8)
        struct.pack_into("<H", run, 0, len(run))
        b += run
    struct.pack_into("<I", b, 0, len(b))
    return bytes(b)


def drawing(
    records=(),
    *,
    order="big",
    version=b"\0\x03\0\x04",
    name=b"!!GLOBAL",
    number=1,
    extension=0,
    stream=None,
    end=True,
    count=None,
    scale=1.0,
    extent=None,
):
    at = 28 + extension * 4
    b = bytearray(at + 240)
    b[16:20] = extension.to_bytes(4, order)
    b[20:24] = (60).to_bytes(4, order)
    if extension:
        b[28:32] = b"\x12\xff\0\0"
    b[at : at + 4] = b"\x11\0\x80\0" if name == b"3DGLOBAL" else b"\x10\0\0\0"
    if name == b"!!GLOBAL":
        b[at + 2 : at + 4] = (1).to_bytes(2, order)
    b[at + 8 : at + 16] = name.ljust(8, b" ")
    b[at + 26 : at + 28] = number.to_bytes(2, order)
    prefix = ">" if order == "big" else "<"
    if count is None:
        count = len(records) if stream is None else 0
    struct.pack_into(prefix + "I", b, at + 28, count)
    struct.pack_into(prefix + "d", b, at + 32, scale)
    # Observed sentinel of a view without extent: +1e38 minima, -1e38 maxima.
    struct.pack_into(prefix + "4f", b, at + 40, *(extent or (1e38, 1e38, -1e38, -1e38)))
    b[at + 96 : at + 104] = b"1/1     "
    if stream is None:
        stream = (0x30010000).to_bytes(4, order) + b"".join(records) if records else b""
    b += stream
    if end:
        b += (0xFE000000).to_bytes(4, order)
    # Record word +24: the entity-group size in words, as observed in most files.
    group_words = (len(b) - (at + 240) - (4 if end else 0)) // 4
    struct.pack_into(prefix + "I", b, 24, group_words)
    data = bytearray(
        document_factory()(order=order, view_payload=b[8:], with_usr=False, tail=b"")
    )
    data[12:16] = version
    data[292:300] = name.ljust(8, b" ")  # The directory repeats the view name.
    return bytes(data)


def big_part(source_id=0xE0000001, *, root=True, parent=0, child=0, flags=None):
    b = bytearray(356)
    struct.pack_into(
        ">IIH", b, 0, 0x61000001, 352, (64 if root else 0) if flags is None else flags
    )
    struct.pack_into(">I", b, 16, source_id)
    b[20:60] = "試作部品".encode("cp932").ljust(40, b" ")
    b[60:108] = b"comment".ljust(48, b" ")
    for at in (108, 180):
        struct.pack_into(">9d", b, at, 13, -7, 5, 0, 0, 1, 1, 0, 0)
    struct.pack_into(">4I", b, 260, parent, child, 0, 0)
    return bytes(b)


def placement(
    name,
    *,
    origin=(0.0, 0.0),
    scale=1.0,
    entity_id=0x80000301,
    order="little",
    first=False,
):
    """A view placement record of the 2D global view in the observed layout."""
    prefix = ">" if order == "big" else "<"
    b = bytearray(264)
    struct.pack_into(prefix + "I", b, 0, 264)
    b[12:16] = bytes.fromhex("400001cb")
    struct.pack_into(prefix + "I", b, 16, entity_id)
    b[24:28] = bytes.fromhex("f00040fd" if order == "little" else "00f040fd")
    b[32:40] = bytes.fromhex("2060000001200000")
    struct.pack_into(prefix + "3d", b, 40, origin[0], origin[1], 0.0)
    struct.pack_into(prefix + "d", b, 64, scale)
    b[96:136] = b" " * 40
    b[136:144] = name.encode("cp932").ljust(8, b" ")
    b[144:176] = b"\xff" * 32
    struct.pack_into(prefix + "6f", b, 176, 1e38, 1e38, 0, -1e38, -1e38, 0)
    return ((0x40000000).to_bytes(4, order) if first else b"") + bytes(b)


def sheet(records=(), *, order="little", version=b"\0\x08\0\x03", extent=None):
    """A 2D global view with one placement record, then the 2D view ``!XY``."""
    stream = placement("!XY", order=order, first=True)
    data = bytearray(drawing(order=order, version=version, stream=stream))
    struct.pack_into((">" if order == "big" else "<") + "I", data, 520, 0)
    base = icadkit.read(bytes(data)).container()
    other = icadkit.read(
        drawing(
            records, order=order, version=version, name=b"!XY", number=2, extent=extent
        )
    ).container()
    return replace(
        base,
        view_names=base.view_names + other.view_names,
        records=base.records + tuple(r for r in other.records if r.tag == "V/W"),
    ).to_bytes()
