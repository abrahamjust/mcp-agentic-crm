"""
How Agent B talks to the CRM.

    MCPCRMClient       primary: the MCP tool layer (Role 1)
    RESTCRMClient      fallback: the backend's REST API (Role 2)
    FallbackCRMClient  MCP first, REST when MCP is unreachable (design doc 5.3)
"""
from followup.crm.base import (
    CRMClient,
    CRMError,
    CRMRequestError,
    CRMUnavailableError,
    LeadNotFoundError,
    ToolMissingError,
)
from followup.crm.fallback import FallbackCRMClient
from followup.crm.mcp_client import MCPCRMClient, ToolNames
from followup.crm.rest import RESTCRMClient

__all__ = [
    "CRMClient",
    "CRMError",
    "CRMRequestError",
    "CRMUnavailableError",
    "FallbackCRMClient",
    "LeadNotFoundError",
    "MCPCRMClient",
    "RESTCRMClient",
    "ToolMissingError",
    "ToolNames",
]
