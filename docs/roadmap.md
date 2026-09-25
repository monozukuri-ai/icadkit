# Release scope and follow-up work

## Current source: version coverage

Qualified V8L1 standard-part templates and V8L3 resaves now expose root-owned
entity inventories and [saved parameter tables](parameters.md). Parameter
definitions also retain separate ownership in V7L7/V8L3 internal placements.
The reader preserves unknown fields and does not execute expressions. Local
verification covers 76 original/resaved template pairs and additional disabled
condition and explanation probes.

P2 adds qualified V8L1/V8L2 placement, saved appearance, five native primitive
kinds and saved-body bindings. V8L3 profile revision 2 also normalizes nonidentity
saved roots, verified against reopened SDK observations. Original V8L1 templates
have a separately guarded source-ID binding. Geometry conversion remains bounded;
a bound resource can still have unsupported curves/surfaces. P3 and P4 below
extend old inventories and internal mirrors. P5 adds explicit external resolution.
Actual V8L4 qualification remains separate work. This source change is not a release or remote CI result.

## Version-support P6: old originals and 2D inventory (unreleased)

[View and drawing access](drawing.md) separates document/registered-part
classification, bounded view framing and saved 2D geometry. Both byte orders have
individually gated observed versions. Points, lines, circles and arcs use local
millimetres; UTF-16LE text content does not imply font/layout support. Dimensions,
hatching, old text encodings and projection evaluation remain opaque.
Big-endian V6L1/V6L2/V7L1 internal part inventories retain raw coordinate blocks;
units, evaluated placement, external/mirror geometry and older 3D layouts are
not qualified. Exact Parasolid schema gaps remain a separate dependency issue.

Classification matches 38 originals (21 raw versions). A 1,018-file big-endian
scan leaves 54 partial view inventories and three unknown classifications.
All 45 non-3D V8L1 inputs have complete view framing and partial drawings.
Local SDK checks cover 108 observations and 1,759 decoded 2D elements, including
15 additional originals and eight new saved/reopened cases. This evidence
qualifies bounded records, not every file saved by each historical iCAD version.
V8L4 remains unsupported until real V8L4 inputs and independent evidence exist.

## Version-support P5: explicit external assemblies (unreleased)

[Assembly resolution](references.md) uses explicit roots or a caller resolver,
preserving saved names, actual paths, hashes and occurrence ownership separately.
Per-call snapshots detect dependency changes; missing, ambiguous, unsupported,
cycle and limit states remain explicit. Default reads do not load references.
The viewer composes external frames, target saved roots and mirror parity while
sharing source geometry evaluation and preserving per-occurrence selection.

Local evidence covers 15 existing assemblies plus 19 new saved/reopened bundles
in V7L6/V7L7/V8L1/V8L2/V8L3: 44 placed saved bodies and 38 native primitives match
SDK mass properties, centroids and frames. Independent authoring equations cover
all 82 placements. Missing child/grandchild cases retain two missing occurrences;
changed roots and compound mirrors have separate SDK/browser checks. Native
extrusions in the older corpus remain opaque; resolution does not add geometry
families. V7L2–V7L5, V8L4, general feature history and assembly GLB remain outside
this scope. Validation is local Linux/Wine evidence, not remote platform CI.

## Version-support P4: internal mirrors (unreleased)

V7L6/V7L7 revision 2 and V8L1/V8L2/V8L3 revision 3 now distinguish saved part
frames, absolute mirror parity and signed relative orientation. Native
box/cone signed heights are normalized without changing raw bytes; qualified
symmetric primitives and saved final B-Reps use their already reflected geometry.
The viewer preserves outward winding and occurrence selection.

Local evidence covers 45 real files, including 30 newly saved/reopened inputs:
136 native primitives and 38 converted saved bodies match SDK mass properties
and centroids. Independent reflection/rotation equations cover 42 cases. Two
V8 bodies from an entity mirror within one owner remain unsupported because
multiple distinct resource keys do not establish a reliable association.
V7L2–V7L5 mirrors and mirrored CSG history remain unsupported; external mirror
resolution is covered separately by P5. Earlier release/stage descriptions below retain their
historical boundaries; P4 is a local source change, not a published release.

## v0.3.0 scope

Version 0.3.0 preserves qualified binary attribute envelopes without
interrupting inventories, adds bounded V8L1/V8L2 inventories, and exposes native
cone/frustum/full-ring-torus parameters and display meshes. Old inventories keep
units, evaluated frames, appearance and geometry unavailable. Binary attribute
ownership is qualified; attribute meaning is not inferred.

The published `parasolid-core 0.3.1` dependency adds TORUS (54) to the exact V34
profile revision 2. Qualified saved toroidal resources no longer require an
external catalog; unknown types and nearby keys retain explicit diagnostics.

Mirrored geometry, automatic external loading,
typed attribute/BOM semantics, whole-scene GLB and general surface/feature
coverage remain outside this bounded release scope.

## v0.3.0 metadata and saved geometry

Version 0.3.0 extends qualified V7L7/V8L3 metadata traversal using counted
subrecord boundaries, adds full native spheres, and places qualified V8L3 saved
final bodies/imported solids through explicit resource keys. Shared resource
payloads retain distinct occurrence frames. The saved-body converter now accepts
bounded spherical, conical and toroidal surfaces, including trimmed examples.
Unknown framing, ambiguous ownership, unsupported schemas/topology and
mirrored/external ancestry remain explicit failures.

Public synthetic tests cover metadata boundaries/limits, sphere parameters and
closed outward display meshes, shared/reordered/missing resource bindings,
independent unit/frame expectations and periodic shared seams. Offline private
checks compare the saved SDK observations to part inventories and evaluated
geometry. Catalog-dependent resources remain catalog-dependent; no vendor
catalog or native sample is part of the distribution. Package/CI qualification
and publication remain separate from these local implementation checks.

The inventory and analytic display additions share these bounded ownership
and validation rules; broader model semantics remain future work.

## v0.2 delivered scope

P0, P1 and bounded P2 are implemented in version 0.2.0. See
[part structure and frames](parts.md), [native parameters and appearance](native.md)
and [current support](support.md) for qualified boundaries.

The primary v0.2 use case is Python access to part structure, placement and
attributes. The core scope is part definitions and occurrences, parent
relationships, verified transforms and units, names, and selected saved
attributes. Parts with unsupported geometry must remain in the part tree.
Native primitive reconstruction is a follow-up capability, not a prerequisite
for reading part metadata.

## Why this direction

Version 0.1.0 reads selected embedded Parasolid resources. An authored native
iCAD box has no such resource; importing the same box through Parasolid and
saving it produces a resource that can be read with an explicit schema catalog.
Reading more Parasolid schemas alone cannot address native iCAD geometry.
This also means that the part tree cannot be built solely from recognized
Parasolid resources. Part identity and metadata need their own container reader.

Resource counts also do not describe part counts. Definitions, occurrences,
parent relationships, source units and transforms need their own evidence and
representation before the package can reconstruct a scene.

## Priorities

| Priority | Work | Intended result |
| --- | --- | --- |
| Foundation | Differential files authored in iCAD, with independent expected values | Establish record ownership and distinguish semantic changes from save noise |
| Core | Part definitions, internal occurrences, hierarchy and units | Preserve shared parts and evaluate verified local-to-world transforms |
| Core | Names and selected part attributes | Query saved properties without losing raw values, ownership or overrides |
| Core | Python traversal and occurrence rows | Query each placement together with its definition, properties and diagnostics |
| Next | Color, visibility and native box/cylinder parameters | Extend model inspection independently of the core part graph |
| Next | Additional exact Parasolid profiles | Reduce explicit catalog requirements for validated schema keys |
| P2 | Optional resource preview and GLB export | Explicit resource selection and caller-declared units; no assembly placement |

The core scope follows the differential corpus evidence.
Unknown record layouts must remain unsupported. A useful partial model must
identify omitted or unresolved content rather than present an empty or complete
model. Native primitive parameters and an evaluated B-Rep are separate results.
Part graph, placement, attribute and geometry completeness must be reported
separately. Structural occurrence counts must not be presented as a qualified BOM.

Attribute access should preserve ordered entries, raw names and values, verified
types and units, and the definition or occurrence that owns each value. Missing,
empty, undecodable and inherited values must remain distinguishable. Effective
values should be exposed only when inheritance and override rules are verified.

## Validation before support claims

Start with one part, repeated placement, translation, rotation, hierarchy,
Japanese names, and attribute addition, modification and deletion. Include an
unchanged resave control and compare each file with its recorded parent case.
Then test property inheritance and occurrence overrides, color, visibility and
native primitive dimensions. Include
native and Parasolid-imported representations and mixed models. Save and reopen
each case in iCAD, and compare with known construction values and independent
geometry where appropriate.

Use separate examples to validate transform composition, source versus display
units, mirrored placements, missing references and unsupported native shapes.
A mirror that has not been validated must remain unsupported. Preserve external
reference information; automatic external-file loading is outside the initial
scope. Part graphs, placement and attributes need independently authored holdout
models, even where geometry remains unsupported. New geometry cases also need
their own holdout validation.

Existing inspection, extraction, geometry and CLI behavior should remain
compatible. New model results need explicit completeness, provenance, bounded
traversal and diagnostics. Release qualification continues to include native
file regression, public synthetic tests, platform wheel installation, source
rebuild and artifact verification.

## P0 implementation status

The [part API](parts.md) now provides bounded native hierarchy,
internal snapshot definitions, unresolved external references, millimetre world
and relative part frames, comments and general extended text. Save/reopen SDK
oracles cover nested noncommuting rotations, independent holdouts, per-occurrence
text, Japanese names, repeated references and mixed geometry representations.
Synthetic tests cover corruption, limits and unsupported states.

Evidence refined the original sharing proposal: an iCAD same-name group can
contain independently stored geometry and attributes. Internal records therefore
retain separate snapshot definitions. Repeated saved external names share only
an unresolved reference within their host; they do not establish file identity.

Mirrored frame evaluation, custom named/typed attributes, inherited effective
values, automatic external loading and resource-to-part geometry mapping remain
unqualified. The selected stored-text scope is usable without those features.
Release qualification covers platform wheels and their cold-install checks
separately from the private iCAD-authored regression corpus.

## P1 implementation status

Qualified native boxes/cylinders now expose dimensions and their global frames.
Stored entity palette indices, visibility and layer values are available without
inventing part-level inheritance. Unsupported shapes, mirrors and converted
Parasolid entities retain their source ranges and diagnostics. Parameter reading
remains separate from native B-Rep evaluation and whole-scene reconstruction.

The exact `SCH_3401212_34101_13006` profile is now included through the published
`parasolid-core 0.3.0` dependency. Qualified embedded resources use this built-in
profile without an external catalog. Raw values, byte boundaries and B-Rep are
checked against the explicit-catalog path, with separate SDK geometry evidence.
No nearby-key fallback or bundled vendor catalog is used. This does not establish
resource ownership, global geometry placement or general file units.

## P2 implementation status

The optional [preview API and CLI](preview.md) exports a selected solid resource
to a metre-based GLB, a provenance manifest and an offline local viewer. It
maps the reader's B-Rep into the pinned public conversion backend without
reparsing the resource. Source units are mandatory; output coordinates retain
the resource axes/origin and do not apply part placement or saved appearance.
Conversion must be complete and pass OCCT validity checks. Boxes, cylinders,
spheres and an independent holdout have local SDK/mesh checks; an authored
through-hole resource fails conversion and is explicitly unsupported.

The base install remains independent of preview dependencies. Artifact checks
and optional cold-install tests accompany synthetic geometry, limits, CLI and
loopback-server checks. Local headless-browser validation covers rendering and
source picking; remote platform CI and release publication are separate gates.

## V7L7 stage 2: bounded part inventory

Version 0.3.0 adds the verified little-endian V7L7 record profile to the
part API, CLI and viewer. It reads saved part hierarchy, names, comments, general
extended text and external reference names. Profile-specific metadata framing
is accepted only for observed layouts; unknown layouts stop traversal and retain
the unparsed tail. This is bounded support, not general V7L7 compatibility.

Validation compares saved/reopened SDK inventories across 24 cases and three
save generations, plus an original legacy model. The existing V8L3 placement and
primitive regressions remain separate checks. Public tests use synthetic data.

## V7L7 stage 3: placement and standalone primitive owners

Version 0.3.0 qualifies millimetre placement relative to the saved
root frame. Raw coordinate blocks retain their bytes and ranges. Reopened SDK
checks include translated/rotated roots, nested placements, an independent
compound-rotation holdout and an original legacy model.

Complete internal owners consisting entirely of qualified nonmirrored
box/cylinder/sphere headers expose saved entity appearance. Boxes and cylinders
also expose dimensions and global frames for the viewer. Final SDK faces,
edges, mass properties and appearance are checked separately from part frames.
Unknown or CSG entities keep the whole owner's list opaque, including operands
that resemble standalone primitives. Incomplete indexes, unqualified root
contexts and mirrored/external ancestry cannot provide evaluated geometry.
Additional layouts and broader legacy metadata framing remain later work.

## V7L7 stage 4: bounded final boolean bodies

The separate [CSG API](csg.md) reads explicitly bound saved postfix programs
and evaluates box/cylinder union, difference and intersection with optional
OCCT. The viewer opts in with `--csg`, displays final results and retains source
operands without drawing them. Unknown programs reject the whole body.
SDK comparisons cover repeated cuts, mixed operations, disconnected results,
multiple owners, root transforms, held-out cases and saved final appearance.
Negative heights and additional feature operations remain unqualified.

## V7L7 saved final bodies

[Saved final body display](saved-bodies.md) binds qualified final markers to
resources by unique source IDs and applies their saved frames and units. This
provides a display path for the design-change tutorial while its feature
history remains outside the CSG replay scope.

## Deferred scope

General CSG evaluation, full feature history, complete 2D drawings and dimensions,
ICD writing, and unrestricted whole-model STEP/mesh export are later work.
Broader resource-to-part mapping and assembly GLB export remain follow-up work.
Resource-local previews retain explicit coordinate and completeness limits.

## Version-support P3: old little-endian V7 inputs (unreleased)

V7L2–V7L6 now have individually selected part profiles. Original installed
files establish the view, tag, record, metadata and terminal boundaries;
V7L2–V7L5 retain opaque entity geometry while exposing structure and part frames.
V7L6 also uses bounded standalone primitives and saved final-body bindings.
Saved CSG programs remain unqualified for V7L6.

The private original-file scan covers 120 unique files: 92 qualified 3D indices
and 28 unsupported registered-part/non-3D layouts. SDK evidence uses actual
originals and genuine V7L6 saves reopened in iCAD V8L3-09A. V7L2–V7L5 requests
that fall back to V8L3 are not counted as old-version files. Broader mirror
geometry, external-reference resolution, non-3D/big-endian inputs and actual
V8L4 inputs remain later stages. Public tests contain independently authored
bytes; vendor files, SDK dumps and schema catalogs are not distributed.
