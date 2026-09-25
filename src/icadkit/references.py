"""Explicit, bounded external-reference resolution with per-call snapshots."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import stat
import struct
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PureWindowsPath
from typing import Literal

from .document import Document, read
from .errors import IcadError, LimitExceededError, UnsupportedFormatError
from .models import Diagnostic, ErrorCategory, ReadLimits
from .native import NativeEntity, NativePrimitive
from .parts import (
    Matrix4,
    Part,
    PartIndex,
    PartLimits,
    _determinant,
    _frame,
    _relative,
    _with_parity,
)

ReferenceStatus = Literal[
    "resolved",
    "missing",
    "ambiguous",
    "unsupported",
    "cycle",
    "limit",
    "invalid",
    "io_error",
]
IDENTITY: Matrix4 = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))


@dataclass(frozen=True)
class AssemblyLimits:
    """Aggregate bounds. Document attempts include the host; depth starts at zero."""

    max_documents: int = 256
    max_total_bytes: int = 1024 * 1024 * 1024
    max_depth: int = 32
    max_occurrences: int = 100_000
    max_entities: int = 1_000_000
    max_search_entries: int = 100_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} must be an integer")
            if not 0 < v <= (1 << 31) - 1:
                raise ValueError(f"{name} must be between 1 and 2**31 - 1")


@dataclass(frozen=True)
class ReferenceRequest:
    """Caller resolver input; source_path is informational, never an implicit root."""

    source_document_id: str
    source_sha256: str
    source_path: Path | None
    part_id: str
    reference_name: str


ReferenceResolver = Callable[
    [ReferenceRequest], str | Path | Sequence[str | Path] | None
]


@dataclass(frozen=True)
class AssemblyReference:
    occurrence_id: str
    request: ReferenceRequest
    status: ReferenceStatus
    candidates: tuple[Path, ...] = ()
    resolved_path: Path | None = None
    target_document_id: str | None = None
    target_sha256: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class AssemblyDocument:
    document_id: str
    path: Path | None
    source_sha256: str
    document: Document = field(repr=False)
    parts: PartIndex = field(repr=False)


@dataclass(frozen=True)
class AssemblyEntity:
    """Occurrence identity and placement, separate from immutable source evidence."""

    entity_id: str
    owner_id: str
    source_document_id: str
    source_entity: NativeEntity
    document_transform: Matrix4
    primitive: NativePrimitive | None


@dataclass(frozen=True)
class AssemblyOccurrence:
    occurrence_id: str
    parent_id: str | None
    source_document_id: str
    source_part: Part
    definition_document_id: str | None
    content_part_id: str | None
    document_transform: Matrix4 | None
    world_transform: Matrix4 | None
    orientation_world_transform: Matrix4 | None
    entities: tuple[AssemblyEntity, ...]
    reference: AssemblyReference | None = None


@dataclass(frozen=True)
class AssemblyIndex:
    """Resolved inventory. status does not certify geometry or whole-model support."""

    source_sha256: str
    assembly_sha256: str
    documents: tuple[AssemblyDocument, ...]
    occurrences: tuple[AssemblyOccurrence, ...]
    references: tuple[AssemblyReference, ...]
    status: Literal["complete", "partial"]
    bytes_read: int
    document_attempts: int
    diagnostics: tuple[Diagnostic, ...]
    model_status: Literal["partial"] = "partial"

    def document(self, document_id: str) -> AssemblyDocument:
        for document in self.documents:
            if document.document_id == document_id:
                return document
        raise KeyError(document_id)

    def to_dict(self) -> dict[str, object]:
        """JSON-compatible inventory; byte locations always retain a source ID."""
        return {
            "source_sha256": self.source_sha256,
            "assembly_sha256": self.assembly_sha256,
            "status": self.status,
            "model_status": self.model_status,
            "bytes_read": self.bytes_read,
            "document_attempts": self.document_attempts,
            "documents": [
                {
                    "document_id": d.document_id,
                    "path": str(d.path) if d.path else None,
                    "source_sha256": d.source_sha256,
                    "file_size": d.document.file_size,
                    "profile_id": d.parts.profile.profile_id
                    if d.parts.profile
                    else None,
                    "part_status": asdict(d.parts.status),
                }
                for d in self.documents
            ],
            "occurrences": [
                {
                    "occurrence_id": o.occurrence_id,
                    "parent_id": o.parent_id,
                    "name": o.source_part.name,
                    "source_document_id": o.source_document_id,
                    "source_part_id": o.source_part.part_id,
                    "definition_document_id": o.definition_document_id,
                    "content_part_id": o.content_part_id,
                    "document_transform": o.document_transform,
                    "world_transform": o.world_transform,
                    "orientation_world_transform": o.orientation_world_transform,
                    "entity_ids": [e.entity_id for e in o.entities],
                }
                for o in self.occurrences
            ],
            "references": [_reference_dict(r) for r in self.references],
            "diagnostics": [asdict(d) for d in self.diagnostics],
        }


def _reference_dict(r: AssemblyReference) -> dict[str, object]:
    value = asdict(r)
    value["request"]["source_path"] = (
        str(r.request.source_path) if r.request.source_path else None
    )
    value["candidates"] = [str(p) for p in r.candidates]
    value["resolved_path"] = str(r.resolved_path) if r.resolved_path else None
    return value


def _compose(a: Matrix4, b: Matrix4) -> Matrix4:
    result = tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4)
    )
    if not all(math.isfinite(v) for row in result for v in row):
        raise _Failure("invalid", "placement", "Assembly placement overflowed")
    return result


def _point(m: Matrix4, p: Sequence[float]) -> tuple[float, ...]:
    return tuple(sum(m[i][j] * p[j] for j in range(3)) + m[i][3] for i in range(3))


class _Failure(Exception):
    def __init__(self, status: ReferenceStatus, code: str, message: str):
        self.status = status
        self.loaded_path: Path | None = None
        self.source_sha256: str | None = None
        category: ErrorCategory = (
            "limit_exceeded"
            if status == "limit"
            else "io"
            if status == "io_error"
            else "invalid"
            if status == "invalid"
            else "unsupported"
        )
        self.diagnostic = Diagnostic(category, "references." + code, None, message)
        super().__init__(message)


def _canonical(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        return path.resolve()
    except RuntimeError as exc:
        raise _Failure("cycle", "symlink_cycle", str(exc)) from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _Failure("cycle", "symlink_cycle", str(exc)) from exc
        raise


def _limit(message: str) -> LimitExceededError:
    return LimitExceededError(
        Diagnostic("limit_exceeded", "references.limit", None, message)
    )


def _qualified(index: PartIndex) -> bool:
    return bool(
        index.profile
        and index.profile.mirror_policy == "stored_parity"
        and index.status.index == "complete"
        and index.status.hierarchy in ("complete", "partial")
        and index.source_length_unit == "mm"
        and index.parts
        and index.parts[0].placement.world_transform is not None
    )


def _external_frame(index: PartIndex, part: Part) -> Matrix4:
    root = next(p for p in index.parts if p.is_root)
    a = _frame(list(struct.unpack("<9d", root.placement.raw_coordinate_bytes)))
    b = _frame(list(struct.unpack("<9d", part.placement.raw_coordinate_bytes)))
    frame = _relative(a, b) if a is not None and b is not None else None
    if frame is None or part.flags not in (0x50, 0x58) or part.entities:
        raise _Failure(
            "unsupported",
            "placement",
            "External occurrence layout or frame is unqualified",
        )
    return _with_parity(frame, -1 if part.is_mirror else 1)


class _Search:
    def __init__(self, roots: Sequence[str | Path], limit: int):
        self.roots = tuple(dict.fromkeys(Path(p).resolve() for p in roots))
        if not all(p.is_dir() for p in self.roots):
            raise NotADirectoryError("search_roots must be existing directories")
        self.remaining = limit
        self.cache: dict[tuple[str, ...], tuple[Path, ...]] = {}

    def __call__(self, request: ReferenceRequest) -> tuple[Path, ...]:
        name = request.reference_name
        path = PureWindowsPath(name)
        if (
            not name
            or not path.name
            or "\0" in name
            or path.drive
            or path.root
            or ":" in name
            or ".." in path.parts
        ):
            raise _Failure(
                "unsupported",
                "reference_name",
                "Use a relative name within explicit roots, or an explicit resolver",
            )
        if path.suffix and path.suffix.casefold() != ".icd":
            raise _Failure(
                "unsupported",
                "reference_name",
                "Only ICD reference names are qualified",
            )
        if not path.suffix:
            path = path.with_suffix(".icd")
        parts = tuple(p.casefold() for p in path.parts)
        if parts in self.cache:
            return self.cache[parts]
        found: set[Path] = set()
        for root in self.roots:
            candidates = [root]
            for component in parts:
                following: list[Path] = []
                for directory in candidates:
                    if not directory.is_dir():
                        continue
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            if self.remaining <= 0:
                                raise _Failure(
                                    "limit",
                                    "search_limit",
                                    "Search exceeded max_search_entries",
                                )
                            self.remaining -= 1
                            if entry.name.casefold() == component:
                                candidate = _canonical(Path(entry.path))
                                if not candidate.is_relative_to(root):
                                    raise _Failure(
                                        "unsupported",
                                        "outside_root",
                                        "Reference symlink escapes "
                                        "an explicit search root",
                                    )
                                following.append(candidate)
                candidates = following
            found.update(p for p in candidates if p.is_file())
        result = tuple(sorted(found))
        self.cache[parts] = result
        return result


def read_assembly(
    source: str | os.PathLike[str] | bytes | Document,
    *,
    search_roots: Sequence[str | Path] = (),
    resolver: ReferenceResolver | None = None,
    limits: AssemblyLimits | None = None,
    read_limits: ReadLimits | None = None,
    part_limits: PartLimits | None = None,
) -> AssemblyIndex:
    """Resolve using exactly one explicit search policy, never host/cwd fallback.

    Resolver paths must be absolute; multiple distinct existing paths are
    ambiguous even when bytes match. Caches last for this invocation only.
    Individual reference failures remain in the tree; aggregate occurrence/entity
    limits raise before allocating an incomplete result. No network client exists.
    """
    if isinstance(search_roots, (str, Path)):
        raise TypeError("search_roots must be a sequence of directories")
    if bool(search_roots) == (resolver is not None):
        raise ValueError("Supply search_roots or resolver, exclusively")
    if resolver is not None and not callable(resolver):
        raise TypeError("resolver must be callable")
    limits = AssemblyLimits() if limits is None else limits
    read_limits = ReadLimits() if read_limits is None else read_limits
    part_limits = PartLimits() if part_limits is None else part_limits
    if (
        not isinstance(limits, AssemblyLimits)
        or not isinstance(read_limits, ReadLimits)
        or not isinstance(part_limits, PartLimits)
    ):
        raise TypeError("limits must use AssemblyLimits, ReadLimits and PartLimits")
    policy = (
        resolver
        if resolver is not None
        else _Search(search_roots, limits.max_search_entries)
    )
    host_path = (
        Path(source).resolve() if isinstance(source, (str, os.PathLike)) else None
    )
    host = (
        source
        if isinstance(source, Document)
        else read(
            source,
            limits=replace(
                read_limits,
                max_file_bytes=min(read_limits.max_file_bytes, limits.max_total_bytes),
            ),
        )
    )
    if host.file_size > limits.max_total_bytes:
        raise _limit("Host exceeds max_total_bytes")
    index = host.read_parts(limits=part_limits)
    if not _qualified(index):
        raise UnsupportedFormatError(
            Diagnostic(
                "unsupported",
                "references.profile",
                None,
                "Assembly requires a complete qualified "
                "V7L6/V7L7/V8L1/V8L2/V8L3 part index and root",
            )
        )
    documents = [
        AssemblyDocument("document:0", host_path, host.source_sha256, host, index)
    ]
    loaded: dict[Path, AssemblyDocument | _Failure] = (
        {host_path: documents[0]} if host_path else {}
    )
    bytes_read = host.file_size
    attempts = 1

    def load(path: Path) -> AssemblyDocument:
        nonlocal bytes_read, attempts
        cached = loaded.get(path)
        if cached is not None:
            if isinstance(cached, _Failure):
                raise cached
            return cached
        source_sha256 = None
        try:
            if attempts >= limits.max_documents:
                raise _Failure("limit", "document_limit", "Exceeded max_documents")
            attempts += 1
            remaining = limits.max_total_bytes - bytes_read
            cap = min(read_limits.max_file_bytes, remaining)
            flags = (
                os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
            )
            with os.fdopen(os.open(path, flags), "rb") as stream:
                size = os.fstat(stream.fileno())
                if not stat.S_ISREG(size.st_mode):
                    raise _Failure(
                        "unsupported", "file_type", "Reference is not a regular file"
                    )
                if size.st_size > cap:
                    raise _Failure(
                        "limit",
                        "byte_limit",
                        "Reference exceeds max_file_bytes or max_total_bytes",
                    )
                data = stream.read(size.st_size + 1)
            bytes_read += len(data)
            if len(data) > cap:
                raise _Failure(
                    "limit", "byte_limit", "Reference grew beyond byte limit"
                )
            if len(data) != size.st_size:
                raise _Failure(
                    "io_error", "changed", "Reference size changed while reading"
                )
            source_sha256 = hashlib.sha256(data).hexdigest()
            doc = read(data, limits=read_limits)
            ix = doc.read_parts(limits=part_limits)
            if not _qualified(ix):
                raise _Failure(
                    "unsupported",
                    "profile",
                    "Referenced part layout, hierarchy or units are unqualified",
                )
            value = AssemblyDocument(
                f"document:{len(documents)}", path, doc.source_sha256, doc, ix
            )
            documents.append(value)
            loaded[path] = value
            return value
        except (IcadError, OSError) as exc:
            failure = _Failure(
                "limit"
                if isinstance(exc, LimitExceededError)
                else "invalid"
                if isinstance(exc, IcadError) and exc.diagnostic.category == "invalid"
                else "missing"
                if isinstance(exc, FileNotFoundError)
                else "io_error"
                if isinstance(exc, OSError)
                else "unsupported",
                "read",
                str(exc),
            )
            failure.source_sha256 = source_sha256
            failure.loaded_path = path if source_sha256 else None
            loaded[path] = failure
            raise failure from exc
        except _Failure as exc:
            exc.source_sha256 = source_sha256
            exc.loaded_path = path if source_sha256 else None
            loaded[path] = exc
            raise

    occurrences: list[AssemblyOccurrence] = []
    refs: list[AssemblyReference] = []
    diagnostics: list[Diagnostic] = []
    entities_count = 0
    root = next(p for p in index.parts if p.is_root)
    # Iterative traversal bounds external recursion independently of saved part depth.
    pending: list[
        tuple[AssemblyDocument, Part, str | None, Matrix4, int, tuple[Path, ...]]
    ] = [(documents[0], root, None, IDENTITY, 0, (host_path,) if host_path else ())]
    while pending:
        doc, part, parent_id, transform, depth, ancestors = pending.pop()
        if len(occurrences) >= limits.max_occurrences:
            raise _limit("Assembly exceeds max_occurrences")
        oid = f"occurrence:{len(occurrences)}"
        content: AssemblyDocument | None = doc
        content_part: Part | None = part
        content_transform: Matrix4 | None = transform
        orientation = world = None
        reference = None
        children = [
            (doc, child, transform, depth, ancestors)
            for child in doc.parts.children(part.part_id)
        ]
        try:
            if part.is_external:
                content = content_part = content_transform = None
                request = ReferenceRequest(
                    doc.document_id,
                    doc.source_sha256,
                    doc.path,
                    part.part_id,
                    part.external_reference.name or ""
                    if part.external_reference
                    else "",
                )
                reference = AssemblyReference(oid, request, "unsupported")
                orientation = _compose(transform, _external_frame(doc.parts, part))
                world = _with_parity(
                    orientation, -1 if _determinant(orientation) < 0 else 1
                )
                if depth >= limits.max_depth:
                    raise _Failure(
                        "limit", "depth_limit", "Reference exceeds max_depth"
                    )
                candidates = policy(request)
                if candidates is None:
                    candidates = ()
                elif isinstance(candidates, (str, Path)):
                    candidates = (candidates,)
                if len(candidates) > limits.max_search_entries:
                    raise _Failure(
                        "limit",
                        "candidate_limit",
                        "Resolver returned too many candidates",
                    )
                paths: set[Path] = set()
                for candidate in candidates:
                    p = Path(candidate)
                    if not p.is_absolute():
                        raise _Failure(
                            "unsupported",
                            "resolver_path",
                            "Resolver paths must be absolute",
                        )
                    paths.add(_canonical(p))
                ordered = tuple(sorted(paths))
                reference = replace(reference, candidates=ordered)
                if not ordered:
                    raise _Failure(
                        "missing", "missing", "No file matches the saved reference name"
                    )
                if len(ordered) != 1:
                    raise _Failure(
                        "ambiguous",
                        "ambiguous",
                        "Multiple distinct paths match the saved reference name",
                    )
                target = ordered[0]
                if target in ancestors:
                    raise _Failure(
                        "cycle",
                        "cycle",
                        "Reference path occurs in the active ancestor chain",
                    )
                target_doc = load(target)
                target_root = next(p for p in target_doc.parts.parts if p.is_root)
                target_frame = _frame(
                    list(
                        struct.unpack("<9d", target_root.placement.raw_coordinate_bytes)
                    )
                )
                assert target_frame is not None  # Qualified by _qualified().
                # Reference records map stored target coordinates. The target
                # reader normalizes by its root, so restore that basis once.
                orientation = _compose(orientation, target_frame)
                world = _with_parity(
                    orientation, -1 if _determinant(orientation) < 0 else 1
                )
                reference = replace(
                    reference,
                    status="resolved",
                    resolved_path=target,
                    target_document_id=target_doc.document_id,
                    target_sha256=target_doc.source_sha256,
                )
                content = target_doc
                content_part = target_root
                content_transform = orientation
                children = [
                    (target_doc, child, orientation, depth + 1, (*ancestors, target))
                    for child in target_doc.parts.children(content_part.part_id)
                ]
            else:
                local = part.placement.orientation_world_transform
                if local is None:
                    raise _Failure(
                        "unsupported", "placement", "Part orientation is unqualified"
                    )
                orientation = _compose(transform, local)
                world = _with_parity(
                    orientation, -1 if _determinant(orientation) < 0 else 1
                )
        except (OSError, _Failure) as exc:
            failure = (
                exc
                if isinstance(exc, _Failure)
                else _Failure("io_error", "resolve", str(exc))
            )
            diagnostics.append(failure.diagnostic)
            if reference is not None:
                reference = replace(
                    reference,
                    status=failure.status,
                    resolved_path=failure.loaded_path,
                    target_sha256=failure.source_sha256,
                    diagnostics=(failure.diagnostic,),
                )
            if part.is_external:
                content = content_part = content_transform = None
                orientation = world = None
                children = []
        if reference:
            refs.append(reference)
        entities: list[AssemblyEntity] = []
        if (
            content is not None
            and content_part is not None
            and content_transform is not None
        ):
            for entity in content_part.entities:
                if entities_count >= limits.max_entities:
                    raise _limit("Assembly exceeds max_entities")
                entities_count += 1
                primitive = entity.primitive
                if primitive:
                    try:
                        primitive = replace(
                            primitive,
                            world_transform=_compose(
                                content_transform, primitive.world_transform
                            ),
                        )
                    except _Failure as exc:
                        primitive = None
                        diagnostics.append(exc.diagnostic)
                entities.append(
                    AssemblyEntity(
                        f"{oid}/{entity.entity_id}",
                        oid,
                        content.document_id,
                        entity,
                        content_transform,
                        primitive,
                    )
                )
        occurrences.append(
            AssemblyOccurrence(
                oid,
                parent_id,
                doc.document_id,
                part,
                content.document_id if content else None,
                content_part.part_id if content_part else None,
                content_transform,
                world,
                orientation,
                tuple(entities),
                reference,
            )
        )
        if len(occurrences) + len(pending) + len(children) > limits.max_occurrences:
            raise _limit("Assembly exceeds max_occurrences")
        pending.extend(
            (d, p, oid, m, n, chain) for d, p, m, n, chain in reversed(children)
        )
    result = AssemblyIndex(
        host.source_sha256,
        "",
        tuple(documents),
        tuple(occurrences),
        tuple(refs),
        "partial"
        if diagnostics or any(r.status != "resolved" for r in refs)
        else "complete",
        bytes_read,
        attempts,
        tuple(diagnostics),
    )
    digest = hashlib.sha256(
        json.dumps(
            result.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return replace(result, assembly_sha256=digest)
