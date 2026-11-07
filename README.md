# agent-context-bench

**English** | [Português (Brasil)](README.pt-br.md)

**How much does maintaining context cost-and when is it worth delegating work to another model?**

This project compares full conversation history, caching, summarization, and delegation
using the OpenAI and Anthropic APIs. It measures cost, elapsed time, and final-answer
correctness for the same incident, with separate accounting for the main agent and subagent.

In the delegation pilot, using a cheaper subagent reduced cost per successful task by
**73.3% on OpenAI** and **66.2% on Anthropic**, compared with the cached baseline. Median
session time increased to **1.99×** and **2.38×**, respectively. Delegating to the same
model cost more on both providers.

**Scope of these results:** three repetitions per condition, one synthetic scenario,
and live API calls. Every condition passed in 3/3 sessions. This is an initial measurement
of repeatability; it does not establish equivalent quality across different tasks.

[Results](#pilot-results) · [How it works](#how-it-works) ·
[Run it](#run-it) · [Method](#method-and-limitations) · [Development](#development)

## Pilot results

Costs are calculated from the configured rates. Time is the median for the entire session,
including delegation calls. The `cache` condition is the baseline within each provider.

| Provider | Strategy | USD per successful task | Cost change | Median time | Passed |
|---|---|---:|---:|---:|---:|
| OpenAI | Cache | 0.08477 | - | 9.75 s | 3/3 |
| OpenAI | Subagent using the same model | 0.11160 | +31.6% | 19.59 s | 3/3 |
| OpenAI | Cheaper subagent | **0.02266** | **−73.3%** | 19.38 s | 3/3 |
| Anthropic | Cache | 0.07313 | - | 10.21 s | 3/3 |
| Anthropic | Subagent using the same model | 0.11039 | +51.0% | 32.69 s | 3/3 |
| Anthropic | Cheaper subagent | **0.02470** | **−66.2%** | 24.28 s | 3/3 |

Configured models: GPT-5.6 Sol with GPT-6 Luna on OpenAI; Claude Sonnet 5.5 with
Claude Haiku 5.5 on Anthropic. The same-model variant uses the main model in both roles.
These names identify the models requested in the configurations, not verified returned versions.

Together, the two runs recorded **168 calls**, 18 sessions, and **USD 1.28175** in calculated
cost. Complete usage accounting and valid cache controls were reported for every condition.

### What the numbers show

- **Less context in the main agent does not imply an equal reduction in total processing.**
  With the cheaper subagent, cumulative main-agent input tokens fell by 93.2% on OpenAI
  and 85.8% on Anthropic. Across both roles, the reductions were 18.0% and 8.7%.
- **The model used for delegation matters.** Delegating to the same model cost more than
  the baseline. With the cheaper model, some processing moved to a lower rate.
  The comparison also includes differences in the notes and answers generated.
- **There is a time cost.** Delegation in this experiment is sequential: each turn waits
  for the subagent before calling the main agent. A session makes 11 calls, versus 6 in the baseline.
- **Enabling caching does not guarantee cache reads.** On OpenAI, the main agent in the
  delegated variants reported no cache reads; its subagents reached approximately 66.6%.
  Short notes can reduce opportunities for cache reuse.

Example calculation using the total costs of the three repetitions:

```text
OpenAI:    (1 − 0.0679741 / 0.2543148) × 100 = 73.3%
Anthropic: (1 − 0.07409805 / 0.2193878) × 100 = 66.2%
```

The published values are in [benchmarks/delegation-validation.csv](benchmarks/delegation-validation.csv).
This file transcribes the `context-bench report` aggregates supplied by the author for
`openai-delegation-validation-01` and `anthropic-delegation-validation-01`.
The raw files and manifests from those runs remain local and are not included in this snapshot.
Earlier runs using a different output contract are excluded from this table.

## How it works

The scenario simulates an investigation into a payment-processing incident. Evidence
arrives over five turns: an initial hypothesis, its correction, the identified cause,
and two sandbox checks. The sixth turn requests a JSON answer. Irrelevant logs
progressively increase the context.

Evaluation checks five items: cause, action, human approval requirement, discarded
hypothesis, and a reference to the causal evidence. It uses deterministic rules, without
an LLM judge. Expected values come from the evidence; the system prompt describes the
format without providing the answer key.

| Strategy | What changes | Calls per session |
|---|---|---:|
| `full` | Full history with caching disabled for the control | 6 |
| `cache` | Full history with caching enabled | 6 |
| `summary` | One summary call replaces the history after three turns; caching enabled | 7 |
| `delegate_same_model` | A subagent keeps the raw evidence; the main agent receives notes; both use the same model | 11 |
| `delegate_small_model` | The same delegation flow, using a different model for the subagent | 11 |

There is one subagent. It maintains its own history and receives only evidence that has
already been released. Each turn, it produces notes of up to 200 words-a prompt instruction,
not a limit enforced through local truncation. The main agent receives these notes as data,
maintains its history, and answers the final turn. Both use the same cache policy.
Neither agent executes actions or accesses real tools.

### The answer format is part of the experiment

One diagnostic run returned `retry_storm` and `database_latency` with explanations appended.
The information was present, but the fields failed exact matching. Another answer was not
valid JSON; the available diagnostic does not reveal its form.

The output contract was updated to require identifiers without explanations and a JSON
object without Markdown. The instruction was applied to all providers and strategies,
while keeping evaluation strict. The baselines were rerun. Results before and after
this change must therefore be analyzed as separate batches.

## Run it

Requirements: Python 3.13 or 3.14 and `uv`. The core uses official SDKs and local files;
it does not require a server, database, or agent framework.

### Start without credentials

```bash
uv sync --frozen
uv run context-bench demo --output results/demo-01
uv run context-bench report results/demo-01
```

The demo uses fixed responses, zero token counts, and null costs. It checks the installation
and file workflow; it does not measure model performance or quality.

### Run against the APIs

Set `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` in the environment or a local `.env` file.
Check model availability in your account and the rates in `configs/` before using `--live`.
The example rates are dated snapshots, not a current pricing lookup.

For one repetition of the three delegation strategies:

```bash
uv run --env-file .env context-bench run \
  --config configs/openai-delegation.toml \
  --output results/openai-delegation-01 --live

uv run --env-file .env context-bench run \
  --config configs/anthropic-delegation.toml \
  --output results/anthropic-delegation-01 --live
```

These configurations schedule 28 calls per provider. To reproduce the pilot design, use
`configs/openai-delegation-validation.toml` and `configs/anthropic-delegation-validation.toml`:
three repetitions and 84 calls per provider. Always use a new output directory.

To compare `full`, `cache`, and `summary`, use `configs/openai.toml` or
`configs/anthropic.toml`. With three repetitions, these schedule 57 calls per provider.

### Read the results

```bash
uv run context-bench report results/openai-delegation-01
```

| Local file | Contents |
|---|---|
| `manifest.json` | Configuration, SDK versions, scenario and delegation hashes, strategy order, and run status |
| `calls.jsonl` | Usage, cost, requested/returned model, role, operation, bytes, and latency per call |
| `sessions.jsonl` | Session status, pass/fail result, and individual evaluation checks |
| `summary.csv` | Aggregates by strategy, including cost per successful task |

To investigate failures, add `--diagnostics` to `run`. This records only the five evaluated
fields from the final JSON, including their presence and type. Text is limited to 256
characters and evidence to ten entries; content of other unexpected types is not saved.
The option is off by default and does not change requests or evaluation.

```bash
jq '{strategy, passed, checks, answer_diagnostics}' results/YOUR_RUN/sessions.jsonl
```

`Completed` means the run finished, not that every task passed. API errors stop the run;
the SDKs are configured with `max_retries=0`. Empty or truncated output fails the session.
There is no automatic answer repair or resumption of an interrupted run.

## Method and limitations

- Strategy order is shuffled per repetition, with the seed recorded. Prefixes are unique
  to each session/role and remain stable within their histories.
- Cache use is measured from returned counters. Turns are identified in JSONL: the first
  is not assumed to be cold, nor are subsequent turns assumed to be cache hits.
- `known_input_tokens` sums inputs across requests, including cache reads and writes.
  It does not represent peak context-window size or unique tokens.
- `task_latency_*` measures only main-agent task calls. Use `session_elapsed_p50_ms`
  to compare the entire flow. TTFT measures time to the first text delta.
- Cost includes calls from both roles, including failed attempts with known usage.
  Missing usage or rates produce an unknown cost (`null`), not zero.
- The scenario text is shared, but tokenization, answers, rates, and reasoning defaults differ.
  Compare strategies within each provider first. This pilot is not a model ranking.
- Three repetitions of one case do not measure generalization. A p95 based on few calls
  is descriptive; this is neither a robust tail estimate nor a significance test.
- Summarization and delegation are implemented by the application. The project does not
  reproduce native compaction, Claude Code subagents, advisor, Message Threads, MCP/A2A,
  or parallel execution.

<details>
<summary>Cache, pricing, and accounting details</summary>

The OpenAI adapter uses Responses with `prompt_cache_options.mode = "explicit"` and a
30-minute TTL. `full` omits breakpoints; the other strategies retain breakpoints for the
instructions and earlier user messages. This requires a model/API compatible with the
explicit control and the `cache_write_tokens` usage field. The adapter fails when required
usage is missing, without treating missing values as zero or changing the cache policy.

On Anthropic, `full` omits `cache_control`; the other strategies send automatic caching
with a five-minute TTL. TTLs differ between providers.
`cache_control_valid` does not guarantee a hit: the aggregate is also false when calls
fail without a recorded validation. In the uncached control, unexpected reads/writes
invalidate the control.

`[rates]` prices the main agent and the subagent using the same model. `[subagent.rates]`
prices the alternative model. There are four rates in USD per million tokens, plus an
`as_of` date:

```text
cost = (ordinary input × input rate
      + cache reads × read rate
      + cache writes × write rate
      + output × output rate) / 1,000,000
```

The write rate is the total price, not a surcharge. Calculations use only the configured
rates; they do not adjust for context-length tiers, batch pricing, contractual discounts,
or tools. Check the tiers and dates documented in the TOML examples before reusing them.

On Anthropic, total input is the sum of ordinary input, cache reads, and cache creation.
On OpenAI, reads and writes are already included in `input_tokens` and are subtracted
to obtain ordinary input. Reasoning tokens already billed as output are not counted again.

Latency includes receipt of final usage; bytes measure the body serialized by the SDK,
excluding headers/TLS. Session time includes telemetry and local file writes.

</details>

## Optional telemetry

The project uses the core of [a2a-otel-kit](https://github.com/brunovicco/a2a-otel-kit)
for OTLP spans. The package name does not imply use of the A2A protocol in this benchmark.

```bash
uv sync --frozen --extra telemetry
uv run --env-file .env context-bench run \
  --config configs/anthropic-delegation.toml \
  --output results/anthropic-traced-01 --live \
  --otlp-endpoint http://localhost:4318/v1/traces
```

Provide your own collector. Spans record operation, role (`component`), correlation,
duration, and outcome. Token accounting stays in JSONL and is linked through the trace ID.
Prompts, intermediate notes, headers, keys, and exception messages are not recorded.
`--diagnostics` allows only the limited recording of final fields described above.
Keep telemetry settings the same when comparing timings.

## Development

```bash
uv sync --frozen --all-extras --all-groups
uv run python scripts/quality_gate.py
uv build
```

The quality gate checks the lockfile, lint, formatting, architecture, types, tests, security,
and dependencies. Tests use simulated providers to check isolation, chronology, accounting,
failures, and diagnostics. Model quality must be measured through live experiments.

This project derives from [codex-python-engineering-harness](https://github.com/brunovicco/codex-python-engineering-harness),
`library` profile, version 1.2.1. Its origin is recorded in `.harness.json`.
It keeps a `src` layout, official SDKs, and a small CLI. Code is released under the [MIT license](LICENSE).

A useful next extension is to add scenarios with conflicting corrections and essential
evidence that is harder to summarize, to see whether savings persist when preserving
information becomes more demanding.
