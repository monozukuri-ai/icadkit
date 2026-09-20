"""Regression tests for publication boundaries; run with Python 3.11+."""

import unittest
from email.message import Message

from check_license import (
    DISTRIBUTION_LICENSE,
    LICENSE_PATHS,
    ROOT,
    check_archive_licenses,
)
from check_public_tree import check_tree


class PublicationChecks(unittest.TestCase):
    def setUp(self):
        self.prefix = "icadkit-0.1.0.dist-info/licenses/"
        self.metadata = Message()
        self.metadata["License-Expression"] = DISTRIBUTION_LICENSE
        for name in LICENSE_PATHS:
            self.metadata["License-File"] = name
        self.payload = {
            self.prefix + name: (ROOT / name).read_bytes() for name in LICENSE_PATHS
        }

    def check_archive(self):
        check_archive_licenses(self.metadata, self.payload.__getitem__, self.prefix)

    def test_correct_archive(self):
        self.check_archive()

    def test_old_license_expression(self):
        self.metadata.replace_header("License-Expression", "MIT AND Apache-2.0")
        with self.assertRaisesRegex(ValueError, "expression"):
            self.check_archive()

    def test_missing_declared_license(self):
        del self.metadata["License-File"]
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.check_archive()

    def test_duplicate_declared_license(self):
        self.metadata["License-File"] = "LICENSE"
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.check_archive()

    def test_stale_notice(self):
        self.payload[self.prefix + "LICENSE"] += b"changed\n"
        with self.assertRaisesRegex(ValueError, "stale"):
            self.check_archive()

    def test_right_name_wrong_archive_location(self):
        self.payload["LICENSE"] = self.payload.pop(self.prefix + "LICENSE")
        with self.assertRaises(KeyError):
            self.check_archive()

    def test_public_documentation(self):
        files = {"README.md": b"[API](docs/api.md)", "docs/api.md": b"# API\n"}
        check_tree(files, files.__getitem__)

    def test_private_file(self):
        for name in ("reports/result.json", ".local/model.icd", "docs/m3-status.md"):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "nonpublic"),
            ):
                check_tree([name], lambda _: b"")

    def test_catalog_and_model_files(self):
        for name in ("tests/example.ICD", "docs/example.SCH_TXT", "corpus/example.x_b"):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "nonpublic"),
            ):
                check_tree([name], lambda _: b"")

    def test_private_documentation_reference(self):
        with self.assertRaisesRegex(ValueError, "internal reference"):
            check_tree(["README.md"], lambda _: b"See reports/results.json")

    def test_private_link_is_not_satisfied_by_a_local_file(self):
        with self.assertRaisesRegex(ValueError, "link missing"):
            check_tree(["README.md"], lambda _: b"[notes](notes.md)")

    def test_link_outside_repository(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            check_tree(["README.md"], lambda _: b"[sibling](../README.md)")


if __name__ == "__main__":
    unittest.main()
