"""
Agent A: score every unscored lead and write the result back to the CRM.

    poll get_unscored_leads -> LeadScorer.score_many -> update_lead_score

Stateless by design (design doc 5.1): the CRM is the only state. A lead
stays "Unscored" until a write succeeds, so a crash, a restart or a CRM
outage simply means it is picked up on a later cycle. Optionally a
WebSocket listener on the backend's `lead_created` event wakes the loop
immediately instead of waiting for the next poll; it carries no data, so
all reads and writes still go through MCP/REST.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lead_scoring.crm.base import CRMClient, CRMRequestError, CRMUnavailableError, LeadNotFoundError
from lead_scoring.predictor import LeadScorer, ScoreResult

log = logging.getLogger("lead_scoring.agent")


@dataclass
class CycleStats:
    fetched: int = 0
    scored: int = 0
    failed: int = 0
    skipped: int = 0
    buckets: Counter = field(default_factory=Counter)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ScoringAgent:
    def __init__(
        self,
        crm: CRMClient,
        scorer: LeadScorer,
        *,
        batch_size: int = 50,
        max_attempts: int = 3,
        metrics_path: Path | None = None,
    ):
        self.crm = crm
        self.scorer = scorer
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.metrics_path = metrics_path
        # lead_id -> failed attempts; a lead that keeps failing is set aside so
        # it cannot block newer leads (it is retried after a restart)
        self._failures: Counter = Counter()

    @property
    def given_up(self) -> set[str]:
        return {lead_id for lead_id, n in self._failures.items() if n >= self.max_attempts}

    # ------------------------------------------------------------------

    async def run_once(self) -> CycleStats:
        """One poll: fetch a batch of unscored leads, score them, write back.
        Raises CRMUnavailableError if the CRM cannot be reached at all."""
        stats = CycleStats()
        given_up = self.given_up
        leads = await self.crm.get_unscored_leads(self.batch_size + len(given_up))
        leads = [lead for lead in leads if lead["id"] not in given_up][: self.batch_size]
        stats.fetched = len(leads)
        if not leads:
            return stats

        started = time.perf_counter()
        results = await asyncio.to_thread(self._score_isolated, leads, stats)
        inference_ms = 1000 * (time.perf_counter() - started) / len(leads)

        for lead, result in zip(leads, results):
            if result is None:
                continue
            try:
                await self.crm.update_lead_score(lead["id"], result.as_update())
            except LeadNotFoundError:
                log.info("Lead %s was deleted before its score was written", lead["id"])
                stats.skipped += 1
            except CRMRequestError as exc:
                self._failed(lead["id"], exc, stats)
            else:
                stats.scored += 1
                stats.buckets[result.bucket] += 1
                self._record(lead, result, inference_ms)
        return stats

    def _score_isolated(self, leads: list[dict[str, Any]], stats: CycleStats) -> list[ScoreResult | None]:
        try:
            return self.scorer.score_many(leads)
        except Exception:
            log.warning("Batch scoring failed; scoring leads one by one to isolate the bad record", exc_info=True)
        results: list[ScoreResult | None] = []
        for lead in leads:
            try:
                results.append(self.scorer.score(lead))
            except Exception as exc:
                self._failed(lead["id"], exc, stats)
                results.append(None)
        return results

    def _failed(self, lead_id: str, exc: Exception, stats: CycleStats) -> None:
        stats.failed += 1
        self._failures[lead_id] += 1
        attempts = self._failures[lead_id]
        if attempts >= self.max_attempts:
            log.error("Lead %s failed %d times (%s); skipping it until restart", lead_id, attempts, exc)
        else:
            log.warning("Lead %s failed (attempt %d/%d): %s", lead_id, attempts, self.max_attempts, exc)

    def _record(self, lead: dict[str, Any], result: ScoreResult, inference_ms: float) -> None:
        self._failures.pop(lead["id"], None)
        now = datetime.now(timezone.utc)
        created = _parse_time(lead.get("created_at"))
        latency = (now - created).total_seconds() if created else None
        transport = getattr(self.crm, "last_transport", None) or self.crm.name
        log.info(
            "Scored lead %s: %.3f %s via %s%s | %s",
            lead["id"], result.score, result.bucket, transport,
            f", {latency:.2f}s after creation" if latency is not None else "",
            "; ".join(result.top_factors),
        )
        if self.metrics_path:
            row = {
                "lead_id": lead["id"],
                "score": result.score,
                "bucket": result.bucket,
                "model_version": result.model_version,
                "transport": transport,
                "created_at": lead.get("created_at"),
                "scored_at": now.isoformat(),
                "creation_to_score_sec": latency,
                "inference_ms": round(inference_ms, 2),
            }
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.metrics_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        poll_interval: float = 2.0,
        max_backoff: float = 30.0,
        stop: asyncio.Event | None = None,
        wakeup: asyncio.Event | None = None,
    ) -> None:
        """Poll until `stop` is set. Backs off exponentially while the CRM is
        unreachable and never dies on a single bad cycle."""
        stop = stop or asyncio.Event()
        backoff = poll_interval
        log.info("Agent A running: model %s, polling every %.1fs via %s",
                 self.scorer.version, poll_interval, self.crm.name)
        while not stop.is_set():
            delay = poll_interval
            try:
                stats = await self.run_once()
            except CRMUnavailableError as exc:
                log.warning("CRM unreachable (%s); retrying in %.0fs", exc, backoff)
                delay, backoff = backoff, min(backoff * 2, max_backoff)
            except Exception:
                log.exception("Unexpected error in scoring cycle; continuing")
            else:
                backoff = poll_interval
                if stats.fetched:
                    log.info("Cycle: %d fetched, %d scored, %d failed, %d skipped %s",
                             stats.fetched, stats.scored, stats.failed, stats.skipped, dict(stats.buckets))
                if stats.fetched >= self.batch_size:
                    continue  # a backlog: go again straight away
            await _sleep(delay, stop, wakeup)
        log.info("Agent A stopped")


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


async def listen_for_new_leads(ws_url: str, wakeup: asyncio.Event, *, max_backoff: float = 30.0) -> None:
    """Set `wakeup` whenever the backend broadcasts lead_created. Reconnects
    forever; cancel the task to stop it."""
    import websockets

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(ws_url) as socket:
                log.info("Listening for new leads on %s", ws_url)
                backoff = 1.0
                async for raw in socket:
                    try:
                        event = json.loads(raw).get("event")
                    except (ValueError, AttributeError):
                        continue
                    if event == "lead_created":
                        wakeup.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("WebSocket %s unavailable (%r); polling continues", ws_url, exc)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)
