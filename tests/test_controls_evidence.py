import hashlib
import json
import shutil
from pathlib import Path

import pytest
from scripts.reproduce_controls import ROOT, reproduce


def test_controls_reproduce_without_api_clients_or_source_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline reproduction instantiated an API client")

    monkeypatch.setattr("agent_context_bench.providers.LiveProvider", forbidden)
    evidence = ROOT / "benchmarks/controls-evidence"
    before = {path: path.read_bytes() for path in evidence.rglob("*") if path.is_file()}
    output = tmp_path / "tables"
    reproduce(evidence, output)
    assert (output / "controls-validation.csv").read_bytes() == (
        ROOT / "benchmarks/controls-validation.csv"
    ).read_bytes()
    assert all(path.read_bytes() == contents for path, contents in before.items())


def test_controls_evidence_has_no_responses_diagnostics_or_telemetry() -> None:
    forbidden = {"answer_diagnostics", "trace_id", "content", "headers", "api_key", "text"}

    def check(value: object) -> None:
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    for path in (ROOT / "benchmarks/controls-evidence").rglob("*"):
        if path.suffix == ".jsonl":
            for line in path.read_text().splitlines():
                check(json.loads(line))
        elif path.suffix == ".json":
            check(json.loads(path.read_text()))


@pytest.mark.parametrize("damage", ["cost", "model", "schedule", "checksum", "population"])
def test_controls_tampering_is_rejected_before_export(tmp_path: Path, damage: str) -> None:
    evidence = tmp_path / "evidence"
    shutil.copytree(ROOT / "benchmarks/controls-evidence", evidence)
    name = "openai-controls-validation-20261118-01/calls.jsonl"
    path = evidence / name
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if damage in ("cost", "checksum"):
        rows[0]["cost_usd"] += 1
    elif damage == "model":
        rows[0]["model"] = "incorrect-model"
    elif damage == "schedule":
        rows.pop()
    else:
        name = "openai-controls-validation-20261118-01/manifest.json"
        path = evidence / name
        manifest = json.loads(path.read_text())
        manifest["config"]["scenario"] = "ambiguous"
    path.write_text(
        json.dumps(manifest)
        if damage == "population"
        else "".join(json.dumps(row) + "\n" for row in rows)
    )
    if damage != "checksum":
        index = json.loads((evidence / "sha256.json").read_text())
        index[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (evidence / "sha256.json").write_text(json.dumps(index))
    output = tmp_path / "invalid"
    with pytest.raises(ValueError):
        reproduce(evidence, output)
    assert not output.exists()
