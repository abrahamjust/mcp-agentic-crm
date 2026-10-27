"""
How Agent A talks to the CRM.

    MCPCRMClient       primary: the MCP tool layer (Role 1)
    RESTCRMClient      fallback: the backend's REST API (Role 2)
    FallbackCRMClient  MCP first, REST when MCP is unreachable (design doc 5.3)
"""
from lead_scoring.crm.base import (
    CRMClient,
    CRMError,
    CRMRequestError,
    CRMUnavailableError,
    LeadNotFoundError,
)
from lead_scoring.crm.fallback import FallbackCRMClient
from lead_scoring.crm.mcp_client import MCPCRMClient, ToolNames
from lead_scoring.crm.rest import RESTCRMClient

__all__ = [
    "CRMClient",
    "CRMError",
    "CRMRequestError",
    "CRMUnavailableError",
    "FallbackCRMClient",
    "LeadNotFoundError",
    "MCPCRMClient",
    "RESTCRMClient",
    "ToolNames",
]
