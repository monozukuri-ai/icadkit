"""Bounded V7L7 final-body programs; operands are never finished entities.

Reading is dependency-free. Evaluation imports the optional OCCT runtime only
on request. This API does not establish complete model or feature-history support.
"""

from __future__ import annotations

import math
import struct
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

from .errors import (
    IcadError,
    InvalidFormatError,
    LimitExceededError,
    UnsupportedFormatError,
)
from .models import ByteRange, Diagnostic, ErrorCategory, Status
from .native import NativeAppearance, NativePrimitive, ProfileSegment
from .parts import Part, PartIndex, PartLimits, _frame, _relative

if TYPE_CHECKING:
    from .document import Document


@dataclass(frozen=True)
class CsgLimits:
    max_bodies: int = 256
    max_operands: int = 64
    max_operations: int = 128
    max_stack_depth: int = 64
    max_subshapes: int = 20_000
    max_triangles: int = 250_000
    max_vertices: int = 500_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 < value <= 2**31 - 1
            ):
                raise ValueError(f"{name} must be a positive bounded integer")


@dataclass(frozen=True)
class CsgOperand:
    source_id: int
    entity_id: str
    primitive: NativePrimitive


@dataclass(frozen=True)
class CsgBody:
    body_id: str
    owner_id: str
    source_id: int | None
    tree_id: int | None
    tokens: tuple[int, ...]
    operands: tuple[CsgOperand, ...]
    byte_range: ByteRange
    program_range: ByteRange | None
    raw_program: bytes
    appearance: NativeAppearance
    status: Status
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class CsgIndex:
    source_sha256: str
    bodies: tuple[CsgBody, ...]
    status: Status
    diagnostics: tuple[Diagnostic, ...]
    model_status: str = "partial"


@dataclass(frozen=True)
class CsgMesh:
    body_id: str
    positions: tuple[float, ...]
    triangles: tuple[int, ...]
    edges: tuple[int, ...]
    volume_mm3: float
    area_mm2: float
    centroid_mm: tuple[float, float, float]
    solid_count: int
    linear_deflection_mm: float


def _fail(
    code: str,
    message: str,
    at: int | None = None,
    category: ErrorCategory = "unsupported",
) -> NoReturn:
    cls = {
        "unsupported": UnsupportedFormatError,
        "invalid": InvalidFormatError,
        "limit_exceeded": LimitExceededError,
    }[category]
    raise cls(Diagnostic(category, code, at, message))


def _word(b: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(b):
        _fail("csg.truncated", "Truncated CSG field", category="invalid")
    return int(struct.unpack_from("<I", b, at)[0])


def _program(
    b: bytes, at: int, limits: CsgLimits
) -> tuple[int, tuple[int, ...], ByteRange]:
    # Anchored metadata header and one tree. Never search for 0x79 signatures.
    if len(b) < 176 or _word(b, 0) != 0x21000000 or _word(b, 4) + 4 != len(b):
        _fail("csg.metadata", "Unqualified CSG metadata framing", at)
    if _word(b, 12) or _word(b, 16) not in (0xC9500088, 0xC9580088):
        _fail("csg.metadata", "Unqualified CSG metadata header", at)
    key = _word(b, 152)
    if key & 0xF0000000 != 0xC0000000 or not key & 0x0FFFFFFF or _word(b, 156):
        _fail("csg.tree", "Unqualified saved CSG tree identifier", at + 152)
    pos = 160
    attributes = _word(b, pos) == 0x7A0000D8
    if attributes:
        pos += 216  # Qualified length-framed saved attributes, retained opaque.
    if _word(b, 8) != (2 if attributes else 1) or pos + 16 > len(b):
        _fail("csg.metadata", "Unqualified CSG metadata entries", at)
    length = _word(b, pos) & 0xFFFFFF
    count = _word(b, pos + 8)
    if _word(b, pos) >> 24 != 0x79 or _word(b, pos + 4) or _word(b, pos + 12):
        _fail("csg.program_layout", "Unqualified CSG program header", at + pos)
    if length != 16 + 4 * count or pos + length != len(b):
        _fail(
            "csg.program_length",
            "CSG token count or extent is inconsistent",
            at + pos,
            "invalid",
        )
    if count > 4 * limits.max_operations + 1:
        _fail(
            "csg.limit_tokens",
            "CSG program exceeds operation/token limit",
            at + pos,
            "limit_exceeded",
        )
    tokens = tuple(struct.unpack_from(f"<{count}I", b, pos + 16))
    _validate_tokens(tokens, limits, at + pos + 16)
    return key & 0x0FFFFFFF, tokens, ByteRange(at + pos, at + len(b))


def _validate_tokens(tokens: tuple[int, ...], limits: CsgLimits, at: int = 0) -> None:
    depth = operations = 0
    references: set[int] = set()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token & 0xF0000000 == 0x80000000:
            depth += 1
            references.add(token)
        elif token in (1, 2, 3):
            if depth < 2:
                _fail(
                    "csg.stack",
                    "CSG operator has insufficient operands",
                    at + 4 * i,
                    "invalid",
                )
            depth -= 1
            operations += 1
        elif token == 0 and i + 1 < len(tokens) and tokens[i + 1] == 255:
            if depth != 1 or not i or tokens[i - 1] not in (1, 2, 3):
                _fail("csg.checkpoint", "Unqualified CSG checkpoint", at + 4 * i)
            i += 1
        else:
            _fail("csg.operation", f"Unqualified CSG operation 0x{token:x}", at + 4 * i)
        if (
            depth > limits.max_stack_depth
            or operations > limits.max_operations
            or len(references) > limits.max_operands
        ):
            _fail(
                "csg.limit_program",
                "CSG program exceeds configured limits",
                at + 4 * i,
                "limit_exceeded",
            )
        i += 1
    if depth != 1:
        _fail("csg.stack", "CSG program must leave exactly one result", at, "invalid")


def _operand(
    doc: Document,
    part: Part,
    entity_id: str,
    span: ByteRange,
    output_id: int,
    root_frame: tuple[tuple[float, ...], ...],
    implicit: bool,
) -> CsgOperand:
    b = doc.source_bytes(span)
    raw_type = b[15] if len(b) >= 16 else -1
    spec: tuple[int, int, str] | None
    if raw_type == 76 and len(b) >= 184 and (len(b) - 136) % 16 == 0 and b[27] == 0x59:
        # The profile layout carries its own length in the marker word.
        spec = (len(b), 0x59440000 | (len(b) - 24), "profile")
    else:
        spec = {
            75: (160, 0x56440088, "box"),
            71: (136, 0x50440070, "cylinder"),
            70: (224, 0x584400C8, "prism"),
        }.get(raw_type)
    if spec is None:
        _fail(
            "csg.operand_layout",
            "CSG operand is not a qualified box, cylinder, prism or profile extrusion",
            span.start,
        )
    assert spec is not None
    size, marker, kind = spec
    flags = _word(b, 12)
    expected = (0x100E1, 0x100A1) if implicit else (0x101E1, 0x101A1)
    if (
        len(b) != size
        or _word(b, 0) != size
        or not _word(b, 4)
        or _word(b, 8)
        or flags & 0xFFFFFF not in expected
        or _word(b, 16) & 0xF0000000 != 0x80000000
        or _word(b, 20)
        or _word(b, 24) != marker
        or _word(b, 32) not in (0, output_id)
        or _word(b, 40) & 0xFFFF != 0x180
        or _word(b, 44)
    ):
        _fail(
            "csg.operand_layout", "Unqualified component header or mirror", span.start
        )
    numeric = b[48 : size - 8] if kind == "profile" else b[48:]
    values = struct.unpack(f"<{len(numeric) // 8}d", numeric)
    frame = _frame(list(values[:9]))
    # A profile validates its own elements: the second value of a sweep element
    # is arbitrary in saved files.
    checked = values[:10] if kind == "profile" else values
    if frame is None or not all(math.isfinite(v) for v in checked):
        _fail(
            "csg.operand_frame",
            "Nonfinite or non-rigid component frame",
            span.start + 48,
            "invalid",
        )
    assert frame is not None
    world = _relative(root_frame, frame)
    if world is None:
        _fail(
            "csg.operand_frame",
            "Component global frame overflowed",
            span.start + 48,
            "invalid",
        )
    assert world is not None
    p = values[9:]
    if kind == "profile":
        return CsgOperand(_word(b, 16), entity_id, _profile(doc, world, span, b[48:]))
    if kind == "prism":
        return CsgOperand(_word(b, 16), entity_id, _prism(world, p, span, b[48:]))
    height = p[0] if kind == "box" else p[1]
    if height < 0:
        _fail(
            "csg.signed_height",
            "Negative CSG extrusion heights are not qualified",
            span.start + 120,
        )
    valid = height > 0 and (
        p[3] > p[1]
        and p[4] > p[2]
        and math.isfinite(p[3] - p[1])
        and math.isfinite(p[4] - p[2])
        if kind == "box"
        else p[0] > 0
    )
    if not valid:
        _fail(
            "csg.operand_dimensions",
            "Invalid component dimensions",
            span.start + 120,
            "invalid",
        )
    primitive = NativePrimitive(
        "box" if kind == "box" else "cylinder",
        world,
        height,
        None if kind == "box" else p[0],
        (p[1], p[3]) if kind == "box" else None,
        (p[2], p[4]) if kind == "box" else None,
        ByteRange(span.start + 48, span.end),
        b[48:],
    )
    return CsgOperand(_word(b, 16), entity_id, primitive)


def _prism(
    world: tuple[tuple[float, ...], ...],
    p: tuple[float, ...],
    span: ByteRange,
    raw: bytes,
) -> NativePrimitive:
    # Signed height along the frame Z axis, then six XY vertices. A negative
    # height moves the base instead of reversing an axis, so the frame stays
    # rigid and the saved profile is used unchanged.
    signed = p[0]
    points = tuple((p[i], p[i + 1]) for i in range(1, 13, 2))
    edges = [
        (b[0] - a[0], b[1] - a[1])
        for a, b in zip(points, points[1:] + points[:1], strict=True)
    ]
    turns = [
        math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])
        for u, v in zip(edges, edges[1:] + edges[:1], strict=True)
    ]
    if signed == 0 or any(math.hypot(*edge) == 0 for edge in edges):
        _fail(
            "csg.operand_dimensions",
            "Invalid component dimensions",
            span.start + 120,
            "invalid",
        )
    # One strictly convex turn around the profile, in either direction.
    if not (
        (all(t > 0 for t in turns) or all(t < 0 for t in turns))
        and abs(abs(sum(turns)) - math.tau) < 1e-9
    ):
        _fail(
            "csg.operand_layout",
            "Only convex six-vertex prism profiles are qualified",
            span.start + 128,
        )
    if signed < 0:
        world = _moved_base(world, signed, span)
    return NativePrimitive(
        "polygon_extrusion",
        world,
        abs(signed),
        None,
        None,
        None,
        ByteRange(span.start + 48, span.end),
        raw,
        profile_points=points,
    )


def _moved_base(
    world: tuple[tuple[float, ...], ...], signed: float, span: ByteRange
) -> tuple[tuple[float, ...], ...]:
    # A negative height moves the base along the frame axis instead of
    # reversing it: the frame stays rigid and the saved profile is unchanged.
    moved = tuple((*row[:3], row[3] + signed * row[2]) for row in world[:3]) + (
        world[3],
    )
    if not all(math.isfinite(row[3]) for row in moved):
        _fail(
            "csg.operand_frame",
            "Component global frame overflowed",
            span.start + 48,
            "invalid",
        )
    return moved


def _profile(
    doc: Document,
    world: tuple[tuple[float, ...], ...],
    span: ByteRange,
    raw: bytes,
) -> NativePrimitive:
    # The closed line/arc profile is validated by the same core decoder as a
    # standalone extrusion; only the component header differs.
    record = doc._handle.read_component(span.start, span.end)
    primitive = record["primitive"]
    if primitive is None:
        # The geometry diagnostic is the last one; a zero palette index only
        # concerns appearance, which the CSG result reports separately.
        issues = [d for d in record["diagnostics"] if d["code"] != "native.color"]
        issue = issues[-1] if issues else None
        invalid = record["geometry_status"] == "invalid"
        _fail(
            "csg.operand_dimensions" if invalid else "csg.operand_layout",
            issue["message"] if issue else "Unqualified profile extrusion component",
            issue["byte_offset"] if issue else span.start,
            "invalid" if invalid else "unsupported",
        )
    assert primitive is not None
    p = primitive["parameters"]
    signed = p[0]
    segments = tuple(
        ProfileSegment(
            "arc" if p[i + 6] else "line",
            (p[i], p[i + 1]),
            (p[i + 2], p[i + 3]),
            (p[i + 4], p[i + 5]) if p[i + 6] else None,
            p[i + 6] or None,
        )
        for i in range(1, len(p), 7)
    )
    if signed < 0:
        world = _moved_base(world, signed, span)
    return NativePrimitive(
        "profile_extrusion",
        world,
        abs(signed),
        None,
        None,
        None,
        ByteRange(span.start + 48, span.end),
        raw,
        profile=segments,
    )


def _read_csg(doc: Document, parts: PartIndex, limits: CsgLimits) -> CsgIndex:
    if (
        parts.profile is None
        or parts.profile.csg_layout != "v7_postfix"
        or parts.status.index != "complete"
        or parts.source_length_unit != "mm"
    ):
        d = Diagnostic(
            "unsupported",
            "csg.profile",
            12,
            "CSG requires a complete qualified V7L7 index and root frame",
        )
        return CsgIndex(doc.source_sha256, (), "unsupported", (d,))
    root = next(p for p in parts.parts if p.is_root)
    root_frame = _frame(list(struct.unpack("<9d", root.placement.raw_coordinate_bytes)))
    assert root_frame is not None
    bodies: list[CsgBody] = []
    owners = {p.part_id: p for p in parts.parts}

    def qualified_owner(owner_id: str) -> bool:
        seen: set[str] = set()
        while owner_id not in seen:
            seen.add(owner_id)
            owner = owners.get(owner_id)
            if (
                owner is None
                or owner.is_external
                or owner.is_mirror
                or owner.placement.world_transform is None
            ):
                return False
            if owner.is_root:
                return True
            if owner.parent_id is None:
                return False
            owner_id = owner.parent_id
        return False

    ordered = sorted(parts.parts, key=lambda p: p.byte_range.start)
    starts = [p.byte_range.start for p in ordered]
    metadata_by_owner: dict[str, list[ByteRange]] = defaultdict(list)
    for opaque in parts.opaque_ranges:
        if opaque.reason != "entity_metadata":
            continue
        i = bisect_right(starts, opaque.byte_range.start) - 1
        if i >= 0:
            owner = ordered[i]
            if (
                owner.entities
                and owner.byte_range.end <= opaque.byte_range.start
                and opaque.byte_range.end <= owner.entities[0].byte_range.start
            ):
                metadata_by_owner[owner.part_id].append(opaque.byte_range)
    for part in parts.parts:
        # A final type-85 marker, or a single leaf with an explicit saved tree.
        markers = [
            e
            for e in part.entities
            if e.raw_type == 85
            and _word(doc.source_bytes(e.byte_range), 12) & 0xFFFFFF
            not in (0x109E1, 0x109A1)
        ]
        metas = metadata_by_owner[part.part_id]
        if not markers and len(part.entities) == 1 and metas:
            markers = list(part.entities)
        for marker in markers:
            if len(bodies) >= limits.max_bodies:
                _fail(
                    "csg.limit_bodies",
                    "CSG body count exceeds max_bodies",
                    marker.byte_range.start,
                    "limit_exceeded",
                )
            at = marker.byte_range.start
            source_id = tree_id = None
            tokens: tuple[int, ...] = ()
            operands: tuple[CsgOperand, ...] = ()
            program_range = None
            raw_program = b""
            appearance = marker.appearance
            status: Status = "complete"
            diagnostics: tuple[Diagnostic, ...] = ()
            try:
                if not qualified_owner(part.part_id):
                    _fail(
                        "csg.owner",
                        "Unqualified CSG owner placement",
                        part.byte_range.start,
                    )
                if len(markers) != 1 or len(metas) != 1:
                    _fail(
                        "csg.owner",
                        "Only one explicitly framed CSG tree per owner is qualified",
                        at,
                    )
                meta = metas[0]
                raw_program = doc.source_bytes(meta)
                tree_id, tokens, program_range = _program(
                    raw_program, meta.start, limits
                )
                b = doc.source_bytes(marker.byte_range)
                source_id = _word(b, 16) if len(b) >= 20 else None
                implicit = marker.raw_type != 85
                if implicit:
                    if tokens != (source_id,):
                        _fail(
                            "csg.result_binding",
                            "Single-leaf result does not match its saved tree",
                            at,
                            "invalid",
                        )
                elif (
                    len(b) != 48
                    or _word(b, 0) != 48
                    or not _word(b, 4)
                    or _word(b, 8)
                    or _word(b, 12) & 0xFFFFFF not in (0x108C1, 0x10881)
                    or source_id is None
                    or source_id & 0xF0000000 != 0x80000000
                    or _word(b, 20)
                    or _word(b, 24) != 0xFD000018
                    or _word(b, 28) != 0x1000000
                    or _word(b, 32) != tree_id
                    or _word(b, 36)
                    or _word(b, 44) != (0x01800004 | (_word(b, 12) & 0x40))
                ):
                    _fail(
                        "csg.result_binding",
                        "Unqualified final-body marker or tree reference",
                        at,
                    )
                assert source_id is not None
                refs = {t for t in tokens if t & 0xF0000000 == 0x80000000}
                if not implicit and source_id in refs:
                    _fail(
                        "csg.cycle",
                        "Final CSG result cannot be its own operand",
                        at,
                        "invalid",
                    )
                sources = {}
                for e in part.entities:
                    raw = doc.source_bytes(e.byte_range)
                    if len(raw) < 20:
                        _fail(
                            "csg.owner",
                            "Unqualified record in CSG owner",
                            e.byte_range.start,
                        )
                    sid = _word(raw, 16)
                    if sid in sources:
                        _fail(
                            "csg.duplicate_id",
                            "Duplicate CSG entity source ID",
                            e.byte_range.start,
                            "invalid",
                        )
                    sources[sid] = e
                if set(sources) != refs | {source_id}:
                    _fail(
                        "csg.references",
                        "Missing, cross-owner or extra CSG operands",
                        at,
                    )
                if not implicit:
                    linked = [
                        sid
                        for sid in refs
                        if _word(doc.source_bytes(sources[sid].byte_range), 32)
                        == source_id
                    ]
                    if len(linked) != 1:
                        _fail(
                            "csg.result_binding",
                            "CSG component/result backlink is ambiguous",
                            at,
                        )
                operands = tuple(
                    _operand(
                        doc,
                        part,
                        sources[sid].entity_id,
                        sources[sid].byte_range,
                        source_id,
                        root_frame,
                        implicit,
                    )
                    for sid in sorted(refs)
                )
                if not implicit:
                    color = (b[41] & 15) | (b[40] & 16)
                    appearance = NativeAppearance(
                        color or None,
                        bool(_word(b, 12) & 0x40),
                        _word(b, 4),
                        "complete" if color else "unsupported",
                        marker.byte_range,
                        b,
                    )
            except LimitExceededError:
                raise
            except IcadError as exc:
                status = (
                    "invalid" if exc.diagnostic.category == "invalid" else "unsupported"
                )
                diagnostics = (exc.diagnostic,)
                operands = ()
            bodies.append(
                CsgBody(
                    marker.entity_id,
                    part.part_id,
                    source_id,
                    tree_id,
                    tokens,
                    operands,
                    marker.byte_range,
                    program_range,
                    raw_program,
                    appearance,
                    status,
                    diagnostics,
                )
            )
    status = "complete" if all(b.status == "complete" for b in bodies) else "partial"
    return CsgIndex(doc.source_sha256, tuple(bodies), status, ())


def read_csg(
    document: Document,
    *,
    part_limits: PartLimits | None = None,
    limits: CsgLimits | None = None,
) -> CsgIndex:
    """Read qualified programs and retain scoped failures without evaluating CAD."""
    from .document import Document

    if not isinstance(document, Document):
        raise TypeError("document must be an icadkit.Document")
    limits = CsgLimits() if limits is None else limits
    if not isinstance(limits, CsgLimits):
        raise TypeError("limits must be CsgLimits")
    return _read_csg(document, document.read_parts(limits=part_limits), limits)


def evaluate_csg(
    body: CsgBody,
    *,
    limits: CsgLimits | None = None,
    linear_deflection_mm: float = 0.05,
) -> CsgMesh:
    """Evaluate one nonempty solid result, with no repair or fuzzy booleans.

    The optional ``preview`` extra supplies OCCT. Limits bound program and output
    complexity; they are not a wall-clock or native-kernel memory guarantee.
    """
    if not isinstance(body, CsgBody):
        raise TypeError("body must be CsgBody")
    limits = CsgLimits() if limits is None else limits
    if not isinstance(limits, CsgLimits):
        raise TypeError("limits must be CsgLimits")
    if (
        isinstance(linear_deflection_mm, bool)
        or not isinstance(linear_deflection_mm, (int, float))
        or not math.isfinite(linear_deflection_mm)
        or linear_deflection_mm <= 0
    ):
        raise ValueError("linear_deflection_mm must be positive and finite")
    if body.status != "complete":
        _fail(
            "csg.incomplete",
            "Cannot evaluate an unqualified CSG program",
            body.byte_range.start,
        )
    _validate_tokens(body.tokens, limits)
    from ._csg_occt import evaluate

    return evaluate(body, limits, linear_deflection_mm)
