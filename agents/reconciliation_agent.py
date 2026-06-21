"""
Reconciliation Agent — Matches invoices against ledger transactions via MCP.

This agent connects to the LedgerOps MCP server (mcp_server/server.py) as a
subprocess and uses four filtered tools to perform invoice reconciliation:
  • get_pending_invoices       — fetch all pending invoices
  • get_ledger_transactions    — fetch transactions in a date range
  • match_invoice_to_transaction — mark an invoice as matched
  • flag_invoice_mismatch      — flag an invoice with a reason

How McpToolset works:
  McpToolset is ADK's bridge to any MCP server.  You give it connection
  parameters (stdio, SSE, or HTTP) and it:
    1. Spawns/connects to the MCP server process
    2. Discovers all available tools via the MCP protocol
    3. Wraps each tool as an ADK-compatible tool the LLM can call
    4. Handles serialisation, validation, and lifecycle automatically

  When you pass an McpToolset instance in the agent's `tools=[]` list, ADK
  lazily connects to the MCP server on first use and keeps the connection
  alive for the duration of the session.

How tool_filter works:
  By default McpToolset exposes *every* tool the MCP server offers.  The
  `tool_filter` parameter lets you restrict access to a specific subset —
  this enforces the **principle of least privilege** so each agent can only
  call the tools it actually needs.

  You can pass either:
    • A list of tool name strings:  tool_filter=["tool_a", "tool_b"]
    • A callable predicate:         tool_filter=lambda tool: tool.name.startswith("read_")

Lifecycle / exit_stack pattern:
  McpToolset manages the MCP subprocess lifecycle internally.  When passed
  directly in `tools=[...]`, ADK's runner handles startup and teardown
  automatically through its own exit_stack.  This is the recommended pattern
  for `adk web` and production deployment — no manual async cleanup needed.

IMPORTANT — venv Python path fix:
  We must point `command` at THIS project's venv Python executable
  (sys.executable), not the bare string "python". If we use "python", the
  subprocess resolves to whatever "python" means on the system PATH, which
  may not have fastmcp/supabase installed — causing the MCP server to fail
  silently and the agent to see zero tools. Using sys.executable guarantees
  the subprocess runs in the exact same environment as `adk web` itself.
"""

import os
import sys

from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams
from mcp import StdioServerParameters

# ---------------------------------------------------------------------------
# Resolve absolute paths so this works regardless of the working directory
# `adk web` was launched from.
# ---------------------------------------------------------------------------
_VENV_PYTHON = sys.executable
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER_PATH = os.path.join(_PROJECT_ROOT, "mcp_server", "server.py")

# ---------------------------------------------------------------------------
# MCP connection to the LedgerOps server
# ---------------------------------------------------------------------------
# StdioConnectionParams tells ADK to spawn the MCP server as a child process.
#
#   command / args  — the command line to start the server
#                     (equivalent to: <venv_python> mcp_server/server.py)
#   timeout         — seconds to wait for the server to be ready (default 5).
#                     We use 10s to allow for cold-start of the Supabase client.
#
# tool_filter restricts this agent to only the 4 reconciliation-related tools.
# The MCP server also exposes get_overdue_invoices, but this agent should NOT
# have access to it — that belongs to the collections_agent (least privilege).
# ---------------------------------------------------------------------------

reconciliation_mcp = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=_VENV_PYTHON,
            args=[_SERVER_PATH],
        ),
        timeout=10.0,
    ),
    # Least-privilege: only expose the 4 tools this agent needs
    tool_filter=[
        "get_pending_invoices",
        "get_ledger_transactions",
        "match_invoice_to_transaction",
        "flag_invoice_mismatch",
    ],
)

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

reconciliation_agent = Agent(
    name="reconciliation_agent",
    model="gemini-2.5-flash",
    description=(
        "Matches parsed invoices against ledger transactions stored in "
        "Supabase. Identifies discrepancies, duplicates, and missing payments."
    ),
    instruction=(
        "You are the Reconciliation Agent for LedgerOps.\n\n"
        "You have access to these tools via the MCP server:\n"
        "  • get_pending_invoices() — returns all invoices with status 'pending'\n"
        "  • get_ledger_transactions(start_date, end_date) — returns ledger "
        "transactions in a YYYY-MM-DD date range\n"
        "  • match_invoice_to_transaction(invoice_id, transaction_id) — marks "
        "an invoice as 'matched' and links it to a transaction\n"
        "  • flag_invoice_mismatch(invoice_id, reason) — flags an invoice as "
        "'mismatched' with an explanation\n\n"
        "RECONCILIATION WORKFLOW:\n"
        "1. Call get_pending_invoices() to retrieve all pending invoices.\n"
        "2. For each pending invoice, call get_ledger_transactions() with a "
        "relevant date range (e.g., ±7 days around the invoice date) to find "
        "candidate matches.\n"
        "3. Compare invoice amounts, vendors, and dates against the "
        "transactions returned.\n"
        "4. If a confident match is found, call "
        "match_invoice_to_transaction(invoice_id, transaction_id).\n"
        "5. If no match or a discrepancy is found, call "
        "flag_invoice_mismatch(invoice_id, reason) with a clear explanation "
        "(e.g., 'Amount differs: invoice $500 vs transaction $450').\n"
        "6. Summarise the reconciliation results for the user.\n\n"
        "Always explain your reasoning for each match or mismatch."
    ),
    # Pass the McpToolset instance — ADK handles lifecycle automatically
    tools=[reconciliation_mcp],
)