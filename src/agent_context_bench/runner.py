"""Sequential controlled replay with incremental, metadata-only result files."""

import csv
import json
import random
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from agent_context_bench.models import Config, Message, Reply, Strategy
from agent_context_bench.providers import Provider
from agent_context_bench.scenario import (
    TURNS,
    evaluate,
    scenario_digest,
    summary_request,
    system_prompt,
    turn_text,
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
) -> Reply:
    """Record observed usage on success or unknown accounting on failure."""
    started = perf_counter()
    row = {**metadata, "synthetic": demo, "cache_requested": cache}
    try:
        with telemetry.span(str(metadata["operation"]), str(metadata["session_id"])) as trace:
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
    append_json(directory / "calls.jsonl", row)
    return reply


def run(
    config: Config,
    provider: Provider,
    directory: Path,
    telemetry: Telemetry,
    *,
    demo: bool,
) -> bool:
    """Run paired randomized blocks; stop on API errors and retain partial results."""
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "synthetic": demo,
        "started_at": datetime.now(UTC).isoformat(),
        "config": asdict(config),
        "scenario_sha256": scenario_digest(),
        "sdk_versions": {name: version(name) for name in ("openai", "anthropic")},
        "strategy_order": [],
        "status": "running",
    }
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
                run_session(config, provider, directory, telemetry, strategy, repetition, demo=demo)
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
) -> None:
    """Replace old turns once with a paid summary, preserving only observed evidence."""
    session_id = uuid4().hex
    system = system_prompt(session_id)
    messages: list[Message] = []
    checks: dict[str, bool] = {}
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
            messages.append(Message("user", turn_text(index)))
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
            if index == len(TURNS) - 1:
                checks = evaluate(reply.text)
        status = "completed"
    finally:
        append_json(
            directory / "sessions.jsonl",
            {
                **base,
                "synthetic": demo,
                "status": status,
                "checks": checks,
                "passed": status == "completed" and bool(checks) and all(checks.values()),
                "elapsed_ms": (perf_counter() - started) * 1000,
            },
        )


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


def write_report(directory: Path) -> list[dict[str, Any]]:
    """Aggregate this run only; missing costs never become zero or disappear."""
    calls = read_rows(directory / "calls.jsonl")
    sessions = read_rows(directory / "sessions.jsonl")
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
            }
        )
    if rows:
        with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return rows
