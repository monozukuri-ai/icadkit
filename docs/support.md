# Supported capabilities

icadkit 0.2.0 is an experimental reader for selected iCAD SX ICD structures and
embedded Parasolid X_B resources. Support is determined by the actual layout and
exact schema key, not a product-version label or filename extension alone.

Version 0.2.0 additionally supports a bounded
[native part API](parts.md): hierarchy, millimetre part frames, snapshot
definitions, saved names/comments/extended text and unresolved external names.
Mirrored frames, custom attributes and geometry ownership remain unsupported.
[Native parameter access](native.md) adds qualified boxes/cylinders and stored
entity palette indices, visibility and layers. Native B-Rep and effective
appearance remain unsupported. The resource geometry boundaries below apply.
The optional [preview extra](preview.md) adds bounded solid resource
tessellation, metre-based GLB output and a local browser viewer. It requires an
explicit source unit and does not apply part placements or saved appearance.

After 0.2.0, the source checkout also offers a [native file viewer](viewer.md)
for qualified boxes/cylinders in their saved global frames, with part/property
inspection. It is always an explicitly partial view and uses no optional backend.

| Operation | Supported behavior | Scope boundary |
| --- | --- | --- |
| Header inspection | Both byte orders; leading MOD/DRW/RES framing; raw header fields and a CP932 name candidate | Unknown fields and later ranges remain unparsed |
| Resource listing | Owner-based indexing of supported raw and zlib X_B layouts | Does not infer parts, sharing or instances |
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

Whole-model reconstruction, general iCAD-native shape evaluation, assembly
geometry placement, drawing interpretation, feature history, STEP export
and ICD writing remain unsupported. GLB is limited to the optional resource
preview contract; the native viewer separately tessellates supported parameters
for display without producing B-Rep or GLB.

## Schemas

Version 0.2.0 uses `parasolid-core 0.3.0`.
`icadkit.build_info()` lists its compiled profiles, including the exact
`SCH_3401212_34101_13006` key (`icad-sch34101-13006-r1`). This key no longer
requires a catalog when using icadkit 0.2.0.
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
