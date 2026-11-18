"""Reproduce the November 18 task controls from sanitized records without API calls."""

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path

from agent_context_bench.consolidation import consolidate_status
from agent_context_bench.contracts import validate_v4_run
from agent_context_bench.models import Config, Usage, normalize_usage
from agent_context_bench.runner import read_rows

ROOT = Path(__file__).resolve().parents[1]
RUNS = (
    "openai-controls-validation-20261118-01",
    "anthropic-controls-validation-20261118-01",
)


def reproduce(evidence: Path, output: Path) -> None:
    """Check source integrity, schedule, models and prices before writing a new CSV."""
    index = json.loads((evidence / "sha256.json").read_text(encoding="utf-8"))
    required = {
        f"{run}/{filename}"
        for run in RUNS
        for filename in ("manifest.json", "calls.jsonl", "sessions.jsonl")
    } | {"provenance.json"}
    if set(index) != required:
        raise ValueError("Incomplete controls evidence index")
    for name in sorted(required):
        if hashlib.sha256((evidence / name).read_bytes()).hexdigest() != index[name]:
            raise ValueError("Controls evidence checksum mismatch")
    identities = set()
    for run in RUNS:
        directory = evidence / run
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        calls = read_rows(directory / "calls.jsonl")
        sessions = read_rows(directory / "sessions.jsonl")
        validate_v4_run(manifest, calls, sessions, [])
        config = Config.from_dict(manifest["config"])
        if (
            config.scenario != "replay"
            or config.task_schedule != "every_turn"
            or set(config.strategies)
            != {"cache", "cache_small_model", "delegate_same_model", "delegate_small_model"}
            or config.repetitions != 3
            or len(calls) != 102
            or not all(session["passed"] for session in sessions)
        ):
            raise ValueError("Unexpected published controls population")
        identities.add((manifest["scenario_sha256"], manifest["evaluator_sha256"]))
        for call in calls:
            settings = config if call["requested_model"] == config.model else config.worker_config()
            usage = Usage(**call["usage"])
            cost = usage.cost(settings.rates)
            if (
                usage != normalize_usage(config.provider, call["raw_usage"])
                or cost is None
                or call["cost_usd"] is None
                or not math.isclose(cost, call["cost_usd"], rel_tol=1e-12, abs_tol=1e-12)
                or call["requested_model"] != settings.model
                or call["model"] != settings.model
                or call["cache_control_valid"] is not True
            ):
                raise ValueError("Controls usage, model or configured-rate accounting mismatch")
    if len(identities) != 1:
        raise ValueError("Scenario or evaluator differs between control runs")
    output.mkdir(exist_ok=False)
    consolidate_status([evidence / run for run in RUNS], output / "controls-validation.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory for the regenerated CSV")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            output = args.output or Path(temporary) / "controls"
            reproduce(ROOT / "benchmarks/controls-evidence", output)
            if (output / "controls-validation.csv").read_bytes() != (
                ROOT / "benchmarks/controls-validation.csv"
            ).read_bytes():
                raise ValueError("Published controls CSV differs from evidence")
        print("Verified 2 controls runs: checksums, accounting and all 8 conditions match.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Controls reproduction failed ({type(exc).__name__}); inspect evidence and tables.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
