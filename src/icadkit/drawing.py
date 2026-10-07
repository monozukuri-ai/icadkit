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


Extent = tuple[float, float, float, float]


@dataclass(frozen=True)
class DrawingText:
    """Stored text runs and, when qualified, the saved layout.

    The layout is qualified for the observed little-endian record: an 88-byte
    fixed part with constant words, one run per line with the line origin
    (the bottom-left of its first character cell, view-local millimetres),
    the character height, width and pitch, and the writing direction. The
    ``anchor`` is derived from ``base_point`` (1 top-left, 5 centre, 7
    bottom-left; columns 1/4/7, 2/5/8, 3/6/9, rows 1-3, 4-6, 7-9) and the
    cell-based ``box``, in which ASCII and half-width katakana take half a
    cell. Rotated text keeps its ``rotation`` (degrees) without an anchor or
    box. A stroke-glyph cache after the text is framed, never interpreted.
    """

    lines: tuple[str, ...]
    encoding: str
    layout_status: Status = "unsupported"
    base_point: int | None = None
    direction: Literal["horizontal", "vertical"] | None = None
    raw_way: int | None = None
    height: float | None = None
    char_width: float | None = None
    char_pitch: float | None = None
    rotation: float | None = None
    scale_factor: float | None = None
    line_origins: tuple[Point2, ...] = ()
    anchor: Point2 | None = None
    box: Extent | None = None
    glyph_cache: bool = False
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class DimensionItem:
    """One saved geometry item of a dimension record, in view-local mm.

    ``vectors`` items (markers ``0x0344`` and ``0x0300``) store a point and
    two vectors: ``points`` holds the point and the two points it reaches
    (an arrowhead corner, its tip and the other corner; or a measured
    point, its foot on the dimension line and the aux line end). ``line``
    items (``0x0244``, ``0x0200``) hold start and end, ``point`` items
    (``0x0100``) one point, and ``arc`` items (``0x0500``, ``0x0544``) the
    centre, the start point and the end point with ``radius``,
    ``start_angle`` and ``sweep_angle`` in radians. Appearance bytes are the
    saved width, style and colour.
    """

    kind: Literal["vectors", "line", "point", "arc"]
    raw_marker: int
    points: tuple[Point2, ...]
    line_width: int
    line_style: int
    color_index: int
    byte_range: ByteRange
    radius: float | None = None
    start_angle: float | None = None
    sweep_angle: float | None = None


@dataclass(frozen=True)
class DrawingDimension:
    """The saved layout of a dimension record; see ``docs/drawing.md``.

    Every little-endian dimension record is framed as attribute blocks, the
    embedded value text run and geometry items. The length layout
    (``kind="length"``) is interpreted: two arrowheads, the dimension line,
    the line point and the text anchor, two aux lines and the two measured
    points, with the measured ``value``, the text ``gap`` and the arrow
    parameters from the attribute blocks. The diameter family (record type
    27) is interpreted as ``diameter`` or ``radius`` by its value: the
    measured circle (``center``, ``radius``), the pick point, the dimension
    line, the line point and the two arrowheads. Angle records keep their
    items, arc and value with a partial status.
    """

    kind: Literal["length", "angle", "diameter", "radius", "unknown"]
    layout_status: Status
    items: tuple[DimensionItem, ...]
    text: DrawingText | None
    value: float | None = None
    scale_factor: float | None = None
    gap: float | None = None
    arrow_width: float | None = None
    arrow_angle: float | None = None
    underline: tuple[float, float] | None = None
    measured: tuple[Point2, Point2] | None = None
    line: tuple[Point2, Point2] | None = None
    line_point: Point2 | None = None
    extension: float | None = None
    aux_offset: float | None = None
    box: Extent | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    center: Point2 | None = None
    radius: float | None = None
    pick_point: Point2 | None = None


@dataclass(frozen=True)
class HatchEdge:
    """One boundary edge of a hatch: start, saved middle point and end.

    A ``line`` edge's middle point is the chord midpoint; an ``arc`` edge's
    middle point lies on the arc. Coordinates are view-local millimetres.
    """

    kind: Literal["line", "arc"]
    start: Point2
    middle: Point2
    end: Point2


@dataclass(frozen=True)
class DrawingHatch:
    """The saved layout of a hatch record; see ``docs/drawing.md``.

    The record stores an ``anchor``, the boundary as a point list relative
    to it (``raw_points``; interpreted as ``edges`` when it closes), and the
    rendered lines: ``origin``, a unit ``direction`` (``angle`` in degrees)
    and ``segments``. ``spacing`` is the perpendicular distance between
    consecutive lines when it is constant.
    """

    layout_status: Status
    anchor: Point2
    raw_points: tuple[Point2, ...]
    edges: tuple[HatchEdge, ...]
    origin: Point2
    direction: Point2
    angle: float
    spacing: float | None
    segments: tuple[tuple[Point2, Point2], ...]
    box: Extent | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


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
    dimension: DrawingDimension | None = None
    hatch: DrawingHatch | None = None


@dataclass(frozen=True)
class DrawingIndex:
    views: ViewIndex
    entities: tuple[DrawingEntity, ...]
    diagnostics: tuple[Diagnostic, ...]
    status: Status
    coordinate_space: Literal["view_local"] = "view_local"
    length_unit: Literal["mm"] = "mm"
    angle_unit: Literal["radian"] = "radian"


_TEXT_CONSTANTS = bytes.fromhex("400000fd000000013d00080000002800ff0901301800")
_TEXT_LINE_WORDS = {
    1: (2, bytes.fromhex("000000020000")),
    2: (3, bytes.fromhex("030100020002")),
}
_TEXT_ANGLES = {"horizontal": (0, 0), "vertical": (0x20000000, 0x60000000)}
_TEXT_DIRECTIONS: dict[int, Literal["horizontal", "vertical"]] = {
    1: "horizontal",
    2: "vertical",
}
_HALF_WIDTH = 0.5
_FULL_WIDTH = 1.0


def char_cells(ch: str) -> float:
    """Cells a character occupies: ASCII and half-width katakana take half."""
    code = ord(ch)
    return _HALF_WIDTH if code < 0x80 or 0xFF61 <= code <= 0xFF9F else _FULL_WIDTH


def text_box(
    lines: tuple[str, ...],
    origins: tuple[Point2, ...],
    height: float,
    char_width: float,
    char_pitch: float,
    direction: str,
) -> Extent:
    """Cell-based extent of unrotated text lines; see :class:`DrawingText`."""
    boxes = []
    for text, (x, y) in zip(lines, origins, strict=True):
        if direction == "vertical":
            boxes.append(
                (x, y + height - len(text) * char_pitch, x + char_width, y + height)
            )
        else:
            advance = sum(char_pitch * char_cells(c) for c in text[:-1])
            last = char_width * char_cells(text[-1]) if text else 0.0
            boxes.append((x, y, x + advance + last, y + height))
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def text_anchor(base_point: int, box: Extent) -> Point2:
    """Anchor of a base point on a box: left/centre/right, top/middle/bottom."""
    column = (base_point - 1) % 3
    row = (base_point - 1) // 3
    return (
        box[0] + (box[2] - box[0]) * column / 2,
        box[3] - (box[3] - box[1]) * row / 2,
    )


def _text(
    b: bytes, order: str, limits: DrawingLimits, at: int = 0
) -> DrawingText | None:
    # Only counted UTF-16LE runs are qualified. Legacy CP932/glyph encodings
    # retain their bytes without guessing from plausible strings.
    if order != "<" or len(b) < 88 or b[24:28] != b"\x40\0\0\xfd":
        return None
    lines: list[str] = []
    runs: list[bytes] = []
    total = 0
    position = 88
    while position < len(b):
        if len(b) - position < 56:
            return None
        size = int.from_bytes(b[position : position + 2], "little")
        if size < 60 or size % 4 or size > len(b) - position:
            return None
        run = b[position : position + size]
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
        runs.append(run)
        position += size
    if not lines:
        return None
    return _text_layout(b, tuple(lines), runs, at)


def _text_layout(
    b: bytes, lines: tuple[str, ...], runs: list[bytes], at: int
) -> DrawingText:
    issues: list[Diagnostic] = []

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    count = len(runs)
    expected = _TEXT_LINE_WORDS.get(count)
    if (
        b[24:46] != _TEXT_CONSTANTS
        or b[47] != 8
        or any(b[52:68])
        or b[68:72] != b"\x80\0\0\0"
        or b[46] != count
        or b[48] != count
        or expected is None
        or b[14] != expected[0]
        or b[74:80] != expected[1]
    ):
        issue(
            "drawing.text_fields",
            24,
            "Fixed text fields differ from the observed layout",
        )
    base_point = int(b[49]) if 1 <= b[49] <= 9 else None
    if base_point is None:
        issue("drawing.text_base_point", 49, "Unobserved text base point")
    direction = _TEXT_DIRECTIONS.get(b[50])
    if direction is None:
        issue("drawing.text_direction", 50, "Unobserved text direction")
    scale_factor: float | None = struct.unpack_from("<d", b, 80)[0]
    if scale_factor is None or not math.isfinite(scale_factor) or scale_factor <= 0:
        scale_factor = None
        issue("drawing.text_fields", 80, "Text scale factor is not a positive number")
    origins: list[Point2] = []
    metrics: list[tuple[float, float, float]] = []
    rotation: float | None = None
    glyph_cache = False
    position = 88
    for index, run in enumerate(runs):
        x, y = struct.unpack_from("<2d", run, 8)
        height, width, pitch = struct.unpack_from("<3f", run, 24)
        if (
            not all(math.isfinite(v) for v in (x, y, height, width, pitch))
            or min(height, width, pitch) <= 0
        ):
            issue(
                "drawing.text_metrics",
                position + 8,
                "Nonfinite or nonpositive text metrics",
            )
        origins.append((x, y))
        metrics.append((height, width, pitch))
        if index and (run[4:8] != runs[0][4:8]):
            issue("drawing.text_fields", position + 4, "Runs differ in appearance")
        w36, w40, w44 = struct.unpack_from("<IiI", run, 36)
        if direction is not None and (w36, w40) == _TEXT_ANGLES[direction] and w44 == 0:
            run_rotation = 0.0
        elif direction == "horizontal" and w36 == 0 and w44 == 0:
            run_rotation = w40 / (1 << 31) * 360.0
        else:
            run_rotation = None
            issue("drawing.text_fields", position + 36, "Unobserved text angle words")
        if index == 0:
            rotation = run_rotation
        elif run_rotation != rotation:
            issue("drawing.text_fields", position + 36, "Runs differ in rotation")
        n = int.from_bytes(run[52:56], "little")
        cache_at = 56 + 2 * n
        cache_at += -cache_at % 4
        tail = run[cache_at:]
        if any(tail):
            end = cache_at
            for _ in range(n):
                if len(run) - end < 2:
                    end = -1
                    break
                block = int.from_bytes(run[end : end + 2], "little")
                end += 2 + block
                if end > len(run):
                    end = -1
                    break
            if end < 0 or any(run[end:]) or len(run) - end >= 8:
                issue(
                    "drawing.text_glyphs",
                    position + cache_at,
                    "Unframed bytes after the text",
                )
            else:
                glyph_cache = True
        position += len(run)
    anchor: Point2 | None = None
    box: Extent | None = None
    if not issues and rotation not in (None, 0.0):
        issue(
            "drawing.text_rotation",
            88 + 40,
            "Rotated text: anchor and box are not derived",
        )
    elif not issues and direction is not None and base_point is not None:
        h0, w0, p0 = metrics[0]
        if any(m != metrics[0] for m in metrics):
            issue("drawing.text_metrics", 88 + 24, "Runs differ in character metrics")
        else:
            box = text_box(lines, tuple(origins), h0, w0, p0, direction)
            anchor = text_anchor(base_point, box)
    height, width, pitch = metrics[0] if metrics else (None, None, None)
    return DrawingText(
        lines,
        "utf-16-le",
        "partial" if issues else "complete",
        base_point,
        direction,
        int(b[51]),
        height,
        width,
        pitch,
        rotation,
        scale_factor,
        tuple(origins),
        anchor,
        box,
        glyph_cache,
        tuple(issues),
    )


_DIMENSION_KINDS: dict[int, Literal["length", "angle", "diameter"]] = {
    25: "length",
    26: "angle",
    27: "diameter",
}
_ITEM_KINDS: dict[int, Literal["vectors", "line", "point", "arc"]] = {
    0x0344: "vectors",
    0x0300: "vectors",
    0x0244: "line",
    0x0200: "line",
    0x0100: "point",
    0x0500: "arc",
    0x0544: "arc",
}
_ITEM_SIZES = {"vectors": 56, "line": 48, "point": 32, "arc": 48}
_DIAMETER_PATTERN = (
    "arc",
    "arc",
    "point",
    "vectors",
    "vectors",
    "line",
    "point",
    "point",
)
_LENGTH_PATTERN = (
    "vectors",
    "vectors",
    "line",
    "point",
    "point",
    "vectors",
    "vectors",
    "point",
    "point",
)


def _run_text(run: bytes, at: int) -> DrawingText | None:
    """A single saved text run (as embedded in dimensions) as DrawingText."""
    if (
        len(run) < 60
        or run[2:4] != b"\x44\x20"
        or run[48:52]
        not in (
            b"\x20\x20\0\x01",
            b"\x20\0\0\x01",
        )
    ):
        return None
    n = int.from_bytes(run[52:56], "little")
    if 56 + 2 * n > len(run):
        return None
    try:
        text = run[56 : 56 + 2 * n].decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    x, y = struct.unpack_from("<2d", run, 8)
    height, width, pitch = struct.unpack_from("<3f", run, 24)
    w36, w40, w44 = struct.unpack_from("<IiI", run, 36)
    issues: list[Diagnostic] = []
    if (
        not all(math.isfinite(v) for v in (x, y, height, width, pitch))
        or min(height, width, pitch) <= 0
    ):
        issues.append(
            Diagnostic(
                "unsupported", "drawing.text_metrics", at + 8, "Nonfinite text metrics"
            )
        )
    if w36 or w44:
        issues.append(
            Diagnostic(
                "unsupported", "drawing.text_fields", at + 36, "Unobserved angle words"
            )
        )
    rotation = w40 / (1 << 31) * 360.0
    box = (
        text_box((text,), ((x, y),), height, width, pitch, "horizontal")
        if not issues and rotation == 0.0
        else None
    )
    return DrawingText(
        (text,),
        "utf-16-le",
        "partial" if issues else "complete",
        None,
        "horizontal",
        None,
        height,
        width,
        pitch,
        rotation,
        None,
        ((x, y),),
        None,
        box,
        any(run[56 + 2 * n + (-(56 + 2 * n) % 4) :]),
        tuple(issues),
    )


def _dimension(b: bytes, order: str, at: int) -> DrawingDimension | None:
    if order != "<" or len(b) < 48 or b[27] != 0xFD or b[28:32] != b"\0\0\0\x01":
        return None
    issues: list[Diagnostic] = []

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    kind: Literal["length", "angle", "diameter", "radius", "unknown"] = (
        _DIMENSION_KINDS.get(b[15]) or "unknown"
    )
    blocks: dict[int, tuple[int, bytes]] = {}
    position = 40
    while position + 4 <= len(b) and b[position] == 0xFF:
        length = b[position + 3]
        if length < 4 or position + length > len(b):
            return None
        blocks.setdefault(b[position + 1], (position, b[position : position + length]))
        position += length
    # Elements: the value text run and the geometry items, in saved order.
    # The length layout stores the run first, the diameter family after its
    # circle items.
    items: list[DimensionItem] = []
    text: DrawingText | None = None
    while position < len(b):
        if len(b) - position < 8:
            issue("drawing.dimension_item", position, "Truncated dimension item")
            break
        size = int.from_bytes(b[position : position + 2], "little")
        marker = int.from_bytes(b[position + 2 : position + 4], "little")
        if marker == 0x2044:
            if size < 60 or size % 4 or position + size > len(b):
                return None
            run = _run_text(b[position : position + size], at + position)
            if run is None:
                return None
            if text is None:
                text = run
            else:
                issue("drawing.dimension_runs", position, "Additional value text run")
            position += size
            continue
        item_kind = _ITEM_KINDS.get(marker)
        expected = _ITEM_SIZES.get(item_kind or "", 0)
        if item_kind is None or size != expected or position + size > len(b):
            issue("drawing.dimension_item", position, "Unobserved dimension item")
            break
        values = struct.unpack_from(
            "<" + "d" * ((size - (16 if item_kind == "point" else 8)) // 8),
            b,
            position + 8,
        )
        if not all(math.isfinite(v) for v in values):
            issue("drawing.dimension_item", position + 8, "Nonfinite dimension item")
            break
        radius = start = sweep = None
        if item_kind == "vectors":
            px, py, ax, ay, bx, by = values
            points: tuple[Point2, ...] = (
                (px, py),
                (px + ax, py + ay),
                (px + bx, py + by),
            )
        elif item_kind == "line":
            x, y, dx, dy, length = values
            points = ((x, y), (x + dx * length, y + dy * length))
        elif item_kind == "arc":
            cx, cy, radius, start, sweep = values
            points = (
                (cx, cy),
                (cx + radius * math.cos(start), cy + radius * math.sin(start)),
                (
                    cx + radius * math.cos(start + sweep),
                    cy + radius * math.sin(start + sweep),
                ),
            )
        else:
            points = ((values[0], values[1]),)
        items.append(
            DimensionItem(
                item_kind,
                marker,
                points,
                b[position + 4] & 0x7F,
                b[position + 5] >> 4,
                b[position + 5] & 15,
                ByteRange(at + position, at + position + size),
                radius,
                start,
                sweep,
            )
        )
        position += size
    if text is None:
        return None
    value = scale = gap = arrow_width = arrow_angle = None
    underline: tuple[float, float] | None = None
    if 6 in blocks and len(blocks[6][1]) >= 56:
        value = struct.unpack_from("<d", blocks[6][1], 48)[0]
    if 3 in blocks and len(blocks[3][1]) >= 48:
        gap, scale = struct.unpack_from("<2d", blocks[3][1], 32)
    if 4 in blocks and len(blocks[4][1]) >= 48:
        arrow_width, _, angle = struct.unpack_from("<3d", blocks[4][1], 24)
        arrow_angle = math.degrees(angle)
    if 1 in blocks and len(blocks[1][1]) >= 40:
        first, second = struct.unpack_from("<2d", blocks[1][1], 24)
        underline = (first, second)
    elif 16 in blocks and len(blocks[16][1]) >= 32:
        first, second = struct.unpack_from("<2d", blocks[16][1], 16)
        underline = (first, second)
    for name, number in (("value", value), ("gap", gap), ("scale", scale)):
        if number is not None and not math.isfinite(number):
            issue("drawing.dimension_fields", 40, f"Nonfinite dimension {name}")
    measured = line = line_point = None
    extension = aux_offset = None
    center = pick = None
    radius = None
    pattern = tuple(i.kind for i in items)
    broken = any(d.code == "drawing.dimension_item" for d in issues)
    arcs = [i for i in items if i.kind == "arc"]
    if arcs:
        center, radius = arcs[-1].points[0], arcs[-1].radius
    if kind == "length":
        if broken:
            pass  # The item diagnostic already explains the incomplete sequence.
        elif pattern != _LENGTH_PATTERN:
            issue(
                "drawing.dimension_layout",
                40,
                "Unobserved length dimension item sequence",
            )
        else:
            line = (items[2].points[0], items[2].points[1])
            line_point = items[3].points[0]
            measured = (items[7].points[0], items[8].points[0])
            aux = items[5]
            foot = math.dist(aux.points[0], aux.points[1])
            end = math.dist(aux.points[0], aux.points[2])
            extension = end - foot if foot > 0 else None
            aux_offset = math.dist(aux.points[0], items[7].points[0])
    elif kind == "diameter":
        if (
            radius is not None
            and value is not None
            and math.isfinite(value)
            and abs(value - radius) <= 1e-6 * max(1.0, radius)
        ):
            kind = "radius"
        elif radius is not None and (
            value is None
            or not math.isfinite(value)
            or abs(value - 2 * radius) > 1e-6 * max(1.0, radius)
        ):
            issue(
                "drawing.dimension_value",
                40,
                "The value is neither the radius nor the diameter of the arc",
            )
        if broken:
            pass
        elif pattern != _DIAMETER_PATTERN:
            issue(
                "drawing.dimension_layout",
                40,
                "Unobserved diameter dimension item sequence",
            )
        else:
            pick = items[2].points[0]
            line = (items[5].points[0], items[5].points[1])
            line_point = items[6].points[0]
    else:
        issue(
            "drawing.dimension_kind",
            15,
            "Only the length and diameter layouts are interpreted",
        )
    box: Extent | None = None
    if items:
        xs = [p[0] for item in items for p in item.points]
        ys = [p[1] for item in items for p in item.points]
        for item in items:
            if item.kind == "arc" and item.radius is not None:
                assert item.start_angle is not None and item.sweep_angle is not None
                low, high = sorted(
                    (item.start_angle, item.start_angle + item.sweep_angle)
                )
                k = math.ceil(low / (math.pi / 2))
                while k * math.pi / 2 <= high + 1e-12:
                    angle = k * math.pi / 2
                    xs.append(item.points[0][0] + item.radius * math.cos(angle))
                    ys.append(item.points[0][1] + item.radius * math.sin(angle))
                    k += 1
        box = (min(xs), min(ys), max(xs), max(ys))
        if text.box is not None:
            box = (
                min(box[0], text.box[0]),
                min(box[1], text.box[1]),
                max(box[2], text.box[2]),
                max(box[3], text.box[3]),
            )
    if text.layout_status != "complete":
        issue("drawing.dimension_text", 40, "The value text run is not fully qualified")
    return DrawingDimension(
        kind,
        "partial" if issues else "complete",
        tuple(items),
        text,
        value,
        scale,
        gap,
        arrow_width,
        arrow_angle,
        underline,
        measured,
        line,
        line_point,
        extension,
        aux_offset,
        box,
        tuple(issues),
        center,
        radius,
        pick,
    )


_HATCH_MARKER = 0x0944


def _hatch(b: bytes, order: str, at: int) -> DrawingHatch | None:
    if order != "<" or len(b) < 56 or b[15] != 92:
        return None
    issues: list[Diagnostic] = []

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    length = struct.unpack_from("<H", b, 24)[0]
    if length < 24 or (length - 24) % 16 or 48 + (length - 24) > len(b) - 8:
        return None
    count = (length - 24) // 16
    anchor = struct.unpack_from("<2d", b, 32)
    points = tuple(struct.unpack_from("<2d", b, 48 + 16 * i) for i in range(count))
    position = 24 + length
    size, marker = struct.unpack_from("<HH", b, position)
    if marker != _HATCH_MARKER or size < 8 + 7 * 8 or position + size != len(b):
        return None
    values = struct.unpack_from(f"<{(size - 8) // 8}d", b, position + 8)
    if not all(math.isfinite(v) for v in values) or not all(
        math.isfinite(v) for p in points + (anchor,) for v in p
    ):
        issue("drawing.hatch_fields", position + 8, "Nonfinite hatch values")
        values = tuple(0.0 for _ in values)
    ox, oy, dx, dy, z1, z2 = values[:6]
    rest = values[6:]
    if (
        not math.isclose(math.hypot(dx, dy), 1.0, abs_tol=1e-9)
        or z1 != 0
        or z2 != 0
        or (len(rest) - 1) % 3
    ):
        issue("drawing.hatch_fields", position + 24, "Unobserved hatch item words")
    starts = [(0.0, 0.0)] + [(rest[i], rest[i + 1]) for i in range(1, len(rest) - 1, 3)]
    lengths = [rest[i] for i in range(0, len(rest), 3)]
    if any(v <= 0 for v in lengths):
        issue("drawing.hatch_fields", position + 56, "Nonpositive hatch line length")
    segments = tuple(
        ((ox + sx, oy + sy), (ox + sx + dx * run, oy + sy + dy * run))
        for (sx, sy), run in zip(starts, lengths, strict=False)
    )
    spacing: float | None = None
    offsets = [sx * -dy + sy * dx for sx, sy in starts]
    steps = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]
    positive = [v for v in steps if v > 1e-9]
    if positive:
        unit = min(positive)
        if all(
            abs(v) < 1e-9 or math.isclose(v / unit, round(v / unit), abs_tol=1e-6)
            for v in steps
        ):
            spacing = unit
    edges: list[HatchEdge] = []
    if count and count % 2 == 0 and points[-1] == (0.0, 0.0):
        start = anchor
        for i in range(0, count, 2):
            middle = (anchor[0] + points[i][0], anchor[1] + points[i][1])
            end = (anchor[0] + points[i + 1][0], anchor[1] + points[i + 1][1])
            chord = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            straight = math.dist(middle, chord) <= 1e-6 * (1 + math.dist(start, end))
            edges.append(HatchEdge("line" if straight else "arc", start, middle, end))
            start = end
    else:
        issue(
            "drawing.hatch_boundary",
            48,
            "The saved point list is not a closed edge list",
        )
    xs = [p[0] for seg in segments for p in seg] + [e.end[0] for e in edges]
    ys = [p[1] for seg in segments for p in seg] + [e.end[1] for e in edges]
    box = (min(xs), min(ys), max(xs), max(ys)) if xs else None
    return DrawingHatch(
        "partial" if issues else "complete",
        anchor,
        points,
        tuple(edges),
        (ox, oy),
        (dx, dy),
        math.degrees(math.atan2(dy, dx)),
        spacing,
        segments,
        box,
        tuple(issues),
    )


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
    dimension = None
    hatch = None
    status: Status = "unsupported"
    code, message = "drawing.entity", "Unqualified 2D entity layout"
    if (
        len(b) >= 32
        and u(0) == len(b)
        and source is not None
        and source >> 28 == 8
        and b[8:12] == b"\0" * 4
        and b[20:24] == b"\0" * 4
        and (b[5:8] == b"\0" * 3 or (t == 92 and b[5:8] == b"\x01\0\0"))
    ):
        layer, visible = b[4], bool(b[12] & 0x40)
        if t == 21 and document.header.raw_version in (
            b"\0\x07\0\x06",
            b"\0\x07\0\x07",
            b"\0\x08\0\x01",
            b"\0\x08\0\x02",
            b"\0\x08\0\x03",
        ):
            text = _text(b, order, limits, at)
            if text:
                width, style, color = b[92] & 0x7F, b[93] >> 4, b[93] & 15
                if text.layout_status == "complete":
                    status = "complete"
                else:
                    status = "partial"
                    code, message = (
                        "drawing.text_layout",
                        "Text layout is partially qualified; see the text diagnostics",
                    )
        dimension = None
        if t == 92 and document.header.raw_version in (
            b"\0\x07\0\x06",
            b"\0\x07\0\x07",
            b"\0\x08\0\x01",
            b"\0\x08\0\x02",
            b"\0\x08\0\x03",
        ):
            hatch = _hatch(b, order, at)
            if hatch is not None:
                width, style, color = b[28] & 0x7F, b[29] >> 4, b[29] & 15
                if hatch.layout_status == "complete":
                    status = "complete"
                else:
                    status = "partial"
                    code, message = (
                        "drawing.hatch_layout",
                        "Hatch layout is partially qualified; see its diagnostics",
                    )
        if t in _DIMENSION_KINDS and document.header.raw_version in (
            b"\0\x07\0\x06",
            b"\0\x07\0\x07",
            b"\0\x08\0\x01",
            b"\0\x08\0\x02",
            b"\0\x08\0\x03",
        ):
            dimension = _dimension(b, order, at)
            if dimension is not None:
                first = dimension.items[0] if dimension.items else None
                if first is not None:
                    width, style, color = (
                        first.line_width,
                        first.line_style,
                        first.color_index,
                    )
                if dimension.layout_status == "complete":
                    status = "complete"
                else:
                    status = "partial"
                    code, message = (
                        "drawing.dimension_layout",
                        "Dimension layout is partially qualified; see its diagnostics",
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
        (
            ()
            if status == "complete"
            else (
                Diagnostic(
                    "invalid" if status == "invalid" else "unsupported",
                    code,
                    at,
                    message,
                ),
            )
        )
        + (text.diagnostics if text is not None else ())
        + (dimension.diagnostics if dimension is not None else ())
        + (hatch.diagnostics if hatch is not None else ())
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
        dimension,
        hatch,
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
