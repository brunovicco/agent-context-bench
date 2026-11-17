"""Plot the audited status cost composition; run with uv --with matplotlib."""

import csv
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Export a static figure using per-session means from the three-reuse pilot."""
    matplotlib.rcParams.update({"svg.fonttype": "none", "font.family": "DejaVu Sans"})
    with (ROOT / "benchmarks/status-validation.csv").open(encoding="utf-8") as stream:
        rows = [r for r in csv.DictReader(stream) if r["status_updates_per_turn"] == "3"]
    strategies = ("status_full_history", "status_notes_same_model", "status_notes_small_model")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
    colors = ("#334155", "#d97706", "#0891b2")
    for ax, provider, date in zip(
        axes, ("openai", "anthropic"), ("2026-11-07", "2026-11-16"), strict=True
    ):
        selected = [
            next(r for r in rows if r["provider"] == provider and r["strategy"] == s)
            for s in strategies
        ]
        bottom = [0.0] * 3
        for field, label, color in zip(
            ("workload_cost_usd", "notes_cost_usd", "status_cost_usd"),
            ("Investigation", "Note generation", "Status rendering"),
            colors,
            strict=True,
        ):
            values = [float(r[field]) / int(r["sessions"]) for r in selected]
            ax.bar(range(3), values, bottom=bottom, color=color, width=0.62, label=label)
            bottom = [b + v for b, v in zip(bottom, values, strict=True)]
        for x, total in enumerate(bottom):
            ax.text(x, total + 0.005, f"${total:.4f}", ha="center", fontsize=10)
        ax.set_title(
            f"{'OpenAI' if provider == 'openai' else 'Anthropic'} · {date}", fontsize=12, pad=12
        )
        ax.set_xticks(range(3), ("Full\nhistory", "Notes +\nsame model", "Notes +\ncheap model"))
        ax.set_ylim(0, 0.33)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.2)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("Mean calculated cost per session (USD)")
    fig.suptitle(
        "Session cost with repeated status updates", fontsize=17, fontweight="bold", y=0.98
    )
    fig.legend(
        *axes[0].get_legend_handles_labels(),
        loc="lower center",
        ncol=3,
        bbox_to_anchor=(0.5, 0.06),
        frameon=False,
    )
    fig.text(
        0.5,
        0.01,
        "3 sessions per strategy · 15 status updates per session · synthetic incident\n"
        "Historical configured rates; compare strategies within each provider, not providers.",
        ha="center",
        fontsize=9,
        color="#475569",
    )
    fig.tight_layout(rect=(0, 0.18, 1, 0.94))
    fig.savefig(ROOT / "benchmarks/status-cost-composition.svg", metadata={"Date": None})
    plt.close(fig)


if __name__ == "__main__":
    main()
