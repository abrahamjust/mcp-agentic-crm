"""
The model's input contract.

Training and live scoring both call `to_frame()`, so a lead is turned into
model inputs by exactly one piece of code. The field names are the CRM
backend's standardized lead fields (see backend/app/models.py); the training
data is mapped onto those same fields in `dataset.py` before it gets here.

Only fields known when a lead is created are model inputs. Consent is
deliberately NOT an input: scoring stays consent-agnostic and the consent
layer alone decides what may be done with a lead.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

CATEGORICAL = ["lead_origin", "source", "occupation"]
NUMERIC = ["total_visits", "time_on_website_sec", "pages_per_visit"]
FEATURES = CATEGORICAL + NUMERIC

UNKNOWN = "Unknown"

# How "Unknown" is handled per categorical field:
# - occupation: a learned category. 29% of training leads have none (a
#   short-form sign-up path) and they convert at 14%, so it is real signal.
# - lead_origin, source: (almost) never blank in training, so "Unknown" has
#   nothing to learn from; the model fills it with the most common value
#   (neutral) and it is never cited as a reason for a score.
IMPUTE_UNKNOWN = ["lead_origin", "source"]
LEARNED_UNKNOWN = ["occupation"]

# Human-readable names, used in top_factors shown on the dashboard.
LABELS = {
    "lead_origin": "Lead origin",
    "source": "Source",
    "occupation": "Occupation",
    "total_visits": "Website visits",
    "time_on_website_sec": "Time on website",
    "pages_per_visit": "Pages per visit",
}

# Seniority values that imply the lead is employed (backend Seniority enum).
_EMPLOYED_SENIORITY = {"C-level", "Director", "Manager", "IC"}


def _category(value: Any) -> str:
    if value is None:
        return UNKNOWN
    if hasattr(value, "value"):  # an Enum member
        value = value.value
    text = str(value).strip()
    return text or UNKNOWN


def _number(value: Any) -> float:
    """Non-negative float, or NaN for missing / invalid / negative input."""
    if value is None or isinstance(value, bool):
        return math.nan
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    if math.isnan(number) or math.isinf(number) or number < 0:
        return math.nan
    return number


def model_inputs(lead: Mapping[str, Any]) -> dict[str, Any]:
    """One lead record (backend LeadOut JSON or a training row) -> model inputs.

    Serve-time rule: a B2B lead whose job title maps to a known seniority but
    whose occupation was not captured is employed, so occupation becomes
    "Working Professional" instead of "Unknown". Training rows carry no
    seniority, so the rule never fires there.
    """
    row = {name: _category(lead.get(name)) for name in CATEGORICAL}
    row.update({name: _number(lead.get(name)) for name in NUMERIC})
    if row["occupation"] == UNKNOWN and _category(lead.get("seniority")) in _EMPLOYED_SENIORITY:
        row["occupation"] = "Working Professional"
    return row


def to_frame(leads: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Lead records -> a DataFrame with exactly FEATURES, in order."""
    rows = [model_inputs(lead) for lead in leads]
    frame = pd.DataFrame(rows, columns=FEATURES)
    frame[CATEGORICAL] = frame[CATEGORICAL].astype(object)
    frame[NUMERIC] = frame[NUMERIC].astype(float)
    return frame


def describe_value(feature: str, value: Any) -> str:
    """Display form of one input value, e.g. 930 seconds -> '15 min 30 s'."""
    if feature in CATEGORICAL:
        return "not provided" if value == UNKNOWN else str(value)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "not recorded"
    if feature == "time_on_website_sec":
        minutes, seconds = divmod(int(round(value)), 60)
        return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
    if feature == "pages_per_visit":
        return f"{value:.1f}"
    return f"{int(round(value))}"
