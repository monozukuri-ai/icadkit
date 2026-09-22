# Python API and CLI

icadkit 0.2.0 provides header inspection, owner-based resource indexing, lazy
extraction and resource-level raw geometry and B-Rep. For supported platforms
and model boundaries, see [supported capabilities](support.md).

## Compatibility

The public Python interface consists of the names in `icadkit.__all__` and
the documented names in the optional [icadkit.preview module](preview.md).
`icadkit._core`, native handles and attributes beginning with `_` are private.
Within a minor release series, existing arguments, result fields, diagnostic codes, coordinate
semantics and CLI exit codes are preserved. Message wording and human-readable
CLI formatting are not stable interfaces.

CLI JSON uses `schema_version=1`. Ignore unrecognized additional fields.
Removing existing fields or changing their meanings requires a schema version
change. A `complete` status always refers to an explicit scope, not the whole
ICD model.

Version 0.2.0 adds [native part access](parts.md) through
`Document.read_parts()`, returning `PartIndex`, `Part`, `PartDefinition`,
`PartReference`, `PartPlacement` and `PartProperty`. It exposes qualified part
frames and selected stored attributes. Its scope statuses are independent of
the resource geometry API described below. Saved entities expose
`NativeEntity`, `NativeAppearance` and `NativePrimitive`; see
[native parameter and appearance access](native.md).
The optional `icadkit.preview.write_preview()` and `icadkit preview` command
export one qualified resource to a GLB and local viewer, with explicit source
units. See the [preview API and CLI](preview.md) for scope and dependencies.

## Header inspection

```python
from pathlib import Path
import icadkit

info = icadkit.inspect(Path("assembly.icd"))
info = icadkit.inspect(Path("assembly.icd").read_bytes())
info = icadkit.inspect("assembly.icd", limits=icadkit.InspectionLimits(
    max_file_bytes=512 * 1024 * 1024,
    max_record_bytes=64 * 1024 * 1024,
))
```

`source` accepts `str | os.PathLike[str] | bytes`. Bytes represent file contents.
Bytearrays, memoryviews and paths returning bytes are rejected. To use a byte
filename, convert it with `os.fsdecode()`. Paths must name regular files or
symlinks to regular files. Do not modify files while they are being read.

Path inspection seeks to required locations and logically reads 280 bytes on
success; this is not a guarantee about OS read-ahead or physical I/O. It releases
the GIL during I/O. Inspection of bytes borrows the immutable Python buffer
while holding the GIL and copies only small result fields.

`InspectionLimits` defaults to 512 MiB per file and 64 MiB per record. Both
values must be integers in `1..2**64-1`. Zero, negative or out-of-range values
raise `ValueError`; booleans and non-integers raise `TypeError`. These are input
size limits, not process memory limits.

Results are frozen dataclasses, collections are tuples and source values are
bytes.

| `Inspection` field | Meaning |
| --- | --- |
| `file_size` | Input size in bytes |
| `bytes_read` | Bytes read by Rust for inspection; 280 on success |
| `header` | Byte order, four raw version bytes, 40 raw name bytes, full 256-byte MOD data and decoded name candidate |
| `leading_records` | MOD, DRW and RES tags, record ranges and payload ranges |
| `unparsed_ranges` | Unknown MOD fields, DRW/RES payloads and later content |
| `status` | Header complete, container partial, remaining scopes not checked |
| `diagnostics` | Nonfatal diagnostics such as a name decoding failure |

`ByteRange(start, end)` is the half-open interval `[start, end)`; `.length`
returns its byte length. `RecordInfo.payload_range` excludes the eight-byte
opening tag/length and four-byte closing tag. A valid range does not imply that
its contents or references have been interpreted.

`Header.raw_version` is not converted to an iCAD product version.
`raw_name` preserves padding. `name_cp932_candidate` removes trailing spaces
and NULs, then strictly decodes CP932. Interior NULs remain. A decoding failure
returns `None` and `header.name_decode_failed`, retaining the original bytes.
CP932 is a display candidate, not a claim about the formal file specification.

### Errors

```python
try:
    info = icadkit.inspect("model.icd")
except icadkit.IcadError as exc:
    print(exc.diagnostic.code, exc.diagnostic.byte_offset)
except OSError as exc:
    print(exc)
```

| Exception | Typical codes or causes |
| --- | --- |
| `InvalidFormatError(IcadError)` | `header.invalid_magic`, `header.truncated`, `record.invalid_length`, `record.out_of_bounds`, `record.start_tag_mismatch`, `record.end_tag_mismatch`, `record.truncated` |
| `UnsupportedFormatError(IcadError)` | `header.unsupported_layout` |
| `LimitExceededError(IcadError)` | `limit.file_bytes`, `limit.record_bytes` |
| `OSError` and subclasses | Missing files, permissions, I/O failures and nonregular files |
| `TypeError` / `ValueError` | Invalid API arguments or limit values |

`IcadError.diagnostic` contains `category`, `code`, `byte_offset` and
`message`. Branch on codes rather than message wording. Offsets identify
positions in the original ICD unless otherwise specified.

A record extending beyond the file raises `record.out_of_bounds`; a valid
range exceeding the configured record limit raises `limit.record_bytes`.
Inspection stops at the first fatal error and does not return a partial result.

## Documents and extraction

```python
doc = icadkit.read("assembly.icd", limits=icadkit.ReadLimits())
print(doc.source_sha256, doc.resource_index_status)
for resource in doc.resources:
    print(resource.resource_id, resource.encoding, resource.declared_decoded_bytes)
    extracted = doc.extract(resource.resource_id)
    payload = doc.extract_bytes(resource.resource_id)
    print(extracted.source)
    owner_bytes = doc.source_bytes(resource.owner_range)
```

`read()` accepts the same source types and paths as `inspect()`. It checks
file length before allocation, owns the complete input in Rust and computes
SHA-256. A bytes input is copied once into the Rust input buffer. File reading,
hashing, indexing and extraction release the GIL.

A size change during file reading raises `source.size_changed`. Concurrent
same-size writes are not synchronized. After `read()` finishes, changes to or
removal of the source file do not affect the document.

`Document` is a frozen dataclass holding a frozen native handle. Its
`resources`, `records`, `unparsed_ranges` and `diagnostics` are tuples.
`records` contains supported directory-indexed outer records; individual
geometry owners are available through `ResourceRef.owner_range`. Indexing
follows declared ownership and ranges, not signature scanning.

| `ReadLimits` | Default | Applied to |
| --- | ---: | --- |
| `max_file_bytes` | 512 MiB | Input copy or file read |
| `max_record_bytes` | 512 MiB | Outer records and indexed elements |
| `max_resource_bytes` | 64 MiB | Stored and decoded sizes per extraction; source byte copies |
| `max_records` | 200,000 | Directory entries and owner elements |
| `max_resources` | 100,000 | Indexed resources |

Values follow the positive-u64 rules of `InspectionLimits`. A declared decoded
size is retained as metadata during indexing and checked against limits when
that resource is extracted. There is no automatic decompression, extraction
cache, batch extraction API or aggregate process-memory limit. Rust output
buffers and Python bytes can coexist during extraction.

| `ResourceRef` field | Meaning |
| --- | --- |
| `resource_id` | `usr:` plus the owner's absolute offset as 16 hexadecimal digits; reproducible for the same input, not unique across documents |
| `kind` | `parasolid_x_b` for recognized ownership layouts; contents are validated during extraction |
| `owner_range` | Complete owner element in the ICD |
| `storage_range` | Stored payload range, including up to seven alignment bytes |
| `encoding` | `raw` or `zlib` |
| `declared_decoded_bytes` | Declared payload size after decoding |
| `owner_type` / `layout_version` | Source bytes identifying supported storage layouts |
| `source_id` | Original u32 value, without inferred part or placement semantics |

Different owners remain separate resources even when their payload hashes match.
icadkit does not infer shared parts or assembly relationships.

`extract(id)` returns `Extraction(payload, source, status)`.
`extract_bytes(id)` performs the same validation and returns only the payload.
The `SourceRef` contains the original `source_sha256`, `resource_id`,
`container_range` actually consumed (excluding padding), `decoded_range`
within the returned payload, `payload_sha256` and `encoding`. Do not add
decoded offsets to compressed container offsets. `source_bytes(ByteRange(...))`
returns bounded copies of the original input, including unknown regions.

Extraction checks sizes, zlib checksum and EOF, unexpected concatenated streams
or extra data, alignment, the neutral X_B header and terminal suffix. It does
not parse schema-dependent nodes, prove a node terminator or construct B-Rep.

Document status remains header complete, container partial and other scopes not
checked. Only the extraction result reports extraction complete. A failed
extraction does not alter other resources or the document status.
`resource_index_status` describes enumeration of supported owners, not model
completeness. An empty resource list does not establish absence of geometry.

Invalid outer ranges, overlaps or tag mismatches raise errors. Unknown owner
layouts and local damage produce diagnostics and a partial index, retaining
validated resources. Indexing stops when an element's length cannot be determined;
it does not scan ahead for another signature. Unsupported elements of known
length remain unknown ranges while later elements can be processed.

Additional codes include `directory.overlap_or_missing`,
`directory.noncontiguous`, `usr.unsupported_layout`, `entity.invalid_length`,
`resource.table_out_of_bounds`, `resource.unsupported_layout`,
`resource.not_found`, `compression.invalid`, `compression.truncated`,
`resource.decoded_size_mismatch`, `resource.invalid_padding`,
`resource.invalid_header`, `resource.invalid_envelope`, `limit.resource_bytes`,
`limit.records` and `limit.resources`. Extraction error offsets point to the
resource's storage start. Decoded X_B positions in messages are labeled separately.

## Geometry

```python
result = doc.read_geometry(resource_id)  # schema=None, limits=None
result.require_complete("raw_geometry")
raw = result.raw
print(raw.node_count, raw.schema_key, raw.terminator_range)
for node in raw.nodes(start=0, count=10):
    print(node.index, node.node_type, node.type_name, node.decoded_range)
    for field in raw.fields(node.index, count=10):
        print(field.name, field.field_type, field.value_count)
        print(raw.field_values(node.index, field.index, count=10))
if result.brep is not None:
    print(result.brep.counts, result.brep.topology_valid)
    for face in result.brep.entities("faces", count=10):
        print(face.id, face.attributes, face.source)
```

`GeometryResult` is immutable and contains `resource_id`, `source_sha256`,
`source`, `status`, `schema`, `raw`, `brep` and `diagnostics`.
Successful raw results remain accessible even if B-Rep construction fails.
Missing resource IDs raise `InvalidFormatError`; limit violations raise
`LimitExceededError`. Unsupported schemas, invalid nodes or B-Rep, and
extraction failures otherwise produce a result with diagnostics for that resource.

Parsing and B-Rep construction release the GIL. Rust owns the model and Python
copies metadata and requested pages. Raw geometry and B-Rep share a native handle
and remain usable after the original document, catalog or result is discarded.
There is no result cache; repeated calls parse again.

| Status scope | What it checks |
| --- | --- |
| `extraction` | Compression, declared size and envelope of one resource |
| `raw_geometry` | Exact-schema node parsing through the correct terminator and EOF |
| `brep` | Backend geometry/topology construction, with no unsupported curve or surface remaining |
| `topology` | Backend checks of references, loops, edge rings and related structure |
| `header` / `container` / `model` | Document status; not promoted by resource-level success |

`require_complete(scope="brep")` accepts `extraction`, `raw_geometry`,
`brep` or `topology`. Any other status than complete raises
`IncompleteGeometryError` with a `.diagnostic`. A document-level
`require_complete("model")` is not provided.

### Schema selection

```python
catalog = icadkit.SchemaCatalog.from_file(
    "sch_13006.sch_txt",
    expected_id="13006",
    limits=icadkit.CatalogLimits(),
    # expected_sha256="..."  # Optional known 64-digit hexadecimal SHA-256.
)
result = doc.read_geometry(resource_id, schema=catalog)
```

`from_bytes(bytes, expected_id=..., expected_sha256=..., limits=...)` is also
available. A path reads only the specified file. Catalog IDs are checked against
the contents, not inferred from the filename. SHA-256 covers all original bytes;
a mismatch raises `schema.hash_mismatch`. Loaded catalogs are immutable snapshots.

With `schema=None`, the backend selects an exact-key built-in profile. A missing
profile produces `schema.missing_base_schema`; nearby versions are never
substituted. An explicit catalog must match the requested provider schema (base
for a three-component key, effective for a two-component key), otherwise
`schema.catalog_mismatch` is returned. There is no fallback to a built-in profile,
environment lookup, directory search or global catalog registration.

`SchemaSelection` records the key, required provider schema, selection kind,
external catalog hash or built-in profile ID/revision/hash/coverage. A selected
profile does not imply support for every individual geometry type.

| `CatalogLimits` | Default |
| --- | ---: |
| `max_file_bytes` | 4 MiB |
| `max_schema_types` | 65,536 |
| `max_fields_per_type` | 4,096 |
| `max_string_bytes` | 1 MiB |

### Pagination and source values

Raw `nodes(start=0, count=100)`, `fields(node_index, start=0, count=100)`,
`field_values(node_index, field_index, start=0, count=100)`, `user_fields(...)`
and B-Rep `entities(collection, start=0, count=100)` return tuples. Page counts
must be 0–1,000. Pages near the end return remaining entries; pages beyond the
end are empty. Missing node or field indices raise `KeyError`. Negative or
oversized page arguments raise `ValueError`; booleans/non-integers raise
`TypeError`. `raw.node(index)` looks up the source node index.

Field types preserve backend codec codes. `pointer_class`, `element_count`
and `transmitted` preserve schema values. Null numeric values become `None`;
vectors and intervals become tuples. Numeric values are not guessed into text.
Raw ranges identify decoded X_B positions. `raw.to_bytes()` copies the stored
X_B payload; it is not an independent serializer or an ICD writer.

B-Rep collections are `bodies`, `regions`, `shells`, `faces`, `loops`,
`half_edges`, `edges`, `vertices`, `points`, `curves` and `surfaces`.
Entity IDs belong to their B-Rep collection and are not raw node indices.
`NodeSource` records the raw node index/type, candidate node ID and range.
Entity attributes are recursively immutable mappings and tuples. They preserve
analytic geometry and NURBS control points, knots and multiplicities. Retrieving
an entity copies its arrays; use small pages for large NURBS.

Unsupported geometry retains `kind="unsupported"` and its original type name,
and the B-Rep remains partial. It is not replaced by an approximation marked
complete. Counts, curve/surface kinds, topology counts and Euler characteristic
are compact summaries. `vertex_bounds` covers vertices, not all curve/surface
extrema. `surface_area` and `volume` are numbers only when calculable by the
backend, otherwise `None`. Units and assembly placement remain unresolved.

### Geometry limits and diagnostics

`GeometryDiagnostic` adds scope, resource ID, container range, decoded offset,
backend code, node type and node index to the ordinary diagnostic.
`byte_offset` identifies the original ICD; `decoded_offset` identifies the
decoded X_B. A coordinate not applicable to an error is `None`. For catalog
loading errors, byte offsets identify the specified catalog instead.

| `GeometryLimits` | Default |
| --- | ---: |
| `max_payload_bytes` | 64 MiB |
| `max_nodes` | 100,000 |
| `max_schema_types` | 65,536 |
| `max_fields_per_type` | 4,096 |
| `max_string_bytes` | 1 MiB |
| `max_variable_elements` | 1,000,000 |
| `max_diagnostics` | 1,000 |

Geometry and catalog limit values must be positive integers, at most
`2**63-1` in the Python API. Document read limits also apply. Exceeding the
diagnostic limit raises `LimitExceededError` rather than truncating diagnostics
and reporting success.

## Command line

```sh
icadkit --version
icadkit info --json
icadkit inspect model.icd --json
icadkit inspect model.icd --max-file-bytes 536870912 --max-record-bytes 67108864 --json
icadkit resources model.icd --json
icadkit extract model.icd RESOURCE_ID --output body.x_b --json
icadkit check model.icd --target geometry --resource RESOURCE_ID --json
icadkit check model.icd --target geometry --resource RESOURCE_ID \
  --scope raw_geometry --schema sch_13006.sch_txt --schema-id 13006 --json
icadkit check model.icd --target container --json
icadkit check model.icd --target model --json
```

`python -m icadkit` provides the same interface. Output uses UTF-8 independently
of the locale or `PYTHONIOENCODING`. Unix filename surrogates are escaped in
text output. JSON commands write one object to stdout on success and on handled
input/I/O errors. Common fields include `schema_version`, `operation` and
`source`; errors include a structured `error` diagnostic. Raw header bytes use
`raw_version_hex`, `raw_name_hex` and `raw_mod_hex`.

| Exit code | Meaning |
| ---: | --- |
| 0 | Requested inspection, indexing, extraction or geometry scope succeeded |
| 1 | Invalid input, damaged index, I/O failure or existing extraction destination |
| 2 | Invalid CLI arguments; argparse writes to stderr, outside JSON output |
| 3 | Unsupported layout/schema/geometry, or partial/not-checked requested scope |
| 4 | Size or count limit exceeded |

Text-mode failures go to stderr. An undecodable name candidate alone is a
successful inspection with a diagnostic (exit 0).

`resources` returns owner information, source hash, status, diagnostics and
unknown ranges without decompression. `extract` returns the output path,
`provenance` and scope status; payload bytes go to the specified file. Read
limits use kebab-case options such as `--max-resource-bytes`.

Extraction writes a temporary file in the destination directory and publishes
it with a non-overwriting hard link after validation. Existing files and symlinks
are rejected. The parent directory must exist; filesystems without hard-link
support return an I/O error. There is no overwrite option.

Geometry checks require `--resource`; `--scope` defaults to `brep` and accepts
the four geometry scopes listed above. JSON includes status, schema selection,
provenance, node/B-Rep counts and diagnostics; it does not serialize every node
or B-Rep entity. `--schema-sha256` pins the catalog hash. Geometry limits such as
`--max-nodes` and the document read limits are available.

Container checks return exit 3 because unparsed ranges remain. Model checks
return exit 3 with `model.not_implemented`. Resource geometry success does not
change these outcomes.
