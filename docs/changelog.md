# Changelog

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
