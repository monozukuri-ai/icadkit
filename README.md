# icadkit

A Rust-based Python reader for **iCAD SX `.icd` files**. Inspect file headers,
list embedded Parasolid resources, extract validated X_B payloads, and read
resource-level geometry and boundary representations (B-Rep).

icadkit is experimental. It preserves source coordinates and unknown content;
it does not reconstruct complete iCAD models, assembly placement or drawings.
See [supported capabilities and limits](docs/support.md) before choosing it for
your files.

## Installation

Python 3.10–3.14 with the standard GIL is supported. Wheel targets are Linux
x86_64 (glibc 2.28+), Windows x86_64 and macOS ARM64 (11+). Install a wheel
matching your platform:

```sh
python -m pip install /path/to/icadkit-0.1.0-cp310-abi3-PLATFORM.whl
```

Replace the path with the actual wheel filename. Version 0.1.0 is not yet
published on PyPI. To install from a source checkout or source distribution,
install Rust 1.88+ and a C linker, then run this in the source directory:

```sh
python -m pip install .
```

The native extension bundles `parasolid-core` and has no Python runtime
dependencies. iCAD, Wine and an external SDK are not required. Files needing
a schema absent from the built-in profiles require an explicitly supplied
compatible schema catalog.

## Quick start

```python
import icadkit

info = icadkit.inspect("model.icd")
print(info.header.byte_order, info.header.name_cp932_candidate)

doc = icadkit.read("model.icd")
for resource in doc.resources:
    print(resource.resource_id, resource.encoding)
    extracted = doc.extract(resource.resource_id)
    print(len(extracted.payload), extracted.source.payload_sha256)

    geometry = doc.read_geometry(resource.resource_id)
    print(geometry.status, geometry.diagnostics)
    if geometry.brep is not None:
        print(geometry.brep.counts)
    geometry.require_complete("brep")  # Raises if this scope is incomplete.
```

`inspect()` reads only the leading structure. `read()` owns the input and indexes
resources; decompression and geometry parsing happen on request. A successful
extraction or B-Rep applies to that resource and scope only. An empty resource
list does not establish that a file contains no geometry.

Paths can be strings or `Path` objects. Passing `bytes` means file contents,
not a filename. Structured format errors derive from `icadkit.IcadError`; file
access errors use `OSError`. See the [Python API and CLI reference](docs/api.md)
for limits, diagnostics, schema selection and pagination.

## Command line

```sh
icadkit --version
icadkit info --json
icadkit inspect model.icd --json
icadkit resources model.icd --json
icadkit extract model.icd RESOURCE_ID --output body.x_b --json
icadkit check model.icd --target geometry --resource RESOURCE_ID --json
```

Use an ID returned by `resources` in place of `RESOURCE_ID`. Extraction creates a
new file and refuses to overwrite an existing file. `python -m icadkit` provides
the same commands. JSON output includes a schema version, scope status and
diagnostics; exit codes are documented in the [reference](docs/api.md#command-line).

## License

icadkit is offered under [PolyForm Noncommercial 1.0.0](LICENSES/PolyForm-Noncommercial-1.0.0.md).
[Commercial licenses](COMMERCIAL-LICENSE.md) are available from
[UnRobotics Inc.](https://www.un-robotics.com/#contact).
Earlier MIT portions and dependencies retain their original permissions.
Read the [licensing guide](docs/license.md), [license notice](LICENSE) and
[third-party notices](THIRD_PARTY_NOTICES.md) for details.
