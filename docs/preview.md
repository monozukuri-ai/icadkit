# Resource preview and GLB

Version 0.2.0 can render one selected embedded Parasolid resource and
write a GLB plus an offline browser viewer. Install the optional dependencies:

```sh
python -m pip install 'icadkit[preview]==0.2.0'
icadkit resources model.icd --json
icadkit preview model.icd --resource RESOURCE_ID --source-unit m --output preview
```

Replace `RESOURCE_ID` with an ID from `resources`. Select the source unit from
`mm`, `cm`, `m`, or `in` using independent knowledge of that resource. `m` above
is an example, not an inferred default. The command starts a loopback HTTP
server and opens a browser; Ctrl-C stops the server and retains the files.
`--no-open` prints the local URL without opening a browser. `--port 0` (default)
selects a free port. For batch export:

```sh
icadkit preview model.icd --resource RESOURCE_ID --source-unit m \
    --output preview --write-only --json
```

The destination must not exist. It contains `preview.glb`,
`preview.manifest.json`, `index.html`, `viewer.js`, and `viewer.css`.
Keep the GLB and manifest together when exchanging results: the manifest binds
the GLB's SHA-256 to the resource, reader profile and unit declaration. The GLB
uses metres and retains the resource's axes and origin. No axis rotation,
recentering or assembly placement is applied to exported coordinates.
Viewer camera fitting affects only the display.

## Python

```python
import icadkit
from icadkit.preview import PreviewOptions, serve_preview, write_preview

doc = icadkit.read("model.icd")
result = write_preview(
    doc,
    "RESOURCE_ID",
    "preview",
    source_unit="m",  # Caller supplied, never detected.
    options=PreviewOptions(linear_deflection=0.0001, include_edges=True),
)
print(result.glb_path, result.triangle_count, result.glb_sha256)

with serve_preview(result.directory) as server:
    print(server.url)
    server.open_browser()
    input("Press Enter to stop serving: ")
```

The public names in `icadkit.preview` are `LengthUnit`, `PreviewError`,
`PreviewOptions`, `PreviewLimits`, `PreviewResult`, `PreviewServer`,
`write_preview`, and `serve_preview`. Importing this module does not import
OCCT or the optional conversion package. Result/options/limits are frozen
dataclasses. `PreviewResult` exposes output paths, resource/source hashes,
declared unit, triangle/face/edge counts, total output bytes and GLB SHA-256.
Counts describe mesh primitives, not part occurrences.

`write_preview` also accepts `schema=SchemaCatalog(...)`,
`geometry_limits=GeometryLimits(...)`, and `limits=PreviewLimits(...)`.
The explicit schema contract is the same as `Document.read_geometry()`;
catalogs are never discovered automatically. CLI schema and parsing limit
arguments match `check --target geometry`. CLI `--max-triangles` and
`--max-output-bytes` bound export size; other conversion bounds are available
through Python.

Default mesh deflection is **0.0001 metre** (0.1 mm) and angular deflection is
0.5 radians. These govern approximation, not measurement certification.
`PreviewLimits` defaults are 100,000 entities, 200,000 OCCT subshapes,
1,000,000 curve samples, 1,000,000 triangles, 2,000,000 vertices, 128 MiB total
output, and 1,000 diagnostics. They do not cap total process memory or CPU time.
Conversion and validation finish before a destination directory is created;
publication refuses existing paths and cleans files it created if copying fails.
Multi-file publication is not a filesystem transaction.

## Qualified scope

The adapter supports solid bodies using line/circle curves and
plane/cylinder/sphere surfaces, subject to the pinned converter's exact topology
coverage and OCCT validity checks. Presence of supported surface kinds alone
does not guarantee that their trimmed topology can be converted. Wires, sheets,
NURBS, other curve/surface variants and unsupported trimming fail explicitly.
Complete resource B-Rep and topology are required. Partial output, healing and
silent fallback are disabled.

Local Linux checks cover synthetic translated boxes in four units and the
exact V34 profile; iCAD-authored saved/reopened box, cylinder, sphere and holdout
resources are compared with independent SDK area/volume. Nine of ten tested
real resources convert. A through-hole resource is rejected with
`occt.invalid_shape`; general holes/CSG conversion is not qualified.
At the default deflection, mesh volume differs from the saved SDK oracle by up
to 1.28% in this small corpus; exact OCCT area/volume agrees within 1e-8 relative
tolerance. This evidence does not certify arbitrary shapes or assemblies.

The UI provides orbit/pan/zoom, camera presets, visibility controls, display
sections and face/edge source inspection. Its visible banner identifies
resource coordinates, caller-supplied units and illustrative colors.
The source/conversion completeness badges concern the selected resource only.
Part placement, resource-to-part ownership, saved color/visibility, native
primitive evaluation and whole-model completeness are not inferred. Native-only
files with no embedded resource cannot use this preview.

The manifest's `icadkit` object carries schema version 1, input and payload
hashes, resource ranges, exact reader profile, scope statuses, declared/output
units and placement/appearance boundaries. Face/edge source ranges in the
backend mapping refer to **decoded resource bytes**, not compressed ICD offsets.
The `source.container_range` identifies bytes in the original ICD.

Missing dependencies, unsupported geometry and conversion failures raise
`PreviewError(IcadError)` with a structured diagnostic. Input geometry failures
retain the existing `IcadError` subtype. File errors use `OSError` subclasses;
invalid API arguments use `TypeError`/`ValueError`. CLI exit codes are 0 success,
1 invalid data/I/O, 2 arguments, 3 unsupported/missing dependencies, and 4 limits.
`--json` requires `--write-only` and includes a versioned result or diagnostic.

## Dependencies and validation

The preview extra pins `parasolid-kit[occt]==0.2.0`; its OCCT extra selects
`cadquery-ocp-novtk` 7.9.3.1 through versions below 7.10.
The locked OCCT 7.9.3.1.1 wheels require glibc 2.31+ on Linux x86_64 and
macOS 11+ on ARM64; Windows x86_64 wheels are also used in CI. Preview CI uses
standard CPython 3.12; the base reader is tested separately on 3.10–3.14. The icadkit reader still
uses Rust `parasolid-core 0.3.0`. An explicit adapter maps icadkit B-Rep objects
into the optional package's public types. It does not reparse the stream or
substitute a schema key. A `parser_version` in the backend conversion report
identifies that backend package, not the reader used on the ICD resource.

Viewer assets are copied from the optional package at export time, with their
license notices retained. The pinned viewer's camera near-plane floor is
adapted to metre-scale small parts, without changing mesh coordinates. The
exact expected camera implementation is checked before this adaptation, and
the manifest records the changed assets' hashes.
The base icadkit wheel contains neither OCCT nor
viewer assets and has no mandatory Python dependencies. iCAD/Wine is unnecessary
for generation or viewing. Generated viewers fetch only local files.
See [third-party notices](../THIRD_PARTY_NOTICES.md).

Local validation used Linux x86_64, Python 3.12, OCCT 7.9.3.1.1 and headless
Chromium software rendering. CI also installs the extra and runs the synthetic
tests from exact candidate wheels on Linux, Windows and macOS. The release
workflow requires these checks to pass before publication.
