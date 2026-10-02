# Trailing container and per-entity blocks

Version 0.3.5 adds `Document.read_trailer()` and
`Document.read_trailer_block()`. They frame the bytes that follow the last
directory-indexed record: the range `Document.unparsed_ranges` reports as
`uninspected_tail`. No iCAD process, schema catalog or optional package is needed.

```python
import icadkit

doc = icadkit.read("model.icd")
trailer = doc.read_trailer()
print(trailer.status, trailer.byte_range, trailer.revision)
for block in trailer.blocks:
    print(hex(block.source_id), block.view_name, block.declared_decoded_bytes)
    data = doc.read_trailer_block(block.block_id)
    print(data.status, data.bounds)  # None unless the layout is qualified
    print(len(data.faces), len(data.edges))  # empty unless qualified
```

## Framing

Records use one packed header in files of either byte order: a type byte, a
subtype byte, a little-endian 16-bit header size and a little-endian 32-bit
total size. A big-endian document still stores these fields little-endian.
Only the observed nesting is accepted:

| Record | Content |
| --- | --- |
| Root | Covers the trailing range exactly |
| Table group (optional, first) | Framed tables retained as `TrailerTable` |
| Block group | One block set; its subtype is `TrailerIndex.revision` |
| View | Eight name bytes, a block count and the largest decoded block size |
| Block | Entity identifier, encoded size, decoded size and a zlib stream |

Declared counts, the size bound, zero padding to a 16-byte boundary and every
length must agree. Unknown records stop traversal: the remainder becomes an
opaque range and the status is `partial`. Inconsistent lengths, counts, block
headers or padding are `invalid`. An unrecognized root is `unsupported` with the
whole range retained. Nothing is searched for.

`status="complete"` means the framing was traversed. A file that ends with its
last indexed record has `byte_range=None`, no blocks and is also complete; this
is normal for files saved without an interactive display. A missing container
is not an empty model.

## Blocks

`TrailerBlock.source_id` is the stored identifier of a native entity in the
named view. In the checked inputs every saved final-body marker has exactly one
block under its own identifier, while standalone analytic primitives have none.
The identifier is a snapshot value: it is not a persistent CAD identity, and a
block does not assign part ownership by itself. Blocks also exist for bodies
stored inside opaque part metadata; those have no entity in `read_parts()`.

`read_trailer_block()` inflates exactly one indexed block. The stream must end
with a valid checksum, consume the declared encoded size and produce the
declared decoded size. An identifier that is not a block of this document's
index is rejected; arbitrary offsets are never decoded.

`TrailerBlockData` returns the decoded bytes, their SHA-256 and the stored
`raw_kind` word. For revisions 7 and 8 with the 52-byte header, `bounds` holds
the stored single-precision minimum and maximum corners:

- millimetres, in the **entity's saved local frame** (`coordinate_space =
  "entity_local"`), the frame that places its saved body. They are not document
  coordinates; apply the saved body's `world_transform` to place them.
- single precision, so compare with a tolerance.
- an enclosing box, not always the smallest one. In the checked inputs it
  contains the evaluated body in every case and is usually tight, but it was up
  to 1.1 mm larger along one axis for chamfered hexagonal nuts. Use it as a
  conservative extent, not as an exact measurement.

Older revisions (1–4 and 6) and the other block kinds are framed and decoded,
but their payload layout is not qualified: `bounds` is `None` with
`trailer.block_layout`, and no table is exposed. Nonfinite or reversed bounds
are `invalid`.

## Face and edge tables

In the qualified layout two fixed-size tables follow the header: 36-byte face
entries, then 24-byte edge entries. `TrailerBlockData.faces` and `.edges`
expose their identity and adjacency:

- `TrailerFace.source_node_id` is the node identifier of a face of the saved
  body bound to the same entity. A face that is closed in a parameter
  direction, such as a full cylinder, is stored as several entries with one
  identifier. `index` is the one-based entry number.
- `TrailerFace.surface_code` is the stored surface type. In the checked inputs
  1 is a plane, 2 a cylinder, 3 a cone, 4 a sphere and 5 a torus; other values
  occur for free-form and special surfaces and are not qualified. The code is
  the block's own classification: a few saved NURBS surfaces carry code 2.
- `TrailerFace.parameter_bounds` is `(u_min, v_min, u_max, v_max)` of the entry
  on its surface, with lengths in millimetres. It is verified for planar faces
  with straight edges, where it equals the extent of the face's vertices in the
  plane's own `u`/`v` axes. It is `None` when the stored values are not finite
  and ordered.
- `TrailerEdge.source_node_id` is the node identifier of a saved edge. An edge
  that only separates two entries of one saved face carries that face's
  identifier. `TrailerEdge.faces` holds the one-based numbers of the adjacent
  face entries, 0 for none.
- `raw_bytes` is the exact entry and `item_offset` locates the entry's
  parameters in `payload`.

The surface and curve parameters, the entry tags and the link fields between
entries are not interpreted, so a block is `partial`, never `complete`. Counts
or offsets that disagree with the tables make the block `invalid`
(`trailer.block_tables`) and nothing is exposed. Use the saved body for
geometry; the tables identify which saved face or edge an entry belongs to.

The framed table group holds a linked name/value layout that is not qualified.
`TrailerTable` exposes its byte ranges with `status="unsupported"`.

## Limits and CLI

`TrailerLimits` defaults to 500,000 records and 64 MiB for the encoded or
decoded size of one requested block. Exceeding a limit raises
`LimitExceededError`. Decoding one block does not cache it.

```sh
icadkit trailer model.icd --json
icadkit trailer model.icd --bounds --max-block-bytes 16777216
```

`--bounds` decodes every block and adds `decoded_blocks` with `raw_kind`,
`bounds`, status, the decoded size and the face and edge counts; payload bytes
and entries stay available through the Python API. Exit codes follow the framing status: 0 complete (including an
absent container), 3 partial or unsupported, 1 invalid data or I/O failure,
4 a limit and 2 argument errors.

## Local evidence

All 884 trailing containers in a local sample of 2,312 files frame completely:
35,279 blocks, each decoding to its declared size. In the 570 files with a
complete part index, each of 31,174 final saved-body markers has exactly one
block and no standalone primitive has one. For 6,059 saved bodies evaluated from
their bound resources, the stored box contains the evaluated solid in its local
frame in every case. It equals the solid's bounds within 0.05 mm for 6,040 of
them; the other 19 are chamfered hexagonal nuts whose stored box is larger along
one axis. This qualifies the framing, the identifier and the bounds of the
stated layout as an enclosing box.

All 18,203 blocks of the qualified layout in files below 60 MB have consistent
tables: 441,957 face entries and 1,067,181 edge entries. For 6,302 bodies
compared with their bound saved B-Rep, the face identifiers equal the saved
faces, every saved edge appears, every other edge identifier is a face
identifier, and the two faces of each edge entry are the faces of that saved
edge, with no exception. Surface codes 1 to 5 agree with 145,062 saved
surfaces; 54 saved NURBS surfaces carry the cylinder code. The parameter box
equals the vertex extent for all 32,541 planar straight-edged faces checked;
248 face entries have no ordered finite box.

This does not qualify the surface and curve parameters, the link fields, the
older revisions, the other block kinds or the table group. The named table
holds ASCII names with UTF-16 text values in a layout that was not resolved.
