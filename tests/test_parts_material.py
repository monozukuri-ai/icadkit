"""Saved material subrecords of the metadata block that follows a part record."""

import math
import struct

import pytest
from part_fixtures import part, view_parts

import icadkit


def material_block(
    name, matid, gravity, *, extra=b"", name_bytes=None, matid_bytes=None
):
    sub = bytearray(216)
    struct.pack_into("<I", sub, 0, 0x7A000000 | 216)
    sub[16:96] = (name.encode("cp932") if name_bytes is None else name_bytes).ljust(
        80, b" "
    )
    sub[96:136] = (matid.encode("cp932") if matid_bytes is None else matid_bytes).ljust(
        40, b" "
    )
    struct.pack_into("<d", sub, 136, gravity)
    group = struct.pack("<I", 0x79000014) + b"\0" * 16
    body = bytes(sub) + group + extra
    header = bytearray(160)
    struct.pack_into("<4I", header, 0, 0x21000000, 156 + len(body), 2 + bool(extra), 0)
    struct.pack_into("<I", header, 16, 0xC9500088)
    return bytes(header) + body


def model(blocks, profile="v8l3"):
    root = part(0xE0000001, root=True, child=0xA0000003, name="model", profile=profile)
    box = part(0xA0000003, parent=0xE0000001, name="箱", profile=profile)
    return view_parts([root, box, *blocks], profile=profile)


@pytest.mark.parametrize("profile", ["v8l3", "v7l7"])
def test_material_belongs_to_the_preceding_part(profile):
    index = icadkit.read(
        model([material_block("一般構造用圧延鋼材", "SS400", 7.85)], profile)
    ).read_parts()
    assert index.status.index == "complete"
    root, box = index.parts
    assert root.materials == () and root.material is None
    (material,) = box.materials
    assert material == box.material
    assert material.owner_id == box.part_id
    assert (material.name, material.material_id) == ("一般構造用圧延鋼材", "SS400")
    assert material.specific_gravity == 7.85 and material.status == "complete"
    assert material.raw_material_id == b"SS400".ljust(40, b" ")
    assert material.byte_range.length == 216
    row = index.to_rows()[0]
    assert row["materials"][0]["material_id"] == "SS400"
    assert row["materials"][0]["specific_gravity"] == 7.85


def test_bodies_with_different_materials_keep_a_list_and_no_single_material():
    data = model(
        [material_block("鋼", "SS400", 7.85), material_block("アルミ", "A5052P", 2.69)]
    )
    box = icadkit.read(data).read_parts().parts[1]
    assert [m.material_id for m in box.materials] == ["SS400", "A5052P"]
    assert box.material is None
    same = model([material_block("鋼", "SS400", 7.85)] * 2)
    assert icadkit.read(same).read_parts().parts[1].material.material_id == "SS400"


def test_other_material_lengths_stay_opaque():
    short = struct.pack("<I", 0x7A000020) + b"\0" * 28
    box = icadkit.read(model([material_block("鋼", "SS400", 7.85, extra=short)]))
    (material,) = box.read_parts().parts[1].materials
    assert material.material_id == "SS400"


def test_undecodable_text_and_bad_gravity_are_reported():
    box = (
        icadkit.read(model([material_block("", "SS400", 7.85, name_bytes=b"\x81")]))
        .read_parts()
        .parts[1]
    )
    (material,) = box.materials
    assert material.name is None and material.material_id == "SS400"
    assert material.status == "partial"
    assert [d.code for d in material.diagnostics] == ["parts.material_text"]
    box = icadkit.read(model([material_block("鋼", "SS400", math.nan)])).read_parts()
    (material,) = box.parts[1].materials
    assert material.specific_gravity is None and material.status == "invalid"
    assert material.diagnostics[0].code == "parts.material_gravity"


def test_all_zero_record_is_an_empty_material():
    """A body without a material keeps an all-zero record; the SDK reports it
    as a material with blank name, blank identifier and gravity 0."""
    empty = material_block("", "", 0.0, name_bytes=b"\0" * 80, matid_bytes=b"\0" * 40)
    box = (
        icadkit.read(model([empty, material_block("鋼", "S45C", 7.84)]))
        .read_parts()
        .parts[1]
    )
    blank, steel = box.materials
    assert blank.is_empty and not steel.is_empty
    assert (blank.name, blank.material_id, blank.specific_gravity) == ("", "", None)
    assert blank.raw_name == b"\0" * 80 and blank.raw_material_id == b"\0" * 40
    assert blank.status == "complete" and blank.diagnostics == ()
    assert box.material is None
    rows = icadkit.read(model([empty])).read_parts().to_rows()
    assert rows[0]["materials"][0]["is_empty"] is True
    assert rows[0]["materials"][0]["status"] == "complete"
    # Blank text with a non-zero, non-finite gravity is still invalid.
    bad = material_block(
        "", "", math.inf, name_bytes=b"\0" * 80, matid_bytes=b"\0" * 40
    )
    (material,) = icadkit.read(model([bad])).read_parts().parts[1].materials
    assert material.is_empty and material.status == "invalid"
    assert material.diagnostics[0].code == "parts.material_gravity"
