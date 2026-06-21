"""
Intake Agent — Parses uploaded invoice/receipt images using Gemini's native
vision capabilities, then saves the extracted data to Supabase via the
MCP server's create_invoice tool.

How vision parsing works here:
  gemini-2.5-flash is natively multimodal. When a user attaches an image
  in the ADK web UI, it arrives as an inline image part in the
  conversation. The model "sees" it directly in context — no separate
  OCR step needed. This agent's instruction prompt tells it what fields
  to extract, then it calls create_invoice() to persist the result.

Least privilege: intake_agent can ONLY create new invoices. It cannot
read, match, or flag existing ones — that's reconciliation_agent's job.
"""

import os
import sys

from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams
from mcp import StdioServerParameters

_VENV_PYTHON = sys.executable
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER_PATH = os.path.join(_PROJECT_ROOT, "mcp_server", "server.py")

intake_mcp = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=_VENV_PYTHON,
            args=[_SERVER_PATH],
        ),
        timeout=10.0,
    ),
    # Least-privilege: intake_agent can only create new invoice records
    tool_filter=["create_invoice"],
)

intake_agent = Agent(
    name="intake_agent",
    model="gemini-2.5-flash",
    description=(
        "Parses uploaded invoices and receipts (images). Extracts "
        "structured invoice data using vision, then saves it to the ledger."
    ),
    instruction=(
        "You are the Intake Agent for LedgerOps.\n\n"
        "When the user attaches an image of an invoice or receipt, examine "
        "it carefully and extract these fields:\n"
        "  - vendor_name: the company/person being paid (required)\n"
        "  - invoice_number: the invoice/receipt number if visible (optional)\n"
        "  - amount: the total amount due, as a plain number, no currency "
        "symbols (required)\n"
        "  - due_date: the payment due date in YYYY-MM-DD format. If only an "
        "issue date is visible with no explicit due date, use the issue date. "
        "If genuinely no date is visible, ask the user for it instead of "
        "guessing (required)\n\n"
        "WORKFLOW:\n"
        "1. Look at the attached image and extract the fields above.\n"
        "2. If any required field is unclear or missing, ask the user to "
        "clarify BEFORE calling any tool — never guess at amounts or dates.\n"
        "3. Once you have all required fields, call create_invoice() with "
        "them. This saves the invoice with status 'pending' so "
        "reconciliation_agent can later match it.\n"
        "4. Confirm to the user what was extracted and saved, including the "
        "new invoice's ID.\n\n"
        "Be precise with amounts — extract exactly what's printed, don't "
        "round or estimate."
    ),
    tools=[intake_mcp],
)