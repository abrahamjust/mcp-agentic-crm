"""add_missing_columns(): an old crm.db gains new columns without losing rows."""
from sqlalchemy import text

from app.database import Base, add_missing_columns, engine


def test_old_leads_table_is_upgraded_in_place(client):
    # Recreate `leads` as it was before the scoring-input columns existed.
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE interactions"))
        conn.execute(text("DROP TABLE leads"))
        conn.execute(text(
            "CREATE TABLE leads ("
            " id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, email VARCHAR NOT NULL,"
            " phone VARCHAR, company VARCHAR, company_size VARCHAR(7) NOT NULL,"
            " job_title VARCHAR, seniority VARCHAR(7) NOT NULL, source VARCHAR(13) NOT NULL,"
            " product_interest VARCHAR, notes TEXT, consent JSON NOT NULL,"
            " pipeline_stage VARCHAR(9) NOT NULL, created_at DATETIME NOT NULL,"
            " last_activity_at DATETIME NOT NULL, lead_score FLOAT,"
            " bucket VARCHAR(8) NOT NULL, top_factors JSON, scored_at DATETIME,"
            " model_version VARCHAR)"
        ))
        conn.execute(text(
            "INSERT INTO leads VALUES ('old-1', 'Old Lead', 'old@example.com', NULL, NULL,"
            " 'UNKNOWN', NULL, 'UNKNOWN', 'UNKNOWN', NULL, NULL, '[]', 'NEW',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00', NULL, 'UNSCORED', '[]', NULL, NULL)"
        ))
    Base.metadata.create_all(bind=engine)

    added = add_missing_columns()

    assert set(added) == {
        "leads.lead_origin",
        "leads.occupation",
        "leads.total_visits",
        "leads.time_on_website_sec",
        "leads.pages_per_visit",
    }
    body = client.get("/leads/old-1").json()
    assert body["name"] == "Old Lead"
    assert body["lead_origin"] == "Unknown"
    assert body["occupation"] == "Unknown"
    assert body["total_visits"] is None
    assert add_missing_columns() == []  # idempotent
