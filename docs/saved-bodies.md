# Saved final bodies

The current source supports qualified V7L6/V7L7 and V8L1/V8L2/V8L3 display paths using saved final
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

The adapter accepts qualified little-endian V7L6/V7L7/V8L1/V8L2/V8L3, complete part/resource
indexes, qualified internal owners (including mirrors) and saved root frames.
External ancestors in a single-file index prevent evaluation. The separate
[assembly viewer](references.md) evaluates each explicitly resolved source in its
own document, then applies the external occurrence transform to its saved result.
Final type-85 markers are
distinguished from component markers. Exactly one indexed resource must match
the marker's profile-specific key, and the native marker ID must be unique.
Resource order never supplies ownership. Unknown layouts and ambiguous keys
are rejected. Imported-solid and evaluated-native final markers are qualified
separately from feature replay.

| Profile | Binding | Frame applied after scaling resource metres to mm |
| --- | --- | --- |
| V7L6 | Native marker source ID to a unique type-134/version-4 resource | `inverse(saved_root_frame) * saved_resource_frame` |
| V7L7 | Native marker source ID to a unique type-134/version-5 resource | `inverse(saved_root_frame) * saved_resource_frame` |
| V8L1/V8L2/V8L3 | Explicit marker resource key to a unique type-135/version-6 resource | `inverse(saved_root_frame) * saved_marker_frame` |
| Original V8L1 template | Native source ID to a unique type-134/version-5 resource; root-owned 704-byte part with identity root and identical marker/resource frames | Qualified marker frame |

V8 occurrences can share a resource key while retaining distinct native IDs,
owners and frames. Multiple V7 bodies can belong to the same part. In V8,
multiple distinct final-marker resource keys within one owner are rejected with
`saved.owner_resource_keys`: entity-mirror copies can reorder resource payloads
without updating these numeric keys. Shared instances of one key remain qualified.
The reader never guesses the association from order, geometry or mass properties.
Part placement is never applied again. V8 profile revision 2 and later normalize the saved root once,
matching the reopened SDK coordinate system. The older V8L1 template binding
rejects competing resource keys, conflicting frames and unqualified owners.
`resource_source_id`, `binding_kind` and `frame_source` describe the binding.
`resource_frame_range` locates the frame in the resource header for V7L6/V7L7 and
the native marker for V8 profiles. Original bytes and payload hashes remain available.
The final marker supplies visibility, palette index and layer; colors are
illustrative and layer visibility is not interpreted.

The current converter covers solid bodies with planes, cylinders, spheres,
cones, tori, lines, circles and explicit line/circle trims. This is a bounded
surface/boundary combination, not support for arbitrary solids containing those
surfaces. Faces require explicit boundary loops. Shared source vertices/edges/loops and
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
unqualified resource layouts, implicit external-file loading and complete feature
history remain unsupported. Existing `--csg` evaluation remains separately scoped.

An exact built-in schema profile can still lack a base type required by a
resource. Such resources need the explicitly matching catalog. Version 0.3.0
uses `parasolid-core 0.3.1` and exact V34 profile revision 2, which adds
TORUS (54): qualified trimmed-torus and swept-arc solids no longer require a
catalog. Other uncompiled types retain a diagnostic when no catalog is supplied.
Catalogs are not bundled.

## Saved mirrors

For qualified internal part mirrors in V7L6/V7L7/V8L1/V8L2/V8L3, reflection is
already present in the saved final B-Rep and its proper marker/resource frame.
The adapter applies only the frame in the table above. It does not reflect by
`Part.is_mirror` or multiply an occurrence orientation matrix. This prevents
reflection from being applied twice. CSG history under mirrored ancestry remains
unsupported by the separate `--csg` evaluator.

Local save/reopen checks cover axis/oblique planes, same/different-plane double
mirrors, mirrored parents and children, rotation/translation after reflection,
and changed root coordinates. The evaluated analytic volume, area and centroid
match SDK observations; closed mesh winding is checked separately. V7L7
within-owner entity mirrors also qualify through unique native source IDs.
The ambiguous V8 within-owner case above remains explicitly unsupported.

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
separately. `resources_evaluated` counts distinct resources; `evaluated` counts
body occurrences, including copies that share a resource. Selecting the saved
final body shows its properties and diagnostics.
The model summary continues counting saved records, which include components.
`--saved-brep` and `--csg` are alternative evaluation modes. The default viewer
continues to display only qualified standalone native primitives.

Local qualification includes the design-change tutorial, its changed root frame
and saved visibility, and independently authored boolean models. SDK comparisons
check final face counts, volume, area, centroid and appearance. Synthetic tests
cover independent unit/placement expectations, reordered/duplicate/missing
resources, invalid frames, missing catalogs, limits and optional dependencies.

V7L6 uses the observed 48-byte final marker and a unique type-134/version-4
resource binding. CSG operands/programs remain opaque; this path reads the saved
final B-Rep without evaluating history. V7L2–V7L5 do not enable this adapter.
