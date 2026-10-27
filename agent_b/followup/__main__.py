"""
Agent B command line.

    python -m followup run               act on Hot leads continuously
    python -m followup run --once        one cycle, then exit
    python -m followup draft lead.json   draft an email for a lead record (no CRM; '-' = stdin)
    python -m followup harness           simulated Hot leads, allowed + blocked paths, with checks

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

from followup.agent import STAGE_POLICIES, FollowUpAgent, listen_for_events
from followup.crm import FallbackCRMClient, MCPCRMClient, RESTCRMClient
from followup.crm.mcp_client import stdio_server
from followup.drafting import FallbackDrafter, OllamaDrafter, Sender, TemplateDrafter

log = logging.getLogger("followup")


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


def build_drafter(args):
    sender = Sender(name=args.sender_name, organisation=args.sender_org)
    template = TemplateDrafter(sender)
    if args.llm == "template":
        return template
    llm = OllamaDrafter(url=args.ollama_url, model=args.ollama_model, sender=sender, timeout=args.llm_timeout)
    return FallbackDrafter(llm, template)


async def _close(*things) -> None:
    for thing in things:
        close = getattr(thing, "aclose", None)
        if close:
            await close()


async def _run(args) -> int:
    crm, drafter = build_crm(args), build_drafter(args)
    agent = FollowUpAgent(crm, drafter, batch_size=args.batch_size, stage_policy=args.advance_stage,
                          metrics_path=args.metrics_file)
    try:
        if args.once:
            stats = await agent.run_once()
            print(json.dumps(stats.as_dict()))
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
            listener = asyncio.create_task(listen_for_events(_ws_url(args.api_url), wakeup))
        try:
            await agent.run(poll_interval=args.interval, stop=stop, wakeup=wakeup)
        finally:
            if listener:
                listener.cancel()
        return 0
    finally:
        await _close(crm, drafter)


async def _draft(args) -> int:
    drafter = build_drafter(args)
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        draft = await drafter.draft(json.loads(text))
    finally:
        await _close(drafter)
    print(json.dumps(draft.as_payload(), indent=2, ensure_ascii=False))
    return 0


async def _harness(args) -> int:
    from followup import harness

    drafter = build_drafter(args)
    try:
        if args.api_url:
            report = await harness.run_harness(drafter, api_url=args.api_url)
        else:
            report = await harness.run_harness(drafter, transport=harness.in_process_backend())
    finally:
        await _close(drafter)
    harness.print_summary(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(harness.render_markdown(report), encoding="utf-8")
        args.report.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Report written to {args.report}")
    return 0 if report["passed"] else 1


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--llm", choices=["ollama", "template"], default=_env("AGENT_B_LLM", "ollama"),
                   help="ollama = local LLM with template fallback; template = no LLM [AGENT_B_LLM]")
    p.add_argument("--ollama-url", default=_env("OLLAMA_URL", "http://127.0.0.1:11434"), help="[OLLAMA_URL]")
    p.add_argument("--ollama-model", default=_env("AGENT_B_MODEL", "llama3.2:3b"), help="[AGENT_B_MODEL]")
    p.add_argument("--llm-timeout", type=float, default=float(_env("AGENT_B_LLM_TIMEOUT", 120.0)),
                   help="seconds per LLM request [AGENT_B_LLM_TIMEOUT]")
    p.add_argument("--sender-name", default=_env("AGENT_B_SENDER_NAME", Sender.name),
                   help="sign-off name [AGENT_B_SENDER_NAME]")
    p.add_argument("--sender-org", default=_env("AGENT_B_SENDER_ORG", Sender.organisation),
                   help="sign-off organisation [AGENT_B_SENDER_ORG]")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m followup", description="Agent B: consent-gated follow-up automation.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="act on Hot leads continuously")
    run.add_argument("--api-url", default=_env("CRM_API_URL", "http://127.0.0.1:8000"),
                     help="Core CRM backend URL, used for REST fallback [CRM_API_URL]")
    run.add_argument("--mcp-url", default=_env("MCP_SERVER_URL"),
                     help="MCP server URL (Streamable HTTP) [MCP_SERVER_URL]")
    run.add_argument("--mcp-command", default=_env("MCP_SERVER_COMMAND"),
                     help='command that starts the MCP server over stdio, e.g. "python mcp_server/server.py" '
                          "[MCP_SERVER_COMMAND]")
    run.add_argument("--transport", choices=["auto", "mcp", "rest"], default=_env("AGENT_B_TRANSPORT", "auto"),
                     help="auto = MCP with REST fallback when MCP is configured [AGENT_B_TRANSPORT]")
    run.add_argument("--advance-stage", choices=STAGE_POLICIES, default=_env("AGENT_B_ADVANCE_STAGE", "on_approval"),
                     help="when to move a lead New -> Contacted [AGENT_B_ADVANCE_STAGE]")
    run.add_argument("--interval", type=float, default=float(_env("AGENT_B_POLL_INTERVAL", 3.0)),
                     help="seconds between polls [AGENT_B_POLL_INTERVAL]")
    run.add_argument("--batch-size", type=int, default=int(_env("AGENT_B_BATCH_SIZE", 20)),
                     help="leads per cycle [AGENT_B_BATCH_SIZE]")
    run.add_argument("--timeout", type=float, default=float(_env("AGENT_B_TIMEOUT", 10.0)),
                     help="per-request CRM timeout in seconds [AGENT_B_TIMEOUT]")
    run.add_argument("--retry-mcp-after", type=float, default=30.0,
                     help="seconds before retrying MCP after it failed")
    run.add_argument("--no-ws", dest="ws", action="store_false",
                     default=_env("AGENT_B_WS", "1") not in ("0", "false", "no"),
                     help="do not listen for lead_scored / interaction_updated events (poll only) [AGENT_B_WS=0]")
    run.add_argument("--metrics-file", type=Path, default=_env("AGENT_B_METRICS_FILE"),
                     help="append per-lead outcome and latency records (JSON lines) [AGENT_B_METRICS_FILE]")
    run.add_argument("--once", action="store_true", help="run one cycle and exit")
    _add_llm_args(run)

    draft = sub.add_parser("draft", help="draft a follow-up for a lead record from a JSON file")
    draft.add_argument("file", help="JSON lead object; '-' for stdin")
    _add_llm_args(draft)

    harness = sub.add_parser("harness", help="run the simulated Hot-lead test harness")
    harness.add_argument("--api-url", default=None,
                         help="use a running backend (leads appear on the dashboard); default: in-process, temp DB")
    harness.add_argument("--report", type=Path, default=None,
                         help="write a Markdown report (and .json next to it), e.g. reports/harness_report.md")
    _add_llm_args(harness)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpcore", "mcp", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.command == "harness" and not args.verbose:
        logging.getLogger("followup").setLevel(logging.WARNING)

    commands = {"run": _run, "draft": _draft, "harness": _harness}
    try:
        return asyncio.run(commands[args.command](args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
