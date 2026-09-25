# Bounded V7L7 CSG

Version 0.3.0 reads selected saved V7L7 final-body programs.
`read_csg()` is dependency-free. `evaluate_csg()` uses the optional `preview`
extra to evaluate analytic solids with OCCT and generate a display mesh.
This is partial model support; it does not reconstruct feature history or
provide a whole-model B-Rep/STEP export.

```sh
uv run --extra preview icadkit view model.icd --csg
uv run --extra preview icadkit view model.icd --csg --write-only --output csg-view
```

For an installed updated package, install `icadkit[preview]` and use
`icadkit view model.icd --csg`. Neither iCAD nor its SDK is a runtime dependency.
The default viewer keeps its dependency-free standalone primitive path.

```python
import icadkit

document = icadkit.read("model.icd")
index = icadkit.read_csg(document)
for body in index.bodies:
    if body.status != "complete":
        print(body.body_id, body.diagnostics)
        continue
    mesh = icadkit.evaluate_csg(body)
    print(mesh.volume_mm3, mesh.area_mm2, mesh.centroid_mm)
```

Saved results of unqualified programs may be available through the separate
[saved final body mode](saved-bodies.md). That path reads a saved B-Rep and does
not interpret those feature operations.

## Qualified scope

- Little-endian V7L7, a complete part index/hierarchy and a qualified rigid root
  frame. Internal, nonmirrored owners with one explicitly framed tree are supported.
  Mirrored or external ancestors also keep CSG history evaluation unsupported,
  even when their part coordinate frames are available.
- Saved postfix union, difference and intersection of positive-height boxes and
  cylinders, including the qualified saved checkpoints between operations.
  Multiple independent owners and disconnected solid results are supported.
- A single box/cylinder leaf with its explicit saved tree can also be evaluated.
- Explicit source identifiers, same-owner operands, tree/result binding and
  component backlinks are checked. Resource ordering and cached Parasolid streams
  are not used to infer body ownership.
- Operand frames use `inverse(saved_root_frame) * saved_operand_frame`, in mm.
  Part placement is not applied again. No unit scale is guessed.
- Qualified final markers expose saved color index, visibility and layer. Other
  appearance remains unknown; colors in the viewer are illustrative.

Saved parameter bytes, program metadata bytes, source ranges and identifiers are
retained by `CsgBody` and its operands. The `PartIndex` native entity view remains
unchanged: component records are not promoted to finished standalone primitives.
`parts --require-native` still checks that separate native-primitive scope.
`CsgIndex.status="complete"` describes candidate programs in the indexed scope;
it does not mean that all model entities have geometry. `model_status` stays
`partial`, even when every candidate is evaluated.

Negative extrusion heights, spheres/cones, profile extrusions, fillets, mirrors,
additional history/transformation opcodes, imported B-Rep markers, multiple trees
per owner, unknown metadata layouts and automatic external-file loading remain
unsupported. Empty or lower-dimensional boolean results are also unsupported.
An unknown instruction or missing/ambiguous reference rejects the entire body;
no intermediate solid, operand-only replacement, healing or fuzzy repair is used.
Other independent, qualified bodies can still be displayed.

## Evaluation and limits

Mass properties come from the evaluated analytic solid. Display triangles use
an absolute linear deflection of 0.05 mm by default, configurable through
`evaluate_csg(linear_deflection_mm=...)`; triangles are an approximation.
The result includes volume, area, centroid, solid count, vertices, triangles and
face-boundary edges. Invalid/open solids or incomplete triangulation are rejected.

`CsgLimits` defaults to 256 candidate bodies, 64 distinct operands per body,
128 operations, stack depth 64, 20,000 traversed subshape occurrences,
250,000 triangles and 500,000 vertices per evaluation. `read_csg` accepts both
`limits=CsgLimits(...)` and `part_limits=PartLimits(...)`.
`write_native_viewer(..., csg=True, csg_limits=CsgLimits(...))` also enforces its
aggregate `ViewerLimits` triangle/output-byte bounds. The CLI uses the CSG
defaults and exposes the existing read/part/viewer limits.

These are complexity/output guards, not native-kernel memory or execution-time
limits. A single boolean or meshing operation can be expensive before its output
is checked. Optional dependencies are imported only when a qualified body is
evaluated. Missing dependencies and exceeded limits abort before viewer output
is published; individual unsupported/invalid bodies retain scoped diagnostics.

CSG viewer exports use `scope="qualified_native_csg"` when units are qualified.
`scene.csg` reports candidate, evaluated and omitted body counts. Indexed saved
operand records remain selectable, are labelled as operands and are not drawn
separately. Saved record counts include these operands and must not be interpreted
as counts of finished solids. Selecting a result displays its mass properties
and CSG diagnostics; the original saved-record diagnostics remain available.

Qualification uses synthetic malformed programs and independent analytic checks,
plus local saved/reopened SDK comparisons for boolean operations, repeated cuts,
compound programs, disconnected results, transformed roots, held-out dimensions
and final appearance. This does not establish general V7L7 compatibility.
