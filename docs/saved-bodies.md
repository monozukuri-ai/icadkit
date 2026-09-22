# Saved final bodies

The source checkout supports an additional V7L7 display path using saved final
B-Rep solids. This displays the saved result without replaying feature history.
It is separate from `--csg` and does not claim support for unknown CSG operations.

```sh
uv run --extra preview icadkit view model.icd --saved-brep \
  --schema /path/to/sch_26105.sch_txt --schema-id 26105
```

For an installed updated package, install `icadkit[preview]` and run the same
`icadkit view` command. iCAD and its SDK are not runtime dependencies. An exact
built-in geometry profile is used when available; otherwise the matching catalog
must be supplied explicitly. `--schema-sha256` optionally pins its bytes. Catalogs
are never searched for automatically or included in the distribution.
`--schema-id 26105` applies to resources requesting that exact provider, not to
arbitrary V7L7 files. Missing/mismatched catalogs retain diagnostics and no mesh.

```python
import icadkit

model = icadkit.read("model.icd")
index = icadkit.read_saved_bodies(model)
schema = icadkit.SchemaCatalog.from_file("sch_26105.sch_txt", expected_id="26105")
for body in index.bodies:
    if body.status == "complete":
        mesh = icadkit.evaluate_saved_body(model, body, schema=schema)
        print(mesh.volume_mm3, mesh.area_mm2, mesh.centroid_mm, mesh.face_count)
```

`read_saved_bodies()` is dependency-free. Its `complete` status establishes
binding and placement, not geometry conversion or whole-model coverage. Evaluation
requires a complete parsed B-Rep and topology plus the optional preview runtime.
`model_status` remains `partial` in the index and viewer.

## Qualified binding and geometry

The adapter accepts little-endian V7L7, complete part/resource indexes, internal
nonmirrored owners and qualified saved root frames. Final type-85 markers are
distinguished from component markers. Exactly one indexed resource must match
the final marker's source ID, and that native ID must be unique. Resource order
never supplies ownership. Unknown marker layouts and ambiguous IDs are rejected.

Only the qualified type-134/version-5 resource layout is placed. The saved
resource frame uses native mm; B-Rep coordinates use source metres. Conversion
first scales the resource geometry by 1,000, then applies
`inverse(saved_root_frame) * saved_resource_frame` once. Part placement is not
applied again. Source frames, byte ranges, resource IDs and payload hashes remain
available. The saved final marker supplies visibility, palette index and layer;
colors are illustrative and layer visibility is not interpreted.

The current converter covers solid bodies with planes, cylinders, lines,
circles and explicit line/circle trims. Shared source vertices/edges/loops and
material-region shells are preserved. Surface parameter curves, periodic seams
and wire orientation are constructed by the pinned `parasolid-kit==0.2.0` OCCT
adapter. Generated edges/vertices retain the declared body resolution; existing
source tolerances are preserved. Surfaces and source points are not moved, faces
are not sewn or discarded, and no feature operation is guessed. Unresolved
conversion diagnostics, changed face counts, open/invalid solids and incomplete
triangulation reject the whole body.

Tolerance is part of the saved representation, so this is not an exact-arithmetic
claim. `SavedBodyMesh.representation_operations` records the construction steps.
Volume, area and centroid come from the resulting analytic solid; display meshes
use an absolute linear deflection of 0.05 mm by default. Nonanalytic surfaces,
unqualified resource layouts, mirrors, external-file loading and complete feature
history remain unsupported. Existing `--csg` evaluation remains separately scoped.

## Limits and viewer

`SavedBodyLimits` defaults to 256 bodies, 100,000 B-Rep entities, 20,000 traversed
subshape occurrences, 250,000 triangles and 500,000 mesh vertices per body. Source
and output edge/vertex tolerances must not exceed 0.01 mm. The tolerance ceiling
is a rejection policy, not a request to expand source tolerances.

`read_saved_bodies` accepts `part_limits` and `limits`; `evaluate_saved_body`
accepts `geometry_limits`, `limits` and `linear_deflection_mm`.
`write_native_viewer(..., saved_brep=True, schema=schema)` also accepts
`saved_limits`, `geometry_limits` and the aggregate viewer limits. The CLI uses
the default saved-body/geometry limits and existing read/part/viewer limits.
These guards do not cap native-kernel execution time or total process memory.

The viewer uses `scope="qualified_saved_brep"`, reports evaluated/omitted saved
bodies in `scene.saved_brep`, and labels source components without drawing them
separately. Selecting the saved final body shows its properties and diagnostics.
The model summary continues counting saved records, which include components.
`--saved-brep` and `--csg` are alternative evaluation modes. The default viewer
continues to display only qualified standalone native primitives.

Local qualification includes the design-change tutorial, its changed root frame
and saved visibility, and independently authored boolean models. SDK comparisons
check final face counts, volume, area, centroid and appearance. Synthetic tests
cover independent unit/placement expectations, reordered/duplicate/missing
resources, invalid frames, missing catalogs, limits and optional dependencies.
