"""
Per-lead explanations: how much each input field pushed this lead's score
up or down.

Contributions are computed on the model's raw output (log-odds for logistic
regression and XGBoost, probability for random forest). Calibration is a
monotone map applied afterwards, so it does not change which factors raised
or lowered the score. One-hot columns are summed back into the input field
they came from, so a factor is "Occupation", not "occupation_Student".
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from lead_scoring.features import FEATURES


def feature_groups(pipeline: Pipeline) -> np.ndarray:
    """Index into FEATURES for every column the preprocessor outputs."""
    names = pipeline.named_steps["pre"].get_feature_names_out()
    groups = []
    for name in names:
        bare = name.split("__", 1)[-1]  # drop the "cat__" / "num__" prefix
        matches = [i for i, f in enumerate(FEATURES) if bare == f or bare.startswith(f + "_")]
        if not matches:
            raise ValueError(f"Cannot map model column {name!r} to an input field")
        # longest field name wins, in case one field name prefixes another
        groups.append(max(matches, key=lambda i: len(FEATURES[i])))
    return np.asarray(groups)


def _raw_contributions(pipeline: Pipeline, X: pd.DataFrame, background_mean: np.ndarray) -> np.ndarray:
    pre = pipeline.named_steps["pre"]
    clf = pipeline.named_steps["clf"]
    Xt = np.asarray(pre.transform(X), dtype=float)
    kind = type(clf).__name__

    if kind == "LogisticRegression":
        return (Xt - background_mean) * clf.coef_[0]

    if kind == "XGBClassifier":
        import xgboost as xgb

        contribs = clf.get_booster().predict(xgb.DMatrix(Xt), pred_contribs=True)
        return contribs[:, :-1]  # last column is the bias term

    if kind == "RandomForestClassifier":
        import shap

        values = shap.TreeExplainer(clf).shap_values(Xt)
        values = np.asarray(values)
        # binary classifiers: (n, m, 2) or [class0, class1]; take the positive class
        return values[..., 1] if values.ndim == 3 and values.shape[-1] == 2 else values[1]

    raise TypeError(f"No explainer for estimator type {kind}")


def contributions(pipeline: Pipeline, X: pd.DataFrame, background_mean: np.ndarray) -> np.ndarray:
    """(n_leads, len(FEATURES)) signed contribution of each input field."""
    raw = _raw_contributions(pipeline, X, background_mean)
    groups = feature_groups(pipeline)
    out = np.zeros((raw.shape[0], len(FEATURES)))
    for column, field in enumerate(groups):
        out[:, field] += raw[:, column]
    return out
