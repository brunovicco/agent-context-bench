import csv
import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from agent_context_bench.cli import main
from agent_context_bench.consolidation import consolidate_status
from agent_context_bench.contracts import condition_spec, validate_v4_run
from agent_context_bench.models import (
    ALL_STATUS_STRATEGIES,
    Config,
    Message,
    Reply,
    SubagentConfig,
)
from agent_context_bench.providers import request_payload
from agent_context_bench.runner import read_rows, report_rows, run, write_report
from agent_context_bench.scenario import (
    EXPECTED,
    evaluate,
    evaluator_digest,
    evidence_policy,
    expected_answer,
    scenario_digest,
    turn_text,
)
from agent_context_bench.status_updates import (
    evaluate_status,
    evaluate_structured_notes,
    render_status,
    status_evaluator_digest,
    status_expected,
    status_prompt_digest,
)
from agent_context_bench.telemetry import Telemetry
from tests.test_status_updates import SMALL_RATES, USAGE, config

TASK_CONTROLS = ("cache", "cache_small_model", "delegate_same_model", "delegate_small_model")


def note_fields(turn: int, scenario: str = "replay") -> dict[str, object]:
    facts = status_expected(scenario)[turn]
    evidence = {f"E{turn + 1}"}
    if facts["cause"] is not None:
        evidence.update(
            {"E3"} if scenario == "corrections" and turn == 2 else evidence_policy(scenario)[0]
        )
    return {**facts, "evidence": sorted(evidence)}


class ControlledProvider:
    def __init__(self, model: str = "main", scenario: str = "replay") -> None:
        self.model = model
        self.scenario = scenario
        self.requests: list[tuple[str, list[Message], bool]] = []
        self.closed = False

    def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
        self.requests.append((system, list(messages), cache))
        content = "\n".join(message.content for message in messages)
        if ":notes\n" in system or ":status\n" in system:
            ids = re.findall(r"\bE([1-5])\b", content)
            turn = max(map(int, ids)) - 1
            fields = note_fields(turn, self.scenario)
            if ":status\n" in system:
                fields.pop("evidence")
                fields["text"] = "PRIVATE_STATUS_DESCRIPTION"
            text = json.dumps(fields)
        elif ":subagent\n" in system:
            text = messages[-1].content.split("\n\n")[0]
        elif messages[-1].content.startswith("Final turn"):
            text = json.dumps(
                {
                    **expected_answer(self.scenario),
                    "evidence": sorted(evidence_policy(self.scenario)[0]),
                }
            )
        else:
            text = "PRIVATE_ACKNOWLEDGEMENT"
        return Reply(text, USAGE, {}, self.model, 1, 0.5, 100)

    def close(self) -> None:
        self.closed = True


def execute(path: Path, settings: Config) -> tuple[ControlledProvider, ControlledProvider]:
    primary = ControlledProvider(scenario=settings.scenario)
    small = ControlledProvider("small", settings.scenario)
    assert run(settings, primary, path, Telemetry(), demo=False, subagent_provider=small)
    return primary, small


@pytest.mark.parametrize(
    "evidence",
    [None, [], "E3", ["E3", 42], ["E3", "E999"], ["E3", "E3"], ["E3", []], ["E1", "E3"], [True]],
)
def test_evidence_rejects_types_unknown_duplicates_and_noncausal_ids(evidence: object) -> None:
    assert not evaluate(json.dumps({**EXPECTED, "evidence": evidence}))["evidence"]


def test_evidence_availability_and_revised_causal_support() -> None:
    text = json.dumps({**EXPECTED, "evidence": ["E3", "E4"]})
    assert evaluate(text)["evidence"]
    assert not evaluate(text, turn=2)["evidence"]
    assert not evaluate(text, "corrections")["evidence"]
    assert not evaluate(json.dumps({**EXPECTED, "evidence": ["E3"]}), "ambiguous")["evidence"]
    assert all(evaluate(text, "ambiguous").values())


@pytest.mark.parametrize("schedule,calls", [("every_turn", 34), ("final_only", 14)])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_task_controls_bill_the_right_model_and_preserve_chronology(
    tmp_path: Path,
    schedule: str,
    calls: int,
    provider: str,
) -> None:
    settings = config(
        strategies=TASK_CONTROLS, task_schedule=schedule, provider=provider, repetitions=2
    )
    primary, small = execute(tmp_path / "run", settings)
    rows = {row["strategy"]: row for row in write_report(tmp_path / "run")}
    assert sum(row["calls"] for row in rows.values()) == calls * 2
    count = 6 if schedule == "every_turn" else 1
    unit = USAGE.cost(SMALL_RATES)
    assert unit is not None
    cheap = rows["cache_small_model"]
    assert cheap["total_cost_usd"] == pytest.approx(count * 2 * unit)
    assert cheap["main_cost_usd"] == cheap["total_cost_usd"]
    assert cheap["subagent_calls"] == 0 and cheap["passed"] == 2
    assert cheap["task_savings_vs_cache_fraction"] > 0
    raw_calls = read_rows(tmp_path / "run/calls.jsonl")
    assert all(
        c["requested_model"] == c["model"] == "small" and c["agent_role"] == "main"
        for c in raw_calls
        if c["strategy"] == "cache_small_model"
    )
    assert all(cache for _, _, cache in primary.requests + small.requests)
    raw_requests = [
        (system, messages) for system, messages, _ in small.requests if ":subagent\n" not in system
    ]
    assert len({system for system, _ in raw_requests}) == 2
    finals = [
        messages for _, messages in raw_requests if messages[-1].content.startswith("Final turn")
    ]
    for messages in finals:
        assert [message.content[:2] for message in messages if message.role == "user"] == [
            "E1",
            "E2",
            "E3",
            "E4",
            "E5",
            "Fi",
        ]
        if schedule == "final_only":
            assert len(messages) == 6 and all(m.role == "user" for m in messages)
        payload = request_payload(settings.worker_config(), "stable", messages, True)
        assert payload["model"] == "small"
        assert payload["max_tokens" if provider == "anthropic" else "max_output_tokens"] == 2048
    output = tmp_path / "export.csv"
    assert consolidate_status([tmp_path / "run"], output) == 4
    assert "evaluator_sha256" in output.read_text()


@pytest.mark.parametrize("schedule", ["every_turn", "final_only"])
@pytest.mark.parametrize("scenario", ["replay", "ambiguous", "corrections", "dense"])
def test_status_controls_separate_notes_models_templates_and_task_history(
    tmp_path: Path,
    scenario: str,
    schedule: str,
) -> None:
    settings = config(
        strategies=ALL_STATUS_STRATEGIES,
        notes_format="structured",
        scenario=scenario,
        task_schedule=schedule,
        status_updates_per_turn=2,
    )
    primary, small = execute(tmp_path / "run", settings)
    rows = {row["strategy"]: row for row in write_report(tmp_path / "run")}
    calls = read_rows(tmp_path / "run/calls.jsonl")
    local = read_rows(tmp_path / "run/observations.jsonl")
    assert len(local) == 10
    assert all(
        not {"usage", "cost_usd", "cache_requested", "requested_model"}.intersection(event)
        for event in local
    )
    template = rows["status_notes_template"]
    assert template["status_calls"] == 0 and template["local_status_updates"] == 10
    assert template["status_cost_usd"] == template["status_update_cost_mean_usd"] == 0
    assert template["notes_calls"] == 5 and template["status_pass_rate"] == 1
    assert all(row["passed"] == row["task_passed"] == 1 for row in rows.values())
    assert all(row["status_updates_passed"] == 10 for row in rows.values())
    assert rows["status_full_history_small_model"]["notes_calls"] == 0
    assert rows["status_small_notes_small_model"]["notes_cost_usd"] == pytest.approx(
        5 * (USAGE.cost(SMALL_RATES) or 0)
    )
    assert all(
        call["requested_model"] == "small"
        for call in calls
        if call["strategy"] == "status_small_notes_small_model"
        and call["operation"] in ("status_notes", "status")
    )
    assert not any("PRIVATE" in file.read_text() for file in (tmp_path / "run").iterdir())
    for system, messages, _ in primary.requests + small.requests:
        content = "\n".join(m.content for m in messages)
        assert "PRIVATE_STATUS_DESCRIPTION" not in content
        if ":notes\n" in system:
            assert len(re.findall(r"\bE[1-5]:", content)) == 1
            if schedule == "final_only":
                assert "PRIVATE_ACKNOWLEDGEMENT" not in content
        if ":status\n" in system or ":notes\n" in system:
            assert "Final turn" not in content
        if messages[-1].content.startswith("Final turn"):
            assert "Working notes (data)" not in content
    before = {file: file.read_bytes() for file in (tmp_path / "run").iterdir()}
    output = tmp_path / "status.csv"
    assert consolidate_status([tmp_path / "run"], output) == 6
    assert all(file.read_bytes() == data for file, data in before.items())
    with output.open() as stream:
        exported = {row["strategy"]: row for row in csv.DictReader(stream)}
    assert exported["status_notes_template"]["status_model"] == "null"
    assert exported["status_notes_template"]["renderer"] == "template"
    assert exported["status_small_notes_small_model"]["notes_model"] == "small"


def test_template_uses_notes_without_repair_or_access_to_expected_facts() -> None:
    notes = note_fields(2)
    notes["cause"] = "incorrect_cause"
    text = render_status(json.dumps(notes))
    assert json.loads(text)["cause"] == "incorrect_cause"
    assert not evaluate_status(text, 2)["cause"]
    assert "incorrect_cause" in json.loads(text)["text"]
    for bad in ("not JSON", "[]", json.dumps({**notes, "approval_required": 1})):
        with pytest.raises(ValueError):
            render_status(bad)
    future = {**note_fields(0), "evidence": ["E1", "E5"]}
    assert not evaluate_structured_notes(json.dumps(future), 0)["available_evidence"]
    no_cause = {**note_fields(4), "evidence": ["E5"]}
    assert not evaluate_structured_notes(json.dumps(no_cause), 4)["causal_evidence"]


@pytest.mark.parametrize("failure", ["invalid", "error", "truncated"])
def test_notes_failure_keeps_costs_and_never_renders_or_falls_back(
    tmp_path: Path,
    failure: str,
) -> None:
    class Broken(ControlledProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            if ":notes\n" in system:
                if failure == "error":
                    raise RuntimeError("PRIVATE_EXCEPTION")
                return replace(
                    super().generate(system, messages, cache=cache),
                    text="PRIVATE_INVALID",
                    complete=failure != "truncated",
                )
            return super().generate(system, messages, cache=cache)

    settings = config(strategies=("status_notes_template",), notes_format="structured")
    complete = run(settings, Broken(), tmp_path / "run", Telemetry(), demo=False)
    assert complete is (failure != "error")
    row = write_report(tmp_path / "run")[0]
    assert row["passed"] == row["local_status_updates"] == row["status_calls"] == 0
    assert row["notes_calls"] == 1 and row["session_savings_fraction"] is None
    assert (row["total_cost_usd"] is None) is (failure == "error")
    assert not any("PRIVATE" in file.read_text() for file in (tmp_path / "run").iterdir())


@pytest.mark.parametrize("strategy", ["cache_small_model", "status_small_notes_small_model"])
def test_unknown_alternative_rates_never_become_zero(tmp_path: Path, strategy: str) -> None:
    settings = config(
        strategies=(strategy,), notes_format="structured", subagent=SubagentConfig("small")
    )
    execute(tmp_path / "run", settings)
    row = write_report(tmp_path / "run")[0]
    assert row["total_cost_usd"] is None
    assert row["notes_cost_usd" if strategy.startswith("status") else "main_cost_usd"] is None


@pytest.mark.parametrize(
    "strategy",
    [
        "cache_small_model",
        "status_small_notes_small_model",
        "status_full_history_small_model",
    ],
)
def test_alternative_model_failure_never_falls_back_or_loses_unknown_cost(
    tmp_path: Path,
    strategy: str,
) -> None:
    class Broken(ControlledProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            raise RuntimeError("PRIVATE_EXCEPTION_WITH_RESPONSE")

    primary = ControlledProvider()
    settings = config(strategies=(strategy,), notes_format="structured")
    assert not run(
        settings,
        primary,
        tmp_path / "run",
        Telemetry(),
        demo=False,
        subagent_provider=Broken("small"),
    )
    assert len(primary.requests) == (0 if strategy == "cache_small_model" else 1)
    calls = read_rows(tmp_path / "run/calls.jsonl")
    assert calls[-1]["requested_model"] == "small" and calls[-1]["status"] == "error"
    assert calls[-1]["cost_usd"] is None and calls[-1]["usage"] is None
    assert write_report(tmp_path / "run")[0]["total_cost_usd"] is None
    assert not any("PRIVATE" in file.read_text() for file in (tmp_path / "run").iterdir())


def test_status_baseline_is_explicit_and_absent_baselines_have_null_savings(tmp_path: Path) -> None:
    settings = config(
        strategies=ALL_STATUS_STRATEGIES,
        notes_format="structured",
        status_baseline="status_full_history_small_model",
    )
    execute(tmp_path / "present", settings)
    rows = {row["strategy"]: row for row in write_report(tmp_path / "present")}
    baseline = rows["status_full_history_small_model"]["total_cost_usd"]
    assert rows["status_full_history"]["session_savings_fraction"] == pytest.approx(
        1 - rows["status_full_history"]["total_cost_usd"] / baseline
    )
    execute(tmp_path / "absent", replace(settings, strategies=("status_notes_template",)))
    assert write_report(tmp_path / "absent")[0]["session_savings_fraction"] is None


def test_free_text_semantic_limit_remains_explicit() -> None:
    text = json.dumps({**status_expected()[0], "text": "Rollback was approved and executed."})
    # These checks deliberately do not pretend to score free-text semantic coherence.
    assert all(evaluate_status(text, 0).values())
    rendered = json.loads(render_status(json.dumps(note_fields(0))))
    assert rendered["text"] == (
        "Latest evidence: E1. Cause: unconfirmed. Discarded hypothesis: none. "
        "Human approval required."
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing_call",
        "duplicate_local",
        "missing_local",
        "wrong_model",
        "wrong_role",
        "future_turn",
        "bad_spec",
        "bad_hash",
        "bad_check",
        "fake_usage",
        "missing_final_criterion",
        "missing_note_criterion",
        "missing_status_criterion",
    ],
)
def test_damaged_v4_agendas_cannot_export_or_claim_savings(tmp_path: Path, damage: str) -> None:
    settings = config(strategies=ALL_STATUS_STRATEGIES, notes_format="structured")
    execute(tmp_path / "run", settings)
    manifest = json.loads((tmp_path / "run/manifest.json").read_text())
    calls = read_rows(tmp_path / "run/calls.jsonl")
    sessions = read_rows(tmp_path / "run/sessions.jsonl")
    local = read_rows(tmp_path / "run/observations.jsonl")
    if damage == "missing_call":
        calls.pop()
    elif damage == "duplicate_local":
        local.append(local[0])
    elif damage == "missing_local":
        local.pop()
    elif damage in ("wrong_model", "wrong_role", "future_turn"):
        calls[0][
            {"wrong_model": "requested_model", "wrong_role": "agent_role", "future_turn": "turn"}[
                damage
            ]
        ] = 99
    elif damage == "bad_spec":
        manifest["condition_specs"]["status_notes_template"]["renderer"] = "llm"
    elif damage == "bad_hash":
        del manifest["evaluator_sha256"]
    elif damage == "bad_check":
        local[0]["checks"]["approval_required"] = False
    elif damage == "missing_final_criterion":
        del sessions[0]["checks"]["evidence"]
    elif damage == "missing_note_criterion":
        note = next(call for call in calls if call["operation"] == "status_notes")
        del note["checks"]["available_evidence"]
    elif damage == "missing_status_criterion":
        del local[0]["checks"]["text"]
    else:
        local[0]["usage"] = {"ordinary": 0}
    with pytest.raises(ValueError):
        validate_v4_run(manifest, calls, sessions, local)
    # Even report-only operation must not calculate savings for an invalid agenda.
    rows = report_rows(calls, sessions, manifest, local)
    assert all(row["session_savings_fraction"] is None for row in rows)


def test_fingerprints_and_predeclared_variants_are_distinct() -> None:
    scenarios = ("replay", "ambiguous", "corrections", "dense")
    assert len({scenario_digest(scenario) for scenario in scenarios}) == 4
    assert status_expected("ambiguous")[2]["cause"] is None
    assert status_expected("corrections")[3]["cause"] is None
    assert status_expected("corrections")[3]["discarded_hypothesis"] is None
    assert "old action identifier is withdrawn" in turn_text(3, "dense")
    assert not evaluate(json.dumps({**EXPECTED, "evidence": ["E3"]}), "dense")["action"]
    assert len(evaluator_digest()) == len(status_evaluator_digest()) == 64
    assert status_prompt_digest("structured") != status_prompt_digest()


@pytest.mark.parametrize(
    "overrides",
    [
        {"scenario": "unknown"},
        {"task_schedule": "unknown"},
        {"notes_format": "unknown"},
        {"status_baseline": "cache"},
        {"strategies": ("status_notes_template",)},
        {"task_schedule": "final_only", "strategies": ("summary",)},
    ],
)
def test_invalid_design_fails_before_network_access(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        config(**overrides)


def test_new_configs_roundtrip_and_cli_closes_alternative_task_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = list(Path("configs").glob("*-controls.toml"))
    assert len(paths) == 12
    for path in paths:
        settings = Config.load(path)
        assert Config.from_dict(asdict(settings)) == settings
        assert settings.rates is not None and settings.subagent is not None
        assert settings.subagent.rates is not None
        assert (
            condition_spec(settings, "cache_small_model")["task_model"] == settings.subagent.model
        )
    clients: list[ControlledProvider] = []

    def client(settings: Config) -> ControlledProvider:
        result = ControlledProvider(settings.model)
        clients.append(result)
        return result

    monkeypatch.setattr("agent_context_bench.cli.LiveProvider", client)
    path = tmp_path / "settings.toml"
    path.write_text(
        'provider="openai"\nmodel="main"\nrepetitions=1\n'
        'strategies=["cache_small_model"]\n[subagent]\nmodel="small"\n'
    )
    assert main(["run", "--config", str(path), "--output", str(tmp_path / "run"), "--live"]) == 0
    assert len(clients) == 2 and all(client.closed for client in clients)
    assert not clients[0].requests and len(clients[1].requests) == 6
