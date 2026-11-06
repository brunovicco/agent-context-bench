# agent-context-bench

Small Python 3.13 CLI derived from the Codex engineering harness, library profile.
Keep the flat package, official SDKs, local JSONL/CSV and optional telemetry.
Do not add a web server, database, agent framework or separate design documents.
README.md owns usage, methodology, limitations and source links.

- Run `uv sync --frozen --all-extras --all-groups`, then
  `uv run python scripts/quality_gate.py` before delivery.
- Do not use `from __future__ import annotations`.
- Test accounting, experiment isolation, provider payloads and failure recovery.
- No paid API calls in tests. Live experiments require explicit `--live`.
- Never log prompts, answers, headers, API keys, exception messages or hidden reasoning.
- Missing costs are null, not zero. Cache hits must be observed.
- Demo results are synthetic and must not support performance claims.
- Do not publish or modify upstream repositories without a request.
