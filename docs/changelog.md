# Changelog

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
