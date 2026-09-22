#!/usr/bin/env python3
"""Audit wheel/sdist payloads, hashes, types, licenses, and locked backend.

Requires Python 3.11+. Cold install and sdist rebuild are separate CI steps.
"""

import argparse
import base64
import csv
import hashlib
import io
import json
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

import tomllib
from check_license import ROOT, check_archive_licenses, notice_bundle
from check_public_tree import check_tree


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_paths(names):
    forbidden = {".git", ".venv", ".local", "reports", "__pycache__", "target"}
    for name in names:
        path = PurePosixPath(name)
        require(
            not path.is_absolute() and ".." not in path.parts, f"unsafe path: {name}"
        )
        require(
            not forbidden.intersection(path.parts), f"private/build content: {name}"
        )
        require(
            path.suffix.lower() not in {".icd", ".sch_txt", ".x_b", ".x_t"},
            f"CAD/catalog payload: {name}",
        )


def check_licenses(names):
    for required in (
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "LICENSES/parasolid-core-MIT.txt",
        "LICENSES/Apache-2.0.txt",
        "LICENSES/rust-dependencies.txt",
    ):
        require(
            any(n.endswith("/" + required) or n == required for n in names),
            f"missing license: {required}",
        )


def wheel(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "duplicate wheel entries")
        check_paths(names)
        check_licenses(names)
        metadata_names = [n for n in names if n.endswith(".dist-info/METADATA")]
        require(len(metadata_names) == 1, "wheel needs one METADATA")
        dist_info = metadata_names[0].rsplit("/", 1)[0]
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        require(metadata["Name"] == "icadkit", "unexpected distribution")
        require(metadata["Requires-Python"] == ">=3.10", "Python range changed")
        require(
            metadata.get_all("Requires-Dist")
            == ["parasolid-kit[occt]==0.2.0 ; extra == 'preview'"],
            "unexpected base or optional runtime dependency",
        )
        require(
            metadata.get_all("Provides-Extra") == ["preview"], "preview extra missing"
        )
        check_archive_licenses(metadata, archive.read, dist_info + "/licenses/")
        for name in names:
            require(
                name.startswith(("icadkit/", dist_info + "/")),
                f"extra wheel file: {name}",
            )
        for required in (
            "__init__.py",
            "api.py",
            "models.py",
            "document.py",
            "parts.py",
            "native.py",
            "csg.py",
            "saved.py",
            "_saved_occt.py",
            "_csg_occt.py",
            "preview.py",
            "viewer.py",
            "_viewer_io.py",
            "_viewer/index.html",
            "_viewer/viewer.js",
            "_viewer/viewer.css",
            "_preview_bridge.py",
            "geometry.py",
            "schema.py",
            "errors.py",
            "cli.py",
            "__main__.py",
            "_core.pyi",
            "py.typed",
        ):
            require(f"icadkit/{required}" in names, f"missing package file: {required}")
        native = [
            n
            for n in names
            if n.startswith("icadkit/_core.") and n.endswith((".so", ".pyd"))
        ]
        require(len(native) == 1, "wheel must contain exactly one native extension")
        tags = (
            BytesParser().parsebytes(archive.read(dist_info + "/WHEEL")).get_all("Tag")
        )
        require(
            tags and all(t.startswith("cp310-abi3-") for t in tags),
            "unexpected ABI tags",
        )
        record_name = dist_info + "/RECORD"
        records = list(csv.reader(io.StringIO(archive.read(record_name).decode())))
        require({r[0] for r in records} == set(names), "RECORD membership mismatch")
        for name, digest, size in records:
            if name == record_name:
                require(not digest and not size, "RECORD must not hash itself")
                continue
            data = archive.read(name)
            actual = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .rstrip(b"=")
                .decode()
            )
            require(
                digest == "sha256=" + actual and size == str(len(data)),
                f"bad RECORD: {name}",
            )
        return {
            "kind": "wheel",
            "version": metadata["Version"],
            "tags": tags,
            "files": names,
        }


def sdist(path):
    with tarfile.open(path) as archive:
        members = [m for m in archive.getmembers() if not m.isdir()]
        require(all(m.isfile() for m in members), "sdist has links or special files")
        original = [m.name for m in members]
        require(len(original) == len(set(original)), "duplicate sdist entries")
        check_paths(original)
        roots = {PurePosixPath(n).parts[0] for n in original}
        require(len(roots) == 1, "sdist needs one root directory")
        names = {n.split("/", 1)[1]: n for n in original}
        check_licenses(names)
        for name in (
            "Cargo.toml",
            "Cargo.lock",
            "pyproject.toml",
            "uv.lock",
            "crates/icad-core/src/lib.rs",
            "crates/icad-core/src/header.rs",
            "crates/icad-core/src/reader.rs",
            "crates/icad-core/src/record.rs",
            "crates/icad-core/src/error.rs",
            "crates/icad-core/src/limits.rs",
            "crates/icad-core/src/document.rs",
            "crates/icad-core/src/parts.rs",
            "crates/icad-core/src/native.rs",
            "crates/icad-core/src/parasolid.rs",
            "crates/icad-core/src/schema.rs",
            "crates/icad-core/src/resource.rs",
            "crates/icad-core/src/compression.rs",
            "crates/icad-core/src/provenance.rs",
            "crates/icad-core/tests/header.rs",
            "crates/icad-core/tests/resources.rs",
            "crates/icad-python/src/lib.rs",
            "crates/icad-python/src/geometry.rs",
            "crates/icad-python/src/schema.rs",
            "crates/icad-python/src/brep.rs",
            "crates/icad-python/src/document.rs",
            "src/icadkit/document.py",
            "src/icadkit/parts.py",
            "src/icadkit/native.py",
            "src/icadkit/csg.py",
            "src/icadkit/saved.py",
            "src/icadkit/_saved_occt.py",
            "src/icadkit/_csg_occt.py",
            "src/icadkit/geometry.py",
            "src/icadkit/schema.py",
            "src/icadkit/preview.py",
            "src/icadkit/viewer.py",
            "src/icadkit/_viewer_io.py",
            "src/icadkit/_viewer/index.html",
            "src/icadkit/_viewer/viewer.js",
            "src/icadkit/_viewer/viewer.css",
            "src/icadkit/_preview_bridge.py",
            "src/icadkit/_core.pyi",
            "src/icadkit/py.typed",
            "tests/test_api.py",
            "tests/test_cli.py",
            "tests/test_distribution.py",
            "tests/test_inspect.py",
            "tests/test_resources.py",
            "tests/test_geometry.py",
            "tests/geometry_fixtures.py",
            "tests/part_fixtures.py",
            "tests/test_parts.py",
            "tests/test_v7_parts.py",
            "tests/test_part_semantics.py",
            "tests/test_native.py",
            "tests/test_csg.py",
            "tests/test_saved.py",
            "tests/preview_fixtures.py",
            "tests/test_preview.py",
            "tests/test_viewer.py",
            "tests/test_resource_cli.py",
            "tests/conftest.py",
            "tests/fixture_builders.py",
            "tests/test_corpus.py",
            "corpus/generate.py",
            "corpus/public.jsonl",
            "corpus/README.md",
            "scripts/verify_corpus.py",
            "scripts/verify_install.py",
            "scripts/verify_artifacts.py",
            "scripts/check_license.py",
            "scripts/check_public_tree.py",
            "docs/api.md",
            "docs/changelog.md",
            "docs/parts.md",
            "docs/native.md",
            "docs/csg.md",
            "docs/saved-bodies.md",
            "docs/preview.md",
            "docs/viewer.md",
            "docs/support.md",
            "docs/license.md",
            "crates/icad-core/LICENSE",
            "crates/icad-python/LICENSE",
        ):
            require(name in names, f"missing sdist file: {name}")

        def read(name):
            stream = archive.extractfile(names[name])
            require(stream is not None, f"cannot read: {name}")
            return stream.read()

        lock = tomllib.loads(read("Cargo.lock").decode())
        dep = next(p for p in lock["package"] if p["name"] == "parasolid-core")
        require(
            dep["version"] == "0.3.0" and dep["source"].startswith("registry+"),
            "backend must be registry 0.3.0",
        )
        manifest = tomllib.loads(read("Cargo.toml").decode())
        require(
            manifest["workspace"]["dependencies"]["parasolid-core"] == "=0.3.0",
            "backend manifest pin changed",
        )
        require(
            b'PARASOLID_CORE_VERSION: &str = "0.3.0"'
            in read("crates/icad-core/src/lib.rs"),
            "reported backend version changed",
        )
        for name in names:
            require("parasolid-kit" not in name, "sibling source included")
        metadata = BytesParser().parsebytes(read("PKG-INFO"))
        check_archive_licenses(metadata, read, "")
        check_tree([n for n in names if n != "PKG-INFO"], read)
        for member in ("icad-core", "icad-python"):
            require(
                read(f"crates/{member}/LICENSE") == notice_bundle(),
                "stale crate notice",
            )
        require(
            metadata["Version"]
            == manifest["workspace"]["package"]["version"].replace("-dev.", ".dev"),
            "sdist package and Rust versions disagree",
        )
        return {
            "kind": "sdist",
            "version": metadata["Version"],
            "dependency": dep,
            "files": list(names),
        }


def check_release_versions(versions, workspace_version, release_tag=None):
    # Match the Cargo development-version spelling used by the sdist check.
    python_version = workspace_version.replace("-dev.", ".dev")
    require(set(versions) == {python_version}, "artifacts do not match source version")
    if release_tag is not None:
        require(
            release_tag == "v" + workspace_version,
            "release tag must match v<workspace.package.version>",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-platform-set", action="store_true")
    parser.add_argument("--release-tag", help="Require this tag to match Cargo.toml")
    args = parser.parse_args()
    wheels = sorted(args.directory.glob("*.whl"))
    sources = sorted(args.directory.glob("*.tar.gz"))
    require(wheels and sources, "need both wheel and sdist")
    result = []
    for path in [*wheels, *sources]:
        record = wheel(path) if path.suffix == ".whl" else sdist(path)
        record.update(
            name=path.name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size_bytes=path.stat().st_size,
        )
        result.append(record)
    workspace_version = tomllib.loads((ROOT / "Cargo.toml").read_text("utf-8"))[
        "workspace"
    ]["package"]["version"]
    check_release_versions(
        [r["version"] for r in result], workspace_version, args.release_tag
    )
    if args.require_platform_set:
        require(
            len(wheels) == 3 and len(sources) == 1, "require three wheels and one sdist"
        )
        tags = [tag for r in result if r["kind"] == "wheel" for tag in r["tags"]]
        for suffix in ("manylinux_2_28_x86_64", "win_amd64", "macosx_11_0_arm64"):
            require(
                any(t.endswith(suffix) for t in tags), f"missing platform: {suffix}"
            )
    rendered = json.dumps({"schema_version": 1, "artifacts": result}, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(
        json.dumps(
            [{k: v for k, v in r.items() if k != "files"} for r in result], indent=2
        )
    )


if __name__ == "__main__":
    main()
