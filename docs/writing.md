# Writing 2D views and entities

`icadkit.DrawingWriter` edits the 2D views of one document and serializes a
copy. It appends point, line, circle and arc records and adds new 2D views;
everything else is copied from the source document. **No file written by
icadkit has been opened by iCAD.** The verification level of this module is
`corpus_consistent`: its output reproduces iCAD's own files up to the save
noise described below when the same edit is applied to captured pairs, and
every written file reads back through icadkit. Treat the output as unverified
until an iCAD check exists; see the verification levels at the end.

```python
import icadkit
from icadkit import Arc2D, Circle2D, DrawingWriter, EntityStyle, Line2D

doc = icadkit.read("part.icd")              # never modified
writer = DrawingWriter(doc)
print(writer.views)                           # e.g. ('!!GLOBAL', '!XY')
front = writer.add_view("FRONT", scale=1.0)   # cloned from the first 2D view
writer.add_entities(front, [
    Line2D((0, 0), (40, 0)), Line2D((40, 0), (40, 30)),
    Circle2D((15, 15), 5), Arc2D((30, 15), 5, 0, 1.5707963),
], style=EntityStyle(layer=1, line_width=2, line_style=1, color_index=1))
result = writer.write("part-drawing.icd")     # refuses an existing path
print(result.verification, result.added_views, result.added_entities)
```

## What is written

| Item | Rule |
| --- | --- |
| Entity records | The 56-byte point and 72-byte line/circle/arc layouts the reader qualifies: length, layer, visibility bit, type, ID, payload length, the constant bytes observed in every saved record (`0x44` at `+26`, the geometry subtype, the point tail), width, style, colour and the doubles in view-local millimetres |
| Entity IDs | One more than the largest `0x8…` record ID in the document (2D entities, projection-control records, the records of the 3D view's metadata blocks); iCAD renumbers IDs when it saves |
| View bookkeeping | Record length, entity-group words (`+24`, incremented by the added words), entity count (`+28`), extent (`+40`, the union of the stored box and the added geometry; the empty sentinel for a view without extent) |
| New views | A copy of the template's prefix and 240-byte header (the first `2d_view`, else the `2d_global` view) with the name, the next view number, count 0, the scale as a double and as `1/n` or `n/1` text, and the empty extent; the directory gains the name and the record is inserted after the last 2D view |
| View placement | Every observed document stores one 264-byte record per 2D view in the 2D global view (a `0x40000000` list after the entity group): the view's origin on the sheet (`+40`, `+48`), its scale (`+64`) and its name (`+136`). `add_view(origin=...)` clones the last record with its ID, origin, scale, name and an empty box; `delete_view()` removes the view's record, and the global header word `+2` counts the records |
| View counts | MOD `+232` counts the views that are not 3D views and `+234` the 3D views (every observed file); the writer adjusts `+232` by the views it adds or removes |
| Deletions | `delete_entities()` removes the records, subtracts their words from `+24`, decrements `+28`, drops the group marker of an emptied view and recomputes the extent from the remaining derived boxes, or keeps it (`write.extent_kept`) when a remaining entity has none; `delete_view()` removes the record and its directory name and keeps the other view numbers as stored |
| Container | Directory offsets and lengths and the MOD total are recomputed by the [container model](api.md#documents-and-extraction); timestamps, unknown MOD words and the tail are copied |

`Text2D` writes one or two lines of text (the observed line counts) in the
little-endian layout that the reader qualifies: the base point, direction,
height, width and space ratios and row space place the cell-based box, the
per-line origins, metrics and angle words are written, and the view's scale is
stored as the text's scale factor. The height is view-local, like the
coordinates; iCAD shows it multiplied by the view scale. Re-encoding the
6,239 corpus text records with a qualified layout from their decoded fields
reproduces 6,103 of them except for two unknown flag bytes (record byte 13,
saved as zero or `0x24`, and run byte 49, saved as zero or `0x20`; the writer
uses zero), and the other 136 differ in a width byte whose relation to the
run width is unknown or in the last bits of a derived row space. The record omits the optional stroke-glyph
cache, as many iCAD-saved records do; whether iCAD regenerates strokes for
such a record has not been checked. Vertical presentation forms (for example
the vertical long-vowel mark) are the caller's choice, and tilt, fonts and
rotation are not written.

`LengthDimension2D` writes a length dimension between two measured points,
aligned with the segment or with its horizontal or vertical projection,
with the dimension line through `line_point`, whose projection is saved as
the line point, and the value text centred between the arrowheads. Only the
observed default placement is written: arrowheads inside the aux lines and
the text between them; a text that does not fit is refused, and placements
that iCAD saves with extended dimension lines, outside arrowheads or a moved
text are not written. `aux_offset` starts the aux lines away from the
measured points, as some drafting conventions do. The record carries the
attribute blocks of an observed record with its underline lengths, text gap,
scale factor, measured value and arrowhead parameters patched, the value
text run (full-width digits unless `text` is given) in the reading
orientation, and the rendered arrowheads, dimension line, line point, text
anchor, aux lines with their overshoot and measured points exactly as iCAD
saves them. Paper sizes are divided by the view scale. Tolerances, extra
words, suppressed aux lines, angle and diameter dimensions are not written.

`DiameterDimension2D` writes a diameter dimension of a circle in the
observed default placement: the dimension line runs through the centre toward
`text_point`, both arrowheads sit on the circle pointing inward from outside,
the value text (the diameter in full-width digits unless `text` is given)
follows the line two clearances past the near arrowhead and the line ends one
clearance past the far arrowhead and the first underline length past the
text. The record carries the attribute blocks of the captured record with
its underline lengths, extension, gap, scale, value and arrowhead parameters
patched, the quarter arc and full circle items, the pick point, the value
text run along the line (its angle stored through single precision degrees,
as iCAD does), the arrowheads, the line, the line point and the text anchor.
The circle points come from the pick direction, the value is their distance
and the line direction is re-derived from them, in iCAD's own order of
operations, so the captured record is reproduced bit for bit. Directions
along which the text would read upside down, radius and angle dimensions,
leaders and placements with the arrowheads inside the circle are not
written.

`Hatch2D` hatches a simple polygon with parallel lines at an angle and a
perpendicular spacing, on the lattice through its anchor (the first vertex
by default). The record stores the anchor, the boundary edges relative to it
and every clipped line, as iCAD saves them; lines through a vertex may differ
from iCAD's own clipping in the last digits. Arc boundaries, holes and other
patterns are not written.

`add_raw_entity()` appends a saved record verbatim, for example a diameter
dimension record copied from another document of the same version. Its ID is
reassigned unless `keep_id=True`. A text record with a qualified layout
contributes its box to the view's extent; for other records the extent is
kept as stored and the result carries `write.extent_kept`, because their
extent is not derived; `set_extent()` overrides it.

`entity_ids(view)` lists the record IDs of a view, `delete_entities(view,
ids)` removes records (saved or added; unknown IDs raise `KeyError`),
`clear_view(view)` empties a view and `delete_view(name)` removes a `2d_view`
with its placement record. The 2D global view and the last remaining 2D view
cannot be removed (`write.view_kind`, `write.view_last`). When the RES record
names the removed view as the view that was active at save time, the name is
retargeted to a remaining 2D view and the result reports
`write.active_view`. Views added by the same writer leave no trace when
removed again.

## Per-part drawing files

A document holds at most the observed six 2D view numbers (`MAX_VIEW_NUMBER`;
`view_capacity` says how many `add_view()` can still number) and names of
eight CP932 bytes (`MAX_VIEW_NAME_BYTES`). A drawing of every part of an
assembly therefore does not fit into one copy of the assembly file. The
workflow that stays within observed layouts is one file per part: copy the
assembly file, or a drawing template that iCAD saved with a frame, add the
part's views with `add_view(name, scale=..., origin=...)`, draw into them, and
write to a new path. Re-running the workflow on a previously written file
deletes its old views first. Placement records need a 2D global view with
at least one placement record to clone, which every observed document with
a 2D view has; otherwise the result reports `write.placement`.

## Limits

- The document needs a complete view index whose 2D views expose
  `entity_count`, `scale` and a box or empty extent (`write.views`,
  `write.view_fields`); every real file of the local corpus does.
- View numbers above 6 are unobserved and refused (`write.view_number`), as
  are scales other than `1/n` and `n/1`, names longer than eight CP932 bytes,
  a document without any 2D view to clone (`write.template`), removing the
  2D global view or the last 2D view (`write.view_kind`, `write.view_last`)
  and renumbering views after a deletion.
- Angle and radius dimensions, leaders, hatches with arc boundaries or
  holes, projection views linked to 3D, 3D entities and in-place saving are
  not written. Text is limited to two unrotated lines of BMP characters in
  little-endian documents. Appearance beyond the five style fields is not
  written.
- After a deletion the recomputed extent uses the cell-based text boxes;
  iCAD's own extent includes glyph outlines, which differed by one float in
  the captured text cases.
- Unknown flag bytes of saved records are written as their most common
  value: byte 13 as zero (saved records also store `0x10`, `0x20`, `0x24` or
  `0x34`), byte 26 as `0x44` (older files store zero) and the width byte
  without its `0x80` bit.

## Evidence

- Round trip: an unchanged writer reproduces its input; every real file of
  the local corpus serializes byte for byte through the container model.
- Deleting one entity from an iCAD-saved file reproduces the file iCAD saved
  before that entity was added, except for the save noise: the timestamp
  words (MOD `+8`, `+192`, `+200`, every record's `+8`, a view's `+56`), the
  `0x8…` record IDs that iCAD renumbers, the three MOD words at `+240`, and
  the glyph-based extent of text that remains in the view.
- Re-encoding every decoded point, line, circle and arc of the local corpus
  (90,410 records) reproduces the record except for the unknown flag bytes
  listed under limits and, for 2,643 records, the last bits of a direction
  or length double that the reader had rounded.
- Adding the text, length-dimension or diameter-dimension record of an
  iCAD-saved file to its predecessor with `add_raw_entity(keep_id=True)` and
  the saved extent reproduces that file in both V8L3 and V7L7, except for the
  same save noise; `DiameterDimension2D` reproduces the captured diameter
  record itself, bit for bit, from the circle and the pick point.
- Deleting the captured text, length-dimension and diameter-dimension
  records through `delete_entities()`, and the captured local view (its
  record, directory name and placement record) through `delete_view()`,
  reproduces iCAD's own earlier files in both V8L3 and V7L7 up to the save
  noise, the retargeted active-view name and the glyph-based text extent.
- Across the local corpus every document with 2D views stores one placement
  record per 2D view, the global header word `+2` equals their count, and
  MOD `+232`/`+234` count the non-3D and 3D views.

## Verification levels

| Level | Meaning | Status |
| --- | --- | --- |
| L0 | Unmodified documents serialize byte for byte | Established on the local corpus |
| L1 | iCAD-saved before/after pairs are reproduced up to save noise | Established for the captured 2D pairs |
| L2 | iCAD opens, resaves and reports the written content | Not available: no iCAD is reachable |

Until L2 evidence exists, `WriteResult.verification` is `corpus_consistent`
and CLI/JSON consumers must not present written files as iCAD-verified.
