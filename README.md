# agent-context-bench

**English** | [Português (Brasil)](README.pt-br.md)

When an agent receives evidence over several turns, when do caching, summarization or
delegation pay for themselves? A cheaper subagent can reduce the cost of a call while
adding calls and making it harder for the main agent to retain a correction. Measuring
the full workflow shows what happens to cost, latency and the final answer together.

`agent-context-bench` is a Python CLI that compares these strategies on synthetic
incident investigations with OpenAI and Anthropic. It records per-call accounting and
evaluates final answers against a versioned rubric. Results are stored locally in JSONL
and CSV. Telemetry is optional.

[Results](#results) · [Method and limitations](#method-and-limitations) ·
[Run it](#run-it) · [Reproduce the published results](#reproduce-the-published-results)

## Results

### A standalone model is the baseline for delegation

In the original replay, all four strategies passed every rubric check in all three
repetitions on both providers. The lower-cost model alone was the cheapest strategy
and finished faster than the flow that delegated to that same model.
The model pairs were GPT-5.6 Sol and GPT-6 Luna on OpenAI, and Sonnet 5.5 and Haiku
5.5 on Anthropic.

![Estimated cost by model role and median session time for the four strategies on OpenAI and Anthropic](benchmarks/controls-cost-comparison.svg)

The standalone model cost **91.1% less on OpenAI and 85.3% less on Anthropic** than
lower-cost delegation. The delegated workflow still pays for the main model and adds
one subagent call per evidence turn. The medians cover the entire session.
[Exact costs and times](benchmarks/experiments.md#task-controls-on-the-original-replay) ·
[Results CSV](benchmarks/controls-validation.csv) · [Sanitized evidence](benchmarks/controls-evidence)

### Call timing can matter even with caching

Calling the main model only on the final turn reduced estimated cost per passed task
in all eight comparisons with the earlier every-turn control. All 24 sessions passed
under `final_only`, the same count as in the historical control. The observed reductions
ranged from **9.68% to 73.52%**. Both standalone strategies saved money with zero observed
cache reads in the final-only runs. Delegation still made five evidence-processing calls
per session and recorded cache reads.

### Corrections can fail in a delegated workflow

Across the ambiguous-evidence, repeated-correction and dense-constraint variants, **71 of
72 sessions passed**. The failure was on Anthropic with `delegate_small_model` in
`corrections`. A rerun reproduced the failure, with empty cause and evidence fields.
A separate in-memory check found both required identifiers in every final handoff,
including the handoffs for four failed answers. This established that the identifiers
reached the main model while leaving their interpretation unresolved.

In a six-pair follow-up, appending the original final correction, E5, to Sonnet's final
request raised passes from **2/6 to 6/6**. Each pair shared the same prior conversation.
The four recoveries covered both cause and evidence. This tests a mitigation for the
synthetic scenario. The original failure mechanism remains open.

### Reusing status notes can offset their cost

In the historical status experiment, reusing each note three times with a lower-cost
renderer reduced whole-session cost by **21.2% on OpenAI and 43.5% on Anthropic**.
With one update on OpenAI, note generation
cost more than the rendering savings. The newer structured-note and local-rendering
controls have offline coverage and await live measurement.
[Historical status results](benchmarks/status-validation.csv)

The [experiment reference](benchmarks/experiments.md) contains the expanded tables,
configuration choices, follow-up tests and local result paths. The public evidence bundle
covers the original task controls and earlier experiments.

## Method and limitations

Evidence arrives over five turns. A sixth turn requests a JSON answer. The `evidence-v2`
rubric checks the confirmed cause, action identifier, approval requirement, discarded
hypothesis and causal evidence IDs. Strategies within a run share the scenario and rubric.
Order is shuffled within each repetition, and session-specific prefixes isolate context.

Costs include both agent roles and use the rates configured for the run. Missing cost is
recorded as `null`. Cache hits come from provider usage counters. Use session elapsed time
to compare complete workflows and call latency to compare individual requests.

These are small synthetic evidence-retention tasks with explicit conclusions. Three
repetitions per condition provide descriptive comparisons. Provider comparisons use
different models and rates, and `final_only` uses a control from an earlier run. The
[experiment reference](benchmarks/experiments.md) describes provider-specific accounting
and the exact protocols.

## Run it

Requirements are Python 3.13 or 3.14 and `uv`. The CLI uses the official provider SDKs.

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

For three repetitions of the four task controls:

```bash
uv run --env-file .env context-bench run \
  --config configs/openai-controls.toml \
  --output results/openai-controls-NEW --live

uv run --env-file .env context-bench run \
  --config configs/anthropic-controls.toml \
  --output results/anthropic-controls-NEW --live
```

These configurations schedule 102 calls per provider. They include the cheap model alone,
with the same scenario, cache policy and output contract as the other conditions.
Always use a new output directory. The earlier three-condition configs remain available
as `*-delegation-validation.toml` (84 calls per provider).

To compare `full`, `cache`, and `summary`, use `configs/openai.toml` or
`configs/anthropic.toml`. With three repetitions, these schedule 57 calls per provider.

### Read the results

```bash
uv run context-bench report results/openai-controls-NEW
```

| Local file | Contents |
| --- | --- |
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

## Reproduce the published results

```bash
uv run python scripts/reproduce_controls.py
uv run python scripts/reproduce_benchmarks.py
```

These commands verify the checksums and accounting in two task-control runs and
eight earlier runs, then reconstruct four CSVs without API calls. The public files
include manifests, usage and rubric checks. The [evidence bundles](benchmarks/controls-evidence)
and the [historical runs](benchmarks/evidence) contain the source data. The original
task-control rates are documented in [the provenance record](benchmarks/controls-evidence/provenance.json).

## Development

```bash
uv sync --frozen --all-extras --all-groups
uv run python scripts/quality_gate.py
uv build
```

The simulated-provider tests cover context isolation, provider payloads, accounting
and failure recovery. See the [experiment reference](benchmarks/experiments.md)
for chart regeneration and optional OTLP telemetry.

Derived from [codex-python-engineering-harness](https://github.com/brunovicco/codex-python-engineering-harness),
`library` profile v1.2.1. [MIT License](LICENSE).
