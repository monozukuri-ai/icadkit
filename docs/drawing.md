# Views and saved 2D entities

`Document.read_views()` and `Document.read_drawing()` read originals directly,
including registered part files. No iCAD process,
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

## View placement on the sheet

The 2D global view stores one fixed-length control record per 2D view after
its entity group: a `0x40000000` marker followed by 264-byte records. The
reader decodes them as `ViewPlacement` (the `placements` of the `ViewIndex`)
and attaches each record to the `2d_view` whose name it carries
(`View.placement`). Nothing is applied to the entities: `read_drawing()` keeps
view-local millimetres, and a caller composes the sheet by scaling a view's
geometry by `placement.scale` and translating it to `placement.origin`.

| Field | Record offset | Meaning |
| --- | --- | --- |
| `source_id` | `+16` (u32) | The saved record identifier |
| `origin` | `+40`, `+48` (two doubles) | The view origin on the sheet, in millimetres of the 2D global view; the third double at `+56` is retained raw |
| `scale` | `+64` (double) | The view scale; it equals the view header's `scale` in every observed file |
| `raw_name` / `name` | `+136` (8 bytes, CP932) | The 2D view the record belongs to |

Numeric values follow the file byte order. The length word at `+0`, a finite
origin and a positive scale make a record `complete`; a deviating value keeps
the record with `views.placement` diagnostics and `status="invalid"`, and an
invalid scale is reported as `None`. The other words (`+12`, `+24`–`+40`,
`+72`–`+136` and `+144`–`+264`, including the six floats at `+176` that are not
a view extent in saved files) retain their bytes without an assigned meaning.
Records that name no 2D view add `views.placement_view`, several records with
one name add `views.placement_ambiguous` and map to no view, and a 2D view
without a record adds `views.placement_missing`; these diagnostics do not
change the index status, which describes framing only. `DrawingWriter` writes
the same record for each view it adds. Its
entities also include `Ellipse2D`, `EllipticalArc2D`, `Curve2D` (the
segment words are written as zeros), `LeaderNote2D` and
`AngleDimension2D` (little-endian documents only for the two composite
records, whose attribute blocks copy an observed record with the scale,
height, arrow and value doubles patched).

In the installed corpus (1,449 distinct files), 701 documents have a 2D global
view (316 big-endian, 385 little-endian), all of them with a placement list:
818 records are complete, 817 of the 855 2D views have a record and every one
of those agrees with the view header's scale; 38 views have no record and one
record names no 2D view.

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
| Ellipse and elliptical arc (types 88, 89) | Center, the semi-axes (`radius` along the ellipse's x axis, `minor_radius`), the `rotation` of that axis and the parametric start angle and signed sweep, in both byte orders | A point is `center + R(rotation) * (a cos t, b sin t)`, the DXF convention; the 448-byte records of V5 and earlier are not interpreted |
| Free curve (type 16) | `DrawingCurve`: the knots (start point and cumulative offsets), their chord lengths, the total length and the saved extent, in both byte orders | The six doubles saved per segment are retained as `segment_words` without a meaning; a caller draws through the knots |
| Leader note (type 22) | `DrawingNote`: the text runs (one per line), the arrowhead (a corner, the tip, the other corner), the drawn leader (tip, elbow, end) and the arrow parameters | Hidden alternative items are retained in `items`; little-endian V7L6 and later only |
| Symbol (type 212) | `DrawingSymbol`: drawn point markers, lines, arcs and vectors, the text runs (UTF-16LE or CP932) and the nested member list of a group as opaque ranges; `raw_kind` is the saved byte at `+31` (1 point markers, 50 labels) | A run that does not decode is skipped with `drawing.symbol_text` |
| Line information (type 224) | `DrawingSymbol` with the saved `name` (`Line`, `Junction`, `Connection Mark`, symbol names of P&ID and circuit drawings): the drawn lines, circles (marker `0x0444`) and text runs of the framed item stream | Records without a framed item stream (cable names, tables) are attribute records (`drawing.attribute_record`) |
| Point with attributes (type 1, 128 bytes) | The point; the appended attribute record is retained | Connection points of P&ID symbols |
| Motion verification (types 230–234) | Raw type, source range and `drawing.attribute_record` | Not drawing geometry |
| Text | Counted runs with the saved layout below: UTF-16LE in little-endian V7L6 and later, legacy CP932 runs (flag byte `0x40`, one or two bytes per character) in big-endian documents and little-endian V7L2–V7L5, including multiline and private-use characters | Rotated text keeps its angle without an anchor or box; fonts, glyph strokes and tilt are not interpreted |
| Length dimension | The saved layout: measured points, dimension line, line point, value text run, arrowheads and aux lines as saved items, the measured value and the arrow parameters | Tolerances, extra words and other styles are unobserved |
| Diameter and radius dimension | The measured circle or arc (centre, radius), the pick point, the dimension line, the line point, the value text run and the arrowheads; `radius` when the value equals the radius. The leader variant adds `leader` (the arrowhead tip, the elbow and the end of the shelf) | Other item sequences keep their framed items, arc and text with a partial status |
| Angle dimension (type 26) | The vertex (`center`), the drawn dimension arc (`radius`, `arc_start`, `arc_sweep`), the two `measured` points on the legs, the line point, the value in degrees and the value text run | Unobserved item sequences keep their items with a partial status |
| Hatch | The anchor, the boundary edges (start, middle, end; line or arc) and every rendered line with its direction and spacing | Patterns other than parallel lines, dashed lines and point lists that do not close keep the layout partial |
| Other types | Raw type, source range and diagnostics | No decomposition into guessed primitives |

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
not interpreted (`glyph_cache`); other saved records omit it, and the cache
of legacy CP932 runs is retained without framing. A complete layout
makes the text entity `complete`.

Composite records (dimensions, leader notes and symbols) share one framing:
0xFF attribute blocks, then size-framed text runs (marker bytes `44 20`) and
geometry items in saved order. Sizes follow the file byte order; the two
marker bytes do not swap. The framing is the same in big-endian documents
(V1L9–V7L1) and in little-endian V7L2–V7L5, whose runs are CP932 rather than
UTF-16LE; the local corpus reads 3,344 big-endian length, 624 angle and
1,718 diameter dimensions this way, their measured distances agreeing with
the value text. Items whose marker carries the `0x40` bit are
drawn; the others are hidden alternatives that iCAD keeps (the mirrored
arrowhead of a leader, the second aux line of an angle dimension).

`DrawingDimension` frames every dimension record (types 25, 26 and 27) as
attribute blocks, the embedded value text run and geometry `items`: ``vectors`` items (a point and two vectors), ``line`` items,
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
R or diameter symbol that the SDK reports separately. A diameter dimension
on an arc stores three arcs (a quarter arc, the drawn extension arc and the
measured arc) before the pick point and is interpreted too. The leader variant of
the diameter family (a quarter arc, the measured circle, the pick point, the
drawn arrowhead and leader, two hidden arrowheads, a hidden zero-length line
and two points) is interpreted with `leader` = (tip, elbow, end) and `line`
= the shelf. The angle layout (type 26: two arrowheads, three copies of the
dimension arc of which the `0x0544` one is drawn, the line point, the text
anchor, then per leg an aux line and the measured point, and the vertex)
gives `center`, `radius`, `arc_start`, `arc_sweep`, `measured` and the
`value` in degrees; the two angle records of the local corpus read this
way. Unobserved item sequences, additional text runs, other items and
unqualified text runs keep the entity `partial` with
`drawing.dimension_kind`, `drawing.dimension_layout`,
`drawing.dimension_runs`, `drawing.dimension_item` or
`drawing.dimension_text`.

`DrawingNote` reads the leader note record (type 22): the text runs (one
per line), one drawn `vectors` item whose legs are symmetric about its tip
(the `arrow`) and one drawn `vectors` item from the tip to the elbow and the
end of the shelf (the `path`); hidden items, including a 40-byte variant
with a single vector, are retained in `items`. The arrow width and angle
come from attribute block 4 as in dimensions. `DrawingSymbol` reads the
symbol record (type 212, any `raw_kind`): its items (point markers with the
`0x0144` marker, lines, arcs, vectors), its text runs and the nested
member lists of groups (marker bytes `00 fd`) as opaque `nested` ranges.
In the local corpus 523 free curves, 1,468 elliptical arcs, 4 leader notes
and 44 symbols of one V8L2 assembly drawing, and 228 free curves of each
V6/V7 gearbox drawing, read `complete`; their endpoints coincide with the
ends of neighbouring lines, arcs and curves.

`DrawingText` also accepts the 88-byte attribute block that some V8 labels
(line information, P&ID) store instead of the 48-byte block; the runs
follow the block and the fixed words of the extended block are not
compared, so these texts read `complete` unless rotated.

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

Geometry uses **view-local millimetres**. It does not apply the [view placement](#view-placement-on-the-sheet),
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
