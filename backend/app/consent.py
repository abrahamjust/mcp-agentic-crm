"""Consent decisions used by the consent/compliance layer."""
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import Lead


@dataclass(frozen=True)
class ConsentDecision:
    allowed: bool
    reason: str
    consent_on_file: bool


def check_consent(db: Session, lead_id: str, purpose: str) -> ConsentDecision:
    """Return the latest decision for ``purpose`` on a lead.

    Consent is purpose-specific. Missing or malformed records fail closed,
    while a later record for the same purpose supersedes an older one.
    """
    lead = db.get(Lead, lead_id)
    if lead is None:
        return ConsentDecision(False, "Lead not found", False)

    requested_purpose = purpose.strip().lower()
    if not requested_purpose:
        return ConsentDecision(False, "Purpose is required", False)

    records: list[dict[str, Any]] = [
        record
        for record in (lead.consent or [])
        if isinstance(record, dict)
        and str(record.get("purpose", "")).strip().lower() == requested_purpose
    ]
    if not records:
        return ConsentDecision(
            False, f"No consent record exists for purpose '{requested_purpose}'", False
        )

    record = records[-1]
    if not isinstance(record.get("granted"), bool):
        return ConsentDecision(
            False, f"Consent record for '{requested_purpose}' is invalid", True
        )
    if not record["granted"]:
        return ConsentDecision(False, f"Consent denied for purpose '{requested_purpose}'", True)
    return ConsentDecision(True, f"Consent granted for purpose '{requested_purpose}'", True)
