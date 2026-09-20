"""Explicit, bounded schema catalogs; no global catalog registration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import _core


def _positive_limits(value: object) -> None:
    for name in value.__dataclass_fields__:  # type: ignore[attr-defined]
        n = getattr(value, name)
        if not isinstance(n, int) or isinstance(n, bool):
            raise TypeError(f"{name} must be an integer")
        if not 0 < n <= (1 << 63) - 1:
            raise ValueError(f"{name} must be between 1 and 2**63 - 1")


@dataclass(frozen=True)
class CatalogLimits:
    max_file_bytes: int = 4 * 1024 * 1024
    max_schema_types: int = 65_536
    max_fields_per_type: int = 4096
    max_string_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        _positive_limits(self)


@dataclass(frozen=True)
class SchemaCatalog:
    """One immutable catalog, validated against its explicitly expected ID.

    expected_sha256 additionally pins the original bytes when supplied. Loading
    this object never changes built-in profile selection in other reads.
    """

    schema_id: str
    sha256: str
    modeller_version: str
    definition_count: int
    _handle: _core.CatalogHandle = field(repr=False, compare=False)

    @classmethod
    def from_file(
        cls,
        path: str | os.PathLike[str],
        *,
        expected_id: str,
        expected_sha256: str | None = None,
        limits: CatalogLimits | None = None,
    ) -> SchemaCatalog:
        filename = os.fspath(path)
        if not isinstance(filename, str):
            raise TypeError("catalog path must be str or os.PathLike[str]")
        return cls._load(filename, expected_id, expected_sha256, limits)

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        expected_id: str,
        expected_sha256: str | None = None,
        limits: CatalogLimits | None = None,
    ) -> SchemaCatalog:
        if not isinstance(data, bytes):
            raise TypeError("catalog content must be immutable bytes")
        return cls._load(data, expected_id, expected_sha256, limits)

    @classmethod
    def _load(
        cls,
        data: bytes | str,
        expected_id: str,
        expected_sha256: str | None,
        limits: CatalogLimits | None,
    ) -> SchemaCatalog:
        from .document import _raise_native

        if not isinstance(expected_id, str):
            raise TypeError("expected_id must be an ASCII numeric string")
        if not re.fullmatch(r"[0-9]+", expected_id):
            raise ValueError("expected_id must be an ASCII numeric string")
        if expected_sha256 is not None:
            if not isinstance(expected_sha256, str):
                raise TypeError("expected_sha256 must be a hexadecimal string")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
                raise ValueError("expected_sha256 must contain 64 hexadecimal digits")
            expected_sha256 = expected_sha256.lower()
        if limits is None:
            limits = CatalogLimits()
        elif not isinstance(limits, CatalogLimits):
            raise TypeError("limits must be a CatalogLimits instance")
        policy = (
            limits.max_file_bytes,
            limits.max_schema_types,
            limits.max_fields_per_type,
            limits.max_string_bytes,
        )
        try:
            if isinstance(data, bytes):
                handle = _core.catalog_bytes(data, expected_id, expected_sha256, policy)
            else:
                handle = _core.catalog_path(data, expected_id, expected_sha256, policy)
        except _core.InspectionError as exc:
            _raise_native(exc)
        except OSError as exc:
            if isinstance(data, str) and exc.filename is None:
                exc.filename = data
            raise
        return cls(*handle.summary(), handle)
