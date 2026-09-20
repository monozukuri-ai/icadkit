"""Authored framing-only fixtures; these are not iCAD-produced CAD models."""

import zlib


def icd_factory():
    def make(
        order="little", name=None, tail=b"opaque remainder", drw_size=48, res_size=192
    ):
        data = bytearray()
        for tag, size in [(b"MOD", 256), (b"DRW", drw_size), (b"RES", res_size)]:
            data += tag + b"0" + (size // 4).to_bytes(4, order)
            data += b"\xa5" * (size - 12) + tag + b"1"
        data[12:16] = b"\x00\x08\x00\x03"
        name = "試験図面".encode("cp932") if name is None else name
        assert len(name) <= 40
        data[16:56] = name.ljust(40, b" ")
        return bytes(data) + tail

    return make


def resource_factory():
    """Authored USR owner, including deliberately arbitrary node-body bytes."""

    def make(
        payload=None,
        *,
        version=6,
        count=3,
        order="little",
        encoded=None,
        declared=None,
        source_id=42,
    ):
        if payload is None:
            description = b"synthetic M2 envelope"
            schema = b"SCH_3000000_30000"
            payload = (
                b"PS\0\0"
                + len(description).to_bytes(2, "big")
                + description
                + len(schema).to_bytes(4, "big")
                + schema
                + b"\0" * 4
                + b"not parsed as nodes"
                + b"\0\x01\0\x01"
            )
        header_size = 32 if version in (0, 6) else 104
        stride = 20 if version >= 5 else 16
        header = bytearray(header_size)
        header[0:2] = bytes(
            [0x85 if version == 0 else 0x87 if version == 6 else 0x86, version]
        )
        header[2:4] = (0 if version == 0 else header_size).to_bytes(2, order)
        header[8:12] = source_id.to_bytes(4, order)
        header[16:20] = (len(payload) if declared is None else declared).to_bytes(
            4, order
        )
        table_at = 24 if version == 6 else 28
        header[table_at : table_at + 4] = count.to_bytes(4, order)
        table = b"\xa5" * (count * stride)
        table += b"\0" * (-len(table) % 8)
        if encoded is None:
            encoded = zlib.compress(payload) if version >= 4 else payload
        body = header + table + encoded
        body += b"\0" * (-len(body) % 8)
        body[4:8] = len(body).to_bytes(4, order)
        return bytes(body), payload

    return make


def document_factory():
    def make(
        entities=(),
        *,
        order="little",
        tail=b"unparsed tail",
        with_usr=True,
        view_payload=b"\0" * 4,
        end_entity=True,
    ):
        def record(tag, payload):
            assert len(payload) % 4 == 0
            length = len(payload) + 12
            return tag + b"0" + (length // 4).to_bytes(4, order) + payload + tag + b"1"

        mod = bytearray(record(b"MOD", b"\0" * 244))
        mod[16:56] = "合成試験".encode("cp932").ljust(40, b" ")
        res = record(b"RES", b"\0" * 180)
        view = record(b"V/W", view_payload)
        usr_start = 496 + len(view)
        usr = b""
        if with_usr:
            prefix = b"\0" * 8 + b"\xe0\0\0\0" + b"\0" * 4
            opening = b"\x80\xff\0\x05" + (16).to_bytes(4, order) + b"\0" * 8
            group = b"\x84\0\0\0" + (16).to_bytes(4, order) + b"3DGLOBAL"
            ending = b"\x8f\0\0\0" + (8).to_bytes(4, order) if end_entity else b""
            usr = record(
                b"USR",
                b"\0" * 4 + prefix + opening + group + b"".join(entities) + ending,
            )
        offsets = [304, 496, usr_start if with_usr else 0]
        lengths = [len(res), len(view), len(usr)]
        drw = record(
            b"DRW",
            b"\0" * 4
            + b"".join(n.to_bytes(4, order) for n in offsets)
            + b"".join((n // 4).to_bytes(4, order) for n in lengths)
            + b"3DGLOBAL",
        )
        return bytes(mod) + drw + res + view + usr + tail

    return make
