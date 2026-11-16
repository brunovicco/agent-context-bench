"""Sequential controlled replay with incremental, metadata-only result files."""

import csv
import json
import random
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from agent_context_bench.models import (
    DELEGATION_STRATEGIES,
    SMALL_MODEL_STRATEGIES,
    STATUS_STRATEGIES,
    Config,
    Message,
    Reply,
    Strategy,
)
from agent_context_bench.providers import Provider
from agent_context_bench.scenario import (
    DELEGATE_HANDOFF,
    TURNS,
    answer_diagnostics,
    delegate_system_prompt,
    delegation_prompt_digest,
    evaluate,
    scenario_digest,
    summary_request,
    system_prompt,
    turn_text,
)
from agent_context_bench.status_updates import (
    NOTES_SYSTEM,
    STATUS_SYSTEM,
    evaluate_notes,
    evaluate_status,
    notes_request,
    notes_text_diagnostics,
    status_prompt_digest,
    status_request,
    status_text_diagnostics,
)
from agent_context_bench.telemetry import Telemetry


def append_json(path: Path, row: dict[str, Any]) -> None:
    """Persist each completed event; no all-or-nothing result buffer."""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")


def numeric_usage(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep numeric provider accounting, never arbitrary future string fields."""
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = value
        elif isinstance(value, dict):
            result[key] = numeric_usage(value)
    return result


def record_call(
    provider: Provider,
    config: Config,
    telemetry: Telemetry,
    directory: Path,
    metadata: dict[str, Any],
    system: str,
    messages: list[Message],
    *,
    cache: bool,
    demo: bool,
    evaluator: Callable[[str], dict[str, bool]] | None = None,
) -> Reply:
    """Record observed usage on success or unknown accounting on failure."""
    started = perf_counter()
    row = {
        "agent_role": "main",
        **metadata,
        "requested_model": config.model,
        "synthetic": demo,
        "cache_requested": cache,
    }
    try:
        with telemetry.span(
            str(metadata["operation"]), str(metadata["session_id"]), str(row["agent_role"])
        ) as trace:
            row["trace_id"] = trace
            reply = provider.generate(system, messages, cache=cache)
    except (Exception, KeyboardInterrupt) as exc:
        row.update(
            status="error",
            error_type=type(exc).__name__,
            usage=None,
            cost_usd=None,
            latency_ms=(perf_counter() - started) * 1000,
        )
        append_json(directory / "calls.jsonl", row)
        raise
    row.update(
        status="ok" if reply.complete else "incomplete",
        model=reply.model,
        usage=asdict(reply.usage),
        raw_usage=numeric_usage(reply.raw_usage),
        latency_ms=reply.latency_ms,
        ttft_text_ms=reply.ttft_text_ms,
        request_bytes=reply.request_bytes,
        cost_usd=None if demo else reply.usage.cost(config.rates),
        cache_control_valid=cache or (reply.usage.cache_read == reply.usage.cache_write == 0),
    )
    if evaluator is not None:
        row["checks"] = evaluator(reply.text)
    if metadata["operation"] == "status":
        row["status_text_diagnostics"] = status_text_diagnostics(reply.text)
    elif metadata["operation"] == "status_notes":
        row["notes_text_diagnostics"] = notes_text_diagnostics(reply.text)
    append_json(directory / "calls.jsonl", row)
    return reply


def run(
    config: Config,
    provider: Provider,
    directory: Path,
    telemetry: Telemetry,
    *,
    demo: bool,
    subagent_provider: Provider | None = None,
    diagnostics: bool = False,
) -> bool:
    """Run paired randomized blocks; stop on API errors and retain partial results."""
    if any(s in SMALL_MODEL_STRATEGIES for s in config.strategies) and subagent_provider is None:
        raise ValueError("Missing subagent provider")
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 3,
        "diagnostics_enabled": diagnostics,
        "package_version": version("agent-context-bench"),
        "synthetic": demo,
        "started_at": datetime.now(UTC).isoformat(),
        "config": asdict(config),
        "scenario_sha256": scenario_digest(),
        "sdk_versions": {name: version(name) for name in ("openai", "anthropic")},
        "strategy_order": [],
        "status": "running",
    }
    if any(s in DELEGATION_STRATEGIES for s in config.strategies):
        manifest["delegation_prompt_sha256"] = delegation_prompt_digest()
    if any(s in STATUS_STRATEGIES for s in config.strategies):
        manifest["status_prompt_sha256"] = status_prompt_digest()
        manifest["status_schedule"] = "after_each_of_five_evidence_turns_no_wall_clock_wait"
        manifest["status_updates_per_turn"] = config.status_updates_per_turn
    order_log: list[list[str]] = []
    manifest["strategy_order"] = order_log
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Reproducible experiment ordering; this PRNG never protects secrets.
    rng = random.Random(config.seed)  # noqa: S311  # nosec B311
    completed = False
    try:
        for repetition in range(config.repetitions):
            order = list(config.strategies)
            rng.shuffle(order)
            order_log.append(list(order))
            for strategy in order:
                run_session(
                    config,
                    provider,
                    directory,
                    telemetry,
                    strategy,
                    repetition,
                    demo=demo,
                    subagent_provider=subagent_provider,
                    diagnostics=diagnostics,
                )
        completed = True
        manifest["status"] = "completed"
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error_type"] = type(exc).__name__
    finally:
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        write_report(directory)
    return completed


def run_session(
    config: Config,
    provider: Provider,
    directory: Path,
    telemetry: Telemetry,
    strategy: Strategy,
    repetition: int,
    *,
    demo: bool,
    subagent_provider: Provider | None = None,
    diagnostics: bool = False,
) -> None:
    """Replay six turns with full history, one summary, or one isolated evidence worker."""
    session_id = uuid4().hex
    system = system_prompt(session_id)
    messages: list[Message] = []
    delegated = strategy in DELEGATION_STRATEGIES
    worker = provider
    worker_config = config
    if strategy == "delegate_small_model":
        if subagent_provider is None:
            raise ValueError("Missing subagent provider")
        worker = subagent_provider
        worker_config = config.worker_config()
    worker_messages: list[Message] = []
    worker_system = delegate_system_prompt(session_id)
    checks: dict[str, bool] = {}
    diagnostic_fields: dict[str, object] = {}
    observing = strategy in STATUS_STRATEGIES
    notes = ""
    notes_passed = True
    status_checks: list[dict[str, bool]] = []
    started = perf_counter()
    base = {
        "session_id": session_id,
        "provider": config.provider,
        "requested_model": config.model,
        "strategy": strategy,
        "repetition": repetition,
    }
    status = "error"
    try:
        for index in range(len(TURNS)):
            if strategy == "summary" and index == config.summary_after:
                summary = record_call(
                    provider,
                    config,
                    telemetry,
                    directory,
                    {**base, "turn": index, "operation": "summary"},
                    system,
                    summary_request(messages),
                    cache=True,
                    demo=demo,
                )
                if not summary.complete or not summary.text.strip():
                    status = "incomplete"
                    return
                messages = [
                    Message(
                        "user", "Continue from this summary of earlier evidence:\n" + summary.text
                    ),
                    Message("assistant", "I will preserve these facts and constraints."),
                ]
            incoming = turn_text(index)
            if delegated and index < len(TURNS) - 1:
                # A single stateful worker sees only evidence already released.
                # The main agent never receives the raw diagnostic records.
                worker_messages.append(Message("user", incoming))
                evidence = record_call(
                    worker,
                    worker_config,
                    telemetry,
                    directory,
                    {**base, "turn": index, "operation": "delegate", "agent_role": "subagent"},
                    worker_system,
                    worker_messages,
                    cache=True,
                    demo=demo,
                )
                if not evidence.complete or not evidence.text.strip():
                    status = "incomplete"
                    return
                worker_messages.append(Message("assistant", evidence.text))
                incoming = DELEGATE_HANDOFF + evidence.text
            messages.append(Message("user", incoming))
            reply = record_call(
                provider,
                config,
                telemetry,
                directory,
                {**base, "turn": index, "operation": "task"},
                system,
                messages,
                cache=strategy != "full",
                demo=demo,
            )
            if not reply.complete or not reply.text.strip():
                status = "incomplete"
                return
            messages.append(Message("assistant", reply.text))
            if observing and index < len(TURNS) - 1:
                notes, notes_ok, complete = observe_status(
                    config,
                    provider,
                    subagent_provider,
                    directory,
                    telemetry,
                    base,
                    index,
                    messages,
                    notes,
                    status_checks,
                    demo=demo,
                )
                notes_passed = notes_passed and notes_ok
                if not complete:
                    status = "incomplete"
                    return
            if index == len(TURNS) - 1:
                checks = evaluate(reply.text)
                if diagnostics:
                    diagnostic_fields["answer_diagnostics"] = answer_diagnostics(reply.text)
        status = "completed"
    finally:
        task_passed = status == "completed" and bool(checks) and all(checks.values())
        updates_expected = 5 * config.status_updates_per_turn if observing else 0
        updates_passed = sum(all(c.values()) for c in status_checks)
        append_json(
            directory / "sessions.jsonl",
            {
                **base,
                "synthetic": demo,
                "status": status,
                "checks": checks,
                **diagnostic_fields,
                "passed": task_passed and notes_passed and updates_passed == updates_expected,
                "task_passed": task_passed,
                "status_updates_expected": updates_expected,
                "status_updates_passed": updates_passed,
                "notes_passed": notes_passed,
                "elapsed_ms": (perf_counter() - started) * 1000,
            },
        )


def observe_status(
    config: Config,
    provider: Provider,
    small_provider: Provider | None,
    directory: Path,
    telemetry: Telemetry,
    base: dict[str, Any],
    turn: int,
    history: list[Message],
    previous_notes: str,
    checks: list[dict[str, bool]],
    *,
    demo: bool,
) -> tuple[str, bool, bool]:
    """Bill note maintenance and status calls; never feed either back into the task."""
    notes = previous_notes
    notes_ok = True
    shortened = base["strategy"] != "status_full_history"
    if shortened:
        note_reply = record_call(
            provider,
            config,
            telemetry,
            directory,
            {**base, "turn": turn, "operation": "status_notes", "agent_role": "notes"},
            f"Synthetic experiment block: {base['session_id']}:notes\n{NOTES_SYSTEM}",
            notes_request(notes, history[-2:]),
            cache=True,
            demo=demo,
            evaluator=evaluate_notes,
        )
        notes_ok = all(evaluate_notes(note_reply.text).values())
        if not note_reply.complete or not note_reply.text.strip():
            return notes, False, False
        notes = note_reply.text
    renderer = provider
    renderer_config = config
    if base["strategy"] == "status_notes_small_model":
        if small_provider is None:
            raise ValueError("Missing status renderer provider")
        renderer = small_provider
        renderer_config = config.worker_config()
    for tick in range(config.status_updates_per_turn):
        response = record_call(
            renderer,
            renderer_config,
            telemetry,
            directory,
            {**base, "turn": turn, "update": tick, "operation": "status", "agent_role": "status"},
            f"Synthetic experiment block: {base['session_id']}:status\n{STATUS_SYSTEM}",
            status_request(history, notes if shortened else None),
            cache=True,
            demo=demo,
            evaluator=lambda text: evaluate_status(text, turn),
        )
        if not response.complete or not response.text.strip():
            return notes, notes_ok, False
        checks.append(evaluate_status(response.text, turn))
    return notes, notes_ok, True


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Load completed JSONL events from a single run."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def percentile(values: list[float], fraction: float) -> float | None:
    """Interpolate empirical quantiles; callers must report sample counts."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def role_metrics(calls: list[dict[str, Any]], role: str) -> dict[str, Any]:
    """Split observed usage and costs without turning unpriced worker calls into zero."""
    selected = [c for c in calls if c.get("agent_role", "main") == role]
    usage = [c["usage"] for c in selected if c.get("usage") is not None]
    total_input = sum(u["ordinary"] + u["cache_read"] + u["cache_write"] for u in usage)
    costs = [c.get("cost_usd") for c in selected]
    return {
        f"{role}_calls": len(selected),
        f"{role}_known_input_tokens": total_input,
        f"{role}_known_output_tokens": sum(u["output"] for u in usage),
        f"{role}_usage_complete": len(usage) == len(selected),
        f"{role}_cache_read_fraction": sum(u["cache_read"] for u in usage) / total_input
        if total_input
        else None,
        f"{role}_cost_usd": sum(c for c in costs if c is not None)
        if all(c is not None for c in costs)
        else None,
    }


def operation_cost(calls: list[dict[str, Any]], operations: tuple[str, ...]) -> float | None:
    """Return zero for absent operations and null for any unpriced attempt."""
    costs = [c.get("cost_usd") for c in calls if c["operation"] in operations]
    return sum(c for c in costs if c is not None) if all(c is not None for c in costs) else None


def status_metrics(
    calls: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    total: float | None,
) -> dict[str, Any]:
    """Separate rendering from note costs and include failed observations in quality counts."""
    updates = [c for c in calls if c["operation"] == "status"]
    successful = [c for c in updates if c["status"] == "ok"]
    expected = sum(s.get("status_updates_expected", 0) for s in sessions)
    passed = sum(s.get("status_updates_passed", 0) for s in sessions)
    rendered_cost = operation_cost(calls, ("status",))
    pipeline = operation_cost(calls, ("status_notes", "status"))
    return {
        "task_passed": sum(s.get("task_passed", s["passed"]) for s in sessions),
        "status_updates_expected": expected,
        "status_updates_passed": passed,
        "status_pass_rate": passed / expected if expected else None,
        "workload_cost_usd": operation_cost(calls, ("task",)),
        "status_pipeline_cost_usd": pipeline,
        "status_pipeline_cost_mean_usd": pipeline / len(sessions) if pipeline is not None else None,
        "status_update_cost_mean_usd": rendered_cost / len(updates)
        if rendered_cost is not None and updates
        else None,
        "status_overhead_fraction": pipeline / total
        if pipeline is not None and total is not None and total > 0
        else None,
        "session_cost_mean_usd": total / len(sessions) if total is not None else None,
        "status_latency_samples": len(successful),
        "status_latency_p50_ms": percentile([c["latency_ms"] for c in successful], 0.5),
        "status_latency_p95_ms": percentile([c["latency_ms"] for c in successful], 0.95),
    }


def write_report(directory: Path) -> list[dict[str, Any]]:
    """Aggregate this run only; missing costs never become zero or disappear."""
    calls = read_rows(directory / "calls.jsonl")
    sessions = read_rows(directory / "sessions.jsonl")
    manifest_path = directory / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    planned_sessions = manifest.get("config", {}).get("repetitions")
    rows: list[dict[str, Any]] = []
    for strategy in sorted({s["strategy"] for s in sessions}):
        selected = [c for c in calls if c["strategy"] == strategy]
        tasks = [s for s in sessions if s["strategy"] == strategy]
        passed = sum(bool(s["passed"]) for s in tasks)
        costs = [c.get("cost_usd") for c in selected]
        known = bool(costs) and all(c is not None for c in costs)
        total_cost = sum(c for c in costs if c is not None) if known else None
        usage = [c["usage"] for c in selected if c.get("usage") is not None]
        input_count = sum(u["ordinary"] + u["cache_read"] + u["cache_write"] for u in usage)
        task_calls = [c for c in selected if c["operation"] == "task" and c["status"] == "ok"]
        ttfts = [c["ttft_text_ms"] for c in task_calls if c.get("ttft_text_ms") is not None]
        rows.append(
            {
                "strategy": strategy,
                "synthetic": tasks[0]["synthetic"],
                "sessions": len(tasks),
                "passed": passed,
                "pass_rate": passed / len(tasks),
                "calls": len(selected),
                "task_latency_samples": len(task_calls),
                "task_latency_p50_ms": percentile([c["latency_ms"] for c in task_calls], 0.5),
                "task_latency_p95_ms": percentile([c["latency_ms"] for c in task_calls], 0.95),
                "ttft_text_p50_ms": percentile(ttfts, 0.5),
                "session_elapsed_p50_ms": percentile([s["elapsed_ms"] for s in tasks], 0.5),
                "known_input_tokens": input_count,
                "cache_read_fraction": sum(u["cache_read"] for u in usage) / input_count
                if input_count
                else None,
                "request_bytes": sum(c.get("request_bytes", 0) for c in selected),
                "usage_complete": len(usage) == len(selected),
                "cache_control_valid": all(c.get("cache_control_valid", False) for c in selected),
                "total_cost_usd": total_cost,
                "summary_cost_usd": sum(
                    c["cost_usd"] for c in selected if c["operation"] == "summary"
                )
                if known
                else None,
                "cost_per_passed_task_usd": total_cost / passed
                if total_cost is not None and passed
                else None,
                **role_metrics(selected, "main"),
                **role_metrics(selected, "subagent"),
                **role_metrics(selected, "notes"),
                **role_metrics(selected, "status"),
                **status_metrics(selected, tasks, total_cost),
            }
        )
    baseline = next((row for row in rows if row["strategy"] == "status_full_history"), None)
    for row in rows:
        comparable = (
            manifest.get("status") == "completed"
            and baseline is not None
            and row["strategy"] in STATUS_STRATEGIES
            and row["sessions"] == baseline["sessions"] == planned_sessions
            and row["passed"] == row["sessions"]
            and baseline["passed"] == baseline["sessions"]
            and row["status_updates_expected"] == baseline["status_updates_expected"]
        )
        for name, metric in (
            ("status_update_savings_fraction", "status_update_cost_mean_usd"),
            ("status_pipeline_savings_fraction", "status_pipeline_cost_mean_usd"),
            ("session_savings_fraction", "session_cost_mean_usd"),
        ):
            reference = baseline[metric] if baseline is not None else None
            value = row[metric]
            row[name] = (
                1 - value / reference
                if comparable and reference is not None and reference > 0 and value is not None
                else None
            )
    if rows:
        with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    return rows
