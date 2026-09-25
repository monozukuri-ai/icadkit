"""Bounded saved 2D geometry in view-local millimetres, separate from 3D parts.

No sheet placement, projection evaluation, font rendering or dimension
reconstruction is performed. Unsupported records retain their source ranges.
"""

import math
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .errors import LimitExceededError
from .models import ByteRange, Diagnostic, Status
from .views import View, ViewEntry, ViewIndex, ViewLimits

if TYPE_CHECKING:
    from .document import Document

Point2 = tuple[float, float]


@dataclass(frozen=True)
class DrawingLimits:
    max_entity_bytes: int = 4 * 1024 * 1024
    max_text_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 < value <= (1 << 31) - 1:
                raise ValueError(f"{name} must be between 1 and 2**31 - 1")


@dataclass(frozen=True)
class DrawingPrimitive:
    kind: Literal["point", "line", "circle", "arc"]
    points: tuple[Point2, ...] = ()
    direction: Point2 | None = None
    length: float | None = None
    center: Point2 | None = None
    radius: float | None = None
    start_angle: float | None = None
    sweep_angle: float | None = None


@dataclass(frozen=True)
class DrawingText:
    """Stored text runs only; anchors, fonts and layout are not qualified."""

    lines: tuple[str, ...]
    encoding: str
    layout_status: Literal["unsupported"] = "unsupported"


@dataclass(frozen=True)
class DrawingEntity:
    entity_id: str
    view_id: str
    byte_range: ByteRange
    owner_offset: int | None
    source_id: int | None
    raw_type: int | None
    layer: int | None
    visible: bool | None
    line_width: int | None
    line_style: int | None
    color_index: int | None
    primitive: DrawingPrimitive | None
    text: DrawingText | None
    status: Status
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class DrawingIndex:
    views: ViewIndex
    entities: tuple[DrawingEntity, ...]
    diagnostics: tuple[Diagnostic, ...]
    status: Status
    coordinate_space: Literal["view_local"] = "view_local"
    length_unit: Literal["mm"] = "mm"
    angle_unit: Literal["radian"] = "radian"


def _text(b: bytes, order: str, limits: DrawingLimits) -> DrawingText | None:
    # Only counted UTF-16LE runs are qualified. Legacy CP932/glyph encodings
    # retain their bytes without guessing from plausible strings.
    if order != "<" or len(b) < 88 or b[24:28] != b"\x40\0\0\xfd":
        return None
    lines: list[str] = []
    total = 0
    at = 88
    while at < len(b):
        if len(b) - at < 56:
            return None
        size = int.from_bytes(b[at : at + 2], "little")
        if size < 60 or size % 4 or size > len(b) - at:
            return None
        run = b[at : at + size]
        if run[2:4] != b"\x44\x20" or run[48:52] not in (
            b"\x20\x20\0\x01",
            b"\x20\0\0\x01",
        ):
            return None
        n = int.from_bytes(run[52:56], "little") * 2
        if n > size - 56:
            return None
        total += n
        if total > limits.max_text_bytes:
            raise LimitExceededError(
                Diagnostic(
                    "limit_exceeded",
                    "limit.drawing_text",
                    None,
                    "Text exceeds max_text_bytes",
                )
            )
        try:
            lines.append(run[56 : 56 + n].decode("utf-16-le", errors="strict"))
        except UnicodeDecodeError:
            return None
        at += size
    return DrawingText(tuple(lines), "utf-16-le") if lines else None


def _entity(
    document: "Document", view: View, entry: ViewEntry, limits: DrawingLimits
) -> DrawingEntity:
    at = entry.byte_range.start
    if entry.byte_range.length > limits.max_entity_bytes:
        raise LimitExceededError(
            Diagnostic(
                "limit_exceeded",
                "limit.drawing_entity",
                at,
                "Entity exceeds max_entity_bytes",
            )
        )
    b = document.source_bytes(entry.byte_range)
    order = "<" if document.header.byte_order == "little" else ">"

    def u(i: int) -> int:
        return int.from_bytes(b[i : i + 4], document.header.byte_order)

    source = u(16) if len(b) >= 24 else None
    t = b[15] if len(b) >= 24 else None
    layer = visible = width = style = color = None
    primitive = None
    text = None
    status: Status = "unsupported"
    code, message = "drawing.entity", "Unqualified 2D entity layout"
    if (
        len(b) >= 32
        and u(0) == len(b)
        and source is not None
        and source >> 28 == 8
        and b[8:12] == b"\0" * 4
        and b[20:24] == b"\0" * 4
        and b[5:8] == b"\0" * 3
    ):
        layer, visible = b[4], bool(b[12] & 0x40)
        if t == 21 and document.header.raw_version in (
            b"\0\x07\0\x06",
            b"\0\x07\0\x07",
            b"\0\x08\0\x01",
            b"\0\x08\0\x02",
            b"\0\x08\0\x03",
        ):
            text = _text(b, order, limits)
            if text:
                status = "partial"
                code, message = (
                    "drawing.text_layout",
                    "Text content only; layout is unqualified",
                )
        expected = {1: (56, 1), 2: (72, 2), 5: (72, 5), 6: (72, 4)}.get(t or -1)
        if (
            expected
            and (len(b), b[27]) == expected
            and int.from_bytes(b[24:26], document.header.byte_order) == len(b) - 24
            and b[26] in (0, 0x44)
            and b[30:32] == b"\0\0"
        ):
            width, style, color = b[28] & 0x7F, b[29] >> 4, b[29] & 15
            values = struct.unpack_from(order + ("2d" if t == 1 else "5d"), b, 32)
            if all(math.isfinite(v) for v in values):
                x, y = values[:2]
                if t == 1:
                    primitive = DrawingPrimitive("point", ((x, y),))
                elif t == 2:
                    dx, dy, length = values[2:]
                    if length >= 0 and math.isclose(
                        math.hypot(dx, dy), 1, abs_tol=1e-8
                    ):
                        end = (x + dx * length, y + dy * length)
                        if all(math.isfinite(v) for v in end):
                            primitive = DrawingPrimitive(
                                "line", ((x, y), end), (dx, dy), length
                            )
                else:
                    radius, start, sweep = values[2:]
                    if radius > 0 and 0 < abs(sweep) <= math.tau + 1e-8:
                        if t == 5:
                            primitive = DrawingPrimitive(
                                "arc",
                                center=(x, y),
                                radius=radius,
                                start_angle=start,
                                sweep_angle=sweep,
                            )
                        elif math.isclose(sweep, math.tau, abs_tol=1e-8) and start == 0:
                            primitive = DrawingPrimitive(
                                "circle", center=(x, y), radius=radius
                            )
                if primitive:
                    status = "complete"
                else:
                    status = "invalid"
            else:
                status = "invalid"
            if status == "invalid":
                code, message = (
                    "drawing.geometry",
                    "Nonfinite or invalid 2D geometry parameters",
                )
    diagnostics = (
        ()
        if status == "complete"
        else (
            Diagnostic(
                "invalid" if status == "invalid" else "unsupported", code, at, message
            ),
        )
    )
    return DrawingEntity(
        f"drawing@{at}",
        view.view_id,
        entry.byte_range,
        entry.owner_offset,
        source,
        t,
        layer,
        visible,
        width,
        style,
        color,
        primitive,
        text,
        status,
        diagnostics,
    )


def _read_drawing(
    document: "Document", limits: DrawingLimits | None, view_limits: ViewLimits | None
) -> DrawingIndex:
    if limits is None:
        limits = DrawingLimits()
    elif not isinstance(limits, DrawingLimits):
        raise TypeError("limits must be a DrawingLimits instance")
    views = document.read_views(limits=view_limits)
    entities = []
    diagnostics = list(views.diagnostics)
    statuses: list[Status] = [views.status]
    selected = 0
    for view in views.views:
        if view.kind not in ("2d_global", "2d_view", "registered_part"):
            continue
        selected += 1
        diagnostics.extend(view.diagnostics)
        statuses.append(view.status)
        for entry in view.entries:
            if entry.kind == "entity":
                entity = _entity(document, view, entry, limits)
                entities.append(entity)
                statuses.append(entity.status)
            else:
                statuses.append("unsupported")
                diagnostics.append(
                    Diagnostic(
                        "unsupported",
                        "drawing.metadata",
                        entry.byte_range.start,
                        "Non-entity view record retains opaque semantics",
                    )
                )
    status: Status = "complete"
    if "invalid" in statuses:
        status = "invalid"
    elif not selected:
        status = "unsupported"
        diagnostics.append(
            Diagnostic(
                "unsupported",
                "drawing.no_views",
                None,
                "No qualified 2D views; not an empty drawing",
            )
        )
    elif any(s != "complete" for s in statuses):
        status = "partial"
    return DrawingIndex(views, tuple(entities), tuple(diagnostics), status)
