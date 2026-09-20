# Supported capabilities

icadkit 0.1 is an experimental reader for selected iCAD SX ICD structures and
embedded Parasolid X_B resources. Support is determined by the actual layout and
exact schema key, not a product-version label or filename extension alone.

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
values. icadkit does not infer length units, assembly/world transforms, part
relationships or the current saved state. Vertex bounds cover parsed vertices;
they need not include extrema of curved edges or surfaces. Area and volume are
available only when the backend can calculate them.

Whole-model reconstruction, iCAD-native shape interpretation, assembly
placement, drawing interpretation, feature history, tessellation, STEP export
and ICD writing are not provided by this version.

## Schemas

`icadkit.build_info()` lists the profiles supplied by `parasolid-core 0.2.0`.
Profile availability does not mean that every geometry type or every ICD file
using that schema is supported. A missing exact profile produces a diagnostic;
icadkit does not substitute a nearby schema version.

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

Input, record, resource, node and schema limits reject oversized operations.
These limits do not cap total process memory: document bytes, decoded payloads,
raw nodes and B-Rep structures can coexist. Process one resource at a time and
use small pages for large NURBS entities. The [API reference](api.md) lists the
defaults and exception behavior.
