"""Public API; keep the extension module private."""

from __future__ import annotations

import os
from importlib.metadata import version
from typing import cast

from . import _core
from .errors import (
    IcadError,
    InvalidFormatError,
    LimitExceededError,
    UnsupportedFormatError,
)
from .models import (
    BuildInfo,
    ByteRange,
    Diagnostic,
    ErrorCategory,
    Header,
    Inspection,
    InspectionLimits,
    InspectionStatus,
    RecordInfo,
    UnparsedRange,
)


def build_info() -> BuildInfo:
    """Initialize the native registry and return its compiled backend metadata."""
    rust_version, backend_version, profile_ids = _core.build_info()
    return BuildInfo(
        version=version("icadkit"),
        rust_core_version=rust_version,
        parasolid_core_version=backend_version,
        builtin_profile_ids=tuple(profile_ids),
    )


def inspect(
    source: str | os.PathLike[str] | bytes, *, limits: InspectionLimits | None = None
) -> Inspection:
    """Inspect MOD/DRW/RES framing and raw fields; bytes means content, not a path.

    Path input reads only 280 bytes on success and releases the GIL during I/O.
    Byte input is borrowed without a full copy. A CP932 name is only a candidate;
    undecodable names return None and a diagnostic while preserving raw bytes.
    """
    if limits is None:
        limits = InspectionLimits()
    elif not isinstance(limits, InspectionLimits):
        raise TypeError("limits must be an InspectionLimits instance")
    try:
        if isinstance(source, bytes):
            data = _core.inspect_bytes(
                source, limits.max_file_bytes, limits.max_record_bytes
            )
        elif isinstance(source, (str, os.PathLike)):
            path = os.fspath(source)
            if not isinstance(path, str):
                raise TypeError(
                    "path-like input must return str; bytes is input content"
                )
            try:
                data = _core.inspect_path(
                    path, limits.max_file_bytes, limits.max_record_bytes
                )
            except OSError as exc:
                if exc.filename is None:
                    exc.filename = path
                raise
        else:
            raise TypeError("source must be str, os.PathLike[str], or immutable bytes")
    except _core.InspectionError as exc:
        category, code, offset, message = exc.args
        diagnostic = Diagnostic(cast(ErrorCategory, category), code, offset, message)
        error_type: type[IcadError] = {
            "invalid": InvalidFormatError,
            "unsupported": UnsupportedFormatError,
            "limit_exceeded": LimitExceededError,
        }[category]
        raise error_type(diagnostic) from None
    return _inspection(data)


def _inspection(data: _core.RawInspection) -> Inspection:
    raw_header = data["header"]
    diagnostics: tuple[Diagnostic, ...] = ()
    try:
        name: str | None = (
            raw_header["raw_name"].rstrip(b" \0").decode("cp932", errors="strict")
        )
    except UnicodeDecodeError as exc:
        name = None
        diagnostics = (
            Diagnostic(
                "unsupported",
                "header.name_decode_failed",
                16 + exc.start,
                "name bytes cannot be decoded strictly as the CP932 candidate encoding",
            ),
        )
    return Inspection(
        file_size=data["file_size"],
        bytes_read=data["bytes_read"],
        header=Header(**raw_header, name_cp932_candidate=name),
        leading_records=tuple(
            RecordInfo(
                tag=r["tag"],
                byte_range=ByteRange(*r["byte_range"]),
                payload_range=ByteRange(*r["payload_range"]),
            )
            for r in data["leading_records"]
        ),
        unparsed_ranges=tuple(
            UnparsedRange(
                byte_range=ByteRange(*r["byte_range"]),
                reason=r["reason"],
                record_tag=r["record_tag"],
            )
            for r in data["unparsed_ranges"]
        ),
        status=InspectionStatus(**data["status"]),
        diagnostics=diagnostics,
    )
