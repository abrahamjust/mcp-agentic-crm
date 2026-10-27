"""
Agent A command line.

    python -m lead_scoring run              score new leads continuously
    python -m lead_scoring run --once       one cycle, then exit
    python -m lead_scoring score lead.json  score one lead record (or '-' for stdin)
    python -m lead_scoring info             show the loaded model

Every option can also be set by environment variable (shown in --help).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
import signal
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from lead_scoring.agent import ScoringAgent, listen_for_new_leads
from lead_scoring.crm import FallbackCRMClient, MCPCRMClient, RESTCRMClient
from lead_scoring.crm.mcp_client import stdio_server
from lead_scoring.predictor import LeadScorer

log = logging.getLogger("lead_scoring")


def _env(name: str, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _ws_url(api_url: str) -> str:
    parts = urlsplit(api_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + "/ws", "", ""))


def build_crm(args):
    rest = RESTCRMClient(args.api_url, timeout=args.timeout)
    mcp = None
    if args.mcp_url:
        mcp = MCPCRMClient(args.mcp_url, timeout=args.timeout)
    elif args.mcp_command:
        tokens = shlex.split(args.mcp_command, posix=os.name != "nt")
        tokens = [t.strip('"') for t in tokens]
        mcp = MCPCRMClient(stdio_server(tokens[0], tokens[1:]), timeout=args.timeout)

    if args.transport == "rest":
        return rest
    if args.transport == "mcp":
        if mcp is None:
            raise SystemExit("--transport mcp needs --mcp-url or --mcp-command (MCP_SERVER_URL / MCP_SERVER_COMMAND)")
        return mcp
    if mcp is None:
        log.warning("No MCP server configured; using the REST API directly")
        return rest
    return FallbackCRMClient(mcp, rest, retry_primary_after=args.retry_mcp_after)


async def _run(args) -> int:
    scorer = LeadScorer.load(args.model_dir)
    crm = build_crm(args)
    agent = ScoringAgent(crm, scorer, batch_size=args.batch_size, metrics_path=args.metrics_file)
    try:
        if args.once:
            stats = await agent.run_once()
            print(json.dumps({"fetched": stats.fetched, "scored": stats.scored, "failed": stats.failed,
                              "skipped": stats.skipped, "buckets": dict(stats.buckets)}))
            return 0 if stats.failed == 0 else 1

        stop, wakeup = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):  # Windows: Ctrl+C raises KeyboardInterrupt instead
                pass
        listener = None
        if args.ws:
            listener = asyncio.create_task(listen_for_new_leads(_ws_url(args.api_url), wakeup))
        try:
            await agent.run(poll_interval=args.interval, stop=stop, wakeup=wakeup)
        finally:
            if listener:
                listener.cancel()
        return 0
    finally:
        await crm.aclose()


def _score(args) -> int:
    scorer = LeadScorer.load(args.model_dir)
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    payload = json.loads(text)
    leads = payload if isinstance(payload, list) else [payload]
    results = [r.as_dict() for r in scorer.score_many(leads)]
    print(json.dumps(results if isinstance(payload, list) else results[0], indent=2))
    return 0


def _info(args) -> int:
    print(json.dumps(LeadScorer.load(args.model_dir).info(), indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lead_scoring", description="Agent A: ML lead scoring.")
    p.add_argument("--model-dir", default=_env("AGENT_A_MODEL_DIR"),
                   help="model directory (default: models/LATEST) [AGENT_A_MODEL_DIR]")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="score unscored leads continuously")
    run.add_argument("--api-url", default=_env("CRM_API_URL", "http://127.0.0.1:8000"),
                     help="Core CRM backend URL, used for REST fallback [CRM_API_URL]")
    run.add_argument("--mcp-url", default=_env("MCP_SERVER_URL"),
                     help="MCP server URL (Streamable HTTP) [MCP_SERVER_URL]")
    run.add_argument("--mcp-command", default=_env("MCP_SERVER_COMMAND"),
                     help='command that starts the MCP server over stdio, e.g. "python mcp_server/server.py" '
                          "[MCP_SERVER_COMMAND]")
    run.add_argument("--transport", choices=["auto", "mcp", "rest"], default=_env("AGENT_A_TRANSPORT", "auto"),
                     help="auto = MCP with REST fallback when MCP is configured [AGENT_A_TRANSPORT]")
    run.add_argument("--interval", type=float, default=float(_env("AGENT_A_POLL_INTERVAL", 2.0)),
                     help="seconds between polls [AGENT_A_POLL_INTERVAL]")
    run.add_argument("--batch-size", type=int, default=int(_env("AGENT_A_BATCH_SIZE", 50)),
                     help="leads per cycle [AGENT_A_BATCH_SIZE]")
    run.add_argument("--timeout", type=float, default=float(_env("AGENT_A_TIMEOUT", 10.0)),
                     help="per-request timeout in seconds [AGENT_A_TIMEOUT]")
    run.add_argument("--retry-mcp-after", type=float, default=30.0,
                     help="seconds before retrying MCP after it failed")
    run.add_argument("--no-ws", dest="ws", action="store_false",
                     default=_env("AGENT_A_WS", "1") not in ("0", "false", "no"),
                     help="do not listen for lead_created events (poll only) [AGENT_A_WS=0]")
    run.add_argument("--metrics-file", type=Path, default=_env("AGENT_A_METRICS_FILE"),
                     help="append per-lead latency records (JSON lines) [AGENT_A_METRICS_FILE]")
    run.add_argument("--once", action="store_true", help="run one cycle and exit")

    score = sub.add_parser("score", help="score lead record(s) from a JSON file")
    score.add_argument("file", help="JSON lead object or list; '-' for stdin")

    sub.add_parser("info", help="show the loaded model")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpcore", "mcp", "websockets", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.command == "run":
        try:
            return asyncio.run(_run(args))
        except KeyboardInterrupt:
            return 0
    if args.command == "score":
        return _score(args)
    return _info(args)


if __name__ == "__main__":
    sys.exit(main())
