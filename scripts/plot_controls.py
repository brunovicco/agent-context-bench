"""Regenerate task-control SVGs using status-cost-composition.svg as the visual reference."""

import argparse
import csv

from svg_charts import ROOT, Chart, Row

STRATEGIES = ("cache", "cache_small_model", "delegate_same_model", "delegate_small_model")
LABELS = {
    "en": {
        "title": "Cheap-model-alone control: cost and time",
        "subtitle": (
            "Cheap model alone cost 91.1% less on OpenAI and 85.3% less on Anthropic than cheap"
            " delegation."
        ),
        "eyebrow": "AGENT-CONTEXT-BENCH  ·  FOUR TASK CONTROLS",
        "strategies": (
            "Main + cache",
            "Cheap alone + cache",
            "Delegation: same model",
            "Delegation: cheap model",
        ),
        "legend": ("Main agent", "Subagent"),
        "unit": "Mean calculated cost per session (USD)",
        "total": "TOTAL",
        "time": "MEDIAN TIME",
        "change": "VS MAIN",
        "baseline": "baseline",
        "short": ("main", "subagent"),
        "footer": (
            "3 sessions per condition · 204 API calls · all 24 sessions passed the configured "
            "checks"
        ),
        "limits": (
            "Synthetic replay. Historical configured rates. Compare strategies within each "
            "provider."
        ),
        "description": (
            "Horizontal bars split mean session cost into main-agent and subagent costs. "
            "Columns show total USD, median session time and cost change versus the cached main"
            " model. The cheap standalone model costs less and takes less time than cheap "
            "delegation on both providers."
        ),
    },
    "pt-br": {
        "title": "Modelo barato sozinho: custo e tempo",
        "subtitle": (
            "Modelo barato sozinho custou 91,1% menos na OpenAI e 85,3% menos na Anthropic que "
            "a delegação barata."
        ),
        "eyebrow": "AGENT-CONTEXT-BENCH  ·  QUATRO CONTROLES DE TAREFA",
        "strategies": (
            "Principal + cache",
            "Barato sozinho + cache",
            "Delegação: mesmo modelo",
            "Delegação: modelo barato",
        ),
        "legend": ("Agente principal", "Subagente"),
        "unit": "Custo médio calculado por sessão (USD)",
        "total": "TOTAL",
        "time": "TEMPO MEDIANO",
        "change": "VS PRINCIPAL",
        "baseline": "controle",
        "short": ("principal", "subagente"),
        "footer": (
            "3 sessões por condição · 204 chamadas de API · as 24 sessões passaram nos checks "
            "configurados"
        ),
        "limits": (
            "Replay sintético. Tarifas históricas configuradas. Compare estratégias dentro de "
            "cada provedor."
        ),
        "description": (
            "Barras horizontais dividem o custo médio entre principal e subagente. Colunas "
            "mostram total em USD, tempo mediano e variação de custo frente ao principal com "
            "cache. O modelo barato sozinho custa menos e termina mais rápido que a delegação "
            "barata nos dois provedores."
        ),
    },
}


def plot(language: str) -> None:
    """Render the measured controls without changing the reference chart or source values."""
    labels = LABELS[language]
    with (ROOT / "benchmarks/controls-validation.csv").open(encoding="utf-8") as stream:
        data = list(csv.DictReader(stream))
    chart = Chart(labels["title"], labels["description"], 1140, language)
    chart.header(
        labels["eyebrow"], labels["title"], labels["subtitle"], labels["legend"], labels["unit"]
    )
    for index, provider in enumerate(("openai", "anthropic")):
        rows = []
        for strategy, label in zip(STRATEGIES, labels["strategies"], strict=True):
            source = next(
                row for row in data if row["provider"] == provider and row["strategy"] == strategy
            )
            sessions = int(source["sessions"])
            rows.append(
                Row(
                    label,
                    tuple(
                        float(source[key]) / sessions
                        for key in ("main_cost_usd", "subagent_cost_usd")
                    ),
                    -float(source["task_savings_vs_cache_fraction"]),
                    strategy == "cache_small_model",
                    float(source["session_elapsed_p50_ms"]) / 1000,
                )
            )
        chart.panel(
            "OpenAI" if provider == "openai" else "Anthropic",
            "2026-11-18",
            346 + index * 352,
            rows,
            0.12,
            tuple(n * 0.02 for n in range(7)),
            labels["total"],
            labels["change"],
            labels["baseline"],
            labels["short"],
            labels["time"],
            show_ticks=index == 1,
            decimals=5,
        )
        if index == 0:
            chart.line(80, 658, 1520, 658, "#9CA39C")
    chart.footer(1090, labels["footer"], labels["limits"])
    suffix = "" if language == "en" else ".pt-br"
    chart.save(ROOT / f"benchmarks/controls-cost-comparison{suffix}.svg")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("en", "pt-br"), default="en")
    args = parser.parse_args()
    plot(args.language)


if __name__ == "__main__":
    main()
