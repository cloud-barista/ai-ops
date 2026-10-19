"""Plot the frozen BurstGPT test's daily token loss and empirical coverage."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts/burstgpt_output_tokens_v1"


def main() -> None:
    metrics = json.loads((ARTIFACTS / "test_metrics.json").read_text())
    if metrics["selected_method"] != "hgb_base":
        raise ValueError("The plot labels assume the frozen selection is hgb_base")

    fig, axes = plt.subplots(2, 1, figsize=(9.0, 6.5), sharex=True)
    labels = {
        "hgb_base": "Selected quantile model",
        "rolling_bin": "Recent-bin baseline (post-test reference)",
    }
    colors = {"hgb_base": "#b43c38", "rolling_bin": "#1f6d97"}
    for method, label in labels.items():
        by_day = metrics["methods"][method]["by_day"]
        days = sorted(int(day) for day in by_day)
        axes[0].plot(
            days,
            [by_day[str(day)]["pinball_tokens"] for day in days],
            marker="o", markersize=3, linewidth=1.6,
            label=label, color=colors[method],
        )
        axes[1].plot(
            days,
            [by_day[str(day)]["coverage"] for day in days],
            marker="o", markersize=3, linewidth=1.6,
            label=label, color=colors[method],
        )
    axes[0].set_ylabel("Daily p90 pinball loss (tokens)")
    axes[0].set_title("BurstGPT output-token test, relative days 315–334")
    axes[0].grid(alpha=0.25)
    axes[0].legend(fontsize=8)
    axes[1].axhline(0.90, color="#444444", linestyle="--", linewidth=1, label="Nominal 90%")
    axes[1].set_ylabel("Empirical coverage")
    axes[1].set_xlabel("Relative request day")
    axes[1].set_ylim(0, 1.02)
    axes[1].set_xticks(range(315, 335, 2))
    axes[1].grid(alpha=0.25)
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(ARTIFACTS / "daily_drift.png", dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    main()
