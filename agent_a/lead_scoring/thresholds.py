"""
Hot / Warm / Cold cut-offs, chosen on the validation set (never fixed by hand).

Hot  -> Agent B acts on these automatically, so a Hot lead should usually
        convert: the Hot cut-off is the lowest score at which precision
        stays at or above `hot_min_precision`.
Cold -> deprioritised, so few real converters should land here: the Cold
        cut-off is the highest score that still leaves at most
        `cold_max_missed` of converters below it.
Warm -> everything in between.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

HOT, WARM, COLD = "Hot", "Warm", "Cold"


@dataclass(frozen=True)
class Thresholds:
    hot: float
    cold: float
    hot_min_precision: float
    cold_max_missed: float

    def bucket(self, score: float) -> str:
        if score >= self.hot:
            return HOT
        if score < self.cold:
            return COLD
        return WARM

    def as_dict(self) -> dict:
        return asdict(self)


def choose_thresholds(
    y_true: np.ndarray,
    scores: np.ndarray,
    *,
    hot_min_precision: float = 0.80,
    cold_max_missed: float = 0.05,
    min_hot_support: int = 30,
) -> Thresholds:
    y_true = np.asarray(y_true, dtype=int)
    scores = np.asarray(scores, dtype=float)
    if y_true.sum() == 0:
        raise ValueError("Validation set has no positive examples")

    candidates = np.unique(scores)  # ascending

    # Hot: smallest cut-off t such that precision(score >= u) >= target for
    # every u >= t that still selects at least `min_hot_support` leads.
    hot = None
    for t in candidates[::-1]:
        selected = scores >= t
        if selected.sum() < min_hot_support:
            continue
        if y_true[selected].mean() >= hot_min_precision:
            hot = float(t)
        else:
            break
    if hot is None:
        raise ValueError(
            f"No cut-off reaches precision {hot_min_precision:.2f} on the validation set"
        )

    # Cold: largest cut-off t with share of converters below t <= target.
    positives = np.sort(scores[y_true == 1])
    allowed_missed = int(np.floor(cold_max_missed * len(positives)))
    cold = float(positives[allowed_missed])  # converters strictly below: allowed_missed
    cold = min(cold, hot)

    return Thresholds(
        hot=hot,
        cold=cold,
        hot_min_precision=hot_min_precision,
        cold_max_missed=cold_max_missed,
    )
