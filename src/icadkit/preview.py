"""Optional, resource-local preview and metre-based GLB output.

The caller declares the source unit. No part transform, saved appearance or
assembly completeness is inferred. Optional imports occur only on use.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import shutil
import tempfile
from dataclasses import asdict, dataclass, fields
from importlib import import_module, metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from .errors import IcadError
from .models import Diagnostic, ErrorCategory

if TYPE_CHECKING:
    from .document import Document
    from .geometry import GeometryLimits
    from .schema import SchemaCatalog

LengthUnit = Literal["mm", "cm", "m", "in"]


class PreviewError(IcadError):
    """A dependency, conversion, tessellation or output limit failure."""


@dataclass(frozen=True)
class PreviewOptions:
    """Deflection is in output metres; angular deflection is in radians."""

    linear_deflection: float = 0.0001
    angular_deflection: float = 0.5
    include_edges: bool = True

    def __post_init__(self) -> None:
        for name in ("linear_deflection", "angular_deflection"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a positive finite number")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        if self.angular_deflection > math.pi:
            raise ValueError("angular_deflection must not exceed pi")
        if not isinstance(self.include_edges, bool):
            raise TypeError("include_edges must be a boolean")


@dataclass(frozen=True)
class PreviewLimits:
    """Conversion/mesh ceilings, independent of parsing limits."""

    max_entities: int = 100_000
    max_occt_subshapes: int = 200_000
    max_curve_samples: int = 1_000_000
    max_triangles: int = 1_000_000
    max_vertices: int = 2_000_000
    max_output_bytes: int = 128 * 1024 * 1024
    max_diagnostics: int = 1000

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field.name} must be a positive integer")


@dataclass(frozen=True)
class PreviewResult:
    directory: Path
    resource_id: str
    source_sha256: str
    payload_sha256: str
    source_unit: LengthUnit
    triangle_count: int
    face_count: int
    edge_count: int
    output_bytes: int
    glb_sha256: str

    @property
    def index_path(self) -> Path:
        return self.directory / "index.html"

    @property
    def glb_path(self) -> Path:
        return self.directory / "preview.glb"

    @property
    def manifest_path(self) -> Path:
        return self.directory / "preview.manifest.json"


def _error(
    code: str, message: str, category: ErrorCategory = "unsupported"
) -> PreviewError:
    return PreviewError(Diagnostic(category, code, None, message))


def _backend() -> Any:
    try:
        version = metadata.version("parasolid-kit")
    except metadata.PackageNotFoundError as exc:
        raise _error(
            "preview.missing_dependency",
            'Install "icadkit[preview]" to create previews',
        ) from exc
    if version != "0.2.0":
        raise _error(
            "preview.backend_version",
            "Preview requires the qualified parasolid-kit 0.2.0 adapter",
        )
    return import_module("parasolid_kit.interop.preview")


def _backend_error(exc: Any) -> PreviewError:
    d = exc.diagnostic
    category: ErrorCategory = "unsupported"
    if d.kind.value == "invalid":
        category = "invalid"
    elif d.kind.value == "limit":
        category = "limit_exceeded"
    return _error(d.code, d.message, category)


def _artifact(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "filename": path.name,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _adapt_viewer(staged: Path) -> None:
    # The hash-verified 0.2.0 bundle clamps the camera near plane to 0.1 units,
    # cutting through centimetre-sized models exported in metres. Preserve its
    # radius-based near/far ratio, lowering only the absolute floor to 1 nm.
    # Guard the exact pinned implementation rather than patch arbitrary JS.
    path = staged / "viewer.js"
    script = path.read_text(encoding="utf-8")
    original = "_computeNear(e){return Math.max(.1,s.NEAR_FACTOR*e)}"
    replacement = "_computeNear(e){return Math.max(1e-9,s.NEAR_FACTOR*e)}"
    if script.count(original) != 1:
        raise _error(
            "preview.asset_contract", "Unexpected camera clipping implementation"
        )
    script = script.replace(original, replacement)
    script += "\n// icadkit adaptation: metre-scale camera near-plane floor (1 nm).\n"
    path.write_text(script, encoding="utf-8")


def _publish(staged: Path, output: Path) -> None:
    # Reserve the destination exclusively. Never overwrite even an empty directory.
    output.mkdir()
    created = []
    try:
        for source in sorted(staged.iterdir()):
            target = output / source.name
            with target.open("xb") as stream:
                created.append(target)
                with source.open("rb") as content:
                    shutil.copyfileobj(content, stream)
    except BaseException:
        for target in created:
            target.unlink(missing_ok=True)
        try:
            output.rmdir()
        except OSError:
            pass  # Preserve anything another process added.
        raise


def write_preview(
    document: Document,
    resource_id: str,
    destination: str | Path,
    *,
    source_unit: LengthUnit,
    schema: SchemaCatalog | None = None,
    geometry_limits: GeometryLimits | None = None,
    options: PreviewOptions | None = None,
    limits: PreviewLimits | None = None,
) -> PreviewResult:
    """Write a viewer directory and GLB for one complete supported resource.

    Existing destinations are rejected. The source unit is caller supplied,
    not detected. The GLB uses metres while retaining the resource's axes and
    origin. No CAD application, catalog discovery or network access is used.
    """
    from .document import Document

    if not isinstance(document, Document):
        raise TypeError("document must be an icadkit.Document")
    if source_unit not in ("mm", "cm", "m", "in"):
        raise ValueError("source_unit must be mm, cm, m or in")
    if not isinstance(destination, (str, Path)):
        raise TypeError("destination must be a path")
    output = Path(destination)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Preview destination already exists: {output}")
    options = PreviewOptions() if options is None else options
    limits = PreviewLimits() if limits is None else limits
    if not isinstance(options, PreviewOptions) or not isinstance(limits, PreviewLimits):
        raise TypeError("options and limits must be PreviewOptions and PreviewLimits")
    geometry = document.read_geometry(
        resource_id, schema=schema, limits=geometry_limits
    )
    geometry.require_complete("brep").require_complete("topology")
    brep = geometry.brep
    source = geometry.source
    assert brep is not None and source is not None
    if geometry.diagnostics:
        raise _error(
            "preview.geometry_diagnostics",
            "Resource diagnostics must be resolved before preview",
        )
    if sum(brep.counts.values()) > limits.max_entities:
        raise _error(
            "preview.limit_exceeded", "B-Rep exceeds max_entities", "limit_exceeded"
        )
    backend = _backend()
    from ._preview_bridge import bridge

    errors = import_module("parasolid_kit.interop.errors")
    converter = import_module("parasolid_kit.interop.occt")
    limit_type = import_module("parasolid_kit.interop.limits").InteropLimits
    bounded = limit_type(**asdict(limits))
    model = bridge(brep)
    context = {
        "schema_version": 1,
        "scope": "resource_local",
        "source": asdict(source),
        "schema": asdict(geometry.schema) if geometry.schema else None,
        "status": asdict(geometry.status),
        "units": {
            "source": source_unit,
            "source_basis": "caller_supplied",
            "output": "m",
        },
        "placement": "not_applied",
        "appearance": "illustrative",
        "resource_count": len(document.resources),
        "resource_index_status": document.resource_index_status,
        "byte_range_domains": {
            "container_range": "ICD file bytes",
            "decoded_range_and_nodes": "decoded resource bytes",
        },
    }
    try:
        converted = converter.to_occt(
            model,
            source_unit=source_unit,
            target_unit="m",
            limits=bounded,
            source_identity="sha256:" + source.payload_sha256,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".icadkit-preview-", dir=output.parent
        ) as temporary:
            staged = Path(temporary) / "preview"
            result = backend.write_preview(
                converted,
                model,
                staged,
                options=backend.PreviewOptions(**asdict(options)),
                limits=bounded,
            )
            _adapt_viewer(staged)
            glb = result.glb_path.read_bytes()
            if not backend.validate_glb_bytes(glb).valid:
                raise _error("preview.invalid_glb", "GLB failed validation", "invalid")
            page = result.index_path.read_text(encoding="utf-8")
            page = page.replace(
                "<title>Parasolid · B-Rep viewer</title>",
                "<title>icadkit · Resource preview</title>",
            )
            page = page.replace("PARASOLID KIT", "ICADKIT").replace(
                "<h1>B-Rep viewer</h1>", "<h1>Resource preview</h1>"
            )
            banner = (
                '<p id="icadkit-scope" class="icadkit-scope">'
                f"Resource <strong>{html.escape(resource_id)}</strong> · "
                f"Source unit: {source_unit} (caller supplied) · Output: m · "
                "Resource coordinates; assembly placement unknown. "
                "Colors are illustrative.</p>"
            )
            page = page.replace(
                '  <main class="workspace">',
                banner + '\n  <main class="workspace">',
            )
            result.index_path.write_text(page, encoding="utf-8")
            with (staged / "viewer.css").open("a", encoding="utf-8") as css:
                css.write(
                    "\n.icadkit-scope{margin:0;padding:8px 16px;background:#fff0ca;"
                    "color:#47370a;font:13px/1.4 system-ui;}\n"
                )
            manifest = json.loads(result.manifest_path.read_bytes())
            manifest["icadkit"] = context
            manifest["conversion_report"] = converted.report.to_dict()
            manifest["glb"] = _artifact(result.glb_path)
            manifest["asset_bundle"]["adaptation"] = (
                "icadkit resource scope/unit notice and metre-scale camera clipping"
            )
            manifest["asset_bundle"]["assets"] = [
                _artifact(staged / name) for name in backend.STATIC_ASSET_NAMES
            ]
            result.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=True, indent=2, allow_nan=False)
                + "\n",
                encoding="utf-8",
            )
            size = sum(p.stat().st_size for p in staged.iterdir())
            if size > limits.max_output_bytes:
                raise _error(
                    "preview.limit_exceeded",
                    "Preview exceeds max_output_bytes",
                    "limit_exceeded",
                )
            _publish(staged, output)
            return PreviewResult(
                output,
                resource_id,
                document.source_sha256,
                source.payload_sha256,
                source_unit,
                result.report.triangle_count,
                result.report.face_primitive_count,
                result.report.edge_primitive_count,
                size,
                hashlib.sha256(glb).hexdigest(),
            )
    except errors.InteropError as exc:
        raise _backend_error(exc) from exc


class PreviewServer:
    """Loopback-only viewer server; entering the context starts it."""

    def __init__(self, server: Any) -> None:
        self._server = server

    @property
    def url(self) -> str:
        return cast(str, self._server.url)

    def start(self) -> None:
        self._server.start()

    def open_browser(self) -> bool:
        return bool(self._server.open_browser())

    def close(self) -> None:
        self._server.close()

    def __enter__(self) -> PreviewServer:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def serve_preview(directory: str | Path, *, port: int = 0) -> PreviewServer:
    """Prepare a local server for a generated preview; no browser is opened."""
    backend = _backend()
    errors = import_module("parasolid_kit.interop.errors")
    try:
        return PreviewServer(backend.create_preview_server(directory, port=port))
    except errors.InteropError as exc:
        raise _backend_error(exc) from exc
