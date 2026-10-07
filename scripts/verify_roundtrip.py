#!/usr/bin/env python3
"""Check that unmodified ICD files serialize byte for byte through ``to_bytes()``.

Every ``.icd`` below the given roots is read once; duplicates by SHA-256 are
counted once. A readable file must reproduce its own bytes. Unreadable files are
listed with their diagnostic code and do not count as failures. The exit code is
0 when every readable file is identical, 3 otherwise and 2 for bad arguments.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import icadkit


def iter_files(roots):
    for root in roots:
        if root.is_file():
            yield root
            continue
        yield from sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".icd"
        )


def check(data):
    try:
        doc = icadkit.read(data)
    except icadkit.IcadError as exc:
        return {"result": "unreadable", "code": exc.diagnostic.code}
    out = doc.to_bytes()
    row = {
        "byte_order": doc.header.byte_order,
        "raw_version": doc.header.raw_version.hex(),
        "size": len(data),
    }
    if out == data:
        row["result"] = "identical"
        return row
    limit = min(len(out), len(data))
    first = next((i for i in range(limit) if out[i] != data[i]), limit)
    row.update(result="mismatch", first_difference=first, output_size=len(out))
    return row


def verify(roots, limit=None):
    seen = set()
    rows = []
    for path in iter_files(roots):
        if limit is not None and len(rows) >= limit:
            break
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        row = {"path": str(path), "sha256": digest}
        row.update(check(data))
        rows.append(row)
    results = Counter(row["result"] for row in rows)
    versions = Counter(
        (row["byte_order"], row["raw_version"], row["result"])
        for row in rows
        if "raw_version" in row
    )
    return {
        "passed": results["mismatch"] == 0,
        "files": len(rows),
        "results": dict(results),
        "by_version": [
            {"byte_order": o, "raw_version": v, "result": r, "files": n}
            for (o, v, r), n in sorted(versions.items())
        ],
        "mismatches": [row for row in rows if row["result"] == "mismatch"],
        "unreadable": Counter(
            row["code"] for row in rows if row["result"] == "unreadable"
        ),
        "build_info": icadkit.build_info().__dict__,
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path, help="ICD files or directories")
    parser.add_argument("--limit", type=int, help="stop after this many unique files")
    parser.add_argument("--output", type=Path, help="write the full JSON report here")
    args = parser.parse_args()
    for root in args.roots:
        if not root.exists():
            parser.error(f"{root} does not exist")
    report = verify(args.roots, args.limit)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=dict) + "\n")
    summary = {key: report[key] for key in ("passed", "files", "results", "unreadable")}
    summary["mismatches"] = [
        {k: row[k] for k in ("path", "size", "output_size", "first_difference")}
        for row in report["mismatches"][:20]
    ]
    print(json.dumps(summary, default=dict, ensure_ascii=False))
    sys.exit(0 if report["passed"] else 3)
