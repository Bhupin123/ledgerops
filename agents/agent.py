"""
LedgerOps Orchestrator — Root agent that coordinates all sub-agents.

This is the "brain" of the multi-agent system.  It receives user requests
and decides which specialist sub-agent to delegate to:

  • intake_agent        → parse uploaded invoices / receipts
  • reconciliation_agent → match invoices against the Supabase ledger
  • collections_agent   → draft follow-up emails for overdue invoices

The orchestrator itself is an LLM-powered Agent (LlmAgent).  ADK's built-in
delegation mechanism works by:
  1. Reading each sub-agent's `description` field.
  2. Letting the LLM decide which sub-agent best handles the current request.
  3. Transferring control to that sub-agent automatically.

The user interacts *only* with this orchestrator; it transparently routes
work to the right specialist.
"""

from google.adk.agents import Agent

# ---------------------------------------------------------------------------
# Import the three specialist sub-agents
# ---------------------------------------------------------------------------
from agents.intake_agent import intake_agent
from agents.reconciliation_agent import reconciliation_agent
from agents.collections_agent import collections_agent

# ---------------------------------------------------------------------------
# Root Orchestrator Agent
# ---------------------------------------------------------------------------
# `sub_agents` tells ADK that this agent can delegate to any of the listed
# agents.  The orchestrator's `instruction` guides the LLM on *when* to
# delegate and *when* to handle a query itself (e.g., general greetings).
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="ledger_orchestrator",
    model="gemini-2.5-flash",
    description="Root orchestrator for the LedgerOps invoice reconciliation system.",
    instruction=(
        "You are the LedgerOps Orchestrator — the central coordinator of a "
        "multi-agent invoice reconciliation system.\n\n"
        "Your responsibilities:\n"
        "1. Greet the user and explain what LedgerOps can do.\n"
        "2. Route requests to the appropriate specialist agent:\n"
        "   • If the user uploads or mentions an invoice/receipt to parse → "
        "delegate to **intake_agent**.\n"
        "   • If the user asks to reconcile invoices against the ledger → "
        "delegate to **reconciliation_agent**.\n"
        "   • If the user asks to send reminders or follow-up emails for "
        "overdue/mismatched invoices → delegate to **collections_agent**.\n"
        "3. For general questions about LedgerOps or invoice processes, "
        "answer directly without delegating.\n\n"
        "Always be clear about which agent is handling the request."
    ),
    # Wire up the three sub-agents for delegation
    sub_agents=[
        intake_agent,
        reconciliation_agent,
        collections_agent,
    ],
)
