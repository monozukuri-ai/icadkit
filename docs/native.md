# Native primitive parameters and appearance

For qualified V7L7 final boolean bodies, use the separate [CSG API](csg.md)
or `view --csg` with the `preview` extra. The native-primitive scope described
here keeps operands opaque.

Version **0.2.0** adds saved native entities to `Document.read_parts()`.
It reads qualified V8L3 boxes and cylinders, and selected stored appearance
fields. It does not evaluate native B-Rep, CSG, tessellation or a complete scene.

The 0.3.0 [native viewer](viewer.md) can tessellate the supported
box/cylinder/full-sphere/cone/torus parameters for an explicitly partial display with a part tree.
The current source also decodes qualified V7L6/V7L7/V8L1/V8L2 standalone primitive
owners and saved entity appearance. These profiles require a complete saved hierarchy/index and
an entire internal owner's list of qualified box/cylinder/sphere/cone/torus
headers. An unknown or CSG record keeps the whole owner's list opaque; primitive
operands are never drawn as a completed boolean result. Qualified internal
mirrors are included; external owners and unsupported layouts remain unavailable.

```python
import icadkit

index = icadkit.read("assembly.icd").read_parts()
for part in index.walk():  # Root-owned entities can also exist.
    for entity in part.entities:
        print(part.name, entity.appearance.color_index, entity.appearance.visible)
        primitive = entity.primitive
        if primitive is not None:
            print(primitive.kind, primitive.height, primitive.length_unit)
            print(primitive.box_dimensions, primitive.radius)
            print(primitive.world_transform)
        else:
            print(entity.geometry_status, entity.diagnostics)
```

## Ownership and retained entities

`Part.entities` is a tuple of `NativeEntity` objects in saved entity order. Each
has a snapshot `entity_id`, its owning `part_id` in `owner_id`, a source byte
range, raw type code, appearance, optional primitive and scoped diagnostics.
Unsupported shapes remain in this tuple. `source_id` is available only when the
entity header is qualified; it is not a persistent ID across saves.

Ownership follows the length-framed entity list after a part record, independently
of supported geometry. Attribute records are handled separately in
`Part.properties` or as raw `Part.opaque_attributes`. Unknown entity framing stops traversal without searching for
plausible signatures. Some opaque metadata before Parasolid entity lists is
qualified for traversal; its contents are not treated as primitive parameters.
A saved Parasolid wrapper remains an unsupported entity in this API. No mapping
from that entity to `Document.resources` is inferred.

## Primitive contract

In version 0.3.0, `NativePrimitive.kind` is `box`, `cylinder`, `sphere`,
`cone` or `torus`.
The qualified layout retains:

| Field | Box | Cylinder |
| --- | --- | --- |
| `height` | Positive Z extent | Positive axial length |
| `x_bounds`, `y_bounds` | Saved cross-section limits | `None` |
| `box_dimensions` | `(xmax-xmin, ymax-ymin, height)` | `None` |
| `radius` | `None` | Positive radius |
| `world_transform` | Saved cross-section frame | Bottom-centre frame |

For a full sphere, `radius` is positive and `world_transform` is the centre
frame. `height`, `x_bounds`, `y_bounds` and `box_dimensions` are `None`. Only the
observed full-sphere extent is evaluated; other saved extents retain diagnostics.

A coaxial circular cone/frustum uses `radius` for its positive base radius,
`top_radius` for its nonnegative top radius, and positive `height`. Its frame is
at the bottom centre. `top_radius=0` represents an apex. Saved nonzero lateral
offsets remain unsupported.

A full ring torus exposes `major_radius` and `minor_radius`, with
`major_radius > minor_radius > 0`. Its frame is at the centre; the Z axis is the
axis of revolution. `radius` and `height` are `None`. Partial sweeps, horn/spindle
tori and unknown extents remain unsupported. These added fields are `None` for
other primitive kinds. Boxes remain the only kind with non-null box bounds.

Matrices contain four rows and act on column vectors. The first three columns
are the X, Y and Z axes; the fourth is the origin. A box spans the saved X/Y
bounds and Z from zero to `height`. A cylinder's bottom centre is `(0, 0, 0)` and
its top centre is `(0, 0, height)` in this frame.

The exposed `world_transform` is in **3DGLOBAL coordinates**; do not multiply it
by the part frame again. All qualified V7L6/V7L7/V8L1/V8L2/V8L3 profiles use
`inverse(saved_root_frame) * stored_primitive_frame`, independently of the owning
part frame. Qualified mirrored box/cone records additionally reverse the stored Z column
once, as described below. Raw bytes retain the original stored frame. If the root context is
unavailable or normalization overflows, `primitive=None` with a diagnostic. A box's saved reference origin can be the centre of its
bottom cross-section instead of the creation corner. The axes are validated as
orthonormal and are never silently normalized. Nonfinite values, nonpositive
dimensions, reversed bounds and dimension overflow produce an invalid status
with `primitive=None`.

`length_unit="mm"` and `length_unit_source="qualified_native_profile"` describe
these native parameter layouts. They do not establish embedded Parasolid units.
`byte_range` and `raw_bytes` retain the exact frame and dimension bytes. For
unsupported or invalid parameters, the containing entity's range still allows
`doc.source_bytes(entity.byte_range)` to recover the entire original record.

Unqualified mirror layouts, partial spheres/tori, offset cones, boolean results and other native
layouts remain unsupported. In particular, an iCAD face-colour change or boolean
operation can convert a primitive into a Parasolid representation: its former
box or cylinder parameters must not be reported as the current shape. Qualified
primitive parameters do not establish face orientation, watertight B-Rep,
resource ownership or whole-model geometry completeness.

## Qualified entity mirrors

Internal mirrors are qualified in V7L6/V7L7 and V8L1/V8L2/V8L3. The entity mirror
flag is independent of its owning part flag; a mirrored entity can belong to an
unmirrored part. Its saved frame/parameters already incorporate the operation.
Neither the part frame nor its orientation matrix is applied a second time.

For the observed mirrored box/cone layouts, the saved height is negative.
`height` exposes its positive magnitude and `world_transform` reverses the
stored Z column, preserving the same geometry with a negative determinant.
`mirror_convention="signed_height"` identifies this normalization. The raw
negative height remains available in `raw_bytes`. A negative nonmirrored height
or positive mirrored box/cone height is not repaired and remains invalid.

Full cylinders, full spheres and full ring tori use
`mirror_convention="symmetric_frame"`: their stored frames already place the
symmetric shape. Qualified mirrored tori require the observed negative full
sweep; other extent/flag combinations retain diagnostics. Nonmirrored primitives
have `mirror_convention=None`. The viewer reverses triangle winding for a negative
geometry determinant so derived normals stay outward. Unsupported extrusions,
CSG operands and unknown signed layouts remain opaque.

## Appearance contract

`NativeAppearance` exposes a **saved entity palette index**, visibility flag and
layer number, together with a status, raw header bytes and byte range. Palette
indices are not RGB values. Extended palette indices, including their high bit,
are preserved. No palette file or environment configuration is loaded.

The visibility flag is independent of application view/layer settings, so it
does not guarantee that a GUI displays the entity. There is no synthesized
`Part.visible` or inherited part colour. In the authored iCAD cases, changing a
parent's colour or visibility updated descendant entities; a child could then
store a different colour or visibility. These are read directly, without an
inheritance algorithm. Multiple entities under one part can differ.

Appearance is qualified for the observed box, cylinder, sphere, cone and torus headers,
including their mirror flag variants. Geometry can therefore be unsupported
while appearance is complete. Other headers, per-face colour, RGB mapping,
transparency, view-level hiding and effective appearance remain unqualified.
Raw values and the entity record are retained rather than replaced with defaults.

## Status, CLI and validation

`entity.geometry_status` describes primitive parameters; `appearance.status`
describes stored appearance. Each part has `native_geometry_status` and
`appearance_status`, aggregated into `index.status.native_geometry` and
`index.status.appearance`. Empty internal parts have no entity parameters to
check. Unknown framing or unloaded external parts prevent complete scope status.
`Part.geometry_status` remains `not_checked`: native parameter decoding does not
certify the resource B-Rep or placed model geometry.

```sh
icadkit parts assembly.icd --json
icadkit parts assembly.icd --include-root --require-native --json
```

Rows include entities, raw bytes as hex, primitive parameters and separate
statuses. The existing `parts` exit contract continues to cover part structure,
frames and selected attributes. `--require-native` additionally requires complete
native parameters and appearance: unsupported results return `3`, invalid
parameters return `1`, and existing input/limit errors retain their exit codes.
Check `--include-root` when geometry belongs directly to the document root.
`PartLimits.max_entities` bounds the traversed entity and metadata records.

Private qualification uses 38 saved/reopened iCAD SDK cases with 43 supported
primitives, including unequal dimensions, radius/height changes, non-axis-aligned
frames, hierarchy, hidden entities, extended colours, independent examples and
mixed native/Parasolid data. Box vertices/planes/edges and cylinder caps/axes/
radii/surface points are checked alongside SDK centroids, area and volume.
Incorrect unit scaling, repeated part transforms and transposed rotation are
negative controls. CAD files, SDK binaries and vendor catalogs are not distributed;
public tests use independently authored synthetic records and malformed inputs.

The v0.3 additions have offline checks against saved/reopened SDK
observations for ten cone/frustum/torus files, including oblique and dimension
holdouts in V7L7/V8L3. Analytic volume, surface area, centroid and stored appearance
match; this is not a fresh SDK run or native older-product qualification.
