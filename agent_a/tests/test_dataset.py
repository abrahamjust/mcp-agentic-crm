"""Training data is mapped through the backend's own normalizers."""
import numpy as np
import pandas as pd
import pytest

from lead_scoring.dataset import (
    COLUMN_MAP,
    LEAKAGE_EXCLUDED,
    OTHER_EXCLUDED,
    load_training_data,
    to_crm_records,
)
from lead_scoring.features import FEATURES


def kaggle_rows(**overrides) -> pd.DataFrame:
    row = {
        "Lead Origin": "Landing Page Submission",
        "Lead Source": "Google",
        "What is your current occupation": "Unemployed",
        "TotalVisits": 3.0,
        "Total Time Spent on Website": 300,
        "Page Views Per Visit": 2.5,
        "Converted": 1,
        "Tags": "Will revert after reading the email",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_values_map_to_backend_vocabulary():
    raw = pd.concat([
        kaggle_rows(),
        kaggle_rows(**{"Lead Origin": "Quick Add Form", "Lead Source": "Reference",
                       "What is your current occupation": "Housewife"}),
        kaggle_rows(**{"Lead Source": "Olark Chat", "What is your current occupation": "Businessman"}),
        kaggle_rows(**{"Lead Source": "Facebook", "What is your current occupation": np.nan,
                       "TotalVisits": np.nan, "Page Views Per Visit": 2.333}),
    ])
    records = to_crm_records(raw)
    assert records[0] == {
        "lead_origin": "Landing Page Submission", "source": "Website", "occupation": "Unemployed",
        "total_visits": 3, "time_on_website_sec": 300, "pages_per_visit": 2.5,
    }
    assert records[1]["lead_origin"] == "Lead Add Form"
    assert records[1]["source"] == "Referral"
    assert records[1]["occupation"] == "Homemaker"
    assert records[2]["source"] == "Website"
    assert records[2]["occupation"] == "Business Owner"
    assert records[3]["source"] == "Social"
    assert records[3]["occupation"] == "Unknown"
    assert records[3]["total_visits"] is None
    assert records[3]["pages_per_visit"] == 2.33


def test_leakage_columns_are_never_inputs():
    used = set(COLUMN_MAP)
    assert not used & set(LEAKAGE_EXCLUDED)
    assert not used & set(OTHER_EXCLUDED)
    assert "Tags" not in to_crm_records(kaggle_rows())[0]
    assert set(COLUMN_MAP.values()) == set(FEATURES)


def test_missing_expected_column_is_an_error():
    with pytest.raises(ValueError, match="missing expected columns"):
        to_crm_records(kaggle_rows().drop(columns=["Lead Origin"]))


def test_load_drops_blank_source_rows(tmp_path):
    raw = pd.concat([kaggle_rows(), kaggle_rows(**{"Lead Source": np.nan}), kaggle_rows(Converted=0)])
    path = tmp_path / "leads.csv"
    raw.to_csv(path, index=False)
    data = load_training_data(path)
    assert data.n_rows == 3
    assert data.rows_dropped == {"blank Lead Source": 1}
    assert len(data.X) == len(data.y) == 2
    assert list(data.y) == [1, 0]
    assert len(data.sha256) == 64


def test_load_rejects_bad_target(tmp_path):
    path = tmp_path / "leads.csv"
    kaggle_rows(Converted=2).to_csv(path, index=False)
    with pytest.raises(ValueError, match="0/1"):
        load_training_data(path)


def test_load_missing_file_explains_how_to_download(tmp_path):
    with pytest.raises(FileNotFoundError, match="lead_scoring.training.download"):
        load_training_data(tmp_path / "nope.csv")
