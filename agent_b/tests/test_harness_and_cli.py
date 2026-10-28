"""The simulated Hot-lead harness and the command line."""
import json

import httpx
import pytest

from followup import harness
from followup.__main__ import main, parser

pytestmark = pytest.mark.anyio


def test_harness_set_meets_the_brief():
    """5-10 simulated Hot leads with both consent outcomes."""
    cases = harness.load_cases()
    assert 5 <= len(cases) <= 10
    assert {c["expect"] for c in cases} == {"drafted", "blocked"}


async def test_harness_passes_on_the_real_backend(backend_app, template):
    report = await harness.run_harness(template, transport=httpx.ASGITransport(app=backend_app))
    assert report["passed"], json.dumps(report["checks"], indent=2)
    assert report["totals"] == {"drafted": 5, "blocked": 3, "failed": 0}
    markdown = harness.render_markdown(report)
    assert "Overall: PASS" in markdown and "Consent denied for purpose 'marketing'" in markdown
    assert "looks like an instruction" in markdown


async def test_harness_catches_a_wrong_outcome(backend_app, template):
    cases = harness.load_cases()[:2]
    cases[0] = {**cases[0], "expect": "blocked"}  # it has consent, so it will be drafted
    report = await harness.run_harness(template, transport=httpx.ASGITransport(app=backend_app), cases=cases)
    assert not report["passed"]
    assert report["cases"][0]["problems"]


def test_cli_draft_with_template(tmp_path, capsys):
    lead = tmp_path / "lead.json"
    lead.write_text(json.dumps({"name": "Karthik Raman", "email": "k@example.com", "occupation": "Business Owner"}))
    assert main(["draft", str(lead), "--llm", "template"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["generator"] == "template" and out["body"].startswith("Hi Karthik,")


def test_cli_defaults(monkeypatch):
    monkeypatch.delenv("AGENT_B_ADVANCE_STAGE", raising=False)
    args = parser().parse_args(["run"])
    assert args.advance_stage == "on_approval"
    assert args.llm == "ollama" and args.transport == "auto"
    assert args.api_url == "http://127.0.0.1:8000"
