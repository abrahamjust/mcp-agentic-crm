"""Hot/Warm/Cold cut-offs are derived from validation data, not hand-picked."""
import numpy as np
import pytest

from lead_scoring.thresholds import Thresholds, choose_thresholds


@pytest.fixture
def validation():
    rng = np.random.default_rng(0)
    scores = rng.uniform(0, 1, 4000)
    y = (rng.uniform(0, 1, 4000) < scores).astype(int)  # calibrated by construction
    return y, scores


def test_hot_cutoff_meets_precision_target(validation):
    y, scores = validation
    t = choose_thresholds(y, scores, hot_min_precision=0.80, cold_max_missed=0.05)
    hot = scores >= t.hot
    assert y[hot].mean() >= 0.80
    # and it is the lowest such cut-off: lowering it noticeably breaks the target
    assert y[scores >= t.hot - 0.05].mean() < 0.80 + 0.02


def test_cold_cutoff_misses_at_most_the_allowed_share(validation):
    y, scores = validation
    t = choose_thresholds(y, scores, hot_min_precision=0.80, cold_max_missed=0.05)
    missed = y[scores < t.cold].sum() / y.sum()
    assert missed <= 0.05
    assert t.cold < t.hot


def test_bucket_boundaries():
    t = Thresholds(hot=0.6, cold=0.2, hot_min_precision=0.8, cold_max_missed=0.05)
    assert t.bucket(0.6) == "Hot"
    assert t.bucket(0.9999) == "Hot"
    assert t.bucket(0.5999) == "Warm"
    assert t.bucket(0.2) == "Warm"
    assert t.bucket(0.1999) == "Cold"


def test_unreachable_precision_is_an_error():
    y = np.array([0, 1] * 100)
    scores = np.full(200, 0.5)
    with pytest.raises(ValueError, match="precision"):
        choose_thresholds(y, scores, hot_min_precision=0.9)


def test_no_positives_is_an_error():
    with pytest.raises(ValueError, match="positive"):
        choose_thresholds(np.zeros(50), np.linspace(0, 1, 50))
