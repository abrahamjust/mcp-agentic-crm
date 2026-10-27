"""Training pipeline smoke test on synthetic Kaggle-format data (no download needed)."""
import json

import numpy as np
import pandas as pd

from lead_scoring.predictor import LeadScorer
from lead_scoring.training.train import select_model, train


def synthetic_csv(path, n=900, seed=0):
    rng = np.random.default_rng(seed)
    origin = rng.choice(["Landing Page Submission", "API", "Lead Add Form"], n, p=[0.5, 0.4, 0.1])
    occupation = rng.choice(["Unemployed", "Working Professional", "Student", None], n, p=[0.5, 0.15, 0.05, 0.3])
    time_on_site = rng.gamma(1.5, 300, n).round()
    logit = (-1.0 + 2.5 * (origin == "Lead Add Form") + 2.0 * (occupation == "Working Professional")
             - 1.2 * pd.isna(occupation) + 0.002 * time_on_site)
    converted = (rng.uniform(size=n) < 1 / (1 + np.exp(-logit))).astype(int)
    pd.DataFrame({
        "Prospect ID": [f"p{i}" for i in range(n)],
        "Lead Origin": origin,
        "Lead Source": rng.choice(["Google", "Direct Traffic", "Reference", "Olark Chat"], n),
        "What is your current occupation": occupation,
        "TotalVisits": rng.poisson(3, n).astype(float),
        "Total Time Spent on Website": time_on_site,
        "Page Views Per Visit": rng.uniform(0, 5, n).round(2),
        "Tags": rng.choice(["Ringing", "Will revert after reading the email"], n),
        "Converted": converted,
    }).to_csv(path, index=False)


def test_training_produces_a_loadable_documented_model(tmp_path):
    csv = tmp_path / "leads.csv"
    synthetic_csv(csv)
    card = train(csv, reports_dir=tmp_path / "reports", models_dir=tmp_path / "models", quick=True)

    sel = card["test_metrics"]["selected"]
    assert sel["roc_auc"] > card["test_metrics"]["majority_baseline"]["roc_auc"] + 0.15
    assert set(card["cv_results"]) == {"logistic_regression", "random_forest", "xgboost"}
    assert card["thresholds"]["cold"] < card["thresholds"]["hot"]
    assert (tmp_path / "models" / "LATEST").read_text().strip() == card["version"]
    assert (tmp_path / "reports" / "model_report.md").exists()
    assert (tmp_path / "reports" / "figures" / "roc_curve.png").exists()
    assert json.loads((tmp_path / "reports" / "metrics.json").read_text())["version"] == card["version"]

    scorer = LeadScorer.load(tmp_path / "models" / card["version"])
    result = scorer.score({"lead_origin": "Lead Add Form", "occupation": "Working Professional"})
    assert result.bucket == "Hot"


def test_selection_prefers_simpler_model_within_tolerance():
    results = {
        "logistic_regression": {"cv_roc_auc_mean": 0.80},
        "random_forest": {"cv_roc_auc_mean": 0.868},
        "xgboost": {"cv_roc_auc_mean": 0.870},
    }
    assert select_model(results) == "random_forest"
    results["xgboost"]["cv_roc_auc_mean"] = 0.90
    assert select_model(results) == "xgboost"
    results["logistic_regression"]["cv_roc_auc_mean"] = 0.899
    assert select_model(results) == "logistic_regression"
