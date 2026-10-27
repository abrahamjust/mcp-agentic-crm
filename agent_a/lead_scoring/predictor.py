"""
LeadScorer: a standardized lead record -> {score, bucket, top_factors, model_version}.

Pure computation, no I/O after loading, so the same object serves the
polling agent, the HTTP service and the tests. The output fields match
the backend's `PATCH /leads/{id}/score` body (`LeadScoreUpdate`).
"""
from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from lead_scoring import artifacts
from lead_scoring.explain import contributions
from lead_scoring.features import (
    CATEGORICAL,
    FEATURES,
    IMPUTE_UNKNOWN,
    LABELS,
    NUMERIC,
    UNKNOWN,
    describe_value,
    model_inputs,
    to_frame,
)
from lead_scoring.thresholds import Thresholds

DEFAULT_TOP_K = 3
_MIN_EFFECT = 1e-6  # contributions smaller than this are not worth showing


@dataclass(frozen=True)
class ScoreResult:
    lead_id: str | None
    score: float
    bucket: str
    top_factors: list[str] = field(default_factory=list)
    model_version: str = ""

    def as_update(self) -> dict[str, Any]:
        """Arguments for update_lead_score / PATCH /leads/{id}/score."""
        return {
            "score": self.score,
            "bucket": self.bucket,
            "top_factors": list(self.top_factors),
            "model_version": self.model_version,
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class LeadScorer:
    def __init__(self, bundle: Mapping[str, Any], card: Mapping[str, Any], *, top_k: int = DEFAULT_TOP_K):
        if list(card.get("features", [])) != FEATURES:
            raise artifacts.ModelArtifactError(
                f"Model expects features {card.get('features')}, this code provides {FEATURES}"
            )
        self._pipeline = bundle["pipeline"]
        self._calibrated = bundle.get("calibrated") or bundle["pipeline"]
        self._background = np.asarray(bundle["background_mean"])
        t = card["thresholds"]
        self.thresholds = Thresholds(**t)
        self.version: str = card["version"]
        self.card = dict(card)
        self.top_k = top_k

    @classmethod
    def load(cls, model_dir: str | os.PathLike | None = None, **kwargs: Any) -> "LeadScorer":
        bundle, card = artifacts.load(model_dir)
        scorer = cls(bundle, card, **kwargs)
        # The first explanation compiles the TreeSHAP code (seconds); do it
        # now so the first real lead is not slow.
        scorer.score({})
        return scorer

    # ------------------------------------------------------------------

    def score(self, lead: Mapping[str, Any]) -> ScoreResult:
        return self.score_many([lead])[0]

    def score_many(self, leads: Sequence[Mapping[str, Any]]) -> list[ScoreResult]:
        if not leads:
            return []
        X = to_frame(leads)
        probabilities = self._calibrated.predict_proba(X)[:, 1]
        effects = contributions(self._pipeline, X, self._background)

        results = []
        for i, lead in enumerate(leads):
            score = float(np.clip(probabilities[i], 0.0, 1.0))
            if not math.isfinite(score):  # defensive: never send NaN to the CRM
                raise ValueError(f"Model produced a non-finite score for lead {lead.get('id')}")
            results.append(
                ScoreResult(
                    lead_id=lead.get("id"),
                    score=round(score, 4),
                    bucket=self.thresholds.bucket(score),
                    top_factors=self._top_factors(lead, X.iloc[i], effects[i]),
                    model_version=self.version,
                )
            )
        return results

    def _top_factors(self, lead: Mapping[str, Any], inputs, effects: np.ndarray) -> list[str]:
        occupation_inferred = (
            model_inputs({"occupation": lead.get("occupation")})["occupation"] == UNKNOWN
            and inputs["occupation"] != UNKNOWN
        )
        ranked = []
        for j, feature in enumerate(FEATURES):
            value = inputs[feature]
            # an imputed value is not evidence about this lead; don't cite it
            if feature in NUMERIC and (value is None or math.isnan(value)):
                continue
            if feature in IMPUTE_UNKNOWN and value == UNKNOWN:
                continue
            if abs(effects[j]) < _MIN_EFFECT:
                continue
            ranked.append((abs(effects[j]), j, feature, value))
        ranked.sort(key=lambda r: (-r[0], r[1]))

        factors = []
        for _, j, feature, value in ranked[: self.top_k]:
            shown = describe_value(feature, value)
            if feature == "occupation" and occupation_inferred:
                shown += " (from job title)"
            direction = "raises score" if effects[j] > 0 else "lowers score"
            factors.append(f"{LABELS[feature]}: {shown} ({direction})")
        return factors

    def info(self) -> dict[str, Any]:
        """Summary for the service's GET /model endpoint."""
        return {
            "version": self.version,
            "algorithm": self.card.get("algorithm"),
            "features": FEATURES,
            "categorical": CATEGORICAL,
            "numeric": NUMERIC,
            "thresholds": self.thresholds.as_dict(),
            "test_metrics": self.card.get("test_metrics", {}).get("selected"),
            "trained_at": self.card.get("trained_at"),
        }
