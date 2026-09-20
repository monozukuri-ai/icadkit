import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATE = runpy.run_path(str(ROOT / "corpus/generate.py"))["generate"]
VERIFY = runpy.run_path(str(ROOT / "scripts/verify_corpus.py"))["verify"]
MANIFEST = ROOT / "corpus/public.jsonl"


def test_public_corpus(tmp_path):
    GENERATE(tmp_path)
    result = VERIFY(MANIFEST, tmp_path)
    assert result["passed"] and result["cases"] == 8


def test_required_corpus_rejects_missing_or_changed_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        VERIFY(MANIFEST, tmp_path)
    GENERATE(tmp_path)
    first = json.loads(MANIFEST.read_text().splitlines()[0])
    (tmp_path / first["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed input"):
        VERIFY(MANIFEST, tmp_path)


def test_corpus_does_not_regenerate_wrong_expectations(tmp_path):
    GENERATE(tmp_path)
    rows = [json.loads(line) for line in MANIFEST.read_text().splitlines()]
    rows[0]["expected"]["resources"] += 1
    wrong = tmp_path / "wrong.jsonl"
    wrong.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="resource count"):
        VERIFY(wrong, tmp_path)
