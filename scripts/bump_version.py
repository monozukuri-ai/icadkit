# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Update the shared release version and synchronize Cargo/uv lock files."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-dev\.(?:0|[1-9][0-9]*))?"
)


def python_version(version):
    if not VERSION.fullmatch(version):
        raise ValueError("use X.Y.Z or X.Y.Z-dev.N (without a v prefix)")
    return version.replace("-dev.", ".dev")


def read_toml(path):
    return tomllib.loads(path.read_text("utf-8"))


def check_versions(root=ROOT, tag=None):
    """Check version sources without building or importing the native package."""
    workspace = read_toml(root / "Cargo.toml")["workspace"]
    version = workspace["package"]["version"]
    expected_python = python_version(version)
    project = read_toml(root / "pyproject.toml")["project"]
    if "version" in project or "version" not in project.get("dynamic", []):
        raise ValueError("pyproject.toml must use dynamic version metadata")
    locked = read_toml(root / "Cargo.lock")["package"]
    for member in workspace["members"]:
        package = read_toml(root / member / "Cargo.toml")["package"]
        if package.get("version") != {"workspace": True}:
            raise ValueError(f"{member} must inherit version.workspace = true")
        local = [p for p in locked if p["name"] == package["name"]]
        if len(local) != 1 or "source" in local[0] or local[0]["version"] != version:
            raise ValueError(f"Cargo.lock is out of date for {package['name']}")
    packages = read_toml(root / "uv.lock")["package"]
    local = [p for p in packages if p["name"] == project["name"]]
    if len(local) != 1 or local[0].get("source") != {"editable": "."}:
        raise ValueError("uv.lock must contain the local icadkit package")
    # uv normally omits the version of projects with dynamic metadata.
    if local[0].get("version", expected_python) != expected_python:
        raise ValueError("uv.lock contains an outdated icadkit version")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"release tag {tag!r} must match v{version}")
    return version


def external_packages(root, filename):
    """A release-only bump must preserve all resolved third-party packages."""
    packages = read_toml(root / filename)["package"]
    if filename == "Cargo.lock":
        return [p for p in packages if "source" in p]
    return [p for p in packages if p.get("source") != {"editable": "."}]


def bump_version(root, version, *, offline=False):
    python_version(version)  # Reject invalid input before writing anything.
    paths = [root / name for name in ("Cargo.toml", "Cargo.lock", "uv.lock")]
    original = {path: path.read_bytes() for path in paths}
    dependencies = {
        name: external_packages(root, name) for name in ("Cargo.lock", "uv.lock")
    }
    manifest = original[paths[0]].decode("utf-8")
    updated, count = re.subn(
        r"(?ms)(^\[workspace\.package\][^\S\r\n]*(?:#[^\r\n]*)?\r?\n"
        r'(?:(?!^\[).)*?^version\s*=\s*")[^"]+("[^\r\n]*)',
        lambda match: f"{match[1]}{version}{match[2]}",
        manifest,
    )
    if count != 1:
        raise ValueError("expected one [workspace.package] version in Cargo.toml")
    flags = ["--offline"] if offline else []
    try:
        paths[0].write_bytes(updated.encode("utf-8"))
        subprocess.run(["cargo", "update", "--workspace", *flags], cwd=root, check=True)
        subprocess.run(["uv", "lock", *flags], cwd=root, check=True)
        check_versions(root)
        for name, packages in dependencies.items():
            if external_packages(root, name) != packages:
                raise ValueError(f"{name}: release bump changed third-party packages")
    except BaseException:
        for path, contents in original.items():
            path.write_bytes(contents)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("version", nargs="?", help="new X.Y.Z or X.Y.Z-dev.N version")
    action.add_argument("--check", action="store_true", help="check versions and locks")
    parser.add_argument("--tag", help="also check a release tag (with --check)")
    parser.add_argument(
        "--offline", action="store_true", help="use cached dependencies"
    )
    args = parser.parse_args()
    if args.tag is not None and not args.check:
        parser.error("--tag requires --check")
    try:
        if args.check:
            version = check_versions(ROOT, args.tag)
            subprocess.run(
                ["uv", "lock", "--check", *(["--offline"] if args.offline else [])],
                cwd=ROOT,
                check=True,
            )
            print(f"Versions and locks are consistent: {version}")
        else:
            bump_version(ROOT, args.version, offline=args.offline)
            print(
                f"Updated to {args.version}. Review Cargo.toml, Cargo.lock and uv.lock."
            )
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
