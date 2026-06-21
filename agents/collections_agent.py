"""
Collections Agent — Drafts follow-up emails for overdue invoices via MCP.

This agent connects to the LedgerOps MCP server (mcp_server/server.py) as a
subprocess, restricted via tool_filter to ONLY one tool:
  • get_overdue_invoices — fetch invoices past due_date, still pending

Least privilege: this agent can only READ overdue invoices. It has no
access to match_invoice_to_transaction or flag_invoice_mismatch — those
belong exclusively to reconciliation_agent. collections_agent never writes
to the database, it only drafts emails for a human to review and send.
"""

import os
import sys

from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams
from mcp import StdioServerParameters

# ---------------------------------------------------------------------------
# Resolve absolute paths so this works regardless of working directory.
# Use sys.executable (this venv's Python) so the MCP server subprocess has
# access to fastmcp/supabase — using bare "python" would resolve to
# whatever's on system PATH, which may lack these packages.
# ---------------------------------------------------------------------------
_VENV_PYTHON = sys.executable
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER_PATH = os.path.join(_PROJECT_ROOT, "mcp_server", "server.py")

# ---------------------------------------------------------------------------
# MCP connection — same server as reconciliation_agent, different tool_filter
# ---------------------------------------------------------------------------
collections_mcp = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=_VENV_PYTHON,
            args=[_SERVER_PATH],
        ),
        timeout=10.0,
    ),
    # Least-privilege: read-only access to overdue invoices, nothing else
    tool_filter=["get_overdue_invoices"],
)

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------
collections_agent = Agent(
    name="collections_agent",
    model="gemini-2.5-flash",
    description=(
        "Drafts and sends follow-up emails for overdue or mismatched "
        "invoices. Adjusts tone based on urgency and days overdue."
    ),
    instruction=(
        "You are the Collections Agent for LedgerOps.\n\n"
        "You have access to ONE tool:\n"
        "  • get_overdue_invoices() — returns invoices past their due date "
        "that are still 'pending'\n\n"
        "WORKFLOW:\n"
        "1. Call get_overdue_invoices() to retrieve all overdue invoices.\n"
        "2. If there are none, tell the user there's nothing to follow up on.\n"
        "3. For each overdue invoice, draft a polite-but-firm follow-up "
        "email. Include:\n"
        "   - Vendor/recipient name\n"
        "   - Invoice number and amount\n"
        "   - Original due date and how many days overdue it is\n"
        "   - A clear, courteous request for payment or status update\n"
        "4. Adjust tone based on days overdue:\n"
        "   - 1-7 days: friendly reminder\n"
        "   - 8-30 days: firmer, mention payment terms\n"
        "   - 30+ days: formal, mention potential next steps\n"
        "5. You DO NOT have the ability to send emails or modify invoice "
        "records — present drafted emails to the user for their review "
        "and manual sending.\n\n"
        "Always be professional. Never threaten or use aggressive language, "
        "regardless of how overdue an invoice is."
    ),
    tools=[collections_mcp],
)