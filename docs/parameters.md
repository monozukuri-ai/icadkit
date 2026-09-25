# Saved 3D parameters

`Document.read_parameters()` reads saved parametric tables owned by parts in
the qualified 3D inventory. It does not execute expressions, edit dimensions,
regenerate geometry or open external files. No iCAD installation is needed.

```python
import icadkit

doc = icadkit.read("template.icd")
parts = doc.read_parts()
result = doc.read_parameters()
print(result.status)
for parameter in result.parameters:
    owner = parts.part(parameter.owner_part_id)
    print(owner.name, parameter.name, parameter.value, parameter.equation)
    print(parameter.comment, parameter.explain, parameter.enabled)
```

Qualified layouts include V8L1 standard-part templates and their V8L3 resaves,
and the same saved tables in V7L7/V8L3 internal placements. Version bytes alone
never enable decoding. A template with no child parts retains one document root;
its entities and parameters belong to that root. Repeated placements have
separate parameter identities even when names and values match.

`SavedParameter` exposes the name, saved scalar value, kind, raw type, equation,
explanation, condition comment and enabled state. Kinds are `number`, `length`,
`diameter`, `radius` and `angle`. Values retain stored double precision; they are
not rounded to the SDK's text output or replaced by an evaluated expression.
A previous dimension value is retained in `raw_values`, separately from `value`.
No unit conversion is performed.

`enabled` describes the saved condition. Unconditioned numeric variables are
enabled; unsupported or ambiguous condition states produce `None` and a
diagnostic. `raw_expression_state` preserves a separate producer field and is
not the enabled flag. `status` describes decoder completeness, not whether a
condition is active or a formula can execute.

Each value and condition retains `raw_bytes` and physical `byte_ranges`. Rows
can cross multiple length-framed blocks; concatenating their source ranges
reproduces the row. `ParameterTable` retains the raw kind, count, payload and
payload ranges, including the uninterpreted geometry-association table. IDs
identify records in this snapshot, not persistent CAD identities or shared
definitions.

Unknown types, table revisions, condition states, undecodable CP932 text and
ambiguous table pairs remain explicit. Position parameters and geometry
association semantics are unqualified. Nonfinite values, duplicate names within
one owner and out-of-range condition indexes cannot report a complete result.
Unknown tables retain their source ranges; the reader never searches their
payloads for plausible parameter names or another record signature.

```python
result = doc.read_parameters(
    limits=icadkit.ParameterLimits(max_parameters=10_000, max_bytes=8_388_608),
    part_limits=icadkit.PartLimits(max_parts=1_000, max_entities=50_000),
)
```

Defaults are 100,000 value rows and 16 MiB of candidate table records in total,
plus the normal part traversal limits. Exceeding them raises `LimitExceededError`.
The byte limit is checked before copying each table out of the Rust-owned
document. It is not a total process-memory limit.

`complete` covers exposed saved tables in this file's qualified 3D inventory.
It does not cover 2D views, external documents, final shape evaluation or the
whole model. An unsupported part profile produces an unsupported parameter
index, not a successful empty list. See [part inventories](parts.md) for root
ownership and [support boundaries](support.md) for geometry capabilities.

The old V7 part profiles also expose tables whose framing matches the saved-row
contract. Local root-table SDK checks cover 3 V7L2 rows, 7 V7L4 rows, 11 V7L5
rows and 3 genuinely saved V7L6 rows. This does not qualify every old parameter
kind, V7L3 parameter semantics, or expression execution.
