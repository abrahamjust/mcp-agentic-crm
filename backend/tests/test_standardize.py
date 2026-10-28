"""Unit tests for the standardization layer."""
import pytest

from app.models import LeadOrigin, LeadSource, Occupation
from app.standardize import (
    clean_optional_text,
    normalize_consent,
    normalize_count,
    normalize_email,
    normalize_lead_origin,
    normalize_name,
    normalize_occupation,
    normalize_phone,
    normalize_rate,
    normalize_source,
    standardize_lead_input,
)


def test_normalize_email():
    assert normalize_email("John@Gmail.COM ") == "john@gmail.com"
    assert normalize_email(None) is None
    assert normalize_email("") is None


def test_normalize_phone_with_country_code():
    assert normalize_phone("+91 98765-43210") == "+919876543210"


def test_normalize_phone_without_country_code_defaults_india():
    assert normalize_phone("9876543210") == "+919876543210"


def test_normalize_phone_returns_none_when_no_digits():
    assert normalize_phone("call-me-maybe") is None


def test_normalize_name():
    assert normalize_name("john   DOE") == "John Doe"
    assert normalize_name(None) is None
    assert normalize_name("   ") is None


def test_normalize_source_variants():
    assert normalize_source("fb ad") == LeadSource.SOCIAL
    assert normalize_source("Facebook") == LeadSource.SOCIAL
    assert normalize_source("FB") == LeadSource.SOCIAL
    assert normalize_source("Referral") == LeadSource.REFERRAL
    assert normalize_source(None) == LeadSource.UNKNOWN
    assert normalize_source("carrier pigeon") == LeadSource.UNKNOWN


def test_normalize_source_web_analytics_channels():
    assert normalize_source("Google") == LeadSource.WEBSITE
    assert normalize_source("Olark Chat") == LeadSource.WEBSITE
    assert normalize_source("Referral Sites") == LeadSource.WEBSITE
    assert normalize_source("Reference") == LeadSource.REFERRAL
    assert normalize_source("youtubechannel") == LeadSource.SOCIAL


def test_normalize_lead_origin():
    assert normalize_lead_origin("Landing Page Submission") == LeadOrigin.LANDING_PAGE
    assert normalize_lead_origin(" landing page ") == LeadOrigin.LANDING_PAGE
    assert normalize_lead_origin("Quick Add Form") == LeadOrigin.LEAD_ADD_FORM
    assert normalize_lead_origin("API") == LeadOrigin.API
    assert normalize_lead_origin(None) == LeadOrigin.UNKNOWN
    assert normalize_lead_origin("fax") == LeadOrigin.UNKNOWN


def test_normalize_occupation():
    assert normalize_occupation("working professional") == Occupation.WORKING_PROFESSIONAL
    assert normalize_occupation("Businessman") == Occupation.BUSINESS_OWNER
    assert normalize_occupation("Housewife") == Occupation.HOMEMAKER
    assert normalize_occupation("") == Occupation.UNKNOWN
    assert normalize_occupation("astronaut") == Occupation.UNKNOWN


def test_normalize_engagement_numbers():
    assert normalize_count(3.0) == 3
    assert normalize_count(2.6) == 3
    assert normalize_count(None) is None
    assert normalize_count(float("nan")) is None
    assert normalize_count(-1) is None
    assert normalize_rate(2.333) == 2.33
    assert normalize_rate(float("nan")) is None


def test_normalize_consent_structure():
    result = normalize_consent({"marketing": True, "analytics": False})
    purposes = {r["purpose"]: r["granted"] for r in result}
    assert purposes == {"marketing": True, "analytics": False}
    assert all("timestamp" in r for r in result)


def test_normalize_consent_purpose_is_case_insensitive():
    result = normalize_consent({" Marketing ": True})
    assert result[0]["purpose"] == "marketing"


def test_normalize_consent_empty():
    assert normalize_consent(None) == []
    assert normalize_consent({}) == []


def test_clean_optional_text_variants():
    assert clean_optional_text("") is None
    assert clean_optional_text("N/A") is None
    assert clean_optional_text("null") is None
    assert clean_optional_text("  Acme Corp  ") == "Acme Corp"
    assert clean_optional_text(None) is None


def test_standardize_lead_input_end_to_end():
    raw = {
        "name": "john   DOE",
        "email": "John@Gmail.COM ",
        "phone": "9876543210",
        "source": "fb ad",
        "product_interest": "  Data Science certificate ",
        "notes": "N/A",
        "consent": {"marketing": True},
    }
    clean = standardize_lead_input(raw)
    assert clean["name"] == "John Doe"
    assert clean["email"] == "john@gmail.com"
    assert clean["phone"] == "+919876543210"
    assert clean["product_interest"] == "Data Science certificate"
    assert clean["source"] == LeadSource.SOCIAL
    assert clean["notes"] is None
    assert clean["consent"][0]["purpose"] == "marketing"
    assert clean["consent"][0]["granted"] is True
    # scoring inputs absent -> Unknown / None, never an error
    assert clean["lead_origin"] == LeadOrigin.UNKNOWN
    assert clean["occupation"] == Occupation.UNKNOWN
    assert clean["total_visits"] is None


@pytest.mark.parametrize("bad_email", ["", "   ", "notanemail", "a@b", "a b@c.com"])
def test_standardize_rejects_invalid_email(bad_email):
    with pytest.raises(ValueError):
        standardize_lead_input({"name": "Jane", "email": bad_email})


def test_standardize_rejects_missing_name():
    with pytest.raises(ValueError):
        standardize_lead_input({"name": "   ", "email": "jane@example.com"})
