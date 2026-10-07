# Views and saved 2D entities

Version 0.3.5 adds `Document.read_views()` and `Document.read_drawing()`.
Both read originals directly, including registered part files. No iCAD process,
resave, optional geometry package or schema catalog is needed.

```python
import icadkit

doc = icadkit.read("drawing.icd")
views = doc.read_views()
print(views.document_kind, views.profile_id, views.status)
for view in views.views:
    print(view.name, view.kind, view.status, len(view.entries))

drawing = doc.read_drawing()
for entity in drawing.entities:
    print(entity.view_id, entity.raw_type, entity.status)
    if entity.primitive:
        print(entity.primitive)
    if entity.text:
        print(entity.text.lines, entity.text.layout_status)
    # Retained source, including unsupported attributes and glyph records:
    raw = doc.source_bytes(entity.byte_range)
```

## Qualified formats and classification

Observed big-endian profiles cover V1L9, V3L1/L2/L3/L4/L5/L7/L8/L9,
V5L1/L2/L3/L4, V6L1/L2 and V7L1. Little-endian profiles cover V7L2–V7L7
and V8L1–V8L3. The counted view header and record boundaries must also match;
a version label alone does not establish support. V3L6 and V8L4 are not qualified.
Numeric values follow the file byte order. Packed attribute bytes have separate
rules and cannot be decoded by swapping the entire file.

`ViewIndex.document_kind` is `document`, `registered_part` or `unknown`.
Classification uses qualified global views or the dedicated `@BUHIN` view;
paths and extensions are not evidence of file kind. Inconsistent/unknown view
headers keep classification unknown. A registered part remains a registered part
even though the SDK can insert it into a new document.

`View` retains its name bytes, decoded CP932 name (or `None`), raw view number,
directory range, header range, record entries, opaque ranges and diagnostics.
For the 2D kinds (`2d_global`, `2d_view`, `registered_part`) it also qualifies
three saved header fields: `entity_count` (header word `+28`, accepted only
when it equals the number of entity records), `scale` (the double at `+32`,
accepted when finite and positive; `raw_scale_text` keeps the eight bytes at
`+96` such as `1/1`) and `extent` (four floats at `+40`: min x, min y, max x,
max y in view-local millimetres). `extent_kind` is `box`, `empty` for the
saved sentinel of a view without extent (minima `+1e38`, maxima `-1e38`) or
`unqualified`. A deviating value keeps its field `None`, adds a
`views.entity_count`, `views.scale` or `views.extent` diagnostic and leaves the
view partial without stopping traversal. `raw_entity_words` retains the record
word `+24` without an assigned meaning: it equals the entity-group size in
words for 3,734 of the 4,050 qualified 2D views of the local corpus and
differs for the rest. 3D views keep all of these fields unqualified, since
their header stores other values at the same offsets.
Kinds are `2d_global`, `2d_view`, `registered_part`, `3d_global` and `unknown`.
`ViewEntry` identifies an entity, part, legacy owner or metadata record. An entity's
`owner_offset` is the preceding framed part/legacy owner when present; otherwise
it is `None`. This field does not invent a resolved 3D part or definition.
All ranges are half-open offsets in the original snapshot. IDs containing byte
offsets identify that snapshot only. Stored numeric IDs are not persistent CAD IDs.

`ViewIndex.status=complete` means that all selected view headers and record
boundaries were traversed. It does **not** mean their contents are understood.
Unknown records stop traversal; the remaining range is retained without scanning
for a plausible next entity. A missing view is not interpreted as an empty drawing.

A qualified `40000000` metadata group can contain repeated 264-byte
view-control records, with the tag present only on the first record. Each
continuation must match the fixed length and internal markers. Records retain
their exact ranges as `metadata`, without geometry or part ownership. This
qualifies traversal only; projection-control semantics remain unavailable.

Big-endian V5L1/V5L3 3D views also admit the observed `50000001` group of
112/192-byte records. The first record carries the tag; subsequent records must
match the qualified length and internal marker at the next exact boundary.
Their numeric links and optional name bytes remain opaque `metadata` with no
owner or geometry assigned. Local checks traverse the ten previously stopped
files; this does not qualify legacy assembly or external-reference semantics.

## Drawing scope

`DrawingIndex` contains the view inventory and bounded 2D `DrawingEntity` records.
3D entities remain outside this API; 2D entities are not inserted into `read_parts()`.
Each entity has a view ID, source range, stored ID/type, qualified layer/visibility
and line width/style/palette index, decoded geometry or text, and diagnostics.
Layer values outside the observed one-byte layout remain unqualified.

| Saved entity | Exposed values | Boundary |
| --- | --- | --- |
| Point | One `(x, y)` point, including hidden points | Point symbol/style remains in source bytes |
| Line | Start/end, direction and length | Zero-length lines retain their direction |
| Circle | Center and positive radius | Saved planar circles only |
| Arc | Center, radius, start angle and signed sweep | Angles are radians; sweep is not an end angle |
| Text | Counted UTF-16LE runs in little-endian V7L6/V7L7/V8L1/V8L2/V8L3, including multiline and private-use characters, with the saved layout below | Rotated text keeps its angle without an anchor or box; fonts, glyph strokes and tilt are not interpreted |
| Length dimension | The saved layout: measured points, dimension line, line point, value text run, arrowheads and aux lines as saved items, the measured value and the arrow parameters | Tolerances, extra words and other styles are unobserved |
| Diameter and radius dimension | The measured circle or arc (centre, radius), the pick point, the dimension line, the line point, the value text run and the arrowheads; `radius` when the value equals the radius | Leader variants and angle dimensions keep their framed items, arc and text with a partial status |
| Hatch | The anchor, the boundary edges (start, middle, end; line or arc) and every rendered line with its direction and spacing | Patterns other than parallel lines, dashed lines and point lists that do not close keep the layout partial |
| Symbols, projection controls and other types | Raw type, source range and diagnostics | No decomposition into guessed primitives |

`DrawingText` qualifies the saved text layout of the observed little-endian
record: `base_point` (1 top-left, 5 centre, 7 bottom-left; columns 1/4/7,
2/5/8, 3/6/9 and rows 1-3, 4-6, 7-9), `direction` (`horizontal` or
`vertical`), the raw `way` code, the character `height`, `char_width` and
`char_pitch`, one origin per line (the bottom-left of its first character
cell) and `scale_factor`, the view scale when the text was created. Metrics
and origins are view-local: the SDK reports the character height multiplied
by the view scale (the paper height). From these
it derives the cell-based `box`, in which ASCII and half-width katakana take
half a cell, and the `anchor` of the base point on that box. In the local
corpus, 183 text entities with SDK observations match their anchor and extent
this way. Rotated text keeps its `rotation` in degrees with a
`drawing.text_rotation` diagnostic and no anchor or box; unobserved fixed
words, base points, directions, metrics or an unframed stroke-glyph cache keep
the layout `partial` with `drawing.text_fields`, `drawing.text_base_point`,
`drawing.text_direction`, `drawing.text_metrics` or `drawing.text_glyphs`.
The glyph cache that some saved records carry after each line is framed but
not interpreted (`glyph_cache`); other saved records omit it. A complete layout
makes the text entity `complete`.

`DrawingDimension` frames every little-endian dimension record (types 25,
26 and 27) as attribute blocks, the embedded value text run and geometry
`items`: ``vectors`` items (a point and two vectors), ``line`` items,
``point`` items and ``arc`` items (centre, radius, start and sweep angle)
with their saved width, style and colour; the run may sit between items. The length layout
(type 25) is interpreted when its items follow the observed sequence: two
arrowheads, the dimension `line`, the `line_point` and the text anchor, two
aux lines and the two `measured` points. `value` is the measured length,
`gap` the text offset and `scale_factor` the view scale when the dimension
was created (both paper millimetres), `arrow_width` and `arrow_angle` the
arrowhead parameters, `underline` the two saved underline lengths, `extension` the aux line
overshoot and `aux_offset` the distance from a measured point to the start
of its aux line, both in view units. `box` is the extent of all
items and of the unrotated text. In the local corpus the measured points,
line point, value and text of 126 length dimensions agree with their SDK
observations. The diameter family (type 27) is interpreted when its items
follow the observed sequence: a quarter arc, the measured circle or arc
(`center`, `radius`), the `pick_point`, the value text run, two arrowheads,
the dimension `line`, the `line_point` and the text anchor; `kind` is
`radius` when `value` equals the radius and `diameter` when it equals the
diameter (`drawing.dimension_value` otherwise). In the local corpus the
centre and value of 43 such records and the line point of the 38 with the
observed sequence agree with their SDK observations; the text includes the
R or diameter symbol that the SDK reports separately. Angle dimensions,
leader variants with a second text run, unobserved item sequences, other
items and unqualified text runs keep the entity `partial` with
`drawing.dimension_kind`, `drawing.dimension_layout`,
`drawing.dimension_runs`, `drawing.dimension_item` or
`drawing.dimension_text`.

`DrawingHatch` reads the hatch record (type 92): an `anchor`, the boundary
as a point list relative to it (`raw_points`, interpreted as `edges` of
start, middle and end when the list closes; a middle point off the chord
marks an arc) and the rendered lines (`origin`, unit `direction`, `angle`,
`segments`), with `spacing` when the lines lie on a regular lattice. `box` is
the extent of the segments and edges. In the local corpus all 123 hatch
records frame this way and 13 with SDK observations agree in angle and
spacing. Point lists that do not close, unobserved item words or nonpositive
lengths keep the entity `partial` with `drawing.hatch_boundary` or
`drawing.hatch_fields`.

Geometry uses **view-local millimetres**. It does not apply paper placement,
view rotation, scale or a 3D projection transform. Saved line/circle records in
a projected view can be read independently of the unimplemented projection
relationship. Fonts, glyph outlines, tilt and legacy CP932 text runs remain unqualified;
rotated text and unobserved layouts give a `partial` entity.

Nonfinite coordinates, invalid radii/directions and out-of-bound record lengths
produce `invalid` diagnostics. Unsupported layouts remain `unsupported`, with
their original bytes accessible. There is no 2D renderer or sheet export in
this stage. The existing 3D viewer still rejects unqualified zero-part models.

## Limits and CLI

`ViewLimits` defaults to 10,000 views and 500,000 records across the document.
`DrawingLimits` defaults to 4 MiB per entity and 1 MiB of decoded text input
per entity. `ReadLimits` continues to bound input and copied source ranges.
Policies require positive integers; exceeded limits raise `LimitExceededError`.

```sh
icadkit views drawing.icd --json
icadkit drawing drawing.icd --json
icadkit drawing drawing.icd --max-view-records 100000 --max-entity-bytes 1048576
```

CLI JSON has `schema_version=1`, `operation`, `source` and `result` or `error`.
Byte values use hex strings, declared by `raw_bytes_encoding=hex`.
Exit codes are 0 for complete scope, 3 for partial/unsupported, 1 for invalid
input/I/O failure, 4 for a resource limit and 2 for argument errors.

## Local evidence

Classification matches SDK observations for 38 legacy originals across 21 raw
versions: 24 documents and 14 registered parts. Geometry/content comparison uses
108 saved SDK observations, including 15 additional original files selected before
their geometry was decoded, and eight new saved/reopened iCAD cases. The checked
1,759 entities include lines, circles, arcs, points and text. SDK null/error lists
remain explicitly unverified; they are not counted as empty-list matches.

The installed corpus scan covers 1,018 big-endian originals: 964 have complete
view framing, 54 remain partial, and three cannot be classified. This is inventory
coverage, not full drawing/model support. All 45 V8L1 inputs without a qualified
3D global view have readable view framing; their drawing results remain partial.
Validation used iCAD V8L3-09A to read old originals, not historical executables.
Windows/macOS wheels and remote CI have not been rerun for this change.
