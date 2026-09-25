"""Offline, hash-pinned native-reader regression and saved SDK observation checks.

Manifests and CAD/SDK inputs are caller-owned, never discovered or distributed.
Profile support and SDK comparability are reported separately from regression.
"""

import argparse
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import icadkit


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    def encode(obj):
        if isinstance(obj, bytes):
            return {"bytes_hex": obj.hex()}
        raise TypeError(type(obj).__name__)

    return json.dumps(
        value, default=encode, sort_keys=True, ensure_ascii=True, allow_nan=False
    ).encode()


def snapshot(doc, index):
    """Pre-profile public contract, including source values and all diagnostics."""
    value = {
        "header": asdict(doc.header),
        "document_status": asdict(doc.status),
        "resource_index": doc.resource_index_status,
        "resources": [asdict(r) for r in doc.resources],
        "document_diagnostics": [asdict(d) for d in doc.diagnostics],
        "parts": [asdict(p) for p in index.parts],
        "definitions": [asdict(d) for d in index.definitions],
        "parts_status": asdict(index.status),
        "diagnostics": [asdict(d) for d in index.diagnostics],
        "opaque_ranges": [asdict(r) for r in index.opaque_ranges],
        "source_length_unit": index.source_length_unit,
        "length_unit_source": index.length_unit_source,
        "rows": index.to_rows(include_root=True),
    }
    return {
        "snapshot_sha256": hashlib.sha256(canonical(value)).hexdigest(),
        "raw_version": doc.header.raw_version.hex(),
        "byte_order": doc.header.byte_order,
        "parts_status": asdict(index.status),
        "part_count_without_root": sum(not p.is_root for p in index.parts),
        "resources": len(doc.resources),
        "diagnostics": sorted({d.code for d in index.diagnostics}),
    }


def resolve(spec, roots):
    relative = Path(spec["path"])
    require(
        not relative.is_absolute() and ".." not in relative.parts,
        "unsafe manifest path",
    )
    require(spec["root"] in roots, f"missing root: {spec['root']}")
    root = Path(roots[spec["root"]]).resolve()
    path = (root / relative).resolve()
    require(path.is_relative_to(root), "manifest path escapes supplied root")
    return path


def checked_file(spec, roots, checked):
    path = resolve(spec, roots)
    require(path.is_file(), f"required input missing: {path}")
    if path not in checked:
        checked[path] = digest(path)
    require(checked[path] == spec["sha256"], f"changed input: {path}")
    return path


def validate_manifest(manifest):
    require(manifest.get("schema_version") == 1, "unsupported manifest schema")
    rows = manifest["files"]
    require(bool(rows), "empty manifest")
    require(len({r["id"] for r in rows}) == len(rows), "duplicate corpus ID")
    families = {}
    payloads = {}
    for row in rows:
        split = row["partition"]
        require(split in ("development", "validation"), "unknown partition")
        family = row["family_id"]
        require(bool(family), "empty family")
        require(families.setdefault(family, split) == split, "family split leakage")
        require(
            payloads.setdefault(row["input"]["sha256"], family) == family,
            "identical input belongs to different families",
        )
        if "oracle" in row:
            require(
                row["oracle"]["scope"] in ("saved_document", "library_part_insertion"),
                "unknown SDK observation scope",
            )
        if "parameter_oracle" in row:
            require(
                row["parameter_oracle"]["scope"] == "root_saved_parameters",
                "unknown parameter observation scope",
            )
        for file in [row["input"], *row.get("related_files", [])]:
            if file["path"].lower().endswith(".icd"):
                require(
                    payloads.setdefault(file["sha256"], family) == family,
                    "shared CAD dependency belongs to different families",
                )
    return rows


def sdk_inventory(oracle):
    """Host inventory only: resolved external descendants belong to another file."""
    result = []
    tree = oracle.get("tree")
    if not isinstance(tree, dict) or "info" not in tree:
        return None

    def visit(node, parent):
        info = node["info"]
        entry = {
            "name": info["name"],
            "mirror": info["is_mirror"],
        }
        key = (*parent, json.dumps(entry, sort_keys=True))
        result.append((key, info))
        if not info["is_external"] and not info.get("is_unloaded", False):
            for child in node["children"]:
                visit(child, key)

    # Root name is a document name, not a saved child part name.
    for child in tree["children"]:
        visit(child, ())
    return result


def compare_sdk(index, oracle, scope):
    if scope == "library_part_insertion":
        return {"status": "not_comparable", "reason": "insertion is not raw input"}
    expected = sdk_inventory(oracle)
    if expected is None:
        return {"status": "unverified", "reason": "SDK inventory unavailable"}
    if index.status.index != "complete" or index.status.hierarchy == "invalid":
        return {"status": "unsupported", "reason": "native inventory incomplete"}
    rows = []
    keys = {}
    for p in index.walk():
        if p.is_root:
            keys[p.part_id] = ()
            continue
        entry = {
            "name": p.name,
            "mirror": p.is_mirror,
        }
        if p.parent_id not in keys:
            return {"status": "unverified", "reason": "parent inventory unavailable"}
        key = (*keys[p.parent_id], json.dumps(entry, sort_keys=True))
        keys[p.part_id] = key
        rows.append((key, p))
    expected_counts = Counter(key for key, _ in expected)
    actual_counts = Counter(key for key, _ in rows)
    # A name path under repeated, indistinguishable parents is not proof of
    # occurrence ownership. Do not certify that association by list ordering.
    ambiguous_parents = {
        key[:-1]
        for key, _ in expected
        if expected_counts[key[:-1]] > 1 or actual_counts[key[:-1]] > 1
    }
    failures = []
    if expected_counts != actual_counts:
        failures.append("hierarchy/name/mirror mismatch")
    frames_checked = 0
    frames_unverified = 0
    properties_unverified = 0
    # Repeated names need one-to-one frame matching, not zip/order or ID equality.
    remaining = list(expected)
    for key, part in sorted(
        rows, key=lambda row: row[1].placement.world_transform is None
    ):
        candidates = [(i, info) for i, (k, info) in enumerate(remaining) if k == key]
        match = None
        for i, info in candidates:
            # An unloaded SDK placeholder can replace comment/ref flags. It is
            # not an oracle for those saved fields. Still check name and frame.
            unloaded = info.get("is_unloaded", False)
            if not unloaded and (
                info["comment"] != part.comment
                or info["is_external"] != part.is_external
            ):
                continue
            cs = info.get("cs")
            frame_known = (
                part.placement.world_transform is not None
                and isinstance(cs, dict)
                and "org" in cs
            )
            if not frame_known:
                match = (i, False, unloaded)
                break
            wanted = [
                [cs[axis][coord] for axis in ("xvec", "yvec", "zvec", "org")]
                for coord in "xyz"
            ] + [[0, 0, 0, 1]]
            if not all(
                math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-5)
                for arow, brow in zip(
                    part.placement.world_transform, wanted, strict=True
                )
                for a, b in zip(arow, brow, strict=True)
            ):
                continue
            match = (i, True, unloaded)
            break
        if match is None:
            failures.append(f"saved properties or placement mismatch: {part.name}")
        else:
            i, frame_known, unloaded = match
            remaining.pop(i)
            frames_checked += frame_known
            frames_unverified += not frame_known
            properties_unverified += 2 if unloaded else 0
    return {
        "status": (
            "mismatch"
            if failures
            else "partial"
            if frames_unverified or properties_unverified or ambiguous_parents
            else "matched"
        ),
        "scope": "host child-part inventory and available world frames only",
        "parts": len(rows),
        "frames_checked": frames_checked,
        "frames_unverified": frames_unverified,
        "saved_properties_unverified": properties_unverified,
        "ambiguous_parent_paths": len(ambiguous_parents),
        "unloaded_scope": "SDK placeholder properties are not saved properties",
        "failures": failures,
    }


def compare_parameters(doc, oracle, *, owner_id=None):
    """Saved scalar fields, with SDK decimal-output tolerance; no evaluation."""
    expected = oracle.get("parameters")
    if not isinstance(expected, list):
        return {"status": "not_comparable", "reason": "SDK did not return a list"}
    index = doc.read_parameters()
    if index.status != "complete":
        return {"status": "unsupported", "reader_status": index.status}
    if owner_id is None:
        roots = [p for p in doc.read_parts().parts if p.is_root]
        if len(roots) != 1:
            return {"status": "unsupported", "reason": "no unique document root"}
        owner_id = roots[0].part_id
    actual = [p for p in index.parameters if p.owner_part_id == owner_id]
    failures = []
    if len(actual) != len(expected):
        failures.append("parameter_count")
    fields = {
        "name": "Name",
        "raw_type": "Type",
        "equation": "Equation",
        "explain": "Explain",
        "comment": "Comment",
        "enabled": "status",
    }
    for i, (parameter, observation) in enumerate(zip(actual, expected, strict=False)):
        for field, key in fields.items():
            if key not in observation or getattr(parameter, field) != observation[key]:
                failures.append(f"{i}:{field}")
        value = observation.get("dvalue")
        if (
            parameter.value is None
            or not isinstance(value, (float, int))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not math.isclose(parameter.value, value, rel_tol=1e-12, abs_tol=5.1e-9)
        ):
            failures.append(f"{i}:value")
    return {
        "status": "mismatch" if failures else "matched",
        "scope": "saved scalar fields of one owner; no formula evaluation",
        "parameters": len(actual),
        "failures": failures,
        "value_tolerance": {"relative": 1e-12, "absolute": 5.1e-9},
    }


def verify(manifest, roots, *, partition=None):
    rows = validate_manifest(manifest)
    checked = {}
    results = []
    for row in rows:
        if partition and row["partition"] != partition:
            continue
        path = checked_file(row["input"], roots, checked)
        for spec in row.get("related_files", []):
            checked_file(spec, roots, checked)
        for spec in row.get("absent_files", []):
            require(
                not resolve(spec, roots).exists(), "expected missing dependency exists"
            )
        doc = icadkit.read(path)
        index = doc.read_parts()
        actual = snapshot(doc, index)
        result = {
            "id": row["id"],
            "input_root": row["input"]["root"],
            "family_id": row["family_id"],
            "partition": row["partition"],
            "baseline_equal": actual == row["baseline"],
            "actual": actual,
        }
        profile = getattr(index, "profile", None)
        result["profile_id"] = profile.profile_id if profile else None
        result["document_kind"] = getattr(index, "document_kind", "unknown")
        result["views"] = [
            {"kind": v.kind, "byte_range": asdict(v.byte_range)}
            for v in getattr(index, "views", ())
        ]
        if "oracle" in row:
            spec = row["oracle"]
            result["sdk_observation_scope"] = spec["scope"]
            oracle = json.loads(
                checked_file(spec["file"], roots, checked).read_text("utf-8-sig")
            )
            mode = oracle.get("capture_mode", "document")
            require(
                (spec["scope"] == "library_part_insertion")
                == (mode == "library_part_insertion_into_new_document"),
                "SDK observation scope contradicts captured provenance",
            )
            result["sdk"] = compare_sdk(index, oracle, spec["scope"])
        if "parameter_oracle" in row:
            spec = row["parameter_oracle"]
            observation = json.loads(
                checked_file(spec["file"], roots, checked).read_text("utf-8-sig")
            )
            result["parameters"] = compare_parameters(doc, observation)
        results.append(result)
    require(bool(results), "no inputs in requested partition")
    sdk = Counter(r["sdk"]["status"] for r in results if "sdk" in r)
    parameters = Counter(
        r["parameters"]["status"] for r in results if "parameters" in r
    )
    populations = {}
    for root in sorted({r["input_root"] for r in results}):
        selected = [r for r in results if r["input_root"] == root]
        versions = Counter(
            (
                r["actual"]["raw_version"],
                r["actual"]["byte_order"],
                r["actual"]["parts_status"]["index"],
            )
            for r in selected
        )
        populations[root] = {
            "files": len(selected),
            "by_version_order_index": [
                {"raw_version": v, "byte_order": o, "index": s, "files": n}
                for (v, o, s), n in sorted(versions.items())
            ],
        }
    return {
        "schema_version": 1,
        "scope": "Offline regression plus host SDK inventories; not full CAD support",
        "files": len(results),
        "checked_artifacts": len(checked),
        "baseline_equal": sum(r["baseline_equal"] for r in results),
        "sdk": dict(sdk),
        "parameters": dict(parameters),
        "populations": populations,
        "passed": all(r["baseline_equal"] for r in results)
        and not sdk["mismatch"]
        and not parameters["mismatch"],
        "rows": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", help="Expected manifest digest")
    parser.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--partition", choices=["development", "validation"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = {}
    for value in args.root:
        name, separator, path = value.partition("=")
        require(
            separator and name and path and name not in roots, "invalid root argument"
        )
        roots[name] = Path(path)
    manifest_digest = digest(args.manifest)
    if args.manifest_sha256:
        require(manifest_digest == args.manifest_sha256, "changed corpus manifest")
    manifest = json.loads(args.manifest.read_text())
    result = verify(manifest, roots, partition=args.partition)
    result["manifest_sha256"] = manifest_digest
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("rows", "populations")}
        )
    )
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
