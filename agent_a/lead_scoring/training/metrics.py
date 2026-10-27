"""Evaluation metrics, including bootstrap confidence intervals."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def classification_metrics(y_true, proba, threshold: float = 0.5) -> dict[str, float]:
    y_true = np.asarray(y_true)
    proba = np.clip(np.asarray(proba, dtype=float), 1e-7, 1 - 1e-7)
    pred = (proba >= threshold).astype(int)
    return {
        "accuracy": float(accuracy_score(y_true, pred)),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, proba)),
        "pr_auc": float(average_precision_score(y_true, proba)),
        "brier": float(brier_score_loss(y_true, proba)),
        "log_loss": float(log_loss(y_true, proba)),
    }


def expected_calibration_error(y_true, proba, n_bins: int = 10) -> float:
    """Weighted mean |observed rate - mean predicted| over equal-width bins."""
    y_true = np.asarray(y_true, dtype=float)
    proba = np.asarray(proba, dtype=float)
    bins = np.minimum((proba * n_bins).astype(int), n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        mask = bins == b
        if mask.any():
            ece += mask.mean() * abs(y_true[mask].mean() - proba[mask].mean())
    return float(ece)


def bootstrap_auc(y_true, proba, baseline_proba=None, *, n_boot: int = 1000, seed: int = 42) -> dict:
    """95% percentile CI for ROC-AUC, and for the AUC gain over a baseline
    model scored on the same resampled leads (paired bootstrap)."""
    rng = np.random.default_rng(seed)
    y_true = np.asarray(y_true)
    proba = np.asarray(proba)
    aucs, gains = [], []
    n = len(y_true)
    while len(aucs) < n_boot:
        idx = rng.integers(0, n, n)
        if y_true[idx].min() == y_true[idx].max():
            continue  # resample with one class only; AUC undefined
        auc = roc_auc_score(y_true[idx], proba[idx])
        aucs.append(auc)
        if baseline_proba is not None:
            gains.append(auc - roc_auc_score(y_true[idx], np.asarray(baseline_proba)[idx]))
    out = {"roc_auc_ci95": [float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))]}
    if gains:
        out["auc_gain_vs_baseline_ci95"] = [float(np.percentile(gains, 2.5)), float(np.percentile(gains, 97.5))]
    return out


def bucket_table(y_true, buckets) -> list[dict]:
    """Per bucket: how many leads, what share, and how many actually converted."""
    y_true = np.asarray(y_true)
    buckets = np.asarray(buckets)
    total_pos = max(int(y_true.sum()), 1)
    rows = []
    for name in ("Hot", "Warm", "Cold"):
        mask = buckets == name
        count = int(mask.sum())
        converted = int(y_true[mask].sum())
        rows.append({
            "bucket": name,
            "leads": count,
            "share_of_leads": count / len(y_true),
            "conversion_rate": converted / count if count else 0.0,
            "share_of_conversions": converted / total_pos,
        })
    return rows
