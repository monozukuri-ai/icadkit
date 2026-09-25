"""Native part records, with explicit boundaries for unresolved semantics."""

import math
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from . import _core
from .models import ByteRange, Diagnostic, Status
from .native import NativeEntity, _entity_row, _read_entity

if TYPE_CHECKING:
    from .document import Document


@dataclass(frozen=True)
class PartLimits:
    """Entity/part allocation and hierarchy depth limits; depth includes the root."""

    max_parts: int = 100_000
    max_entities: int = 500_000
    max_depth: int = 256
    max_property_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 < value <= (1 << 31) - 1:
                raise ValueError(f"{name} must be between 1 and 2**31 - 1")


@dataclass(frozen=True)
class PartProperty:
    """An explicitly stored field, without inheritance or type inference.

    Empty text is an empty string; undecodable bytes give value=None. An absent
    property is not equivalent to either case. raw_value includes source padding.
    """

    name: str
    value: str | None
    raw_value: bytes
    owner_id: str
    byte_range: ByteRange
    encoding: Literal["cp932", "utf-16-le"] = "cp932"
    origin: Literal["stored"] = "stored"
    owner_kind: Literal["occurrence"] = "occurrence"
    value_type: Literal["text"] = "text"


Matrix4 = tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class PartOpaqueAttribute:
    """A bounded binary attribute with qualified ownership, unknown semantics."""

    source_id: int
    subtype: int
    owner_id: str
    byte_range: ByteRange
    raw_bytes: bytes
    status: Literal["unsupported"] = "unsupported"


@dataclass(frozen=True)
class PartReference:
    """Saved external model name. No filesystem search or loading is performed."""

    name: str | None
    raw_name: bytes
    byte_range: ByteRange
    status: Literal["not_loaded"] = "not_loaded"
    path: None = None


@dataclass(frozen=True)
class PartDefinition:
    """Snapshot definition or unresolved external model reference.

    Internal records remain separate even when names or geometry match.
    External occurrences with the same saved model name share an unresolved
    reference within this document, not a verified identity across files.
    """

    definition_id: str
    kind: Literal["internal", "external"]
    occurrence_ids: tuple[str, ...]
    reference: PartReference | None = None
    status: Status = "complete"


@dataclass(frozen=True)
class PartPlacement:
    """Part coordinate frame, independent of geometry payload coordinates.

    Matrices use rows and act on column vectors, in millimetres. world_transform
    comes from the saved part frame; local_transform is inverse(parent world)
    times world. The original first block (values/raw_bytes) is retained, but
    is not used as the part frame or as a geometry transform.
    Qualified global frames normalize by inverse(saved root frame). Coordinate
    transforms remain right-handed, matching the SDK. orientation transforms
    additionally encode the saved occurrence parity by reversing Y for a mirror.
    They describe occurrence orientation, never another geometry transform.
    All original coordinate blocks remain unchanged.
    """

    values: tuple[float | None, ...]
    raw_bytes: bytes
    byte_range: ByteRange
    status: Status = "unsupported"
    local_transform: Matrix4 | None = None
    world_transform: Matrix4 | None = None
    coordinate_values: tuple[float | None, ...] = ()
    raw_coordinate_bytes: bytes = b""
    coordinate_byte_range: ByteRange | None = None
    orientation_world_transform: Matrix4 | None = None
    orientation_local_transform: Matrix4 | None = None


@dataclass(frozen=True)
class Part:
    """One saved native part record, including the document root.

    part_id identifies a record in this snapshot, not a persistent CAD identity.
    A name does not establish shared definition identity. The original numeric
    references are retained even when they cannot be uniquely resolved.
    """

    part_id: str
    source_id: int
    view_offset: int
    flags: int
    is_root: bool
    name: str | None
    raw_name: bytes
    parent_id: str | None
    parent_source_id: int
    first_child_source_id: int
    previous_source_id: int
    next_source_id: int
    properties: tuple[PartProperty, ...]
    placement: PartPlacement
    byte_range: ByteRange
    definition_id: str | None = None
    resource_ids: None = None
    geometry_status: Literal["not_checked"] = "not_checked"
    external_reference: PartReference | None = None
    is_external: bool | None = None
    is_mirror: bool | None = None
    entities: tuple[NativeEntity, ...] = ()
    native_geometry_status: Status = "not_checked"
    appearance_status: Status = "not_checked"
    opaque_attributes: tuple[PartOpaqueAttribute, ...] = ()

    @property
    def comment(self) -> str | None:
        return self.properties[0].value

    @property
    def extra_info(self) -> str | None:
        """Stored extended information; None for absent, ambiguous or undecodable."""
        values = [p.value for p in self.properties if p.name == "extra_info"]
        return values[0] if len(values) == 1 else None


@dataclass(frozen=True)
class PartStatus:
    index: Status
    hierarchy: Status
    text: Status
    attributes: Status = "partial"
    placements: Status = "unsupported"
    definitions: Status = "unsupported"
    units: Status = "unsupported"
    references: Status = "not_checked"
    stored_attributes: Status = "not_checked"
    geometry: Status = "not_checked"
    model: Status = "partial"
    native_geometry: Status = "not_checked"
    appearance: Status = "not_checked"


@dataclass(frozen=True)
class PartOpaqueRange:
    byte_range: ByteRange
    reason: str


@dataclass(frozen=True)
class PartProfile:
    """Matched record policies, not a claim of document or geometry completeness."""

    profile_id: str
    byte_order: Literal["little", "big"]
    raw_version: bytes
    part_tag: int
    coordinate_convention: Literal["root_relative", "saved_global", "unqualified"]
    entity_policy: Literal[
        "standalone_owner", "saved_entities", "inventory_only", "opaque_entities"
    ]
    source_length_unit: Literal["mm"] | None
    saved_body_layout: (
        Literal["v7l6_source_id", "v7_source_id", "v8_resource_key", "v8l1_saved_body"]
        | None
    )
    csg_layout: Literal["v7_postfix"] | None
    view_byte_range: ByteRange
    part_byte_range: ByteRange
    mirror_policy: Literal["stored_parity"] | None = None


@dataclass(frozen=True)
class PartView:
    byte_range: ByteRange
    kind: Literal["3d_global", "unknown"]


@dataclass(frozen=True)
class PartIndex:
    """A bounded part inventory; success does not mean a complete CAD model.

    Parts are in source order. walk() yields each record once, including
    disconnected or cyclic records, and never filters on geometry support.
    """

    source_sha256: str
    parts: tuple[Part, ...]
    status: PartStatus
    diagnostics: tuple[Diagnostic, ...]
    opaque_ranges: tuple[PartOpaqueRange, ...]
    source_length_unit: Literal["mm"] | None = None
    definitions: tuple[PartDefinition, ...] = ()
    length_unit_source: Literal["qualified_part_profile"] | None = None
    profile: PartProfile | None = None
    views: tuple[PartView, ...] = ()
    document_kind: Literal["unknown"] = "unknown"
    _by_id: dict[str, Part] = field(init=False, repr=False, compare=False)
    _children: dict[str | None, tuple[Part, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {p.part_id: p for p in self.parts})
        children: dict[str | None, list[Part]] = defaultdict(list)
        for p in self.parts:
            children[p.parent_id].append(p)
        object.__setattr__(
            self, "_children", {k: tuple(v) for k, v in children.items()}
        )

    def part(self, part_id: str) -> Part:
        """Find a snapshot record; unknown IDs raise KeyError."""
        return self._by_id[part_id]

    def definition(self, definition_id: str) -> PartDefinition:
        for definition in self.definitions:
            if definition.definition_id == definition_id:
                return definition
        raise KeyError(definition_id)

    def children(self, part_id: str) -> tuple[Part, ...]:
        """Children in source order; consult hierarchy status before trusting it."""
        self.part(part_id)
        return self._children.get(part_id, ())

    def walk(self, *, include_root: bool = True) -> Iterator[Part]:
        """Iterative parent-first traversal, with each record yielded at most once."""
        visited = set()
        starts = self._children.get(None, ()) + self.parts
        for start in starts:
            stack = [start]
            while stack:
                p = stack.pop()
                if p.part_id in visited:
                    continue
                visited.add(p.part_id)
                if include_root or not p.is_root:
                    yield p
                stack.extend(reversed(self._children.get(p.part_id, ())))

    def to_rows(self, *, include_root: bool = False) -> list[dict[str, object]]:
        """JSON-compatible per-part rows; not a BOM or definition deduplication.

        Raw values use hex. Unresolved geometry ownership and transforms stay
        null. Ordered properties retain empty/undecodable values.
        """
        return [
            {
                "part_id": p.part_id,
                "source_id": p.source_id,
                "parent_id": p.parent_id,
                "parent_source_id": p.parent_source_id,
                "is_root": p.is_root,
                "name": p.name,
                "raw_name_hex": p.raw_name.hex(),
                "properties": [
                    {
                        "name": v.name,
                        "value": v.value,
                        "raw_value_hex": v.raw_value.hex(),
                        "owner_id": v.owner_id,
                        "byte_range": asdict(v.byte_range),
                        "encoding": v.encoding,
                        "origin": v.origin,
                        "owner_kind": v.owner_kind,
                        "value_type": v.value_type,
                    }
                    for v in p.properties
                ],
                "byte_range": asdict(p.byte_range),
                "opaque_attributes": [
                    {
                        "source_id": a.source_id,
                        "subtype": a.subtype,
                        "owner_id": a.owner_id,
                        "byte_range": asdict(a.byte_range),
                        "raw_bytes_hex": a.raw_bytes.hex(),
                        "status": a.status,
                    }
                    for a in p.opaque_attributes
                ],
                "definition_id": p.definition_id,
                "resource_ids": p.resource_ids,
                "source_length_unit": self.source_length_unit,
                "length_unit_source": self.length_unit_source,
                "placement_values": p.placement.values,
                "raw_placement_hex": p.placement.raw_bytes.hex(),
                "coordinate_values": p.placement.coordinate_values,
                "raw_coordinate_hex": p.placement.raw_coordinate_bytes.hex(),
                "coordinate_byte_range": (
                    asdict(p.placement.coordinate_byte_range)
                    if p.placement.coordinate_byte_range
                    else None
                ),
                "local_transform": p.placement.local_transform,
                "world_transform": p.placement.world_transform,
                "orientation_world_transform": p.placement.orientation_world_transform,
                "orientation_local_transform": p.placement.orientation_local_transform,
                "placement_status": p.placement.status,
                "geometry_status": p.geometry_status,
                "hierarchy_status": self.status.hierarchy,
                "attributes_status": self.status.attributes,
                "is_external": p.is_external,
                "is_mirror": p.is_mirror,
                "entities": [_entity_row(e) for e in p.entities],
                "native_geometry_status": p.native_geometry_status,
                "appearance_status": p.appearance_status,
                "external_reference": (
                    {
                        "name": p.external_reference.name,
                        "raw_name_hex": p.external_reference.raw_name.hex(),
                        "byte_range": asdict(p.external_reference.byte_range),
                        "status": p.external_reference.status,
                        "path": p.external_reference.path,
                    }
                    if p.external_reference
                    else None
                ),
            }
            for p in self.walk(include_root=include_root)
        ]


def _decode(raw: bytes) -> str | None:
    # Only trailing storage padding is removed; internal NULs are preserved.
    try:
        return raw.rstrip(b" \0").decode("cp932", errors="strict")
    except UnicodeDecodeError:
        return None


def _frame(values: list[float]) -> Matrix4 | None:
    origin, z, x = values[:3], values[3:6], values[6:9]
    if not all(math.isfinite(v) for v in values):
        return None
    if any(abs(sum(v * v for v in axis) - 1) > 1e-8 for axis in (x, z)):
        return None
    if abs(sum(a * b for a, b in zip(x, z, strict=True))) > 1e-8:
        return None
    y = (
        z[1] * x[2] - z[2] * x[1],
        z[2] * x[0] - z[0] * x[2],
        z[0] * x[1] - z[1] * x[0],
    )
    return tuple((x[i], y[i], z[i], origin[i]) for i in range(3)) + (
        (0.0, 0.0, 0.0, 1.0),
    )


def _with_parity(frame: Matrix4, parity: int) -> Matrix4:
    """Encode occurrence parity without changing the saved X/Z axes or origin."""
    return tuple(
        tuple(v * parity if j == 1 else v for j, v in enumerate(row)) for row in frame
    )


def _determinant(frame: Matrix4) -> float:
    return sum(
        frame[0][i]
        * (
            frame[1][(i + 1) % 3] * frame[2][(i + 2) % 3]
            - frame[1][(i + 2) % 3] * frame[2][(i + 1) % 3]
        )
        for i in range(3)
    )


def _relative(parent: Matrix4, child: Matrix4) -> Matrix4 | None:
    # Both frames are already qualified as rigid: inverse rotation is transpose.
    result = tuple(
        tuple(sum(parent[k][i] * child[k][j] for k in range(3)) for j in range(3))
        + (sum(parent[k][i] * (child[k][3] - parent[k][3]) for k in range(3)),)
        for i in range(3)
    ) + ((0.0, 0.0, 0.0, 1.0),)
    return result if all(math.isfinite(v) for row in result for v in row) else None


def _global_entity(entity: NativeEntity, root: Matrix4 | None) -> NativeEntity:
    if entity.primitive is None:
        return entity
    world = _relative(root, entity.primitive.world_transform) if root else None
    if world is not None:
        return replace(
            entity, primitive=replace(entity.primitive, world_transform=world)
        )
    kind: Literal["unsupported", "invalid"] = (
        "unsupported" if root is None else "invalid"
    )
    return replace(
        entity,
        primitive=None,
        geometry_status=kind,
        diagnostics=entity.diagnostics
        + (
            Diagnostic(
                kind,
                "native.root_frame",
                entity.byte_range.start,
                "Global primitive frame is unavailable; source parameters retained",
            ),
        ),
    )


def _scope(statuses: list[Status], index_status: Status) -> Status:
    if not statuses:
        return index_status
    if "invalid" in statuses:
        return "invalid"
    if all(s == "complete" for s in statuses):
        return index_status
    return "unsupported" if all(s == "unsupported" for s in statuses) else "partial"


def _read_parts(doc: "Document", limits: PartLimits | None) -> PartIndex:
    from .document import _raise_native

    if limits is None:
        limits = PartLimits()
    elif not isinstance(limits, PartLimits):
        raise TypeError("limits must be a PartLimits instance")
    try:
        data = doc._handle.read_parts(
            (
                limits.max_parts,
                limits.max_entities,
                limits.max_depth,
                limits.max_property_bytes,
            )
        )
    except _core.InspectionError as exc:
        _raise_native(exc)
    counts = Counter((p["view_offset"], p["source_id"]) for p in data["parts"])
    ids = {
        (p["view_offset"], p["source_id"]): f"part:{p['byte_range'][0]:x}"
        for p in data["parts"]
        if counts[p["view_offset"], p["source_id"]] == 1
    }
    parts: list[Part] = []
    definitions: dict[str, PartDefinition] = {}
    occurrences: dict[str, list[str]] = defaultdict(list)
    reference_ids: dict[bytes, str] = {}
    diagnostics = [Diagnostic(**d) for d in data["diagnostics"]]
    raw_profile = data["profile"]
    profile = (
        PartProfile(
            raw_profile["profile_id"],
            raw_profile["byte_order"],
            raw_profile["raw_version"],
            raw_profile["part_tag"],
            raw_profile["coordinate_convention"],
            raw_profile["entity_policy"],
            raw_profile["source_length_unit"],
            raw_profile["saved_body_layout"],
            raw_profile["csg_layout"],
            ByteRange(*raw_profile["view_byte_range"]),
            ByteRange(*raw_profile["part_byte_range"]),
            raw_profile["mirror_policy"],
        )
        if raw_profile
        else None
    )
    root_relative = (
        profile is not None and profile.coordinate_convention == "root_relative"
    )
    mirrors_qualified = profile is not None and profile.mirror_policy == "stored_parity"
    inventory_only = profile is not None and profile.entity_policy == "inventory_only"
    opaque_entities = profile is not None and profile.entity_policy in (
        "inventory_only",
        "opaque_entities",
    )
    roots = [p for p in data["parts"] if p["is_root"]]
    root_frame = None
    if root_relative and len(roots) == 1 and data["hierarchy_status"] == "complete":
        root = roots[0]
        if all(math.isfinite(v) for v in root["placement_values"]):
            root_frame = _frame(root["coordinate_values"])
    if root_relative and data["parts"] and root_frame is None:
        diagnostics.append(
            Diagnostic(
                "unsupported",
                "parts.root_frame",
                12,
                "Global placement requires a complete hierarchy and a rigid "
                "saved root frame; raw coordinates retained",
            )
        )
    units_qualified = (
        bool(data["parts"])
        and profile is not None
        and profile.source_length_unit == "mm"
        and (not root_relative or root_frame is not None)
    )
    if inventory_only and data["parts"]:
        diagnostics.append(
            Diagnostic(
                "unsupported",
                "parts.inventory_profile",
                12,
                "This inventory profile does not qualify units, placement, geometry "
                "or appearance; raw values retained",
            )
        )
    blocked_placements: set[int] = set()
    if root_relative and root_frame is not None:
        children_by_source: dict[int, list[_core.RawPart]] = defaultdict(list)
        for raw_part in data["parts"]:
            children_by_source[raw_part["parent_source_id"]].append(raw_part)
        pending = [(roots[0], False)]
        while pending:
            raw_part, inherited_block = pending.pop()
            if inherited_block:
                blocked_placements.add(raw_part["source_id"])
            child_block = inherited_block or bool(
                raw_part["flags"] & (0x10 if mirrors_qualified else 0x18)
            )
            pending.extend(
                (child, child_block)
                for child in children_by_source[raw_part["source_id"]]
            )
    text_status: Status = "complete" if data["parts"] else "not_checked"
    stored_attributes: Status = text_status
    for p in data["parts"]:
        at, end = p["byte_range"]
        part_id = f"part:{at:x}"
        opaque_attributes = tuple(
            PartOpaqueAttribute(sid, subtype, part_id, ByteRange(*span), raw)
            for span, sid, subtype, raw in p["opaque_attributes"]
        )
        if opaque_attributes:
            stored_attributes = "partial"
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "parts.attribute_semantics",
                    opaque_attributes[0].byte_range.start,
                    "Binary attributes retain ownership and raw bytes; "
                    "their values are not interpreted",
                )
            )
        name, comment = _decode(p["raw_name"]), _decode(p["raw_comment"])
        if name is None or comment is None:
            text_status = "partial"
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "parts.text_encoding",
                    at + 20,
                    "Name or comment cannot be decoded as CP932; source bytes retained",
                )
            )
        properties = [
            PartProperty(
                "comment",
                comment,
                p["raw_comment"],
                part_id,
                ByteRange(at + 60, at + 108),
            )
        ]
        for byte_range, raw in p["extra_fields"]:
            try:
                value = raw.decode("utf-16-le", errors="strict").rstrip("\0")
            except UnicodeDecodeError:
                value = None
                stored_attributes = "partial"
                diagnostics.append(
                    Diagnostic(
                        "unsupported",
                        "parts.attribute_encoding",
                        byte_range[0],
                        "Extended information is not valid UTF-16LE; bytes retained",
                    )
                )
            properties.append(
                PartProperty(
                    "extra_info",
                    value,
                    raw,
                    part_id,
                    ByteRange(*byte_range),
                    "utf-16-le",
                )
            )
        if len(p["extra_fields"]) > 1:
            stored_attributes = "partial"
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "parts.multiple_extra_info",
                    at,
                    "Multiple extended records retained; no value chosen",
                )
            )
        known = p["is_root"] or (
            p["flags"] in (0, 8, 0x50, 0x58)
            and p["source_id"] & 0xF0000000 == 0xA0000000
        )
        external = bool(p["flags"] & 0x10) if known else None
        mirror = bool(p["flags"] & 8) if known else None
        reference = None
        definition_id = None
        if external:
            reference = PartReference(
                _decode(p["raw_reference_name"]),
                p["raw_reference_name"],
                ByteRange(at + 276, at + 316),
            )
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "parts.external_unresolved",
                    at + 276,
                    "External model and children not loaded; saved reference retained",
                )
            )
            if reference.name:
                key = reference.raw_name.rstrip(b" \0")
                definition_id = reference_ids.setdefault(key, f"external:{at:x}")
            else:
                diagnostics.append(
                    Diagnostic(
                        "unsupported",
                        "parts.reference_name",
                        at + 276,
                        "Empty or undecodable external name; reference not grouped",
                    )
                )
        elif known and not p["is_root"]:
            definition_id = f"internal:{at:x}"
        if definition_id:
            occurrences[definition_id].append(part_id)
            if definition_id not in definitions:
                definitions[definition_id] = PartDefinition(
                    definition_id,
                    "external" if external else "internal",
                    (),
                    reference,
                    "partial" if external else "complete",
                )
        placement_range = ByteRange(at + 108, at + 180)
        coordinate_range = ByteRange(at + 180, at + 252)
        finite = all(
            math.isfinite(v) for v in p["placement_values"] + p["coordinate_values"]
        )
        mirror_supported = mirrors_qualified and not external
        world = (
            _frame(p["coordinate_values"])
            if known and (not mirror or mirror_supported)
            else None
        )
        placement_status: Status = "complete" if world is not None else "unsupported"
        if not finite:
            placement_status = "invalid"
            world = None
            diagnostics.append(
                Diagnostic(
                    "invalid",
                    "parts.placement_nonfinite",
                    at + 108,
                    "Stored placement contains a nonfinite double; raw bits retained",
                )
            )
        elif known and (not mirror or mirror_supported) and world is None:
            placement_status = "invalid"
            diagnostics.append(
                Diagnostic(
                    "invalid",
                    "parts.coordinate_frame",
                    at + 180,
                    "Part frame axes are not orthonormal; no transform supplied",
                )
            )
        elif mirror and not mirror_supported:
            diagnostics.append(
                Diagnostic(
                    "unsupported",
                    "parts.mirrored_placement",
                    at + 8,
                    "Mirrored placement retained; transforms unsupported",
                )
            )
        if root_relative and world is not None:
            if p["source_id"] in blocked_placements:
                world = None
                placement_status = "unsupported"
                diagnostics.append(
                    Diagnostic(
                        "unsupported",
                        "parts.placement_ancestor",
                        at + 180,
                        "Placement beneath a mirrored or external ancestor "
                        "is not qualified",
                    )
                )
            else:
                world = _relative(root_frame, world) if root_frame is not None else None
            if world is None:
                unavailable = root_frame is None or p["source_id"] in blocked_placements
                placement_status = "unsupported" if unavailable else "invalid"
                if not unavailable:
                    diagnostics.append(
                        Diagnostic(
                            "invalid",
                            "parts.global_nonfinite",
                            at + 180,
                            "Root-relative transform overflowed; "
                            "global frame unavailable",
                        )
                    )
        if inventory_only:
            world = None
            if placement_status != "invalid":
                placement_status = "unsupported"
        parent = (p["view_offset"], p["parent_source_id"])
        entities = tuple(_read_entity(doc, e, part_id) for e in p["entities"])
        if root_relative:
            context = (
                root_frame
                if known
                and (not mirror or mirror_supported)
                and not external
                and p["source_id"] not in blocked_placements
                else None
            )
            entities = tuple(_global_entity(e, context) for e in entities)
        entity_scope = "partial" if external else data["index_status"]
        parts.append(
            Part(
                part_id,
                p["source_id"],
                p["view_offset"],
                p["flags"],
                p["is_root"],
                name,
                p["raw_name"],
                ids.get(parent) if p["parent_source_id"] else None,
                p["parent_source_id"],
                p["first_child_source_id"],
                p["previous_source_id"],
                p["next_source_id"],
                tuple(properties),
                PartPlacement(
                    tuple(
                        v if math.isfinite(v) else None for v in p["placement_values"]
                    ),
                    doc.source_bytes(placement_range),
                    placement_range,
                    placement_status,
                    world_transform=world,
                    coordinate_values=tuple(
                        v if math.isfinite(v) else None for v in p["coordinate_values"]
                    ),
                    raw_coordinate_bytes=doc.source_bytes(coordinate_range),
                    coordinate_byte_range=coordinate_range,
                    orientation_world_transform=(
                        _with_parity(world, -1 if mirror else 1)
                        if world is not None and mirrors_qualified and not external
                        else None
                    ),
                ),
                ByteRange(at, end),
                definition_id=definition_id,
                external_reference=reference,
                is_external=external,
                is_mirror=mirror,
                entities=entities,
                native_geometry_status=(
                    "unsupported"
                    if opaque_entities
                    else _scope([e.geometry_status for e in entities], entity_scope)
                ),
                appearance_status=(
                    "unsupported"
                    if opaque_entities
                    else _scope([e.appearance.status for e in entities], entity_scope)
                ),
                opaque_attributes=opaque_attributes,
            )
        )
    by_id = {p.part_id: p for p in parts}
    for i, part in enumerate(parts):
        placement = part.placement
        if placement.world_transform is None:
            continue
        local = None
        orientation_local = None
        if data["hierarchy_status"] == "complete":
            orientation_world = placement.orientation_world_transform
            if orientation_world is not None:
                if part.is_root:
                    orientation_local = orientation_world
                elif part.parent_id is not None:
                    parent_orientation = by_id[
                        part.parent_id
                    ].placement.orientation_world_transform
                    if parent_orientation is not None:
                        orientation_local = _relative(
                            parent_orientation, orientation_world
                        )
            if part.is_root:
                local = placement.world_transform
            elif part.parent_id is not None:
                parent_world = by_id[part.parent_id].placement.world_transform
                if parent_world is not None:
                    local = _relative(parent_world, placement.world_transform)
                    if local is None:
                        diagnostics.append(
                            Diagnostic(
                                "invalid",
                                "parts.local_nonfinite",
                                part.byte_range.start,
                                "Relative transform overflowed; "
                                "local frame unavailable",
                            )
                        )
                        placement = replace(placement, status="invalid")
        if local is None and placement.status == "complete":
            placement = replace(placement, status="partial")
        parts[i] = replace(
            part,
            placement=replace(
                placement,
                local_transform=local,
                orientation_local_transform=orientation_local,
            ),
        )
    external_found = any(p.is_external for p in parts)
    index_status = data["index_status"]
    hierarchy = data["hierarchy_status"]
    if hierarchy == "complete" and external_found:
        hierarchy = "partial"
    definition_status = _scope(
        [
            "complete" if p.definition_id and not p.is_external else "unsupported"
            for p in parts
            if not p.is_root
        ],
        index_status,
    )
    if external_found and definition_status != "invalid":
        definition_status = "partial"
    if text_status != "complete" or index_status != "complete":
        stored_attributes = "partial" if parts else index_status
    return PartIndex(
        doc.source_sha256,
        tuple(parts),
        PartStatus(
            index_status,
            hierarchy,
            text_status,
            placements=_scope([p.placement.status for p in parts], index_status),
            definitions=definition_status,
            units="complete" if units_qualified else "unsupported",
            references="partial" if external_found else index_status,
            stored_attributes=stored_attributes,
            native_geometry=_scope(
                [p.native_geometry_status for p in parts], index_status
            ),
            appearance=_scope([p.appearance_status for p in parts], index_status),
        ),
        tuple(diagnostics),
        tuple(
            PartOpaqueRange(ByteRange(*r), reason)
            for r, reason in data["opaque_ranges"]
        ),
        source_length_unit="mm" if units_qualified else None,
        definitions=tuple(
            replace(d, occurrence_ids=tuple(occurrences[d.definition_id]))
            for d in definitions.values()
        ),
        length_unit_source=("qualified_part_profile" if units_qualified else None),
        profile=profile,
        views=tuple(
            PartView(ByteRange(*v["byte_range"]), v["kind"]) for v in data["views"]
        ),
        document_kind=data["document_kind"],
    )
