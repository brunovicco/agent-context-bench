import hashlib
import json
import shutil
from pathlib import Path

import pytest
from scripts.reproduce_benchmarks import ROOT, reproduce


def test_public_tables_reproduce_offline_without_modifying_evidence(tmp_path: Path) -> None:
    evidence = ROOT / "benchmarks/evidence"
    before = {p: p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
    output = tmp_path / "tables"
    reproduce(evidence, output)
    for table in output.iterdir():
        assert table.read_bytes() == (ROOT / "benchmarks" / table.name).read_bytes()
    assert all(p.read_bytes() == content for p, content in before.items())


def test_public_evidence_excludes_response_content_and_telemetry() -> None:
    forbidden = {"answer_diagnostics", "trace_id", "content", "headers", "api_key"}

    def check(value: object) -> None:
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            if "text" in value:
                assert type(value["text"]) is bool  # Saved verdict, never response text.
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    for path in (ROOT / "benchmarks/evidence").rglob("*"):
        if path.suffix == ".jsonl":
            for line in path.read_text().splitlines():
                check(json.loads(line))
        elif path.suffix == ".json":
            check(json.loads(path.read_text()))


@pytest.mark.parametrize("rehash", [False, True])
def test_modified_evidence_is_rejected_before_export(tmp_path: Path, rehash: bool) -> None:
    evidence = tmp_path / "evidence"
    shutil.copytree(ROOT / "benchmarks/evidence", evidence)
    relative = "openai-validation-01/calls.jsonl"
    path = evidence / relative
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["cost_usd"] += 1
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if rehash:
        index_path = evidence / "sha256.json"
        index = json.loads(index_path.read_text())
        index[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        index_path.write_text(json.dumps(index))
    output = tmp_path / "invalid"
    with pytest.raises(ValueError):
        reproduce(evidence, output)
    assert not output.exists()
