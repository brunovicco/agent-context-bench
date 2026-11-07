import json
from pathlib import Path

import pytest

from agent_context_bench.cli import main
from agent_context_bench.models import Config
from agent_context_bench.runner import read_rows
from agent_context_bench.scenario import answer_diagnostics, evaluate
from tests.test_delegation import EvidenceProvider


def test_diagnostics_limits_content_and_excludes_unknown_fields() -> None:
    answer = {
        "cause": "x" * 300,
        "action": {"private": "NOT_LOGGED"},
        "approval_required": True,
        "evidence": ["E3", {"private": "NOT_LOGGED"}] + ["y" * 300] * 10,
        "extra": "NOT_LOGGED",
    }
    text = json.dumps(answer)
    before = evaluate(text)
    result = answer_diagnostics(text)
    assert evaluate(text) == before
    fields = result["fields"]
    assert isinstance(fields, dict)
    assert fields["cause"] == {
        "present": True,
        "type": "str",
        "value": "x" * 256,
        "truncated": True,
    }
    assert fields["action"] == {"present": True, "type": "dict"}
    assert fields["discarded_hypothesis"]["present"] is False
    assert fields["evidence"]["truncated"]
    assert len(fields["evidence"]["value"]) == 10
    assert "NOT_LOGGED" not in json.dumps(result)


@pytest.mark.parametrize("text", ["not JSON", "[]", '{"cause": null, "action": 123}'])
def test_diagnostics_handles_invalid_or_unexpected_shapes(text: str) -> None:
    result = answer_diagnostics(text)
    assert "not JSON" not in json.dumps(result)
    assert result["json_valid"] == (text != "not JSON")


def test_cli_diagnostics_is_opt_in_and_preserves_requests_and_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients: list[EvidenceProvider] = []

    class FakeLive(EvidenceProvider):
        def __init__(self, config: Config) -> None:
            super().__init__(config.model)
            clients.append(self)

    monkeypatch.setattr("agent_context_bench.cli.LiveProvider", FakeLive)
    monkeypatch.setattr(
        "agent_context_bench.runner.uuid4", lambda: type("ID", (), {"hex": "fixed"})()
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'provider="anthropic"\nmodel="test"\nrepetitions=1\nstrategies=["delegate_same_model"]\n'
    )
    for enabled in (False, True):
        directory = tmp_path / str(enabled)
        args = ["run", "--config", str(config_path), "--output", str(directory), "--live"]
        if enabled:
            args.append("--diagnostics")
        assert main(args) == 0
        session = read_rows(directory / "sessions.jsonl")[0]
        assert session["passed"] and all(session["checks"].values())
        assert ("answer_diagnostics" in session) == enabled
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest["diagnostics_enabled"] == enabled
        if enabled:
            assert session["answer_diagnostics"]["fields"]["cause"]["value"] == "retry_storm"
        else:
            assert "retry_storm" not in (directory / "sessions.jsonl").read_text()
        assert "retry_storm" not in (directory / "calls.jsonl").read_text()
    assert clients[0].requests == clients[1].requests
