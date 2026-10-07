"""Write 2D views and point, line, circle and arc entities into an ICD copy.

Everything here is byte surgery on the :mod:`icadkit.container` model.
Existing records are copied, new entity records use the layouts the reader
qualifies, a new view copies the prefix and header of an existing 2D view of
the same document, and the directory, record lengths and MOD total are
recomputed on serialization. The output reproduces iCAD's own files up to the
save noise seen in resave pairs (timestamps, record IDs, three MOD words) when
the same edit is applied to captured pairs, but **no output of this module has
been opened by iCAD**; see ``docs/writing.md`` for the verification level.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Literal

from .container import ContainerRecord
from .document import Document
from .drawing import (
    _TEXT_CONSTANTS,
    _TEXT_LINE_WORDS,
    DrawingEntity,
    DrawingLimits,
    _dimension,
    _hatch,
    _text,
    char_cells,
)
from .errors import UnsupportedFormatError
from .models import ByteRange, Diagnostic

Point2 = tuple[float, float]
Extent = tuple[float, float, float, float]

_EMPTY_EXTENT: Extent = (1e38, 1e38, -1e38, -1e38)
_GROUP_MARKER = 0x30010000
_PLACEMENT_TAG = 0x40000000
_TERMINATOR = 0xFE000000
_TWO_D = ("2d_global", "2d_view", "registered_part")
# Every observed 2D view number lies in 1..6; the reader classifies 2..6.
_MAX_VIEW_NUMBER = 6
_FIRST_ID = 0x80000001


@dataclass(frozen=True)
class Point2D:
    point: Point2


@dataclass(frozen=True)
class Line2D:
    start: Point2
    end: Point2


@dataclass(frozen=True)
class Circle2D:
    center: Point2
    radius: float


@dataclass(frozen=True)
class Arc2D:
    """Counter-clockwise from ``start_angle`` by the signed ``sweep_angle``,
    both in radians; ``0 < |sweep_angle| <= 2*pi``."""

    center: Point2
    radius: float
    start_angle: float
    sweep_angle: float


@dataclass(frozen=True)
class Text2D:
    """One or two lines of text placed by a base point.

    ``lines`` holds one or two BMP strings, the observed line counts. The
    ``anchor`` is where ``base_point`` sits on the cell-based box (1
    top-left, 5 centre, 7 bottom-left; columns 1/4/7, 2/5/8, 3/6/9, rows
    1-3, 4-6, 7-9). ``height`` is the character height in millimetres;
    ``width_ratio`` and ``space_ratio`` scale the saved character width and
    pitch, ``row_space`` separates lines or columns. ``way`` is the saved
    code of unknown meaning observed as 1 to 4. The record is written
    without the optional stroke-glyph cache; vertical presentation forms
    such as the vertical long-vowel mark are the caller's choice.
    """

    lines: tuple[str, ...] | str
    anchor: Point2
    height: float
    base_point: int = 1
    direction: Literal["horizontal", "vertical"] = "horizontal"
    width_ratio: float = 1.0
    space_ratio: float = 1.0
    row_space: float = 0.0
    way: int = 1


@dataclass(frozen=True)
class LengthDimension2D:
    """A length dimension between two measured points.

    ``start`` and ``end`` are the measured points, ``line_point`` a point on
    the dimension line (its projection is saved as the line point). The value
    text is centred between the arrowheads, which are drawn inside: the
    observed default placement; a text that does not fit is refused.
    ``orientation`` measures along the segment (``aligned``) or its
    horizontal or vertical projection. ``text`` replaces the value text (the
    measured length in full-width digits). ``height``, ``gap`` (text offset
    from the line), ``arrow_width``, ``extension`` (aux line overshoot),
    ``aux_offset`` (distance from a measured point to its aux line) and
    ``underline`` are paper millimetres, divided by the view scale when
    written; ``arrow_angle`` is in degrees. The record stores
    its rendered arrowheads, lines and points exactly as iCAD saves them.
    """

    start: Point2
    end: Point2
    line_point: Point2
    text: str | None = None
    orientation: Literal["aligned", "horizontal", "vertical"] = "aligned"
    height: float = 4.0
    gap: float = 1.0
    arrow_width: float = 1.5
    arrow_angle: float = 15.0
    extension: float = 2.8
    aux_offset: float = 0.0
    underline: tuple[float, float] = (3.0, 1.0)
    line_width: int = 3
    text_width: int = 2


@dataclass(frozen=True)
class DiameterDimension2D:
    """A diameter dimension of a circle in the observed default placement.

    ``center`` and ``radius`` describe the measured circle and ``text_point``
    picks the side: the dimension line runs through the centre toward it,
    both arrowheads sit on the circle pointing inward from outside, the
    value text (the diameter in full-width digits unless ``text`` is given)
    follows the line beyond the near arrowhead and the line ends
    ``underline[0]`` past the text. Directions along which the text would
    read upside down are refused as unobserved. ``height``, ``gap``,
    ``arrow_width``, ``space`` (the clearance between arrowheads, text and
    line ends), ``extension`` and ``underline`` are paper millimetres,
    divided by the view scale when written; ``arrow_angle`` is in degrees.
    The record stores its rendered items exactly as iCAD saves them.
    """

    center: Point2
    radius: float
    text_point: Point2
    text: str | None = None
    height: float = 4.0
    gap: float = 1.0
    arrow_width: float = 1.5
    arrow_angle: float = 15.0
    space: float = 1.0
    extension: float = 2.8
    underline: tuple[float, float] = (3.0, 1.0)
    line_width: int = 3
    text_width: int = 2


@dataclass(frozen=True)
class Hatch2D:
    """Parallel hatch lines clipped to a simple polygon.

    ``polygon`` lists the boundary vertices (three or more, closed
    implicitly, straight edges only). Lines run at ``angle`` degrees with
    the perpendicular ``spacing``, on a lattice through ``anchor`` (the
    first vertex by default); the record stores the boundary edges relative
    to the anchor and every clipped line, as iCAD saves them.
    """

    polygon: tuple[Point2, ...]
    angle: float = 45.0
    spacing: float = 3.0
    anchor: Point2 | None = None


Primitive2D = (
    Point2D
    | Line2D
    | Circle2D
    | Arc2D
    | Text2D
    | LengthDimension2D
    | DiameterDimension2D
    | Hatch2D
)

# The attribute part (+32..+376) of an observed length dimension record. Its
# doubles are patched: underline lengths (+64, +72), text gap and scale
# factor (+128, +136), scale (+160, +368), measured value (+216), and for
# each arrowhead the scale, width, dot diameter and angle (+264.., +312..).
# Bytes +32..+376 of the captured diameter dimension record; the doubles that
# depend on the request are patched in ``_encode_diameter_dimension``.
_DIAMETER_DIM_ATTRIBUTES = bytes.fromhex(
    "140008000000ec00ff1001200002010458012002000000000000000000000840000000000000f03fff1400380000030802040001030204020000000000000000666666666666064000000000000000006666666666660640000000000000f03fff03013008030308010101010301010114016402d0020000080109020a020000000000000000f03f000000000000f03fff050118000102048c00000000010000000000000000f03fff0601501c00010c010c010201ff03010000000000000000020100200000000000000000050100010000000100000000feffffffffff23400000000000000000000000000000f03f000000000000f03fff040230000001080601000000000000000000000000f03f000000000000f83f000000000000f03f65732d3852c1d03fff040230000001080701000000000000000000000000f03f000000000000f83f000000000000f03f65732d3852c1d03f"
)
_LENGTH_DIM_ATTRIBUTES = bytes.fromhex(
    "0a0008000000b400ff01013800020004e4002004000000000000000000000000000000"
    "0000000840000000000000f03f00000000000000000000000000001440ff0301300803"
    "0308010101010201010114016402d00200000501060207020000000000000000f03f00"
    "0000000000f03fff050118000102048c00000000010000000000000000f03fff060150"
    "1c00010c0102010201ff0301000000000000000002010000000000000000000002010001"
    "000200010000000000000000000044400000000000000000000000000000f03f000000"
    "000000f03fff040230000001080301000000000000000000000000f03f000000000000"
    "f83f000000000000f03f65732d3852c1d03fff040230000001080401000000000000000"
    "000000000f03f000000000000f83f000000000000f03f65732d3852c1d03fff02012001"
    "00040c01000000080109010a020b0200010000000000000000f03f"
)


@dataclass(frozen=True)
class EntityStyle:
    """The qualified appearance bytes of a 2D entity record."""

    layer: int = 1
    visible: bool = True
    line_width: int = 2
    line_style: int = 1
    color_index: int = 1

    def __post_init__(self) -> None:
        for name, upper in (
            ("layer", 255),
            ("line_width", 127),
            ("line_style", 15),
            ("color_index", 15),
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 <= value <= upper:
                raise ValueError(f"{name} must be between 0 and {upper}")
        if not isinstance(self.visible, bool):
            raise TypeError("visible must be a bool")


@dataclass(frozen=True)
class WriteResult:
    """A serialized copy; ``verification`` never claims an iCAD check."""

    payload: bytes
    path: Path | None
    source_sha256: str
    output_sha256: str
    added_views: tuple[str, ...]
    added_entities: int
    verification: Literal["corpus_consistent"] = "corpus_consistent"
    diagnostics: tuple[Diagnostic, ...] = ()
    deleted_views: tuple[str, ...] = ()
    deleted_entities: int = 0


@dataclass
class _Entity:
    entity_id: int
    offset: int
    length: int
    box: Extent | None


@dataclass
class _Placement:
    """A 264-byte view placement record of the 2D global view: name at +136."""

    offset: int
    name: str


@dataclass
class _ViewState:
    index: int
    name: str
    kind: str
    number: int
    body: bytearray
    header: int
    count: int
    group_words: int
    has_marker: bool
    extent: Extent | None
    extent_known: bool
    scale: float
    entities: list[_Entity]
    entity_end: int
    placement_tag: int | None
    placements: list[_Placement]
    placement_end: int


def _unsupported(code: str, offset: int | None, message: str) -> UnsupportedFormatError:
    return UnsupportedFormatError(Diagnostic("unsupported", code, offset, message))


def _finite(values: Iterable[float], what: str) -> tuple[float, ...]:
    out = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"{what} must be numbers")
        if not math.isfinite(value):
            raise ValueError(f"{what} must be finite")
        out.append(float(value))
    return tuple(out)


def _arc_points(
    cx: float, cy: float, r: float, a0: float, sweep: float
) -> list[Point2]:
    points = [
        (cx + r * math.cos(a0), cy + r * math.sin(a0)),
        (cx + r * math.cos(a0 + sweep), cy + r * math.sin(a0 + sweep)),
    ]
    low, high = (a0, a0 + sweep) if sweep >= 0 else (a0 + sweep, a0)
    k = math.ceil(low / (math.pi / 2))
    while k * math.pi / 2 <= high + 1e-12:
        a = k * math.pi / 2
        points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        k += 1
    return points


def _box(points: Iterable[Point2]) -> Extent:
    xs, ys = zip(*points, strict=True)
    return (min(xs), min(ys), max(xs), max(ys))


def _placement_name(body: bytearray, offset: int) -> str:
    return (
        bytes(body[offset + 136 : offset + 144]).rstrip(b" ").decode("cp932", "replace")
    )


def _entity_box(entity: DrawingEntity) -> Extent | None:
    """The derived extent of a saved entity, or None when it is not derived."""
    p = entity.primitive
    if p is not None:
        if p.kind in ("point", "line"):
            return _box(p.points)
        assert p.center is not None and p.radius is not None
        cx, cy = p.center
        if p.kind == "circle":
            return (cx - p.radius, cy - p.radius, cx + p.radius, cy + p.radius)
        assert p.start_angle is not None and p.sweep_angle is not None
        return _box(_arc_points(cx, cy, p.radius, p.start_angle, p.sweep_angle))
    if entity.text is not None:
        return entity.text.box
    if entity.dimension is not None:
        return entity.dimension.box
    if entity.hatch is not None:
        return entity.hatch.box
    return None


def _union(a: Extent | None, b: Extent | None) -> Extent | None:
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _encode_text(
    order: str, text: Text2D, style: EntityStyle, entity_id: int, scale: float
) -> tuple[bytes, Extent]:
    """The observed little-endian text record without its glyph cache."""
    if order != "<":
        raise _unsupported(
            "write.text_order",
            None,
            "Text records are qualified for little-endian documents",
        )
    lines = (text.lines,) if isinstance(text.lines, str) else tuple(text.lines)
    if not 1 <= len(lines) <= 2:
        raise ValueError("text needs one or two lines (the observed counts)")
    for line in lines:
        if not isinstance(line, str) or not line:
            raise ValueError("every text line must be a nonempty string")
        if any(ord(c) > 0xFFFF or c in "\r\n" for c in line):
            raise ValueError("text lines must be BMP characters without line breaks")
    ax, ay = _finite(text.anchor, "text anchor")
    height, width_ratio, space_ratio, row_space = _finite(
        (text.height, text.width_ratio, text.space_ratio, text.row_space),
        "text metrics",
    )
    if min(height, width_ratio, space_ratio) <= 0 or row_space < 0:
        raise ValueError(
            "text height and ratios must be positive, row_space nonnegative"
        )
    if text.base_point not in range(1, 10):
        raise ValueError("base_point must be 1..9")
    if text.direction not in ("horizontal", "vertical"):
        raise ValueError("direction must be 'horizontal' or 'vertical'")
    if text.way not in (1, 2, 3, 4):
        raise ValueError("way must be 1, 2, 3 or 4 (the observed codes)")
    count = len(lines)
    char_width = height * width_ratio
    pitch = height * space_ratio
    vertical = text.direction == "vertical"
    if vertical:
        column_pitch = height + row_space
        block_w = (count - 1) * column_pitch + char_width
        block_h = max(len(line) for line in lines) * pitch
    else:
        widths = [
            sum(pitch * char_cells(c) for c in line[:-1])
            + char_width * char_cells(line[-1])
            for line in lines
        ]
        block_w = max(widths)
        block_h = count * height + (count - 1) * row_space
    column = (text.base_point - 1) % 3
    row = (text.base_point - 1) // 3
    left = ax - block_w * column / 2
    top = ay + block_h * row / 2
    origins = []
    for index in range(count):
        if vertical:
            origins.append(
                (left + block_w - char_width - index * column_pitch, top - height)
            )
        else:
            origins.append((left, top - (index + 1) * height - index * row_space))
    fixed = bytearray(88)
    fixed[4] = style.layer
    fixed[12] = 0x40 if style.visible else 0
    fixed[14] = count + 1
    fixed[15] = 21
    struct.pack_into("<I", fixed, 16, entity_id)
    fixed[24:46] = _TEXT_CONSTANTS
    fixed[46] = count
    fixed[47] = 8
    fixed[48] = count
    fixed[49] = text.base_point
    fixed[50] = 2 if vertical else 1
    fixed[51] = text.way
    fixed[68:72] = b"\x80\0\0\0"
    fixed[72] = style.line_width
    fixed[73] = 1  # Constant in every saved record; the colour is in the run.
    fixed[74:80] = _TEXT_LINE_WORDS[count][1]
    struct.pack_into("<d", fixed, 80, scale)
    out = bytearray(fixed)
    for line, (x, y) in zip(lines, origins, strict=True):
        run = bytearray(56)
        run[2:4] = b"\x44\x20"
        run[4] = style.line_width
        run[5] = (style.line_style << 4) | style.color_index
        struct.pack_into("<2d", run, 8, x, y)
        struct.pack_into("<3f", run, 24, height, char_width, pitch)
        struct.pack_into(
            "<2I", run, 36, *((0x20000000, 0x60000000) if vertical else (0, 0))
        )
        run[48:52] = b"\x20\0\0\x01"
        struct.pack_into("<I", run, 52, len(line))
        run += line.encode("utf-16-le")
        run += b"\0" * (-len(run) % 8)
        struct.pack_into("<H", run, 0, len(run))
        out += run
    struct.pack_into("<I", out, 0, len(out))
    return bytes(out), (left, top - block_h, left + block_w, top)


def _full_width(value: float) -> str:
    """The measured value as iCAD writes it: full-width digits, no trailing zeros."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "".join(chr(ord(c) + 0xFEE0) if c != "-" else "\uff0d" for c in text)


def _readable(angle: float) -> float:
    """Normalize a direction to the reading orientation (-90, 90] degrees."""
    while angle > 90:
        angle -= 180
    while angle <= -90:
        angle += 180
    return angle


def _clean(values: tuple[float, ...]) -> tuple[float, ...]:
    """iCAD stores positive zeros; a product with -0.0 would differ in one bit."""
    return tuple(0.0 if v == 0 else v for v in values)


def _item(
    order: str,
    marker: int,
    width: int,
    color: int,
    values: tuple[float, ...],
    point: bool = False,
) -> bytes:
    body = struct.pack(order + f"{len(values)}d", *values) + (
        b"\x01\0\x06\0\0\0\0\0" if point else b""
    )
    return (
        struct.pack(order + "HH", 8 + len(body), marker)
        + bytes([width, 0x10 | color, 0, 0])
        + body
    )


def _encode_length_dimension(
    order: str,
    dim: LengthDimension2D,
    style: EntityStyle,
    entity_id: int,
    scale: float,
) -> tuple[bytes, Extent]:
    """The observed length dimension record: attributes, text and rendered items."""
    if order != "<":
        raise _unsupported(
            "write.text_order",
            None,
            "Dimension records are qualified for little-endian documents",
        )
    sx, sy = _finite(dim.start, "dimension start")
    ex, ey = _finite(dim.end, "dimension end")
    lx, ly = _finite(dim.line_point, "dimension line point")
    height, gap, arrow_width, arrow_angle, extension, aux_offset = _finite(
        (
            dim.height,
            dim.gap,
            dim.arrow_width,
            dim.arrow_angle,
            dim.extension,
            dim.aux_offset,
        ),
        "dimension metrics",
    )
    u1, u2 = _finite(dim.underline, "dimension underline")
    if (
        height <= 0
        or arrow_width <= 0
        or not 0 < arrow_angle < 90
        or gap < 0
        or extension < 0
        or aux_offset < 0
    ):
        raise ValueError(
            "dimension metrics must be positive (gap, extension and aux_offset "
            "nonnegative)"
        )
    if u1 < 0 or u2 < 0:
        raise ValueError("underline lengths must be nonnegative")
    for name in ("line_width", "text_width"):
        value = getattr(dim, name)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 127
        ):
            raise ValueError(f"{name} must be an integer between 0 and 127")
    if dim.orientation == "aligned":
        dx, dy = ex - sx, ey - sy
    elif dim.orientation == "horizontal":
        dx, dy = ex - sx, 0.0
    elif dim.orientation == "vertical":
        dx, dy = 0.0, ey - sy
    else:
        raise ValueError("orientation must be 'aligned', 'horizontal' or 'vertical'")
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError("the measured points must differ along the orientation")
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    offset = (lx - sx) * nx + (ly - sy) * ny
    f2 = (sx + offset * nx, sy + offset * ny)
    f3 = (f2[0] + ux * length, f2[1] + uy * length)
    text = _full_width(length) if dim.text is None else dim.text
    if (
        not isinstance(text, str)
        or not text
        or any(ord(c) > 0xFFFF or c in "\r\n" for c in text)
    ):
        raise ValueError("dimension text must be a nonempty line of BMP characters")
    height_v, gap_v, arrow_v, extension_v, offset_v = (
        v / scale for v in (height, gap, arrow_width, extension, aux_offset)
    )
    # iCAD evaluates the arrowhead half-width as sin(a) * width / cos(a); the
    # order of operations reproduces the saved bits, and its double is saved too.
    radians = math.radians(arrow_angle)
    half = math.sin(radians) * arrow_v / math.cos(radians)
    along = (lx - f2[0]) * ux + (ly - f2[1]) * uy
    line_point = (f2[0] + along * ux, f2[1] + along * uy)
    mid = ((f2[0] + f3[0]) / 2, (f2[1] + f3[1]) / 2)  # Text between the arrows.
    angle = _readable(math.degrees(math.atan2(uy, ux)))
    tx, ty = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    upx, upy = -ty, tx
    anchor = (mid[0] + gap_v * upx, mid[1] + gap_v * upy)
    cells = sum(char_cells(c) for c in text[:-1]) + char_cells(text[-1])
    width_v = cells * height_v
    if width_v + 2 * arrow_v > length + 1e-9:
        raise ValueError(
            "the value text must fit between the arrowheads: shorten the text or "
            "measure a longer distance"
        )
    origin = (anchor[0] - width_v / 2 * tx, anchor[1] - width_v / 2 * ty)
    color = style.color_index
    # Attribute part with the patched doubles.
    attributes = bytearray(_LENGTH_DIM_ATTRIBUTES)
    for at, value in (
        (64, u1),
        (72, u2),
        (128, gap),
        (136, scale),
        (160, scale),
        (216, length),
        (368, scale),
        (264, scale),
        (272, arrow_width),
        (288, math.radians(arrow_angle)),
        (312, scale),
        (320, arrow_width),
        (336, math.radians(arrow_angle)),
    ):
        struct.pack_into("<d", attributes, at - 32, value)
    header = bytearray(32)
    header[4] = style.layer
    header[12] = 0x40 if style.visible else 0
    header[14] = 11
    header[15] = 25
    struct.pack_into("<I", header, 16, entity_id)
    struct.pack_into("<H", header, 24, 352)
    header[27] = 0xFD
    header[28:32] = b"\0\0\0\x01"
    run = bytearray(56)
    run[2:4] = b"\x44\x20"
    run[4] = dim.text_width
    run[5] = 0x10 | color
    struct.pack_into("<2d", run, 8, *origin)
    struct.pack_into("<3f", run, 24, height_v, height_v, height_v)
    struct.pack_into("<2I", run, 36, 0, round((angle % 360.0) / 360.0 * (1 << 31)))
    run[48:52] = b"\x20\0\0\x01"
    struct.pack_into("<I", run, 52, len(text))
    run += text.encode("utf-16-le")
    run += b"\0" * (-len(run) % 8)
    struct.pack_into("<H", run, 0, len(run))
    items = b""
    for tip, ix, iy in ((f2, ux, uy), (f3, -ux, -uy)):
        rx, ry = -iy, ix
        corner = (tip[0] + arrow_v * ix - half * rx, tip[1] + arrow_v * iy - half * ry)
        items += _item(
            order,
            0x0344,
            dim.line_width,
            color,
            _clean(
                (
                    corner[0],
                    corner[1],
                    tip[0] - corner[0],
                    tip[1] - corner[1],
                    2 * half * rx,
                    2 * half * ry,
                )
            ),
        )
    items += _item(
        order, 0x0244, dim.line_width, color, _clean((f2[0], f2[1], ux, uy, length))
    )
    items += _item(order, 0x0100, dim.line_width, color, _clean(line_point), point=True)
    items += _item(order, 0x0100, dim.line_width, color, _clean(anchor), point=True)
    for point, foot in (((sx, sy), f2), ((ex, ey), f3)):
        vx, vy = foot[0] - point[0], foot[1] - point[1]
        reach = math.hypot(vx, vy)
        if reach > 0:
            # The aux line starts aux_offset away from the measured point and
            # overshoots the foot by extension, both along the same direction.
            dx_, dy_ = vx / reach, vy / reach
            point = (point[0] + offset_v * dx_, point[1] + offset_v * dy_)
            vx, vy = foot[0] - point[0], foot[1] - point[1]
            wx, wy = vx + extension_v * dx_, vy + extension_v * dy_
        else:
            wx, wy = 0.0, 0.0
        items += _item(
            order,
            0x0344,
            dim.line_width,
            color,
            _clean((point[0], point[1], vx, vy, wx, wy)),
        )

    for point in (dim.start, dim.end):
        items += _item(
            order,
            0x0100,
            dim.line_width,
            color,
            (float(point[0]), float(point[1])),
            point=True,
        )
    record = bytearray(header) + attributes + run + items
    struct.pack_into("<I", record, 0, len(record))
    decoded = _dimension(bytes(record), order, 0)
    if decoded is None or decoded.box is None:
        raise _unsupported(
            "write.dimension", None, "The dimension record does not read back"
        )
    return bytes(record), decoded.box


def _encode_diameter_dimension(
    order: str,
    dim: DiameterDimension2D,
    style: EntityStyle,
    entity_id: int,
    scale: float,
) -> tuple[bytes, Extent]:
    """The observed diameter dimension record: attributes, items and text."""
    if order != "<":
        raise _unsupported(
            "write.text_order",
            None,
            "Dimension records are qualified for little-endian documents",
        )
    cx, cy = _finite(dim.center, "dimension center")
    px, py = _finite(dim.text_point, "dimension text point")
    (radius,) = _finite((dim.radius,), "dimension radius")
    height, gap, arrow_width, arrow_angle, space, extension = _finite(
        (
            dim.height,
            dim.gap,
            dim.arrow_width,
            dim.arrow_angle,
            dim.space,
            dim.extension,
        ),
        "dimension metrics",
    )
    u1, u2 = _finite(dim.underline, "dimension underline")
    if radius <= 0:
        raise ValueError("dimension radius must be positive")
    if (
        height <= 0
        or arrow_width <= 0
        or not 0 < arrow_angle < 90
        or gap < 0
        or space < 0
        or extension < 0
    ):
        raise ValueError(
            "dimension metrics must be positive (gap, space and extension nonnegative)"
        )
    if u1 < 0 or u2 < 0:
        raise ValueError("underline lengths must be nonnegative")
    for name in ("line_width", "text_width"):
        value = getattr(dim, name)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 127
        ):
            raise ValueError(f"{name} must be an integer between 0 and 127")
    reach = math.hypot(px - cx, py - cy)
    if reach <= 0:
        raise ValueError("the text point must differ from the center")
    # iCAD's own order of operations, which reproduces the saved bits: the
    # circle points come from the pick direction, the value is their distance,
    # and the line direction is re-derived from those points.
    ux0, uy0 = (px - cx) / reach, (py - cy) / reach
    tip1 = (cx + radius * ux0, cy + radius * uy0)
    tip2 = (cx - radius * ux0, cy - radius * uy0)
    measured = math.dist(tip1, tip2)
    ux, uy = (tip1[0] - tip2[0]) / measured, (tip1[1] - tip2[1]) / measured
    nx, ny = -uy, ux
    angle = math.degrees(math.atan2(uy0, ux0)) % 360.0
    if _readable(angle) != angle:
        raise ValueError(
            "the text would not be readable along this direction; pick the "
            "text point on the other side of the circle (unobserved placement)"
        )
    text = _full_width(2 * radius) if dim.text is None else dim.text
    if (
        not isinstance(text, str)
        or not text
        or any(ord(c) > 0xFFFF or c in "\r\n" for c in text)
    ):
        raise ValueError("dimension text must be a nonempty line of BMP characters")
    height_v, gap_v, arrow_v, space_v, extension_v, u1_v = (
        v / scale for v in (height, gap, arrow_width, space, extension, u1)
    )
    radians = math.radians(arrow_angle)
    half = math.sin(radians) * arrow_v / math.cos(radians)
    cells = sum(char_cells(c) for c in text[:-1]) + char_cells(text[-1])
    width_v = cells * height_v
    # Observed placement: arrowheads outside the circle, the text start two
    # clearances past the near arrowhead, the line one clearance past the far
    # arrowhead and the first underline length past the text.
    reach_text = arrow_v + 2 * space_v
    reach_near = reach_text + width_v + u1_v
    line_point = (tip1[0] + reach_text * ux, tip1[1] + reach_text * uy)
    anchor = (line_point[0] + gap_v * nx, line_point[1] + gap_v * ny)
    start = (tip1[0] + reach_near * ux, tip1[1] + reach_near * uy)
    length = reach_near + radius + (radius + arrow_v + space_v)
    color = style.color_index
    attributes = bytearray(_DIAMETER_DIM_ATTRIBUTES)
    for at, value in (
        (56, u1),
        (64, u2),
        (96, extension),
        (112, extension),
        (160, gap),
        (168, scale),
        (192, scale),
        (248, measured),
        (296, scale),
        (304, arrow_width),
        (320, radians),
        (344, scale),
        (352, arrow_width),
        (368, radians),
    ):
        struct.pack_into("<d", attributes, at - 32, value)
    header = bytearray(32)
    header[4] = style.layer
    header[12] = 0x40 if style.visible else 0
    header[14] = 10
    header[15] = 27
    struct.pack_into("<I", header, 16, entity_id)
    struct.pack_into("<H", header, 24, 352)
    header[27] = 0xFD
    header[28:32] = b"\0\0\0\x01"
    run = bytearray(56)
    run[2:4] = b"\x44\x20"
    run[4] = dim.text_width
    run[5] = 0x10 | color
    struct.pack_into("<2d", run, 8, *anchor)
    struct.pack_into("<3f", run, 24, height_v, height_v, height_v)
    # The angle word is stored through single precision degrees.
    degrees32 = struct.unpack("<f", struct.pack("<f", angle))[0]
    struct.pack_into("<2I", run, 36, 0, round(degrees32 / 360.0 * (1 << 31)))
    run[48:52] = b"\x20\0\0\x01"
    struct.pack_into("<I", run, 52, len(text))
    run += text.encode("utf-16-le")
    run += b"\0" * (-len(run) % 8)
    struct.pack_into("<H", run, 0, len(run))
    w = dim.line_width
    items = _item(order, 0x0500, w, color, (cx, cy, radius, 0.0, math.pi / 2))
    items += _item(order, 0x0500, w, color, (cx, cy, radius, 0.0, math.tau))
    items += _item(order, 0x0100, w, color, (px, py), point=True)
    items += bytes(run)
    # Near arrowhead: corner and other corner from the circle point.
    corner = (
        tip1[0] + (arrow_v * ux - half * nx),
        tip1[1] + (arrow_v * uy - half * ny),
    )
    other = (tip1[0] + arrow_v * ux + half * nx, tip1[1] + arrow_v * uy + half * ny)
    items += _item(
        order,
        0x0344,
        w,
        color,
        _clean(
            (
                corner[0],
                corner[1],
                tip1[0] - corner[0],
                tip1[1] - corner[1],
                other[0] - corner[0],
                other[1] - corner[1],
            )
        ),
    )
    # Far arrowhead: its corner sits on the re-derived direction, its vectors
    # reach the original circle point.
    far_tip = (cx - radius * ux, cy - radius * uy)
    ix, iy = -ux, -uy
    rx, ry = -iy, ix
    corner = (
        far_tip[0] + arrow_v * ix - half * rx,
        far_tip[1] + arrow_v * iy - half * ry,
    )
    other = (tip2[0] + arrow_v * ix + half * rx, tip2[1] + arrow_v * iy + half * ry)
    items += _item(
        order,
        0x0344,
        w,
        color,
        _clean(
            (
                corner[0],
                corner[1],
                tip2[0] - corner[0],
                tip2[1] - corner[1],
                other[0] - corner[0],
                other[1] - corner[1],
            )
        ),
    )
    items += _item(
        order, 0x0244, w, color, _clean((start[0], start[1], -ux, -uy, length))
    )
    items += _item(order, 0x0100, w, color, _clean(line_point), point=True)
    items += _item(order, 0x0100, w, color, _clean(anchor), point=True)
    record = bytearray(header) + attributes + items
    struct.pack_into("<I", record, 0, len(record))
    decoded = _dimension(bytes(record), order, 0)
    if decoded is None or decoded.box is None or decoded.layout_status != "complete":
        raise _unsupported(
            "write.dimension", None, "The dimension record does not read back"
        )
    return bytes(record), decoded.box


def _hatch_lines(
    polygon: tuple[Point2, ...], angle: float, spacing: float, anchor: Point2
) -> list[tuple[Point2, float]]:
    """Clipped hatch segments as (start, length), by lattice line then position."""
    radians = math.radians(angle)
    dx, dy = math.cos(radians), math.sin(radians)
    nx, ny = -dy, dx
    base = anchor[0] * nx + anchor[1] * ny
    offsets = [(x * nx + y * ny - base) / spacing for x, y in polygon]
    segments: list[tuple[Point2, float]] = []
    # Lattice lines along an extreme edge of the polygon draw nothing.
    low, high = min(offsets) + 1e-9, max(offsets) - 1e-9
    for j in range(math.ceil(low), math.floor(high) + 1):
        level = base + j * spacing
        crossings: list[float] = []
        for i, a in enumerate(polygon):
            b = polygon[(i + 1) % len(polygon)]
            va = a[0] * nx + a[1] * ny - level
            vb = b[0] * nx + b[1] * ny - level
            if (va <= 0 < vb) or (vb <= 0 < va):
                t = va / (va - vb)
                px, py = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
                crossings.append(px * dx + py * dy)
        crossings.sort()
        for u0, u1 in zip(crossings[0::2], crossings[1::2], strict=False):
            if u1 - u0 > 1e-9:
                segments.append(((u0 * dx + level * nx, u0 * dy + level * ny), u1 - u0))
    return segments


def _encode_hatch(
    order: str, hatch: Hatch2D, style: EntityStyle, entity_id: int
) -> tuple[bytes, Extent]:
    """The observed hatch record: anchor, boundary edges and rendered lines."""
    if order != "<":
        raise _unsupported(
            "write.text_order",
            None,
            "Hatch records are qualified for little-endian documents",
        )
    polygon = tuple(
        (float(x), float(y))
        for x, y in (_finite(point, "hatch polygon") for point in tuple(hatch.polygon))
    )
    if len(polygon) < 3:
        raise ValueError("a hatch polygon needs at least three vertices")
    area = sum(
        polygon[i][0] * polygon[(i + 1) % len(polygon)][1]
        - polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
        for i in range(len(polygon))
    )
    if abs(area) < 1e-9:
        raise ValueError("the hatch polygon has no area")
    (angle, spacing) = _finite((hatch.angle, hatch.spacing), "hatch angle and spacing")
    if spacing <= 0:
        raise ValueError("hatch spacing must be positive")
    anchor = (
        polygon[0]
        if hatch.anchor is None
        else tuple(_finite(hatch.anchor, "hatch anchor"))
    )
    lines = _hatch_lines(polygon, angle, spacing, (anchor[0], anchor[1]))
    if not lines:
        raise ValueError("no hatch line crosses the polygon")
    radians = math.radians(angle)
    dx, dy = math.cos(radians), math.sin(radians)
    header = bytearray(32)
    header[4] = style.layer
    header[5] = 1
    header[12] = 0x40 if style.visible else 0
    header[14] = 2
    header[15] = 92
    struct.pack_into("<I", header, 16, entity_id)
    struct.pack_into("<H", header, 24, 24 + 32 * len(polygon))
    header[27] = 3
    header[28] = style.line_width
    header[29] = (style.line_style << 4) | style.color_index
    out = bytearray(header)
    out += struct.pack("<2d", anchor[0], anchor[1])
    for i, end in enumerate(polygon[1:] + polygon[:1]):
        start = polygon[i]
        out += struct.pack(
            "<4d",
            (start[0] + end[0]) / 2 - anchor[0],
            (start[1] + end[1]) / 2 - anchor[1],
            end[0] - anchor[0],
            end[1] - anchor[1],
        )
    origin = lines[0][0]
    values = [origin[0], origin[1], dx, dy, 0.0, 0.0]
    for index, (start, length) in enumerate(lines):
        if index:
            values += [start[0] - origin[0], start[1] - origin[1]]
        values.append(length)
    item = struct.pack("<HH", 8 + 8 * len(values), _HATCH_MARKER_WORD) + bytes(
        [style.line_width, (style.line_style << 4) | style.color_index, 0, 0]
    )
    out += item + struct.pack(f"<{len(values)}d", *_clean(tuple(values)))
    struct.pack_into("<I", out, 0, len(out))
    decoded = _hatch(bytes(out), order, 0)
    if decoded is None or decoded.box is None:
        raise _unsupported("write.hatch", None, "The hatch record does not read back")
    return bytes(out), decoded.box


_HATCH_MARKER_WORD = 0x0944


def _encode(
    order: str,
    primitive: Primitive2D,
    style: EntityStyle,
    entity_id: int,
    scale: float = 1.0,
) -> tuple[bytes, Extent]:
    """One qualified record: the layout the reader decodes, nothing invented."""
    values: tuple[float, ...]
    box: Extent
    if isinstance(primitive, Hatch2D):
        return _encode_hatch(order, primitive, style, entity_id)
    if isinstance(primitive, DiameterDimension2D):
        return _encode_diameter_dimension(order, primitive, style, entity_id, scale)
    if isinstance(primitive, LengthDimension2D):
        return _encode_length_dimension(order, primitive, style, entity_id, scale)
    if isinstance(primitive, Text2D):
        return _encode_text(order, primitive, style, entity_id, scale)
    if isinstance(primitive, Point2D):
        (x, y) = _finite(primitive.point, "point coordinates")
        size, kind, subtype, values = 56, 1, 1, (x, y)
        box = _box([(x, y)])
    elif isinstance(primitive, Line2D):
        sx, sy = _finite(primitive.start, "line coordinates")
        ex, ey = _finite(primitive.end, "line coordinates")
        length = math.hypot(ex - sx, ey - sy)
        if length <= 0:
            raise ValueError("a line needs distinct end points")
        size, kind, subtype = 72, 2, 2
        values = (sx, sy, (ex - sx) / length, (ey - sy) / length, length)
        box = _box([(sx, sy), (ex, ey)])
    elif isinstance(primitive, Circle2D):
        cx, cy = _finite(primitive.center, "circle center")
        (radius,) = _finite((primitive.radius,), "circle radius")
        if radius <= 0:
            raise ValueError("circle radius must be positive")
        size, kind, subtype = 72, 6, 4
        values = (cx, cy, radius, 0.0, math.tau)
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
    elif isinstance(primitive, Arc2D):
        cx, cy = _finite(primitive.center, "arc center")
        radius, start, sweep = _finite(
            (primitive.radius, primitive.start_angle, primitive.sweep_angle),
            "arc values",
        )
        if radius <= 0:
            raise ValueError("arc radius must be positive")
        if not 0 < abs(sweep) <= math.tau + 1e-12:
            raise ValueError("arc sweep must be nonzero and at most a full turn")
        size, kind, subtype = 72, 5, 5
        values = (cx, cy, radius, start, sweep)
        box = _box(_arc_points(cx, cy, radius, start, sweep))
    else:
        raise TypeError(
            "entities must be Point2D, Line2D, Circle2D, Arc2D, Text2D, "
            "LengthDimension2D, DiameterDimension2D or Hatch2D"
        )
    record = bytearray(size)
    struct.pack_into(order + "I", record, 0, size)
    record[4] = style.layer
    record[12] = 0x40 if style.visible else 0
    record[14] = 1
    record[15] = kind
    struct.pack_into(order + "I", record, 16, entity_id)
    struct.pack_into(order + "H", record, 24, size - 24)
    record[26] = 0x44
    record[27] = subtype
    record[28] = style.line_width
    record[29] = (style.line_style << 4) | style.color_index
    struct.pack_into(order + f"{len(values)}d", record, 32, *values)
    if kind == 1:
        # The constant tail of every saved point record: a 16-bit one in the
        # file's byte order and the byte 6, meaning unknown.
        struct.pack_into(order + "H", record, 48, 1)
        record[50] = 6
    return bytes(record), box


def _scale_text(scale: float) -> bytes:
    """``1/n`` or ``n/1`` as saved; other ratios are unobserved."""
    if scale <= 0 or not math.isfinite(scale):
        raise ValueError("scale must be a positive number")
    for n in range(1, 1000):
        if math.isclose(scale * n, 1, rel_tol=1e-9):
            return f"1/{n}".encode().ljust(8, b" ")
        if math.isclose(scale, n, rel_tol=1e-9):
            return f"{n}/1".encode().ljust(8, b" ")
    raise ValueError("scale must be 1/n or n/1 for an integer n below 1000")


class DrawingWriter:
    """Edit the 2D views of one document and serialize a copy.

    The writer reads the container, the view index and the saved 2D entities
    once. It needs a complete view index whose 2D views expose
    ``entity_count``, ``scale`` and a qualified extent. It never changes the
    document it was created from and never writes in place.
    """

    def __init__(self, document: Document):
        self._document = document
        self._order = "<" if document.header.byte_order == "little" else ">"
        self._container = document.container()
        self._added_views: list[str] = []
        self._added_entities = 0
        self._deleted_views: list[str] = []
        self._deleted_entities = 0
        self._notes: list[Diagnostic] = []
        self._view_delta = 0
        self._views: dict[str, _ViewState] = {}
        index = document.read_views()
        if index.status != "complete":
            raise _unsupported(
                "write.views", None, "The view index must be complete before writing"
            )
        drawing = document.read_drawing()
        by_offset = {r.byte_range.start: i for i, r in enumerate(document.records[2:])}
        ids = [_FIRST_ID - 1]
        for view in index.views:
            for entry in view.entries:
                if entry.kind == "entity":
                    ids.append(self._word(entry.byte_range.start + 16))
                elif entry.kind == "metadata" and entry.tag == 0x40000000:
                    ids.append(self._word(entry.byte_range.start + 20))
                elif entry.kind == "metadata" and entry.tag is None:
                    ids.append(self._word(entry.byte_range.start + 16))
                elif entry.kind == "metadata" and entry.tag == 0x21000000:
                    start, end = entry.byte_range.start, entry.byte_range.end
                    ids.extend(self._word(at) for at in range(start, end - 3, 4))
            if view.kind not in _TWO_D or view.header_range is None:
                continue
            name = (view.name or "").strip()
            if (
                view.entity_count is None
                or view.scale is None
                or view.extent_kind == "unqualified"
                or view.raw_entity_words is None
                or view.raw_view_number is None
                or not name
            ):
                raise _unsupported(
                    "write.view_fields",
                    view.byte_range.start,
                    f"View {name!r} lacks qualified header fields",
                )
            record_index = by_offset[view.byte_range.start]
            body = bytearray(self._container.records[record_index].body)
            base = view.byte_range.start + 8
            if self._unpack("I", body, len(body) - 4) != _TERMINATOR:
                raise _unsupported(
                    "write.terminator", view.byte_range.end - 8, "Unqualified view end"
                )
            entities = [e for e in drawing.entities if e.view_id == view.view_id]
            # Observed order: [entity group][0x40000000 placement list]; other
            # metadata lists (0x20000000) precede the entities.
            placement_tag: int | None = None
            placements: list[_Placement] = []
            placement_end: int | None = None
            for entry in view.entries:
                if entry.kind != "metadata":
                    continue
                offset = entry.byte_range.start - base
                length = entry.byte_range.length
                if entry.tag == _PLACEMENT_TAG and length == 268:
                    placement_tag = offset
                    placements.append(
                        _Placement(offset + 4, _placement_name(body, offset + 4))
                    )
                elif entry.tag is None and length == 264 and placement_tag is not None:
                    placements.append(_Placement(offset, _placement_name(body, offset)))
                if placement_tag is not None:
                    placement_end = offset + length
            entity_entries = [e for e in view.entries if e.kind == "entity"]
            if entity_entries:
                entity_end = max(e.byte_range.end for e in entity_entries) - base
            elif placement_tag is not None:
                entity_end = placement_tag
            else:
                entity_end = len(body) - 4
            extent = (
                (
                    view.extent.min_x,
                    view.extent.min_y,
                    view.extent.max_x,
                    view.extent.max_y,
                )
                if view.extent is not None
                else None
            )
            self._views[name] = _ViewState(
                index=record_index,
                name=name,
                kind=view.kind,
                number=view.raw_view_number,
                body=body,
                header=view.header_range.start - base,
                count=view.entity_count,
                group_words=view.raw_entity_words,
                has_marker=bool(entities),
                extent=extent,
                extent_known=all(_entity_box(e) is not None for e in entities),
                scale=view.scale,
                entities=[
                    _Entity(
                        self._word(e.byte_range.start + 16),
                        e.byte_range.start - base,
                        e.byte_range.length,
                        _entity_box(e),
                    )
                    for e in entities
                ],
                entity_end=entity_end,
                placement_tag=placement_tag,
                placements=placements,
                placement_end=entity_end if placement_end is None else placement_end,
            )
        self._next_id = max(i for i in ids if i >> 28 == 8) + 1

    def _word(self, at: int) -> int:
        return int.from_bytes(
            self._document.source_bytes(ByteRange(at, at + 4)),
            self._document.header.byte_order,
        )

    def _unpack(self, fmt: str, data: bytes | bytearray, at: int) -> int:
        return int(struct.unpack_from(self._order + fmt, data, at)[0])

    #: Every observed 2D view number lies in 1..6; higher numbers are refused.
    MAX_VIEW_NUMBER = _MAX_VIEW_NUMBER
    #: View names are stored in eight CP932 bytes.
    MAX_VIEW_NAME_BYTES = 8

    @property
    def views(self) -> tuple[str, ...]:
        """Names of the 2D views that can receive entities, in record order."""
        return tuple(
            s.name for s in sorted(self._views.values(), key=lambda s: s.index)
        )

    @property
    def view_capacity(self) -> int:
        """How many more views ``add_view`` can number within the observed range."""
        used = max((s.number for s in self._views.values()), default=0)
        return max(0, _MAX_VIEW_NUMBER - used)

    def add_view(
        self,
        name: str,
        *,
        scale: float = 1.0,
        template: str | None = None,
        origin: tuple[float, float] = (0.0, 0.0),
    ) -> str:
        """Add an empty 2D view cloned from an existing one; returns its name.

        The prefix and header of the template (the first ``2d_view`` by
        default, else the ``2d_global`` view) are copied; name, number, count,
        scale, scale text and extent are set. The number is the next unused
        one; numbers above 6 are unobserved and refused. The 2D global view
        receives a placement record for the view (its origin on the sheet in
        mm, its scale and its name), cloned from the last existing one; when
        the global view stores no placement list, the result reports
        ``write.placement`` instead.
        """
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        placed = _finite(origin, "origin")
        if len(placed) != 2:
            raise ValueError("origin must be (x, y)")
        raw_name = name.encode("cp932")
        if not raw_name.strip() or len(raw_name) > 8 or raw_name != raw_name.strip():
            raise ValueError(
                "name must be 1 to 8 CP932 bytes without surrounding spaces"
            )
        if name in self._views:
            raise ValueError(f"view {name!r} already exists")
        if template is None:
            candidates = [s for s in self._views.values() if s.kind == "2d_view"] or [
                s for s in self._views.values() if s.kind == "2d_global"
            ]
            if not candidates:
                raise _unsupported(
                    "write.template", None, "No 2D view to clone the new view from"
                )
            source = min(candidates, key=lambda s: s.index)
        else:
            source = self._views[template]
        number = max(s.number for s in self._views.values()) + 1
        if number > _MAX_VIEW_NUMBER:
            raise _unsupported(
                "write.view_number", None, f"View number {number} is unobserved"
            )
        text = _scale_text(float(scale))
        header = source.header
        body = bytearray(source.body[: header + 240])
        body += struct.pack(self._order + "I", _TERMINATOR)
        struct.pack_into(self._order + "I", body, 16, 0)
        body[header : header + 4] = b"\x10\0\0\0"
        body[header + 8 : header + 16] = raw_name.ljust(8, b" ")
        struct.pack_into(self._order + "H", body, header + 26, number)
        struct.pack_into(self._order + "I", body, header + 28, 0)
        struct.pack_into(self._order + "d", body, header + 32, float(scale))
        struct.pack_into(self._order + "4f", body, header + 40, *_EMPTY_EXTENT)
        body[header + 96 : header + 104] = text
        # Insert after the last 2D view record, before any 3D view.
        position = max(s.index for s in self._views.values()) + 1
        records = list(self._container.records)
        records.insert(
            position, ContainerRecord("V/W", len(body) // 4 + 3, bytes(body))
        )
        view_position = sum(1 for r in records[:position] if r.tag == "V/W")
        names = list(self._container.view_names)
        names.insert(view_position, raw_name.ljust(8, b" "))
        self._container = replace(
            self._container,
            records=tuple(records),
            view_names=tuple(names),
            diagnostics=(),
        )
        for state in self._views.values():
            if state.index >= position:
                state.index += 1
        self._view_delta += 1
        self._views[name] = _ViewState(
            position,
            name,
            "2d_view",
            number,
            body,
            header,
            0,
            0,
            False,
            None,
            True,
            float(scale),
            [],
            header + 240,
            None,
            [],
            header + 240,
        )
        self._place_view(name, raw_name, float(scale), (placed[0], placed[1]))
        self._added_views.append(name)
        return name

    def add_entities(
        self,
        view: str,
        entities: Iterable[Primitive2D],
        *,
        style: EntityStyle | None = None,
    ) -> tuple[int, ...]:
        """Append entity records to a 2D view; returns the assigned IDs."""
        if style is None:
            style = EntityStyle()
        if not isinstance(style, EntityStyle):
            raise TypeError("style must be an EntityStyle")
        state = self._views[view]
        records = []
        boxes: list[Extent | None] = []
        for primitive in entities:
            record, box = _encode(
                self._order, primitive, style, self._next_id, state.scale
            )
            records.append((self._next_id, record))
            boxes.append(box)
            self._next_id += 1
        self._append(state, [r for _, r in records], boxes)
        return tuple(i for i, _ in records)

    def add_raw_entity(self, view: str, record: bytes, *, keep_id: bool = False) -> int:
        """Append a saved entity record verbatim (for example a dimension
        record copied from another document of the same version). Its ID is
        reassigned unless ``keep_id``. A text record with a qualified layout
        contributes its box to the view's extent; for other records the
        extent is left as stored, since their own extent is not derived."""
        if not isinstance(record, bytes):
            raise TypeError("record must be bytes")
        if (
            len(record) < 32
            or len(record) % 4
            or self._unpack("I", record, 0) != len(record)
            or self._unpack("I", record, 16) >> 28 != 8
        ):
            raise ValueError("record must be a framed 2D entity record")
        state = self._views[view]
        entity_id = self._unpack("I", record, 16)
        if keep_id:
            # Later IDs must stay unique after a kept one.
            self._next_id = max(self._next_id, entity_id + 1)
        else:
            entity_id = self._next_id
            self._next_id += 1
            data = bytearray(record)
            struct.pack_into(self._order + "I", data, 16, entity_id)
            record = bytes(data)
        box: Extent | None = None
        if record[15] == 21:
            text = _text(record, self._order, DrawingLimits())
            if text is not None and text.box is not None:
                box = text.box
        elif record[15] in (25, 26, 27):
            dimension = _dimension(record, self._order, 0)
            if dimension is not None and dimension.layout_status == "complete":
                box = dimension.box
        elif record[15] == 92:
            hatch = _hatch(record, self._order, 0)
            if hatch is not None and hatch.layout_status == "complete":
                box = hatch.box
        self._append(state, [record], [box])
        return entity_id

    def set_extent(self, view: str, extent: Extent | None) -> None:
        """Override the saved extent (``None`` writes the empty sentinel)."""
        state = self._views[view]
        if extent is None:
            state.extent = None
        else:
            values = _finite(extent, "extent")
            if len(values) != 4 or values[0] > values[2] or values[1] > values[3]:
                raise ValueError("extent must be (min_x, min_y, max_x, max_y)")
            state.extent = (values[0], values[1], values[2], values[3])
        state.extent_known = True
        self._write_extent(state)

    def _shift(self, state: _ViewState, at: int, delta: int) -> None:
        """Move placement bookkeeping at or after ``at`` by ``delta`` bytes."""
        for placement in state.placements:
            if placement.offset >= at:
                placement.offset += delta
        if state.placement_tag is not None and state.placement_tag >= at:
            state.placement_tag += delta
        if state.placement_end >= at:
            state.placement_end += delta

    def _place_view(
        self, name: str, raw_name: bytes, scale: float, origin: tuple[float, float]
    ) -> None:
        sheet = next((s for s in self._views.values() if s.kind == "2d_global"), None)
        if sheet is None or sheet.placement_tag is None or not sheet.placements:
            self._notes.append(
                Diagnostic(
                    "unsupported",
                    "write.placement",
                    None,
                    f"View {name!r} has no placement record: the 2D global view "
                    "stores no placement list to clone",
                )
            )
            return
        source = sheet.placements[-1]
        record = bytearray(sheet.body[source.offset : source.offset + 264])
        struct.pack_into(self._order + "I", record, 16, self._next_id)
        self._next_id += 1
        struct.pack_into(self._order + "3d", record, 40, origin[0], origin[1], 0.0)
        struct.pack_into(self._order + "d", record, 64, scale)
        record[136:144] = raw_name.ljust(8, b" ")
        struct.pack_into(
            self._order + "6f", record, 176, 1e38, 1e38, 0.0, -1e38, -1e38, 0.0
        )
        at = sheet.placement_end
        sheet.body[at:at] = record
        sheet.placements.append(_Placement(at, name))
        sheet.placement_end += 264
        self._count_placements(sheet, 1)

    def _count_placements(self, sheet: _ViewState, delta: int) -> None:
        """The global view header word +2 counts its placement records."""
        at = sheet.header + 2
        value = self._unpack("H", sheet.body, at) + delta
        struct.pack_into(self._order + "H", sheet.body, at, max(0, value))

    def _append(
        self, state: _ViewState, records: list[bytes], boxes: list[Extent | None]
    ) -> None:
        added = b"".join(records)
        at = state.entity_end
        offset = at
        if not state.has_marker:
            added = struct.pack(self._order + "I", _GROUP_MARKER) + added
            state.has_marker = True
            offset += 4
        state.body[at:at] = added
        self._shift(state, at, len(added))
        state.entity_end += len(added)
        for record, box in zip(records, boxes, strict=True):
            state.entities.append(
                _Entity(self._unpack("I", record, 16), offset, len(record), box)
            )
            offset += len(record)
        state.group_words += len(added) // 4
        struct.pack_into(self._order + "I", state.body, 16, state.group_words)
        state.count += len(records)
        struct.pack_into(self._order + "I", state.body, state.header + 28, state.count)
        for box in boxes:
            if box is None:
                state.extent_known = False
            else:
                state.extent = _union(state.extent, box)
        self._write_extent(state)
        self._added_entities += len(records)

    def entity_ids(self, view: str) -> tuple[int, ...]:
        """IDs of the entity records of a 2D view, in record order."""
        return tuple(e.entity_id for e in self._views[view].entities)

    def delete_entities(self, view: str, entity_ids: Iterable[int]) -> int:
        """Remove entity records from a 2D view; returns how many were removed.

        The record words, the entity count and the extent change the way
        iCAD's own deletions change them: the extent is recomputed from the
        remaining entities when every one of them has a derived box,
        otherwise the stored extent is kept and the result reports
        ``write.extent_kept``. Unknown IDs raise ``KeyError``.
        """
        state = self._views[view]
        wanted: set[int] = set()
        for entity_id in entity_ids:
            if isinstance(entity_id, bool) or not isinstance(entity_id, int):
                raise TypeError("entity IDs must be integers")
            wanted.add(entity_id)
        present = {e.entity_id for e in state.entities}
        missing = sorted(wanted - present)
        if missing:
            raise KeyError(missing[0])
        victims = [e for e in state.entities if e.entity_id in wanted]
        if not victims:
            return 0
        first = min(e.offset for e in state.entities)
        for victim in sorted(victims, key=lambda e: e.offset, reverse=True):
            del state.body[victim.offset : victim.offset + victim.length]
            for other in state.entities:
                if other.offset > victim.offset:
                    other.offset -= victim.length
            self._shift(state, victim.offset + 1, -victim.length)
            state.entity_end -= victim.length
            state.group_words -= victim.length // 4
        state.entities = [e for e in state.entities if e.entity_id not in wanted]
        state.count -= len(victims)
        if (
            not state.entities
            and state.has_marker
            and self._unpack("I", state.body, first - 4) == _GROUP_MARKER
        ):
            # An empty view stores no entity group marker.
            del state.body[first - 4 : first]
            self._shift(state, first, -4)
            state.entity_end -= 4
            state.group_words -= 1
            state.has_marker = False
        struct.pack_into(self._order + "I", state.body, 16, state.group_words)
        struct.pack_into(self._order + "I", state.body, state.header + 28, state.count)
        boxes = [e.box for e in state.entities]
        if all(box is not None for box in boxes):
            extent: Extent | None = None
            for box in boxes:
                extent = _union(extent, box)
            state.extent = extent
            state.extent_known = True
        else:
            state.extent_known = False
        self._write_extent(state)
        self._deleted_entities += len(victims)
        return len(victims)

    def clear_view(self, view: str) -> int:
        """Remove every entity record of a 2D view; returns how many."""
        return self.delete_entities(view, self.entity_ids(view))

    def delete_view(self, name: str) -> None:
        """Remove a ``2d_view`` record and its directory entry.

        The 2D global view and the last remaining 2D view cannot be removed.
        The numbers of the remaining views are kept as stored: renumbering
        after a deletion is unobserved. The view's placement record leaves the
        2D global view with it, and when RES names the view as the active one
        it is retargeted to a remaining view (``write.active_view``).
        """
        state = self._views[name]
        if state.kind != "2d_view":
            raise _unsupported(
                "write.view_kind",
                None,
                f"View {name!r} is a {state.kind} view and cannot be removed",
            )
        if not any(
            s.kind == "2d_view" and s is not state for s in self._views.values()
        ):
            raise _unsupported(
                "write.view_last",
                None,
                "Every observed document keeps at least one 2D view; "
                f"{name!r} is the last one",
            )
        position = state.index
        records = list(self._container.records)
        del records[position]
        view_position = sum(
            1 for r in self._container.records[:position] if r.tag == "V/W"
        )
        names = list(self._container.view_names)
        del names[view_position]
        self._container = replace(
            self._container,
            records=tuple(records),
            view_names=tuple(names),
            diagnostics=(),
        )
        del self._views[name]
        self._view_delta -= 1
        for other in self._views.values():
            if other.index > position:
                other.index -= 1
        if name in self._added_views:
            self._added_views.remove(name)
        else:
            self._deleted_views.append(name)
        self._release_active_view(name)
        sheet = next((s for s in self._views.values() if s.kind == "2d_global"), None)
        hit = (
            next((p for p in sheet.placements if p.name == name), None)
            if sheet is not None
            else None
        )
        if sheet is None or hit is None:
            self._notes.append(
                Diagnostic(
                    "unsupported",
                    "write.placement",
                    None,
                    f"View {name!r} had no placement record to remove",
                )
            )
            return
        start, length = hit.offset, 264
        if (
            len(sheet.placements) == 1
            and sheet.placement_tag is not None
            and hit.offset == sheet.placement_tag + 4
        ):
            # The list tag goes with its only record.
            start, length = sheet.placement_tag, 268
            sheet.placement_tag = None
        del sheet.body[start : start + length]
        sheet.placements.remove(hit)
        self._shift(sheet, start + 1, -length)
        self._count_placements(sheet, -1)

    def _release_active_view(self, name: str) -> None:
        """RES names the view that was active when iCAD saved; retarget it."""
        raw = name.encode("cp932").ljust(8, b" ")
        for index, record in enumerate(self._container.records):
            if record.tag != "RES" or record.body.count(raw) != 1:
                continue
            remaining = sorted(self._views.values(), key=lambda s: s.index)
            target = next(
                (s for s in remaining if s.kind == "2d_view"),
                next((s for s in remaining if s.kind == "2d_global"), None),
            )
            if target is None:
                return
            replacement = target.name.encode("cp932").ljust(8, b" ")
            self._container = self._container.with_record(
                index, record.with_body(record.body.replace(raw, replacement))
            )
            self._notes.append(
                Diagnostic(
                    "unsupported",
                    "write.active_view",
                    None,
                    f"RES named the removed view {name!r} as the active view; "
                    f"it now names {target.name!r}",
                )
            )

    def _write_extent(self, state: _ViewState) -> None:
        values = state.extent if state.extent is not None else _EMPTY_EXTENT
        struct.pack_into(self._order + "4f", state.body, state.header + 40, *values)

    def to_bytes(self) -> bytes:
        """Serialize the edited copy; unchanged writers reproduce their input."""
        records = list(self._container.records)
        for state in self._views.values():
            body = bytes(state.body)
            records[state.index] = ContainerRecord("V/W", len(body) // 4 + 3, body)
        mod = bytearray(self._container.mod_record)
        if self._view_delta:
            # MOD +232 counts the views that are not 3D views, +234 the 3D views.
            count = self._unpack("H", mod, 232) + self._view_delta
            struct.pack_into(self._order + "H", mod, 232, max(0, count))
        return replace(
            self._container, records=tuple(records), mod_record=bytes(mod)
        ).to_bytes()

    def write(self, path: str | Path) -> WriteResult:
        """Write the copy to a new file; existing paths are refused."""
        payload = self.to_bytes()
        target = Path(path)
        with open(target, "xb") as handle:
            handle.write(payload)
        return self._result(payload, target)

    def result(self) -> WriteResult:
        """The serialized copy without writing a file."""
        return self._result(self.to_bytes(), None)

    def _result(self, payload: bytes, path: Path | None) -> WriteResult:
        diagnostics = tuple(
            Diagnostic(
                "unsupported",
                "write.extent_kept",
                None,
                f"View {s.name!r} keeps its stored extent: it holds entities "
                "whose extent is not derived",
            )
            for s in self._views.values()
            if not s.extent_known
        ) + tuple(self._notes)
        return WriteResult(
            payload,
            path,
            self._document.source_sha256,
            sha256(payload).hexdigest(),
            tuple(self._added_views),
            self._added_entities,
            diagnostics=diagnostics,
            deleted_views=tuple(self._deleted_views),
            deleted_entities=self._deleted_entities,
        )
