"""
Input standardization layer.

Every incoming record (form submission, CSV row, mock live-feed) passes
through `standardize_lead_input()` before it is stored, so Agent A,
Agent B and the frontend always read clean, consistently shaped data.
"""
import re
from datetime import datetime, timezone
from typing import Any

import phonenumbers

from app.models import CompanySize, LeadOrigin, LeadSource, Occupation, Seniority

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Field-level normalizers
# ---------------------------------------------------------------------------


def normalize_email(raw: str | None) -> str | None:
    """'John@Gmail.COM ' -> 'john@gmail.com'"""
    if not raw:
        return None
    cleaned = raw.strip().lower()
    return cleaned or None


def normalize_phone(raw: str | None, default_region: str = "IN") -> str | None:
    """'+91 98765-43210' / '9876543210' -> '+919876543210' (E.164).

    If the number cannot be validated, fall back to the digits found
    rather than dropping the field: a typo should not destroy the record.
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = phonenumbers.parse(raw, default_region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    digits = re.sub(r"[^\d+]", "", raw)
    return digits or None


def normalize_name(raw: str | None) -> str | None:
    """'john   DOE' -> 'John Doe'"""
    if not raw:
        return None
    collapsed = re.sub(r"\s+", " ", raw.strip())
    return collapsed.title() if collapsed else None


_COMPANY_SIZE_BUCKETS = [
    (1, 10, CompanySize.S_1_10),
    (11, 50, CompanySize.S_11_50),
    (51, 200, CompanySize.S_51_200),
    (201, 1000, CompanySize.S_201_1000),
]


def normalize_company_size(raw: str | int | None) -> CompanySize:
    """'50-100', '~75', 75, blank -> one of the fixed CompanySize buckets."""
    if raw is None or raw == "":
        return CompanySize.UNKNOWN

    if isinstance(raw, int):
        numbers = [raw]
    else:
        numbers = [int(n) for n in re.findall(r"\d+", str(raw))]

    if not numbers:
        return CompanySize.UNKNOWN

    # a range like "50-100" uses its midpoint; "~75" just uses 75
    estimate = sum(numbers) / len(numbers)

    if estimate >= 1000:
        return CompanySize.S_1000_PLUS
    for low, high, bucket in _COMPANY_SIZE_BUCKETS:
        if low <= estimate <= high:
            return bucket
    return CompanySize.UNKNOWN


_SENIORITY_KEYWORDS = [
    (Seniority.C_LEVEL, ["ceo", "cto", "cfo", "coo", "cmo", "chief", "founder"]),
    (Seniority.DIRECTOR, ["director", "vp", "vice president", "head of"]),
    (Seniority.MANAGER, ["manager", "lead", "team lead"]),
    (Seniority.IC, ["engineer", "developer", "analyst", "associate", "intern", "executive"]),
]


def title_to_seniority(raw_title: str | None) -> Seniority:
    """Free-text job title -> a fixed seniority tier via keyword lookup.

    Uses word-boundary matching, not a plain substring test: "cto" (a
    C-level keyword) is hiding inside "dire-CTO-r", so a substring check
    would tag every "Director" as C-level.
    """
    if not raw_title:
        return Seniority.UNKNOWN
    lowered = raw_title.lower()
    for tier, keywords in _SENIORITY_KEYWORDS:
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", lowered):
                return tier
    return Seniority.UNKNOWN


_SOURCE_MAP = {
    "website": LeadSource.WEBSITE,
    "web": LeadSource.WEBSITE,
    "referral": LeadSource.REFERRAL,
    "referred": LeadSource.REFERRAL,
    "social": LeadSource.SOCIAL,
    "fb": LeadSource.SOCIAL,
    "facebook": LeadSource.SOCIAL,
    "fb ad": LeadSource.SOCIAL,
    "instagram": LeadSource.SOCIAL,
    "linkedin": LeadSource.SOCIAL,
    "cold": LeadSource.COLD_OUTREACH,
    "cold outreach": LeadSource.COLD_OUTREACH,
    "cold call": LeadSource.COLD_OUTREACH,
    "event": LeadSource.EVENT,
    "conference": LeadSource.EVENT,
    # Web-analytics channel names (also the vocabulary of Agent A's training
    # data, which goes through this same function so training and live
    # scoring see identical categories).
    "google": LeadSource.WEBSITE,
    "bing": LeadSource.WEBSITE,
    "organic search": LeadSource.WEBSITE,
    "direct traffic": LeadSource.WEBSITE,
    "pay per click ads": LeadSource.WEBSITE,
    "olark chat": LeadSource.WEBSITE,
    "live chat": LeadSource.WEBSITE,
    "click2call": LeadSource.WEBSITE,
    "referral sites": LeadSource.WEBSITE,  # sites linking to us, not a person
    "welingak website": LeadSource.WEBSITE,
    "blog": LeadSource.WEBSITE,
    "welearn": LeadSource.WEBSITE,
    "welearnblog_home": LeadSource.WEBSITE,
    "press_release": LeadSource.WEBSITE,
    "reference": LeadSource.REFERRAL,
    "social media": LeadSource.SOCIAL,
    "youtubechannel": LeadSource.SOCIAL,
    "nc_edm": LeadSource.COLD_OUTREACH,  # email direct marketing
}


def normalize_source(raw: str | None) -> LeadSource:
    """'fb ad' / 'Facebook' / 'FB' -> a fixed LeadSource value."""
    if not raw:
        return LeadSource.UNKNOWN
    return _SOURCE_MAP.get(raw.strip().lower(), LeadSource.UNKNOWN)


_LEAD_ORIGIN_MAP = {
    "landing page submission": LeadOrigin.LANDING_PAGE,
    "landing page": LeadOrigin.LANDING_PAGE,
    "api": LeadOrigin.API,
    "lead add form": LeadOrigin.LEAD_ADD_FORM,
    "quick add form": LeadOrigin.LEAD_ADD_FORM,
    "add form": LeadOrigin.LEAD_ADD_FORM,
    "manual": LeadOrigin.LEAD_ADD_FORM,
    "lead import": LeadOrigin.LEAD_IMPORT,
    "import": LeadOrigin.LEAD_IMPORT,
}


def normalize_lead_origin(raw: str | None) -> LeadOrigin:
    """'Landing Page' / 'quick add form' / 'API' -> a fixed LeadOrigin value."""
    if not raw:
        return LeadOrigin.UNKNOWN
    return _LEAD_ORIGIN_MAP.get(raw.strip().lower(), LeadOrigin.UNKNOWN)


_OCCUPATION_MAP = {
    "working professional": Occupation.WORKING_PROFESSIONAL,
    "professional": Occupation.WORKING_PROFESSIONAL,
    "employed": Occupation.WORKING_PROFESSIONAL,
    "business owner": Occupation.BUSINESS_OWNER,
    "businessman": Occupation.BUSINESS_OWNER,
    "self-employed": Occupation.BUSINESS_OWNER,
    "student": Occupation.STUDENT,
    "unemployed": Occupation.UNEMPLOYED,
    "homemaker": Occupation.HOMEMAKER,
    "housewife": Occupation.HOMEMAKER,
    "other": Occupation.OTHER,
}


def normalize_occupation(raw: str | None) -> Occupation:
    """'working professional' / 'Businessman' -> a fixed Occupation value."""
    if not raw:
        return Occupation.UNKNOWN
    return _OCCUPATION_MAP.get(raw.strip().lower(), Occupation.UNKNOWN)


def normalize_count(raw: float | int | None) -> int | None:
    """Non-negative engagement counter: 3.0 -> 3, blank/negative/NaN -> None."""
    if raw is None or raw != raw or raw < 0:  # raw != raw is the NaN test
        return None
    return int(round(raw))


def normalize_rate(raw: float | int | None) -> float | None:
    """Non-negative ratio such as pages per visit, rounded to 2 dp."""
    if raw is None or raw != raw or raw < 0:
        return None
    return round(float(raw), 2)


def normalize_consent(raw_consent: dict[str, bool] | None) -> list[dict[str, Any]]:
    """{'marketing': True, 'analytics': False} ->
    [{'purpose': 'marketing', 'granted': True, 'timestamp': ...}, ...]

    One record per purpose, never a single yes/no flag.
    """
    now = datetime.now(timezone.utc).isoformat()
    if not raw_consent:
        return []
    return [
        {"purpose": purpose.strip().lower(), "granted": bool(granted), "timestamp": now}
        for purpose, granted in raw_consent.items()
    ]


def clean_optional_text(raw: str | None) -> str | None:
    """'', 'N/A', 'null', whitespace-only -> None (a single NULL form)."""
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped or stripped.lower() in {"n/a", "na", "null", "none"}:
        return None
    return stripped


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def standardize_lead_input(raw: dict[str, Any]) -> dict[str, Any]:
    """Raw dict (form / CSV / live-feed) -> standardized fields for a Lead.

    Raises ValueError if a required field (name, valid email) is missing;
    the API turns that into a 422 instead of a server error.
    """
    name = normalize_name(raw.get("name"))
    email = normalize_email(raw.get("email"))

    if not name:
        raise ValueError("Name is required")
    if not email or not _EMAIL_RE.match(email):
        raise ValueError("A valid email address is required")

    return {
        "name": name,
        "email": email,
        "phone": normalize_phone(raw.get("phone")),
        "company": clean_optional_text(raw.get("company")),
        "company_size": normalize_company_size(raw.get("company_size")),
        "job_title": clean_optional_text(raw.get("job_title")),
        "seniority": title_to_seniority(raw.get("job_title")),
        "source": normalize_source(raw.get("source")),
        "product_interest": clean_optional_text(raw.get("product_interest")),
        "notes": clean_optional_text(raw.get("notes")),
        "consent": normalize_consent(raw.get("consent")),
        "lead_origin": normalize_lead_origin(raw.get("lead_origin")),
        "occupation": normalize_occupation(raw.get("occupation")),
        "total_visits": normalize_count(raw.get("total_visits")),
        "time_on_website_sec": normalize_count(raw.get("time_on_website_sec")),
        "pages_per_visit": normalize_rate(raw.get("pages_per_visit")),
    }
