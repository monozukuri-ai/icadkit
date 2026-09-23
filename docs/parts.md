# Native parts

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

Version 0.3.0 also reads the observed little-endian V7L7
`3DGLOBAL` part layout. V7L7 supports the saved inventory, hierarchy, snapshot
definitions, names, comments, extended text and unresolved external names.
Both profiles provide qualified millimetre part frames and bounded native
parameters, subject to the profile-specific restrictions below.
V8L1/V8L2 additionally support bounded inventory access: saved hierarchy,
snapshot definitions, names/comments, extended text and external names. These
profiles retain raw coordinate blocks but expose no evaluated frames, units,
native geometry or appearance. `parts.inventory_profile` explains this boundary;
the viewer opens their part/property inventory without meshes. Observed extended
part records are length-checked and retained as `part_metadata_extension` ranges.
Files without a qualified 3DGLOBAL view remain unsupported, including 2D-only
files. No file header is rewritten to select a newer reader.
Other byte orders, view headers and part record layouts remain unsupported.
A product-version label alone does not establish support: the record tags must
match the profile. An empty document has a root record and zero non-root rows;
unknown content is not an empty assembly.

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

## Coordinate frames and units

V7L7 evaluates `world_transform = inverse(saved_root_frame) * saved_part_frame`.
The root becomes identity; each child local frame is inverse(parent world) times
child world. This normalization is independently checked against reopened SDK
frames, including translated and rotated roots. It applies only with a complete
saved hierarchy and a finite rigid root frame. Otherwise transforms and units
remain unavailable, with `parts.root_frame` and the underlying diagnostics.
Mirrored placements and descendants of mirrored/external ancestors remain
unsupported. Nonfinite, non-rigid or overflowing evaluated frames are invalid.
Both raw coordinate blocks and their exact source ranges remain unchanged.

For V8L3, the stored part frame is already the qualified world frame. The
following matrix convention applies to both profiles. This saved-global frame
can differ from iCAD SDK observations relative to the document root: comparing
those observations requires applying the saved root frame once. V8L3 does not
normalize the saved root to identity.

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
transforms. Mirrored records retain `is_mirror=True` and their raw coordinates;
their transform matrices remain unsupported.

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

V7L7 decodes a complete internal owner's entity list only when every entity
matches a qualified standalone box, cylinder, sphere, cone or torus header and none is
mirrored. Qualified analytic parameters and saved appearance are exposed.
Unknown/CSG records keep the
entire owner's list opaque, including any primitive-shaped operands. Incomplete
indexes and mirrored/external owners also stay opaque. Independent qualified
owners can still be displayed. See [native access](native.md).

## Completeness and limits

| Scope | Meaning |
| --- | --- |
| `index` | Qualified entity stream traversed without unknown framing |
| `hierarchy` | Root/parent/child/sibling consistency; partial for unloaded external children |
| `text` | Recovered part names and comments decode as CP932 |
| `stored_attributes` | Qualified comments and extended text decode without ambiguity |
| `attributes` | Always partial: arbitrary attributes are not interpreted |
| `placements` | World and relative part frames available; mirrors remain unsupported |
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
appearance scopes; see [native access](native.md). External references and mirrored frames return `3` with retained
rows. Exit `0` does not certify arbitrary attributes, geometry ownership or a
complete model. `check --target model` continues to reject model completeness.

A qualified V7L7 inventory can now return exit `0` for the default metadata
and placement scopes. Unqualified roots, mirrors or incomplete indexes still
return exit `3` (invalid data returns `1`). Requesting native geometry adds that
scope to the exit decision; CSG owners remain unsupported. Each result retains
independent `index`, `hierarchy` and `stored_attributes` statuses.

Qualification uses private iCAD-authored models reopened through its SDK,
including nested rotations, independent holdouts, repeated internal/external
parts, Japanese text changes and mixed native/Parasolid representations.
Public tests use independently authored synthetic framing and malformed data;
CAD files, vendor DLLs and schema catalogs are excluded from distributions.
