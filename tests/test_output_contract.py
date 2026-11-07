import json

from agent_context_bench.scenario import EXPECTED, SYSTEM, TURNS, evaluate


def test_diagnostic_examples_remain_strictly_scored() -> None:
    exact = {**EXPECTED, "evidence": ["E3"]}
    assert all(evaluate(json.dumps(exact)).values())
    verbose = {
        **exact,
        "cause": "retry_storm: retry_limit changed from 3 to 300",
        "discarded_hypothesis": "database_latency (ruled out by E2)",
    }
    checks = evaluate(json.dumps(verbose))
    assert checks == {
        "cause": False,
        "action": True,
        "approval_required": True,
        "discarded_hypothesis": False,
        "evidence": True,
    }
    # Markdown wrappers stay invalid; no implicit repair or relaxed matching.
    assert not any(evaluate("```json\n" + json.dumps(exact) + "\n```").values())
    for expected in EXPECTED.values():
        if isinstance(expected, str):
            assert expected not in SYSTEM
            assert expected not in TURNS[-1]
