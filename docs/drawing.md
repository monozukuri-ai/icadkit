# Views and saved 2D entities

The unreleased source adds `Document.read_views()` and `Document.read_drawing()`.
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
| Text | Counted UTF-16LE runs in little-endian V7L6/V7L7/V8L1/V8L2/V8L3, including multiline and private-use characters | Content only; `layout_status=unsupported` |
| Dimensions, symbols, hatch, projection controls and other types | Raw type, source range and diagnostics | No decomposition into guessed primitives |

Geometry uses **view-local millimetres**. It does not apply paper placement,
view rotation, scale or a 3D projection transform. Saved line/circle records in
a projected view can be read independently of the unimplemented projection
relationship. Stored text baselines differ from SDK anchors under rotation,
vertical writing and view scaling. Text anchors, fonts, glyph outlines, layout
and legacy CP932 text runs remain unqualified. Text content therefore gives a
`partial` entity, even when every character is decoded.

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
