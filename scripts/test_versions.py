"""Regression tests for release-version updates; run with Python 3.11+."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bump_version import ROOT, bump_version, check_versions, python_version, read_toml


class VersionManagement(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="icadkit-version-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("Cargo.toml", "Cargo.lock", "pyproject.toml", "uv.lock"):
            shutil.copy2(ROOT / name, self.root / name)
        workspace = read_toml(self.root / "Cargo.toml")["workspace"]
        for member in workspace["members"]:
            (self.root / member).mkdir(parents=True)
            shutil.copy2(
                ROOT / member / "Cargo.toml", self.root / member / "Cargo.toml"
            )
        self.current = workspace["package"]["version"]
        major, minor, patch_number = self.current.split("-")[0].split(".")
        self.next = f"{major}.{minor}.{int(patch_number) + 1}"

    def snapshot(self):
        return {
            p.relative_to(self.root): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def replace(self, name, old, new):
        path = self.root / name
        text = path.read_text("utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def refresh_locks(self, command, *, cwd, check):
        self.assertEqual(cwd, self.root)
        self.assertTrue(check)
        workspace = read_toml(self.root / "Cargo.toml")["workspace"]
        version = workspace["package"]["version"]
        if command[0] == "cargo":
            for member in workspace["members"]:
                name = read_toml(self.root / member / "Cargo.toml")["package"]["name"]
                self.replace(
                    "Cargo.lock",
                    f'name = "{name}"\nversion = "{self.current}"',
                    f'name = "{name}"\nversion = "{version}"',
                )
        else:
            path = self.root / "uv.lock"
            text = re.sub(
                r'(name = "icadkit"\n)version = "[^"]+"\n', r"\1", path.read_text()
            )
            path.write_text(text, encoding="utf-8")

    def test_repository_versions_and_matching_tag(self):
        before = self.snapshot()
        self.assertEqual(check_versions(self.root, f"v{self.current}"), self.current)
        self.assertEqual(self.snapshot(), before)

    def test_rejects_stale_cargo_lock_for_each_crate(self):
        for name in ("icad-core", "icad-python"):
            with self.subTest(name=name):
                path = self.root / "Cargo.lock"
                original = path.read_bytes()
                self.replace(
                    "Cargo.lock",
                    f'name = "{name}"\nversion = "{self.current}"',
                    f'name = "{name}"\nversion = "{self.next}"',
                )
                with self.assertRaisesRegex(ValueError, "Cargo.lock"):
                    check_versions(self.root)
                path.write_bytes(original)

    def test_rejects_crate_version_override(self):
        self.replace(
            "crates/icad-core/Cargo.toml",
            "version.workspace = true",
            f'version = "{self.current}"',
        )
        with self.assertRaisesRegex(ValueError, "inherit"):
            check_versions(self.root)

    def test_rejects_static_python_version(self):
        self.replace(
            "pyproject.toml", 'dynamic = ["version"]', f'version = "{self.current}"'
        )
        with self.assertRaisesRegex(ValueError, "dynamic"):
            check_versions(self.root)

    def test_uv_lock_with_explicit_or_omitted_version(self):
        path = self.root / "uv.lock"
        original = path.read_bytes()
        for version in (None, python_version(self.current), self.next):
            with self.subTest(version=version):
                path.write_bytes(original)
                text = re.sub(
                    r'(name = "icadkit"\n)(?:version = "[^"]+"\n)?',
                    lambda m, v=version: m[1] + (f'version = "{v}"\n' if v else ""),
                    path.read_text(),
                )
                path.write_text(text, encoding="utf-8")
                if version == self.next:
                    with self.assertRaisesRegex(ValueError, "uv.lock"):
                        check_versions(self.root)
                else:
                    check_versions(self.root)

    def test_rejects_registry_package_in_place_of_checkout(self):
        self.replace(
            "uv.lock", 'source = { editable = "." }', 'source = { registry = "x" }'
        )
        with self.assertRaisesRegex(ValueError, "local icadkit"):
            check_versions(self.root)

    def test_rejects_wrong_tag(self):
        for tag in (f"v{self.next}", self.current, "main", ""):
            with (
                self.subTest(tag=tag),
                self.assertRaisesRegex(ValueError, "release tag"),
            ):
                check_versions(self.root, tag)

    def test_bump_preserves_dependencies_and_unrelated_edits(self):
        self.replace("Cargo.toml", "[workspace.package]", "[workspace.package] # keep")
        self.replace("Cargo.lock", "version = 4", "version = 4 # keep")
        before = self.snapshot()
        with patch(
            "bump_version.subprocess.run", side_effect=self.refresh_locks
        ) as run:
            bump_version(self.root, self.next, offline=True)
        self.assertEqual(check_versions(self.root), self.next)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["cargo", "update", "--workspace", "--offline"],
                ["uv", "lock", "--offline"],
            ],
        )
        after = self.snapshot()
        for name in before:
            if str(name) == "Cargo.toml":
                self.assertEqual(
                    after[name],
                    before[name].replace(
                        f'version = "{self.current}"'.encode(),
                        f'version = "{self.next}"'.encode(),
                        1,
                    ),
                )
            elif str(name) == "Cargo.lock":
                expected = before[name]
                for crate in ("icad-core", "icad-python"):
                    expected = expected.replace(
                        f'name = "{crate}"\nversion = "{self.current}"'.encode(),
                        f'name = "{crate}"\nversion = "{self.next}"'.encode(),
                    )
                self.assertEqual(after[name], expected)
            else:
                self.assertEqual(after[name], before[name])

    def test_rollback_after_partial_lock_update_or_interrupt(self):
        self.replace("Cargo.toml", "[workspace.package]", "[workspace.package] # keep")
        before = self.snapshot()
        for failure in (
            subprocess.CalledProcessError(1, ["uv", "lock"]),
            KeyboardInterrupt(),
        ):

            def fail(command, error=failure, **kwargs):
                if command[0] == "uv":
                    (self.root / "uv.lock").write_text("partial lock update")
                    raise error
                self.refresh_locks(command, **kwargs)

            with (
                self.subTest(failure=type(failure).__name__),
                patch("bump_version.subprocess.run", side_effect=fail),
                self.assertRaises(type(failure)),
            ):
                bump_version(self.root, self.next)
            self.assertEqual(self.snapshot(), before)

    def test_rollback_if_dependency_resolution_changes(self):
        before = self.snapshot()

        def change_dependency(command, **kwargs):
            self.refresh_locks(command, **kwargs)
            if command[0] == "uv":
                self.replace(
                    "Cargo.lock", 'name = "parasolid-core"', 'name = "changed"'
                )

        with (
            patch("bump_version.subprocess.run", side_effect=change_dependency),
            self.assertRaisesRegex(ValueError, "third-party"),
        ):
            bump_version(self.root, self.next)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_version_never_writes_or_runs_tools(self):
        before = self.snapshot()
        for version in ("v1.2.3", "1.2", "01.2.3", "1.2.3-dev.01", "1.2.3+build", ""):
            with (
                self.subTest(version=version),
                patch("bump_version.subprocess.run") as run,
                self.assertRaises(ValueError),
            ):
                bump_version(self.root, version)
            run.assert_not_called()
            self.assertEqual(self.snapshot(), before)

    def test_development_version_matches_python_metadata(self):
        with patch("bump_version.subprocess.run", side_effect=self.refresh_locks):
            bump_version(self.root, self.next + "-dev.1")
        self.assertEqual(check_versions(self.root), self.next + "-dev.1")
        self.assertEqual(python_version(self.next + "-dev.1"), self.next + ".dev1")


if __name__ == "__main__":
    unittest.main()
