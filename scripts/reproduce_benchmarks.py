"""Recalculate the published pilot tables from sanitized evidence, without API calls."""

import argparse
import csv
import hashlib
import json
import math
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from agent_context_bench.consolidation import consolidate_status
from agent_context_bench.models import Rates, Usage, normalize_usage
from agent_context_bench.runner import read_rows, report_rows

ROOT = Path(__file__).resolve().parents[1]
BASE_RUNS = ("openai-validation-01", "anthropic-validation-01")
DELEGATION_RUNS = ("openai-delegation-validation-01", "anthropic-delegation-validation-01")
STATUS_RUNS = (
    "openai-status-length-validation-01",
    "anthropic-status-length-validation-01",
    "openai-status-reuse3-03",
    "anthropic-status-reuse3-03",
)
DELEGATION_COLUMNS = (
    "provider",
    "source_run",
    "strategy",
    "sessions",
    "passed",
    "calls",
    "usage_complete",
    "cache_control_valid",
    "total_cost_usd",
    "cost_per_passed_task_usd",
    "session_elapsed_p50_ms",
    "known_input_tokens",
    "main_known_input_tokens",
    "subagent_known_input_tokens",
    "cache_read_fraction",
    "main_cost_usd",
    "subagent_cost_usd",
)
BASE_COLUMNS = (
    "provider",
    "source_run",
    "started_at",
    "scenario_sha256",
    "model",
    "strategy",
    "sessions",
    "passed",
    "calls",
    "usage_complete",
    "cache_control_valid",
    "total_cost_usd",
    "summary_cost_usd",
    "cost_per_passed_task_usd",
    "session_elapsed_p50_ms",
    "known_input_tokens",
    "cache_read_fraction",
    "savings_vs_full_fraction",
    "savings_vs_cache_fraction",
)


def audit_run(directory: Path) -> list[dict[str, Any]]:
    """Check each call's accounting and the completed session population before aggregation."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    calls = read_rows(directory / "calls.jsonl")
    sessions = read_rows(directory / "sessions.jsonl")
    expected = Counter((s, r) for s in config["strategies"] for r in range(config["repetitions"]))
    if (
        manifest["status"] != "completed"
        or manifest["synthetic"] is not False
        or Counter((s["strategy"], s["repetition"]) for s in sessions) != expected
        or not all(s["passed"] is True and all(s["checks"].values()) for s in sessions)
    ):
        raise ValueError("Published pilot must contain all passing planned sessions")
    for call in calls:
        small = call["requested_model"] != config["model"]
        model_config = config["subagent"] if small else config
        usage = Usage(**call["usage"])
        cost = usage.cost(Rates(**model_config["rates"]))
        if (
            call["status"] != "ok"
            or call["synthetic"] is not False
            or call["requested_model"] != model_config["model"]
            or usage != normalize_usage(config["provider"], call["raw_usage"])
            or cost is None
            or call["cost_usd"] is None
            or not math.isclose(cost, call["cost_usd"], rel_tol=1e-12, abs_tol=1e-12)
        ):
            raise ValueError("Published accounting does not match configured rates")
    rows = report_rows(calls, sessions, manifest)
    for row in rows:
        selected = [c for c in calls if c["strategy"] == row["strategy"]]
        expected_per_session = {"task": 6}
        if row["strategy"] == "summary":
            expected_per_session["summary"] = 1
        elif row["strategy"].startswith("delegate_"):
            expected_per_session["delegate"] = 5
        if not row["strategy"].startswith("status_"):
            for session in (s for s in sessions if s["strategy"] == row["strategy"]):
                observed = Counter(
                    c["operation"] for c in selected if c["session_id"] == session["session_id"]
                )
                if observed != expected_per_session:
                    raise ValueError("Incomplete published call schedule")
        row.update(
            provider=config["provider"],
            source_run=directory.name,
            started_at=manifest["started_at"],
            scenario_sha256=manifest["scenario_sha256"],
            model=config["model"],
        )
    return rows


def reproduce(evidence: Path, output: Path) -> None:
    """Verify checksums and write all three derived tables to a new directory."""
    checksums = json.loads((evidence / "sha256.json").read_text(encoding="utf-8"))
    runs = (*BASE_RUNS, *DELEGATION_RUNS, *STATUS_RUNS)
    required = {
        f"{run}/{file}"
        for run in runs
        for file in ("manifest.json", "calls.jsonl", "sessions.jsonl")
    }
    if set(checksums) != required:
        raise ValueError("Incomplete evidence index")
    for name in sorted(required):
        if hashlib.sha256((evidence / name).read_bytes()).hexdigest() != checksums[name]:
            raise ValueError("Evidence checksum mismatch")
    reports = {run: audit_run(evidence / run) for run in runs}
    output.mkdir(exist_ok=False)
    for filename, sources, columns in (
        ("cache-validation.csv", BASE_RUNS, BASE_COLUMNS),
        ("delegation-validation.csv", DELEGATION_RUNS, DELEGATION_COLUMNS),
    ):
        rows = []
        for run in sources:
            report = reports[run]
            for row in report:
                if sources == BASE_RUNS:
                    for baseline in ("full", "cache"):
                        control = next(r for r in report if r["strategy"] == baseline)
                        row[f"savings_vs_{baseline}_fraction"] = (
                            1 - row["total_cost_usd"] / control["total_cost_usd"]
                        )
                rows.append({k: "null" if row[k] is None else row[k] for k in columns})
        with (output / filename).open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    consolidate_status([evidence / run for run in STATUS_RUNS], output / "status-validation.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory for regenerated CSVs")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            output = args.output or Path(temporary) / "tables"
            reproduce(ROOT / "benchmarks/evidence", output)
            for name in (
                "cache-validation.csv",
                "delegation-validation.csv",
                "status-validation.csv",
            ):
                if (output / name).read_bytes() != (ROOT / "benchmarks" / name).read_bytes():
                    raise ValueError("Published table differs from evidence")
        print("Verified 8 runs: accounting, checksums and all 3 published CSVs match.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Reproduction failed ({type(exc).__name__}); inspect the local evidence and tables.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
