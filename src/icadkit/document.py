"""Owned ICD documents with immutable indexes and lazy bounded extraction."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from . import _core
from .api import _inspection
from .drawing import DrawingIndex, DrawingLimits, _read_drawing
from .errors import (
    IcadError,
    InvalidFormatError,
    LimitExceededError,
    UnsupportedFormatError,
)
from .geometry import GeometryLimits, GeometryResult, _read_geometry
from .models import (
    ByteRange,
    Diagnostic,
    ErrorCategory,
    Extraction,
    Header,
    InspectionStatus,
    ReadLimits,
    RecordInfo,
    ResourceRef,
    SourceRef,
    UnparsedRange,
)
from .parameters import ParameterIndex, ParameterLimits, _read_parameters
from .parts import PartIndex, PartLimits, _read_parts
from .schema import SchemaCatalog
from .views import ViewIndex, ViewLimits, _read_views

if TYPE_CHECKING:
    from .references import AssemblyIndex, AssemblyLimits, ReferenceResolver


def _raise_native(exc: _core.InspectionError) -> NoReturn:
    category, code, offset, message = exc.args
    diagnostic = Diagnostic(cast(ErrorCategory, category), code, offset, message)
    error_type: type[IcadError] = {
        "invalid": InvalidFormatError,
        "unsupported": UnsupportedFormatError,
        "limit_exceeded": LimitExceededError,
    }[category]
    raise error_type(diagnostic) from None


@dataclass(frozen=True)
class Document:
    """Rust-owned snapshot; indexing does not decompress any payload.

    A complete resource index covers the supported USR entities only. Opaque
    views, metadata and the tail keep container=partial and model=not_checked.
    Extracted bytes remain valid independently of the document's lifetime.
    """

    file_size: int
    source_sha256: str
    header: Header
    records: tuple[RecordInfo, ...]
    resources: tuple[ResourceRef, ...]
    unparsed_ranges: tuple[UnparsedRange, ...]
    diagnostics: tuple[Diagnostic, ...]
    resource_index_status: Literal["complete", "partial"]
    status: InspectionStatus
    _handle: _core.DocumentHandle = field(repr=False, compare=False)

    def read_views(self, *, limits: ViewLimits | None = None) -> ViewIndex:
        """Classify directory-owned views and bound records, including old originals."""
        return _read_views(self, limits)

    def read_drawing(
        self,
        *,
        limits: DrawingLimits | None = None,
        view_limits: ViewLimits | None = None,
    ) -> DrawingIndex:
        """Read qualified saved 2D entities in view-local coordinates."""
        return _read_drawing(self, limits, view_limits)

    def read_parts(self, *, limits: PartLimits | None = None) -> PartIndex:
        """Read bounded native part records, independently of geometry support."""
        return _read_parts(self, limits)

    def read_assembly(
        self,
        *,
        search_roots: Sequence[str | Path] = (),
        resolver: ReferenceResolver | None = None,
        limits: AssemblyLimits | None = None,
        read_limits: ReadLimits | None = None,
        part_limits: PartLimits | None = None,
    ) -> AssemblyIndex:
        """Resolve explicitly; this document remains an immutable snapshot."""
        from .references import read_assembly

        return read_assembly(
            self,
            search_roots=search_roots,
            resolver=resolver,
            limits=limits,
            read_limits=read_limits,
            part_limits=part_limits,
        )

    def read_parameters(
        self,
        *,
        limits: ParameterLimits | None = None,
        part_limits: PartLimits | None = None,
    ) -> ParameterIndex:
        """Read saved 3D parameters without executing formulas or opening references."""
        return _read_parameters(self, limits, part_limits)

    def read_geometry(
        self,
        resource_id: str,
        *,
        schema: SchemaCatalog | None = None,
        limits: GeometryLimits | None = None,
    ) -> GeometryResult:
        """Parse one X_B using its exact built-in profile or an explicit catalog.

        Unsupported/invalid geometry is a local result. Resource-policy limits
        raise LimitExceededError. This never establishes whole-model completeness.
        """
        return _read_geometry(self, resource_id, schema, limits)

    def extract(self, resource_id: str) -> Extraction:
        """Decode one owner and verify size, checksum, EOF, padding and envelope.

        This does not parse Parasolid nodes or establish B-Rep completeness.
        A failure affects only this call; successful resources remain available.
        """
        if not isinstance(resource_id, str):
            raise TypeError("resource_id must be a string from this document's index")
        try:
            data = self._handle.extract(resource_id)
        except _core.InspectionError as exc:
            _raise_native(exc)
        return Extraction(
            payload=data["payload"],
            source=SourceRef(
                source_sha256=data["source_sha256"],
                resource_id=data["resource_id"],
                container_range=ByteRange(*data["container_range"]),
                decoded_range=ByteRange(*data["decoded_range"]),
                payload_sha256=data["payload_sha256"],
                encoding=data["encoding"],
            ),
            status=replace(self.status, extraction="complete"),
        )

    def extract_bytes(self, resource_id: str) -> bytes:
        """Extract the payload; use extract() when provenance is also needed."""
        return self.extract(resource_id).payload

    def source_bytes(self, byte_range: ByteRange) -> bytes:
        """Copy a bounded range of preserved source bytes, including unknowns."""
        if not isinstance(byte_range, ByteRange):
            raise TypeError("byte_range must be a ByteRange")
        for value in (byte_range.start, byte_range.end):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError("range coordinates must be integers")
            if not 0 <= value <= (1 << 64) - 1:
                raise ValueError(
                    "range coordinates must fit in an unsigned 64-bit integer"
                )
        try:
            return self._handle.source_bytes(byte_range.start, byte_range.end)
        except _core.InspectionError as exc:
            _raise_native(exc)


def read(
    source: str | os.PathLike[str] | bytes, *, limits: ReadLimits | None = None
) -> Document:
    """Read a snapshot, hash it and index supported records without signature scans.

    bytes means immutable content; paths are checked before input allocation.
    Rust owns one input copy. Native indexing and extraction release the GIL.
    """
    if limits is None:
        limits = ReadLimits()
    elif not isinstance(limits, ReadLimits):
        raise TypeError("limits must be a ReadLimits instance")
    policy = (
        limits.max_file_bytes,
        limits.max_record_bytes,
        limits.max_resource_bytes,
        limits.max_records,
        limits.max_resources,
    )
    try:
        if isinstance(source, bytes):
            handle = _core.read_bytes(source, policy)
        elif isinstance(source, (str, os.PathLike)):
            path = os.fspath(source)
            if not isinstance(path, str):
                raise TypeError(
                    "path-like input must return str; bytes is input content"
                )
            try:
                handle = _core.read_path(path, policy)
            except OSError as exc:
                if exc.filename is None:
                    exc.filename = path
                raise
        else:
            raise TypeError("source must be str, os.PathLike[str], or immutable bytes")
    except _core.InspectionError as exc:
        _raise_native(exc)
    data = handle.summary()
    info = _inspection(data["inspection"])
    return Document(
        file_size=info.file_size,
        source_sha256=data["source_sha256"],
        header=info.header,
        records=tuple(
            RecordInfo(
                r["tag"], ByteRange(*r["byte_range"]), ByteRange(*r["payload_range"])
            )
            for r in data["records"]
        ),
        resources=tuple(
            ResourceRef(
                resource_id=r["resource_id"],
                kind=r["kind"],
                owner_range=ByteRange(*r["owner_range"]),
                storage_range=ByteRange(*r["storage_range"]),
                encoding=r["encoding"],
                declared_decoded_bytes=r["declared_decoded_bytes"],
                owner_type=r["owner_type"],
                layout_version=r["layout_version"],
                source_id=r["source_id"],
            )
            for r in data["resources"]
        ),
        unparsed_ranges=tuple(
            UnparsedRange(ByteRange(*r["byte_range"]), r["reason"], r["record_tag"])
            for r in data["unparsed_ranges"]
        ),
        diagnostics=info.diagnostics
        + tuple(Diagnostic(**d) for d in data["diagnostics"]),
        resource_index_status=data["resource_index_status"],
        status=info.status,
        _handle=handle,
    )
