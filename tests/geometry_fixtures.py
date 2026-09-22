"""Authored, non-iCAD wire fixture in neutral Parasolid V30.

The compact scalar layout follows the MIT/Apache-2.0 parasolid-core 0.2.0
onshape_sch30000 profile. Topology and coordinates are authored for these tests;
no installed iCAD catalog or vendor CAD payload is used or needed.
The embedded variant uses the reviewed 13006 BODY/REGION field subset and
unchanged declarations for the exact iCAD V34 profile added in version 0.3.0.
"""

import struct


def header(key=b"SCH_3000000_30000"):
    description = b"icadkit authored wire fixture"
    return (
        b"PS\0\0"
        + struct.pack(">H", len(description))
        + description
        + struct.pack(">I", len(key))
        + key
        + (struct.pack(">H", 205) if key.count(b"_") == 3 else b"")
        + b"\0" * 4
    )


CODES = {
    12: "DPPPPPPPPFFPPPUPUUPPPPPPPPPDPPPPD",
    13: "DPPPPPPPP",
    16: "DPFPPPPPPP",
    17: "PPPPPPPPPC",
    18: "DPPPPPFP",
    19: "DPPPPPCP",
    29: "DPPPPV",
    30: "DPPPPPCVV",
}


def wire_payload(*, bad_reference=False, embedded=False):
    # Endpoints (2,-1,3) and (5,3,3): independent length 5, unit direction (3/5,4/5,0).
    definitions = [
        (12, 1, {9: 1e-6, 10: 1e-8, 16: 2, 24: 2, 25: 6, 26: 4}),
        (19, 2, {2: 1, 5: 3, 6: ord("V")}),
        (13, 3, {7: 2}),
        (18, 4, {4: 9, 5: 999 if bad_reference else 5, 7: 1}),
        (29, 5, {2: 4, 5: (2, -1, 3)}),
        (16, 6, {3: 7, 6: 11, 9: 1}),
        (17, 7, {4: 4, 5: 8, 6: 6, 9: ord("+")}),
        (17, 8, {4: 9, 5: 7, 6: 6, 9: ord("-")}),
        (18, 9, {5: 10, 7: 1}),
        (29, 10, {2: 9, 5: (5, 3, 3)}),
        (30, 11, {2: 6, 7: (2, -1, 3), 8: (0.6, 0.8, 0)}),
    ]
    key = b"SCH_3401212_34101_13006" if embedded else b"SCH_3000000_30000"
    out = bytearray(header(key))
    declared = set()
    for kind, index, overrides in definitions:
        out += struct.pack(">H", kind)
        if embedded and kind not in declared:
            out.append(255)  # Unchanged reviewed base definition, once per type.
            declared.add(kind)
        out += struct.pack(">H", index + 1)
        defaults = {
            "D": index + 100,
            "P": 0,
            "F": -3.14158e13,
            "U": 0,
            "C": ord("+"),
            "V": (0, 0, 0),
        }
        ordinals = list(range(len(CODES[kind])))
        if embedded and kind == 12:
            ordinals = [*range(6), *range(8, 22), 24, 25, 26]
        elif embedded and kind == 19:
            ordinals = ordinals[:7]
        for i in ordinals:
            code = CODES[kind][i]
            value = overrides.get(i, defaults[code])
            if code == "P":
                out += struct.pack(">H", value + 1)
            elif code == "D":
                out += struct.pack(">i", value)
            elif code == "F":
                out += struct.pack(">d", value)
            elif code == "V":
                out += struct.pack(">ddd", *value)
            else:
                out.append(value)
    return bytes(out) + b"\0\1\0\1"


def catalog_bytes(schema_id="30000"):
    # A minimal explicit catalog sufficient for an empty neutral stream.
    return f"""**PARASOLID icadkit authored empty catalog
**END_OF_HEADER***************************************************
T
1
: SCHEMA FILE created by modeller version 3000000/{schema_id};
2 1 0 17
1 NULLP; Null; 1 0 0
**************** end of schema SCH_3000000_{schema_id} ****************
""".encode()
