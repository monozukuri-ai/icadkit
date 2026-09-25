# Explicit external references (unreleased)

`read_assembly()` resolves caller-selected local ICD files and places qualified
V7L6/V7L7/V8L1/V8L2/V8L3 content in the host's root-relative millimetre frame.
`read()` and `read_parts()` remain single-file operations. No reference file is
opened by those APIs, `write_native_viewer()`, or `view` without reference options.

```python
import icadkit
from icadkit.viewer import write_assembly_viewer

assembly = icadkit.read_assembly(
    "bundle/model.icd", search_roots=["bundle"],
    limits=icadkit.AssemblyLimits(max_documents=64, max_depth=8),
)
for reference in assembly.references:
    print(reference.request.reference_name, reference.status,
          reference.resolved_path, reference.target_sha256)
write_assembly_viewer(assembly, "assembly-view")
```

The source accepts a path, immutable bytes, or an existing `Document`.
`doc.read_assembly(...)` is also available; a `Document` has no retained input
path, so its host `AssemblyDocument.path` and resolver `source_path` are `None`.
Exactly one policy is required: nonempty `search_roots` or a `resolver` callback.

## Search policy

Each saved name is interpreted as a relative Windows path within every explicit
root. Both separators and case-insensitive component matching are supported;
`.icd` is appended when the name has no extension. All roots are searched before
choosing a result. Two distinct canonical paths are ambiguous even when their
bytes match; case collisions are also ambiguous. Canonical aliases of one path
are deduplicated. Roots must exist and be directories.

There is no host-directory, working-directory, environment-variable, recursive
basename, or network fallback. Absolute, drive-qualified, UNC, URL, parent-traversal
and non-ICD names are unsupported by this policy. Symlinks escaping a root are
rejected. A caller can explicitly map unusual saved names with a resolver:

```python
from pathlib import Path

targets = {"original-part-name": Path("approved/child.icd").resolve()}
assembly = icadkit.read_assembly(
    "model.icd", resolver=lambda request: targets.get(request.reference_name),
)
```

`ReferenceRequest` contains the original `reference_name`, source document ID,
SHA-256, optional source path and saved part ID. Return an absolute path, a
sequence of candidates, or `None`. Multiple distinct candidates are ambiguous;
a nonexistent single path is missing. The callback controls its own search/I/O
and must return a bounded sequence. The library does not fetch URLs. Explicit
roots or callback paths may themselves reside on caller-mounted filesystems.

## Identity, snapshots and failure states

`AssemblyIndex.documents` holds each successfully loaded canonical file once.
`AssemblyIndex.occurrences` holds every placement separately, including unresolved
external occurrences. Each occurrence retains its original `source_part` and
`source_document_id`; `definition_document_id` and `content_part_id` identify the
loaded content. A referenced root supplies content to the host occurrence rather
than adding a duplicate root to the displayed tree. Host names, comments and
properties remain host-owned; attribute inheritance is not inferred.

`AssemblyEntity` has an occurrence-specific `entity_id` and `owner_id`, plus the
unaltered `source_entity` and its `source_document_id`. Its optional `primitive`
has the assembly placement, while source ranges and raw parameters still refer
to the original document. IDs are snapshot-local, not persistent cross-save IDs.
Use `assembly.document(id)` to retrieve a source document (`KeyError` if absent).
`assembly.to_dict()` supplies a JSON-compatible inventory with separate source
IDs for byte locations.

| Reference status | Meaning |
| --- | --- |
| `resolved` | One qualified document supplies this occurrence |
| `missing` | No matching file is available |
| `ambiguous` | Multiple distinct candidate paths remain |
| `unsupported` | Unqualified name, layout, units, placement or file type |
| `cycle` | Target is already in the active ancestor chain, or a detected symlink loop |
| `limit` | Reference search/read/depth limits prevent resolution |
| `invalid` | Invalid file content or placement |
| `io_error` | Other filesystem/read failures |

Unresolved occurrences retain the saved name, candidates and diagnostics, without
invented descendant geometry or evaluated transforms. If target bytes were read
successfully but their format was rejected, their actual path and SHA-256 are
still recorded. An unopened candidate has no target content hash. Repeated sibling
references are separate occurrences, not cycles.

Read results and failures are cached only within one invocation. A subsequent
`read_assembly()` rereads dependencies, including a grandchild whose host/child
files have not changed. The result owns immutable documents; rendering that
snapshot does not reopen files. Do not modify files during resolution: size
changes are detected, but concurrent same-size writes and multi-file consistency
are not synchronized.

`source_sha256` identifies the host bytes. `assembly_sha256` fingerprints the
resolved inventory, including paths, dependency hashes, placements and failure
states; it is not a path-independent content ID. A resolution `status="complete"`
does not certify geometry. `model_status` remains `partial`.

## Placement and geometry

Stored reference frames map the target's saved coordinate basis. For source root
frame `R`, saved reference frame `F`, referenced root frame `T`, and mirror matrix
`J = diag(1,-1,1,1)` (identity for an unmirrored reference), the normalized map is:

```text
external_map = inverse(R) * F * J * T
child_document_transform = parent_document_transform * external_map
placed_geometry = child_document_transform * target_root_normalized_geometry
```

The `T` factor restores the target's saved basis once. Omitting it misplaces files
with edited root coordinates. Internal part/entity frames already include their
saved placement; internal parents are not multiplied down again. Each external
level composes independently, including double mirrors. `world_transform` retains
the proper SDK coordinate frame; `orientation_world_transform` carries reflection.
Raw source frames remain unchanged.

The assembly viewer evaluates qualified geometry once per loaded document and
places separate meshes per occurrence, reversing winding for reflection. Saved
volume/area are preserved and centroids transformed. This can reflect an already
qualified CSG result; it does not enable unsupported mirrored CSG history within
the source file. Existing native, CSG and saved-B-Rep qualification rules apply.
Unknown native extrusions and unqualified resource bindings remain undisplayed.

## Bounds and CLI

| `AssemblyLimits` | Default |
| --- | ---: |
| `max_documents` | 256, including host and unique attempted target reads |
| `max_total_bytes` | 1 GiB, aggregate input bytes |
| `max_depth` | 32 external levels; host is level 0 |
| `max_occurrences` | 100,000, including host root |
| `max_entities` | 1,000,000 placed inventory entities |
| `max_search_entries` | 100,000 scanned directory entries; also per callback candidate list |

Values must be integers in `1..2**31-1`; booleans/nonintegers are rejected.
`ReadLimits` and `PartLimits` additionally apply per document. Aggregate occurrence
or entity exhaustion raises `LimitExceededError`; individual reference failures
remain in a partial tree. Invalid/unqualified host input fails the operation.
Limits bound input/work counts, not total process memory or time.

```sh
icadkit assembly bundle/model.icd --reference-root bundle --json
icadkit view bundle/model.icd --reference-root bundle \
  --output assembly-view --write-only --json
# Add --saved-brep with the preview extra and an explicit --schema if required.
```

Repeat `--reference-root` for additional roots. Both commands accept
`--reference-max-documents`, `--reference-max-total-bytes`, `--reference-max-depth`,
`--reference-max-occurrences`, `--reference-max-entities` and
`--reference-max-search-entries`, plus existing per-file limits.
`assembly` exits 0 for complete resolution, 3 for partial resolution, 4 for
reference limits and 1 for invalid input/I/O. `view` retains its existing exit
contract: successful partial-view generation returns 0; JSON additionally exposes
`assembly_status`, `assembly_sha256` and reference results.

## Local evidence

P5 checks 15 existing external assemblies and 19 newly saved/reopened assemblies
across the five qualified versions. New cases include missing child/grandchild,
grandchild-only updates, single/double mirrors and changed roots in all three
files. Their 44 placed saved B-Reps and 38 native primitives match SDK placement,
volume, area and centroid; independent authoring equations check the same 82
placements. Browser checks cover picking, owning occurrence, parent visibility
and missing references. Synthetic tests cover ambiguity, cycles, search policies
and limits. SDK capture used V8L3-09A saving older formats, not separate old product
installations. V8L4 has no real input or environment and remains unqualified.
