#!/usr/bin/env python3
"""Generate public synthetic cases without iCAD, schemas or icadkit imports."""

import argparse
import runpy
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
builders = runpy.run_path(str(ROOT / "tests/fixture_builders.py"))
geometry = runpy.run_path(str(ROOT / "tests/geometry_fixtures.py"))


def stored_zlib(payload):
    # One uncompressed DEFLATE block: stable bytes across OS/zlib versions.
    if len(payload) >= 65536:
        raise ValueError("fixture exceeds one stored block")
    return (
        b"\x78\x01\x01"
        + struct.pack("<HH", len(payload), 65535 - len(payload))
        + payload
        + struct.pack(">I", zlib.adler32(payload))
    )


def cases():
    document = builders["document_factory"]()
    resource = builders["resource_factory"]()
    wire = geometry["wire_payload"]()
    result = {"native-uninterpreted": document(with_usr=False)}
    for order in ("little", "big"):
        for version, encoding in ((0, "raw"), (6, "zlib")):
            owner, _ = resource(
                wire,
                version=version,
                count=0,
                order=order,
                encoded=stored_zlib(wire) if encoding == "zlib" else wire,
            )
            result[f"wire-{order}-{encoding}"] = document([owner], order=order)
    unknown = geometry["header"](b"SCH_9999999_99999") + b"\0\1\0\1"
    bad_checksum = bytearray(stored_zlib(wire))
    bad_checksum[-1] ^= 1
    for name, payload, encoded in (
        ("unknown-schema", unknown, stored_zlib(unknown)),
        ("invalid-reference", geometry["wire_payload"](bad_reference=True), None),
        ("invalid-checksum", wire, bytes(bad_checksum)),
    ):
        owner, _ = resource(
            payload,
            version=6,
            count=0,
            encoded=stored_zlib(payload) if encoded is None else encoded,
        )
        result[name] = document([owner])
    return result


def generate(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, payload in cases().items():
        path = root / f"{name}.icd"
        if path.exists() and path.read_bytes() != payload:
            raise ValueError(f"refusing to replace changed fixture: {path}")
        path.write_bytes(payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    generate(args.output)
    print(f"Generated {len(cases())} synthetic inputs", file=sys.stderr)
