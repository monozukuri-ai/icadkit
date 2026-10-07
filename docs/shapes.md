# Part shapes

`icadkit.read_part_shape()` and `icadkit.read_part_shapes()` collect the
qualified solids of a part into one OCCT compound, in the part's own
coordinate frame or in the document root frame, so that a projection,
sectioning or export tool can work per part. They need the optional
`preview` extra (`parasolid-kit[occt]`), like the other kernel-backed
evaluations; without it every body reports `csg.missing_dependency`.

## Python

```python
import icadkit

doc = icadkit.read("assembly.icd")
index = icadkit.read_part_shapes(doc)  # frame="part" by default
for shape in index.shapes:
    print(shape.part_id, shape.name, shape.status, shape.reason, shape.volume_mm3)

shape = icadkit.read_part_shape(doc, index.shapes[1].part_id, frame="part")
shape.shape  # an OCP TopoDS_Compound, or None when nothing converted
shape.write_step("part.step")  # new paths only, millimetres
shape.write_brep("part.brep")
rows = index.to_rows()  # JSON-compatible, without kernel objects
```

`read_part_shape(document, part_id, *, frame="part", schema=None,
part_limits=None, limits=None)` converts one part; `read_part_shapes(document,
*, part_ids=None, frame="part", ...)` converts every part (or the selected
IDs) in one pass and builds each shared resource once. `schema` is the explicit
Parasolid catalog of [`Document.read_geometry()`](api.md#geometry);
`PartShapeLimits(max_parts, max_bodies, saved, csg, geometry)` bounds the call
and carries the per-body limits of the saved-body, CSG and geometry readers.

## Sources and frames

A part takes the first of these sources that offers a qualified body:

1. its [saved final bodies](saved-bodies.md), placed by their saved frames;
2. its qualified [CSG results](csg.md), only when no saved body of the part
   is qualified (a saved body is the final result of the same history, so the
   two are never combined);
3. its standalone [native primitives](native.md): boxes, cylinders, full
   spheres, cones and frusta, ring tori, polygon and profile extrusions and
   revolutions, built as exact kernel solids. An improper primitive frame (a
   signed height or a mirrored entity) equals the right-handed frame with its
   Y axis reversed, so the solid is built from the Y-mirrored parameters in
   that frame.

`frame="world"` keeps the document root frame of the readers;
`frame="part"` applies the inverse of the part's
`placement.world_transform`, so a mirrored occurrence (`stored_parity`)
yields its mirrored solid in its own proper frame. `PartShape.frame_transform`
is the matrix applied to document-root millimetre coordinates. Mass
properties are exact kernel values in millimetres: `volume_mm3`, `area_mm2`
and the volume-weighted `centroid_mm` of the converted bodies, per body and
per part.

## Drawability

Each `ShapeBody` records its `source` (`saved`, `csg` or `native`), resource,
payload hash, face and solid counts, status and diagnostics. The part
`status` is `complete` when every body converted, `partial` when some did and
`unsupported` when none did; `reason` names the first diagnostic code:
`shape.no_bodies` (the part stores no body), `shape.external_reference` (the
bodies live in the referenced file; resolve it with the
[assembly API](references.md)), `shape.frame` (no evaluated rigid part
frame), `csg.missing_dependency` (no kernel), or the saved, CSG and native
codes of the readers. `PartShapeIndex.status` is `complete` when every part
that stores bodies converted, `partial` when some parts did and
`unsupported` when none did; parts without bodies do not lower it. Per-body
limit violations are recorded as unsupported bodies, not raised.

## Export

`write_step()` writes STEP AP214 in millimetres and `write_brep()` the
kernel's BREP format. Both refuse an existing path and a shape without a
converted solid. The compound carries no colours, names or part structure,
and nothing is healed or approximated.

## Limits

- V7L2–V7L5 opaque inventories, V8 parts whose entities the native reader
  does not expose, external occurrences and saved bodies that the adapter
  rejects (unobserved marker states, invalid faces, tolerance) remain
  unsupported with their diagnostics.
- Conversion follows the saved-body adapter's surface and topology scope; a
  body that fails stays absent from the compound.
- Only standalone primitives are built natively; CSG operands follow the
  CSG reader's qualification.

## Local evidence

Against the local SDK observations of parts with mass properties, 1,442 part
observations match the world-frame volume, surface area and centroid within
1e-6 relative, and the part-frame centroid equals the SDK centre expressed in
the SDK's part coordinate system. The other 151 observations are parts
without exposed bodies, saved bodies the adapter rejects, V7L2–V7L5
inventories and same-named parts that the comparison matched crosswise; 124
SDK parts belong to referenced external files that a single-file index does
not contain. Authored checks cover a placed saved box, a CSG result, each
native primitive kind, a revolved profile, a negative-height box, missing
dependencies and the export round trip.
