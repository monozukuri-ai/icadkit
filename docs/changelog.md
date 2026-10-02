# Changelog

## 0.3.5

### Added

- `read_views()` / `read_drawing()` with the `views` / `drawing` CLI commands:
  direct old-format view/registered-part inventories and bounded big-endian
  V6L1/V6L2/V7L1 internal part inventories. Saved 2D points, lines, circles and
  arcs use view-local millimetres. UTF-16LE text content is exposed separately
  from unsupported text layout. See [views and 2D entities](drawing.md) for
  exact profiles and limits.
- Explicit `read_assembly()` resolution and `assembly` / `view --reference-root`
  CLI support for qualified V7L6/V7L7/V8L1/V8L2/V8L3 external assemblies,
  including mirrored references. Separate document/occurrence identities,
  dependency hashes, bounded per-call caches and explicit
  missing/ambiguous/cycle/limit states preserve provenance. Single-file reads
  keep their existing behavior. See [external references](references.md).
- Internal mirror occurrence orientation in V7L6/V7L7 and V8L1/V8L2/V8L3.
  SDK-compatible part frames remain separate from signed orientation; stored
  raw coordinates and parameters are preserved. Qualified native mirrors and
  saved final B-Reps display without applying reflection twice. Reflected
  box/cone meshes preserve outward triangle winding.
- `read_parameters()` for [saved 3D parameter tables](parameters.md) in
  qualified V8L1 standard-part templates, their V8L3 resaves and V7L7/V8L3
  internal placements. Root-owned template entities appear in the part
  inventory. Expressions are retained, not executed.
- `Document.read_trailer()`, `Document.read_trailer_block()` and the
  `icadkit trailer` command for the container after the indexed records. Its
  framing is the same in both byte orders. Each compressed block is stored under
  a saved entity identifier; revisions 7 and 8 expose a stored single-precision
  enclosing box in the entity's local frame, which is not always the smallest
  one, and their face and edge tables as `TrailerBlockData.faces` and `.edges`:
  saved node identifiers, the stored surface type code, a parameter-space box
  per face entry and the two face entries of each edge. Surface and curve
  parameters, older revisions and the named table remain opaque. See
  [trailing container](trailer.md).
- V8 association records as `Document.resource_associations`.
- Qualified six-vertex polygon extrusions, including concave profiles, negative
  heights and negative-height mirrored records, decoded and displayed. Local
  SDK comparisons cover 21 mirrored records. Other polygon layouts, holes and
  tapers remain unsupported.
- Standalone extrusions with straight and circular profile segments
  (`profile_extrusion`, `NativePrimitive.profile`) and revolved profiles
  (`revolution`, `NativePrimitive.revolution_profile`), displayed in the native
  viewer. See [native primitives](native.md).
- Negative heights in qualified cylinders, with or without the mirror flag:
  the solid extends along the frame's negative Z. `height` stays positive and
  the exposed Z column is reversed once. Qualified nonmirrored boxes accept
  negative heights too, retaining the source sign and reversing the display
  frame's Z axis once.
- Saved bodies with elliptical edges, source-identified intersection curves and
  cone apexes convert. Other curve kinds, vertex loops away from a cone apex
  and non-solid bodies remain unsupported. See [saved bodies](saved-bodies.md).
- Six-vertex convex prism operands, with a signed height, in the bounded CSG
  reader. In the checked inputs these records occur only in owners outside the
  CSG scope, so no additional body is evaluated yet.
- `icadkit view --max-bodies` for `--saved-brep` and `--csg`.

### Changed

- V8 saved final bodies bind through the saved association records. The word
  at marker `+32` no longer selects a resource: it is not a resource reference.
  This binds bodies previously left with `saved.binding` and corrects bodies
  that were bound to another body's resource. `SavedBody.binding_kind` is now
  `resource_association` for V8, replacing `saved_resource_key`, and
  `PartProfile.saved_body_layout` is `v8_resource_association`. A frame conflict
  yields `saved.association_frame`.
- V7L2–V7L5 saved final bodies bind by native source ID
  (`saved_body_layout="v7_legacy_source_id"`), as V7L6 already did. Their entity
  geometry, appearance and mirrors keep the existing limits.
- Observed type-85 final-result variants `03` and `04` bind with the existing
  unique-ID/resource/frame checks. Ambiguous V8 associations remain unsupported.
  The observed `+28=0` state is accepted with the same guards, adding 2,305
  bound records in the local installed corpus.
- The registry dependency is `parasolid-core 0.3.5`. Its twenty-six further
  exact iCAD profiles give catalog-free raw parsing and source B-Rep mapping
  for fifteen embedded keys and eleven standard keys. Offset surfaces are read
  under the keys where they were observed, and a trimmed curve on a line with
  unset parameters is mapped from its end points. In a local sample of 33,943
  resources, 33,913 parse without a catalog and agree with the catalog path;
  the others keep their diagnostics. Profile IDs and hashes come from the
  backend; see [supported schemas](support.md#schemas).
- Attribute records with several fragments, or with the saved visibility bit,
  are retained as `Part.opaque_attributes` instead of geometry entities. Text
  fragments inside them are not promoted to extended text.
- View framing traverses repeated 264-byte view-control records under a
  qualified `40000000` metadata group in either byte order. Unknown framing
  still stops. The observed big-endian V5L1/V5L3 `50000001` groups of
  112/192-byte records are traversed while keeping their links and names
  opaque. Ten more real files have complete view framing; part/assembly
  semantics remain separately qualified.

### Upgrade from 0.3.0

Existing APIs keep their signatures and no public name is removed. The package
still has no mandatory Python runtime dependencies. The `preview` extra remains
pinned to `parasolid-kit[occt]==0.2.0`; the compiled Rust reader uses
`parasolid-core 0.3.5`.

V8 saved bodies may bind to a different resource than before, or bind where
they were rejected. Results that depended on the earlier marker-word rule
should be read again. Their `binding_kind` is `resource_association` instead of
`saved_resource_key`.

`NativePrimitive.kind` now also includes `polygon_extrusion`,
`profile_extrusion` and `revolution`. The new fields `profile_points`,
`profile`, `revolution_profile` and `mirror_convention` are `None` where they do
not apply. JSON consumers must allow additional fields while continuing to
check each scope's status.

The exact V34 built-in profile is now `icad-sch34101-13006-r3` and the embedded
V30 profile `icad-sch30000-13006-r6`. Consumers pinning the old profile IDs or
hashes must update their expectations; `icadkit.build_info()` lists the
identifiers and `GeometryResult.schema` reports the selected hash. Nearby
schema keys and unknown base types do not gain implicit support.

### Scope

Mirrored CSG history, complete assembly geometry, inherited attributes and
complete drawing reconstruction remain unsupported. Offset surfaces are read
from the source but not converted to solids. Trailer surface and curve
parameters stay opaque. See the [support boundaries](support.md) and the
[source roadmap](roadmap.md) for the version-support stages and their local
validation scope.

## 0.3.0

### Added

- `icadkit view` and `icadkit.viewer` for offline native display, part/property
  inspection, selection and saved visibility. Unsupported entities retain their
  diagnostics; unreadable inventories fail before opening an empty viewer.
- Bounded V7L7 part access with root-relative millimetre frames and standalone
  primitive ownership. Counted metadata framing extends V7L7/V8L3 traversal;
  unknown framing preserves the unparsed tail and scoped status.
- V8L1/V8L2 inventories with names, hierarchy, stored text and external names.
  Units, evaluated frames, native geometry and appearance remain unavailable.
- `Part.opaque_attributes` retains qualified binary attribute records, source
  ranges and ownership. Values are not interpreted as typed properties or BOM.
- Full native spheres, circular cones/frusta and full ring tori in V7L7/V8L3,
  with dimensions, frames, saved appearance and bounded viewer meshes.
- `read_csg()`, optional `evaluate_csg()` and `view --csg` for qualified V7L7
  box/cylinder unions, differences and intersections. Operands remain undrawn.
- `read_saved_bodies()`, optional `evaluate_saved_body()` and `view --saved-brep`
  for qualified V7L7/V8L3 saved final solids. V8L3 uses explicit resource keys;
  shared resources retain each occurrence's placement. Spherical, conical and
  toroidal saved surfaces have bounded conversion support.
- Published `parasolid-core 0.3.1` supplies exact V34 profile revision 2 with
  TORUS (54), removing the catalog requirement for qualified toroidal resources.

### Upgrade from 0.2.0

The package still has no mandatory Python runtime dependencies. The `preview`
extra remains pinned to `parasolid-kit[occt]==0.2.0`; the compiled Rust reader
uses `parasolid-core 0.3.1`. Install the extra for CSG, saved-body evaluation or
resource preview/GLB. Native parameter viewing needs only the base package.

`NativePrimitive.kind` now also includes `sphere`, `cone` and `torus`.
`height` can be `None` for spheres/tori. Cones use `radius`, `top_radius` and
`height`; tori use `major_radius` and `minor_radius`. The new radius fields are
`None` for other kinds. JSON consumers must allow additional fields, including
`opaque_attributes`, while continuing to check each scope's status.

The exact V34 built-in profile is `icad-sch34101-13006-r2`, revision 2, with
SHA-256 `eaebc3477246b7a6b56d1b5dc500029beba46f54e9226973883bc444684f26eb`.
Consumers pinning the old profile ID/hash must update their expectations.
Nearby schema keys and unknown base types do not gain implicit support.

Part and primitive frame conventions remain profile-specific. V7L7 is relative
to the saved root; V8L3 retains saved-global frames. Do not apply part placement
again to an already placed primitive or evaluated saved body.

### Scope

Every viewer is a partial model. V8L1/V8L2 provide inventory only. Binary
attribute values, mirrors, automatic external loading, whole-scene GLB,
drawings and general feature/history evaluation remain unsupported. Partial
native spheres/tori, offset cones and horn/spindle tori do not gain substitute
meshes. See [support boundaries](support.md).

## 0.2.0

### Added

- `Document.read_parts()`, `PartIndex.walk()` and `to_rows()` for bounded native
  part hierarchy, snapshot definitions and unresolved external references.
- Qualified millimetre part frames, names/comments and stored extended text,
  retaining raw bytes, ownership, scoped diagnostics and unknown values.
- Native box/cylinder dimensions and global frames, plus saved entity palette
  index, visibility and layer. Unsupported shapes remain in the inventory.
- `icadkit parts` with JSON output, explicit scope statuses and resource limits.
- Exact built-in `SCH_3401212_34101_13006` support via `parasolid-core 0.3.0`.
- Optional `icadkit[preview]`, Python preview/server APIs and `icadkit preview`
  for one solid resource, metre-based GLB and an offline browser viewer.
  The caller declares resource units; hashes and source mappings accompany export.
- Preview dependencies and synthetic geometry checks in platform wheel CI.

### Upgrade from 0.1.0

Existing inspection, resource extraction, geometry APIs and CLI commands retain
their contracts. The base package still has no mandatory Python dependencies.
Install the preview extra only when conversion/viewing is needed. Native part
access does not require iCAD, Wine, a schema catalog, or the preview extra.

Qualified resources using the new exact V34 profile can omit the explicit schema
catalog. An explicitly supplied catalog remains authoritative. Availability of a
profile does not imply support for arbitrary geometry with that schema key.

Part frames and native primitive frames use millimetres; resource geometry
retains its own coordinates and requires independently established units.
A primitive's stored frame is already global, so applying the part frame again
would transform it twice. Resource-to-part placement remains unresolved.

### Known limits

- Native layout support is limited to the qualified little-endian V8L3 profile.
  Mirrored frame evaluation, typed/custom attributes, inheritance, automatic
  external loading and persistent cross-save identities are not provided.
- Native primitive parameters do not constitute an evaluated B-Rep. Saved
  appearance is per entity, not effective part/face appearance or RGB color.
- Preview supports a bounded solid/analytic topology subset. Unsupported trimming
  and invalid OCCT conversions are rejected without partial output or healing.
  One authored through-hole resource is known to fail with `occt.invalid_shape`.
- GLB retains resource coordinates and illustrative colors; complete assemblies,
  native CSG, drawing interpretation, STEP export and ICD writing remain outside
  this release. See [support](support.md) and [preview](preview.md).

## 0.1.0

Initial release with bounded header inspection, owner-based resource indexing,
validated X_B extraction, exact-schema raw geometry/B-Rep access, provenance,
CLI diagnostics, and platform wheel/source distribution verification.
