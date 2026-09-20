#!/usr/bin/env python3
"""Cold-install the exact wheel and test outside the checkout (uv required)."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path


def check(command, *, cwd, env=None):
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    ).stdout


def verify(wheel, source, versions):
    rows = []
    for version in versions:
        with tempfile.TemporaryDirectory(prefix="icadkit-cold-") as temporary:
            work = Path(temporary)
            venv = work / "venv"
            check(["uv", "venv", str(venv), "--python", version], cwd=work)
            bindir = venv / ("Scripts" if os.name == "nt" else "bin")
            python = bindir / ("python.exe" if os.name == "nt" else "python")
            check(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-deps",
                    str(wheel),
                ],
                cwd=work,
            )
            env = os.environ.copy()
            env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            smoke = json.loads(
                check(
                    [
                        str(python),
                        "-I",
                        "-c",
                        "import json,sys,platform,icadkit,icadkit._core; "
                        "from dataclasses import asdict; "
                        "from importlib.metadata import distribution; "
                        "assert distribution('icadkit').requires in (None, []); "
                        "assert sys.prefix in icadkit._core.__file__; "
                        "print(json.dumps(dict(python=platform.python_version(), "
                        "machine=platform.machine(), native=icadkit._core.__file__, "
                        "build_info=asdict(icadkit.build_info()))))",
                    ],
                    cwd=work,
                    env=env,
                )
            )
            info = json.loads(
                check(
                    [str(python), "-I", "-m", "icadkit", "info", "--json"],
                    cwd=work,
                    env=env,
                )
            )
            check(
                ["uv", "pip", "install", "--python", str(python), "pytest==8.4.2"],
                cwd=work,
            )
            tests = check(
                [str(python), "-I", "-m", "pytest", str(source / "tests"), "-q"],
                cwd=work,
                env=env,
            )
            check(
                [
                    str(python),
                    str(source / "corpus/generate.py"),
                    "--output",
                    str(work / "corpus"),
                ],
                cwd=work,
                env=env,
            )
            corpus = json.loads(
                check(
                    [
                        str(python),
                        str(source / "scripts/verify_corpus.py"),
                        "--manifest",
                        str(source / "corpus/public.jsonl"),
                        "--root",
                        str(work / "corpus"),
                        "--required",
                    ],
                    cwd=work,
                    env=env,
                )
            )
            rows.append(
                {
                    "requested_python": version,
                    "smoke": smoke,
                    "cli": info,
                    "pytest": tests,
                    "public_corpus": corpus,
                }
            )
            print(
                f"Cold install passed: {platform.system()} Python {smoke['python']}",
                flush=True,
            )
    return {
        "passed": True,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "source_commit": os.environ.get("GITHUB_SHA"),
        "runs": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--python", required=True, action="append")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.wheel.resolve(), args.source.resolve(), args.python)
    except subprocess.CalledProcessError as exc:
        print(exc.stdout)
        print(exc.stderr)
        raise
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
