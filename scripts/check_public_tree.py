"""Check tracked publication paths and public documentation (Python 3.11+)."""

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOTS = {
    ".gitattributes",
    ".gitignore",
    ".github",
    "Cargo.toml",
    "Cargo.lock",
    "pyproject.toml",
    "uv.lock",
    "LICENSE",
    "LICENSES",
    "COMMERCIAL-LICENSE.md",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "docs",
    "src",
    "crates",
    "tests",
    "scripts",
    "corpus",
}
PRIVATE_PARTS = {".local", "reports", "__pycache__", "target", ".venv", "dist"}
PRIVATE_NAMES = re.compile(
    r"^(?:m\d+-status\.md|implementation-plan\.md|format-notes\.md|releasing\.md)$"
)
DOC_PRIVATE = re.compile(
    r"(?:\.local/|\breports/|/home/|/Users/|\bM[0-9]+\b|"
    r"implementation-plan\.md|format-notes\.md|m\d+-status\.md|docs/validation/)"
)


def check_paths(names):
    for name in names:
        p = PurePosixPath(name)
        if (
            p.is_absolute()
            or ".." in p.parts
            or not p.parts
            or p.parts[0] not in PUBLIC_ROOTS
            or PRIVATE_PARTS.intersection(p.parts)
            or PRIVATE_NAMES.match(p.name)
            or p.suffix.lower()
            in {".icd", ".x_b", ".x_t", ".sch_txt", ".so", ".pyd", ".pyc"}
            or name.startswith(("docs/validation/", "crates/icad-core/examples/"))
        ):
            raise ValueError(f"nonpublic path: {name}")


def check_document(name, text, names):
    if DOC_PRIVATE.search(text):
        raise ValueError(f"internal reference in public documentation: {name}")
    for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", text):
        url = urlsplit(target.strip("<>"))
        if url.scheme or url.netloc or not url.path:
            continue
        # Resolve lexically, using the publication set rather than private disk files.
        parts = list(PurePosixPath(name).parent.parts)
        for part in PurePosixPath(unquote(url.path)).parts:
            if part == "..":
                if not parts:
                    raise ValueError(f"link escapes source tree: {name}: {target}")
                parts.pop()
            elif part != ".":
                parts.append(part)
        resolved = "/".join(parts)
        if resolved not in names:
            raise ValueError(f"link missing from public tree: {name}: {target}")


def check_tree(names, read):
    names = set(names)
    check_paths(names)
    for name in sorted(names):
        if name == "README.md" or (name.startswith("docs/") and name.endswith(".md")):
            check_document(name, read(name).decode("utf-8"), names)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged", action="store_true", help="Read Git's index contents"
    )
    args = parser.parse_args()
    names = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    names = [n for n in names if n]
    if not names:
        raise ValueError("no tracked files to audit")

    def read(name):
        if args.staged:
            return subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
        path = ROOT / name
        if path.is_symlink():
            raise ValueError(f"symlink in public tree: {name}")
        return path.read_bytes()

    check_tree(names, read)
    print(f"Public paths and documentation passed: {len(names)} files")
