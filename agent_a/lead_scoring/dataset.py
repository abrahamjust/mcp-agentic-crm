"""
Training data: Kaggle "Lead Scoring X Online Education" -> canonical CRM leads.

Every raw value goes through the CRM backend's own normalizers
(backend/app/standardize.py), the same functions that clean a live form
submission. A category therefore means the same thing in training and in
live scoring (requirement FR2: one canonical representation).

Column policy: a column is a model input only if its value is known when
the lead is created. Columns filled in by the sales team after contact
describe the outcome rather than predict it (target leakage); they are
listed in LEAKAGE_EXCLUDED with the reason.
"""
from __future__ import annotations

import hashlib
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from lead_scoring.features import to_frame

TARGET = "Converted"

# Kaggle column -> backend lead field
COLUMN_MAP = {
    "Lead Origin": "lead_origin",
    "Lead Source": "source",
    "What is your current occupation": "occupation",
    "TotalVisits": "total_visits",
    "Total Time Spent on Website": "time_on_website_sec",
    "Page Views Per Visit": "pages_per_visit",
}

LEAKAGE_EXCLUDED = {
    "Tags": "Sales-rep status recorded after contact ('Will revert after reading the email' converts 97%, 'Ringing' 3%).",
    "Lead Quality": "Sales-rep judgement recorded after contact ('High in Relevance' converts 95%, 'Worst' 2%).",
    "Lead Profile": "Assigned by the sales team after qualification ('Potential Lead' converts 79%).",
    "Last Activity": "Latest touch-point at extraction time, largely sales actions (SMS sent, phone call); unknown when a lead is created.",
    "Last Notable Activity": "Same as Last Activity; snapshot at extraction time.",
    "Asymmetrique Activity Index": "Scores assigned by the business after profiling; 46% missing.",
    "Asymmetrique Profile Index": "Scores assigned by the business after profiling; 46% missing.",
    "Asymmetrique Activity Score": "Scores assigned by the business after profiling; 46% missing.",
    "Asymmetrique Profile Score": "Scores assigned by the business after profiling; 46% missing.",
}

OTHER_EXCLUDED = {
    "Prospect ID": "Identifier.",
    "Lead Number": "Identifier.",
    "Do Not Email": "Consent signal; scoring is kept consent-agnostic (the consent layer enforces it).",
    "Do Not Call": "Consent signal; 2 of 9,240 rows are 'Yes'.",
    "Country": "27% missing, 96% India among known values; location is also a fairness risk.",
    "City": "40% missing or unselected; location is also a fairness risk.",
    "Specialization": "No added predictive value in cross-validation (AUC +0.001).",
    "How did you hear about X Education": "78% missing or unselected.",
    "What matters most to you in choosing a course": "Near-constant (99.9% one value when present).",
    "Search": "Near-constant.",
    "Magazine": "Constant.",
    "Newspaper Article": "Near-constant.",
    "X Education Forums": "Near-constant.",
    "Newspaper": "Near-constant.",
    "Digital Advertisement": "Near-constant.",
    "Through Recommendations": "Near-constant.",
    "Receive More Updates About Our Courses": "Constant.",
    "Update me on Supply Chain Content": "Constant.",
    "Get updates on DM Content": "Constant.",
    "I agree to pay the amount through cheque": "Constant.",
    "A free copy of Mastering The Interview": "Weak signal (40% vs 36% conversion); not a CRM form field.",
}

DEFAULT_CSV = Path(__file__).resolve().parents[1] / "data" / "raw" / "Leads X Education.csv"
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


ROW_FILTERS = {
    "blank Lead Source": (
        "In this dataset a blank source marks leads added by hand (81% convert), "
        "while in the CRM it only means the field was left empty; keeping them "
        "would make 'source not provided' raise the score."
    ),
}


@dataclass(frozen=True)
class TrainingData:
    X: pd.DataFrame
    y: np.ndarray
    source_path: Path
    sha256: str
    n_rows: int
    rows_dropped: dict[str, int]


def _backend_standardize():
    """Import the backend's normalizers (the single source of truth)."""
    if str(_BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(_BACKEND_DIR))
    try:
        from app import standardize
    except ImportError as exc:  # pragma: no cover - environment problem
        raise ImportError(
            f"Could not import the CRM backend normalizers from {_BACKEND_DIR}. "
            "Training needs the backend package and its requirements installed."
        ) from exc
    return standardize


def _blank_to_none(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def to_crm_records(raw: pd.DataFrame) -> list[dict[str, Any]]:
    """Raw Kaggle rows -> standardized CRM lead records (model fields only)."""
    missing = set(COLUMN_MAP) - set(raw.columns)
    if missing:
        raise ValueError(f"Dataset is missing expected columns: {sorted(missing)}")

    std = _backend_standardize()
    records = []
    for row in raw[list(COLUMN_MAP)].itertuples(index=False, name=None):
        values = dict(zip(COLUMN_MAP.values(), (_blank_to_none(v) for v in row)))
        records.append({
            "lead_origin": std.normalize_lead_origin(values["lead_origin"]).value,
            "source": std.normalize_source(values["source"]).value,
            "occupation": std.normalize_occupation(values["occupation"]).value,
            "total_visits": std.normalize_count(values["total_visits"]),
            "time_on_website_sec": std.normalize_count(values["time_on_website_sec"]),
            "pages_per_visit": std.normalize_rate(values["pages_per_visit"]),
        })
    return records


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_training_data(path: Path = DEFAULT_CSV) -> TrainingData:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Download it with: python -m lead_scoring.training.download"
        )
    raw = pd.read_csv(path)
    if TARGET not in raw.columns:
        raise ValueError(f"Dataset has no '{TARGET}' column")
    labels = raw[TARGET]
    if labels.isna().any() or not labels.isin([0, 1]).all():
        raise ValueError(f"'{TARGET}' must be 0/1 with no missing values")

    n_rows = len(raw)
    blank_source = raw["Lead Source"].isna()
    raw = raw[~blank_source].reset_index(drop=True)

    X = to_frame(to_crm_records(raw))
    return TrainingData(
        X=X,
        y=raw[TARGET].to_numpy(dtype=int),
        source_path=path,
        sha256=file_sha256(path),
        n_rows=n_rows,
        rows_dropped={"blank Lead Source": int(blank_source.sum())},
    )
