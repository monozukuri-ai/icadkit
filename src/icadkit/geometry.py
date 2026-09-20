"""Resource-scoped geometry with Rust-owned data and bounded Python pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, cast

from . import _core
from .errors import IcadError, LimitExceededError
from .models import ByteRange, Diagnostic, InspectionStatus, SourceRef, Status
from .schema import SchemaCatalog, _positive_limits

if TYPE_CHECKING:
    from .document import Document

MetadataValue = (
    None
    | bool
    | int
    | float
    | str
    | tuple["MetadataValue", ...]
    | Mapping[str, "MetadataValue"]
)
FieldScalar = None | bool | int | float | tuple[None | float, ...]
BrepCollection = Literal[
    "bodies",
    "regions",
    "shells",
    "faces",
    "loops",
    "half_edges",
    "edges",
    "vertices",
    "points",
    "curves",
    "surfaces",
]
GeometryScope = Literal["extraction", "raw_geometry", "brep", "topology"]


def _freeze(value: Any) -> MetadataValue:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return cast(MetadataValue, value)


def _page(start: int, count: int) -> None:
    for name, n in (("start", start), ("count", count)):
        if not isinstance(n, int) or isinstance(n, bool):
            raise TypeError(f"{name} must be an integer")
        if not 0 <= n <= (1 << 63) - 1:
            raise ValueError(f"{name} must be between 0 and 2**63 - 1")
    if count > 1000:
        raise ValueError("page count must be at most 1000")


def _index(index: int) -> None:
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError("node index must be an integer")
    if not 0 <= index <= (1 << 32) - 1:
        raise ValueError("node index must fit in an unsigned 32-bit integer")


@dataclass(frozen=True)
class GeometryLimits:
    max_payload_bytes: int = 64 * 1024 * 1024
    max_nodes: int = 100_000
    max_schema_types: int = 65_536
    max_fields_per_type: int = 4096
    max_string_bytes: int = 1024 * 1024
    max_variable_elements: int = 1_000_000
    max_diagnostics: int = 1000

    def __post_init__(self) -> None:
        _positive_limits(self)


@dataclass(frozen=True)
class GeometryDiagnostic(Diagnostic):
    """byte_offset is in ICD; decoded_offset is in the extracted X_B.

    A compressed stream has no byte-for-byte mapping between those spaces.
    container_range always identifies the owning encoded payload.
    """

    scope: str
    resource_id: str
    container_range: ByteRange
    decoded_offset: int | None
    backend_code: str | None
    node_type: int | None
    node_index: int | None


@dataclass(frozen=True)
class GeometryStatus(InspectionStatus):
    topology: Status


@dataclass(frozen=True)
class SchemaSelection:
    kind: Literal["builtin", "external", "unavailable"]
    schema_key: str
    provider_schema: str
    catalog_sha256: str | None
    profile_id: str | None
    profile_revision: int | None
    profile_sha256: str | None
    coverage: str | None


@dataclass(frozen=True)
class RawNode:
    index: int
    node_type: int
    type_name: str
    decoded_range: ByteRange
    field_count: int
    user_field_count: int
    variable_length: int | None


@dataclass(frozen=True)
class RawField:
    index: int
    name: str
    field_type: str
    pointer_class: int
    element_count: int
    transmitted: bool
    value_count: int
    decoded_range: ByteRange


@dataclass(frozen=True)
class RawGeometry:
    node_count: int
    schema_count: int
    schema_key: str
    terminator_range: ByteRange
    _handle: _core.GeometryHandle = field(repr=False, compare=False)

    def nodes(self, start: int = 0, count: int = 100) -> tuple[RawNode, ...]:
        _page(start, count)
        return tuple(_node(d) for d in self._handle.nodes(start, count))

    def node(self, index: int) -> RawNode:
        _index(index)
        return _node(self._handle.node(index))

    def fields(
        self, node_index: int, start: int = 0, count: int = 100
    ) -> tuple[RawField, ...]:
        _index(node_index)
        _page(start, count)
        return tuple(
            RawField(**{**d, "decoded_range": ByteRange(*d["decoded_range"])})
            for d in self._handle.fields(node_index, start, count)
        )

    def field_values(
        self, node_index: int, field_index: int, start: int = 0, count: int = 100
    ) -> tuple[FieldScalar, ...]:
        _index(node_index)
        _page(field_index, 0)
        _page(start, count)
        return tuple(
            cast(FieldScalar, _freeze(v))
            for v in self._handle.field_values(node_index, field_index, start, count)
        )

    def user_fields(
        self, node_index: int, start: int = 0, count: int = 100
    ) -> tuple[int | None, ...]:
        _index(node_index)
        _page(start, count)
        return tuple(self._handle.user_fields(node_index, start, count))

    def to_bytes(self) -> bytes:
        """Copy retained original X_B bytes; this is not an independent writer."""
        return self._handle.raw_bytes()


@dataclass(frozen=True)
class NodeSource:
    node_index: int
    node_type: int
    type_name: str
    node_id: int | None
    decoded_range: ByteRange


@dataclass(frozen=True)
class BrepEntity:
    id: int
    source: NodeSource
    attributes: Mapping[str, MetadataValue]


@dataclass(frozen=True)
class Brep:
    """Resource-local B-Rep, with source numeric units left unspecified.

    vertex_bounds excludes curved extrema. Surface area and volume are optional
    backend metrics, not iCAD model measurements or assembly quantities.
    """

    complete: bool
    schema_key: str
    source_format: str
    counts: Mapping[str, int]
    curve_kinds: Mapping[str, int]
    surface_kinds: Mapping[str, int]
    topology_valid: bool
    closed_loop_count: int
    closed_edge_ring_count: int
    euler_characteristic: int
    vertex_bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None
    surface_area: float | None
    volume: float | None
    _handle: _core.GeometryHandle = field(repr=False, compare=False)

    def entities(
        self, collection: BrepCollection, start: int = 0, count: int = 100
    ) -> tuple[BrepEntity, ...]:
        """Return a page; geometry parameters/arrays are materialized on request."""
        if not isinstance(collection, str):
            raise TypeError("collection must be a string")
        if collection not in self.counts:
            raise ValueError(f"unknown B-Rep collection: {collection}")
        _page(start, count)
        return tuple(
            BrepEntity(
                d["id"],
                NodeSource(
                    **{
                        **d["source"],
                        "decoded_range": ByteRange(*d["source"]["decoded_range"]),
                    }
                ),
                cast(Mapping[str, MetadataValue], _freeze(d["attributes"])),
            )
            for d in self._handle.entities(collection, start, count)
        )


class IncompleteGeometryError(IcadError):
    """A requested completeness scope was not established."""


@dataclass(frozen=True)
class GeometryResult:
    resource_id: str
    source_sha256: str
    source: SourceRef | None
    status: GeometryStatus
    schema: SchemaSelection | None
    raw: RawGeometry | None
    brep: Brep | None
    diagnostics: tuple[GeometryDiagnostic, ...]

    def require_complete(self, scope: GeometryScope = "brep") -> GeometryResult:
        if scope not in ("extraction", "raw_geometry", "brep", "topology"):
            raise ValueError("scope must be extraction, raw_geometry, brep or topology")
        if getattr(self.status, scope) != "complete":
            diagnostic = next(
                (d for d in self.diagnostics if d.scope == scope),
                self.diagnostics[0]
                if self.diagnostics
                else Diagnostic(
                    "unsupported",
                    "geometry.incomplete",
                    None,
                    f"{scope} is {getattr(self.status, scope)}",
                ),
            )
            raise IncompleteGeometryError(diagnostic)
        return self


def _node(data: dict[str, Any]) -> RawNode:
    return RawNode(**{**data, "decoded_range": ByteRange(*data["decoded_range"])})


def _diagnostic(data: dict[str, Any]) -> GeometryDiagnostic:
    return GeometryDiagnostic(
        **{**data, "container_range": ByteRange(*data["container_range"])}
    )


def _read_geometry(
    doc: Document,
    resource_id: str,
    schema: SchemaCatalog | None,
    limits: GeometryLimits | None,
) -> GeometryResult:
    from .document import _raise_native

    if not isinstance(resource_id, str):
        raise TypeError("resource_id must be a string from this document's index")
    if schema is not None and not isinstance(schema, SchemaCatalog):
        raise TypeError("schema must be a SchemaCatalog instance")
    if limits is None:
        limits = GeometryLimits()
    elif not isinstance(limits, GeometryLimits):
        raise TypeError("limits must be a GeometryLimits instance")
    policy = (
        limits.max_payload_bytes,
        limits.max_nodes,
        limits.max_schema_types,
        limits.max_fields_per_type,
        limits.max_string_bytes,
        limits.max_variable_elements,
        limits.max_diagnostics,
    )
    try:
        handle = doc._handle.read_geometry(
            resource_id, schema._handle if schema else None, policy
        )
    except _core.InspectionError as exc:
        _raise_native(exc)
    except _core.GeometryError as exc:
        raise LimitExceededError(_diagnostic(exc.args[0])) from None
    data = handle.summary()
    raw = None
    if data["has_raw"]:
        r = handle.raw_summary()
        raw = RawGeometry(
            **{
                **r,
                "terminator_range": ByteRange(*r["terminator_range"]),
                "_handle": handle,
            }
        )
    brep = None
    if data["has_brep"]:
        b = handle.brep_summary()
        for key in ("counts", "curve_kinds", "surface_kinds", "vertex_bounds"):
            b[key] = _freeze(b[key])
        brep = Brep(**b, _handle=handle)
    source = data["source"]
    if source is not None:
        source = SourceRef(
            **{
                **source,
                "container_range": ByteRange(*source["container_range"]),
                "decoded_range": ByteRange(*source["decoded_range"]),
            }
        )
    return GeometryResult(
        resource_id=data["resource_id"],
        source_sha256=data["source_sha256"],
        source=source,
        status=GeometryStatus(
            header=doc.status.header,
            container=doc.status.container,
            model="not_checked",
            **data["status"],
        ),
        schema=SchemaSelection(**data["schema"]) if data["schema"] else None,
        raw=raw,
        brep=brep,
        diagnostics=tuple(_diagnostic(d) for d in data["diagnostics"]),
    )
