# Saved final bodies

Version 0.3.5 supports qualified V7L6/V7L7 and V8L1/V8L2/V8L3 display paths using saved final
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

The adapter accepts qualified little-endian V7L2–V7L7 and V8L1/V8L2/V8L3,
complete part/resource indexes, qualified internal owners (including mirrors
where the profile qualifies them) and saved root frames.
External ancestors in a single-file index prevent evaluation. The separate
[assembly viewer](references.md) evaluates each explicitly resolved source in its
own document, then applies the external occurrence transform to its saved result.
Final type-85 markers are distinguished from component markers. Each binding is
an explicit saved reference: a native entity identifier stored in the resource
header, or a saved association record. Resource order, geometry and mass
properties never supply ownership. Unknown layouts, missing or repeated
references are rejected. Imported-solid and evaluated-native final markers are
qualified separately from feature replay.

V8 files that store numbered resources also store one fixed 96-byte
**association record** per entity: the entity identifier, the resource number
and the entity frame. `Document.resource_associations` exposes these records.
A V8 final marker binds through the single association for its identifier; the
association frame must equal the marker frame byte for byte. The word at marker
offset `+32` is not a resource reference: after copies and edits it keeps an
earlier value while resource numbers are reassigned, so it never selects a
resource. Earlier source used that word and therefore rejected owners with
several distinct values (`saved.owner_resource_keys`) and bound some bodies to
another body's resource. Both are replaced by the association rule.

The observed final-result state words at marker offset `+44` include
`01000004`, `01800004`, `03000004`, `03800004`, `04000004` and `04800004`,
with the saved visibility bit `40` added when present. Their other bits are
retained without assigning semantics. Offset `+28` admits the observed words
`00000000` and `01000000`, with the same unique-ID, owner, resource and frame
checks for both. Other states remain unsupported. Local checks add 2,305 bound
records in the installed corpus; separate SDK comparisons match five newly
bound records in volume, area and centroid. Accepting a marker does not qualify
its resource's schema, surfaces or tessellation.

| Profile | Binding | Frame applied after scaling resource metres to mm |
| --- | --- | --- |
| V7L2–V7L6 | Native marker source ID to a unique type-134/version-4 resource | `inverse(saved_root_frame) * saved_resource_frame` |
| V7L7 | Native marker source ID to a unique type-134/version-5 resource | `inverse(saved_root_frame) * saved_resource_frame` |
| V8L1/V8L2/V8L3 | The marker's unique association record to a unique type-135/version-6 resource number | `inverse(saved_root_frame) * saved_marker_frame` |
| Original V8L1 template | Native source ID to a unique type-134/version-5 resource; root-owned 704-byte part with identity root and identical marker/resource frames | Qualified marker frame |

V8 occurrences can share one resource number while retaining distinct native
IDs, owners and frames; each has its own association record. Multiple bodies can
belong to the same part in every profile. A marker without an association, with
several associations, or whose association names a missing or repeated resource
number has `saved.binding`. A frame that differs between marker and association
has `saved.association_frame`; neither frame is chosen.
Part placement is never applied again. V8 profile revision 2 and later normalize the saved root once,
matching the reopened SDK coordinate system. The older V8L1 template binding
rejects a resource numbered like the marker's `+32` word, conflicting frames and
unqualified owners.
`resource_source_id`, `binding_kind` (`native_source_id` or
`resource_association`) and `frame_source` describe the binding.
`resource_frame_range` locates the frame in the resource header for V7L6/V7L7 and
the native marker for V8 profiles. Original bytes and payload hashes remain available.
The final marker supplies visibility, palette index and layer; colors are
illustrative and layer visibility is not interpreted.

The current converter covers solid bodies with planes, cylinders, spheres,
cones and tori, bounded by lines, circles, ellipses, source-identified
surface-intersection curves and explicit trims of those curves. This is a
bounded surface/boundary combination, not support for arbitrary solids
containing those surfaces. Faces require explicit boundary loops. A conical
face that ends in its apex is stored with a loop of one vertex and no edge; the
adapter closes it with one degenerated boundary edge when that vertex lies at
the cone's apex. Vertex loops elsewhere, on other surfaces, or as a face's only
loop remain unsupported (`saved.half_edge`). Intersection curves are rebuilt
by the pinned adapter from their two support surfaces and saved chart points;
closed or unresolved branches are rejected without a substitute curve.
Shared source vertices/edges/loops and
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

Version 0.3.5 uses `parasolid-core 0.3.5`, which also supplies reviewed
B-Rep mappings for [twenty-six further schema keys](support.md#schemas). This
removes their catalog requirement within the qualified subsets. SPUN_SURF
remains unsupported, and offset surfaces are read but not converted. Resource parsing does not establish a saved-body binding or extend
the converter's supported surfaces and topology.

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
within-owner entity mirrors qualify through unique native source IDs, and V8
within-owner mirror copies through their association records.

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

V7L2–V7L6 use the observed 48-byte final marker and a unique type-134/version-4
resource binding. CSG operands/programs remain opaque; this path reads the saved
final B-Rep without evaluating history. V7L2–V7L5 keep their other limits:
entity geometry and appearance stay opaque and mirrored placements are
unqualified, so bodies under a mirrored owner are not bound there.

## Local evidence for bindings and conversion

In a local sample of 305 V8 files with numbered resources, every one of 28,685
indexed resources is named by an association record and every final marker has
exactly one; 27,118 marker frames equal their association frames byte for byte,
while the word at marker `+32` differs from the associated resource number for
6,957 markers. Against the previous rule, 6,739 bodies that were rejected now
bind and 2,670 bodies now bind to a different resource. For every one of 6,059
evaluated bodies the saved [per-entity box](trailer.md) contains the solid in
its local frame, and it is tight for 6,040 of them.

Of 29,703 distinct bound resources in the sample, 27,093 convert to valid
solids. An explicit schema catalog is needed only where no built-in profile
applies: for six of these resources, compared with 27,796 under core 0.3.3.
Results agree with the catalog path, except that 253 resources whose line trims
store unset parameters are now mapped; 168 of them convert. The others keep
their diagnostics: unsupported curve or surface kinds, kernel validation
failures, an intersection limit kind the mapper does not accept and SPUN_SURF
surfaces.

Saved SDK observations cover 1,789 evaluated bodies from V7L2–V7L7 and
V8L1–V8L3 inputs: volume, area and centroid match for all of them, including
153 with elliptical edges, 347 with intersection curves and 103 cone apexes.
V7L5 has no SDK observation with an evaluated body; its seven evaluated bodies
are checked against the saved bounds only. Bodies that fail conversion keep
their diagnostics; none is reported as matched.
