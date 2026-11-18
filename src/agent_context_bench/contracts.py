"""Declared condition schedules and recorded-quality validation without provider access."""

from collections import Counter
from typing import Any

from agent_context_bench.models import (
    ALL_STATUS_STRATEGIES,
    DELEGATION_STRATEGIES,
    Config,
    Strategy,
)


def condition_spec(config: Config, strategy: Strategy) -> dict[str, Any]:
    """Declare models and the exact per-session agenda before making any requests."""
    observing = strategy in ALL_STATUS_STRATEGIES
    full_status = strategy in ("status_full_history", "status_full_history_small_model")
    small_model = config.subagent.model if config.subagent else None
    small_renderer = strategy in (
        "status_notes_small_model",
        "status_full_history_small_model",
        "status_small_notes_small_model",
    )
    return {
        "task_model": small_model if strategy == "cache_small_model" else config.model,
        "delegate_model": (small_model if strategy == "delegate_small_model" else config.model)
        if strategy in DELEGATION_STRATEGIES
        else None,
        "notes_model": (
            small_model if strategy == "status_small_notes_small_model" else config.model
        )
        if observing and not full_status
        else None,
        "status_model": (small_model if small_renderer else config.model)
        if observing and strategy != "status_notes_template"
        else None,
        "renderer": "template"
        if strategy == "status_notes_template"
        else "llm"
        if observing
        else None,
        "notes_format": config.notes_format if observing and not full_status else None,
        "task_turns": list(range(6)) if config.task_schedule == "every_turn" else [5],
        "delegate_turns": list(range(5)) if strategy in DELEGATION_STRATEGIES else [],
        "notes_turns": list(range(5)) if observing and not full_status else [],
        "status_turns": list(range(5)) if observing else [],
        "updates_per_turn": config.status_updates_per_turn if observing else 0,
    }


def checks_passed(checks: object) -> bool:
    """Read a recorded verdict only from a nonempty dictionary of boolean checks."""
    if (
        not isinstance(checks, dict)
        or not checks
        or any(type(value) is not bool for value in checks.values())
    ):
        raise ValueError("Missing or malformed recorded checks")
    return all(checks.values())


def validate_quality_counts(
    calls: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    observations: list[dict[str, Any]] | None = None,
) -> None:
    """Cross-check saved aggregates without reevaluating discarded response text."""
    for session in sessions:
        selected = [
            call
            for call in [*calls, *(observations or [])]
            if call["session_id"] == session["session_id"]
        ]
        status_passed = sum(
            checks_passed(call.get("checks"))
            for call in selected
            if call["operation"] in ("status", "status_template")
        )
        # Evaluate every note's metadata, even after a prior note failed.
        note_verdicts = [
            checks_passed(call.get("checks"))
            for call in selected
            if call["operation"] == "status_notes"
        ]
        notes_passed = all(note_verdicts)
        task_passed = checks_passed(session.get("checks"))
        expected = {
            "task_passed": task_passed,
            "notes_passed": notes_passed,
            "status_updates_passed": status_passed,
            "passed": task_passed
            and notes_passed
            and status_passed == session["status_updates_expected"],
        }
        if any(
            type(session.get(key)) is not type(value) or session[key] != value
            for key, value in expected.items()
        ):
            raise ValueError("Session quality counts disagree with recorded checks")


def validate_v4_run(
    manifest: dict[str, Any],
    calls: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> None:
    """Validate declared model routing and schedules without reevaluating discarded text."""
    config = Config.from_dict(manifest["config"])
    specs = {strategy: condition_spec(config, strategy) for strategy in config.strategies}
    if (
        manifest.get("schema_version") != 4
        or manifest["status"] != "completed"
        or manifest["synthetic"] is not False
        or manifest.get("condition_specs") != specs
    ):
        raise ValueError("Expected a completed live run with consistent condition specifications")
    for key in ("scenario_sha256", "evaluator_sha256", "status_evaluator_sha256"):
        digest = manifest.get(key)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("Missing evaluator or scenario provenance")
    if not isinstance(manifest.get("evaluator_version"), str) or not manifest["evaluator_version"]:
        raise ValueError("Missing evaluator version")
    if any(strategy in ALL_STATUS_STRATEGIES for strategy in config.strategies) and (
        manifest.get("status_updates_per_turn") != config.status_updates_per_turn
        or not isinstance(manifest.get("status_prompt_sha256"), str)
        or len(manifest["status_prompt_sha256"]) != 64
    ):
        raise ValueError("Missing status provenance")
    expected_sessions = Counter(
        (s, r) for s in config.strategies for r in range(config.repetitions)
    )
    if Counter((s["strategy"], s["repetition"]) for s in sessions) != expected_sessions:
        raise ValueError("Incomplete or duplicate sessions")
    session_map = {s["session_id"]: s for s in sessions}
    if len(session_map) != len(sessions):
        raise ValueError("Duplicate session identifiers")
    expected_calls: Counter[tuple[str, str, int, int | None, str, str]] = Counter()
    expected_local: Counter[tuple[str, int, int]] = Counter()
    for session in sessions:
        spec = specs[session["strategy"]]
        if (
            session["status"] != "completed"
            or session["synthetic"] is not False
            or session["provider"] != config.provider
            or session["requested_model"] != spec["task_model"]
            or session["status_updates_expected"]
            != len(spec["status_turns"]) * spec["updates_per_turn"]
        ):
            raise ValueError("Incomplete or inconsistent session")
        sid = session["session_id"]
        for operation, role, turns, model in (
            ("task", "main", spec["task_turns"], spec["task_model"]),
            ("delegate", "subagent", spec["delegate_turns"], spec["delegate_model"]),
            ("status_notes", "notes", spec["notes_turns"], spec["notes_model"]),
            (
                "summary",
                "main",
                [config.summary_after] if session["strategy"] == "summary" else [],
                spec["task_model"],
            ),
        ):
            expected_calls.update((sid, operation, turn, None, role, model) for turn in turns)
        for turn in spec["status_turns"]:
            for update in range(spec["updates_per_turn"]):
                if spec["renderer"] == "template":
                    expected_local.update([(sid, turn, update)])
                else:
                    expected_calls.update(
                        [(sid, "status", turn, update, "status", spec["status_model"])]
                    )
    actual_calls: Counter[tuple[str, str, int, int | None, str, str]] = Counter()
    for call in calls:
        observed_session = session_map.get(call["session_id"])
        if (
            observed_session is None
            or call["strategy"] != observed_session["strategy"]
            or call["repetition"] != observed_session["repetition"]
            or call["provider"] != config.provider
            or call["synthetic"] is not False
            or call["status"] != "ok"
            or call.get("cache_requested") is not (call["strategy"] != "full")
        ):
            raise ValueError("Invalid call association, cache policy or completion")
        actual_calls.update(
            [
                (
                    call["session_id"],
                    call["operation"],
                    call["turn"],
                    call.get("update"),
                    call["agent_role"],
                    call["requested_model"],
                )
            ]
        )
    actual_local: Counter[tuple[str, int, int]] = Counter()
    for observation in observations:
        observed_session = session_map.get(observation["session_id"])
        if (
            observed_session is None
            or observation["strategy"] != observed_session["strategy"]
            or observation["repetition"] != observed_session["repetition"]
            or observation["provider"] != config.provider
            or observation["operation"] != "status_template"
            or observation["status"] != "ok"
            or observation.get("renderer") != "template"
            or any(key in observation for key in ("usage", "cost_usd", "cache_requested"))
        ):
            raise ValueError("Invalid local observation")
        actual_local.update(
            [(observation["session_id"], observation["turn"], observation["update"])]
        )
    if expected_calls != actual_calls or expected_local != actual_local:
        raise ValueError("Missing or duplicate scheduled events")
    final_keys = {"cause", "action", "approval_required", "discarded_hypothesis", "evidence"}
    status_keys = {"latest_evidence", "cause", "discarded_hypothesis", "approval_required", "text"}
    note_keys = {"nonempty", "word_limit"}
    if config.notes_format == "structured":
        note_keys |= {
            "schema",
            "latest_evidence",
            "cause",
            "discarded_hypothesis",
            "approval_required",
            "available_evidence",
            "causal_evidence",
        }
    checked = [(session.get("checks"), final_keys) for session in sessions]
    checked.extend(
        (call.get("checks"), status_keys if call["operation"] == "status" else note_keys)
        for call in calls
        if call["operation"] in ("status", "status_notes")
    )
    checked.extend((event.get("checks"), status_keys) for event in observations)
    if any(not isinstance(checks, dict) or set(checks) != keys for checks, keys in checked):
        raise ValueError("Missing or unexpected recorded evaluation criteria")
    validate_quality_counts(calls, sessions, observations)
