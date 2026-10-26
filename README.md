# icadkit

A Rust-based Python reader for **iCAD SX `.icd` files**. Read saved part
structure, coordinate frames, names and selected attributes; inspect native
box/cylinder parameters; and extract embedded Parasolid geometry.

Supported workflows include:

- [Part access](docs/parts.md), [saved 3D parameter tables](docs/parameters.md)
  and explicit [external-reference resolution](docs/references.md).
- [View inventories and saved 2D geometry](docs/drawing.md), plus decoded
  surface and edge items of the [trailing container](docs/trailer.md).
- The [native file viewer](docs/viewer.md) for qualified
  [native primitives](docs/native.md) and their part properties.
- Optional [V7L7 boolean evaluation](docs/csg.md) and
  [saved final body display](docs/saved-bodies.md), including qualified spun,
  rolling-ball blend and toroidal surfaces.

icadkit is experimental. Unsupported geometry remains in the part inventory;
unknown values and source ranges are preserved. Part frames, native primitive
frames and embedded resource coordinates have separate contracts. Complete
assembly geometry, inherited attributes and complete drawing reconstruction remain unsupported.
Read the [support boundaries](docs/support.md) and [release notes](docs/changelog.md).

Run `icadkit view model.icd` to open qualified native primitives and their
part properties. The base viewer works without the preview extra.

## Installation

The base reader supports standard GIL-enabled CPython 3.10–3.14. Wheel targets
are Linux x86_64 (glibc 2.28+), Windows x86_64 and macOS ARM64 (11+):

```sh
python -m pip install icadkit
```

The native extension bundles `parasolid-core` and has no mandatory Python
runtime dependencies. iCAD, Wine and an external SDK are not required. An
explicit compatible catalog is needed only for schemas absent from the built-in
profiles. Release files are available on the
[GitHub releases page](https://github.com/monozukuri-ai/icadkit/releases).

For preview/GLB, install `python -m pip install 'icadkit[preview]'`.
The optional OCCT dependency requires glibc 2.31+ on Linux; see
[preview requirements](docs/preview.md#dependencies-and-validation).
To build from a source checkout or sdist, install Rust 1.88+ and a C linker,
then run `python -m pip install .` (or `'.[preview]'`).

## Updating the package version

`[workspace.package].version` in `Cargo.toml` is the single release-version
source. Both Rust crates inherit it, Maturin derives the Python package version
from it, and `icadkit.__version__` / the CLI use the installed package metadata.
With Python 3.11+, Cargo and uv available, update it with one command:

```sh
python3 scripts/bump_version.py X.Y.Z
python3 scripts/bump_version.py --check --tag vX.Y.Z
```

Replace `X.Y.Z` with the intended release number. The command updates the
workspace version, synchronizes `Cargo.lock` and `uv.lock`, and checks their
consistency. On failure it restores all three files. Add `--offline` to use
cached dependencies. Stable
`X.Y.Z` and development `X.Y.Z-dev.N` versions are supported; Python metadata
uses `X.Y.Z.devN` for the latter. `--check` does not modify the source or locks.
CI checks the same rules and requires release tags to match the source version.

Use `icadkit --version` to check the installed package version and `icadkit info`
to inspect its backend version. The `parasolid-core` and `parasolid-kit`
dependency pins have their own versions and are updated separately.
Usage guides describe capabilities without repeating the package version.
Record version-specific changes in the [changelog](docs/changelog.md); retain
the release numbers attached to historical notes and validation results.

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
unions, differences and intersections of boxes, cylinders, prisms and profile
extrusions, including saved single-leaf extrusion bodies. Unknown programs
retain diagnostics without substitute geometry.
The [saved final body mode](docs/saved-bodies.md), `view --saved-brep`, also
displays qualified V7L7/V8L3 final bodies and imported solids, including bounded
spherical, conical and toroidal surfaces. V8L3 resource sharing preserves each
occurrence's saved placement. An explicit schema catalog is needed when the
exact built-in profile lacks a required type. Counted metadata framing also
extends part inventories while preserving unknown records and diagnostics.
Bounded binary attributes are preserved as raw owned records. The native viewer
also displays qualified cones/frusta and full ring tori. The built-in exact V34
profile covers qualified toroidal resources without an external catalog.

Qualified millimetre frames and bounded native/saved geometry cover
V7L6/V7L7 and V8L1/V8L2/V8L3, including internal mirrors.
Signed occurrence orientation is separate from already placed geometry; see
[mirror semantics](docs/parts.md#coordinate-frames-and-units). Explicit
[external-reference resolution](docs/references.md) now composes qualified child
and grandchild placements, including mirrors, through `read_assembly()` or
`icadkit view model.icd --reference-root /path/to/bundle`. Saved names, resolved
paths, hashes and missing/ambiguous references remain separate. Extended information is stored text, not interpreted
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
