#!/usr/bin/env python3
"""Verify a hash-pinned public or local corpus. Every listed input is required."""

import argparse
import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

import icadkit


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(manifest, root):
    rows = [json.loads(line) for line in manifest.read_text().splitlines() if line]
    require(bool(rows), "empty corpus manifest")
    require(len({r["id"] for r in rows}) == len(rows), "duplicate corpus ID")
    results = []
    for row in rows:
        relative = Path(row["path"])
        require(
            not relative.is_absolute() and ".." not in relative.parts, "unsafe path"
        )
        path = root / relative
        payload = path.read_bytes()  # missing inputs fail, never skip
        require(
            hashlib.sha256(payload).hexdigest() == row["sha256"],
            f"changed input: {row['id']}",
        )
        doc = icadkit.read(path)
        expected = row["expected"]
        require(doc.header.byte_order == expected["byte_order"], "byte order")
        require(len(doc.resources) == expected["resources"], "resource count")
        require(doc.resource_index_status == "complete", "resource index incomplete")
        require(
            doc.status.container == "partial" and doc.status.model == "not_checked",
            "scope promotion",
        )
        actual = {"id": row["id"], "source_sha256": doc.source_sha256}
        if doc.resources:
            g = doc.read_geometry(doc.resources[0].resource_id)
            actual.update(
                status=asdict(g.status), codes=[d.code for d in g.diagnostics]
            )
            for scope, status in expected["status"].items():
                require(getattr(g.status, scope) == status, f"{row['id']}: {scope}")
            require(
                actual["codes"] == expected["codes"],
                f"{row['id']}: diagnostics {actual['codes']}",
            )
            if "wire" in expected:
                g.require_complete("brep")
                b = g.brep
                require(g.raw.node_count == 11, "wire raw node count")
                for kind, count in expected["wire"]["counts"].items():
                    require(b.counts[kind] == count, f"wire {kind}")
                points = sorted(e.attributes["position"] for e in b.entities("points"))
                require(points == [(2, -1, 3), (5, 3, 3)], "wire coordinates")
                require(math.dist(*points) == 5, "independent wire length")
                require(b.volume is None, "wire is not a solid")
                require(
                    g.raw.to_bytes() == doc.extract_bytes(doc.resources[0].resource_id),
                    "payload retention",
                )
                actual["wire_length"] = math.dist(*points)
                actual["counts"] = dict(b.counts)
        results.append(actual)
    return {
        "passed": True,
        "cases": len(results),
        "build_info": asdict(icadkit.build_info()),
        "rows": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--required",
        action="store_true",
        help="compatibility flag; inputs are always required",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.manifest, args.root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases": report["cases"]}))
