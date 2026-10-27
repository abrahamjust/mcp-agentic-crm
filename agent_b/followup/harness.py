"""
Test harness (brief, Role 4): simulated Hot leads with consent granted,
denied and missing, run through the real Agent B against the real backend.

    1. create each lead (POST /leads) and mark it Hot as Agent A would
       (PATCH /leads/{id}/score)
    2. run Agent B until nothing is left
    3. check every lead: exactly one interaction of the expected kind and
       status, a matching consent audit entry, a well-formed draft
    4. approve one draft and reject another; check that only the approved
       lead moves New -> Contacted
    5. run Agent B again; check that nothing is handled twice

By default the backend runs in-process against a throwaway SQLite file, so
crm.db is untouched. With --api-url the leads go to a running backend and
appear on the dashboard (useful as a live demo).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from followup.agent import ACTION_BLOCKED, ACTION_DRAFT, FollowUpAgent
from followup.crm import RESTCRMClient
from followup.drafting import Drafter, validate

LEADS_FILE = Path(__file__).with_name("harness_leads.json")
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


@dataclass
class CaseResult:
    case: str
    expect: str
    lead_id: str
    name: str
    outcome: str = "none"
    status: str | None = None
    consent_check: str | None = None
    audit_result: str | None = None
    reason: str | None = None
    generator: str | None = None
    subject: str | None = None
    body: str | None = None
    fallback_reason: str | None = None
    dropped_fields: dict[str, str] | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def load_cases(path: Path = LEADS_FILE) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def in_process_backend() -> httpx.AsyncBaseTransport:
    """The backend app on a fresh temporary database."""
    os.environ["CRM_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "agent_b_harness.db")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from app.database import Base, engine
    from app.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return httpx.ASGITransport(app=app)


async def run_harness(
    drafter: Drafter,
    *,
    api_url: str = "http://crm",
    transport: httpx.AsyncBaseTransport | None = None,
    cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cases = cases if cases is not None else load_cases()
    run_tag = datetime.now(timezone.utc).strftime("%H%M%S")
    http = httpx.AsyncClient(base_url=api_url, transport=transport, timeout=30)
    crm = RESTCRMClient(api_url, transport=transport, timeout=30)
    agent = FollowUpAgent(crm, drafter, stage_policy="on_approval")
    results: list[CaseResult] = []
    checks: list[tuple[str, bool]] = []
    try:
        # 1. simulated Agent A output
        for i, case in enumerate(cases):
            lead_in = dict(case["lead"])
            if api_url != "http://crm":  # live backend: keep emails unique across runs
                user, _, domain = lead_in["email"].partition("@")
                lead_in["email"] = f"{user}+h{run_tag}@{domain}"
            lead = (await http.post("/leads", json=lead_in)).raise_for_status().json()
            await http.patch(f"/leads/{lead['id']}/score", json={
                "score": round(0.95 - 0.02 * i, 3), "bucket": "Hot", "model_version": "harness",
                "top_factors": ["Occupation: Working Professional (raises score)",
                                "Lead origin: Lead Add Form (raises score)",
                                "Time on website: 25 min (raises score)"],
            })
            results.append(CaseResult(case["case"], case["expect"], lead["id"], lead["name"]))

        # 2. Agent B until the queue is empty
        started = time.perf_counter()
        total = {"drafted": 0, "blocked": 0, "failed": 0}
        while True:
            stats = await agent.run_once()
            for k in total:
                total[k] += getattr(stats, k)
            if stats.fetched == 0 or stats.drafted + stats.blocked == 0:
                break
        elapsed = time.perf_counter() - started

        # 3. per-lead checks
        for r in results:
            await _check_lead(http, r)
        checks.append(("every lead has the expected outcome", all(r.passed for r in results)))
        checks.append(("no lead without marketing consent got a draft",
                       not any(r.expect == "blocked" and r.outcome == ACTION_DRAFT for r in results)))

        # 4. approval boundary
        drafted = [r for r in results if r.outcome == ACTION_DRAFT]
        if len(drafted) >= 2:
            approve, reject = drafted[0], drafted[1]
            for r, status in ((approve, "approved"), (reject, "rejected")):
                items = (await http.get(f"/leads/{r.lead_id}/interactions")).json()
                await http.patch(f"/interactions/{items[0]['id']}/status", json={"status": status})
            stats = await agent.run_once()
            stage_a = (await http.get(f"/leads/{approve.lead_id}")).json()["pipeline_stage"]
            stage_r = (await http.get(f"/leads/{reject.lead_id}")).json()["pipeline_stage"]
            untouched = [(await http.get(f"/leads/{r.lead_id}")).json()["pipeline_stage"] for r in drafted[2:]]
            checks.append((f"approved draft moves {approve.name} New -> Contacted", stage_a == "Contacted"))
            checks.append((f"rejected draft leaves {reject.name} in New", stage_r == "New"))
            checks.append(("pending drafts leave their leads in New", all(s == "New" for s in untouched)))

        # 5. idempotency
        before = len((await http.get("/interactions", params={"limit": 1000})).json())
        again = await agent.run_once()
        after = len((await http.get("/interactions", params={"limit": 1000})).json())
        checks.append(("a second run handles nothing twice", again.fetched == 0 and before == after))

        return {
            "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "backend": "in-process (temporary database)" if api_url == "http://crm" else api_url,
            "drafter": drafter.name,
            "leads": len(results),
            "totals": total,
            "agent_seconds": round(elapsed, 2),
            "seconds_per_lead": round(elapsed / max(len(results), 1), 2),
            "checks": [{"check": c, "passed": ok} for c, ok in checks],
            "passed": all(ok for _, ok in checks),
            "cases": [r.__dict__ | {"passed": r.passed} for r in results],
        }
    finally:
        await crm.aclose()
        await http.aclose()


async def _check_lead(http: httpx.AsyncClient, r: CaseResult) -> None:
    items = (await http.get(f"/leads/{r.lead_id}/interactions")).json()
    audits = (await http.get("/audit-log", params={"lead_id": r.lead_id})).json()
    if len(items) != 1:
        r.problems.append(f"{len(items)} interactions (want exactly 1)")
    if not items:
        return
    item = items[0]
    payload = item.get("payload") or {}
    r.outcome, r.status, r.consent_check = item["action_type"], item["status"], item.get("consent_check")
    r.audit_result = audits[0]["result"] if audits else None

    want = {"drafted": (ACTION_DRAFT, "pending_approval", "passed", "allowed"),
            "blocked": (ACTION_BLOCKED, "blocked", "blocked", "blocked")}[r.expect]
    got = (r.outcome, r.status, r.consent_check, r.audit_result)
    for label, w, g in zip(("action_type", "status", "consent_check", "audit result"), want, got):
        if w != g:
            r.problems.append(f"{label} {g!r} (want {w!r})")
    if payload.get("audit_log_id") not in {a["log_id"] for a in audits}:
        r.problems.append("interaction does not reference its consent audit entry")

    if r.outcome == ACTION_BLOCKED:
        r.reason = payload.get("reason")
        if "email" in payload:
            r.problems.append("a blocked lead has an email draft")
    if r.outcome == ACTION_DRAFT:
        email = payload.get("email") or {}
        r.generator, r.subject, r.body = email.get("generator"), email.get("subject"), email.get("body")
        r.fallback_reason = email.get("fallback_reason")
        r.dropped_fields = email.get("dropped_fields")
        first = r.name.split(" ")[0]
        body = r.body or ""
        if not body.startswith(f"Hi {first},"):
            r.problems.append("draft does not open with the lead's first name")
        if "Best regards," not in body:
            r.problems.append("draft has no sign-off")
        if r.generator == "llm":
            middle = body.split("\n\n")[1:-1]
            r.problems += [f"draft {p}" for p in validate(r.subject or "", middle)]


def render_markdown(report: dict[str, Any]) -> str:
    def cell(v: Any) -> str:
        return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")

    lines = [
        "# Agent B harness report",
        "",
        f"- Run: {report['ran_at']}",
        f"- Backend: {report['backend']}",
        f"- Drafter: `{report['drafter']}`",
        f"- Leads: {report['leads']} simulated Hot leads; "
        f"{report['totals']['drafted']} drafted, {report['totals']['blocked']} blocked, "
        f"{report['totals']['failed']} failed",
        f"- Agent time: {report['agent_seconds']} s ({report['seconds_per_lead']} s per lead)",
        f"- **Overall: {'PASS' if report['passed'] else 'FAIL'}**",
        "",
        "## Checks",
        "",
        "| Check | Result |",
        "|---|---|",
        *[f"| {cell(c['check'])} | {'pass' if c['passed'] else '**FAIL**'} |" for c in report["checks"]],
        "",
        "## Leads",
        "",
        "| Case | Lead | Action logged | Status | Consent | Audit | Draft by | Result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in report["cases"]:
        lines.append(
            f"| {cell(c['case'])} | {cell(c['name'])} | `{cell(c['outcome'])}` | {cell(c['status'])} | "
            f"{cell(c['consent_check'])} | {cell(c['audit_result'])} | {cell(c['generator'])} | "
            f"{'pass' if c['passed'] else 'FAIL: ' + cell('; '.join(c['problems']))} |"
        )
    lines += ["", "## Blocked attempts", ""]
    for c in report["cases"]:
        if c["outcome"] == ACTION_BLOCKED:
            lines.append(f"- **{c['name']}** ({c['case']}): {c['reason']}")
    lines += ["", "## Drafts (pending approval, not sent)", ""]
    for c in report["cases"]:
        if c["outcome"] == ACTION_DRAFT:
            lines += [f"### {c['name']}: {c['case']}", ""]
            if c.get("fallback_reason"):
                lines += [f"_Template used: {c['fallback_reason']}_", ""]
            for key, why in (c.get("dropped_fields") or {}).items():
                lines += [f"_Form field `{key}` left out of the email: {why}_", ""]
            lines += [f"**Subject:** {c['subject']}", "", "```text", c["body"] or "", "```", ""]
    return "\n".join(lines)


def print_summary(report: dict[str, Any]) -> None:
    print(f"\nAgent B harness: {report['leads']} Hot leads via {report['backend']}, drafts by {report['drafter']}")
    for c in report["cases"]:
        mark = "PASS" if c["passed"] else "FAIL"
        extra = f" ({c['generator']})" if c["generator"] else ""
        print(f"  [{mark}] {c['name']:<16} {c['case']:<48} -> {c['outcome']}{extra}")
        for p in c["problems"]:
            print(f"         - {p}")
    print()
    for c in report["checks"]:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}")
    print(f"\n  Agent time {report['agent_seconds']} s ({report['seconds_per_lead']} s per lead). "
          f"Overall: {'PASS' if report['passed'] else 'FAIL'}\n")
