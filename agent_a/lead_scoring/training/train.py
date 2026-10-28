"""
Train, compare, calibrate and evaluate the lead-scoring model.

    python -m lead_scoring.training.train [--csv PATH] [--no-latest]

Protocol (all random steps use SEED, so a re-run gives the same model):
  1. Stratified split: 60% train / 20% validation / 20% test.
  2. Each candidate (logistic regression, random forest, XGBoost) is tuned
     by 5-fold cross-validated ROC-AUC on the training split only.
  3. Selection: highest CV ROC-AUC, except that a simpler model within
     SELECTION_TOLERANCE of the best is preferred.
  4. The selected model's probabilities are calibrated (Platt scaling) on
     the validation split; Hot/Warm/Cold cut-offs are chosen there too.
  5. The test split is used once, at the end, for the reported numbers,
     compared against a majority-class baseline and the logistic-regression
     baseline (with paired bootstrap confidence intervals).
Outputs: models/<version>/ (artifact + model card) and reports/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_curve, roc_curve
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from lead_scoring import artifacts
from lead_scoring.dataset import (
    DEFAULT_CSV,
    LEAKAGE_EXCLUDED,
    OTHER_EXCLUDED,
    ROW_FILTERS,
    load_training_data,
)
from lead_scoring.explain import contributions
from lead_scoring.features import (
    CATEGORICAL,
    FEATURES,
    IMPUTE_UNKNOWN,
    LABELS,
    LEARNED_UNKNOWN,
    NUMERIC,
    UNKNOWN,
)
from lead_scoring.thresholds import choose_thresholds
from lead_scoring.training import plots
from lead_scoring.training.metrics import (
    bootstrap_auc,
    bucket_table,
    classification_metrics,
    expected_calibration_error,
)

log = logging.getLogger("lead_scoring.train")

SEED = 42
CV_FOLDS = 5
SELECTION_TOLERANCE = 0.005
HOT_MIN_PRECISION = 0.80
COLD_MAX_MISSED = 0.05
REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"

DISPLAY = {
    "majority_baseline": "Majority-class baseline",
    "logistic_regression": "Logistic regression",
    "random_forest": "Random forest",
    "xgboost": "XGBoost",
}
SIMPLICITY_ORDER = ["logistic_regression", "random_forest", "xgboost"]
# how per-lead contributions are computed (explain.py), and their unit
EXPLAINERS = {
    "logistic_regression": ("coefficient × (value − training mean)", "log-odds"),
    "random_forest": ("TreeSHAP", "probability"),
    "xgboost": ("TreeSHAP (XGBoost native)", "log-odds"),
}


def build_preprocessor() -> ColumnTransformer:
    numeric = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
        ("scale", StandardScaler()),
    ])
    def one_hot():
        return OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=20, sparse_output=False)

    imputed_categorical = Pipeline([
        ("impute", SimpleImputer(missing_values=UNKNOWN, strategy="most_frequent")),
        ("onehot", one_hot()),
    ])
    return ColumnTransformer([
        ("catimp", imputed_categorical, IMPUTE_UNKNOWN),
        ("cat", one_hot(), LEARNED_UNKNOWN),
        ("num", numeric, NUMERIC),
    ])


def candidates(quick: bool = False) -> dict[str, tuple[object, dict]]:
    """Each candidate with its tuning grid. `quick` keeps one point per grid
    (smoke tests / CI); the reported model always uses the full grids."""
    full = {
        "logistic_regression": (
            LogisticRegression(max_iter=5000),
            {"clf__C": [0.01, 0.1, 1.0, 10.0]},
        ),
        "random_forest": (
            RandomForestClassifier(n_estimators=300, random_state=SEED, n_jobs=-1),
            {"clf__max_depth": [6, 10, None], "clf__min_samples_leaf": [1, 5, 20]},
        ),
        "xgboost": (
            XGBClassifier(
                eval_metric="logloss", tree_method="hist", subsample=0.9,
                colsample_bytree=0.9, random_state=SEED, n_jobs=-1,
            ),
            {
                "clf__n_estimators": [200, 400],
                "clf__max_depth": [3, 4, 6],
                "clf__learning_rate": [0.03, 0.1],
                "clf__min_child_weight": [1, 5],
            },
        ),
    }
    if quick:
        return {name: (est, {k: v[:1] for k, v in grid.items()}) for name, (est, grid) in full.items()}
    return full


def select_model(cv_results: dict[str, dict]) -> str:
    best = max(r["cv_roc_auc_mean"] for r in cv_results.values())
    for name in SIMPLICITY_ORDER:
        if cv_results[name]["cv_roc_auc_mean"] >= best - SELECTION_TOLERANCE:
            return name
    raise AssertionError("unreachable")


def _version(algorithm: str, data_sha: str, params: dict) -> str:
    fingerprint = json.dumps(
        {"data": data_sha, "params": params, "seed": SEED, "features": FEATURES,
         "preprocessor": repr(build_preprocessor()),
         "sklearn": sklearn.__version__, "xgboost": xgboost.__version__},
        sort_keys=True, default=str,
    )
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:8]
    return f"{algorithm}-{datetime.now(timezone.utc):%Y%m%d}-{digest}"


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def _figures(y_test, curves: dict[str, np.ndarray], raw_sel, cal_sel, buckets_rows, importance,
             contribution_unit: str, out_dir: Path):
    plots.apply_style()
    import matplotlib.pyplot as plt

    names = list(curves)  # selected model first, then baselines

    fig, ax = plt.subplots(figsize=(6, 5))
    for color, name in zip(plots.SERIES, names):
        fpr, tpr, _ = roc_curve(y_test, curves[name])
        auc = classification_metrics(y_test, curves[name])["roc_auc"]
        ax.plot(fpr, tpr, color=color, label=f"{name}  (AUC {auc:.3f})")
    ax.plot([0, 1], [0, 1], color=plots.MUTED, lw=1, ls="--", label="Majority / chance (AUC 0.500)")
    ax.set(xlabel="False positive rate", ylabel="True positive rate", xlim=(0, 1), ylim=(0, 1.01))
    ax.set_title("ROC curve, held-out test set")
    ax.legend(loc="lower right")
    plots.save(fig, out_dir / "roc_curve.png")

    fig, ax = plt.subplots(figsize=(6, 5))
    for color, name in zip(plots.SERIES, names):
        precision, recall, _ = precision_recall_curve(y_test, curves[name])
        ap = classification_metrics(y_test, curves[name])["pr_auc"]
        ax.plot(recall, precision, color=color, label=f"{name}  (PR-AUC {ap:.3f})")
    ax.axhline(np.mean(y_test), color=plots.MUTED, lw=1, ls="--", label=f"Base rate ({np.mean(y_test):.3f})")
    ax.set(xlabel="Recall", ylabel="Precision", xlim=(0, 1), ylim=(0, 1.01))
    ax.set_title("Precision-recall curve, held-out test set")
    ax.legend(loc="lower left")
    plots.save(fig, out_dir / "pr_curve.png")

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot([0, 1], [0, 1], color=plots.MUTED, lw=1, ls="--", label="Perfect calibration")
    for color, (label, proba) in zip(plots.SERIES, [("Uncalibrated", raw_sel), ("Calibrated (Platt)", cal_sel)]):
        frac, mean_pred = calibration_curve(y_test, proba, n_bins=10, strategy="quantile")
        ece = expected_calibration_error(y_test, proba)
        ax.plot(mean_pred, frac, color=color, marker="o", ms=5, label=f"{label}  (ECE {ece:.3f})")
    ax.set(xlabel="Predicted conversion probability", ylabel="Observed conversion rate", xlim=(0, 1), ylim=(0, 1.01))
    ax.set_title("Calibration, held-out test set")
    ax.legend(loc="upper left")
    plots.save(fig, out_dir / "calibration.png")

    fig, ax = plt.subplots(figsize=(6, 4))
    labels = [r["bucket"] for r in buckets_rows]
    rates = [r["conversion_rate"] for r in buckets_rows]
    bars = ax.bar(labels, rates, width=0.55, color=[plots.BUCKET_COLORS[b] for b in labels])
    for bar, row in zip(bars, buckets_rows):
        ax.annotate(
            f"{row['conversion_rate']:.0%} converted\n{row['share_of_leads']:.0%} of leads",
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 4), textcoords="offset points", ha="center", va="bottom",
            color=plots.INK_SECONDARY, fontsize=9,
        )
    ax.set(ylabel="Actual conversion rate", ylim=(0, 1.15),
           xlabel=f"Overall conversion rate: {np.mean(y_test):.0%}")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="x", visible=False)
    ax.set_title("Conversion rate by bucket, held-out test set")
    plots.save(fig, out_dir / "bucket_conversion.png")

    fig, ax = plt.subplots(figsize=(6, 3.6))
    order = sorted(importance, key=importance.get)
    ax.barh([LABELS[f] for f in order], [importance[f] for f in order], height=0.55, color=plots.SERIES[0])
    ax.set(xlabel=f"Mean |contribution| ({contribution_unit})")
    ax.grid(axis="y", visible=False)
    ax.set_title("What drives the score (test set)")
    plots.save(fig, out_dir / "feature_importance.png")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    return f"{v:.3f}"


def _report(card: dict, out_path: Path) -> None:
    tm = card["test_metrics"]
    cols = ["accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc", "brier"]
    lines = [
        f"# Agent A: lead-scoring model report",
        "",
        f"Model version `{card['version']}`, trained {card['trained_at']}.",
        "Generated by `python -m lead_scoring.training.train`; numbers below are on the",
        "held-out **test set**, which was not used for model selection, calibration or thresholds.",
        "",
        "## Data",
        "",
        f"- Source: Kaggle *Lead Scoring X Online Education* (`{card['data']['file']}`, sha256 `{card['data']['sha256'][:12]}...`)",
        f"- {card['data']['rows']:,} leads; {card['data']['rows_used']:,} used "
        f"({card['data']['positive_rate']:.1%} converted)",
        *[f"- Dropped {d['count']} rows with {k}: {d['reason']}" for k, d in card["data"]["rows_dropped"].items()],
        f"- Split (stratified, seed {SEED}): train {card['data']['split']['train']:,} / "
        f"validation {card['data']['split']['validation']:,} / test {card['data']['split']['test']:,}",
        f"- Inputs: {', '.join(LABELS[f] for f in FEATURES)} (only fields known when a lead is created)",
        "",
        "Excluded as target leakage (recorded by the sales team after contact):",
        "",
        *[f"- **{col}**: {why}" for col, why in LEAKAGE_EXCLUDED.items()],
        "",
        "## Model comparison",
        "",
        f"Cross-validated on the training split ({CV_FOLDS}-fold ROC-AUC); the simplest model within "
        f"{SELECTION_TOLERANCE} of the best is selected.",
        "",
        "| Model | CV ROC-AUC | Best parameters |",
        "|---|---|---|",
        *[
            f"| {DISPLAY[n]} | {r['cv_roc_auc_mean']:.3f} ± {r['cv_roc_auc_std']:.3f} | `{r['best_params']}` |"
            for n, r in card["cv_results"].items()
        ],
        "",
        f"**Selected: {DISPLAY[card['algorithm']]}**, calibrated with Platt scaling on the validation split.",
        "",
        "## Test-set results",
        "",
        "Classification metrics use a 0.5 probability cut-off; ROC-AUC, PR-AUC and Brier are threshold-free.",
        "",
        "| Model | " + " | ".join(c.replace("_", "-").upper() if c in ("roc_auc", "pr_auc") else c.capitalize() for c in cols) + " |",
        "|---|" + "---|" * len(cols),
        *[
            f"| {DISPLAY.get(n, n) if n != 'selected' else '**Selected (calibrated)**'} | "
            + " | ".join(_fmt(m[c]) for c in cols) + " |"
            for n, m in tm.items()
        ],
        "",
        f"- ROC-AUC 95% CI (bootstrap, 1,000 resamples): "
        f"{card['bootstrap']['roc_auc_ci95'][0]:.3f} – {card['bootstrap']['roc_auc_ci95'][1]:.3f}",
        f"- ROC-AUC gain over the logistic-regression baseline, 95% CI: "
        f"{card['bootstrap']['auc_gain_vs_baseline_ci95'][0]:+.3f} – {card['bootstrap']['auc_gain_vs_baseline_ci95'][1]:+.3f}",
        f"- Calibration error (ECE): uncalibrated {card['calibration']['ece_uncalibrated']:.3f}, "
        f"calibrated {card['calibration']['ece_calibrated']:.3f}",
        "",
        "![ROC](figures/roc_curve.png) ![PR](figures/pr_curve.png)",
        "",
        "![Calibration](figures/calibration.png)",
        "",
        "## Hot / Warm / Cold",
        "",
        f"Cut-offs chosen on the validation split: **Hot** when score ≥ {card['thresholds']['hot']:.3f} "
        f"(lowest cut-off keeping precision ≥ {HOT_MIN_PRECISION:.0%}); **Cold** when score < "
        f"{card['thresholds']['cold']:.3f} (at most {COLD_MAX_MISSED:.0%} of converters fall below it).",
        "",
        "| Bucket | Leads | Share of leads | Actual conversion | Share of all conversions |",
        "|---|---|---|---|---|",
        *[
            f"| {r['bucket']} | {r['leads']} | {r['share_of_leads']:.1%} | {r['conversion_rate']:.1%} | {r['share_of_conversions']:.1%} |"
            for r in card["test_buckets"]
        ],
        "",
        "![Buckets](figures/bucket_conversion.png)",
        "",
        "## Explanations",
        "",
        f"Each score comes with its top 3 factors, from exact per-lead contributions "
        f"({EXPLAINERS[card['algorithm']][0]}), e.g.",
        "`Occupation: Working Professional (raises score)`. Overall importance:",
        "",
        "![Importance](figures/feature_importance.png)",
        "",
        "## Serving rules",
        "",
        *[f"- {rule}" for rule in card["serving_rules"]],
        "",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def train(
    csv: Path = DEFAULT_CSV,
    *,
    set_latest: bool = True,
    reports_dir: Path = REPORTS_DIR,
    models_dir: Path = artifacts.MODELS_DIR,
    quick: bool = False,
) -> dict:
    data = load_training_data(csv)
    X, y = data.X, data.y
    log.info("Loaded %d leads, using %d (%.1f%% converted) from %s",
             data.n_rows, len(y), 100 * y.mean(), data.source_path)

    X_rest, X_test, y_rest, y_test = train_test_split(X, y, test_size=0.20, stratify=y, random_state=SEED)
    X_train, X_val, y_train, y_val = train_test_split(
        X_rest, y_rest, test_size=0.25, stratify=y_rest, random_state=SEED
    )

    cv = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=SEED)
    fitted: dict[str, Pipeline] = {}
    cv_results: dict[str, dict] = {}
    for name, (estimator, grid) in candidates(quick).items():
        # Parallelism lives inside the estimators (threads), not in a process
        # pool: process pools are fragile on Windows and in sandboxed shells.
        search = GridSearchCV(
            Pipeline([("pre", build_preprocessor()), ("clf", estimator)]),
            grid, scoring="roc_auc", cv=cv, n_jobs=1, refit=True,
        )
        search.fit(X_train, y_train)
        i = search.best_index_
        cv_results[name] = {
            "cv_roc_auc_mean": float(search.cv_results_["mean_test_score"][i]),
            "cv_roc_auc_std": float(search.cv_results_["std_test_score"][i]),
            "best_params": {k.removeprefix("clf__"): v for k, v in search.best_params_.items()},
        }
        fitted[name] = search.best_estimator_
        log.info("%-20s CV ROC-AUC %.4f ± %.4f  %s", name, cv_results[name]["cv_roc_auc_mean"],
                 cv_results[name]["cv_roc_auc_std"], cv_results[name]["best_params"])

    algorithm = select_model(cv_results)
    pipeline = fitted[algorithm]
    log.info("Selected %s", algorithm)

    calibrated = CalibratedClassifierCV(FrozenEstimator(pipeline), method="sigmoid")
    calibrated.fit(X_val, y_val)
    thresholds = choose_thresholds(
        y_val, calibrated.predict_proba(X_val)[:, 1],
        hot_min_precision=HOT_MIN_PRECISION, cold_max_missed=COLD_MAX_MISSED,
    )
    log.info("Thresholds: hot >= %.3f, cold < %.3f", thresholds.hot, thresholds.cold)

    # ---- test set: used once, here ----
    majority = DummyClassifier(strategy="most_frequent").fit(X_train, y_train)
    test_proba = {name: model.predict_proba(X_test)[:, 1] for name, model in fitted.items()}
    test_proba["majority_baseline"] = majority.predict_proba(X_test)[:, 1]
    selected_proba = calibrated.predict_proba(X_test)[:, 1]

    test_metrics = {
        "majority_baseline": classification_metrics(y_test, test_proba["majority_baseline"]),
        **{n: classification_metrics(y_test, test_proba[n]) for n in SIMPLICITY_ORDER},
        "selected": classification_metrics(y_test, selected_proba),
    }
    boot = bootstrap_auc(y_test, selected_proba, test_proba["logistic_regression"], seed=SEED)
    buckets = np.array([thresholds.bucket(p) for p in selected_proba])
    test_buckets = bucket_table(y_test, buckets)

    background_mean = np.asarray(pipeline.named_steps["pre"].transform(X_train), dtype=float).mean(axis=0)
    effects = contributions(pipeline, X_test, background_mean)
    importance = {f: float(v) for f, v in zip(FEATURES, np.abs(effects).mean(axis=0))}

    version = _version(algorithm, data.sha256, cv_results[algorithm]["best_params"])
    card = {
        "algorithm": algorithm,
        "best_params": cv_results[algorithm]["best_params"],
        "features": FEATURES,
        "categorical": CATEGORICAL,
        "numeric": NUMERIC,
        "thresholds": thresholds.as_dict(),
        "calibration": {
            "method": "sigmoid (Platt) fitted on the validation split",
            "ece_uncalibrated": expected_calibration_error(y_test, test_proba[algorithm]),
            "ece_calibrated": expected_calibration_error(y_test, selected_proba),
        },
        "data": {
            "file": data.source_path.name,
            "sha256": data.sha256,
            "rows": data.n_rows,
            "rows_used": len(y),
            "rows_dropped": {k: {"count": n, "reason": ROW_FILTERS[k]} for k, n in data.rows_dropped.items()},
            "positive_rate": float(y.mean()),
            "split": {"train": len(y_train), "validation": len(y_val), "test": len(y_test)},
            "seed": SEED,
            "excluded_leakage": LEAKAGE_EXCLUDED,
            "excluded_other": OTHER_EXCLUDED,
        },
        "selection_rule": f"highest {CV_FOLDS}-fold CV ROC-AUC; simplest model within {SELECTION_TOLERANCE} preferred",
        "cv_results": cv_results,
        "test_metrics": test_metrics,
        "bootstrap": boot,
        "test_buckets": test_buckets,
        "global_importance": importance,
        "serving_rules": [
            "Missing numeric inputs are imputed with the training median and are not cited in top_factors.",
            "A missing lead origin or source is filled with the most common training value (neutral) and is "
            "not cited in top_factors; a missing occupation is a learned category ('not provided').",
            "Categories never seen in training (e.g. source 'Event') are encoded as an infrequent category, "
            "never an error.",
            "Consent is not a model input; the consent layer decides what may be done with a lead.",
        ],
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "xgboost": xgboost.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    bundle = {"pipeline": pipeline, "calibrated": calibrated, "background_mean": background_mean}
    model_dir = artifacts.save(bundle, card, version, set_latest=set_latest, models_dir=models_dir)
    card = json.loads((model_dir / artifacts.CARD_FILE).read_text(encoding="utf-8"))
    log.info("Saved model %s to %s", version, model_dir)

    curves = {
        f"{DISPLAY[algorithm]} (selected)": selected_proba,
        **{DISPLAY[n]: test_proba[n] for n in SIMPLICITY_ORDER if n != algorithm},
    }
    _figures(y_test, curves, test_proba[algorithm], selected_proba, test_buckets, importance,
             EXPLAINERS[algorithm][1], reports_dir / "figures")
    _report(card, reports_dir / "model_report.md")
    (reports_dir / "metrics.json").write_text(
        json.dumps({k: card[k] for k in ("version", "cv_results", "test_metrics", "bootstrap",
                                         "test_buckets", "thresholds", "calibration")}, indent=2) + "\n",
        encoding="utf-8",
    )
    log.info("Report written to %s", reports_dir / "model_report.md")
    return card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the Agent A lead-scoring model.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--no-latest", action="store_true", help="do not point models/LATEST at the new model")
    parser.add_argument("--quick", action="store_true", help="one point per tuning grid (smoke test only)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    card = train(args.csv, set_latest=not args.no_latest, quick=args.quick)
    sel = card["test_metrics"]["selected"]
    print(f"\nModel {card['version']}: test ROC-AUC {sel['roc_auc']:.3f}, PR-AUC {sel['pr_auc']:.3f}, "
          f"F1 {sel['f1']:.3f}; Hot >= {card['thresholds']['hot']:.3f}, Cold < {card['thresholds']['cold']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
