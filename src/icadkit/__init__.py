"""Bounded iCAD SX inspection, structural resource indexing and extraction."""

from importlib.metadata import version

from .api import build_info, inspect
from .document import Document, read
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
    Extraction,
    Header,
    Inspection,
    InspectionLimits,
    InspectionStatus,
    ReadLimits,
    RecordInfo,
    ResourceRef,
    SourceRef,
    UnparsedRange,
)

__version__ = version("icadkit")
__all__ = [
    "Brep",
    "BrepEntity",
    "GeometryDiagnostic",
    "GeometryLimits",
    "GeometryResult",
    "GeometryStatus",
    "IncompleteGeometryError",
    "NodeSource",
    "RawField",
    "RawGeometry",
    "RawNode",
    "SchemaSelection",
    "CatalogLimits",
    "SchemaCatalog",
    "BuildInfo",
    "ByteRange",
    "Diagnostic",
    "Document",
    "Extraction",
    "Header",
    "IcadError",
    "Inspection",
    "InspectionLimits",
    "InspectionStatus",
    "InvalidFormatError",
    "LimitExceededError",
    "RecordInfo",
    "ReadLimits",
    "ResourceRef",
    "SourceRef",
    "UnparsedRange",
    "UnsupportedFormatError",
    "__version__",
    "build_info",
    "inspect",
    "read",
]

from .geometry import (
    Brep,
    BrepEntity,
    GeometryDiagnostic,
    GeometryLimits,
    GeometryResult,
    GeometryStatus,
    IncompleteGeometryError,
    NodeSource,
    RawField,
    RawGeometry,
    RawNode,
    SchemaSelection,
)
from .native import NativeAppearance, NativeEntity, NativePrimitive
from .parts import (
    Part,
    PartDefinition,
    PartIndex,
    PartLimits,
    PartOpaqueRange,
    PartPlacement,
    PartProperty,
    PartReference,
    PartStatus,
)
from .schema import CatalogLimits, SchemaCatalog

__all__ += [
    "NativeAppearance",
    "NativeEntity",
    "NativePrimitive",
    "Part",
    "PartIndex",
    "PartLimits",
    "PartDefinition",
    "PartReference",
    "PartOpaqueRange",
    "PartPlacement",
    "PartProperty",
    "PartStatus",
]
