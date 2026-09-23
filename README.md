# icadkit

A Rust-based Python reader for **iCAD SX `.icd` files**. Read saved part
structure, coordinate frames, names and selected attributes; inspect native
box/cylinder parameters; and extract embedded Parasolid geometry.

Version **0.3.0** adds:

- A [native file viewer](docs/viewer.md) with a part tree, stored properties,
  selection and visibility controls. Qualified boxes, cylinders, full spheres,
  cones/frusta and full ring tori use their saved frames.
- Broader [part access](docs/parts.md): V7L7 traversal, bounded V8L1/V8L2
  inventories and binary attributes retained with ownership and raw bytes.
- Optional [V7L7 boolean evaluation](docs/csg.md) and
  [saved final body display](docs/saved-bodies.md) for V7L7/V8L3, including
  imported solids, shared resources and bounded analytic surfaces.
- Updated exact V34 Parasolid support through `parasolid-core 0.3.1`, including
  qualified toroidal resources without an external catalog.

icadkit is experimental. Unsupported geometry remains in the part inventory;
unknown values and source ranges are preserved. Part frames, native primitive
frames and embedded resource coordinates have separate contracts. Complete
assembly geometry, inherited attributes and drawings remain unsupported.
Read the [support boundaries](docs/support.md) and [release notes](docs/changelog.md).

Run `icadkit view model.icd` to open qualified native primitives and their
part properties. The base viewer works without the preview extra.

## Installation

The base reader supports standard GIL-enabled CPython 3.10–3.14. Wheel targets
are Linux x86_64 (glibc 2.28+), Windows x86_64 and macOS ARM64 (11+):

```sh
python -m pip install icadkit==0.3.0
```

The native extension bundles `parasolid-core` and has no mandatory Python
runtime dependencies. iCAD, Wine and an external SDK are not required. An
explicit compatible catalog is needed only for schemas absent from the built-in
profiles. Release files are available on the
[GitHub releases page](https://github.com/monozukuri-ai/icadkit/releases).

For preview/GLB, install `python -m pip install 'icadkit[preview]==0.3.0'`.
The optional OCCT dependency requires glibc 2.31+ on Linux; see
[preview requirements](docs/preview.md#dependencies-and-validation).
To build from a source checkout or sdist, install Rust 1.88+ and a C linker,
then run `python -m pip install .` (or `'.[preview]'`).

## Part structure, frames and attributes

```python
import icadkit

doc = icadkit.read("assembly.icd")
parts = doc.read_parts()
print(parts.status, parts.diagnostics)

for part in parts.walk(include_root=False):
    print(part.name, part.parent_id, part.definition_id)
    print(part.comment, part.extra_info)
    print(part.placement.local_transform, part.placement.world_transform)

rows = parts.to_rows()  # JSON-compatible rows; not certified BOM quantities.
```

The reader supports the observed little-endian V7L7 part layout:
hierarchy, names, comments, extended text and saved external reference names.
Qualified V7L7 part frames use millimetres and are evaluated relative to the
saved root frame. Qualified standalone primitive owners also expose geometry and saved
entity appearance. The separate [CSG API](docs/csg.md) and
`uv run --extra preview icadkit view model.icd --csg` evaluate qualified
box/cylinder unions, differences and intersections. Unknown programs retain
diagnostics without substitute geometry.
The [saved final body mode](docs/saved-bodies.md), `view --saved-brep`, also
displays qualified V7L7/V8L3 final bodies and imported solids, including bounded
spherical, conical and toroidal surfaces. V8L3 resource sharing preserves each
occurrence's saved placement. An explicit schema catalog is needed when the
exact built-in profile lacks a required type. Counted metadata framing also
extends part inventories while preserving unknown records and diagnostics.
Version 0.3.0 also preserves bounded binary attributes as raw owned records,
reads V8L1/V8L2 part inventories without assigning units or geometry, and displays
native cones/frusta and full ring tori. The updated exact V34 profile in
`parasolid-core 0.3.1` removes the catalog requirement for qualified toroidal
resources.

Qualified V8L3 part frames use millimetres. Mirrored frames and external-file
loading remain unsupported. Extended information is stored text, not interpreted
typed engineering properties. Check each scope's status before relying on its values.

## Embedded geometry

```python
import icadkit

doc = icadkit.read("model.icd")
for resource in doc.resources:
    print(resource.resource_id, resource.encoding)
    geometry = doc.read_geometry(resource.resource_id)
    print(geometry.status, geometry.diagnostics)
    if geometry.brep is not None:
        print(geometry.brep.counts)
    geometry.require_complete("brep")  # Raises if this scope is incomplete.
```

`inspect()` reads leading structure. `read()` owns the input and indexes
resources; decompression and geometry parsing happen on request. A complete
resource B-Rep does not establish whole-model completeness. An empty resource
list does not establish that a file contains no native geometry.

Paths accept strings or `Path` objects; `bytes` means file contents.
Structured format errors derive from `icadkit.IcadError`; file access errors use
`OSError`. See the [API reference](docs/api.md) for schemas, limits and pagination.

## Command line

```sh
icadkit info --json
icadkit inspect model.icd --json
icadkit parts assembly.icd --json
icadkit resources model.icd --json
icadkit extract model.icd RESOURCE_ID --output body.x_b --json
icadkit check model.icd --target geometry --resource RESOURCE_ID --json
# Requires the preview extra; use the independently known unit of the resource.
icadkit preview model.icd --resource RESOURCE_ID --source-unit m --output preview
```

Use an ID from `resources` in place of `RESOURCE_ID`. Extraction and preview
refuse existing output paths. `python -m icadkit` provides the same commands.
Preview requires a declared source unit; its GLB uses metres and resource-local
coordinates, without part placement or saved appearance. `--write-only --json`
exports without starting a server. CLI scope statuses and exit codes are
explained in the [reference](docs/api.md#command-line).

## License

icadkit is offered under [PolyForm Noncommercial 1.0.0](LICENSES/PolyForm-Noncommercial-1.0.0.md).
[Commercial licenses](COMMERCIAL-LICENSE.md) are available from
[UnRobotics Inc.](https://www.un-robotics.com/#contact).
Dependencies retain their own licenses and copyright notices.
Read the [licensing guide](docs/license.md), [license notice](LICENSE) and
[third-party notices](THIRD_PARTY_NOTICES.md) for details.
