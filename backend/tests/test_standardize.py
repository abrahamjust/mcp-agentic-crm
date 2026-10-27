"""Unit tests for the standardization layer."""
import pytest

from app.models import CompanySize, Seniority, LeadSource
from app.standardize import (
    clean_optional_text,
    normalize_company_size,
    normalize_consent,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_source,
    standardize_lead_input,
    title_to_seniority,
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


def test_normalize_company_size_range():
    assert normalize_company_size("50-100") == CompanySize.S_51_200


def test_normalize_company_size_approx():
    assert normalize_company_size("~75") == CompanySize.S_51_200


def test_normalize_company_size_blank():
    assert normalize_company_size("") == CompanySize.UNKNOWN
    assert normalize_company_size(None) == CompanySize.UNKNOWN


def test_normalize_company_size_large():
    assert normalize_company_size("5000") == CompanySize.S_1000_PLUS


def test_title_to_seniority():
    assert title_to_seniority("VP of Sales") == Seniority.DIRECTOR
    assert title_to_seniority("Chief Technology Officer") == Seniority.C_LEVEL
    assert title_to_seniority("Software Engineer") == Seniority.IC
    assert title_to_seniority("Team Lead") == Seniority.MANAGER
    assert title_to_seniority(None) == Seniority.UNKNOWN
    assert title_to_seniority("Astronaut") == Seniority.UNKNOWN


def test_title_to_seniority_no_false_positive_substring_match():
    """Regression: 'cto' (C-level) is a substring of 'director'; word-boundary
    matching must keep directors out of the C-level tier."""
    assert title_to_seniority("Director of Engineering") == Seniority.DIRECTOR
    assert title_to_seniority("Director") == Seniority.DIRECTOR


def test_normalize_source_variants():
    assert normalize_source("fb ad") == LeadSource.SOCIAL
    assert normalize_source("Facebook") == LeadSource.SOCIAL
    assert normalize_source("FB") == LeadSource.SOCIAL
    assert normalize_source("Referral") == LeadSource.REFERRAL
    assert normalize_source(None) == LeadSource.UNKNOWN
    assert normalize_source("carrier pigeon") == LeadSource.UNKNOWN


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
        "company": "  ",
        "company_size": "~75",
        "job_title": "VP of Sales",
        "source": "fb ad",
        "product_interest": "Enterprise plan",
        "notes": "N/A",
        "consent": {"marketing": True},
    }
    clean = standardize_lead_input(raw)
    assert clean["name"] == "John Doe"
    assert clean["email"] == "john@gmail.com"
    assert clean["phone"] == "+919876543210"
    assert clean["company"] is None
    assert clean["company_size"] == CompanySize.S_51_200
    assert clean["seniority"] == Seniority.DIRECTOR
    assert clean["source"] == LeadSource.SOCIAL
    assert clean["notes"] is None
    assert clean["consent"][0]["purpose"] == "marketing"
    assert clean["consent"][0]["granted"] is True


@pytest.mark.parametrize("bad_email", ["", "   ", "notanemail", "a@b", "a b@c.com"])
def test_standardize_rejects_invalid_email(bad_email):
    with pytest.raises(ValueError):
        standardize_lead_input({"name": "Jane", "email": bad_email})


def test_standardize_rejects_missing_name():
    with pytest.raises(ValueError):
        standardize_lead_input({"name": "   ", "email": "jane@example.com"})
