import json
from pathlib import Path
from typing import Any

import pytest

from agent_context_bench.models import Config, Rates, Usage, normalize_usage
from agent_context_bench.scenario import EXPECTED, evaluate, scenario_digest


def test_provider_counts_are_disjoint() -> None:
    anthropic = normalize_usage(
        "anthropic",
        {
            "input_tokens": 100,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 300,
            "output_tokens": 40,
        },
    )
    openai = normalize_usage(
        "openai",
        {
            "input_tokens": 600,
            "output_tokens": 40,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    )
    assert anthropic == openai == Usage(100, 200, 300, 40)
    assert openai.total_input == 600
    assert openai.cost(Rates(2, 0.2, 2.5, 8, "2026-11-05")) == pytest.approx(0.00131)
    assert openai.cost(None) is None


def test_missing_write_accounting_and_overlapping_usage_fail() -> None:
    with pytest.raises(ValueError, match="write accounting"):
        normalize_usage("openai", {"input_tokens": 10, "output_tokens": 1})
    with pytest.raises(ValueError, match="overlapping"):
        normalize_usage(
            "openai",
            {
                "input_tokens": 10,
                "output_tokens": 1,
                "input_tokens_details": {"cached_tokens": 8, "cache_write_tokens": 8},
            },
        )
    with pytest.raises(ValueError, match="provider"):
        normalize_usage("unknown", {})
    assert normalize_usage("anthropic", {"input_tokens": 9, "output_tokens": 1}) == Usage(
        9, 0, 0, 1
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "invalid"},
        {"model": " "},
        {"repetitions": 0},
        {"repetitions": 101},
        {"summary_after": 6},
        {"max_output_tokens": 1},
        {"timeout_seconds": float("nan")},
        {"strategies": ()},
        {"strategies": ("invalid",)},
        {"strategies": ("cache", "cache")},
    ],
)
def test_invalid_configuration_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Config(**({"provider": "openai", "model": "test"} | overrides))


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_invalid_prices_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        Rates(bad, 0, 0, 0, "2026-11-05")


def test_load_configuration_and_reject_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'provider="anthropic"\nmodel="test"\nstrategies=["cache"]\n'
        '[rates]\ninput=2\ncache_read=0.2\ncache_write=2.5\noutput=8\nas_of="2026-11-05"\n'
    )
    config = Config.load(path)
    assert config.strategies == ("cache",)
    assert config.rates is not None and config.rates.input == 2
    path.write_text('provider="openai"\nmodel="test"\nunknown=1\n')
    with pytest.raises(TypeError):
        Config.load(path)


def test_rubric_does_not_accept_wrong_boolean_or_hypothesis() -> None:
    answer = {**EXPECTED, "evidence": ["E3"]}
    assert all(evaluate(json.dumps(answer)).values())
    answer["approval_required"] = "true"
    answer["cause"] = "database_latency"
    checks = evaluate(json.dumps(answer))
    assert not checks["approval_required"] and not checks["cause"]
    for malformed in ("invalid", "[]", "null", "1", "{}"):
        assert not all(evaluate(malformed).values())
    assert len(scenario_digest()) == 64
