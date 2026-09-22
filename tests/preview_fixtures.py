"""Authored box, not exported CAD data.

Scalar layouts use the MIT/Apache-2.0 parasolid-core V30 profile (see
geometry_fixtures.py). Coordinates, topology and expected measurements are
constructed here without a catalog, CAD application or optional dependency.
"""

import math
import struct

from geometry_fixtures import CODES, header


def box_payload(*, embedded=False):
    # A 10 x 20 x 30 box, translated to (2, -1, 3).
    positions = [
        (2, -1, 3),
        (12, -1, 3),
        (12, 19, 3),
        (2, 19, 3),
        (2, -1, 33),
        (12, -1, 33),
        (12, 19, 33),
        (2, 19, 33),
    ]
    faces = [
        ((0, 3, 2, 1), (0, 0, -1), (1, 0, 0)),
        ((4, 5, 6, 7), (0, 0, 1), (1, 0, 0)),
        ((0, 1, 5, 4), (0, -1, 0), (1, 0, 0)),
        ((3, 7, 6, 2), (0, 1, 0), (1, 0, 0)),
        ((0, 4, 7, 3), (-1, 0, 0), (0, 1, 0)),
        ((1, 2, 6, 5), (1, 0, 0), (0, 1, 0)),
    ]
    nodes = [
        (12, 1, {9: 1e-6, 10: 1e-8, 16: 1, 24: 2, 25: 100, 26: 10}),
        (19, 2, {2: 1, 3: 4, 5: 3, 6: ord("S")}),
        (13, 3, {4: 40, 7: 2}),
        (19, 4, {2: 1, 5: 5, 6: ord("V")}),
        (13, 5, {7: 4, 8: 40}),
    ]
    for v, position in enumerate(positions):
        nodes += [
            (18, 10 + v, {4: 11 + v if v < 7 else 0, 5: 20 + v, 7: 1}),
            (29, 20 + v, {2: 10 + v, 5: position}),
        ]
    edges = {}
    fins = {}
    for f, (cycle, normal, x_axis) in enumerate(faces):
        nodes += [
            (
                14,
                40 + f,
                {
                    3: 41 + f if f < 5 else 0,
                    5: 50 + f,
                    6: 3,
                    7: 60 + f,
                    11: 41 + f if f < 5 else 0,
                    13: 5,
                },
            ),
            (15, 50 + f, {2: 150 + 4 * f, 3: 40 + f}),
            (50, 60 + f, {2: 40 + f, 7: positions[cycle[0]], 8: normal, 9: x_axis}),
        ]
        for i, start in enumerate(cycle):
            end = cycle[(i + 1) % 4]
            pair = tuple(sorted((start, end)))
            if pair not in edges:
                edges[pair] = (len(edges), start, end, [])
            edge, _, _, members = edges[pair]
            fin = 150 + f * 4 + i
            members.append(fin)
            fins[fin] = {
                1: 50 + f,
                2: 150 + 4 * f + (i + 1) % 4,
                3: 150 + 4 * f + (i - 1) % 4,
                4: 10 + end,
                6: 100 + edge,
            }
    for edge, start, end, members in edges.values():
        direction = tuple(
            b - a for a, b in zip(positions[start], positions[end], strict=True)
        )
        length = math.dist(positions[start], positions[end])
        nodes += [
            (
                16,
                100 + edge,
                {3: members[0], 5: 101 + edge if edge < 11 else 0, 6: 120 + edge, 9: 1},
            ),
            (
                30,
                120 + edge,
                {
                    2: 100 + edge,
                    7: positions[start],
                    8: tuple(v / length for v in direction),
                },
            ),
        ]
        for fin in members:
            fins[fin][5] = next(other for other in members if other != fin)
            fins[fin][9] = ord("+" if fin == members[0] else "-")
    nodes += [(17, fin, attrs) for fin, attrs in fins.items()]
    codes = {**CODES, 14: "DPFPPPPPCPPPPP", 15: "DPPPP", 50: "DPPPPPCVVV"}
    key = b"SCH_3401212_34101_13006" if embedded else b"SCH_3000000_30000"
    out = bytearray(header(key))
    declared = set()
    for kind, index, attrs in sorted(nodes, key=lambda n: n[1]):
        out += struct.pack(">H", kind)
        if embedded and kind not in declared:
            out.append(255)
            declared.add(kind)
        out += struct.pack(">H", index + 1)
        defaults = {
            "D": index + 1000,
            "P": 0,
            "F": -3.14158e13,
            "U": 0,
            "C": ord("+"),
            "V": (0, 0, 0),
        }
        ordinals = list(range(len(codes[kind])))
        if embedded and kind == 12:
            ordinals = [*range(6), *range(8, 22), 24, 25, 26]
        elif embedded and kind == 19:
            ordinals = ordinals[:7]
        for i in ordinals:
            code = codes[kind][i]
            value = attrs.get(i, defaults[code])
            if code == "V":
                out += struct.pack(">ddd", *value)
            else:
                out += struct.pack(
                    {"D": ">i", "P": ">H", "F": ">d", "U": ">B", "C": ">B"}[code],
                    value + 1 if code == "P" else value,
                )
    return bytes(out) + b"\0\1\0\1"
