import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_context_bench.cli import main
from agent_context_bench.models import (
    STATUS_STRATEGIES,
    Config,
    Message,
    Rates,
    Reply,
    SubagentConfig,
    Usage,
)
from agent_context_bench.runner import read_rows, run, write_report
from agent_context_bench.scenario import EXPECTED, TURNS
from agent_context_bench.status_updates import STATUS_EXPECTED, evaluate_notes, evaluate_status
from agent_context_bench.telemetry import Telemetry

MAIN_RATES = Rates(2, 0.2, 2.5, 10, "fixture")
SMALL_RATES = Rates(0.1, 0.01, 0.125, 0.5, "fixture")
USAGE = Usage(100, 20, 30, 10)


class StatusProvider:
    def __init__(self, model: str = "main") -> None:
        self.model = model
        self.requests: list[tuple[str, list[Message], bool]] = []
        self.closed = False

    def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
        self.requests.append((system, list(messages), cache))
        content = "\n".join(m.content for m in messages)
        if ":notes\n" in system:
            turn = max(int(t) for t in re.findall(r"\bE([1-5]):", content))
            text = f"NOTE_PRIVATE snapshot={turn}"
        elif ":status\n" in system:
            ids = re.findall(r"snapshot=(\d)", content) or re.findall(r"\bE([1-5]):", content)
            turn = max(int(t) for t in ids) - 1
            # Deterministic fixture; it does not simulate model performance or quality.
            text = json.dumps({**STATUS_EXPECTED[turn], "text": "STATUS_PRIVATE update"})
        elif messages[-1].content == TURNS[-1]:
            text = json.dumps({**EXPECTED, "evidence": ["E3"]})
        else:
            text = "MAIN_PRIVATE acknowledgement"
        return Reply(text, USAGE, {}, self.model, 1, 0.5, 100)

    def close(self) -> None:
        self.closed = True


def config(**kwargs: Any) -> Config:
    return replace(
        Config(
            "openai",
            "main",
            repetitions=1,
            strategies=STATUS_STRATEGIES,
            rates=MAIN_RATES,
            subagent=SubagentConfig("small", SMALL_RATES),
        ),
        **kwargs,
    )


@pytest.mark.parametrize("ticks", [1, 2])
def test_status_cost_decomposition_quality_and_isolation(tmp_path: Path, ticks: int) -> None:
    primary, small = StatusProvider(), StatusProvider("small")
    directory = tmp_path / "run"
    assert run(
        config(status_updates_per_turn=ticks),
        primary,
        directory,
        Telemetry(),
        demo=False,
        subagent_provider=small,
    )
    reports = {r["strategy"]: r for r in write_report(directory)}
    main_unit, small_unit = USAGE.cost(MAIN_RATES), USAGE.cost(SMALL_RATES)
    assert main_unit is not None and small_unit is not None
    for strategy, row in reports.items():
        notes = 0 if strategy == "status_full_history" else 5
        cheap = strategy == "status_notes_small_model"
        rendered = 5 * ticks * (small_unit if cheap else main_unit)
        assert row["calls"] == 6 + notes + 5 * ticks
        assert row["passed"] == row["task_passed"] == 1
        assert row["status_updates_expected"] == row["status_updates_passed"] == 5 * ticks
        assert row["status_calls"] == 5 * ticks and row["status_pass_rate"] == 1
        assert row["notes_calls"] == notes
        assert row["workload_cost_usd"] == pytest.approx(6 * main_unit)
        assert row["notes_cost_usd"] == pytest.approx(notes * main_unit)
        assert row["status_cost_usd"] == pytest.approx(rendered)
        assert row["status_update_cost_mean_usd"] == pytest.approx(rendered / (5 * ticks))
        assert row["status_pipeline_cost_usd"] == pytest.approx(notes * main_unit + rendered)
        assert row["total_cost_usd"] == pytest.approx((6 + notes) * main_unit + rendered)
        assert row["session_cost_mean_usd"] == row["total_cost_usd"]
        assert row["usage_complete"] and row["cache_control_valid"]
        assert row["status_overhead_fraction"] == pytest.approx(
            row["status_pipeline_cost_usd"] / row["total_cost_usd"]
        )
    assert len(small.requests) == 5 * ticks
    requests = primary.requests + small.requests
    assert all(cache for _, _, cache in requests)
    for system, history, _ in requests:
        content = "\n".join(m.content for m in history)
        assert "STATUS_PRIVATE" not in content
        assert "retry_storm" not in system and "database_latency" not in system
        if ":notes\n" in system:
            ids = re.findall(r"\bE([1-5]):", content)
            assert len(ids) == 1  # No earlier raw turns, only previous notes.
            turn = int(ids[0])
            assert (f"snapshot={turn - 1}" in content) == (turn > 1)
        elif ":status\n" in system:
            assert "Final turn" not in content
            if "NOTE_PRIVATE" in content:
                assert "diagnostic sample=" not in content
            else:
                ids = sorted({int(x) for x in re.findall(r"\bE([1-5]):", content)})
                assert ids == list(range(1, max(ids) + 1))
        else:
            assert "NOTE_PRIVATE" not in content
    calls = read_rows(directory / "calls.jsonl")
    assert all("checks" in c for c in calls if c["operation"] in ("status", "status_notes"))
    assert all(c["requested_model"] == "main" for c in calls if c["agent_role"] != "status")
    assert len({system.split("\n")[0] for system, _, _ in requests}) == 8
    assert not any("PRIVATE" in p.read_text() for p in directory.iterdir())
    assert b"\r\n" not in (directory / "summary.csv").read_bytes()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert len(manifest["status_prompt_sha256"]) == 64
    assert manifest["status_updates_per_turn"] == ticks
    assert "no_wall_clock_wait" in manifest["status_schedule"]


@pytest.mark.parametrize("operation", ["status", "notes"])
def test_auxiliary_quality_failure_does_not_change_main_answer(
    tmp_path: Path, operation: str
) -> None:
    class Invalid(StatusProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            result = super().generate(system, messages, cache=cache)
            if f":{operation}\n" in system:
                text = (
                    (result.text + " word" * 201)
                    if operation == "notes"
                    else '{"approval_required": false}'
                )
                return replace(result, text=text)
            return result

    directory = tmp_path / "invalid"
    assert run(
        config(strategies=("status_notes_same_model",)),
        Invalid(),
        directory,
        Telemetry(),
        demo=False,
    )
    row = write_report(directory)[0]
    assert row["task_passed"] == 1 and row["passed"] == 0
    assert row["calls"] == 16 and row["total_cost_usd"] > 0


def test_partial_status_failure_preserves_prior_checks_and_unknown_cost(tmp_path: Path) -> None:
    class Broken(StatusProvider):
        count = 0

        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            if ":status\n" in system:
                self.count += 1
                if self.count == 2:
                    raise RuntimeError("PRIVATE_ERROR")
            return super().generate(system, messages, cache=cache)

    directory = tmp_path / "failed"
    assert not run(
        config(strategies=("status_full_history",), status_updates_per_turn=2),
        Broken(),
        directory,
        Telemetry(),
        demo=False,
    )
    row = write_report(directory)[0]
    assert row["status_updates_passed"] == 1 and row["status_updates_expected"] == 10
    assert row["status_calls"] == 2 and row["status_latency_samples"] == 1
    assert row["total_cost_usd"] is None and row["status_cost_usd"] is None
    assert row["status_pipeline_cost_usd"] is None and row["status_update_cost_mean_usd"] is None
    assert row["workload_cost_usd"] > 0 and not row["usage_complete"]
    assert not any("PRIVATE_ERROR" in p.read_text() for p in directory.iterdir())


@pytest.mark.parametrize("operation", ["notes", "status"])
@pytest.mark.parametrize("failure", ["empty", "truncated"])
def test_incomplete_observation_preserves_billable_usage(
    tmp_path: Path,
    operation: str,
    failure: str,
) -> None:
    class Incomplete(StatusProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            result = super().generate(system, messages, cache=cache)
            if f":{operation}\n" in system:
                return replace(
                    result,
                    text="" if failure == "empty" else result.text,
                    complete=failure != "truncated",
                )
            return result

    directory = tmp_path / "incomplete"
    assert run(
        config(strategies=("status_notes_same_model",)),
        Incomplete(),
        directory,
        Telemetry(),
        demo=False,
    )
    session = read_rows(directory / "sessions.jsonl")[0]
    assert session["status"] == "incomplete" and not session["passed"]
    assert write_report(directory)[0]["total_cost_usd"] > 0


def test_unknown_renderer_rates_never_inherit_main_rates(tmp_path: Path) -> None:
    directory = tmp_path / "unpriced"
    assert run(
        config(strategies=("status_notes_small_model",), subagent=SubagentConfig("small")),
        StatusProvider(),
        directory,
        Telemetry(),
        demo=False,
        subagent_provider=StatusProvider("small"),
    )
    row = write_report(directory)[0]
    assert row["main_cost_usd"] > 0 and row["notes_cost_usd"] > 0
    assert row["status_cost_usd"] is None and row["total_cost_usd"] is None
    assert row["status_pipeline_cost_usd"] is None and row["session_cost_mean_usd"] is None
    assert row["status_update_cost_mean_usd"] is None


@pytest.mark.parametrize("value", [0, 21, True, 1.5])
def test_invalid_update_count_rejected(value: Any) -> None:
    with pytest.raises(ValueError, match="status_updates_per_turn"):
        config(status_updates_per_turn=value)


def test_missing_renderer_fails_before_creating_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        config(subagent=None)
    with pytest.raises(ValueError):
        run(config(), StatusProvider(), tmp_path / "absent", Telemetry(), demo=False)
    assert not (tmp_path / "absent").exists()


def test_status_checks_handle_stale_facts_missing_nulls_and_bad_json() -> None:
    for turn in range(5):
        good = {**STATUS_EXPECTED[turn], "text": "Investigating."}
        assert all(evaluate_status(json.dumps(good), turn).values())
        assert not evaluate_status(json.dumps({**good, "approval_required": 1}), turn)[
            "approval_required"
        ]
    missing = {"latest_evidence": "E1", "approval_required": True, "text": "Investigating."}
    assert not evaluate_status(json.dumps(missing), 0)["cause"]
    assert not evaluate_status(json.dumps(STATUS_EXPECTED[0]), 2)["latest_evidence"]
    for bad in ("no json", "[]", "null"):
        assert not any(evaluate_status(bad, 0).values())
    assert not evaluate_notes("")["nonempty"]
    assert not evaluate_notes("word " * 201)["word_limit"]


def test_status_cli_uses_both_models_and_closes_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients: list[StatusProvider] = []

    class FakeLive(StatusProvider):
        def __init__(self, settings: Config) -> None:
            super().__init__(settings.model)
            clients.append(self)

    monkeypatch.setattr("agent_context_bench.cli.LiveProvider", FakeLive)
    assert (
        main(
            [
                "run",
                "--config",
                "configs/anthropic-status.toml",
                "--output",
                str(tmp_path / "cli"),
                "--live",
            ]
        )
        == 0
    )
    assert len(clients) == 2 and all(c.closed for c in clients)
    assert clients[0].model != clients[1].model
    assert len(clients[1].requests) == 5


def test_repetitions_reset_notes_and_main_task_stays_equivalent_to_cache(tmp_path: Path) -> None:
    observed = StatusProvider()
    run(
        config(strategies=("status_notes_same_model",), repetitions=2),
        observed,
        tmp_path / "notes",
        Telemetry(),
        demo=False,
    )
    note_requests = [h for s, h, _ in observed.requests if ":notes\n" in s]
    assert "snapshot=" not in note_requests[0][0].content
    assert "snapshot=" not in note_requests[5][0].content
    control = StatusProvider()
    run(
        config(strategies=("cache",), repetitions=2),
        control,
        tmp_path / "cache",
        Telemetry(),
        demo=False,
    )
    tasks = [
        (s.split("\n", 1)[1], h, c)
        for s, h, c in observed.requests
        if ":notes\n" not in s and ":status\n" not in s
    ]
    baseline = [(s.split("\n", 1)[1], h, c) for s, h, c in control.requests]
    assert tasks == baseline


def test_savings_include_note_overhead_and_require_successful_comparable_runs(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "savings"
    run(
        config(),
        StatusProvider(),
        directory,
        Telemetry(),
        demo=False,
        subagent_provider=StatusProvider("small"),
    )
    reports = {r["strategy"]: r for r in write_report(directory)}
    baseline = reports["status_full_history"]
    same, cheap = reports["status_notes_same_model"], reports["status_notes_small_model"]
    assert baseline["session_savings_fraction"] == 0
    assert same["status_update_savings_fraction"] == 0
    assert same["status_pipeline_savings_fraction"] == pytest.approx(-1)
    assert same["session_savings_fraction"] < 0
    assert cheap["status_update_savings_fraction"] > cheap["session_savings_fraction"]
    # An otherwise identical run with one failed condition cannot claim savings.
    path = directory / "sessions.jsonl"
    sessions = read_rows(path)
    for session in sessions:
        if session["strategy"] == "status_notes_small_model":
            session["passed"] = False
    path.write_text("".join(json.dumps(s) + "\n" for s in sessions))
    reports = {r["strategy"]: r for r in write_report(directory)}
    assert reports["status_notes_small_model"]["session_savings_fraction"] is None
    # Different sample counts also suppress comparative fields.
    extra = next(s.copy() for s in sessions if s["strategy"] == "status_full_history")
    sessions.append(extra)
    path.write_text("".join(json.dumps(s) + "\n" for s in sessions))
    reports = {r["strategy"]: r for r in write_report(directory)}
    assert reports["status_notes_same_model"]["status_update_savings_fraction"] is None


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_status_configs_keep_existing_models_rates_and_request_limits(provider: str) -> None:
    old = Config.load(Path(f"configs/{provider}-delegation.toml"))
    new = Config.load(Path(f"configs/{provider}-status.toml"))
    assert new.strategies == STATUS_STRATEGIES
    assert new.repetitions == new.status_updates_per_turn == 1
    assert new.model == old.model and new.rates == old.rates and new.subagent == old.subagent
    assert new.max_output_tokens == old.max_output_tokens
    assert new.timeout_seconds == old.timeout_seconds


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, (False, False, False, None, False)),
        ({"text": None}, (True, False, False, None, False)),
        ({"text": 42}, (True, False, False, None, False)),
        ({"text": ["PRIVATE"]}, (True, False, False, None, False)),
        ({"text": ""}, (True, True, False, 0, True)),
        ({"text": " \n\t "}, (True, True, False, 0, True)),
        ({"text": "PRIVATE\nstatus\tupdate"}, (True, True, True, 3, True)),
        ({"text": "PRIVATE " * 40}, (True, True, True, 40, True)),
        ({"text": "PRIVATE " * 41}, (True, True, True, 41, False)),
    ],
)
def test_status_text_shape_preserves_original_verdict(
    fields: dict[str, Any], expected: tuple[bool, bool, bool, int | None, bool]
) -> None:
    from agent_context_bench.status_updates import status_text_diagnostics

    response = json.dumps({**STATUS_EXPECTED[0], **fields})
    details = status_text_diagnostics(response)
    assert tuple(details.values()) == expected
    assert evaluate_status(response, 0)["text"] == all(
        details[k] for k in ("text_is_string", "text_nonempty", "text_within_limit")
    )
    assert "PRIVATE" not in json.dumps(details)


@pytest.mark.parametrize("response", ["not JSON PRIVATE", "[]", "null", '"PRIVATE"'])
def test_status_text_diagnostics_reject_nonobjects(response: str) -> None:
    from agent_context_bench.status_updates import status_text_diagnostics

    assert tuple(status_text_diagnostics(response).values()) == (False, False, False, None, False)
    assert not any(evaluate_status(response, 0).values())


@pytest.mark.parametrize("complete", [True, False])
def test_status_metadata_persist_without_raw_text(tmp_path: Path, complete: bool) -> None:
    class LongStatus(StatusProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            result = super().generate(system, messages, cache=cache)
            if ":status\n" in system:
                value = json.loads(result.text)
                value["text"] = "PRIVATE " * 41
                return replace(result, text=json.dumps(value), complete=complete)
            return result

    directory = tmp_path / "diagnostic"
    assert run(
        config(strategies=("status_notes_same_model",)),
        LongStatus(),
        directory,
        Telemetry(),
        demo=False,
    )
    calls = read_rows(directory / "calls.jsonl")
    for call in calls:
        if call["operation"] == "status":
            assert call["status_text_diagnostics"] == {
                "text_present": True,
                "text_is_string": True,
                "text_nonempty": True,
                "text_word_count": 41,
                "text_within_limit": False,
            }
            assert call["checks"]["text"] is False
            assert call["status"] == ("ok" if complete else "incomplete")
        else:
            assert "status_text_diagnostics" not in call
    session = read_rows(directory / "sessions.jsonl")[0]
    assert session["passed"] is False
    assert session["task_passed"] is complete
    assert not any("PRIVATE" in p.read_text() for p in directory.iterdir())


@pytest.mark.parametrize("words", [0, 150, 200, 201])
def test_note_word_diagnostics_keep_hard_limit(words: int) -> None:
    from agent_context_bench.status_updates import notes_text_diagnostics

    text = " \n\t ".join(["PRIVATE"] * words)
    assert notes_text_diagnostics(text) == {
        "text_nonempty": words > 0,
        "text_word_count": words,
        "text_within_limit": words <= 200,
    }
    assert all(evaluate_notes(text).values()) == (0 < words <= 200)
    assert "PRIVATE" not in json.dumps(notes_text_diagnostics(text))


@pytest.mark.parametrize("complete", [True, False])
def test_note_metadata_and_cost_survive_quality_failure(tmp_path: Path, complete: bool) -> None:
    class LongNotes(StatusProvider):
        def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
            result = super().generate(system, messages, cache=cache)
            if ":notes\n" in system:
                return replace(result, text=result.text + " PRIVATE" * 199, complete=complete)
            return result

    directory = tmp_path / "notes-diagnostic"
    assert run(
        config(strategies=("status_notes_same_model",)),
        LongNotes(),
        directory,
        Telemetry(),
        demo=False,
    )
    calls = read_rows(directory / "calls.jsonl")
    notes = [c for c in calls if c["operation"] == "status_notes"]
    assert len(notes) == (5 if complete else 1)
    for call in notes:
        assert call["notes_text_diagnostics"] == {
            "text_nonempty": True,
            "text_word_count": 201,
            "text_within_limit": False,
        }
        assert call["checks"] == {"nonempty": True, "word_limit": False}
        assert call["status"] == ("ok" if complete else "incomplete")
        assert call["cost_usd"] == USAGE.cost(MAIN_RATES)
    assert all("notes_text_diagnostics" not in c for c in calls if c["operation"] != "status_notes")
    session = read_rows(directory / "sessions.jsonl")[0]
    assert session["passed"] is False
    assert session["task_passed"] is complete
    assert write_report(directory)[0]["notes_cost_usd"] > 0
    assert not any("PRIVATE" in p.read_text() for p in directory.iterdir())


def test_prompt_revision_is_fingerprinted(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_context_bench import status_updates

    revised = status_updates.status_prompt_digest()
    # Previously shipped instructions, retained only to verify experiment identity changes.
    old_notes = status_updates.NOTES_SYSTEM.replace(
        "Aim for about 150 words; use fewer when sufficient. Never exceed 200 words.\n"
        "Omit unrelated diagnostic records.",
        "Return updated notes in at most 200 words. Omit unrelated diagnostic records.",
    )
    old_status = status_updates.STATUS_SYSTEM.replace(
        "Aim for about 25 words in text; use fewer when sufficient. "
        "Never exceed the 40-word limit.\n",
        "",
    )
    monkeypatch.setattr(status_updates, "NOTES_SYSTEM", old_notes)
    assert status_updates.status_prompt_digest() != revised
    monkeypatch.setattr(status_updates, "STATUS_SYSTEM", old_status)
    assert status_updates.status_prompt_digest() != revised
    assert status_updates.NOTES_WORD_LIMIT == 200
    assert status_updates.STATUS_WORD_LIMIT == 40


@pytest.mark.parametrize("state", ["failed", "interrupted", "running", "short", "missing"])
def test_partial_batch_cannot_claim_savings(tmp_path: Path, state: str) -> None:
    directory = tmp_path / "partial"
    assert run(
        config(),
        StatusProvider(),
        directory,
        Telemetry(),
        demo=False,
        subagent_provider=StatusProvider("small"),
    )
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    if state == "missing":
        path.unlink()
    else:
        if state == "short":
            manifest["config"]["repetitions"] = 2
        else:
            manifest["status"] = state
        path.write_text(json.dumps(manifest))
    for row in write_report(directory):
        assert row["session_savings_fraction"] is None
        assert row["status_pipeline_savings_fraction"] is None
        assert row["status_update_savings_fraction"] is None


def test_cost_per_passed_task_requires_whole_session_success(tmp_path: Path) -> None:
    directory = tmp_path / "denominator"
    assert run(
        config(strategies=("status_notes_same_model",), repetitions=2),
        StatusProvider(),
        directory,
        Telemetry(),
        demo=False,
    )
    path = directory / "sessions.jsonl"
    sessions = read_rows(path)
    sessions[0].update(passed=False, notes_passed=False)
    path.write_text("".join(json.dumps(s) + "\n" for s in sessions))
    row = write_report(directory)[0]
    assert row["task_passed"] == 2 and row["passed"] == 1
    assert row["cost_per_passed_task_usd"] == row["total_cost_usd"]
    assert row["session_cost_mean_usd"] == row["total_cost_usd"] / 2
