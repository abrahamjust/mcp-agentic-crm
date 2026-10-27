"""
Exploratory data analysis of the raw training data.

    python -m lead_scoring.training.eda [--csv PATH]

Writes reports/eda.md and reports/figures/eda_*.png: class balance,
missing values, conversion by category, numeric distributions by outcome,
and the target-leakage audit that justifies the excluded columns.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from lead_scoring.dataset import COLUMN_MAP, DEFAULT_CSV, LEAKAGE_EXCLUDED, TARGET
from lead_scoring.training import plots
from lead_scoring.training.train import REPORTS_DIR


def _conversion_by(df: pd.DataFrame, column: str, top: int = 10) -> pd.DataFrame:
    values = df[column].fillna("(blank)").replace("Select", "(not selected)")
    table = df.groupby(values)[TARGET].agg(leads="count", conversion_rate="mean")
    return table.sort_values("leads", ascending=False).head(top)


def _md_table(frame: pd.DataFrame) -> str:
    frame = frame.reset_index()
    head = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    sep = "|" + "---|" * len(frame.columns)
    rows = []
    for row in frame.itertuples(index=False):
        cells = []
        for v in row:
            if isinstance(v, float):
                cells.append(f"{v:.1%}" if 0 <= v <= 1 else f"{v:,.1f}")
            else:
                cells.append(f"{v:,}" if isinstance(v, (int, np.integer)) else str(v))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, sep, *rows])


def _bar_conversion(table: pd.DataFrame, title: str, overall: float, path: Path) -> None:
    import matplotlib.pyplot as plt

    table = table.iloc[::-1]
    fig, ax = plt.subplots(figsize=(6.5, 0.45 * len(table) + 1.2))
    ax.barh(table.index.astype(str), table["conversion_rate"], height=0.6, color=plots.SERIES[0])
    for y, (rate, n) in enumerate(zip(table["conversion_rate"], table["leads"])):
        ax.annotate(f"{rate:.0%}  (n={n:,})", (rate, y), xytext=(4, 0), textcoords="offset points",
                    va="center", color=plots.INK_SECONDARY, fontsize=8)
    ax.set(xlim=(0, 1.2), xlabel=f"Conversion rate (overall {overall:.0%})")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}" if v <= 1 else ""))
    ax.grid(axis="y", visible=False)
    ax.set_title(title)
    plots.save(fig, path)


def run(csv: Path = DEFAULT_CSV, reports_dir: Path = REPORTS_DIR) -> Path:
    import matplotlib.pyplot as plt

    plots.apply_style()
    df = pd.read_csv(csv)
    fig_dir = reports_dir / "figures"
    overall = df[TARGET].mean()

    missing = pd.DataFrame({
        "missing": df.isna().mean(),
        "not_selected": (df == "Select").mean(),
    })
    missing["missing_or_not_selected"] = missing.sum(axis=1)
    missing = missing.sort_values("missing_or_not_selected", ascending=False)
    missing = missing[missing["missing_or_not_selected"] > 0]
    missing.index.name = "column"

    fig, ax = plt.subplots(figsize=(6.5, 0.32 * len(missing) + 1.2))
    m = missing.iloc[::-1]
    ax.barh(m.index, m["missing"], height=0.6, color=plots.SERIES[0], label="Blank")
    ax.barh(m.index, m["not_selected"], left=m["missing"], height=0.6, color=plots.SERIES[1],
            label="'Select' (form default, not chosen)", edgecolor=plots.SURFACE, linewidth=1)
    ax.set(xlim=(0, 1), xlabel="Share of leads")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right")
    ax.set_title("Missing values by column")
    plots.save(fig, fig_dir / "eda_missing.png")

    by_input = {raw: _conversion_by(df, raw) for raw in ("Lead Origin", "Lead Source", "What is your current occupation")}
    for raw, table in by_input.items():
        _bar_conversion(table, f"Conversion by {raw}", overall, fig_dir / f"eda_conv_{COLUMN_MAP[raw]}.png")

    numeric = ["TotalVisits", "Total Time Spent on Website", "Page Views Per Visit"]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
    for ax, col in zip(axes, numeric):
        cap = df[col].quantile(0.99)
        bins = np.linspace(0, cap, 30)
        for color, (label, value) in zip(plots.SERIES, [("Not converted", 0), ("Converted", 1)]):
            ax.hist(df.loc[df[TARGET] == value, col].clip(upper=cap).dropna(), bins=bins, density=True,
                    histtype="step", linewidth=2, color=color, label=label)
        ax.set(xlabel=col + " (capped at p99)", yticks=[])
        ax.grid(axis="x", visible=False)
    axes[0].legend(loc="upper right")
    axes[0].set_title("Engagement by outcome")
    plots.save(fig, fig_dir / "eda_numeric.png")

    leakage_tables = {col: _conversion_by(df, col, top=6) for col in ("Tags", "Lead Quality", "Last Activity")}
    _bar_conversion(leakage_tables["Tags"], "Leakage example: conversion by Tags", overall, fig_dir / "eda_leakage_tags.png")

    numeric_summary = df[numeric].describe(percentiles=[0.5, 0.95, 0.99]).T[["count", "mean", "50%", "95%", "99%", "max"]]

    lines = [
        "# Agent A: exploratory data analysis",
        "",
        "Generated by `python -m lead_scoring.training.eda` from the raw Kaggle file.",
        "",
        "## Overview",
        "",
        f"- {len(df):,} leads, {df.shape[1]} columns",
        f"- Target `{TARGET}`: {overall:.1%} converted, {1 - overall:.1%} not; moderately imbalanced, so",
        "  ROC-AUC / PR-AUC are reported alongside accuracy, and a majority-class baseline is included",
        f"  (it scores {1 - overall:.1%} accuracy while finding no converters).",
        f"- Duplicate Prospect IDs: {df['Prospect ID'].duplicated().sum()}",
        "",
        "## Missing values",
        "",
        "'Select' is the form's default option and means the lead did not answer; it is counted as missing.",
        "",
        _md_table(missing.rename(columns=lambda c: c.replace("_", " "))),
        "",
        "![Missing](figures/eda_missing.png)",
        "",
        "## Model inputs",
        "",
        "Conversion rate by the categorical inputs (top values):",
        "",
    ]
    for raw, table in by_input.items():
        lines += [f"### {raw}", "", _md_table(table), "", f"![{raw}](figures/eda_conv_{COLUMN_MAP[raw]}.png)", ""]
    lines += [
        "Observations:",
        "",
        "- *Lead Add Form* leads convert at ~92% and are the only origin carrying *Reference* and "
        "*Welingak Website* sources, so source is nested inside origin.",
        "- *Working Professional* converts at ~92%; a blank occupation converts at ~14%. Blank occupation "
        "is tied to the API / landing-page sign-up path (never blank for Lead Add Form), so it is kept as a "
        "learned category rather than imputed.",
        "",
        "### Engagement",
        "",
        _md_table(numeric_summary),
        "",
        "Heavy right tails (TotalVisits max 251); the model applies log1p and median imputation.",
        "",
        "![Numeric](figures/eda_numeric.png)",
        "",
        "## Target-leakage audit",
        "",
        "These columns are recorded by the sales team after contact, so they describe the outcome rather",
        "than predict it. A model using them would look excellent offline and fail on a brand-new lead.",
        "",
        *[f"- **{col}**: {why}" for col, why in LEAKAGE_EXCLUDED.items()],
        "",
    ]
    for col, table in leakage_tables.items():
        lines += [f"### {col}", "", _md_table(table), ""]
    lines += ["![Tags](figures/eda_leakage_tags.png)", ""]

    out = reports_dir / "eda.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EDA report for the lead-scoring dataset.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args(argv)
    print(f"EDA written to {run(args.csv)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
