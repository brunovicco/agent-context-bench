# agent-context-bench

**English** | [Português (Brasil)](README.pt-br.md)

**How much does maintaining context cost-and when is it worth delegating work to another model?**

This project compares full conversation history, caching, summarization, delegation,
and status updates using the OpenAI and Anthropic APIs. It measures cost, elapsed time, and final-answer
correctness for the same incident, with separate accounting for the main agent and subagent.

In the delegation pilot, using a cheaper subagent reduced cost per successful task by
**73.3% on OpenAI** and **66.2% on Anthropic**, compared with the cached baseline. Median
session time increased to **1.99×** and **2.38×**, respectively. Delegating to the same
model cost more on both providers.

**Scope of these results:** three repetitions per condition, one synthetic scenario,
and live API calls. Every condition passed in 3/3 sessions. This is an initial measurement
of repeatability; it does not establish equivalent quality across different tasks.

[Results](#pilot-results) · [Status updates](#status-update-experiment-v030) · [How it works](#how-it-works) ·
[Run it](#run-it) · [Method](#method-and-limitations) · [Development](#development)

## Pilot results

Costs are calculated from the configured rates. Time is the median for the entire session,
including delegation calls. The `cache` condition is the baseline within each provider.

| Provider | Strategy | USD per successful task | Cost change | Median time | Passed |
| --- | --- | ---: | ---: | ---: | ---: |
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
| --- | --- | ---: |
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

## Status update experiment (v0.3.0)

This experiment compares the cost of producing status updates from the main agent's full
conversation history versus short working notes. The main agent still runs the same six-turn
incident investigation in every condition. Notes and status responses never enter its history.

| Strategy | Status input | Status model | Calls per session, with one update per point |
| --- | --- | --- | ---: |
| `status_full_history` | All main-agent user/assistant messages received so far | Main model | 6 task + 5 status = 11 |
| `status_notes_same_model` | Latest incremental notes | Main model | 6 task + 5 notes + 5 status = 16 |
| `status_notes_small_model` | Latest incremental notes | `[subagent].model` | 6 task + 5 notes + 5 status = 16 |

The notes generator always uses the main model and its rates in both notes conditions.
It receives the previous notes and only the latest task exchange, and must produce at most
200 words. The prompt targets about 150 words, with shorter notes allowed, to leave room
below the hard limit. Notes are generated once per observation point and reused for repeated updates
at that point. Their generation cost is included. Outputs are not truncated to meet the limit.
Both notes conditions use the same prompts and settings; model outputs can vary between sessions.

Status generation has its own isolated context and a common prompt across the three conditions.
It returns JSON containing the latest evidence ID, confirmed cause (or null), discarded
hypothesis (or null), human approval requirement, and a status description of at most 40 words.
The common status prompt targets about 25 words; this is guidance, not a new validation limit.
Checks validate those structured facts against the current observation point, and check the
text's presence and length. They do not evaluate the free text's full semantic quality.
No status response becomes input to a later update. Cache is enabled for task, note, and status
calls, with the existing provider policy and isolated prefixes per session/role.

**Timing:** updates run sequentially after each of the five evidence turns, before the final
answer. `status_updates_per_turn` controls how many updates use each snapshot (default 1,
integer from 1 to 20). There is no wall-clock scheduler, 30-second wait, or concurrent execution.
Increasing this setting measures repeated observations of unchanged evidence; cache behavior
and elapsed time may differ from a real background agent. This is an application experiment,
not a reproduction of Claude Code internals or a measurement of subscription usage limits.

The final task and every scheduled status update must pass, and notes must meet the size limit,
for a session to count as `passed`. `task_passed` separately counts correct final task answers;
`status_pass_rate` covers expected updates, including those missing after an interruption.
Structured checks for notes/status appear in `calls.jsonl` without their text.
Note calls also record `notes_text_diagnostics`: `text_nonempty`, `text_word_count`, and
`text_within_limit`, using `split()` and the unchanged 200-word limit. These metadata are
recorded for returned responses, including incomplete ones, without saving the notes.
Status calls also record `status_text_diagnostics`: `text_present`, `text_is_string`,
`text_nonempty`, `text_word_count`, and `text_within_limit`. These metadata are always
recorded for returned responses, including incomplete ones; no response text is saved.
The word count uses `split()` and is null for missing or non-string fields. Empty strings
have zero words and meet the size limit, but still fail the nonempty check. Invalid JSON
or a non-object response is treated as missing fields. The 40-word limit and approval
checks are unchanged. Existing runs cannot recover these metadata retroactively. `--diagnostics`
continues to apply only to the main agent's final answer. API errors stop the run; incomplete
or empty auxiliary responses stop the current session and retain known usage.

The 150/25-word prompt targets change `status_prompt_sha256` in the manifest. Keep prior
runs, including failures, and use new output directories for this revision. Compare all three
conditions with the same prompt revision; do not pool different hashes as one experiment.
No automatic retry or truncation is added. The completed runs below use the revised prompts.

| Report field | Meaning |
| --- | --- |
| `workload_cost_usd` | Main investigation calls |
| `notes_cost_usd` | Incremental note generation |
| `status_cost_usd` | Status rendering calls only |
| `status_update_cost_mean_usd` | Average cost per attempted status rendering call |
| `status_pipeline_cost_usd` | Notes plus status rendering |
| `total_cost_usd` / `session_cost_mean_usd` | All calls, as a run total / mean per started session |
| `status_overhead_fraction` | Share of total cost spent on notes and status |
| `status_update_savings_fraction` | Rendering cost reduction versus `status_full_history` |
| `status_pipeline_savings_fraction` | Notes + rendering cost reduction versus `status_full_history` |
| `session_savings_fraction` | Total session cost reduction versus `status_full_history` |
| `cost_per_passed_task_usd` | All attempted-call costs divided by fully passed sessions (`passed`), not only correct final answers (`task_passed`); legacy field name |

Savings are fractions: `0.25` means 25% less, negative values mean more expensive. They are
reported only for a completed run with all planned repetitions, equal session/update counts,
all sessions passing, and known comparison costs with a positive baseline. Otherwise they
are `null`. A cheaper update does not necessarily mean a cheaper session. Partial runs must
not be compared as completed workloads, even when their partial costs are known.

The manifest records `status_prompt_sha256`, the schedule, and update count. Schema version 3
adds these metrics while keeping older JSONL reports readable. Delegation results above
remain a separate experiment.

### Audited live status results

[Consolidated CSV](benchmarks/status-validation.csv) contains 12 conditions from four
completed runs. Each run has three repetitions of each strategy: **9/9 sessions passed**,
with **45/45 status updates at `k = 1`** or **135/135 at `k = 3`**. Each run generated
30 notes and made 129 or 219 calls, respectively. Across these runs: 696 calls,
36 passed sessions, 360 passed updates, and 120 note generations. `synthetic: false`
means real API calls; the incident itself remains synthetic.

Sources are the local `manifest.json`, `calls.jsonl`, and `sessions.jsonl` in:

| Provider | Updates per turn | Source directory under `results/` | Date (UTC) |
| --- | ---: | --- | --- |
| OpenAI | 1 | `openai-status-length-validation-01` | 2026-11-07 |
| Anthropic | 1 | `anthropic-status-length-validation-01` | 2026-11-07 |
| OpenAI | 3 | `openai-status-reuse3-03` | 2026-11-07 |
| Anthropic | 3 | `anthropic-status-reuse3-03` | 2026-11-16 |

All four use status prompt SHA-256
`ec1cbb883ff80e79b5883d0fb88ba8b47f50d8e4f4776252b776337075d2326b`
and scenario SHA-256
`1fec88977685fae92acf8800484af594e3ddded0c1a74b21ecfd89fbd7561de5`.
The current code matches both hashes. Configurations match their manifests: seed 42,
2,048 output tokens, 60-second request timeout, three repetitions, SDKs OpenAI 2.54.0
and Anthropic 0.125.0. Requested and returned model strings match in every recorded call;
these aliases do not establish immutable model versions.

Configured USD per million tokens, dated **2026-11-06**, reproduced here as historical
experiment inputs rather than a current price quotation:

| Provider / role | Model | Ordinary input | Cache read | Cache write (total) | Output |
| --- | --- | ---: | ---: | ---: | ---: |
| OpenAI main / notes | `gpt-5.6-sol` | 4 | 0.4 | 5 | 20 |
| OpenAI cheap status | `gpt-6-luna` | 0.1 | 0.01 | 0.125 | 0.5 |
| Anthropic main / notes | `claude-sonnet-5-5` | 2 | 0.1 | 2.5 | 10 |
| Anthropic cheap status | `claude-haiku-5-5` | 0.1 | 0.01 | 0.125 | 0.5 |

Raw usage normalization and every recorded call cost were checked against these rates.
Usage is complete, all calls requested cache, and cache controls are valid throughout.
OpenAI uses explicit Responses breakpoints in the system/developer content and user
messages, with a 30-minute TTL; Anthropic sends automatic ephemeral caching with a
five-minute TTL. This policy is the same across all three status strategies, including
notes and task calls. A cache request does not imply a hit. In the full-history **status
calls only**, observed read/input fractions were 66.707118% (OpenAI, `k = 1`), 0%
(Anthropic, `k = 1`), 88.860982% (OpenAI, `k = 3`), and 66.648076% (Anthropic, `k = 3`).
The denominator includes ordinary input, cache reads, and cache writes; it counts tokens
across requests, not unique context. Per-condition cache and token totals are in the CSV.

**One update per evidence turn (`k = 1`):**

| Provider | Strategy | Total USD, 3 sessions | Mean USD/session | Median session time |
| --- | --- | ---: | ---: | ---: |
| OpenAI | Full history | 0.5061482 | 0.168716067 | 20.84 s |
| OpenAI | Notes, same model | 0.5489598 | 0.182986600 | 33.67 s |
| OpenAI | Notes, cheap model | 0.5165627 | 0.172187567 | 38.40 s |
| Anthropic | Full history | 0.7795653 | 0.259855100 | 17.65 s |
| Anthropic | Notes, same model | 0.5255119 | 0.175170633 | 37.98 s |
| Anthropic | Notes, cheap model | 0.47431685 | 0.158105617 | 38.02 s |

**Three updates sharing each snapshot (`k = 3`):**

| Provider | Strategy | Total USD, 3 sessions | Mean USD/session | Median session time |
| --- | --- | ---: | ---: | ---: |
| OpenAI | Full history | 0.6567402 | 0.218913400 | 36.91 s |
| OpenAI | Notes, same model | 0.6396958 | 0.213231933 | 42.75 s |
| OpenAI | Notes, cheap model | 0.5173699 | 0.172456633 | 55.68 s |
| Anthropic | Full history | 0.8580869 | 0.286028967 | 44.65 s |
| Anthropic | Notes, same model | 0.5617414 | 0.187247133 | 62.30 s |
| Anthropic | Notes, cheap model | 0.485141845 | 0.161713948 | 64.53 s |

Every comparison below uses `1 - strategy_cost / full_history_cost` within the same
provider and update count. Positive percentages mean savings; negative ones mean extra cost.

| Provider | Updates/turn | Notes strategy | Status rendering savings | Notes + status savings | Whole-session savings |
| --- | ---: | --- | ---: | ---: | ---: |
| OpenAI | 1 | Same model | 83.487117% | −17.423849% | −8.458313% |
| OpenAI | 1 | Cheap model | 99.407986% | −3.482398% | −2.057599% |
| Anthropic | 1 | Same model | 91.781068% | 46.201803% | 32.589111% |
| Anthropic | 1 | Cheap model | 99.125974% | 53.930846% | 39.156239% |
| OpenAI | 3 | Same model | 68.039085% | 2.946856% | 2.595303% |
| OpenAI | 3 | Cheap model | 98.834827% | 33.768857% | 21.221527% |
| Anthropic | 3 | Same model | 86.635873% | 46.747130% | 34.535605% |
| Anthropic | 3 | Cheap model | 98.066242% | 58.099438% | 43.462388% |

Note generation is a substantial cost, even when status rendering is very cheap.
For `k = 3`, the composition of the three-session total is:

| Provider | Strategy | Investigation USD | Notes USD | Status USD |
| --- | --- | ---: | ---: | ---: |
| OpenAI | Full history | 0.2568768 | 0 | 0.3998634 |
| OpenAI | Notes, same model | 0.2516158 | 0.2602800 | 0.1278000 |
| OpenAI | Notes, cheap model | 0.2525358 | 0.2601750 | 0.0046591 |
| Anthropic | Full history | 0.2239669 | 0 | 0.6341200 |
| Anthropic | Notes, same model | 0.2240543 | 0.2529425 | 0.0847446 |
| Anthropic | Notes, cheap model | 0.2194420 | 0.2534375 | 0.012262345 |

The absent notes operation costs zero; an attempted call with missing accounting remains
`null`. Each notes condition generates 15 notes across its three sessions at either `k`.
Amortized note cost per update is `notes_cost / (15 × k)`; for the cheap-model condition,
it falls from USD 0.0174133 to 0.0057817 on OpenAI and from USD 0.0167460 to 0.0056320
on Anthropic between these runs. Note outputs and main-task costs vary, so the realized
session saving is measured separately. On OpenAI, sharing each note three times changed
both notes conditions from extra cost to savings against the corresponding control.
Executing more updates did **not** reduce absolute session cost: compare each strategy
with the full-history control at the same `k`. Rendering savings near 99% are not session
savings. Median sequential session time increased with notes in every comparison;
at `k = 3`, cheap-model notes took 55.68 versus 36.91 seconds on OpenAI and 64.53 versus
44.65 seconds on Anthropic. These measurements include serial note/status generation,
network time, and local overhead; they do not isolate model speed or predict concurrency.

### Failure history and limits

Earlier prompts violated size constraints. For example, `anthropic-status-validation-01`
passed 7/9 sessions with two failed status-text checks and one failed note-word-limit check.
Those runs retain the earlier prompt hash and are excluded from the consolidated CSV.
The revised prompts passed all four completed runs without relaxing the 40/200-word limits,
truncating outputs, or retrying for quality. This is a small pilot, not proof of reliability.

Interrupted attempts remain in `results/`. OpenAI `reuse3-01` and `reuse3-02` record
`APIError`; the user reported insufficient balance and recharging. The user also reported
insufficient Anthropic balance, a later failed attempt, and a successful minimal call.
Local Anthropic files differ from the supplied narrative: `reuse3-01` records
`BadRequestError` (160 calls, 7 started sessions), while `reuse3-02` records
`APITimeoutError` (141 calls, 6 started sessions), rather than a quick `BadRequestError`
in `02`. The saved error classes cannot establish the underlying causes, and no exception
messages were retained. Missing updates after interruption are schedule shortfalls,
not observed model-content failures. Unknown costs remain `null`.

All four complete source sets are present. Raw results stay local and ignored by Git;
the public CSV preserves source-run provenance and aggregate metrics, without prompts or
raw answers. Configured-rate costs are not a reconciliation with provider invoices.
The `k = 3` provider runs occurred nine days apart; model aliases, service variation,
and execution conditions further limit direct comparisons. These results do not establish
an OpenAI-versus-Anthropic ranking, reproduce Claude Code internals, or measure subscriptions.
Only structured status fields and description size are checked; note semantics and the
full semantic quality of free-text status descriptions are not evaluated.

### Reproduce reports or run a new comparison

Regenerate the public CSV offline from explicitly selected local source runs, then compare
it with the committed result:

```bash
uv run context-bench consolidate \
  results/openai-status-length-validation-01 \
  results/anthropic-status-length-validation-01 \
  results/openai-status-reuse3-03 \
  results/anthropic-status-reuse3-03 \
  --output /tmp/status-validation-recomputed.csv
diff -u benchmarks/status-validation.csv /tmp/status-validation-recomputed.csv
```

The output path must be new. `consolidate` reads schema-3 live status runs and validates
completed manifests, planned repetitions, session associations, and the full task/note/status
call schedule. Missing files, interrupted runs, incomplete responses, and missing or duplicate
scheduled calls fail before creating the CSV. Session approval flags and status pass counts
must agree with the saved boolean checks; contradictory or malformed quality metadata is
rejected. Existing source files, including `summary.csv`, are never rewritten. Every run keeps
its own baseline, provider, update count, model aliases, and hashes; runs are not pooled, even when supplied in the same command. Completed sessions
with failed quality checks can be exported, with no savings claimed for those conditions.
Unknown costs are written as `null`. This reuses recorded costs and evaluation results;
it does not reprice calls or reevaluate answers that were never retained.

Recompute existing local reports without API calls:

```bash
uv run context-bench report results/openai-status-length-validation-01
uv run context-bench report results/anthropic-status-length-validation-01
uv run context-bench report results/openai-status-reuse3-03
uv run context-bench report results/anthropic-status-reuse3-03
```

For a separately authorized paid reproduction, use the committed `*-status-validation.toml`
(`k = 1`) or `*-status-reuse3.toml` (`k = 3`) configuration and a **new** output directory:

```bash
uv run --env-file .env context-bench run \
  --config configs/openai-status-validation.toml --output results/openai-status-validation-NEW --live --diagnostics
uv run --env-file .env context-bench run \
  --config configs/anthropic-status-validation.toml --output results/anthropic-status-validation-NEW --live --diagnostics
uv run --env-file .env context-bench run \
  --config configs/openai-status-reuse3.toml --output results/openai-status-reuse3-NEW --live --diagnostics
uv run --env-file .env context-bench run \
  --config configs/anthropic-status-reuse3.toml --output results/anthropic-status-reuse3-NEW --live --diagnostics
```

Each validation configuration plans 129 calls; each reuse configuration plans 219 calls.
The shorter `*-status.toml` examples use one repetition and plan 43 calls. In general,
the three strategies make `28 + 15 × k` calls per repetition. `--diagnostics` is optional
and saves only bounded evaluated final fields from this synthetic incident. Check historical
model availability and rates before any paid run; do not reuse an existing output directory.

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
