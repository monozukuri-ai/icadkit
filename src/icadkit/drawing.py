"""Bounded saved 2D geometry in view-local millimetres, separate from 3D parts.

No sheet placement, projection evaluation, font rendering or dimension
reconstruction is performed. Unsupported records retain their source ranges.
"""

import math
import struct
from dataclasses import dataclass, replace
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
    """A saved point, line, circle, arc, ellipse or elliptical arc.

    For ``ellipse`` and ``elliptical_arc`` (record types 88 and 89)
    ``radius`` is the semi-axis along the ellipse's own x axis,
    ``minor_radius`` the semi-axis along its y axis and ``rotation`` the
    angle of that x axis in radians; ``start_angle`` and ``sweep_angle``
    are parametric angles on the unrotated ellipse (the DXF convention), so
    a point is ``center + R(rotation) * (radius*cos t, minor_radius*sin t)``.
    ``vectors`` (record type 12) stores a point and two segments from it
    (``points``: the point and the two ends).
    """

    kind: Literal[
        "point", "line", "circle", "arc", "ellipse", "elliptical_arc", "vectors"
    ]
    points: tuple[Point2, ...] = ()
    direction: Point2 | None = None
    length: float | None = None
    center: Point2 | None = None
    radius: float | None = None
    start_angle: float | None = None
    sweep_angle: float | None = None
    minor_radius: float | None = None
    rotation: float | None = None


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
    Underlined view labels ("A-A") append one line item after their runs;
    ``underline`` holds its start and end.
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
    underline: tuple[Point2, Point2] | None = None


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
    line, the line point and the two arrowheads; its leader variant (an
    arrowhead on the circle, a leader to an elbow and a shelf) is
    interpreted too, with ``leader`` holding the tip, the elbow and the end.
    The angle layout (record type 26, ``kind="angle"``) is interpreted as
    the vertex (``center``), the drawn dimension arc (``radius``,
    ``arc_start``, ``arc_sweep`` in radians), the two ``measured`` points on
    the legs and the ``value`` in degrees. The chamfer layout (type 29,
    ``kind="chamfer"``) is the length layout with one hidden arrowhead and a
    trailing hidden line; its ``value`` is the chamfer size of the text
    (``C1``) and the ``measured`` points coincide. Unobserved sequences keep
    their items with a partial status.
    """

    kind: Literal["length", "angle", "diameter", "radius", "chamfer", "unknown"]
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
    arc_start: float | None = None
    arc_sweep: float | None = None
    leader: tuple[Point2, ...] | None = None


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
class DrawingCurve:
    """The saved layout of a free curve record (type 16); see ``docs/drawing.md``.

    ``knots`` are the saved points of the curve in view-local millimetres
    (the start point and the cumulative offsets that follow it),
    ``chord_lengths`` the saved distance between consecutive knots (their
    sum is ``length``) and ``extent`` the saved bounding box of the smooth
    curve. Each segment also stores six doubles that describe the curve
    between two knots; they are retained in ``segment_words`` without an
    assigned meaning, so a caller that needs the curve draws through the
    knots. ``box`` is the extent of the knots and the saved extent.
    """

    layout_status: Status
    knots: tuple[Point2, ...]
    chord_lengths: tuple[float, ...]
    length: float
    extent: Extent | None
    segment_words: tuple[tuple[float, ...], ...]
    box: Extent | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class DrawingNote:
    """The saved layout of a leader note record (type 22).

    The record frames attribute blocks, the text runs (``texts``, one per
    line; ``text`` is the first) and ``vectors`` items: the arrowhead
    (``arrow``: a corner, the tip and the other corner), the drawn leader
    (``path``: the tip, the elbow and the end of the shelf under the text)
    and hidden alternatives that are not drawn. Balloons (the older type 34)
    add the drawn ``circle`` (centre, radius) around the text. ``arrow_width``
    and ``arrow_angle`` (degrees) come from the attribute block shared with
    dimensions.
    """

    layout_status: Status
    text: DrawingText | None
    items: tuple[DimensionItem, ...]
    arrow: tuple[Point2, Point2, Point2] | None
    path: tuple[Point2, ...]
    arrow_width: float | None = None
    arrow_angle: float | None = None
    box: Extent | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    texts: tuple[DrawingText, ...] = ()
    circle: tuple[Point2, float] | None = None


@dataclass(frozen=True)
class DrawingSymbol:
    """The saved layout of a symbol record (type 212).

    A symbol groups ``point``, ``line``, ``arc`` and ``vectors`` items with
    text runs (``texts``) and, in some records, a nested size-framed member
    list that is retained as ``nested`` ranges without interpretation.
    ``raw_kind`` is the saved byte at ``+31`` (1 for point markers, 50 for
    labels in the local corpus). Line-information records (type 224: the
    P&ID and circuit lines, junction marks, symbol instances) read the same
    way with their saved ``name``.
    """

    layout_status: Status
    items: tuple[DimensionItem, ...]
    texts: tuple[DrawingText, ...]
    nested: tuple[ByteRange, ...]
    box: Extent | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    raw_kind: int | None = None
    name: str | None = None


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
    curve: DrawingCurve | None = None
    note: DrawingNote | None = None
    symbol: DrawingSymbol | None = None


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
# Underlined view labels: one run and one line item (+46 counts both, +60
# carries 0x40 and the header byte +14 is 3), observed for one line only
_TEXT_UNDERLINED_WORDS = {1: (3, bytes.fromhex("000003010000"))}
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
    # Counted runs only: UTF-16LE in little-endian V7L6 and later, legacy CP932
    # in big-endian documents and little-endian V7L2-V7L5 (see _run_chars).
    byteorder: Literal["little", "big"] = "little" if order == "<" else "big"
    if len(b) < 88 or b[26:28] != b"\0\xfd" or b[40:43] != b"\xff\x09\x01":
        return None
    # The attribute block is 48 bytes; some V8 labels (line information,
    # P&ID) store an 88-byte block. The word at +24 is 16 + the block length
    # and the runs follow the block.
    block_length = b[43]
    if block_length not in (48, 88):
        return None
    if int.from_bytes(b[24:26], byteorder) != 16 + block_length:
        return None
    lines: list[str] = []
    runs: list[bytes] = []
    ends: list[int] = []
    encoding = "utf-16-le"
    total = 0
    position = 40 + block_length
    if len(b) < position:
        return None
    underline: tuple[Point2, Point2] | None = None
    while position < len(b):
        if len(b) - position < 56:
            if lines:
                # Underlined view labels ("A-A", "Z矢視図") append one 48-byte
                # line item after their runs; it is read like a dimension item
                underline = _underline(b, order, at, position)
                if underline is not None:
                    break
            return None
        size = int.from_bytes(b[position : position + 2], byteorder)
        if size < 60 or size % 4 or size > len(b) - position:
            return None
        run = b[position : position + size]
        decoded = _run_chars(run, byteorder)
        if decoded is None:
            if lines and position + size == len(b):
                # View labels and update marks append one attribute chunk
                # framed like a run; it is retained without interpretation.
                break
            return None
        text, encoding, end = decoded
        total += end - 56
        if total > limits.max_text_bytes:
            raise LimitExceededError(
                Diagnostic(
                    "limit_exceeded",
                    "limit.drawing_text",
                    None,
                    "Text exceeds max_text_bytes",
                )
            )
        lines.append(text)
        runs.append(run)
        ends.append(end)
        position += size
    if not lines:
        return None
    layout = _text_layout(
        b, tuple(lines), runs, ends, at, order, encoding, underline is not None
    )
    if underline is not None:
        layout = replace(layout, underline=underline)
    return layout


def _underline(
    b: bytes, order: str, at: int, position: int
) -> tuple[Point2, Point2] | None:
    """The one line item that underlined view labels append after their runs."""
    items, runs, nested, issues, end = _items(b, order, at, position, prefix="text")
    if issues or runs or nested or end != len(b) or len(items) != 1:
        return None
    item = items[0]
    if item.kind != "line" or item.points[0] == item.points[1]:
        return None
    return (item.points[0], item.points[1])


def _text_constants(order: str) -> bytes:
    """The fixed words +24..+46 of a text record in the file byte order: the
    16-bit words at +24 and +32..+40 swap, the bytes from +40 do not."""
    if order == "<":
        return _TEXT_CONSTANTS
    c = _TEXT_CONSTANTS
    swapped = bytearray(c)
    for at in (0, 8, 10, 12, 14):
        swapped[at], swapped[at + 1] = c[at + 1], c[at]
    return bytes(swapped)


def _text_layout(
    b: bytes,
    lines: tuple[str, ...],
    runs: list[bytes],
    ends: list[int],
    at: int,
    order: str = "<",
    encoding: str = "utf-16-le",
    underlined: bool = False,
) -> DrawingText:
    issues: list[Diagnostic] = []

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    count = len(runs)
    items = count + (1 if underlined else 0)
    expected = (_TEXT_UNDERLINED_WORDS if underlined else _TEXT_LINE_WORDS).get(count)
    block_end = 40 + b[43]
    if block_end == 88:
        if (
            b[24:46] != _text_constants(order)
            or b[47] != 8
            or any(b[52:60] + b[61:68])
            or b[60] != (0x40 if underlined else 0)
            or b[68:72] != b"\x80\0\0\0"
            or b[46] != items
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
    elif b[46] != count or b[48] != count or expected is None or b[14] != expected[0]:
        issue(
            "drawing.text_fields",
            24,
            "Fixed text fields of the extended attribute block differ",
        )
    base_point = int(b[49]) if 1 <= b[49] <= 9 else None
    if base_point is None:
        issue("drawing.text_base_point", 49, "Unobserved text base point")
    direction = _TEXT_DIRECTIONS.get(b[50])
    if direction is None:
        issue("drawing.text_direction", 50, "Unobserved text direction")
    scale_factor: float | None = struct.unpack_from(order + "d", b, block_end - 8)[0]
    if scale_factor is None or not math.isfinite(scale_factor) or scale_factor <= 0:
        scale_factor = None
        issue("drawing.text_fields", 80, "Text scale factor is not a positive number")
    origins: list[Point2] = []
    metrics: list[tuple[float, float, float]] = []
    rotation: float | None = None
    glyph_cache = False
    position = block_end
    for index, run in enumerate(runs):
        x, y = struct.unpack_from(order + "2d", run, 8)
        height, width, pitch = struct.unpack_from(order + "3f", run, 24)
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
        w36, w40, w44 = struct.unpack_from(order + "IiI", run, 36)
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
        n = int.from_bytes(run[52:56], "little" if order == "<" else "big")
        cache_at = ends[index]
        cache_at += -cache_at % 4
        tail = run[cache_at:]
        if encoding == "cp932":
            # The stroke-glyph cache of legacy runs is not framed by a size word
            # per character; it is retained without interpretation.
            glyph_cache = glyph_cache or any(tail)
        elif any(tail):
            end = cache_at
            for _ in range(n):
                if len(run) - end < 2:
                    end = -1
                    break
                block = int.from_bytes(
                    run[end : end + 2], "little" if order == "<" else "big"
                )
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
        encoding,
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


# Little-endian versions whose composite records (text runs in UTF-16LE) are qualified.
_COMPOSITE_VERSIONS = (
    b"\0\x07\0\x06",
    b"\0\x07\0\x07",
    b"\0\x08\0\x01",
    b"\0\x08\0\x02",
    b"\0\x08\0\x03",
)
# Records that carry attributes rather than drawing geometry: line information
# (224) whose item stream could not be framed, motion verification (230-234).
_ATTRIBUTE_TYPES = frozenset({211, 224, 230, 231, 232, 233, 234})
_DIMENSION_KINDS: dict[int, Literal["length", "angle", "diameter", "chamfer"]] = {
    25: "length",
    26: "angle",
    27: "diameter",
    29: "chamfer",  # a chamfer size (C1) with one drawn arrowhead on a leader
}
# A hidden text run inside a composite record: size-framed like a run, retained
# without decoding.
_HIDDEN_RUN_MARKER = 0x2000
_ITEM_KINDS: dict[int, Literal["vectors", "line", "point", "arc"]] = {
    0x0344: "vectors",
    0x0300: "vectors",
    0x0244: "line",
    0x0200: "line",
    0x0100: "point",
    0x0144: "point",
    0x0400: "arc",
    0x0444: "arc",
    0x0500: "arc",
    0x0544: "arc",
}
# A size-framed nested record inside a symbol: the member list of a group
# (saved entity identifiers), retained without interpretation.
_NESTED_MARKER = 0xFD00
_ANGLE_PATTERN = (
    "vectors",
    "vectors",
    "arc",
    "arc",
    "arc",
    "point",
    "point",
    "vectors",
    "point",
    "vectors",
    "point",
    "point",
)
_DIAMETER_PATTERN_ARC = (
    "arc",
    "arc",
    "arc",
    "point",
    "vectors",
    "vectors",
    "line",
    "point",
    "point",
)
_LEADER_DIAMETER_PATTERN = (
    "arc",
    "arc",
    "point",
    "vectors",
    "vectors",
    "vectors",
    "vectors",
    "line",
    "point",
    "point",
)
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


def _run_chars(
    run: bytes, byteorder: Literal["little", "big"]
) -> tuple[str, str, int] | None:
    """The text of a saved run: UTF-16LE (flag byte 0x20 at +48) or the legacy
    CP932 run (flag byte 0x40, one or two bytes per character) of big-endian
    documents and little-endian V7L2-V7L5. Returns the text, its encoding and
    the offset after the last character."""
    if len(run) < 60 or run[2:4] != b"\x44\x20" or run[51] != 1:
        return None
    n = int.from_bytes(run[52:56], byteorder)
    if run[48] == 0x40:
        position = 56
        chars: list[str] = []
        for _ in range(n):
            if position >= len(run):
                return None
            lead = run[position]
            width = 2 if 0x81 <= lead <= 0x9F or 0xE0 <= lead <= 0xFC else 1
            try:
                chars.append(run[position : position + width].decode("cp932"))
            except UnicodeDecodeError:
                return None
            position += width
        return "".join(chars), "cp932", position
    if run[48] != 0x20 or run[49] not in (0x00, 0x20, 0x40) or run[50] != 0:
        return None
    if 56 + 2 * n > len(run):
        return None
    try:
        text = run[56 : 56 + 2 * n].decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    return text, "utf-16-le", 56 + 2 * n


def _run_text(
    run: bytes, at: int, order: str = "<", *, lenient: bool = False
) -> DrawingText | None:
    """A single saved text run (as embedded in dimensions) as DrawingText.

    Numbers follow the file byte order; the text is UTF-16LE or legacy
    CP932 (see ``_run_chars``). ``lenient`` accepts the flag word at ``+44``
    that leader notes and symbols carry (dimension runs keep it zero)."""
    byteorder: Literal["little", "big"] = "little" if order == "<" else "big"
    decoded = _run_chars(run, byteorder)
    if decoded is None:
        return None
    text, encoding, end = decoded
    x, y = struct.unpack_from(order + "2d", run, 8)
    height, width, pitch = struct.unpack_from(order + "3f", run, 24)
    w36, w40, w44 = struct.unpack_from(order + "IiI", run, 36)
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
    if w36 or (w44 and not lenient):
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
        encoding,
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
        any(run[end + (-end % 4) :]),
        tuple(issues),
    )


@dataclass(frozen=True)
class _Composite:
    """The framed parts of a composite record: attribute blocks, text runs,
    geometry items and nested records, in saved order."""

    blocks: dict[int, tuple[int, bytes]]
    items: tuple[DimensionItem, ...]
    runs: tuple[tuple[int, DrawingText], ...]
    nested: tuple[ByteRange, ...]
    issues: tuple[Diagnostic, ...]
    end: int = 0


def _items(
    b: bytes,
    order: str,
    at: int,
    position: int,
    *,
    prefix: str,
    lenient: bool = False,
    nested: bool = False,
    single_run_code: str | None = None,
    strict_runs: bool = True,
) -> tuple[
    list[DimensionItem],
    list[tuple[int, DrawingText]],
    list[ByteRange],
    list[Diagnostic],
    int,
]:
    """The size-framed text runs, geometry items and nested records from
    ``position`` to the end of the record. Sizes follow the file byte order;
    the two marker bytes do not swap. A run that does not decode ends the
    record (``strict_runs``) or is skipped with a diagnostic."""
    byteorder: Literal["little", "big"] = "little" if order == "<" else "big"
    issues: list[Diagnostic] = []

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    items: list[DimensionItem] = []
    runs: list[tuple[int, DrawingText]] = []
    nested_ranges: list[ByteRange] = []
    while position < len(b):
        if len(b) - position < 8:
            issue(f"drawing.{prefix}_item", position, f"Truncated {prefix} item")
            break
        size = int.from_bytes(b[position : position + 2], byteorder)
        marker = int.from_bytes(b[position + 2 : position + 4], "little")
        if marker == 0x2044:
            if size < 60 or size % 4 or position + size > len(b):
                issue(f"drawing.{prefix}_text", position, "Unframed text run")
                break
            run = _run_text(
                b[position : position + size], at + position, order, lenient=lenient
            )
            if run is None:
                if strict_runs:
                    issue(
                        f"drawing.{prefix}_text", position, "Text run is not qualified"
                    )
                    break
                issue(f"drawing.{prefix}_text", position, "Text run is not qualified")
                position += size
                continue
            if runs and single_run_code:
                issue(single_run_code, position, "Additional value text run")
            runs.append((position, run))
            position += size
            continue
        if nested and marker == _NESTED_MARKER:
            if size < 8 or position + size > len(b):
                issue(f"drawing.{prefix}_item", position, "Truncated nested record")
                break
            nested_ranges.append(ByteRange(at + position, at + position + size))
            position += size
            continue
        if marker == _HIDDEN_RUN_MARKER and 60 <= size and position + size <= len(b):
            position += size  # a hidden run: retained, not drawn
            continue
        item_kind = _ITEM_KINDS.get(marker)
        expected = _ITEM_SIZES.get(item_kind or "", 0)
        if item_kind == "vectors" and size in (40, 72):
            # a point and one vector (hidden in notes) or three vectors (the
            # strokes of surface-texture symbols, type 28)
            expected = size
        if item_kind is None or size != expected or position + size > len(b):
            issue(f"drawing.{prefix}_item", position, f"Unobserved {prefix} item")
            break
        values = struct.unpack_from(
            order + "d" * ((size - (16 if item_kind == "point" else 8)) // 8),
            b,
            position + 8,
        )
        if not all(math.isfinite(v) for v in values):
            issue(f"drawing.{prefix}_item", position + 8, f"Nonfinite {prefix} item")
            break
        radius = start = sweep = None
        if item_kind == "vectors" and size == 40:
            px, py, ax, ay = values
            points: tuple[Point2, ...] = ((px, py), (px + ax, py + ay))
        elif item_kind == "vectors" and size == 72:
            px, py, ax, ay, bx, by, cx2, cy2 = values
            points = (
                (px, py),
                (px + ax, py + ay),
                (px + bx, py + by),
                (px + cx2, py + cy2),
            )
        elif item_kind == "vectors":
            px, py, ax, ay, bx, by = values
            points = (
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
    return items, runs, nested_ranges, issues, position


def _composite(
    b: bytes,
    order: str,
    at: int,
    *,
    prefix: str,
    lenient: bool = False,
    nested: bool = False,
    single_run_code: str | None = None,
    kind_word: bool = True,
    strict_runs: bool = True,
) -> _Composite | None:
    """Frame a composite record (dimension, leader note, symbol): the 0xFF
    attribute blocks, then size-framed text runs and geometry items in saved
    order (see ``_items``). Returns None when the framing itself is not
    qualified."""
    if len(b) < 48 or b[27] != 0xFD:
        return None
    if b[28:32] != b"\0\0\0\x01" and (kind_word or b[28:31] != b"\0\0\0"):
        return None
    blocks: dict[int, tuple[int, bytes]] = {}
    position = 40
    while position + 4 <= len(b) and b[position] == 0xFF:
        length = b[position + 3]
        if length < 4 or position + length > len(b):
            return None
        blocks.setdefault(b[position + 1], (position, b[position : position + length]))
        position += length
    items, runs, nested_ranges, issues, end = _items(
        b,
        order,
        at,
        position,
        prefix=prefix,
        lenient=lenient,
        nested=nested,
        single_run_code=single_run_code,
        strict_runs=strict_runs,
    )
    if strict_runs and any(d.code == f"drawing.{prefix}_text" for d in issues):
        return None
    return _Composite(
        blocks, tuple(items), tuple(runs), tuple(nested_ranges), tuple(issues), end
    )


def _items_box(
    items: tuple[DimensionItem, ...], boxes: tuple[Extent | None, ...]
) -> Extent | None:
    """The extent of geometry items (arcs include their axis extremes) and boxes."""
    xs: list[float] = []
    ys: list[float] = []
    for item in items:
        xs.extend(p[0] for p in item.points)
        ys.extend(p[1] for p in item.points)
        if item.kind == "arc" and item.radius is not None:
            assert item.start_angle is not None and item.sweep_angle is not None
            low, high = sorted((item.start_angle, item.start_angle + item.sweep_angle))
            k = math.ceil(low / (math.pi / 2))
            while k * math.pi / 2 <= high + 1e-12:
                angle = k * math.pi / 2
                xs.append(item.points[0][0] + item.radius * math.cos(angle))
                ys.append(item.points[0][1] + item.radius * math.sin(angle))
                k += 1
    for box in boxes:
        if box is not None:
            xs.extend((box[0], box[2]))
            ys.extend((box[1], box[3]))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _symmetric_legs(p: Point2, q: Point2, r: Point2) -> bool:
    """``q`` is the tip of an arrowhead whose corners ``p`` and ``r`` lie at the
    same distance; extension lines (a point, its foot and the line end) are not."""
    left = math.dist(p, q)
    right = math.dist(r, q)
    if left <= 1e-9 or right <= 1e-9:
        return False
    return (
        math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-6)
        and math.dist(p, r) < left + right
    )


def _note(b: bytes, order: str, at: int) -> DrawingNote | None:
    composite = _composite(b, order, at, prefix="note", lenient=True)
    if composite is None:
        return None
    issues = list(composite.issues)

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    texts = tuple(run for _, run in composite.runs)
    text = texts[0] if texts else None
    if text is None:
        issue("drawing.note_text", 40, "No text run")
    for position, run in composite.runs:
        if run.layout_status != "complete":
            issue("drawing.note_text", position, "A text run is not fully qualified")
    arrow: tuple[Point2, Point2, Point2] | None = None
    path: tuple[Point2, ...] = ()
    circle: tuple[Point2, float] | None = None
    for item in composite.items:
        offset = item.byte_range.start - at
        if item.kind == "arc" and item.radius is not None and circle is None:
            circle = (item.points[0], item.radius)  # the balloon around the text
            continue
        if item.kind != "vectors":
            issue("drawing.note_item", offset, "Unobserved note item")
            continue
        if not item.raw_marker & 0x40:
            continue  # hidden alternatives are retained in items, not drawn
        if len(item.points) == 2:
            # the two-point leader of the older layouts: the tip and the end
            if not path:
                path = item.points
            else:
                issue("drawing.note_item", offset, "Additional leader item")
            continue
        p, q, r = item.points
        if arrow is None and _symmetric_legs(p, q, r):
            arrow = (p, q, r)
        elif not path:
            path = (p, q, r)
        else:
            issue("drawing.note_item", offset, "Additional leader item")
    if not path:
        issue("drawing.note_layout", 40, "No leader item")
    arrow_width = arrow_angle = None
    if 4 in composite.blocks and len(composite.blocks[4][1]) >= 48:
        width, _, angle = struct.unpack_from(order + "3d", composite.blocks[4][1], 24)
        if math.isfinite(width) and math.isfinite(angle):
            arrow_width, arrow_angle = width, math.degrees(angle)
    box = _items_box(composite.items, tuple(run.box for run in texts))
    return DrawingNote(
        "partial" if issues else "complete",
        text,
        composite.items,
        arrow,
        path,
        arrow_width,
        arrow_angle,
        box,
        tuple(issues),
        texts,
        circle,
    )


def _symbol(b: bytes, order: str, at: int) -> DrawingSymbol | None:
    composite = _composite(
        b,
        order,
        at,
        prefix="symbol",
        lenient=True,
        nested=True,
        kind_word=False,
        strict_runs=False,
    )
    if composite is None:
        return None
    return _symbol_from(composite, b, order, at, b[31], None)


def _symbol_from(
    composite: _Composite,
    b: bytes,
    order: str,
    at: int,
    raw_kind: int,
    name: str | None,
) -> DrawingSymbol:
    issues = list(composite.issues)
    texts = tuple(run for _, run in composite.runs)
    for position, run in composite.runs:
        if run.layout_status != "complete":
            issues.append(
                Diagnostic(
                    "unsupported",
                    "drawing.symbol_text",
                    at + position,
                    "A text run is not fully qualified",
                )
            )
    if not composite.items and not texts and not composite.nested:
        issues.append(
            Diagnostic(
                "unsupported", "drawing.symbol_layout", at + 40, "Symbol without items"
            )
        )
    box = _items_box(composite.items, tuple(run.box for run in texts))
    return DrawingSymbol(
        "partial" if issues else "complete",
        composite.items,
        texts,
        composite.nested,
        box,
        tuple(issues),
        raw_kind,
        name,
    )


def _record_name(raw: bytes) -> str | None:
    """The saved name of a record: a UTF-16LE string ending in a zero word, or
    ASCII padded with spaces."""
    if len(raw) >= 2:
        end = 0
        while end + 1 < len(raw) and raw[end : end + 2] != b"\0\0":
            end += 2
        if end:
            try:
                text = raw[:end].decode("utf-16-le", errors="strict")
            except UnicodeDecodeError:
                text = ""
            if text and all(c.isprintable() for c in text) and not text.isspace():
                return text.strip() or None
    if raw and all(0x20 <= c < 0x7F for c in raw.rstrip(b"\0")):
        return raw.rstrip(b"\0").decode("ascii").strip() or None
    return None


def _scanned_symbol(
    b: bytes, order: str, at: int, *, name_at: int | None
) -> DrawingSymbol | None:
    """A record whose size-framed items follow a saved name and attribute words
    of unframed length: line information (type 224: P&ID and circuit lines,
    junction and connection marks, symbol instances) and motion-verification
    records (230, 234). The item stream starts where a chain of items reaches
    the end of the record without an unobserved item."""
    if len(b) < 96 or b[27] not in (0xFD, 0xFE):
        return None
    name = _record_name(b[name_at : name_at + 64]) if name_at is not None else None
    for start in range(40, len(b) - 8, 4):
        items, runs, nested_ranges, issues, end = _items(
            b, order, at, start, prefix="symbol", lenient=True, strict_runs=False
        )
        if not items and not runs:
            continue
        if end != len(b) or any(d.code == "drawing.symbol_item" for d in issues):
            continue
        composite = _Composite(
            {}, tuple(items), tuple(runs), tuple(nested_ranges), tuple(issues), end
        )
        return _symbol_from(composite, b, order, at, b[31], name)
    return None


def _hatch_legacy(b: bytes, order: str, at: int) -> DrawingHatch | None:
    """The hatch layout of big-endian documents: the rendered lines only (the
    origin, a unit direction, two zero words, then a length and the next start
    offset per line), without the boundary point list of the later layout."""
    byteorder: Literal["little", "big"] = "little" if order == "<" else "big"
    if (
        len(b) < 80
        or b[27] != 9
        or int.from_bytes(b[24:26], byteorder) != len(b) - 24
        or b[26] not in (0, 0x44)
        or b[30:32] != b"\0\0"
        or (len(b) - 32) % 8
    ):
        return None
    issues: list[Diagnostic] = []

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    values = struct.unpack_from(order + f"{(len(b) - 32) // 8}d", b, 32)
    if not all(math.isfinite(v) for v in values):
        issue("drawing.hatch_fields", 32, "Nonfinite hatch values")
        values = tuple(0.0 for _ in values)
    ox, oy, dx, dy, z1, z2 = values[:6]
    rest = values[6:]
    if (
        not math.isclose(math.hypot(dx, dy), 1.0, abs_tol=1e-9)
        or z1 != 0
        or z2 != 0
        or (len(rest) - 1) % 3
    ):
        issue("drawing.hatch_fields", 48, "Unobserved hatch words")
    starts = [(0.0, 0.0)] + [(rest[i], rest[i + 1]) for i in range(1, len(rest) - 1, 3)]
    lengths = [rest[i] for i in range(0, len(rest), 3)]
    if any(v <= 0 for v in lengths):
        issue("drawing.hatch_fields", 80, "Nonpositive hatch line length")
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
    xs = [p[0] for seg in segments for p in seg]
    ys = [p[1] for seg in segments for p in seg]
    box = (min(xs), min(ys), max(xs), max(ys)) if xs else None
    return DrawingHatch(
        "partial" if issues else "complete",
        (ox, oy),
        (),
        (),
        (ox, oy),
        (dx, dy),
        math.degrees(math.atan2(dy, dx)),
        spacing,
        segments,
        box,
        tuple(issues),
    )


def _curve(b: bytes, order: str, at: int) -> DrawingCurve | None:
    byteorder: Literal["little", "big"] = "little" if order == "<" else "big"
    if len(b) < 136 or b[27] != 0x10 or b[26] not in (0, 0x44) or b[30:32] != b"\0\0":
        return None
    body = 24 + int.from_bytes(b[24:26], byteorder)
    if body < len(b):
        # The elliptical arcs of V5-V7 are saved as a free curve with the
        # ellipse words appended in one size-framed chunk, retained raw
        chunk = (
            int.from_bytes(b[body : body + 2], byteorder) if body + 2 <= len(b) else 0
        )
        if not 8 <= chunk <= len(b) - body:
            return None
        b = b[:body]
    elif body != len(b):
        return None
    n = int.from_bytes(b[38:40], byteorder)
    if n < 2 or len(b) != 136 + 72 * (n - 1):
        return None
    issues: list[Diagnostic] = []

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    saved_extent = struct.unpack_from(order + "4d", b, 40)
    (length,) = struct.unpack_from(order + "d", b, 80)
    chords = struct.unpack_from(order + f"{n - 1}d", b, 120)
    base = 120 + 8 * (n - 1)
    x0, y0 = struct.unpack_from(order + "2d", b, base)
    offsets = struct.unpack_from(order + f"{2 * (n - 1)}d", b, base + 16)
    words = struct.unpack_from(order + f"{6 * (n - 1)}d", b, base + 16 + 16 * (n - 1))
    if not all(
        math.isfinite(v)
        for v in (*saved_extent, length, *chords, x0, y0, *offsets, *words)
    ):
        return None
    knots: list[Point2] = [(x0, y0)]
    for i in range(n - 1):
        knots.append((x0 + offsets[2 * i], y0 + offsets[2 * i + 1]))
    for i in range(n - 1):
        chord = math.dist(knots[i], knots[i + 1])
        if chords[i] <= 0 or not math.isclose(
            chord, chords[i], rel_tol=1e-6, abs_tol=1e-9
        ):
            issue(
                "drawing.curve_layout",
                120 + 8 * i,
                "Chord length differs from the knots",
            )
            break
    if not math.isclose(sum(chords), length, rel_tol=1e-6, abs_tol=1e-9):
        issue("drawing.curve_layout", 80, "Curve length differs from the chords")
    saved: Extent = (saved_extent[0], saved_extent[1], saved_extent[2], saved_extent[3])
    extent: Extent | None = saved
    if saved[0] > saved[2] or saved[1] > saved[3]:
        issue("drawing.curve_fields", 40, "Saved extent is not a box")
        extent = None
    xs = [k[0] for k in knots]
    ys = [k[1] for k in knots]
    box: Extent = (min(xs), min(ys), max(xs), max(ys))
    if extent is not None:
        box = (
            min(box[0], extent[0]),
            min(box[1], extent[1]),
            max(box[2], extent[2]),
            max(box[3], extent[3]),
        )
    return DrawingCurve(
        "partial" if issues else "complete",
        tuple(knots),
        tuple(chords),
        length,
        extent,
        tuple(tuple(words[6 * i : 6 * i + 6]) for i in range(n - 1)),
        box,
        tuple(issues),
    )


def _dimension(b: bytes, order: str, at: int) -> DrawingDimension | None:
    composite = _composite(
        b, order, at, prefix="dimension", single_run_code="drawing.dimension_runs"
    )
    if composite is None or not composite.runs:
        return None
    issues = list(composite.issues)

    def issue(code: str, offset: int, message: str) -> None:
        issues.append(Diagnostic("unsupported", code, at + offset, message))

    kind: Literal["length", "angle", "diameter", "radius", "chamfer", "unknown"] = (
        _DIMENSION_KINDS.get(b[15]) or "unknown"
    )
    blocks = composite.blocks
    items = list(composite.items)
    text = composite.runs[0][1]
    value = scale = gap = arrow_width = arrow_angle = None
    underline: tuple[float, float] | None = None
    if 6 in blocks and len(blocks[6][1]) >= 56:
        value = struct.unpack_from(order + "d", blocks[6][1], 48)[0]
    if 3 in blocks and len(blocks[3][1]) >= 48:
        gap, scale = struct.unpack_from(order + "2d", blocks[3][1], 32)
    if 4 in blocks and len(blocks[4][1]) >= 48:
        arrow_width, _, angle = struct.unpack_from(order + "3d", blocks[4][1], 24)
        arrow_angle = math.degrees(angle)
    if 1 in blocks and len(blocks[1][1]) >= 40:
        first, second = struct.unpack_from(order + "2d", blocks[1][1], 24)
        underline = (first, second)
    elif 16 in blocks and len(blocks[16][1]) >= 32:
        first, second = struct.unpack_from(order + "2d", blocks[16][1], 16)
        underline = (first, second)
    for name, number in (("value", value), ("gap", gap), ("scale", scale)):
        if number is not None and not math.isfinite(number):
            issue("drawing.dimension_fields", 40, f"Nonfinite dimension {name}")
    measured = line = line_point = None
    extension = aux_offset = None
    center = pick = None
    radius = None
    arc_start = arc_sweep = None
    leader: tuple[Point2, ...] | None = None
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
        elif pattern == _LEADER_DIAMETER_PATTERN:
            # The leader variant: an arrowhead on the circle, the leader from
            # its tip to an elbow and the shelf to the text; the hidden copies
            # of the arrowheads and the zero-length line are not drawn.
            pick = items[2].points[0]
            leader = items[4].points
            line = (leader[1], leader[2])
            line_point = items[8].points[0]
        elif pattern == _DIAMETER_PATTERN_ARC:
            # A dimension on an arc: a quarter arc, the drawn extension arc and
            # the measured arc precede the pick point
            pick = items[3].points[0]
            line = (items[6].points[0], items[6].points[1])
            line_point = items[7].points[0]
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
    elif kind == "chamfer":
        # The length layout with one hidden arrowhead and a trailing hidden
        # line; the value is the chamfer size of the text (C1), the measured
        # points coincide at the chamfer
        if broken:
            pass
        elif pattern != _LENGTH_PATTERN + ("line",):
            issue(
                "drawing.dimension_layout",
                40,
                "Unobserved chamfer dimension item sequence",
            )
        else:
            line = (items[2].points[0], items[2].points[1])
            line_point = items[3].points[0]
            measured = (items[7].points[0], items[8].points[0])
    elif kind == "angle":
        if broken:
            pass
        elif pattern != _ANGLE_PATTERN:
            issue(
                "drawing.dimension_layout",
                40,
                "Unobserved angle dimension item sequence",
            )
        else:
            # Three arcs share the vertex: the drawn dimension arc carries the
            # 0x0544 marker; the others are its hidden copies or the arc
            # through the measured points.
            drawn = [i for i in items[2:5] if i.raw_marker == 0x0544]
            arc = drawn[0] if drawn else items[3]
            center, radius = arc.points[0], arc.radius
            arc_start, arc_sweep = arc.start_angle, arc.sweep_angle
            line_point = items[5].points[0]
            measured = (items[7].points[0], items[9].points[0])
            # Each aux line: the measured point, its foot on the arc and the end
            # beyond it; the second one may start at the vertex itself.
            aux = items[7]
            foot = math.dist(aux.points[0], aux.points[1])
            end = math.dist(aux.points[0], aux.points[2])
            extension = end - foot if foot > 0 else None
            aux_offset = math.dist(aux.points[0], items[8].points[0])
    else:
        issue(
            "drawing.dimension_kind",
            15,
            "Only the length, angle and diameter layouts are interpreted",
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
        arc_start,
        arc_sweep,
        leader,
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
    curve = None
    note = None
    symbol = None
    status: Status = "unsupported"
    code, message = "drawing.entity", "Unqualified 2D entity layout"
    if (
        len(b) >= 32
        and u(0) == len(b)
        and source is not None
        and source >> 28 == 8
        and b[8:12] == b"\0" * 4
        and b[20:24] == b"\0" * 4
        and (
            b[5:8] == b"\0" * 3
            or (t == 92 and b[5:8] == b"\x01\0\0")
            # V5-V7 elliptical arcs saved as curves carry the word 0x0090 at
            # +6 (file byte order) after the layer byte
            or (
                t == 89
                and b[27] == 0x10
                and b[5] == 0
                and int.from_bytes(b[6:8], document.header.byte_order) == 0x90
            )
        )
    ):
        layer, visible = b[4], bool(b[12] & 0x40)
        if t == 21:
            text = _text(b, order, limits, at)
            if text:
                run_at = 40 + b[43]  # the first run follows the attribute block
                width, style, color = (
                    b[run_at + 4] & 0x7F,
                    b[run_at + 5] >> 4,
                    b[run_at + 5] & 15,
                )
                if text.layout_status == "complete":
                    status = "complete"
                else:
                    status = "partial"
                    code, message = (
                        "drawing.text_layout",
                        "Text layout is partially qualified; see the text diagnostics",
                    )
        dimension = None
        if t == 92:
            hatch = (
                _hatch(b, order, at) if order == "<" else _hatch_legacy(b, order, at)
            )
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
        if t in _DIMENSION_KINDS or t == 46:
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
        if t == 16 or (t == 89 and b[27] == 0x10):
            # Free curve (any byte order): knots, chord lengths and the saved
            # extent; V5-V7 elliptical arcs are saved this way with ellipse words
            curve = _curve(b, order, at)
            if curve is not None:
                width, style, color = b[28] & 0x7F, b[29] >> 4, b[29] & 15
                if curve.layout_status == "complete":
                    status = "complete"
                else:
                    status = "partial"
                    code, message = (
                        "drawing.curve_layout",
                        "Free curve layout is partially qualified; see its diagnostics",
                    )
        if t in (22, 34, 44):
            note = _note(b, order, at)
            if note is not None:
                first_item = note.items[0] if note.items else None
                if first_item is not None:
                    width, style, color = (
                        first_item.line_width,
                        first_item.line_style,
                        first_item.color_index,
                    )
                if note.layout_status == "complete":
                    status = "complete"
                else:
                    status = "partial"
                    code, message = (
                        "drawing.note_layout",
                        "Leader note layout is partially qualified; see diagnostics",
                    )
        if t in (212, 14, 28):
            symbol = _symbol(b, order, at)
        elif t in (224, 230, 234):
            symbol = _scanned_symbol(
                b, order, at, name_at=32 if t == 224 else 36 if t == 234 else None
            )
        if symbol is not None:
            first_item = symbol.items[0] if symbol.items else None
            if first_item is not None:
                width, style, color = (
                    first_item.line_width,
                    first_item.line_style,
                    first_item.color_index,
                )
            if symbol.layout_status == "complete":
                status = "complete"
            else:
                status = "partial"
                code, message = (
                    "drawing.symbol_layout",
                    "Symbol layout is partially qualified; see its diagnostics",
                )
        if t in _ATTRIBUTE_TYPES and symbol is None:
            code, message = (
                "drawing.attribute_record",
                "Attribute record (line information, motion verification, 3D "
                "projection frame) without drawing geometry",
            )
        body = len(b)
        payload = 24 + int.from_bytes(b[24:26], document.header.byte_order)
        if 32 < payload < len(b) - 8:
            chunk = int.from_bytes(b[payload : payload + 2], document.header.byte_order)
            if 8 <= chunk <= len(b) - payload:
                # A primitive with attribute chunks appended (connection points
                # of P&ID symbols, motion-verification circles); the primitive
                # reads as usual and the chunks are retained
                body = payload
        # Motion-verification records (232) frame a circle (subtype 4) first
        key = 6 if t == 232 and b[27] == 4 else t
        expected = {
            1: (56, 1),
            2: (72, 2),
            5: (72, 5),
            6: (72, 4),
            12: (80, 3),
            88: (88, 7),
            89: (88, 8),
        }.get(key if key is not None else -1)
        if (
            expected
            and (body, b[27]) == expected
            and int.from_bytes(b[24:26], document.header.byte_order) == body - 24
            and b[26] in (0, 0x44)
            and b[30:32] == b"\0\0"
        ):
            width, style, color = b[28] & 0x7F, b[29] >> 4, b[29] & 15
            values = struct.unpack_from(
                order + {1: "2d", 12: "6d", 88: "7d", 89: "7d"}.get(t or -1, "5d"),
                b,
                32,
            )
            if all(math.isfinite(v) for v in values):
                x, y = values[:2]
                if t == 1:
                    primitive = DrawingPrimitive("point", ((x, y),))
                elif t == 12:
                    # two segments from a point (view-direction and section marks)
                    ax, ay, bx, by = values[2:]
                    primitive = DrawingPrimitive(
                        "vectors", ((x, y), (x + ax, y + ay), (x + bx, y + by))
                    )
                elif t in (88, 89):
                    # Ellipse (88) and elliptical arc (89): the semi-axes, the
                    # angle of the ellipse's x axis and parametric start/sweep
                    major, minor, rotation, start, sweep = values[2:]
                    if major > 0 and minor > 0 and 0 < abs(sweep) <= math.tau + 1e-8:
                        full = math.isclose(abs(sweep), math.tau, abs_tol=1e-8)
                        primitive = DrawingPrimitive(
                            "ellipse"
                            if t == 88 and full and start == 0
                            else "elliptical_arc",
                            center=(x, y),
                            radius=major,
                            start_angle=start,
                            sweep_angle=sweep,
                            minor_radius=minor,
                            rotation=rotation,
                        )
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
        + (curve.diagnostics if curve is not None else ())
        + (note.diagnostics if note is not None else ())
        + (symbol.diagnostics if symbol is not None else ())
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
        curve,
        note,
        symbol,
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
