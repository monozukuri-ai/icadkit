# Supported capabilities

icadkit 0.3.0 is an experimental reader for selected iCAD SX ICD structures and
embedded Parasolid X_B resources. Support follows the actual record layout and
exact schema key, not a product-version label alone.

The unreleased [view and drawing APIs](drawing.md) classify old originals and
registered parts, inventory counted view records in both byte orders, and read
bounded saved 2D points, lines, circles, arcs and UTF-16LE text content. Text
layout, dimensions, hatching and projection evaluation remain unsupported.
Big-endian V6L1/V6L2/V7L1 also have a bounded internal `61000001` part inventory;
its coordinates remain raw and its units, evaluated placement and geometry
remain unqualified. Other old 3D owners are retained by the view inventory.

The current [part API](parts.md) reads qualified little-endian V7L2–V7L7 and
V8L1/V8L2/V8L3 inventories, hierarchy, snapshot definitions, names/comments,
extended text and saved external names. Counted metadata framing and retained
binary attributes improve traversal without inventing attribute semantics.
These qualified little-endian profiles expose root-relative millimetre frames. V8L3 revision 2
corrects placement for nonidentity saved roots; original stored coordinates
remain available. V7L6/V7L7 revision 2 and V8L1/V8L2/V8L3 revision 3
add signed occurrence orientation and bounded internal mirror geometry.
V7L2–V7L5 mirror placement remains unqualified. The separate
[assembly API](references.md) resolves explicit local reference roots or a caller
resolver for V7L6/V7L7/V8L1/V8L2/V8L3, composing qualified external placements and
mirrors. Default part reads remain single-file.

V7L2–V7L5 use separate `opaque_entities` profiles: part frames and structure
are qualified, while native geometry and appearance remain unsupported. Their
saved final bodies bind by native source ID, as in V7L6.
V7L6 additionally enables bounded standalone primitives. Registered parts and non-3D views are not promoted to empty models.

The current source also reads [saved 3D parameter tables](parameters.md) and the
root-owned entities of qualified V8L1 templates and V8L3 resaves. Local SDK
comparisons cover 76 template pairs, 1,602 saved parameter rows and four repeated
or rotated internal placements in V7L7/V8L3. Additional iCAD-authored probes
verify disabled conditions and nonempty explanations. This adds inventory and
saved-field access; it does not qualify full standard-part geometry or formula
execution. The changes have not been released or checked in remote platform CI.
Qualified template entity headers expose saved IDs, raw types, layer and
visibility; palette/face appearance and geometry remain unqualified.

[Native parameter access](native.md) supports qualified boxes, cylinders, full
spheres, circular cones/frusta and full ring tori with saved appearance. V7L6, V7L7, V8L1 and V8L2
require complete standalone primitive owners; unknown/CSG records keep the
owner's list opaque. The [native viewer](viewer.md) displays these parameters
without optional dependencies and labels every view as a partial model.

The unreleased native reader additionally supports six-vertex straight polygon
extrusions, extrusions whose profile has straight and circular segments,
revolved profiles, and negative heights in qualified boxes and cylinders.
Holes, tapers, partial revolutions and other layouts remain unsupported.

With the `preview` extra, [V7L7 CSG](csg.md) evaluates bounded box/cylinder
programs, while [saved final bodies](saved-bodies.md) place qualified V7L6/V7L7/V8L1/V8L2/V8L3
solids through explicit resource bindings; V7L2–V7L5 bind the same way.
V8 bodies bind through saved association records, not through the marker word
that earlier source treated as a resource key. Saved spherical, conical and
toroidal surfaces, elliptical and intersection edges and cone apexes have
bounded conversion support. Unsupported programs, missing or repeated
bindings and unresolved external ancestry retain diagnostics instead of meshes.
Mirrored CSG history remains unsupported; saved final results can qualify
independently.
The separate [resource preview](preview.md) exports one supported solid to GLB
with caller-declared units and no assembly placement.

| Operation | Supported behavior | Scope boundary |
| --- | --- | --- |
| Header inspection | Both byte orders; leading MOD/DRW/RES framing; raw header fields and a CP932 name candidate | Unknown fields and later ranges remain unparsed |
| Views / classification | Qualified counted views, document vs registered part, record ranges and ownership | Complete means framing only; unknown structures remain opaque |
| Saved 2D drawing | Points, lines, circles, arcs in view-local mm; UTF-16LE text content | No sheet layout, font rendering, dimension or projection evaluation |
| Resource listing | Owner-based indexing of supported raw and zlib X_B layouts; V8 entity-to-resource association records | Does not infer parts, sharing or instances |
| Trailing container | Record framing in both byte orders; per-entity block identifiers, decoded bytes and qualified local bounds | Face/edge tables, older block revisions and the named table remain opaque |
| Extraction | Bounds, sizes, checksum, stream termination, alignment and X_B envelope validation | Does not parse geometry nodes |
| Raw geometry | Exact-schema node parsing and paginated access | Requires a matching built-in profile or explicit catalog |
| B-Rep | Backend-supported topology, analytic curves/surfaces and NURBS | Unsupported types remain partial with diagnostics |

## Interpreting results

`complete` applies only to the named scope. Successful raw parsing, B-Rep
construction or topology checks do not establish complete ICD model support.
The document container remains `partial` and its model remains `not_checked`.
Zero recognized resources does not mean the file has no native geometry or 2D
content.

Coordinates, measurements, IDs and header version bytes are preserved as source
values. Resource geometry does not infer length units, assembly/world transforms
or the current saved state, and does not establish part relationships.
The part API separately qualifies part frames; it does not map
resources into those frames.
Vertex bounds cover parsed vertices;
they need not include extrema of curved edges or surfaces. Area and volume are
available only when the backend can calculate them.

Whole-model reconstruction, general iCAD-native shape evaluation,
drawing interpretation, feature history, STEP export
and ICD writing remain unsupported. GLB is limited to the optional resource
preview contract; the native viewer separately tessellates supported parameters
for display without producing B-Rep or GLB.

## Schemas

Version 0.3.0 uses `parasolid-core 0.3.1` and the exact
`SCH_3401212_34101_13006` profile `icad-sch34101-13006-r2` (revision 2).
`icadkit.build_info()` reports the backend version and profile IDs;
`GeometryResult.schema` also reports the selected profile's hash. Revision 2 adds the reviewed
TORUS (54) declaration, so qualified trimmed-torus/swept-arc resources no longer
need an external catalog. Version 0.2.0 used core 0.3.0 and profile revision 1.
An explicit compatible catalog remains authoritative.

The current unreleased source uses the published `parasolid-core 0.3.4`
registry crate. Its reviewed profiles replace icadkit's temporary
`icad-legacy-13006-raw-r1` profile and support both raw parsing and B-Rep mapping:

| Exact key | Built-in profile (revision 1) |
| --- | --- |
| `SCH_1500137_15003_13006` | `icad-1500137-15003-13006-r1` |
| `SCH_1500245_15003_13006` | `icad-1500245-15003-13006-r1` |
| `SCH_1700223_16100_13006` | `icad-1700223-16100-13006-r1` |
| `SCH_1700256_16100_13006` | `icad-1700256-16100-13006-r1` |
| `SCH_1901315_19008_13006` | `icad-1901315-19008-13006-r1` |
| `SCH_2100293_20000_13006` | `icad-2100293-20000-13006-r1` |
| `SCH_2100311_20000_13006` | `icad-2100311-20000-13006-r1` |
| `SCH_2401260_20000_13006` | `icad-2401260-20000-13006-r1` |
| `SCH_2800188_28002_13006` | `icad-2800188-28002-13006-r1` |
| `SCH_2901199_28101_13006` | `icad-2901199-28101-13006-r1` |
| `SCH_3200152_32001_13006` | `icad-3200152-32001-13006-r1` |
| `SCH_3200252_32001_13006` | `icad-3200252-32001-13006-r1` |
| `SCH_3301231_33103_13006` | `icad-3301231-33103-13006-r1` |
| `SCH_2401000_20000` | `icad-2401000-20000-r1` |
| `SCH_2800000_28002` | `icad-2800000-28002-r1` |
| `SCH_2901000_28101` | `icad-2901000-28101-r1` |
| `SCH_3200000_32001` | `icad-3200000-32001-r1` |
| `SCH_3301000_33103` | `icad-3301000-33103-r1` |

The last five keys carry no base suffix and transmit no schema. Their profiles
declare complete layouts, and only for the node types checked under each key;
another type yields `schema.builtin_profile_uncovered_type` and needs a catalog.

In a local sample of 33,943 resource occurrences from 837 files, 33,078 parse
without a catalog, compared with 2,725 under core 0.3.3. Every one of them has
the same raw, B-Rep and topology status as the explicit catalog path: 32,771
complete source B-Reps, 53 partial due to unsupported SPUN_SURF (68) geometry,
and 254 that fail B-Rep mapping in both paths, 253 of them for a trimmed curve
whose parameters are stored as null. The other 865 are not covered: keys
without a profile, such as `SCH_2601246_26105_13006` and `SCH_2601000_26105`,
and resources with a base type that their profile does not admit, such as
OFFSET_SURF (60). All but two of them parse with an explicit catalog. Base
types beyond the shared subset are admitted only by the exact profiles that
reviewed them.
SPUN_SURF parameters remain available as raw fields; a catalog does not make
that geometry supported. `require_complete()` rejects these partial B-Reps,
while `require_complete("raw_geometry")` succeeds.

Profile IDs and hashes now come directly from parasolid-core. No schema key or
provider provenance is rewritten, and no vendor catalog is bundled. These checks
qualify resource parsing and source B-Rep mapping; OCCT conversion, saved-body
ownership and whole-model display have separate requirements.

Profile availability does not mean that every geometry type or every ICD file
using that schema is supported. A missing exact profile produces a diagnostic;
icadkit does not substitute a nearby schema version.

Built-in field labels and generic pointer classifications need not match an
external catalog's schema metadata. In the V34 comparisons, type 74 field 3 uses
generic pointer class `0` in the built-in subset and `1001` in the catalog;
decoded values, byte ranges and source B-Rep still agree.

For other supported keys, provide a `SchemaCatalog` with an explicit expected ID
and optionally an expected SHA-256. icadkit does not search your machine or read
catalog locations from environment variables. Schema catalogs and vendor sample
models are not distributed with the package. See [schema selection](api.md#schema-selection).

## Platforms and resource limits

The supported wheel configurations use standard GIL-enabled CPython 3.10–3.14:

| Platform | Architecture | Minimum runtime |
| --- | --- | --- |
| Linux | x86_64 | glibc 2.28 |
| Windows | x86_64 | A CPython-supported Windows version |
| macOS | ARM64 | macOS 11 |

PyPy, free-threaded CPython and other platform/architecture combinations are not
qualified by this release's wheel checks.
The optional preview dependencies have their own platform requirements; the
locked OCCT wheels require glibc 2.31+ on Linux. See [preview](preview.md).

Input, record, resource, node and schema limits reject oversized operations.
These limits do not cap total process memory: document bytes, decoded payloads,
raw nodes and B-Rep structures can coexist. Process one resource at a time and
use small pages for large NURBS entities. The [API reference](api.md) lists the
defaults and exception behavior.

The qualified V7L5 originals contain an unlinked document root only. A child
record or nonzero hierarchy link yields `parts.legacy_root_only`; V7L5 child
assemblies are not inferred from the other V7 profiles.
