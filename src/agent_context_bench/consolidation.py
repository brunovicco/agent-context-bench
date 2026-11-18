"""Offline status CSV export; each run keeps its own baseline and provenance."""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agent_context_bench.contracts import validate_quality_counts, validate_v4_run
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
V4_COLUMNS = (
    "schema_version",
    "evaluator_version",
    "evaluator_sha256",
    "status_evaluator_sha256",
    "scenario",
    "task_schedule",
    "notes_format",
    "baseline_strategy",
    "task_model",
    "delegate_model",
    "notes_model",
    "renderer",
    "local_status_updates",
    "task_savings_vs_cache_fraction",
    "main_cost_usd",
    "subagent_cost_usd",
    "main_known_input_tokens",
    "subagent_known_input_tokens",
)


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
    modern = False
    for directory in directories:
        for filename in ("manifest.json", "calls.jsonl", "sessions.jsonl"):
            if not (directory / filename).is_file():
                raise FileNotFoundError("Missing required result file")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        calls = read_rows(directory / "calls.jsonl")
        sessions = read_rows(directory / "sessions.jsonl")
        observations = read_rows(directory / "observations.jsonl")
        schema = manifest["schema_version"]
        if schema == 4:
            validate_v4_run(manifest, calls, sessions, observations)
            modern = True
        else:
            validate_status_run(manifest, calls, sessions)
            if observations:
                raise ValueError("Legacy runs cannot contain local observations")
        config = manifest["config"]
        reports = report_rows(calls, sessions, manifest, observations)
        for row in reports:
            row.update(
                schema_version=schema,
                provider=config["provider"],
                source_run=directory.name,
                started_at=manifest["started_at"],
                model=config["model"],
                status_model=config["subagent"]["model"]
                if row["strategy"] == "status_notes_small_model"
                else config["model"],
                status_prompt_sha256=manifest.get("status_prompt_sha256"),
                scenario_sha256=manifest["scenario_sha256"],
                status_updates_per_turn=config["status_updates_per_turn"],
            )
            if schema == 4:
                row.update(
                    status_model=manifest["condition_specs"][row["strategy"]]["status_model"],
                    schema_version=4,
                    evaluator_version=manifest["evaluator_version"],
                )
            rows.append(row)
    columns = (*STATUS_COLUMNS, *V4_COLUMNS) if modern else STATUS_COLUMNS
    with output.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {key: "null" if row.get(key) is None else row[key] for key in columns} for row in rows
        )
    return len(rows)
