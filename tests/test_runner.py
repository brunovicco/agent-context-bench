import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_context_bench.cli import main
from agent_context_bench.models import Config, Message, Rates, Reply, Usage
from agent_context_bench.providers import DemoProvider
from agent_context_bench.runner import numeric_usage, percentile, read_rows, run, write_report
from agent_context_bench.telemetry import Telemetry


class RecordingProvider(DemoProvider):
    def __init__(self) -> None:
        self.requests: list[tuple[str, list[Message], bool]] = []

    def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
        self.requests.append((system, list(messages), cache))
        result = super().generate(system, messages, cache=cache)
        # Deterministic fixture, NOT a live benchmark measurement.
        return replace(result, usage=Usage(100, 50 if cache else 0, 0, 10))


def test_summary_is_billed_and_cache_is_isolated(tmp_path: Path) -> None:
    config = Config("anthropic", "test", repetitions=1, rates=Rates(1, 0.1, 1.25, 2, "test"))
    provider = RecordingProvider()
    directory = tmp_path / "run"
    assert run(config, provider, directory, Telemetry(), demo=False)
    calls = read_rows(directory / "calls.jsonl")
    sessions = read_rows(directory / "sessions.jsonl")
    assert len(calls) == 19 and len(sessions) == 3
    assert all(s["passed"] for s in sessions)
    summary = [r for r in calls if r["operation"] == "summary"]
    assert len(summary) == 1 and summary[0]["cost_usd"] > 0
    assert len({system for system, _, _ in provider.requests}) == 3
    request = next(m for _, m, _ in provider.requests if m[0].content.startswith("Continue from"))
    assert "diagnostic sample" not in request[0].content
    assert len(request) == 3
    rows = {r["strategy"]: r for r in write_report(directory)}
    assert rows["summary"]["total_cost_usd"] > rows["cache"]["total_cost_usd"]
    assert rows["summary"]["cost_per_passed_task_usd"] == rows["summary"]["total_cost_usd"]
    assert rows["summary"]["summary_cost_usd"] == summary[0]["cost_usd"]
    assert "retry_storm" not in (directory / "calls.jsonl").read_text()
    assert "diagnostic sample" not in (directory / "manifest.json").read_text()
    with pytest.raises(FileExistsError):
        run(config, provider, directory, Telemetry(), demo=False)


@pytest.mark.parametrize("failure", [RuntimeError("PRIVATE_RESPONSE"), KeyboardInterrupt()])
def test_failure_keeps_partial_results_and_unknown_cost(
    tmp_path: Path, failure: BaseException
) -> None:
    class Broken(RecordingProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            if self.requests:
                raise failure
            return super().generate(system, messages, cache=cache)

    directory = tmp_path / "partial"
    config = Config("openai", "test", repetitions=1, strategies=("cache",))
    assert not run(config, Broken(), directory, Telemetry(), demo=False)
    calls = read_rows(directory / "calls.jsonl")
    assert len(calls) == 2 and calls[-1]["usage"] is None
    assert calls[-1]["cost_usd"] is None
    assert "PRIVATE_RESPONSE" not in (directory / "calls.jsonl").read_text()
    row = write_report(directory)[0]
    assert row["total_cost_usd"] is None
    assert not row["usage_complete"] and row["passed"] == 0
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] in ("failed", "interrupted")


@pytest.mark.parametrize("summary", [False, True])
def test_truncation_retains_usage_and_fails_quality(tmp_path: Path, summary: bool) -> None:
    class Truncated(RecordingProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            reply = super().generate(system, messages, cache=cache)
            if not summary or messages[-1].content.startswith("Summarize"):
                return replace(reply, complete=False)
            return reply

    config = Config("anthropic", "test", repetitions=1, strategies=("summary",))
    directory = tmp_path / "truncated"
    assert run(config, Truncated(), directory, Telemetry(), demo=False)
    assert read_rows(directory / "calls.jsonl")[-1]["status"] == "incomplete"
    session = read_rows(directory / "sessions.jsonl")[0]
    assert session["status"] == "incomplete" and not session["passed"]


def test_baseline_with_unexpected_cache_is_invalid(tmp_path: Path) -> None:
    class Cached(RecordingProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            return super().generate(system, messages, cache=True)

    directory = tmp_path / "bad-control"
    config = Config("anthropic", "test", repetitions=1, strategies=("full",))
    run(config, Cached(), directory, Telemetry(), demo=False)
    assert not write_report(directory)[0]["cache_control_valid"]


def test_cli_demo_report_and_explicit_live_requirement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "demo"
    assert main(["demo", "--output", str(path)]) == 0
    assert main(["report", str(path)]) == 0
    assert main(["demo", "--output", str(path)]) == 1
    assert main(["report", str(tmp_path / "absent")]) == 1
    rows = write_report(path)
    assert all(row["synthetic"] and row["total_cost_usd"] is None for row in rows)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--config", "unused", "--output", "unused"])
    assert exc.value.code == 2
    assert "Completed" in capsys.readouterr().out


def test_cli_live_path_uses_fake_provider_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeLive(RecordingProvider):
        closed = False

        def __init__(self, config: Config) -> None:
            super().__init__()

        def close(self) -> None:
            FakeLive.closed = True

    monkeypatch.setattr("agent_context_bench.cli.LiveProvider", FakeLive)
    config = tmp_path / "settings.toml"
    config.write_text('provider="openai"\nmodel="test"\nrepetitions=1\n')
    assert main(["run", "--config", str(config), "--output", str(tmp_path / "live"), "--live"]) == 0
    assert FakeLive.closed


def test_numeric_usage_and_quantiles() -> None:
    assert numeric_usage({"count": 2, "text": "PRIVATE", "nested": {"count": 3}, "bool": True}) == {
        "count": 2,
        "nested": {"count": 3},
    }
    assert percentile([], 0.5) is None
    assert percentile([10, 20], 0.5) == 15
