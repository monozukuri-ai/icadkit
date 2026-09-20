import json
import os
import subprocess
import sys

import pytest

import icadkit


@pytest.mark.parametrize("entrypoint", [["icadkit"], [sys.executable, "-m", "icadkit"]])
def test_installed_cli(entrypoint, tmp_path):
    result = subprocess.run(
        [*entrypoint, "info", "--json"],
        cwd=tmp_path,
        check=True,
        encoding="utf-8",
        capture_output=True,
    )
    info = json.loads(result.stdout)
    assert result.stderr == ""
    assert info["schema_version"] == 1
    assert info["version"] == icadkit.__version__
    assert info["parasolid_core_version"] == "0.2.0"
    assert info["builtin_profile_ids"]


@pytest.mark.parametrize("argument", ["--help", "--version"])
def test_cli_help_and_version(argument, tmp_path):
    result = subprocess.run(
        ["icadkit", argument],
        cwd=tmp_path,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    assert "icadkit" in result.stdout


def test_unimplemented_command_is_argument_error(tmp_path):
    result = subprocess.run(
        ["icadkit", "geometry", "missing.icd"],
        cwd=tmp_path,
        encoding="utf-8",
        capture_output=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""


def test_inspect_json_and_text(icd_factory, tmp_path):
    path = tmp_path / "図面.icd"
    path.write_bytes(icd_factory())
    result = subprocess.run(
        ["icadkit", "inspect", str(path), "--json"],
        check=True,
        encoding="utf-8",
        capture_output=True,
    )
    data = json.loads(result.stdout)
    assert result.stderr == ""
    assert data["schema_version"] == 1 and data["operation"] == "inspect"
    assert data["header"]["raw_version_hex"] == "00080003"
    assert data["header"]["name_cp932_candidate"] == "試験図面"
    assert len(bytes.fromhex(data["header"]["raw_mod_hex"])) == 256
    assert data["status"]["header"] == "complete"
    assert data["status"]["model"] == "not_checked"
    assert data["unparsed_ranges"][-1]["reason"] == "uninspected_tail"
    text = subprocess.run(
        ["icadkit", "inspect", str(path)],
        check=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert "container=partial" in text.stdout


@pytest.mark.parametrize(
    "case,exit_code,code",
    [
        ("missing", 1, "io.not_found"),
        ("truncated", 1, "header.truncated"),
        ("unsupported", 3, "header.unsupported_layout"),
        ("limit", 4, "limit.file_bytes"),
    ],
)
def test_inspect_json_errors(case, exit_code, code, icd_factory, tmp_path):
    path = tmp_path / "input.icd"
    args = ["icadkit", "inspect", str(path), "--json"]
    if case == "truncated":
        path.write_bytes(b"MOD0")
    elif case == "unsupported":
        data = bytearray(icd_factory())
        data[4:8] = (65).to_bytes(4, "little")
        path.write_bytes(data)
    elif case == "limit":
        path.write_bytes(icd_factory())
        args += ["--max-file-bytes", "8"]
    result = subprocess.run(args, encoding="utf-8", capture_output=True)
    assert result.returncode == exit_code
    assert result.stderr == ""
    assert json.loads(result.stdout)["error"]["code"] == code


def test_inspect_text_error_uses_stderr(tmp_path):
    result = subprocess.run(
        ["icadkit", "inspect", str(tmp_path / "missing.icd")],
        encoding="utf-8",
        capture_output=True,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert "io.not_found" in result.stderr


@pytest.mark.parametrize("value", ["0", "-1", "18446744073709551616", "not-an-integer"])
def test_invalid_cli_limit_is_usage_error(value, tmp_path):
    result = subprocess.run(
        [
            "icadkit",
            "inspect",
            str(tmp_path / "missing.icd"),
            "--max-file-bytes",
            value,
        ],
        encoding="utf-8",
        capture_output=True,
    )
    assert result.returncode == 2
    assert not result.stdout


def test_japanese_text_output_is_utf8_even_with_ascii_stdio(icd_factory, tmp_path):
    path = tmp_path / "図面.icd"
    path.write_bytes(icd_factory())
    process_env = os.environ.copy()
    process_env["PYTHONIOENCODING"] = "ascii"
    result = subprocess.run(
        ["icadkit", "inspect", str(path)],
        env=process_env,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    assert "図面.icd" in result.stdout
    assert "試験図面" in result.stdout
    assert not result.stderr
