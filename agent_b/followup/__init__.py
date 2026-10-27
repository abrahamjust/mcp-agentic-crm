"""
Agent B (Role 4): consent-gated follow-up automation for the MCP-native
agentic CRM.

For every Hot lead nothing has been done for yet, check marketing consent
(Role 5), then either record a blocked attempt or draft a follow-up email
with an LLM and store it as pending_approval for a person to review. When a
person approves a draft, advance the lead from New to Contacted.
"""
__version__ = "1.0.0"
