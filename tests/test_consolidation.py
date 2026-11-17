import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_context_bench.cli import main
from agent_context_bench.consolidation import consolidate_status
from agent_context_bench.models import Message, Reply, SubagentConfig
from agent_context_bench.runner import read_rows, run
from agent_context_bench.telemetry import Telemetry
from tests.test_status_updates import StatusProvider, config


def make_run(path: Path, updates: int = 1, provider: str = "openai") -> Path:
    assert run(
        config(status_updates_per_turn=updates, provider=provider),
        StatusProvider(),
        path,
        Telemetry(),
        demo=False,
        subagent_provider=StatusProvider("small"),
    )
    return path


def rewrite(path: Path, value: object) -> None:
    if path.suffix == ".jsonl":
        assert isinstance(value, list)
        path.write_text("".join(json.dumps(row) + "\n" for row in value))
    else:
        path.write_text(json.dumps(value))


def test_offline_export_preserves_sources_and_keeps_each_run_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = make_run(tmp_path / "one")
    second = make_run(tmp_path / "three", 3, "anthropic")
    # Different prompt revisions must retain their identities, never be pooled.
    manifest = json.loads((second / "manifest.json").read_text())
    manifest["status_prompt_sha256"] = "a" * 64
    rewrite(second / "manifest.json", manifest)
    before = {p: p.read_bytes() for d in (first, second) for p in d.iterdir()}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline command instantiated an API client")

    monkeypatch.setattr("agent_context_bench.cli.LiveProvider", forbidden)
    output = tmp_path / "combined.csv"
    assert main(["consolidate", str(first), str(second), "--output", str(output)]) == 0
    assert "Consolidated 6 conditions" in capsys.readouterr().out
    assert all(p.read_bytes() == content for p, content in before.items())
    assert b"\r" not in output.read_bytes() and b"PRIVATE" not in output.read_bytes()
    with output.open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 6
    for directory, updates in ((first, 1), (second, 3)):
        selected = [r for r in rows if r["source_run"] == directory.name]
        baseline = next(r for r in selected if r["strategy"] == "status_full_history")
        for row in selected:
            assert int(row["status_updates_per_turn"]) == updates
            assert int(row["status_updates_passed"]) == 5 * updates
            assert float(row["session_savings_fraction"]) == pytest.approx(
                1 - float(row["total_cost_usd"]) / float(baseline["total_cost_usd"])
            )
            if directory == second:
                assert row["provider"] == "anthropic"
                assert row["status_prompt_sha256"] == "a" * 64
    again = tmp_path / "again.csv"
    consolidate_status([first, second], again)
    assert again.read_bytes() == output.read_bytes()


@pytest.mark.parametrize("filename", ["manifest.json", "calls.jsonl", "sessions.jsonl"])
def test_missing_source_fails_before_output(tmp_path: Path, filename: str) -> None:
    directory = make_run(tmp_path / "source")
    (directory / filename).unlink()
    output = tmp_path / "missing.csv"
    with pytest.raises(FileNotFoundError):
        consolidate_status([directory], output)
    assert not output.exists()


@pytest.mark.parametrize(
    "damage",
    [
        "interrupted",
        "demo",
        "repetitions",
        "duplicate_session",
        "incomplete_session",
        "missing_call",
        "duplicate_call",
        "orphan",
        "cross_strategy",
        "incomplete_call",
    ],
)
def test_invalid_schedule_never_exports_even_when_earlier_source_is_valid(
    tmp_path: Path, damage: str
) -> None:
    valid = make_run(tmp_path / "valid")
    directory = make_run(tmp_path / "damaged")
    manifest = json.loads((directory / "manifest.json").read_text())
    calls = read_rows(directory / "calls.jsonl")
    sessions = read_rows(directory / "sessions.jsonl")
    if damage == "interrupted":
        manifest["status"] = "interrupted"
    elif damage == "demo":
        manifest["synthetic"] = True
    elif damage == "repetitions":
        manifest["config"]["repetitions"] = 2
    elif damage == "duplicate_session":
        sessions[1]["session_id"] = sessions[0]["session_id"]
    elif damage == "incomplete_session":
        sessions[0]["status"] = "incomplete"
    elif damage == "missing_call":
        calls.pop()
    elif damage == "duplicate_call":
        calls[-1] = calls[0]
    elif damage == "orphan":
        calls[0]["session_id"] = "unknown"
    elif damage == "cross_strategy":
        calls[0]["strategy"] = "status_full_history"
    else:
        calls[0]["status"] = "incomplete"
    rewrite(directory / "manifest.json", manifest)
    rewrite(directory / "calls.jsonl", calls)
    rewrite(directory / "sessions.jsonl", sessions)
    output = tmp_path / "invalid.csv"
    with pytest.raises(ValueError):
        consolidate_status([valid, directory], output)
    assert not output.exists()


def test_unknown_costs_stay_null_and_failed_quality_does_not_claim_savings(tmp_path: Path) -> None:
    directory = tmp_path / "unknown"
    settings = config(subagent=SubagentConfig("small"))
    assert run(
        settings,
        StatusProvider(),
        directory,
        Telemetry(),
        demo=False,
        subagent_provider=StatusProvider("small"),
    )
    output = tmp_path / "unknown.csv"
    consolidate_status([directory], output)
    with output.open() as stream:
        cheap = next(
            r for r in csv.DictReader(stream) if r["strategy"] == "status_notes_small_model"
        )
    assert cheap["status_cost_usd"] == cheap["total_cost_usd"] == "null"
    assert cheap["session_savings_fraction"] == "null"
    assert float(cheap["notes_cost_usd"]) > 0

    class InvalidNotes(StatusProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            reply = super().generate(system, messages, cache=cache)
            return (
                replace(reply, text=reply.text + " word" * 201) if ":notes\n" in system else reply
            )

    failed = tmp_path / "failed-quality"
    assert run(
        config(),
        InvalidNotes(),
        failed,
        Telemetry(),
        demo=False,
        subagent_provider=StatusProvider("small"),
    )
    output = tmp_path / "failed-quality.csv"
    consolidate_status([failed], output)
    with output.open() as stream:
        row = next(r for r in csv.DictReader(stream) if r["strategy"] == "status_notes_same_model")
    assert row["passed"] == "0" and row["task_passed"] == "1"
    assert row["session_savings_fraction"] == row["cost_per_passed_task_usd"] == "null"


def test_duplicate_inputs_and_existing_output_are_rejected_without_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = make_run(tmp_path / "source")
    output = tmp_path / "existing.csv"
    output.write_bytes(b"keep\n")
    with pytest.raises(ValueError):
        consolidate_status([directory, directory / "."], tmp_path / "duplicate.csv")
    with pytest.raises(ValueError):
        consolidate_status([], tmp_path / "empty.csv")
    assert main(["consolidate", str(directory), "--output", str(output)]) == 1
    assert output.read_bytes() == b"keep\n"
    assert "FileExistsError" in capsys.readouterr().err


@pytest.mark.parametrize(
    "damage",
    [
        "task_passed",
        "notes_passed",
        "status_updates_passed",
        "passed",
        "integer_flag",
        "status_check",
        "note_check",
        "missing_checks",
        "empty_checks",
        "nonboolean_checks",
        "empty_final_checks",
        "later_malformed_note",
    ],
)
def test_inconsistent_quality_metadata_is_rejected(tmp_path: Path, damage: str) -> None:
    directory = make_run(tmp_path / "source")
    calls = read_rows(directory / "calls.jsonl")
    sessions = read_rows(directory / "sessions.jsonl")
    session = next(s for s in sessions if s["strategy"] == "status_notes_same_model")
    selected = [c for c in calls if c["session_id"] == session["session_id"]]
    statuses = [c for c in selected if c["operation"] == "status"]
    notes = [c for c in selected if c["operation"] == "status_notes"]
    if damage in ("task_passed", "notes_passed", "passed"):
        session[damage] = False
    elif damage == "status_updates_passed":
        session[damage] = 500  # Must not export a pass rate greater than one.
    elif damage == "integer_flag":
        session["passed"] = 1
    elif damage == "status_check":
        statuses[0]["checks"]["approval_required"] = False
    elif damage == "note_check":
        notes[0]["checks"]["word_limit"] = False
    elif damage == "missing_checks":
        del statuses[0]["checks"]
    elif damage == "empty_checks":
        statuses[0]["checks"] = {}
    elif damage == "nonboolean_checks":
        statuses[0]["checks"]["text"] = "PRIVATE"
    elif damage == "empty_final_checks":
        session["checks"] = {}
    else:
        notes[0]["checks"]["word_limit"] = False
        notes[1]["checks"] = {}
        session.update(notes_passed=False, passed=False)
    rewrite(directory / "calls.jsonl", calls)
    rewrite(directory / "sessions.jsonl", sessions)
    before = {p: p.read_bytes() for p in directory.iterdir()}
    output = tmp_path / "invalid.csv"
    with pytest.raises(ValueError):
        consolidate_status([directory], output)
    assert not output.exists()
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("failed", ["task", "status"])
def test_consistent_failed_checks_export_without_savings(tmp_path: Path, failed: str) -> None:
    directory = make_run(tmp_path / "source")
    calls = read_rows(directory / "calls.jsonl")
    sessions = read_rows(directory / "sessions.jsonl")
    session = next(s for s in sessions if s["strategy"] == "status_notes_same_model")
    session["passed"] = False
    if failed == "task":
        session["checks"]["cause"] = False
        session["task_passed"] = False
    else:
        call = next(
            c
            for c in calls
            if c["session_id"] == session["session_id"] and c["operation"] == "status"
        )
        call["checks"]["approval_required"] = False
        session["status_updates_passed"] -= 1
    rewrite(directory / "calls.jsonl", calls)
    rewrite(directory / "sessions.jsonl", sessions)
    output = tmp_path / "failed.csv"
    consolidate_status([directory], output)
    with output.open() as stream:
        row = next(r for r in csv.DictReader(stream) if r["strategy"] == "status_notes_same_model")
    assert row["passed"] == "0" and row["session_savings_fraction"] == "null"
    assert row["status_updates_passed"] == ("4" if failed == "status" else "5")
    assert row["task_passed"] == ("0" if failed == "task" else "1")
