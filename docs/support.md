# Supported capabilities

icadkit is an experimental reader for selected iCAD SX ICD structures and
embedded Parasolid X_B resources. Support follows the actual record layout and
exact schema key, not a product-version label alone.

The [view and drawing APIs](drawing.md) added in 0.3.5 classify old originals and
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

Version 0.3.5 also reads [saved 3D parameter tables](parameters.md) and the
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

The 0.3.5 native reader additionally supports six-vertex straight polygon
extrusions, extrusions whose profile has straight and circular segments,
revolved profiles, and negative heights in qualified boxes and cylinders.
Holes, tapers, partial revolutions and other layouts remain unsupported.

With the `preview` extra, [V7L7 CSG](csg.md) evaluates bounded programs of
boxes, cylinders, convex six-vertex prisms and line/arc profile extrusions,
while [saved final bodies](saved-bodies.md) place qualified V7L6/V7L7/V8L1/V8L2/V8L3
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
With the same extra, [part shapes](shapes.md) collect a part's qualified
solids (saved bodies, else CSG results, else standalone primitives) into one
OCCT compound in the part or document frame, report per part whether and why
it cannot be converted, and write STEP or BREP; 1,442 local SDK part
observations match in volume, area and centroid.

| Operation | Supported behavior | Scope boundary |
| --- | --- | --- |
| Header inspection | Both byte orders; leading MOD/DRW/RES framing; raw header fields and a CP932 name candidate | Unknown fields and later ranges remain unparsed |
| Views / classification | Qualified counted views, document vs registered part, record ranges and ownership | Complete means framing only; unknown structures remain opaque |
| Saved 2D drawing | Points, lines, circles, arcs in view-local mm; UTF-16LE text content | No sheet layout, font rendering, dimension or projection evaluation |
| Resource listing | Owner-based indexing of supported raw and zlib X_B layouts; V8 entity-to-resource association records | Does not infer parts, sharing or instances |
| Trailing container | Record framing in both byte orders; per-entity block identifiers, decoded bytes, qualified local bounds, and the identity and adjacency of face and edge entries | Surface/curve parameters, older block revisions and the named table remain opaque |
| Extraction | Bounds, sizes, checksum, stream termination, alignment and X_B envelope validation | Does not parse geometry nodes |
| Raw geometry | Exact-schema node parsing and paginated access | Requires a matching built-in profile or explicit catalog |
| B-Rep | Backend-supported topology, analytic curves/surfaces and NURBS | Unsupported types remain partial with diagnostics |
| Part shapes | One OCCT compound per part from saved bodies, CSG results or native primitives, in the part or document frame, with mass properties, drawability status and STEP/BREP export; see [part shapes](shapes.md) | Needs the `preview` extra; bodies the readers do not expose or the adapter rejects stay absent with diagnostics |
| 2D writing | New 2D views with their placement records, point/line/circle/arc, text, length and diameter dimension and hatch records, entity and view deletion, in a copy of a document serialized through the container model; see [writing](writing.md) | `corpus_consistent` only: reproduces iCAD files up to save noise in local checks and has never been opened by iCAD |

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

The reader uses the published `parasolid-core` registry crate at the exact
version pinned in `Cargo.toml` and reported by `icadkit info`; its reviewed
profiles support both raw parsing and B-Rep mapping, and the SPUN_SURF (68)
mapping arrived with 0.3.6.
The V34 profile is now `icad-sch34101-13006-r3` and the embedded V30 profile
`icad-sch30000-13006-r6`: both admit further base types observed under their
keys. The other exact keys are:

| Exact key | Built-in profile |
| --- | --- |
| `SCH_1500137_15003_13006` | `icad-1500137-15003-13006-r1` |
| `SCH_1500245_15003_13006` | `icad-1500245-15003-13006-r1` |
| `SCH_1700223_16100_13006` | `icad-1700223-16100-13006-r1` |
| `SCH_1700256_16100_13006` | `icad-1700256-16100-13006-r1` |
| `SCH_1901261_19008_13006` | `icad-1901261-19008-13006-r1` |
| `SCH_1901315_19008_13006` | `icad-1901315-19008-13006-r1` |
| `SCH_2100293_20000_13006` | `icad-2100293-20000-13006-r2` |
| `SCH_2100311_20000_13006` | `icad-2100311-20000-13006-r1` |
| `SCH_2401260_20000_13006` | `icad-2401260-20000-13006-r1` |
| `SCH_2601246_26105_13006` | `icad-2601246-26105-13006-r1` |
| `SCH_2800188_28002_13006` | `icad-2800188-28002-13006-r1` |
| `SCH_2901199_28101_13006` | `icad-2901199-28101-13006-r1` |
| `SCH_3200152_32001_13006` | `icad-3200152-32001-13006-r1` |
| `SCH_3200252_32001_13006` | `icad-3200252-32001-13006-r1` |
| `SCH_3301231_33103_13006` | `icad-3301231-33103-13006-r2` |
| `SCH_1300218_13006` | `icad-1300218-13006-r1` |
| `SCH_1302234_13006` | `icad-1302234-13006-r1` |
| `SCH_1500000_15003` | `icad-1500000-15003-r1` |
| `SCH_1700000_16100` | `icad-1700000-16100-r1` |
| `SCH_1901000_19008` | `icad-1901000-19008-r1` |
| `SCH_2401000_20000` | `icad-2401000-20000-r1` |
| `SCH_2601000_26105` | `icad-2601000-26105-r1` |
| `SCH_2800000_28002` | `icad-2800000-28002-r1` |
| `SCH_2901000_28101` | `icad-2901000-28101-r1` |
| `SCH_3200000_32001` | `icad-3200000-32001-r1` |
| `SCH_3301000_33103` | `icad-3301000-33103-r1` |

The last eleven keys carry no base suffix and transmit no schema. Their profiles
declare complete layouts, and only for the node types checked under each key;
another type yields `schema.builtin_profile_uncovered_type` and needs a catalog.

In a local sample of 33,943 resource occurrences from 837 files, 33,913 parse
without a catalog, compared with 2,725 under core 0.3.3 and 33,078 under 0.3.4.
Every one of them has the same raw, B-Rep and topology status as the explicit
catalog path: 33,836 complete source B-Reps, 66 partial due to unsupported
SPUN_SURF (68) geometry, and 11 that fail B-Rep mapping in both paths, ten of
them for an intersection curve with a limit kind the mapper does not accept.
Since `parasolid-core 0.3.6` maps SPUN_SURF, the same
sample has 33,902 complete source B-Reps and the same 11 failures.
The other 30 are not covered: keys of schema revisions 8008 and 12103, six
resources under the standard V30 key with a blend or offset type, and two that
no catalog reads either. Base types beyond the shared subset, including
OFFSET_SURF (60), are admitted only by the exact profiles that reviewed them.
A trimmed curve on a line whose two parameters are stored as null is mapped
from its stored end points; other unset parameters remain errors.
In version 0.3.5, SPUN_SURF parameters remain available as raw fields and a
catalog does not make that geometry supported: `require_complete()` rejects
these partial B-Reps, while `require_complete("raw_geometry")` succeeds. Since
`parasolid-core 0.3.6` the surface maps as `spun` (profile curve, spin axis,
degeneracy points and parameters, x axis), and its [saved
bodies](saved-bodies.md) convert within the adapter's limits.

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
