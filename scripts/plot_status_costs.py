"""Regenerate status costs in the supplied SVG's embedded-font visual style."""

import csv

from svg_charts import ROOT, Chart, Row


def main() -> None:
    """Export the historical three-reuse pilot with its original shared scale and colors."""
    with (ROOT / "benchmarks/status-validation.csv").open(encoding="utf-8") as stream:
        data = [row for row in csv.DictReader(stream) if row["status_updates_per_turn"] == "3"]
    title = "Session cost with repeated status updates"
    chart = Chart(
        title,
        "Stacked horizontal bars of calculated mean session cost in USD, "
        "split into investigation, note generation and status rendering for OpenAI and "
        "Anthropic. Compare strategies within each provider.",
        1000,
        "en",
    )
    chart.header(
        "AGENT-CONTEXT-BENCH  ·  STATUS UPDATES DURING LONG RUNS",
        title,
        "Reusing incremental notes with a cheaper model cut total session cost by "
        "21.2% on OpenAI and 43.5% on Anthropic.",
        ("Investigation", "Note generation", "Status rendering"),
        "Mean calculated cost per session (USD)",
    )
    strategies = ("status_full_history", "status_notes_same_model", "status_notes_small_model")
    for index, (provider, date) in enumerate(
        (("openai", "2026-11-07"), ("anthropic", "2026-11-16"))
    ):
        rows = []
        for strategy, label in zip(
            strategies, ("Full history", "Notes + same model", "Notes + cheap model"), strict=True
        ):
            source = next(
                row for row in data if row["provider"] == provider and row["strategy"] == strategy
            )
            sessions = int(source["sessions"])
            components = tuple(
                float(source[key]) / sessions
                for key in ("workload_cost_usd", "notes_cost_usd", "status_cost_usd")
            )
            rows.append(
                Row(
                    label,
                    components,
                    -float(source["session_savings_fraction"]),
                    strategy == "status_notes_small_model",
                )
            )
        chart.panel(
            "OpenAI" if provider == "openai" else "Anthropic",
            date,
            346 + index * 282,
            rows,
            0.30,
            tuple(n * 0.05 for n in range(7)),
            "TOTAL",
            "CHANGE",
            "baseline",
            ("investigation", "notes", "status"),
            show_ticks=index == 1,
        )
        if index == 0:
            chart.line(80, 582, 1520, 582, "#9CA39C")
    chart.footer(
        944,
        "3 sessions per strategy · 15 status updates per session · "
        "synthetic payment-processing incident",
        "Historical configured rates. Compare strategies within each provider, "
        "not across providers.",
    )
    chart.save(ROOT / "benchmarks/status-cost-composition.svg")


if __name__ == "__main__":
    main()
