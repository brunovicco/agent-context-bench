import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from agent_context_bench.cli import main
from agent_context_bench.models import (
    Config,
    Message,
    Rates,
    Reply,
    Strategy,
    SubagentConfig,
    Usage,
)
from agent_context_bench.runner import read_rows, run, write_report
from agent_context_bench.scenario import DELEGATE_HANDOFF, EXPECTED, TURNS
from agent_context_bench.telemetry import Telemetry

MAIN_RATES = Rates(2, 0.2, 2.5, 10, "test")
WORKER_RATES = Rates(0.1, 0.01, 0.125, 0.5, "test")
OBSERVED = Usage(100, 50, 30, 10)


class EvidenceProvider:
    def __init__(self, model: str = "main") -> None:
        self.model = model
        self.requests: list[tuple[str, list[Message], bool]] = []
        self.closed = False

    def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
        self.requests.append((system, list(messages), cache))
        if ":subagent\n" in system:
            # Deliberately synthetic fixture: test orchestration, not model quality.
            seen = [f"E{i}" for i in range(1, 6) if any(f"E{i}:" in m.content for m in messages)]
            text = "Observed evidence: " + ", ".join(seen)
        elif messages[-1].content.startswith("Final turn"):
            text = json.dumps({**EXPECTED, "evidence": ["E3"]})
        else:
            text = "MAIN_ACK_PRIVATE"
        return Reply(text, OBSERVED, {}, self.model, 1, 0.5, 100)

    def close(self) -> None:
        self.closed = True


def settings(strategy: Strategy, *, priced: bool = True) -> Config:
    return Config(
        "openai",
        "main",
        repetitions=1,
        strategies=(strategy,),
        rates=MAIN_RATES,
        subagent=SubagentConfig("small", WORKER_RATES if priced else None),
    )


@pytest.mark.parametrize("strategy", ["delegate_same_model", "delegate_small_model"])
def test_isolation_chronology_cache_and_role_costs(tmp_path: Path, strategy: Strategy) -> None:
    config = settings(strategy)
    primary, small = EvidenceProvider(), EvidenceProvider("small")
    directory = tmp_path / "run"
    assert run(config, primary, directory, Telemetry(), demo=False, subagent_provider=small)
    worker = primary if strategy == "delegate_same_model" else small
    worker_requests = [r for r in worker.requests if ":subagent\n" in r[0]]
    main_requests = [r for r in primary.requests if ":subagent\n" not in r[0]]
    assert len(worker_requests) == 5 and len(main_requests) == 6
    assert all(cache for _, _, cache in [*worker_requests, *main_requests])
    assert len({system for system, _, _ in worker_requests}) == 1
    assert worker_requests[0][0] != main_requests[0][0]
    for index, (system, history, _) in enumerate(worker_requests):
        content = system + "".join(m.content for m in history)
        assert "MAIN_ACK_PRIVATE" not in content and "Final turn" not in content
        assert len(history) == 2 * index + 1
        assert f"E{index + 1}:" in content
        for future in range(index + 2, 6):
            assert f"E{future}:" not in content
        assert all(value not in system for value in ("retry_storm", "E3", "database_latency"))
    assert all("diagnostic sample=" not in m.content for _, h, _ in main_requests for m in h)
    assert main_requests[0][1][0].content.startswith(DELEGATE_HANDOFF)
    assert main_requests[-1][1][-1].content == TURNS[-1]
    calls = read_rows(directory / "calls.jsonl")
    worker_calls = [c for c in calls if c["agent_role"] == "subagent"]
    worker_model = "main" if strategy == "delegate_same_model" else "small"
    assert all(c["requested_model"] == c["model"] == worker_model for c in worker_calls)
    worker_prices = MAIN_RATES if strategy == "delegate_same_model" else WORKER_RATES
    worker_cost = OBSERVED.cost(worker_prices)
    main_cost = OBSERVED.cost(MAIN_RATES)
    assert worker_cost is not None and main_cost is not None
    row = write_report(directory)[0]
    assert row["passed"] == 1 and row["calls"] == 11 and row["task_latency_samples"] == 6
    assert row["main_cost_usd"] == pytest.approx(main_cost * 6)
    assert row["subagent_cost_usd"] == pytest.approx(worker_cost * 5)
    assert row["total_cost_usd"] == pytest.approx(main_cost * 6 + worker_cost * 5)
    assert row["known_input_tokens"] == 11 * OBSERVED.total_input
    assert row["main_known_input_tokens"] == 6 * OBSERVED.total_input
    assert row["subagent_known_input_tokens"] == 5 * OBSERVED.total_input
    assert row["main_cache_read_fraction"] == pytest.approx(50 / OBSERVED.total_input)
    assert row["subagent_calls"] == 5
    assert row["session_elapsed_p50_ms"] > 0
    assert not any("MAIN_ACK_PRIVATE" in p.read_text() for p in directory.iterdir())
    assert not any("Observed evidence:" in p.read_text() for p in directory.iterdir())
    if strategy == "delegate_same_model":
        assert not small.requests


def test_unknown_worker_prices_never_inherit_main_prices(tmp_path: Path) -> None:
    directory = tmp_path / "unpriced"
    config = settings("delegate_small_model", priced=False)
    assert config.worker_config().rates is None
    assert run(
        config,
        EvidenceProvider(),
        directory,
        Telemetry(),
        demo=False,
        subagent_provider=EvidenceProvider("small"),
    )
    row = write_report(directory)[0]
    assert row["main_cost_usd"] > 0
    assert row["subagent_cost_usd"] is None and row["total_cost_usd"] is None
    assert row["cost_per_passed_task_usd"] is None and row["usage_complete"]


@pytest.mark.parametrize("mode", ["error", "empty", "truncated"])
def test_worker_failure_retains_usage_and_does_not_fall_back(tmp_path: Path, mode: str) -> None:
    class Broken(EvidenceProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            if mode == "error":
                raise RuntimeError("PRIVATE_CREDENTIAL")
            result = super().generate(system, messages, cache=cache)
            return replace(
                result, text="" if mode == "empty" else result.text, complete=mode != "truncated"
            )

    directory = tmp_path / mode
    primary = EvidenceProvider()
    ok = run(
        settings("delegate_small_model"),
        primary,
        directory,
        Telemetry(),
        demo=False,
        subagent_provider=Broken("small"),
    )
    assert ok == (mode != "error")
    assert not primary.requests
    assert not read_rows(directory / "sessions.jsonl")[0]["passed"]
    calls = read_rows(directory / "calls.jsonl")
    assert len(calls) == 1 and calls[0]["agent_role"] == "subagent"
    row = write_report(directory)[0]
    assert row["main_calls"] == 0 and row["passed"] == 0
    if mode == "error":
        assert row["total_cost_usd"] is None and not row["subagent_usage_complete"]
    else:
        assert row["subagent_cost_usd"] > 0 and row["usage_complete"]
    assert "PRIVATE_CREDENTIAL" not in (directory / "calls.jsonl").read_text()


def test_session_repetitions_do_not_share_worker_history(tmp_path: Path) -> None:
    config = replace(settings("delegate_small_model"), repetitions=2)
    worker = EvidenceProvider("small")
    run(
        config,
        EvidenceProvider(),
        tmp_path / "isolated",
        Telemetry(),
        demo=False,
        subagent_provider=worker,
    )
    assert len(worker.requests) == 10
    assert worker.requests[0][0] != worker.requests[5][0]
    assert len(worker.requests[0][1]) == len(worker.requests[5][1]) == 1


def test_missing_worker_fails_before_starting_any_session(tmp_path: Path) -> None:
    directory = tmp_path / "absent"
    with pytest.raises(ValueError, match="provider"):
        run(
            settings("delegate_small_model"), EvidenceProvider(), directory, Telemetry(), demo=False
        )
    assert not directory.exists()
    with pytest.raises(ValueError):
        Config("openai", "main", strategies=("delegate_small_model",))
    with pytest.raises(ValueError):
        replace(settings("delegate_small_model"), subagent=SubagentConfig("main"))
    with pytest.raises(ValueError):
        SubagentConfig(" ")
    with pytest.raises(ValueError):
        Config("openai", "main").worker_config()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_delivered_configs_use_separate_prices_and_unchanged_request_limits(provider: str) -> None:
    config = Config.load(Path("configs") / f"{provider}-delegation.toml")
    worker = config.worker_config()
    assert config.repetitions == 1
    assert config.strategies == ("cache", "delegate_same_model", "delegate_small_model")
    assert worker.provider == config.provider and worker.model != config.model
    assert worker.max_output_tokens == config.max_output_tokens
    assert worker.timeout_seconds == config.timeout_seconds
    assert worker.rates is not None and config.rates is not None
    assert worker.rates.input < config.rates.input
    assert Config.load(Path("configs") / f"{provider}.toml").strategies == (
        "full",
        "cache",
        "summary",
    )


@pytest.mark.parametrize("fail", [False, True])
def test_cli_closes_both_clients_and_keeps_models_distinct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail: bool,
) -> None:
    clients: list[EvidenceProvider] = []

    class FakeLive(EvidenceProvider):
        def __init__(self, config: Config) -> None:
            super().__init__(config.model)
            clients.append(self)

        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            if fail and self is clients[-1]:
                raise RuntimeError("PRIVATE")
            return super().generate(system, messages, cache=cache)

    monkeypatch.setattr("agent_context_bench.cli.LiveProvider", FakeLive)
    rc = main(
        [
            "run",
            "--config",
            "configs/openai-delegation.toml",
            "--output",
            str(tmp_path / "cli"),
            "--live",
        ]
    )
    assert rc == int(fail)
    assert len(clients) == 2 and all(c.closed for c in clients)
    assert clients[0].model != clients[1].model


def test_legacy_report_without_roles_still_accounts_for_all_calls(tmp_path: Path) -> None:
    directory = tmp_path / "legacy"
    run(
        Config("openai", "main", strategies=("cache",), repetitions=1, rates=MAIN_RATES),
        EvidenceProvider(),
        directory,
        Telemetry(),
        demo=False,
    )
    path = directory / "calls.jsonl"
    rows = read_rows(path)
    for row in rows:
        row.pop("agent_role")
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = write_report(directory)[0]
    assert report["main_calls"] == report["calls"] == 6
    assert report["main_cost_usd"] == report["total_cost_usd"]
    assert report["subagent_calls"] == 0 and report["subagent_cost_usd"] == 0


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_worker_payload_uses_same_cache_policy_as_main(
    provider: Literal["openai", "anthropic"],
) -> None:
    from agent_context_bench.providers import request_payload

    config = replace(settings("delegate_small_model"), provider=provider)
    messages = [
        Message("user", "evidence"),
        Message("assistant", "brief"),
        Message("user", "new evidence"),
    ]
    primary = request_payload(config, "stable", messages, True)
    worker = request_payload(config.worker_config(), "stable", messages, True)
    assert primary.pop("model") != worker.pop("model")
    assert primary == worker
