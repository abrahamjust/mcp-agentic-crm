"""
Agent B: act on every Hot lead, inside the consent gate.

    poll get_leads(bucket=Hot, actioned=false)
      -> check_consent(lead_id, "marketing")      Role 5; writes the audit row
         blocked / error -> log_interaction(no_action_consent_blocked, status=blocked)
         allowed         -> draft email (LLM) -> log_interaction(drafted_followup_email,
                                                                 status=pending_approval)
    poll approved drafts -> update_deal_stage(New -> Contacted)

Stateless by design (design doc 5.1): the CRM is the only state. Logging
any interaction marks a lead "actioned", so it is never handled twice, and
a lead stays un-actioned until its interaction is written, so a crash, a
restart or a CRM outage means it is picked up on a later cycle (5.3: "Agent
B catches up on restart by polling for Hot leads with no prior
interaction").

Consent is a gate, not a filter (5.1): Agent B fetches every Hot lead, then
asks. A consent check that errors is treated as blocked (fail closed, 5.3).
If the CRM cannot be reached at all, nothing is done and the lead is
retried later, which is also fail closed.

Nothing is sent to a customer. Drafts wait as pending_approval for a person
(FR5); the lead moves to Contacted once a person approves (or, with
--advance-stage on_draft, as soon as the draft is stored).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from followup.crm.base import CRMClient, CRMRequestError, CRMUnavailableError, LeadNotFoundError
from followup.drafting import Drafter, DraftError

log = logging.getLogger("followup.agent")

PURPOSE = "marketing"
ACTION_DRAFT = "drafted_followup_email"
ACTION_BLOCKED = "no_action_consent_blocked"
STAGE_POLICIES = ("on_approval", "on_draft", "never")


@dataclass
class CycleStats:
    fetched: int = 0
    drafted: int = 0
    blocked: int = 0
    failed: int = 0
    skipped: int = 0
    advanced: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def suggest_next_step(lead: dict[str, Any], stage_policy: str) -> str:
    stage = lead.get("pipeline_stage", "New")
    if stage != "New":
        return f"Lead is already {stage}: review the draft against recent contact before sending."
    if stage_policy == "on_approval":
        return "Review and send this email, then book a 15-minute call. Approving moves the lead to Contacted."
    return "Review and send this email, then book a 15-minute call."


class FollowUpAgent:
    def __init__(
        self,
        crm: CRMClient,
        drafter: Drafter,
        *,
        batch_size: int = 20,
        max_attempts: int = 3,
        stage_policy: str = "on_approval",
        metrics_path: Path | None = None,
    ):
        if stage_policy not in STAGE_POLICIES:
            raise ValueError(f"stage_policy must be one of {STAGE_POLICIES}")
        self.crm = crm
        self.drafter = drafter
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.stage_policy = stage_policy
        self.metrics_path = metrics_path
        # lead_id -> failed attempts; a lead that keeps failing is set aside so
        # it cannot block newer leads (it is retried after a restart)
        self._failures: Counter = Counter()
        # approved drafts already handled in this process (re-checked after a restart)
        self._approvals_done: set[str] = set()

    @property
    def given_up(self) -> set[str]:
        return {lead_id for lead_id, n in self._failures.items() if n >= self.max_attempts}

    # ------------------------------------------------------------------
    # one cycle
    # ------------------------------------------------------------------

    async def run_once(self) -> CycleStats:
        """Handle a batch of Hot leads, then any newly approved drafts.
        Raises CRMUnavailableError if the CRM cannot be reached at all."""
        stats = CycleStats()
        given_up = self.given_up
        leads = await self.crm.get_pending_hot_leads(self.batch_size + len(given_up))
        leads = [lead for lead in leads if lead["id"] not in given_up][: self.batch_size]
        stats.fetched = len(leads)
        for lead in leads:
            await self.handle_lead(lead, stats)
        if self.stage_policy == "on_approval":
            stats.advanced += await self.advance_approved()
        return stats

    async def handle_lead(self, lead: dict[str, Any], stats: CycleStats) -> None:
        lead_id = lead["id"]
        try:
            decision, error = await self.crm.check_consent(lead_id, PURPOSE, ACTION_DRAFT), None
        except LeadNotFoundError:
            log.info("Lead %s was deleted before Agent B acted", lead_id)
            stats.skipped += 1
            return
        except CRMRequestError as exc:  # unusable answer: fail closed
            decision, error = None, str(exc)

        try:
            if decision is None or not decision["allowed"]:
                await self._log_blocked(lead, decision, error)
                stats.blocked += 1
                return

            try:
                draft = await self.drafter.draft(lead)
            except DraftError as exc:
                self._failed(lead_id, exc, stats)
                return
            interaction = await self._log_draft(lead, decision, draft)
            stats.drafted += 1
            if self.stage_policy == "on_draft":
                stats.advanced += await self._advance(lead_id)
            self._record(lead, "drafted", interaction, draft.generator)
        except LeadNotFoundError:
            log.info("Lead %s was deleted before its action was logged", lead_id)
            stats.skipped += 1
        except CRMRequestError as exc:
            self._failed(lead_id, exc, stats)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def _log_blocked(self, lead: dict[str, Any], decision: dict[str, Any] | None, error: str | None) -> None:
        payload = {
            "purpose": PURPOSE,
            "action_attempted": ACTION_DRAFT,
            "reason": decision.get("reason") if decision else f"Consent check failed: {error}",
            "consent_on_file": decision.get("consent_on_file") if decision else None,
            "audit_log_id": decision.get("audit_log_id") if decision else None,
            "lead_bucket": lead.get("bucket"),
            "lead_score": lead.get("lead_score"),
            "acted_at": _now().isoformat(),
        }
        interaction = await self.crm.log_interaction(lead["id"], {
            "action_type": ACTION_BLOCKED,
            "status": "blocked",
            "consent_check": "blocked" if decision else "error",
            "payload": payload,
        })
        self._record(lead, "blocked", interaction, None, reason=payload["reason"])

    async def _log_draft(self, lead: dict[str, Any], decision: dict[str, Any], draft) -> dict[str, Any]:
        payload = {
            "email": draft.as_payload(),
            "purpose": PURPOSE,
            "consent_reason": decision.get("reason"),
            "audit_log_id": decision.get("audit_log_id"),
            "suggested_next_step": suggest_next_step(lead, self.stage_policy),
            # for the reviewer only; never part of the customer-facing text
            "reviewer_context": {
                "lead_score": lead.get("lead_score"),
                "bucket": lead.get("bucket"),
                "top_factors": lead.get("top_factors") or [],
                "model_version": lead.get("model_version"),
            },
            "acted_at": _now().isoformat(),
        }
        return await self.crm.log_interaction(lead["id"], {
            "action_type": ACTION_DRAFT,
            "status": "pending_approval",
            "consent_check": "passed",
            "payload": payload,
        })

    async def _advance(self, lead_id: str) -> int:
        """New -> Contacted. Never moves a lead backwards or out of a later
        stage a person has set."""
        lead = await self.crm.get_lead(lead_id)
        if lead.get("pipeline_stage") != "New":
            return 0
        await self.crm.update_deal_stage(lead_id, "Contacted")
        log.info("Lead %s moved New -> Contacted", lead_id)
        return 1

    async def advance_approved(self) -> int:
        """Advance leads whose drafts a person approved. Idempotent: a lead
        that is no longer New is left alone."""
        try:
            approved = await self.crm.get_approved_drafts(200)
        except CRMUnavailableError as exc:
            log.debug("Cannot list approved drafts (%s); will retry", exc)
            return 0
        advanced = 0
        for item in approved:
            if item["id"] in self._approvals_done:
                continue
            try:
                advanced += await self._advance(item["lead_id"])
            except LeadNotFoundError:
                pass
            except CRMRequestError as exc:
                log.warning("Could not advance lead %s after approval: %s", item.get("lead_id"), exc)
                continue
            self._approvals_done.add(item["id"])
        return advanced

    # ------------------------------------------------------------------
    # bookkeeping
    # ------------------------------------------------------------------

    def _failed(self, lead_id: str, exc: Exception, stats: CycleStats) -> None:
        stats.failed += 1
        self._failures[lead_id] += 1
        attempts = self._failures[lead_id]
        if attempts >= self.max_attempts:
            log.error("Lead %s failed %d times (%s); skipping it until restart", lead_id, attempts, exc)
        else:
            log.warning("Lead %s failed (attempt %d/%d): %s", lead_id, attempts, self.max_attempts, exc)

    def _record(self, lead: dict[str, Any], outcome: str, interaction: dict[str, Any],
                generator: str | None, reason: str | None = None) -> None:
        self._failures.pop(lead["id"], None)
        now = _now()
        scored = _parse_time(lead.get("scored_at"))
        created = _parse_time(lead.get("created_at"))
        score_to_action = (now - scored).total_seconds() if scored else None
        create_to_action = (now - created).total_seconds() if created else None
        transport = getattr(self.crm, "last_transport", None) or self.crm.name
        log.info(
            "Lead %s (%s): %s via %s%s%s",
            lead["id"], lead.get("name"), outcome, transport,
            f", draft by {generator}" if generator else "",
            f", {score_to_action:.2f}s after scoring" if score_to_action is not None else "",
        )
        if reason:
            log.info("  reason: %s", reason)
        if self.metrics_path:
            row = {
                "lead_id": lead["id"],
                "outcome": outcome,
                "interaction_id": interaction.get("id"),
                "generator": generator,
                "transport": transport,
                "created_at": lead.get("created_at"),
                "scored_at": lead.get("scored_at"),
                "acted_at": now.isoformat(),
                "score_to_action_sec": score_to_action,
                "creation_to_action_sec": create_to_action,
            }
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.metrics_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

    # ------------------------------------------------------------------
    # loop
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        poll_interval: float = 3.0,
        max_backoff: float = 30.0,
        stop: asyncio.Event | None = None,
        wakeup: asyncio.Event | None = None,
    ) -> None:
        """Poll until `stop` is set. Backs off exponentially while the CRM is
        unreachable and never dies on a single bad cycle."""
        stop = stop or asyncio.Event()
        backoff = poll_interval
        log.info("Agent B running: drafts by %s, stage policy %s, polling every %.1fs via %s",
                 self.drafter.name, self.stage_policy, poll_interval, self.crm.name)
        while not stop.is_set():
            delay = poll_interval
            try:
                stats = await self.run_once()
            except CRMUnavailableError as exc:
                log.warning("CRM unreachable (%s); retrying in %.0fs", exc, backoff)
                delay, backoff = backoff, min(backoff * 2, max_backoff)
            except Exception:
                log.exception("Unexpected error in Agent B cycle; continuing")
            else:
                backoff = poll_interval
                if stats.fetched or stats.advanced:
                    log.info("Cycle: %s", stats.as_dict())
                if stats.fetched >= self.batch_size:
                    continue  # a backlog: go again straight away
            await _sleep(delay, stop, wakeup)
        log.info("Agent B stopped")


async def _sleep(seconds: float, stop: asyncio.Event, wakeup: asyncio.Event | None) -> None:
    events = [stop] + ([wakeup] if wakeup else [])
    waiters = [asyncio.create_task(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()
        if wakeup:
            wakeup.clear()


def is_wakeup_event(message: Any) -> bool:
    """A Hot lead was scored, or a draft was approved."""
    if not isinstance(message, dict):
        return False
    event, data = message.get("event"), message.get("data") or {}
    if event == "lead_scored":
        return data.get("bucket") == "Hot"
    if event == "interaction_updated":
        return data.get("status") == "approved" and data.get("action_type") == ACTION_DRAFT
    return False


async def listen_for_events(ws_url: str, wakeup: asyncio.Event, *, max_backoff: float = 30.0) -> None:
    """Set `wakeup` on the backend events Agent B reacts to. The event only
    wakes the loop; all reads and writes still go through MCP/REST.
    Reconnects forever; cancel the task to stop it."""
    import websockets

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(ws_url) as socket:
                log.info("Listening for Hot leads and approvals on %s", ws_url)
                backoff = 1.0
                async for raw in socket:
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    if is_wakeup_event(message):
                        wakeup.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("WebSocket %s unavailable (%r); polling continues", ws_url, exc)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)
