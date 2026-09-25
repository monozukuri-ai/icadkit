"""Bounded iCAD SX inspection, structural resource indexing and extraction."""

from importlib.metadata import version

from .api import build_info, inspect
from .csg import (
    CsgBody,
    CsgIndex,
    CsgLimits,
    CsgMesh,
    CsgOperand,
    evaluate_csg,
    read_csg,
)
from .document import Document, read
from .drawing import (
    DrawingEntity,
    DrawingIndex,
    DrawingLimits,
    DrawingPrimitive,
    DrawingText,
)
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
from .views import View, ViewEntry, ViewIndex, ViewLimits

__version__ = version("icadkit")
__all__ = [
    "DrawingEntity",
    "DrawingIndex",
    "DrawingLimits",
    "DrawingPrimitive",
    "DrawingText",
    "View",
    "ViewEntry",
    "ViewIndex",
    "ViewLimits",
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
from .parameters import (
    ParameterCondition,
    ParameterIndex,
    ParameterLimits,
    ParameterTable,
    SavedParameter,
)
from .parts import (
    Part,
    PartDefinition,
    PartIndex,
    PartLimits,
    PartOpaqueAttribute,
    PartOpaqueRange,
    PartPlacement,
    PartProfile,
    PartProperty,
    PartReference,
    PartStatus,
    PartView,
)
from .references import (
    AssemblyDocument,
    AssemblyEntity,
    AssemblyIndex,
    AssemblyLimits,
    AssemblyOccurrence,
    AssemblyReference,
    ReferenceRequest,
    ReferenceResolver,
    read_assembly,
)
from .saved import (
    SavedBody,
    SavedBodyIndex,
    SavedBodyLimits,
    SavedBodyMesh,
    evaluate_saved_body,
    read_saved_bodies,
)
from .schema import CatalogLimits, SchemaCatalog

__all__ += [
    "ParameterCondition",
    "ParameterIndex",
    "ParameterLimits",
    "ParameterTable",
    "SavedParameter",
]

__all__ += [
    "NativeAppearance",
    "NativeEntity",
    "NativePrimitive",
    "Part",
    "PartIndex",
    "PartLimits",
    "PartOpaqueAttribute",
    "PartDefinition",
    "PartReference",
    "PartOpaqueRange",
    "PartPlacement",
    "PartProfile",
    "PartProperty",
    "PartStatus",
    "PartView",
]


__all__ += [
    "CsgBody",
    "CsgIndex",
    "CsgLimits",
    "CsgMesh",
    "CsgOperand",
    "evaluate_csg",
    "read_csg",
]

__all__ += [
    "SavedBody",
    "SavedBodyIndex",
    "SavedBodyLimits",
    "SavedBodyMesh",
    "read_saved_bodies",
    "evaluate_saved_body",
]

__all__ += [
    "AssemblyDocument",
    "AssemblyEntity",
    "AssemblyIndex",
    "AssemblyLimits",
    "AssemblyOccurrence",
    "AssemblyReference",
    "ReferenceRequest",
    "ReferenceResolver",
    "read_assembly",
]
