"""Small CLI: demo, run and report."""

import argparse
import json
import sys
from pathlib import Path

from agent_context_bench.models import Config
from agent_context_bench.providers import DemoProvider, LiveProvider
from agent_context_bench.runner import run, write_report
from agent_context_bench.telemetry import Telemetry


def main(argv: list[str] | None = None) -> int:
    """Run offline by default; real requests require the explicit live flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Synthetic offline plumbing check; no performance claims")
    demo.add_argument("--output", type=Path, default=Path("results/demo"))
    live = sub.add_parser("run", help="Run a controlled experiment using a provider API")
    live.add_argument("--config", type=Path, required=True)
    live.add_argument("--output", type=Path, required=True)
    live.add_argument("--live", action="store_true", help="Allow billable API requests")
    live.add_argument(
        "--diagnostics",
        action="store_true",
        help="Save bounded final JSON fields from the synthetic incident to sessions.jsonl",
    )
    live.add_argument("--otlp-endpoint", help="Optional OTLP/HTTP traces endpoint")
    report = sub.add_parser("report", help="Regenerate a CSV from existing local results")
    report.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run" and not args.live:
        parser.error("run requires --live; use demo for an offline execution")
    telemetry: Telemetry | None = None
    provider: LiveProvider | None = None
    subagent_provider: LiveProvider | None = None
    try:
        if args.command == "report":
            if not (args.directory / "manifest.json").exists():
                raise ValueError("Expected a benchmark run directory")
            print(json.dumps(write_report(args.directory), indent=2))
            return 0
        if args.output.exists():
            raise FileExistsError("Use a new output directory; existing results are immutable")
        if args.command == "demo":
            config = Config(provider="openai", model="synthetic-demo", repetitions=1)
            telemetry = Telemetry()
            ok = run(config, DemoProvider(), args.output, telemetry, demo=True)
        else:
            config = Config.load(args.config)
            telemetry = Telemetry(args.otlp_endpoint)
            provider = LiveProvider(config)
            if "delegate_small_model" in config.strategies:
                subagent_provider = LiveProvider(config.worker_config())
            ok = run(
                config,
                provider,
                args.output,
                telemetry,
                demo=False,
                subagent_provider=subagent_provider,
                diagnostics=args.diagnostics,
            )
        print(f"{'Completed' if ok else 'Stopped; inspect manifest.json'}: {args.output}")
        return 0 if ok else 1
    except Exception as exc:
        # SDK exception text may contain request bodies, URLs or credentials.
        print(
            f"Cannot run benchmark ({type(exc).__name__}). Check configuration and credentials.",
            file=sys.stderr,
        )
        return 1
    finally:
        if subagent_provider is not None:
            subagent_provider.close()
        if provider is not None:
            provider.close()
        if telemetry is not None:
            telemetry.close()


if __name__ == "__main__":
    raise SystemExit(main())
