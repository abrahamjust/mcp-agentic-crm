"""LeadScorer against the committed model: output contract and explanations."""
import json
import shutil

import numpy as np
import pytest

from lead_scoring import artifacts
from lead_scoring.explain import contributions
from lead_scoring.features import FEATURES, to_frame
from lead_scoring.predictor import LeadScorer

STRONG = {"id": "strong", "lead_origin": "Lead Add Form", "source": "Referral",
          "occupation": "Working Professional", "total_visits": 6, "time_on_website_sec": 1500,
          "pages_per_visit": 3.5}
WEAK = {"id": "weak", "lead_origin": "API", "source": "Website", "occupation": "Student",
        "total_visits": 1, "time_on_website_sec": 20, "pages_per_visit": 1}


def test_output_is_a_valid_backend_score_update(scorer):
    """The body Agent A sends must pass the backend's own LeadScoreUpdate schema."""
    from app.schemas import LeadScoreUpdate

    for lead in (STRONG, WEAK, {}):
        update = scorer.score(lead).as_update()
        parsed = LeadScoreUpdate(**update)
        assert 0.0 <= parsed.score <= 1.0
        assert parsed.bucket.value in ("Hot", "Warm", "Cold")
        assert parsed.model_version == scorer.version


def test_strong_and_weak_leads_are_ranked_sensibly(scorer):
    strong, weak = scorer.score(STRONG), scorer.score(WEAK)
    assert strong.score > weak.score
    assert strong.bucket == "Hot"
    assert weak.bucket == "Cold"


def test_bucket_follows_thresholds(scorer):
    for lead in (STRONG, WEAK, {}, {"occupation": "Unemployed", "time_on_website_sec": 600}):
        result = scorer.score(lead)
        assert result.bucket == scorer.thresholds.bucket(result.score)


def test_scoring_is_deterministic_and_batch_matches_single(scorer):
    leads = [STRONG, WEAK, {}]
    batch = scorer.score_many(leads)
    singles = [scorer.score(lead) for lead in leads]
    assert batch == singles
    assert scorer.score_many(leads) == batch
    assert scorer.score_many([]) == []


def test_top_factors_are_readable_and_bounded(scorer):
    result = scorer.score(STRONG)
    assert 1 <= len(result.top_factors) <= 3
    for factor in result.top_factors:
        assert factor.endswith(("(raises score)", "(lowers score)"))
        assert ": " in factor
    assert any(f.startswith("Occupation: Working Professional") for f in result.top_factors)


def test_imputed_values_are_never_cited(scorer):
    factors = scorer.score({"occupation": "Unemployed"}).top_factors
    joined = " ".join(factors)
    for absent in ("Website visits", "Time on website", "Pages per visit", "Source", "Lead origin"):
        assert absent not in joined


def test_full_backend_lead_record_is_accepted(scorer):
    """A LeadOut JSON from GET /leads, unrelated fields and all."""
    lead = {
        "id": "abc", "name": "Priya", "email": "priya@example.com", "phone": None, "source": "Event",
        "product_interest": "Data Science certificate", "notes": None, "lead_origin": "Unknown", "occupation": "Unknown",
        "total_visits": None, "time_on_website_sec": None, "pages_per_visit": None,
        "consent": [{"purpose": "marketing", "granted": True, "timestamp": "2026-10-27T10:00:00+00:00"}],
        "pipeline_stage": "New", "created_at": "2026-10-27T10:00:00Z", "last_activity_at": "2026-10-27T10:00:00Z",
        "lead_score": None, "bucket": "Unscored", "top_factors": [], "scored_at": None, "model_version": None,
    }
    result = scorer.score(lead)
    assert result.lead_id == "abc"
    assert 0 <= result.score <= 1


def test_consent_does_not_change_the_score(scorer):
    granted = {**STRONG, "consent": [{"purpose": "marketing", "granted": True}]}
    denied = {**STRONG, "consent": [{"purpose": "marketing", "granted": False}]}
    assert scorer.score(granted).score == scorer.score(denied).score


def test_garbage_values_do_not_crash(scorer):
    result = scorer.score({"source": 123, "lead_origin": ["x"], "total_visits": "lots", "pages_per_visit": -3})
    assert result.bucket in ("Hot", "Warm", "Cold")


def test_contributions_cover_every_feature(scorer):
    X = to_frame([STRONG, WEAK])
    effects = contributions(scorer._pipeline, X, scorer._background)
    assert effects.shape == (2, len(FEATURES))
    assert np.isfinite(effects).all()


def test_model_card_documents_the_evaluation(scorer):
    card = scorer.card
    assert card["features"] == FEATURES
    sel = card["test_metrics"]["selected"]
    base = card["test_metrics"]["majority_baseline"]
    assert sel["roc_auc"] > base["roc_auc"] + 0.2
    assert card["thresholds"]["cold"] < card["thresholds"]["hot"]
    assert set(card["data"]["excluded_leakage"]) >= {"Tags", "Lead Quality", "Last Notable Activity"}


def test_tampered_artifact_is_refused(tmp_path):
    source = artifacts.resolve_model_dir()
    copy = tmp_path / source.name
    shutil.copytree(source, copy)
    with open(copy / artifacts.MODEL_FILE, "ab") as fh:
        fh.write(b"tampered")
    with pytest.raises(artifacts.ModelArtifactError, match="sha256"):
        LeadScorer.load(copy)


def test_feature_mismatch_is_refused(tmp_path):
    source = artifacts.resolve_model_dir()
    copy = tmp_path / source.name
    shutil.copytree(source, copy)
    card = json.loads((copy / artifacts.CARD_FILE).read_text())
    card["features"] = ["something_else"]
    (copy / artifacts.CARD_FILE).write_text(json.dumps(card))
    with pytest.raises(artifacts.ModelArtifactError, match="features"):
        LeadScorer.load(copy)


def test_missing_model_dir_is_a_clear_error(tmp_path):
    with pytest.raises(artifacts.ModelArtifactError):
        LeadScorer.load(tmp_path)
