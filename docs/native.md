# Native primitive parameters and appearance

For qualified V7L7 final boolean bodies, use the separate [CSG API](csg.md)
or `view --csg` with the `preview` extra. The native-primitive scope described
here keeps operands opaque.

Version **0.2.0** adds saved native entities to `Document.read_parts()`.
It reads qualified V8L3 boxes and cylinders, and selected stored appearance
fields. It does not evaluate native B-Rep, CSG, tessellation or a complete scene.

The 0.3.0 [native viewer](viewer.md) can tessellate the supported
box/cylinder/full-sphere/cone/torus parameters for an explicitly partial display with a part tree.
Version 0.3.5 also decodes qualified V7L6/V7L7/V8L1/V8L2 standalone primitive
owners and saved entity appearance. These profiles require a complete saved hierarchy/index and
an entire internal owner's list of qualified primitive
headers. An unknown or CSG record keeps the whole owner's list opaque; primitive
operands are never drawn as a completed boolean result. Qualified internal
mirrors are included; external owners and unsupported layouts remain unavailable.

It also accepts negative heights in qualified nonmirrored boxes
and in cylinders, and adds the bounded polygon extrusion, profile extrusion and
revolution layouts described below.

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

The `polygon_extrusion` kind, added in 0.3.5, exposes six distinct XY vertices in
`profile_points` and a positive `height`. Its qualified 368-byte record stores
two identical closed straight profiles and a zero trailer. Concave and convex
simple polygons are accepted in either winding. The viewer triangulates both
caps and preserves outward winding. Mirrored records with the qualified negative
height use the same signed-height convention as mirrored boxes. Local checks
match 21 mirrored records to saved SDK volume, area and centroid observations.
Holes, curved segments, tapers and other vertex counts remain unsupported; degenerate or
self-intersecting profiles are invalid. `profile_points` is `None` for other
kinds. As with other primitives, older versions require a complete qualified
standalone owner before any member is exposed as geometry.

A `profile_extrusion` is the general saved extrusion: frame, signed height and
a closed profile of straight and circular segments. `NativePrimitive.profile`
is a tuple of `ProfileSegment` values in profile order, each with `kind`
(`line` or `arc`), `start` and `end`; an arc also has `center` and a signed
`sweep_angle` in radians, positive from +X toward +Y. In the record an arc
follows its start vertex as two flagged elements, the centre and the sweep; a
trailing bit field marks those elements. Only the first value of the sweep
element is used: the second is arbitrary in saved files and is ignored. The
profile must return to its first vertex, either by a repeated vertex or by a
final arc. Zero-length repeats are dropped and collinear vertices are kept.
The viewer draws arcs as chords, with a full turn using the same chord count as
a cylinder. Open, degenerate or self-intersecting profiles and invalid arcs are
`invalid` (`native.profile`); unqualified flag patterns are unsupported
(`native.profile_layout`). The six-vertex `polygon_extrusion` layout is separate
and unchanged.

A `revolution` turns the polyline in `revolution_profile`, a tuple of
`(radius, axial)` pairs, completely about the frame Z axis. The region is closed
along the axis: an end with a positive radius gets a flat disc. `height`,
`radius` and the profile fields of other kinds are `None`. No sweep angle is
stored in this layout, so partial revolutions are not represented. Negative
radii, repeated points and self-intersecting or degenerate profiles are
`invalid` (`native.revolution`).

Nonmirrored boxes, cylinders, polygon extrusions and profile extrusions can
store a negative height. The reader exposes its absolute value and reverses only
the Z column of the frame; the original sign remains in `raw_bytes`. This sign
does not imply an entity mirror, so `mirror_convention` remains `None` for
these nonmirrored records.

Matrices contain four rows and act on column vectors. The first three columns
are the X, Y and Z axes; the fourth is the origin. A box spans the saved X/Y
bounds and Z from zero to `height`. A cylinder's bottom centre is `(0, 0, 0)` and
its top centre is `(0, 0, height)` in this frame.

The exposed `world_transform` is in **3DGLOBAL coordinates**; do not multiply it
by the part frame again. All qualified V7L6/V7L7/V8L1/V8L2/V8L3 profiles use
`inverse(saved_root_frame) * stored_primitive_frame`, independently of the owning
part frame. Qualified negative-height box/cone/polygon records additionally reverse
the stored Z column once. Raw bytes retain the original stored frame. If the root context is
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

For the observed mirrored box/cone/polygon layouts, the saved height is negative.
`height` exposes its positive magnitude and `world_transform` reverses the
stored Z column, preserving the same geometry with a negative determinant.
`mirror_convention="signed_height"` identifies this normalization. The raw
negative height remains available in `raw_bytes`. Positive mirrored box/cone/polygon
heights remain unqualified and are rejected. Nonmirrored boxes and qualified
polygons accept either height sign as described above; nonmirrored cones retain
their positive-height requirement.

Full cylinders with a positive height, full spheres, full ring tori and
revolutions use `mirror_convention="symmetric_frame"`: their stored frames
already place the symmetric shape. A mirrored cylinder or profile extrusion with
a negative height uses `signed_height`, like a mirrored box. A mirrored profile
extrusion with a positive height uses `stored_profile`: its saved frame and
profile already describe the reflected solid and nothing is reversed. Qualified mirrored tori require the observed negative full
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

For the 0.3.5 cylinder sign, profile extrusion and revolution layouts,
saved SDK observations match volume, area and centroid for 290 negative-height
cylinders (30 of them mirrored), 83 profile extrusions (19 mirrored) and 133
revolutions (58 mirrored). In a local sample all 6,638 extrusion records of this
layout have a closed profile under the rule above; 987 standalone extrusions and
217 standalone revolutions decode and tessellate with the expected volume.
Records used as operands of a saved boolean body stay opaque in the part
inventory. Hexagonal prism records occur only as such operands in the checked
inputs; the [CSG reader](csg.md) decodes them as operands. A profile extrusion
that is the only record of a saved single-leaf body is likewise read by the CSG
reader, with the same profile rules, and not by this inventory.

The v0.3 additions have offline checks against saved/reopened SDK
observations for ten cone/frustum/torus files, including oblique and dimension
holdouts in V7L7/V8L3. Analytic volume, surface area, centroid and stored appearance
match; this is not a fresh SDK run or native older-product qualification.
