"""Small immutable results returned by the public API."""

from dataclasses import dataclass
from typing import Literal

Status = Literal["not_checked", "complete", "partial", "unsupported", "invalid"]
ErrorCategory = Literal["invalid", "unsupported", "limit_exceeded", "io"]
UnparsedReason = Literal[
    "unknown_header_fields",
    "record_payload",
    "uninspected_tail",
    "view_names",
    "unsupported_usr_layout",
    "unknown_usr_fields",
    "entity_metadata",
    "unsupported_resource",
    "unparsed_entities",
]


@dataclass(frozen=True)
class BuildInfo:
    """Compiled backend identity; profile presence does not imply ICD support."""

    version: str
    rust_core_version: str
    parasolid_core_version: str
    builtin_profile_ids: tuple[str, ...]


@dataclass(frozen=True)
class InspectionLimits:
    """Size policy, in bytes. Inspection never allocates record payloads."""

    max_file_bytes: int = 512 * 1024 * 1024
    max_record_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in ("max_file_bytes", "max_record_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 < value <= (1 << 64) - 1:
                raise ValueError(f"{name} must be between 1 and 2**64 - 1")


@dataclass(frozen=True)
class ReadLimits:
    """Owned input/index limits, plus encoded/decoded bytes per extraction.

    Outer USR records aggregate many resources, so their default cap is larger
    than one resource's cap. There is no decoded cache or bulk extraction API.
    """

    max_file_bytes: int = 512 * 1024 * 1024
    max_record_bytes: int = 512 * 1024 * 1024
    max_resource_bytes: int = 64 * 1024 * 1024
    max_records: int = 200_000
    max_resources: int = 100_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 < value <= (1 << 64) - 1:
                raise ValueError(f"{name} must be between 1 and 2**64 - 1")


@dataclass(frozen=True)
class ByteRange:
    """Half-open [start, end); the containing field specifies the coordinate space."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Diagnostic:
    category: ErrorCategory
    code: str
    byte_offset: int | None
    message: str


@dataclass(frozen=True)
class Header:
    byte_order: Literal["little", "big"]
    raw_version: bytes
    raw_name: bytes
    raw_mod: bytes
    name_cp932_candidate: str | None


@dataclass(frozen=True)
class RecordInfo:
    tag: str
    byte_range: ByteRange
    payload_range: ByteRange


@dataclass(frozen=True)
class UnparsedRange:
    byte_range: ByteRange
    reason: UnparsedReason
    record_tag: str | None


@dataclass(frozen=True)
class InspectionStatus:
    header: Status
    container: Status
    extraction: Status
    raw_geometry: Status
    brep: Status
    model: Status


@dataclass(frozen=True)
class Inspection:
    """Inspection of the leading framing, not reconstruction of the ICD model."""

    file_size: int
    bytes_read: int
    header: Header
    leading_records: tuple[RecordInfo, ...]
    unparsed_ranges: tuple[UnparsedRange, ...]
    status: InspectionStatus
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class ResourceRef:
    """Structurally indexed owner; payload validation is deferred to extraction.

    storage_range includes 0..7 alignment bytes. SourceRef.container_range
    reports the exact encoded payload after extraction. source_id is opaque.
    """

    resource_id: str
    kind: Literal["parasolid_x_b"]
    owner_range: ByteRange
    storage_range: ByteRange
    encoding: Literal["raw", "zlib"]
    declared_decoded_bytes: int
    owner_type: int
    layout_version: int
    source_id: int


@dataclass(frozen=True)
class SourceRef:
    """Provenance in separate original-container and decoded-payload coordinates."""

    source_sha256: str
    resource_id: str
    container_range: ByteRange
    decoded_range: ByteRange
    payload_sha256: str
    encoding: Literal["raw", "zlib"]


@dataclass(frozen=True)
class Extraction:
    """Verified byte extraction, without a schema-dependent geometry parse."""

    payload: bytes
    source: SourceRef
    status: InspectionStatus
