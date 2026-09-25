# Native parts

The unreleased big-endian V6L1/V6L2/V7L1 `61000001` profiles add internal
part inventories, names/comments and bounded hierarchy links. Both coordinate
blocks are retained as raw values; `entity_policy=inventory_only`,
`coordinate_convention=unqualified` and `source_length_unit=None` prevent
evaluated placement or geometry claims. Old metadata keeps traversal partial.
Mirror/external layouts stop the qualified inventory. This adds no big-endian
assembly resolver, saved-body binding or 3D viewer geometry. Use
[read_views() / read_drawing()](drawing.md) for older owners, registered parts
and 2D documents, including V1L9/V3/V5 originals.

V8L1 parametric standard-part templates and their V8L3 resaves now retain their
real document root and its owned entity list. The counted preamble and extended
part records are traversed without inventing child parts. Use
`index.to_rows(include_root=True)` or `icadkit parts template.icd --include-root`
to include a root-only template. Saved parameter definitions are available
through [Document.read_parameters()](parameters.md). Final body markers,
dimensions and hidden parameter tables remain separate opaque geometry records.
Qualified template entity headers retain saved IDs, layer and visibility, while
their complete appearance and evaluated geometry remain unsupported.

For qualified V7L7 final boolean bodies, use the separate [CSG API](csg.md)
or `view --csg` with the `preview` extra. The native-primitive scope described
here keeps operands opaque.

Added in **0.2.0**.
Read saved part structure, coordinate frames, names, comments and selected
extended information without installing iCAD or a geometry schema catalog.

```python
import icadkit

doc = icadkit.read("assembly.icd")
index = doc.read_parts()
print(index.status, index.diagnostics)

for part in index.walk(include_root=False):
    print(part.name, part.parent_id, part.definition_id)
    print(part.comment, part.extra_info)
    print(part.placement.local_transform, part.placement.world_transform)

rows = index.to_rows()  # One row per saved part, excluding the document root.
# Optional, if pandas is installed: pandas.DataFrame(rows)
```

`index.profile` describes the matched native record profile. It is `None` until
the reader validates both a known view header and a part record layout. A version
label alone does not select an executable profile. `profile.view_byte_range` and
`profile.part_byte_range` identify that evidence; the rest of the input may still
be partial, invalid or unsupported.

The immutable `PartProfile` includes `profile_id`, `raw_version`, `byte_order`,
`part_tag`, `coordinate_convention`, `entity_policy`, `source_length_unit`,
`saved_body_layout`, `csg_layout` and `mirror_policy`. These describe decoder policies, not the
completeness of an individual part or body. Continue to check `index.status` and
the per-part/geometry diagnostics. Inventory-only profiles do not expose
qualified units or evaluated coordinates. The `opaque_entities` policy used by
V7L2–V7L5 permits qualified part frames but leaves entity geometry and appearance
unsupported. It does not grant saved-body or CSG decoding.

`index.views` retains `PartView` entries with source byte ranges. The currently
recognized kind is `3d_global`; all other layouts remain `unknown`. Absence of a
recognized 3D view does not prove a 2D-only file. `index.document_kind` currently
remains `unknown`: no saved discriminator between a normal document and a
registered library part has been qualified. Names and filename suffixes are not
used to infer this distinction.

The current source reads observed little-endian V7L2–V7L7 and V8L1–V8L3
`3DGLOBAL` layouts. Each version has an explicit profile and tag check:
V7L2–V7L5 require `0x61000001`, V7L6/V7L7 require `0x61000002`, and V8 profiles
require `0x61000003`. Unknown layouts stop with source ranges and diagnostics.

V7L2–V7L5 expose inventory, hierarchy, names, comments, stored attributes and
root-relative millimetre frames, with opaque entity geometry. V7L6/V7L7 and
V8L1/V8L2 additionally qualify native primitives and saved appearance only for
complete standalone owners. Unknown or CSG siblings keep the owner's geometry
opaque. Saved final bodies use the separate [binding API](saved-bodies.md).

Old V7 owners require the observed 356-byte record; V8 extended owners are not
accepted by header substitution. Counted metadata includes a bounded 80-byte
`0x81000050` envelope for V7L2–V7L6. Payloads remain opaque, and metadata count,
length, nested headers and end offsets must agree. Registered-part/2D views
without the qualified 3D layout remain unsupported.

## Structure and identity

`PartIndex.parts` is a tuple in source order. `part(part_id)` looks up a record;
`children(part_id)` returns children in source order. Unknown IDs raise
`KeyError`. `walk()` visits parents before children in a valid hierarchy.
Disconnected and cyclic records are retained and visited once; check
`status.hierarchy` before relying on these relationships.

`Part` represents one saved occurrence record, including the document root.
Its ID identifies a byte location in this snapshot, not a persistent CAD
identity. Saving or reopening can change numeric source IDs. Internal non-root
records each receive their own `PartDefinition`, available through
`index.definitions` and `index.definition(part.definition_id)`. These are
snapshot definitions, not geometry deduplication: native copies can store their
own geometry, and same-named parts can have different shapes and attributes.
The root has no definition. Counts are not qualified BOM quantities.

External occurrences retain `external_reference.name`, original bytes and their
source range. Occurrences with the same saved reference name share an unresolved
external definition **within this file**. This is not proof of matching files or
content. No file search, path reconstruction or external loading occurs;
`external_reference.status` is `not_loaded` and `path` is `None`. The offline
reader cannot distinguish an available target from a missing one. External
children are not stored in the host and are not invented: hierarchy and
reference status remain partial, even if the saved host graph is consistent.
Read-only and unloaded runtime states are not inferred from saved flags.

The separate [assembly API](references.md) explicitly resolves caller-selected
files, retaining host occurrences and referenced documents with separate IDs.
Its resolved tree and transforms do not mutate this single-file `PartIndex`.

## Coordinate frames and units

All qualified V7L2–V7L7/V8L1–V8L3 profiles evaluate `world_transform = inverse(saved_root_frame) * saved_part_frame`.
The root becomes identity; each child local frame is inverse(parent world) times
child world. This normalization is independently checked against reopened SDK
frames, including translated and rotated roots. It applies only with a complete
saved hierarchy and a finite rigid root frame. Otherwise transforms and units
remain unavailable, with `parts.root_frame` and the underlying diagnostics.
V7L6/V7L7 revision 2 and V8L1/V8L2/V8L3 revision 3 qualify internal mirror
placements with `mirror_policy="stored_parity"`. V7L2–V7L5 mirrors and
descendants of external ancestors remain unsupported. Nonfinite, non-rigid or overflowing evaluated frames are invalid.
Both raw coordinate blocks and their exact source ranges remain unchanged.

V8L1/V8L2/V8L3 profile revision 2 uses this same normalization. This changes
V8L3 `world_transform` for nonidentity saved roots from the previous saved-frame
convention. Newly authored, saved and reopened SDK cases verify the document-root
coordinate convention for both parts and geometry. Raw coordinates are unchanged.

`placement.world_transform` is the saved **part coordinate frame** in the
`3DGLOBAL` work frame. It is a 4-by-4 tuple of rows acting on column vectors:
columns 0–2 are the X, Y and Z axes, and column 3 is the origin.
`local_transform = inverse(parent.world_transform) * world_transform`;
the root's local frame equals its world frame. Stored frames already include
parent placement: multiplying them down the hierarchy would apply it twice.
Invalid hierarchy prevents relative-frame evaluation.

`source_length_unit == "mm"` and
`length_unit_source == "qualified_part_profile"` describe the qualified part
coordinate profile. The unit was checked against iCAD operations and explicit
SDK millimetre/metre output; it is not decoded from a universal file-unit field.
It does not certify units of embedded Parasolid payloads or convert geometry.
A part frame is not an established transform for a resource's geometry:
`resource_ids` stays `None`, and geometry ownership remains unresolved.

The second nine-double block is retained in `coordinate_values`,
`raw_coordinate_bytes` and `coordinate_byte_range`. It stores origin, Z axis
and X axis; the Y axis is their cross product. The first block remains available
as `values`, `raw_bytes` and `byte_range`, with no geometry-transform meaning
assigned. No axis normalization or coordinate repair is performed. Nonfinite
numbers become `None` in JSON-compatible values, while raw bits are preserved.
Non-orthonormal frames and overflow produce diagnostics and unavailable
transforms. Mirrored records retain `is_mirror=True` and their raw coordinates.

For `mirror_policy="stored_parity"`, the SDK-compatible `world_transform` stays
right-handed. `orientation_world_transform = world_transform * diag(1,-1,1,1)`
for a mirrored occurrence, and equals `world_transform` otherwise. The saved
flag is absolute parity; it is not XORed with the parent again.
`orientation_local_transform = inverse(parent.orientation_world_transform) *
orientation_world_transform` exposes relative parity. A mirrored child below a
mirrored parent has positive local determinant, while an unmirrored child below
a mirrored parent has negative local determinant. Both orientation fields are
`None` for unqualified mirror profiles/placements and are included in JSON rows.
These orientation matrices describe occurrence handedness; they must not be
applied to already placed native or saved geometry.

## Stored attributes

The observed binary attribute envelopes are traversable without interpreting
their contents. `Part.opaque_attributes` contains `PartOpaqueAttribute` entries
with `source_id`, `subtype`, `owner_id`, `byte_range`, exact `raw_bytes` and
`status="unsupported"`. Rows include these bytes as hex. Qualified framing can
leave `status.index="complete"`, while `stored_attributes="partial"` and
`parts.attribute_semantics` report unknown attribute meaning. Unknown attribute layouts retain a partial index and opaque bytes; unknown
record framing stops traversal. `max_property_bytes` applies to these payloads.

Names and comments use strict CP932 decoding. General extended information
(`SxInfEx` in the validation SDK) is exposed as `extra_info`, decoded strictly
as UTF-16LE. It is **opaque text**: strings such as `material=steel` are not
converted into typed or named engineering properties.

`properties` is an ordered tuple of `PartProperty` entries with `owner_id`,
`owner_kind="occurrence"`, `origin="stored"`, `value_type="text"`, encoding,
original padded bytes and source byte range. The comment is always present;
extended information is present only when its qualified record exists.
An empty string, an absent entry and an undecodable entry (`value=None` plus a
diagnostic) remain distinct. Padding is removed only at the end; undecodable
text never uses replacement characters. Multiple extended records are retained,
but the `extra_info` convenience accessor returns `None` with an ambiguity
diagnostic instead of choosing a value.

Parent text is not inherited. Repeated same-name occurrences can carry
independent extended information. Adding, updating and deleting this information
were checked through iCAD save/reopen. In the tested SDK, setting it to an empty
string was a no-op; deletion used a separate operation. Stored empty extended
text is covered by synthetic parser tests, not an authored SDK case.
Custom named/reference/instance attributes, typed values, inheritance and
effective-value resolution remain outside the qualified scope. Absence here
does not prove that an arbitrary property is unset in iCAD.

## Native entities

`Part.entities` additionally exposes [native primitive parameters and stored
appearance](native.md). Qualified boxes and cylinders retain global frames and
dimensions; unsupported entities remain with diagnostics. `Part.resource_ids`
is still unresolved, and native parameter support does not evaluate a B-Rep.

V7L6/V7L7 decode a complete internal owner's entity list only when every entity
matches a qualified standalone box, cylinder, sphere, cone or torus header,
including qualified entity mirrors. Qualified analytic parameters and saved appearance are exposed.
Unknown/CSG records keep the
entire owner's list opaque, including any primitive-shaped operands. Incomplete
indexes and external owners also stay opaque. Independent qualified
owners can still be displayed. See [native access](native.md).

## Completeness and limits

| Scope | Meaning |
| --- | --- |
| `index` | Qualified entity stream traversed without unknown framing |
| `hierarchy` | Root/parent/child/sibling consistency; partial for unloaded external children |
| `text` | Recovered part names and comments decode as CP932 |
| `stored_attributes` | Qualified comments and extended text decode without ambiguity |
| `attributes` | Always partial: arbitrary attributes are not interpreted |
| `placements` | World and relative part frames available; signed orientation for qualified internal mirrors |
| `definitions` | Internal snapshot definitions; partial for unresolved external definitions |
| `units` | Millimetres established for the qualified part coordinate profile |
| `references` | Partial when external references remain unloaded |
| `native_geometry` | Qualified native primitive parameters; separate from B-Rep |
| `appearance` | Selected saved entity palette/visibility/layer fields |
| `geometry` | Resource B-Rep and placed geometry not checked by this operation |
| `model` | Partial: this API does not establish whole-model completeness |

Unsupported native shapes and embedded geometry do not remove part rows.
Unknown entity framing stops view traversal; the parser does not search later
bytes for plausible part signatures. Length-framed geometry contents stay
opaque. Recovered records, diagnostics and `opaque_ranges` remain available.
Preceding metadata blocks use a qualified fixed header followed by counted,
length-framed `0x79`–`0x7c` subrecords. Their lengths must consume the block
exactly; their contents remain opaque. Unknown tags or inconsistent framing
stop traversal. Truncated or out-of-bounds outer lengths are invalid. Metadata
subrecords count toward `PartLimits.max_entities`.
The document's `unparsed_ranges` still apply to the rest of the container.
Use `doc.source_bytes(byte_range)` for exact source bytes.

```python
index = doc.read_parts(limits=icadkit.PartLimits(
    max_parts=100_000,
    max_entities=500_000,
    max_depth=256,
    max_property_bytes=1_048_576,
))
```

Limits bound part count, traversed entities, tree depth including the root, and
bytes per extended text field. They must be integers in `1..2**31-1`; booleans
are rejected. Exceeding a limit raises `LimitExceededError`, never a truncated
successful inventory. `ReadLimits` separately bounds the container input.

## Command line

```sh
icadkit parts assembly.icd --json
icadkit parts assembly.icd --include-root --max-parts 10000
```

JSON has `schema_version=1`, scope statuses, definitions, per-part rows,
diagnostics and opaque ranges. Raw bytes use hex strings. `part_count` always
excludes the root; `definition_count` counts internal snapshot definitions and
unresolved external name groups. Both remain structural counts, not BOM totals.

Exit codes: `0` means inventory, hierarchy, recovered text, selected stored
attributes, part frames, snapshot definitions, units and references are complete
in the qualified scope; `1` means invalid data or I/O failure; `2` means invalid
arguments; `3` means unsupported or partial content; `4` means a resource limit
was exceeded. `--require-native` also requires complete native parameter and
appearance scopes; see [native access](native.md). External references and unqualified mirror profiles return `3` with retained
rows. Exit `0` does not certify arbitrary attributes, geometry ownership or a
complete model. `check --target model` continues to reject model completeness.

A qualified V7L7 inventory can now return exit `0` for the default metadata
and placement scopes. Unqualified roots, mirror layouts or incomplete indexes still
return exit `3` (invalid data returns `1`). Requesting native geometry adds that
scope to the exit decision; CSG owners remain unsupported. Each result retains
independent `index`, `hierarchy` and `stored_attributes` statuses.

Qualification uses private iCAD-authored models reopened through its SDK,
including nested rotations, independent holdouts, repeated internal/external
parts, Japanese text changes and mixed native/Parasolid representations.
Public tests use independently authored synthetic framing and malformed data;
CAD files, vendor DLLs and schema catalogs are excluded from distributions.

The qualified V7L5 originals contain an unlinked document root only. A child
record or nonzero hierarchy link yields `parts.legacy_root_only`; V7L5 child
assemblies are not inferred from the other V7 profiles.
