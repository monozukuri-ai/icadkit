"""Command line entry points for backend information and prefix inspection."""

import argparse
import io
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from threading import Event

from . import (
    Diagnostic,
    GeometryLimits,
    IcadError,
    Inspection,
    InspectionLimits,
    LimitExceededError,
    PartLimits,
    ReadLimits,
    SchemaCatalog,
    UnsupportedFormatError,
    __version__,
    build_info,
    inspect,
    read,
)


def _positive_u64(value: str) -> int:
    try:
        integer = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 0 < integer <= (1 << 64) - 1:
        raise argparse.ArgumentTypeError("must be between 1 and 2**64 - 1")
    return integer


def _inspection_json(info: Inspection) -> dict[str, object]:
    return {
        "file_size": info.file_size,
        "bytes_read": info.bytes_read,
        "header": {
            "byte_order": info.header.byte_order,
            "raw_version_hex": info.header.raw_version.hex(),
            "raw_name_hex": info.header.raw_name.hex(),
            "raw_mod_hex": info.header.raw_mod.hex(),
            "name_cp932_candidate": info.header.name_cp932_candidate,
        },
        "leading_records": [asdict(record) for record in info.leading_records],
        "unparsed_ranges": [asdict(item) for item in info.unparsed_ranges],
        "status": asdict(info.status),
        "diagnostics": [asdict(diagnostic) for diagnostic in info.diagnostics],
    }


def _inspect_command(args: argparse.Namespace) -> int:
    diagnostic: Diagnostic
    try:
        result = inspect(
            args.path,
            limits=InspectionLimits(
                max_file_bytes=args.max_file_bytes,
                max_record_bytes=args.max_record_bytes,
            ),
        )
    except IcadError as exc:
        diagnostic = exc.diagnostic
        exit_code = (
            4
            if isinstance(exc, LimitExceededError)
            else 3
            if isinstance(exc, UnsupportedFormatError)
            else 1
        )
    except OSError as exc:
        code = (
            "io.not_found"
            if isinstance(exc, FileNotFoundError)
            else "io.permission_denied"
            if isinstance(exc, PermissionError)
            else "io.read_failed"
        )
        diagnostic = Diagnostic("io", code, None, str(exc))
        exit_code = 1
    else:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "operation": "inspect",
                        "source": args.path,
                        **_inspection_json(result),
                    },
                    sort_keys=True,
                )
            )
        else:
            print(
                f"{args.path}: {result.file_size} bytes; read {result.bytes_read} bytes"
            )
            print(
                f"Byte order: {result.header.byte_order}; "
                f"version bytes: {result.header.raw_version.hex()}"
            )
            print(f"Name (CP932 candidate): {result.header.name_cp932_candidate!r}")
            for record in result.leading_records:
                span = record.byte_range
                print(f"{record.tag}: [{span.start}, {span.end}), {span.length} bytes")
            print("Status: header=complete, container=partial, model=not_checked")
            for item in result.unparsed_ranges:
                print(
                    f"Unparsed: [{item.byte_range.start}, {item.byte_range.end}) "
                    f"({item.reason})"
                )
            for warning in result.diagnostics:
                print(f"Diagnostic: {warning.code} at byte {warning.byte_offset}")
        return 0
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "inspect",
                    "source": args.path,
                    "error": asdict(diagnostic),
                },
                sort_keys=True,
            )
        )
    else:
        print(
            f"icadkit: {diagnostic.code} at byte {diagnostic.byte_offset}: "
            f"{diagnostic.message}",
            file=sys.stderr,
        )
    return exit_code


def _write_exclusive(output: str, payload: bytes) -> None:
    """Publish a complete file using an atomic, non-overwriting hard link."""
    destination = Path(output)
    fd, temporary = tempfile.mkstemp(prefix=".icadkit-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)


def _resource_command(args: argparse.Namespace) -> int:
    result: dict[str, object] = {
        "schema_version": 1,
        "operation": args.command,
        "source": args.path,
    }
    exit_code = 0
    try:
        doc = read(
            args.path,
            limits=ReadLimits(
                max_file_bytes=args.max_file_bytes,
                max_record_bytes=args.max_record_bytes,
                max_resource_bytes=args.max_resource_bytes,
                max_records=args.max_records,
                max_resources=args.max_resources,
            ),
        )
        if args.command == "resources":
            result.update(
                {
                    "source_sha256": doc.source_sha256,
                    "file_size": doc.file_size,
                    "resource_index_status": doc.resource_index_status,
                    "resources": [asdict(r) for r in doc.resources],
                    "records": [asdict(r) for r in doc.records],
                    "unparsed_ranges": [asdict(r) for r in doc.unparsed_ranges],
                    "diagnostics": [asdict(d) for d in doc.diagnostics],
                    "status": asdict(doc.status),
                }
            )
            if doc.resource_index_status == "partial":
                exit_code = (
                    1 if any(d.category == "invalid" for d in doc.diagnostics) else 3
                )
        else:
            extracted = doc.extract(args.resource_id)
            _write_exclusive(args.output, extracted.payload)
            result.update(
                {
                    "output": args.output,
                    "provenance": asdict(extracted.source),
                    "status": asdict(extracted.status),
                }
            )
    except IcadError as exc:
        result["error"] = asdict(exc.diagnostic)
        exit_code = (
            4
            if isinstance(exc, LimitExceededError)
            else 3
            if isinstance(exc, UnsupportedFormatError)
            else 1
        )
    except OSError as exc:
        result["error"] = asdict(
            Diagnostic(
                "io",
                "io.already_exists"
                if isinstance(exc, FileExistsError)
                else "io.failed",
                None,
                str(exc),
            )
        )
        exit_code = 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif "error" in result:
        print(f"icadkit: {result['error']}", file=sys.stderr)
    elif args.command == "resources":
        print(
            f"{args.path}: {len(doc.resources)} resources; "
            f"index={doc.resource_index_status}"
        )
        for resource in doc.resources:
            print(
                f"{resource.resource_id}  {resource.encoding}  "
                f"{resource.declared_decoded_bytes} decoded bytes  "
                f"owner=[{resource.owner_range.start}, {resource.owner_range.end})"
            )
        print("Status: container=partial, extraction=not_checked, model=not_checked")
        for diagnostic in doc.diagnostics:
            print(f"Diagnostic: {diagnostic.code} at byte {diagnostic.byte_offset}")
    else:
        print(f"Wrote {len(extracted.payload)} bytes to {args.output}")
        print(f"SHA-256: {extracted.source.payload_sha256}")
        print(
            "Status: extraction=complete, raw_geometry=not_checked, model=not_checked"
        )
    return exit_code


def _parts_command(args: argparse.Namespace) -> int:
    result: dict[str, object] = {
        "schema_version": 1,
        "operation": "parts",
        "source": args.path,
    }
    code = 3
    try:
        doc = read(
            args.path,
            limits=ReadLimits(
                **{
                    name: getattr(args, name)
                    for name in ReadLimits.__dataclass_fields__
                }
            ),
        )
        parts = doc.read_parts(
            limits=PartLimits(
                **{
                    name: getattr(args, name)
                    for name in PartLimits.__dataclass_fields__
                }
            )
        )
        result.update(
            {
                "source_sha256": parts.source_sha256,
                "status": asdict(parts.status),
                "part_count": sum(not p.is_root for p in parts.parts),
                "source_length_unit": parts.source_length_unit,
                "length_unit_source": parts.length_unit_source,
                "definition_count": len(parts.definitions),
                "definitions": [
                    {
                        "definition_id": d.definition_id,
                        "kind": d.kind,
                        "occurrence_ids": d.occurrence_ids,
                        "status": d.status,
                        "reference_name": d.reference.name if d.reference else None,
                    }
                    for d in parts.definitions
                ],
                "parts": parts.to_rows(include_root=args.include_root),
                "opaque_ranges": [asdict(r) for r in parts.opaque_ranges],
                "diagnostics": [asdict(d) for d in parts.diagnostics],
            }
        )
        if any(d.category == "invalid" for d in parts.diagnostics):
            code = 1
        elif (
            parts.status.index
            == parts.status.hierarchy
            == parts.status.text
            == parts.status.placements
            == parts.status.definitions
            == parts.status.units
            == parts.status.references
            == parts.status.stored_attributes
            == "complete"
        ):
            code = 0
        if args.require_native:
            if parts.status.native_geometry == "invalid":
                code = 1
            elif code == 0 and (
                parts.status.native_geometry != "complete"
                or parts.status.appearance != "complete"
            ):
                code = 3
    except IcadError as exc:
        result["error"] = asdict(exc.diagnostic)
        code = (
            4
            if isinstance(exc, LimitExceededError)
            else (3 if isinstance(exc, UnsupportedFormatError) else 1)
        )
    except OSError as exc:
        result["error"] = asdict(Diagnostic("io", "io.failed", None, str(exc)))
        code = 1
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.json else 2,
            allow_nan=False,
        )
    )
    return code


def _check_command(args: argparse.Namespace) -> int:
    result: dict[str, object] = {
        "schema_version": 1,
        "operation": "check",
        "source": args.path,
        "target": args.target,
        "scope": args.scope,
    }
    code = 3
    try:
        doc = read(
            args.path,
            limits=ReadLimits(
                **{
                    name: getattr(args, name)
                    for name in ReadLimits.__dataclass_fields__
                }
            ),
        )
        result["source_sha256"] = doc.source_sha256
        if args.target == "geometry":
            catalog = (
                SchemaCatalog.from_file(
                    args.schema,
                    expected_id=args.schema_id,
                    expected_sha256=args.schema_sha256,
                )
                if args.schema
                else None
            )
            geometry = doc.read_geometry(
                args.resource,
                schema=catalog,
                limits=GeometryLimits(
                    **{
                        name: getattr(args, name)
                        for name in GeometryLimits.__dataclass_fields__
                    }
                ),
            )
            result.update(
                {
                    "resource_id": geometry.resource_id,
                    "status": asdict(geometry.status),
                    "provenance": asdict(geometry.source) if geometry.source else None,
                    "schema": asdict(geometry.schema) if geometry.schema else None,
                    "diagnostics": [asdict(d) for d in geometry.diagnostics],
                    "node_count": geometry.raw.node_count if geometry.raw else None,
                    "brep_counts": dict(geometry.brep.counts)
                    if geometry.brep
                    else None,
                }
            )
            status = getattr(geometry.status, args.scope)
            code = (
                0
                if status == "complete"
                else 1
                if any(d.category == "invalid" for d in geometry.diagnostics)
                else 3
            )
        else:
            result["status"] = asdict(doc.status)
            result["diagnostics"] = [asdict(d) for d in doc.diagnostics]
            if args.target == "model":
                result["diagnostics"] = [
                    asdict(
                        Diagnostic(
                            "unsupported",
                            "model.not_implemented",
                            None,
                            "whole ICD model reconstruction is not implemented",
                        )
                    )
                ]
            elif any(d.category == "invalid" for d in doc.diagnostics):
                code = 1
    except IcadError as exc:
        result["error"] = asdict(exc.diagnostic)
        code = (
            4
            if isinstance(exc, LimitExceededError)
            else (3 if isinstance(exc, UnsupportedFormatError) else 1)
        )
    except OSError as exc:
        result["error"] = asdict(Diagnostic("io", "io.failed", None, str(exc)))
        code = 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


def _preview_command(args: argparse.Namespace) -> int:
    from .preview import PreviewLimits, serve_preview, write_preview

    result: dict[str, object] = {
        "schema_version": 1,
        "operation": "preview",
        "source": args.path,
    }
    code = 0
    try:
        doc = read(
            args.path,
            limits=ReadLimits(
                **{
                    name: getattr(args, name)
                    for name in ReadLimits.__dataclass_fields__
                }
            ),
        )
        catalog = (
            SchemaCatalog.from_file(
                args.schema,
                expected_id=args.schema_id,
                expected_sha256=args.schema_sha256,
            )
            if args.schema
            else None
        )
        preview = write_preview(
            doc,
            args.resource,
            args.output,
            source_unit=args.source_unit,
            schema=catalog,
            geometry_limits=GeometryLimits(
                **{
                    name: getattr(args, name)
                    for name in GeometryLimits.__dataclass_fields__
                }
            ),
            limits=PreviewLimits(
                max_triangles=args.max_triangles,
                max_output_bytes=args.max_output_bytes,
            ),
        )
        result.update(asdict(preview))
        result["directory"] = str(preview.directory)
        result.update(scope="resource_local", output_unit="m", placement="not_applied")
        if not args.write_only:
            with serve_preview(preview.directory, port=args.port) as server:
                print(f"Preview: {server.url}", flush=True)
                print(
                    "Resource coordinates; assembly placement unknown. "
                    "Press Ctrl-C to stop.",
                    flush=True,
                )
                if not args.no_open and not server.open_browser():
                    print("Open the URL above in a browser.", flush=True)
                try:
                    Event().wait()
                except KeyboardInterrupt:
                    pass
    except IcadError as exc:
        result["error"] = asdict(exc.diagnostic)
        code = {"limit_exceeded": 4, "unsupported": 3}.get(exc.diagnostic.category, 1)
    except OSError as exc:
        result["error"] = asdict(Diagnostic("io", "io.failed", None, str(exc)))
        code = 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif "error" in result:
        print(f"icadkit: {result['error']}", file=sys.stderr)
    else:
        print(f"Wrote {preview.glb_path} ({preview.triangle_count} triangles)")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    # Keep Japanese paths/names usable when stdout is redirected on a system
    # whose locale encoding cannot represent them. In-memory capture streams
    # have no encoding to configure.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(
        prog="icadkit",
        description="Inspect iCAD SX records and extract indexed resources",
    )
    parser.add_argument("--version", action="version", version=f"icadkit {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    info = commands.add_parser("info", help="show the compiled backend identity")
    info.add_argument(
        "--json", action="store_true", help="emit a versioned JSON object"
    )
    inspection = commands.add_parser(
        "inspect", help="inspect MOD/DRW/RES framing and raw header fields"
    )
    inspection.add_argument("path", help="path to an ICD file")
    inspection.add_argument(
        "--json", action="store_true", help="emit a versioned JSON result or error"
    )
    defaults = InspectionLimits()
    inspection.add_argument(
        "--max-file-bytes", type=_positive_u64, default=defaults.max_file_bytes
    )
    inspection.add_argument(
        "--max-record-bytes", type=_positive_u64, default=defaults.max_record_bytes
    )
    for command in ("resources", "extract"):
        subparser = commands.add_parser(
            command,
            help=(
                "list structurally indexed resources without decompression"
                if command == "resources"
                else "extract one resource to a new file, with verification"
            ),
        )
        subparser.add_argument("path", help="path to an ICD file")
        subparser.add_argument("--json", action="store_true")
        read_defaults = ReadLimits()
        for name in read_defaults.__dataclass_fields__:
            subparser.add_argument(
                "--" + name.replace("_", "-"),
                type=_positive_u64,
                default=getattr(read_defaults, name),
            )
        if command == "extract":
            subparser.add_argument("resource_id", help="ID from the resources command")
            subparser.add_argument(
                "--output", required=True, help="new output path; never overwritten"
            )
    parts = commands.add_parser(
        "parts", help="read native part hierarchy and stored text"
    )
    parts.add_argument("path")
    parts.add_argument("--json", action="store_true")
    parts.add_argument("--include-root", action="store_true")
    parts.add_argument(
        "--require-native",
        action="store_true",
        help="also require complete native primitive parameters and stored appearance",
    )
    for part_defaults in (ReadLimits(), PartLimits()):
        for name in part_defaults.__dataclass_fields__:
            parts.add_argument(
                "--" + name.replace("_", "-"),
                type=_positive_u64,
                default=getattr(part_defaults, name),
            )
    preview = commands.add_parser(
        "preview", help="view one resource and write a metre-based GLB (preview extra)"
    )
    preview.add_argument("path")
    preview.add_argument("--resource", required=True)
    preview.add_argument(
        "--source-unit", required=True, choices=("mm", "cm", "m", "in")
    )
    preview.add_argument("--output", required=True, help="new viewer directory")
    preview.add_argument(
        "--write-only", action="store_true", help="write without serving"
    )
    preview.add_argument("--no-open", action="store_true", help="do not open a browser")
    preview.add_argument(
        "--port", type=int, default=0, help="loopback port (0: automatic)"
    )
    preview.add_argument("--json", action="store_true", help="requires --write-only")
    preview.add_argument("--schema")
    preview.add_argument("--schema-id")
    preview.add_argument("--schema-sha256")
    preview.add_argument("--max-triangles", type=_positive_u64, default=1_000_000)
    preview.add_argument(
        "--max-output-bytes", type=_positive_u64, default=128 * 1024 * 1024
    )
    for preview_defaults in (ReadLimits(), GeometryLimits()):
        for name in preview_defaults.__dataclass_fields__:
            preview.add_argument(
                "--" + name.replace("_", "-"),
                type=_positive_u64,
                default=getattr(preview_defaults, name),
            )
    check = commands.add_parser("check", help="check one explicit completeness target")
    check.add_argument("path")
    check.add_argument(
        "--target", choices=("container", "geometry", "model"), required=True
    )
    check.add_argument("--resource", help="resource ID required for geometry")
    check.add_argument(
        "--scope",
        choices=("extraction", "raw_geometry", "brep", "topology"),
        default="brep",
    )
    check.add_argument("--schema", help="explicit schema catalog path")
    check.add_argument("--schema-id", help="required expected catalog ID")
    check.add_argument("--schema-sha256", help="expected catalog SHA-256")
    check.add_argument("--json", action="store_true")
    for check_defaults in (ReadLimits(), GeometryLimits()):
        for name in check_defaults.__dataclass_fields__:
            check.add_argument(
                "--" + name.replace("_", "-"),
                type=_positive_u64,
                default=getattr(check_defaults, name),
            )
    args = parser.parse_args(argv)
    if args.command == "parts":
        for name in PartLimits.__dataclass_fields__:
            if getattr(args, name) > (1 << 31) - 1:
                parser.error(f"--{name.replace('_', '-')} must be at most 2**31 - 1")
        return _parts_command(args)
    if args.command in ("check", "preview"):
        if args.command == "check" and args.target == "geometry" and not args.resource:
            parser.error("check --target geometry requires --resource")
        if bool(args.schema) != bool(args.schema_id):
            parser.error("--schema and --schema-id must be supplied together")
        if args.schema_sha256 and not args.schema:
            parser.error("--schema-sha256 requires --schema and --schema-id")
        if args.schema_id and (
            not args.schema_id.isascii() or not args.schema_id.isdigit()
        ):
            parser.error("--schema-id must be an ASCII numeric ID")
        if args.schema_sha256 and (
            len(args.schema_sha256) != 64
            or any(c not in "0123456789abcdefABCDEF" for c in args.schema_sha256)
        ):
            parser.error("--schema-sha256 must contain 64 hexadecimal digits")
        if (
            args.command == "check"
            and args.target != "geometry"
            and (args.resource or args.schema)
        ):
            parser.error("--resource and --schema require --target geometry")
        for name in GeometryLimits.__dataclass_fields__:
            if getattr(args, name) > (1 << 63) - 1:
                parser.error(f"--{name.replace('_', '-')} must be at most 2**63 - 1")
        if args.command == "preview":
            if not 0 <= args.port <= 65535:
                parser.error("--port must be between 0 and 65535")
            if args.json and not args.write_only:
                parser.error("preview --json requires --write-only")
            return _preview_command(args)
        return _check_command(args)
    if args.command == "inspect":
        return _inspect_command(args)
    if args.command in ("resources", "extract"):
        return _resource_command(args)
    metadata = asdict(build_info())
    if args.json:
        print(json.dumps({"schema_version": 1, **metadata}, sort_keys=True))
    else:
        print(
            f"icadkit {metadata['version']} (Rust core {metadata['rust_core_version']})"
        )
        print(f"parasolid-core {metadata['parasolid_core_version']}")
        print("Compiled profiles: " + ", ".join(metadata["builtin_profile_ids"]))
        print(
            "Resource-level raw geometry and B-Rep; "
            "whole-model reconstruction is not implemented."
        )
    return 0
