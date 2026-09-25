"""Saved parametric tables; no expression execution or geometry regeneration."""

import math
import struct
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .errors import LimitExceededError
from .models import ByteRange, Diagnostic, ErrorCategory, Status
from .parts import PartLimits

if TYPE_CHECKING:
    from .document import Document


@dataclass(frozen=True)
class ParameterLimits:
    """Limit value rows and total bytes of candidate parameter table records."""

    max_parameters: int = 100_000
    max_bytes: int = 16_777_216

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 < value <= (1 << 31) - 1:
                raise ValueError(f"{name} must be between 1 and 2**31 - 1")


@dataclass(frozen=True)
class ParameterTable:
    """An owned table with physical payload fragments and uninterpreted rows.

    ``raw_kind`` encodes a layout revision and table kind. Kinds 1, 2 and 3
    contain conditions, values and opaque geometry associations respectively.
    A row can span multiple fragments; concatenating ``payload_ranges``
    reproduces ``raw_payload`` exactly.
    """

    table_id: str
    owner_part_id: str
    source_id: int
    byte_range: ByteRange
    raw_kind: int
    count: int
    payload_ranges: tuple[ByteRange, ...]
    raw_payload: bytes


@dataclass(frozen=True)
class ParameterCondition:
    """A saved condition referring to one value row in the same owner."""

    parameter_index: int
    entity_source_id: int
    comment: str | None
    enabled: bool | None
    raw_flags: int
    raw_bytes: bytes
    byte_ranges: tuple[ByteRange, ...]


ParameterKind = Literal["number", "length", "diameter", "radius", "angle"]


@dataclass(frozen=True)
class SavedParameter:
    parameter_id: str
    owner_part_id: str
    table_id: str
    ordinal: int
    name: str | None
    kind: ParameterKind | None
    raw_type: int
    value: float | None
    equation: str | None
    explain: str | None
    comment: str | None
    enabled: bool | None
    raw_expression_state: int
    raw_values: tuple[float, ...]
    raw_bytes: bytes
    byte_ranges: tuple[ByteRange, ...]
    conditions: tuple[ParameterCondition, ...]
    status: Status
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class ParameterIndex:
    """Saved values in the qualified 3D part inventory of this file only.

    Completeness covers table framing and exposed fields, not evaluation,
    2D parameters, external documents or the opaque association table.
    ``enabled`` is a condition flag; ``status`` is decoder completeness.
    """

    source_sha256: str
    parameters: tuple[SavedParameter, ...]
    tables: tuple[ParameterTable, ...]
    status: Status
    diagnostics: tuple[Diagnostic, ...]
    opaque_ranges: tuple[ByteRange, ...]


def _word(data: bytes, at: int = 0) -> int:
    return int(struct.unpack_from("<I", data, at)[0])


def _spans(table: ParameterTable, at: int, length: int) -> tuple[ByteRange, ...]:
    """Map a logical row through the validated FD payload fragments."""
    result = []
    logical = 0
    for span in table.payload_ranges:
        size = span.end - span.start
        lo, hi = max(at, logical), min(at + length, logical + size)
        if lo < hi:
            result.append(
                ByteRange(span.start + lo - logical, span.start + hi - logical)
            )
        logical += size
    return tuple(result)


def _table(data: bytes, span: ByteRange, owner: str) -> ParameterTable | None:
    if (
        len(data) < 48
        or _word(data) != len(data)
        or _word(data, 4) == 0
        or _word(data, 8) != 0
        or _word(data, 12) & 0xFF00FFFF not in (0x4D002081, 0x4D000081)
        or _word(data, 16) & 0xF0000000 != 0x80000000
        or _word(data, 20) != 0
        or _word(data, 24) != 0xFD000010
        or _word(data, 28) != 0x01000000
    ):
        return None
    raw_kind, count = struct.unpack_from("<II", data, 32)
    revision, kind = raw_kind >> 8, raw_kind & 255
    if revision not in (0x100, 0x101, 0x102) or kind not in (1, 2, 3):
        return None
    stride = {1: 296 if revision == 0x102 else 160, 2: 376, 3: 16}[kind]
    fragments: list[bytes]
    ranges: list[ByteRange]
    at, fragments, ranges = 40, [], []
    while at < len(data):
        if len(fragments) + 1 >= (_word(data, 12) >> 16) & 255:
            return None
        if len(data) - at < 8:
            return None
        tag, subtype = struct.unpack_from("<II", data, at)
        length = tag & 0xFFFFFF
        if (
            tag >> 24 != 0xFD
            or subtype != 0x01000000
            or length < 8
            or length % 4
            or length > len(data) - at
        ):
            return None
        fragments.append(data[at + 8 : at + length])
        ranges.append(ByteRange(span.start + at + 8, span.start + at + length))
        at += length
    if (
        len(fragments) + 1 != (_word(data, 12) >> 16) & 255
        or sum(map(len, fragments)) != count * stride
    ):
        return None
    return ParameterTable(
        f"parameter-table:{span.start:x}",
        owner,
        _word(data, 16),
        span,
        raw_kind,
        count,
        tuple(ranges),
        b"".join(fragments),
    )


def _text(data: bytes) -> str | None:
    # Preserve every source byte, including producer padding, in the row.
    try:
        return data.split(b"\0", 1)[0].decode("cp932")
    except UnicodeDecodeError:
        return None


def _read_parameters(
    doc: "Document", limits: ParameterLimits | None, part_limits: PartLimits | None
) -> ParameterIndex:
    if limits is None:
        limits = ParameterLimits()
    elif not isinstance(limits, ParameterLimits):
        raise TypeError("limits must be a ParameterLimits instance")
    parts = doc.read_parts(limits=part_limits)
    diagnostics: list[Diagnostic] = []
    opaque: list[ByteRange] = []
    tables: list[ParameterTable] = []
    status: Status = parts.status.index
    if parts.status.index != "complete" or parts.status.hierarchy != "complete":
        diagnostics.extend(parts.diagnostics)
        opaque.extend(r.byte_range for r in parts.opaque_ranges)
        if status == "complete":
            status = parts.status.hierarchy
    consumed = 0
    count = 0
    for part in parts.parts:
        for entity in part.entities:
            if entity.raw_type != 77:
                continue
            span = entity.byte_range
            consumed += span.end - span.start
            if consumed > limits.max_bytes:
                raise LimitExceededError(
                    Diagnostic(
                        "limit_exceeded",
                        "limit.parameter_bytes",
                        span.start,
                        "parameter table records exceed max_bytes",
                    )
                )
            table = _table(doc.source_bytes(span), span, part.part_id)
            if table is None:
                opaque.append(span)
                diagnostics.append(
                    Diagnostic(
                        "unsupported",
                        "parameters.table_layout",
                        span.start,
                        "unqualified parameter table; source range retained",
                    )
                )
                if status == "complete":
                    status = "partial"
                continue
            if table.raw_kind & 255 == 2:
                count += table.count
                if count > limits.max_parameters:
                    raise LimitExceededError(
                        Diagnostic(
                            "limit_exceeded",
                            "limit.parameters",
                            span.start,
                            "saved parameter count exceeds max_parameters",
                        )
                    )
            tables.append(table)

    by_owner: dict[str, list[ParameterTable]] = defaultdict(list)
    for table in tables:
        by_owner[table.owner_part_id].append(table)
    parameters: list[SavedParameter] = []
    kinds: dict[int, ParameterKind] = {
        0: "number",
        1: "length",
        2: "diameter",
        3: "radius",
        4: "angle",
    }
    for owner, group in by_owner.items():
        if len({t.source_id for t in group}) != len(group):
            diagnostics.append(
                Diagnostic(
                    "invalid",
                    "parameters.duplicate_id",
                    group[0].byte_range.start,
                    "duplicate parameter table source ID within one owner",
                )
            )
            opaque.extend(t.byte_range for t in group)
            status = "invalid"
            continue
        values = [t for t in group if t.raw_kind & 255 == 2]
        conditions = [t for t in group if t.raw_kind & 255 == 1]
        # There is no observed way to disambiguate multiple value tables in
        # one owner. Never join unrelated tables by a coincident row number.
        if (
            len(values) != 1
            or len(conditions) != 1
            or (values[0].raw_kind >> 8 != conditions[0].raw_kind >> 8)
        ):
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "parameters.table_pair",
                    group[0].byte_range.start,
                    "requires one value table and one matching condition table "
                    "per owner",
                )
            )
            opaque.extend(t.byte_range for t in group)
            if status == "complete":
                status = "partial"
            continue
        value_table, condition_table = values[0], conditions[0]
        stride = 296 if condition_table.raw_kind >> 8 == 0x102 else 160
        linked: dict[int, list[ParameterCondition]] = defaultdict(list)
        for i in range(condition_table.count):
            row = condition_table.raw_payload[i * stride : (i + 1) * stride]
            index, flags = _word(row, 140), _word(row)
            ranges = _spans(condition_table, i * stride, stride)
            if index >= value_table.count:
                diagnostics.append(
                    Diagnostic(
                        "invalid",
                        "parameters.condition_index",
                        ranges[0].start,
                        "condition index is outside its owner's value table",
                    )
                )
                status = "invalid"
                opaque.extend(ranges)
                continue
            linked[index].append(
                ParameterCondition(
                    index,
                    _word(row, 132),
                    _text(row[4:132]),
                    not bool(flags & 0x100) if flags in (1, 0x101) else None,
                    flags,
                    row,
                    ranges,
                )
            )
        names: set[str] = set()
        for i in range(value_table.count):
            row = value_table.raw_payload[i * 376 : (i + 1) * 376]
            ranges = _spans(value_table, i * 376, 376)
            raw_type, expression_state = _word(row), _word(row, 68)
            raw_values = struct.unpack_from("<6d", row, 72)
            name, equation, explain = (
                _text(row[4:68]),
                _text(row[120:248]),
                _text(row[248:376]),
            )
            cs = tuple(linked.get(i, ()))
            comment = (
                cs[0].comment
                if len(cs) == 1
                else ""
                if not cs and raw_type == 0
                else None
            )
            enabled = (
                cs[0].enabled
                if len(cs) == 1
                else True
                if not cs and raw_type == 0
                else None
            )
            issues: list[tuple[ErrorCategory, str, str]] = []
            if raw_type not in kinds:
                issues.append(
                    ("unsupported", "parameters.type", "unknown saved parameter type")
                )
            if None in (name, equation, explain, comment):
                issues.append(
                    (
                        "unsupported",
                        "parameters.text",
                        "undecodable text or ambiguous condition comment",
                    )
                )
            if enabled is None:
                issues.append(
                    (
                        "unsupported",
                        "parameters.condition_state",
                        "unknown or ambiguous condition state",
                    )
                )
            if expression_state not in (0, 1, 255):
                issues.append(
                    (
                        "unsupported",
                        "parameters.expression_state",
                        "unqualified expression state; raw value retained",
                    )
                )
            if not all(math.isfinite(v) for v in raw_values):
                issues.append(
                    ("invalid", "parameters.value", "nonfinite saved parameter value")
                )
            if not name or name in names:
                issues.append(
                    (
                        "invalid",
                        "parameters.name",
                        "empty or duplicate parameter name in owner",
                    )
                )
            if name is not None:
                names.add(name)
            local = tuple(
                Diagnostic(category, code, ranges[0].start, message)
                for category, code, message in issues
            )
            local_status: Status = (
                "invalid"
                if any(d.category == "invalid" for d in local)
                else "partial"
                if local
                else "complete"
            )
            if local_status == "invalid":
                status = "invalid"
            elif local and status == "complete":
                status = "partial"
            parameters.append(
                SavedParameter(
                    f"parameter:{value_table.byte_range.start:x}:{i}",
                    owner,
                    value_table.table_id,
                    i,
                    name,
                    kinds.get(raw_type),
                    raw_type,
                    raw_values[3]
                    if raw_type in kinds and math.isfinite(raw_values[3])
                    else None,
                    equation,
                    explain,
                    comment,
                    enabled,
                    expression_state,
                    raw_values,
                    row,
                    ranges,
                    cs,
                    local_status,
                    local,
                )
            )
            diagnostics.extend(local)
    return ParameterIndex(
        doc.source_sha256,
        tuple(parameters),
        tuple(tables),
        status,
        tuple(diagnostics),
        tuple(opaque),
    )
