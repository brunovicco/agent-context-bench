"""Offline status CSV export; each run keeps its own baseline and provenance."""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agent_context_bench.models import STATUS_STRATEGIES
from agent_context_bench.runner import read_rows, report_rows

STATUS_COLUMNS = (
    "provider",
    "source_run",
    "strategy",
    "started_at",
    "model",
    "status_model",
    "status_prompt_sha256",
    "scenario_sha256",
    "status_updates_per_turn",
    "sessions",
    "passed",
    "task_passed",
    "status_updates_expected",
    "status_updates_passed",
    "calls",
    "notes_calls",
    "usage_complete",
    "cache_control_valid",
    "workload_cost_usd",
    "notes_cost_usd",
    "status_cost_usd",
    "status_pipeline_cost_usd",
    "total_cost_usd",
    "session_cost_mean_usd",
    "cost_per_passed_task_usd",
    "status_update_cost_mean_usd",
    "status_update_savings_fraction",
    "status_pipeline_savings_fraction",
    "session_savings_fraction",
    "known_input_tokens",
    "cache_read_fraction",
    "status_known_input_tokens",
    "status_known_output_tokens",
    "status_cache_read_fraction",
    "notes_known_input_tokens",
    "notes_known_output_tokens",
    "notes_cache_read_fraction",
    "session_elapsed_p50_ms",
    "status_latency_p50_ms",
)


def checks_passed(checks: object) -> bool:
    """Read a recorded verdict only from a nonempty dictionary of boolean checks."""
    if (
        not isinstance(checks, dict)
        or not checks
        or any(type(value) is not bool for value in checks.values())
    ):
        raise ValueError("Missing or malformed recorded checks")
    return all(checks.values())


def validate_quality_counts(calls: list[dict[str, Any]], sessions: list[dict[str, Any]]) -> None:
    """Cross-check saved aggregates without reevaluating discarded response text."""
    for session in sessions:
        selected = [call for call in calls if call["session_id"] == session["session_id"]]
        status_passed = sum(
            checks_passed(call.get("checks")) for call in selected if call["operation"] == "status"
        )
        # Evaluate every note's metadata, even after a prior note failed.
        note_verdicts = [
            checks_passed(call.get("checks"))
            for call in selected
            if call["operation"] == "status_notes"
        ]
        notes_passed = all(note_verdicts)
        task_passed = checks_passed(session.get("checks"))
        expected = {
            "task_passed": task_passed,
            "notes_passed": notes_passed,
            "status_updates_passed": status_passed,
            "passed": task_passed
            and notes_passed
            and status_passed == session["status_updates_expected"],
        }
        if any(
            type(session.get(key)) is not type(value) or session[key] != value
            for key, value in expected.items()
        ):
            raise ValueError("Session quality counts disagree with recorded checks")


def validate_status_run(
    manifest: dict[str, Any], calls: list[dict[str, Any]], sessions: list[dict[str, Any]]
) -> None:
    """Reject incomplete schedules and broken call/session associations before export."""
    config = manifest["config"]
    repetitions = config["repetitions"]
    updates = config["status_updates_per_turn"]
    if (
        manifest["schema_version"] != 3
        or manifest["status"] != "completed"
        or manifest["synthetic"] is not False
        or set(config["strategies"]) != set(STATUS_STRATEGIES)
        or type(repetitions) is not int
        or repetitions < 1
        or type(updates) is not int
        or not 1 <= updates <= 20
        or manifest["status_updates_per_turn"] != updates
    ):
        raise ValueError("Expected a completed live status run")
    expected = Counter((s, r) for s in STATUS_STRATEGIES for r in range(repetitions))
    if Counter((s["strategy"], s["repetition"]) for s in sessions) != expected:
        raise ValueError("Incomplete or duplicate sessions")
    session_map = {s["session_id"]: s for s in sessions}
    if len(session_map) != len(sessions):
        raise ValueError("Duplicate session identifiers")
    expected_calls: Counter[tuple[str, str, int, int | None]] = Counter()
    for session in sessions:
        if (
            session["status"] != "completed"
            or session["synthetic"] is not False
            or session["provider"] != config["provider"]
            or session["status_updates_expected"] != 5 * updates
        ):
            raise ValueError("Incomplete session schedule")
        sid = session["session_id"]
        expected_calls.update((sid, "task", turn, None) for turn in range(6))
        expected_calls.update(
            (sid, "status", turn, tick) for turn in range(5) for tick in range(updates)
        )
        if session["strategy"] != "status_full_history":
            expected_calls.update((sid, "status_notes", turn, None) for turn in range(5))
    actual_calls: Counter[tuple[str, str, int, int | None]] = Counter()
    for call in calls:
        observed_session = session_map.get(call["session_id"])
        if (
            observed_session is None
            or call["strategy"] != observed_session["strategy"]
            or call["repetition"] != observed_session["repetition"]
            or call["provider"] != config["provider"]
            or call["synthetic"] is not False
            or call["status"] != "ok"
        ):
            raise ValueError("Invalid call association or incomplete response")
        actual_calls.update(
            [(call["session_id"], call["operation"], call["turn"], call.get("update"))]
        )
    if actual_calls != expected_calls:
        raise ValueError("Missing or duplicate scheduled calls")
    validate_quality_counts(calls, sessions)


def consolidate_status(directories: list[Path], output: Path) -> int:
    """Write a new CSV after validating all sources; never rewrite source reports."""
    if not directories or len({p.resolve() for p in directories}) != len(directories):
        raise ValueError("Provide distinct source runs")
    rows: list[dict[str, Any]] = []
    for directory in directories:
        for filename in ("manifest.json", "calls.jsonl", "sessions.jsonl"):
            if not (directory / filename).is_file():
                raise FileNotFoundError("Missing required result file")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        calls = read_rows(directory / "calls.jsonl")
        sessions = read_rows(directory / "sessions.jsonl")
        validate_status_run(manifest, calls, sessions)
        config = manifest["config"]
        for row in report_rows(calls, sessions, manifest):
            row.update(
                provider=config["provider"],
                source_run=directory.name,
                started_at=manifest["started_at"],
                model=config["model"],
                status_model=config["subagent"]["model"]
                if row["strategy"] == "status_notes_small_model"
                else config["model"],
                status_prompt_sha256=manifest["status_prompt_sha256"],
                scenario_sha256=manifest["scenario_sha256"],
                status_updates_per_turn=config["status_updates_per_turn"],
            )
            rows.append({key: "null" if row[key] is None else row[key] for key in STATUS_COLUMNS})
    with output.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=STATUS_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
