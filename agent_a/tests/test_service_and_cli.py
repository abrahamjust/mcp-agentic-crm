"""HTTP prediction service and command line."""
import json

from fastapi.testclient import TestClient

from lead_scoring.__main__ import _ws_url, main
from lead_scoring.service import app


def test_service_scores_a_lead():
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["status"] == "ok"

        body = client.post("/score", json={"id": "x", "lead_origin": "Lead Add Form",
                                           "occupation": "Working Professional", "name": "ignored"}).json()
        assert body["lead_id"] == "x"
        assert body["bucket"] == "Hot"
        assert body["model_version"] == health["model_version"]

        info = client.get("/model").json()
        assert info["thresholds"]["cold"] < info["thresholds"]["hot"]


def test_service_rejects_negative_engagement():
    with TestClient(app) as client:
        assert client.post("/score", json={"total_visits": -1}).status_code == 422


def test_cli_score_file(tmp_path, capsys):
    path = tmp_path / "lead.json"
    path.write_text(json.dumps({"id": "cli", "occupation": "Student", "time_on_website_sec": 10}))
    assert main(["score", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lead_id"] == "cli"
    assert out["bucket"] in ("Hot", "Warm", "Cold")


def test_cli_info(capsys):
    assert main(["info"]) == 0
    assert "thresholds" in json.loads(capsys.readouterr().out)


def test_ws_url_derived_from_api_url():
    assert _ws_url("http://127.0.0.1:8000") == "ws://127.0.0.1:8000/ws"
    assert _ws_url("https://crm.example.com/api/") == "wss://crm.example.com/api/ws"
