from typing import Any, Literal, TypedDict

from .models import ErrorCategory, Status, UnparsedReason

class InspectionError(Exception): ...

class RawHeader(TypedDict):
    byte_order: Literal["little", "big"]
    raw_version: bytes
    raw_name: bytes
    raw_mod: bytes

class RawRecord(TypedDict):
    tag: str
    byte_range: tuple[int, int]
    payload_range: tuple[int, int]

class RawUnparsedRange(TypedDict):
    byte_range: tuple[int, int]
    reason: UnparsedReason
    record_tag: str | None

class RawStatus(TypedDict):
    header: Status
    container: Status
    extraction: Status
    raw_geometry: Status
    brep: Status
    model: Status

class RawInspection(TypedDict):
    file_size: int
    bytes_read: int
    header: RawHeader
    leading_records: list[RawRecord]
    unparsed_ranges: list[RawUnparsedRange]
    status: RawStatus

def build_info() -> tuple[str, str, list[str]]: ...
def inspect_bytes(
    data: bytes, max_file_bytes: int, max_record_bytes: int
) -> RawInspection: ...
def inspect_path(
    path: str, max_file_bytes: int, max_record_bytes: int
) -> RawInspection: ...

class RawResource(TypedDict):
    resource_id: str
    kind: Literal["parasolid_x_b"]
    owner_range: tuple[int, int]
    storage_range: tuple[int, int]
    encoding: Literal["raw", "zlib"]
    declared_decoded_bytes: int
    owner_type: int
    layout_version: int
    source_id: int

class RawDiagnostic(TypedDict):
    category: ErrorCategory
    code: str
    byte_offset: int
    message: str

class RawDocument(TypedDict):
    inspection: RawInspection
    source_sha256: str
    records: list[RawRecord]
    resources: list[RawResource]
    unparsed_ranges: list[RawUnparsedRange]
    diagnostics: list[RawDiagnostic]
    resource_index_status: Literal["complete", "partial"]

class RawExtraction(TypedDict):
    payload: bytes
    source_sha256: str
    resource_id: str
    container_range: tuple[int, int]
    decoded_range: tuple[int, int]
    payload_sha256: str
    encoding: Literal["raw", "zlib"]

class DocumentHandle:
    def read_geometry(
        self,
        resource_id: str,
        catalog: CatalogHandle | None,
        policy: tuple[int, int, int, int, int, int, int],
    ) -> GeometryHandle: ...
    def summary(self) -> RawDocument: ...
    def extract(self, resource_id: str) -> RawExtraction: ...
    def source_bytes(self, start: int, end: int) -> bytes: ...

def read_bytes(
    data: bytes, policy: tuple[int, int, int, int, int]
) -> DocumentHandle: ...
def read_path(path: str, policy: tuple[int, int, int, int, int]) -> DocumentHandle: ...

class GeometryError(Exception): ...

class CatalogHandle:
    def summary(self) -> tuple[str, str, str, int]: ...

def catalog_bytes(
    data: bytes,
    expected_id: str,
    expected_sha256: str | None,
    policy: tuple[int, int, int, int],
) -> CatalogHandle: ...
def catalog_path(
    path: str,
    expected_id: str,
    expected_sha256: str | None,
    policy: tuple[int, int, int, int],
) -> CatalogHandle: ...

class GeometryHandle:
    def summary(self) -> dict[str, Any]: ...
    def raw_summary(self) -> dict[str, Any]: ...
    def brep_summary(self) -> dict[str, Any]: ...
    def nodes(self, start: int, count: int) -> list[dict[str, Any]]: ...
    def node(self, index: int) -> dict[str, Any]: ...
    def fields(self, index: int, start: int, count: int) -> list[dict[str, Any]]: ...
    def field_values(
        self, index: int, field: int, start: int, count: int
    ) -> list[Any]: ...
    def user_fields(self, index: int, start: int, count: int) -> list[int | None]: ...
    def raw_bytes(self) -> bytes: ...
    def entities(
        self, collection: str, start: int, count: int
    ) -> list[dict[str, Any]]: ...
