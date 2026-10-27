"""The model's input contract (shared by training and live scoring)."""
import enum
import math

from lead_scoring.features import FEATURES, describe_value, model_inputs, to_frame


def test_full_record_passes_through():
    row = model_inputs({
        "lead_origin": "Lead Add Form", "source": "Referral", "occupation": "Student",
        "total_visits": 3, "time_on_website_sec": 120, "pages_per_visit": 1.5,
    })
    assert row == {
        "lead_origin": "Lead Add Form", "source": "Referral", "occupation": "Student",
        "total_visits": 3.0, "time_on_website_sec": 120.0, "pages_per_visit": 1.5,
    }


def test_missing_fields_become_unknown_and_nan():
    row = model_inputs({})
    assert row["lead_origin"] == row["source"] == row["occupation"] == "Unknown"
    assert all(math.isnan(row[f]) for f in ("total_visits", "time_on_website_sec", "pages_per_visit"))


def test_blank_and_invalid_values_are_tolerated():
    row = model_inputs({
        "source": "   ", "occupation": None, "total_visits": "abc",
        "time_on_website_sec": -5, "pages_per_visit": float("inf"),
    })
    assert row["source"] == "Unknown"
    assert row["occupation"] == "Unknown"
    assert math.isnan(row["total_visits"])
    assert math.isnan(row["time_on_website_sec"])
    assert math.isnan(row["pages_per_visit"])


def test_numeric_strings_and_booleans():
    row = model_inputs({"total_visits": "4", "time_on_website_sec": True})
    assert row["total_visits"] == 4.0
    assert math.isnan(row["time_on_website_sec"])  # a bool is not a count


def test_enum_members_are_unwrapped():
    class Source(str, enum.Enum):
        WEBSITE = "Website"

    assert model_inputs({"source": Source.WEBSITE})["source"] == "Website"


def test_employed_seniority_fills_missing_occupation():
    assert model_inputs({"seniority": "Director"})["occupation"] == "Working Professional"
    assert model_inputs({"seniority": "IC", "occupation": "Unknown"})["occupation"] == "Working Professional"


def test_seniority_never_overrides_a_given_occupation():
    assert model_inputs({"seniority": "Director", "occupation": "Student"})["occupation"] == "Student"


def test_unknown_seniority_does_not_fill_occupation():
    assert model_inputs({"seniority": "Unknown"})["occupation"] == "Unknown"


def test_to_frame_has_exact_columns_and_dtypes():
    frame = to_frame([{"source": "Website", "total_visits": 2}, {}])
    assert list(frame.columns) == FEATURES
    assert frame["total_visits"].dtype == float
    assert frame["source"].dtype == object
    assert len(frame) == 2


def test_describe_value():
    assert describe_value("time_on_website_sec", 930.0) == "15 min 30 s"
    assert describe_value("time_on_website_sec", 45.0) == "45 s"
    assert describe_value("pages_per_visit", 2.456) == "2.5"
    assert describe_value("total_visits", 3.0) == "3"
    assert describe_value("occupation", "Unknown") == "not provided"
    assert describe_value("total_visits", float("nan")) == "not recorded"
