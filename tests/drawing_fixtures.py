"""Independently authored framing/geometry fixtures; no vendor file bytes."""

import math
import struct

from fixture_builders import document_factory


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


def text_entity(lines=("試験", "A12")):
    b = bytearray(88)
    b[4], b[12], b[15] = 1, 0x40, 21
    struct.pack_into("<I", b, 16, 0x80000456)
    b[24:28] = b"\x40\0\0\xfd"
    for line in lines:
        raw = line.encode("utf-16-le")
        run = bytearray(56) + raw + b"\0\0"
        run += b"\0" * (-len(run) % 4)
        struct.pack_into("<H", run, 0, len(run))
        run[2:4] = b"\x44\x20"
        run[48:52] = b"\x20\x20\0\x01"
        struct.pack_into("<I", run, 52, len(raw) // 2)
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
    if stream is None:
        stream = (0x30010000).to_bytes(4, order) + b"".join(records) if records else b""
    b += stream
    if end:
        b += (0xFE000000).to_bytes(4, order)
    data = bytearray(
        document_factory()(order=order, view_payload=b[8:], with_usr=False, tail=b"")
    )
    data[12:16] = version
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
