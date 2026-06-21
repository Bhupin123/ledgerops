"""
LedgerOps MCP Server — Model Context Protocol tools for invoice reconciliation.

This module creates an MCP server using the FastMCP library.  MCP (Model
Context Protocol) lets AI agents discover and call your tools through a
standardised JSON-RPC interface — think of it as a USB-C port for AI.

Security features implemented:
  • Input validation on every tool (UUID format, date format, amount bounds,
    string length limits) — rejects malformed/malicious input before it
    reaches the database.
  • Audit logging — every write operation (create, match, flag) is recorded
    in ledgerops.audit_log with the acting agent, action, and details. This
    gives a tamper-evident trail of what each agent did and when.
  • Least privilege at the agent layer — each ADK agent's tool_filter only
    exposes the tools it actually needs (see agents/*.py).
  • RLS + explicit role revocation at the database layer — only the
    service_role (used exclusively by this MCP server) can read/write
    ledgerops tables; anon/authenticated roles are explicitly revoked.

IMPORTANT — stdio transport and stdout:
  In stdio mode, stdout is reserved exclusively for the JSON-RPC protocol
  stream. All diagnostic output goes to stderr instead.

IMPORTANT — schema targeting:
  Tables live in `ledgerops`, not `public`. Every query explicitly chains
  .schema("ledgerops") before .table(...).

Run standalone:  python mcp_server/server.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY: str = os.environ.get("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise EnvironmentError(
        "Missing SUPABASE_URL and/or SUPABASE_SERVICE_KEY in environment. "
        "Set them in your .env file."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
mcp = FastMCP("LedgerOps")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_uuid(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    if not _UUID_RE.match(value.strip()):
        raise ValueError(
            f"{field_name} must be a valid UUID (got '{value}'). "
            "Expected format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        )


def _validate_date(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"{field_name} must be a valid date in YYYY-MM-DD format (got '{value}')."
        )


def _log_action(
    agent_name: str,
    action: str,
    invoice_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write an entry to ledgerops.audit_log.

    This is fire-and-forget — if logging fails, we print a warning to
    stderr but DO NOT raise, since a logging failure should never block
    the actual business operation that's already succeeded.
    """
    try:
        supabase.schema("ledgerops").table("audit_log").insert({
            "agent_name": agent_name,
            "action": action,
            "invoice_id": invoice_id,
            "details": details or {},
        }).execute()
    except Exception as exc:  # noqa: BLE001 — intentionally broad, see docstring
        print(f"[audit_log] WARNING: failed to write audit entry: {exc}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_pending_invoices() -> list[dict[str, Any]]:
    """Retrieve all invoices that currently have status 'pending'."""
    response = (
        supabase.schema("ledgerops").table("invoices")
        .select("*").eq("status", "pending").execute()
    )
    return response.data


@mcp.tool()
def get_ledger_transactions(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Retrieve ledger transactions within an inclusive date range.

    Args:
        start_date: Start of the range in YYYY-MM-DD format (inclusive).
        end_date:   End of the range in YYYY-MM-DD format (inclusive).
    """
    start_clean = _validate_date(start_date, "start_date")
    end_clean = _validate_date(end_date, "end_date")
    if start_clean > end_clean:
        raise ValueError(
            f"start_date ({start_clean}) must not be after end_date ({end_clean})."
        )
    response = (
        supabase.schema("ledgerops").table("ledger_transactions")
        .select("*")
        .gte("transaction_date", start_clean)
        .lte("transaction_date", end_clean)
        .execute()
    )
    return response.data


@mcp.tool()
def match_invoice_to_transaction(invoice_id: str, transaction_id: str) -> dict[str, Any]:
    """Mark an invoice as 'matched' and link it to a ledger transaction.

    Args:
        invoice_id:      UUID of the invoice to update.
        transaction_id:  UUID of the matching ledger transaction.
    """
    _validate_uuid(invoice_id, "invoice_id")
    _validate_uuid(transaction_id, "transaction_id")
    response = (
        supabase.schema("ledgerops").table("invoices")
        .update({"status": "matched", "matched_transaction_id": transaction_id.strip()})
        .eq("id", invoice_id.strip()).execute()
    )
    if not response.data:
        raise ValueError(f"No invoice found with id '{invoice_id}'.")

    _log_action(
        agent_name="reconciliation_agent",
        action="match_invoice_to_transaction",
        invoice_id=invoice_id.strip(),
        details={"transaction_id": transaction_id.strip()},
    )
    return response.data[0]


@mcp.tool()
def flag_invoice_mismatch(invoice_id: str, reason: str) -> dict[str, Any]:
    """Flag an invoice as 'mismatched' and record the reason.

    Args:
        invoice_id: UUID of the invoice to flag.
        reason:     Human-readable explanation.
    """
    _validate_uuid(invoice_id, "invoice_id")
    if not reason or not reason.strip():
        raise ValueError("reason must not be empty.")
    if len(reason.strip()) > 1000:
        raise ValueError("reason must be 1000 characters or fewer.")
    response = (
        supabase.schema("ledgerops").table("invoices")
        .update({"status": "mismatched", "mismatch_reason": reason.strip()})
        .eq("id", invoice_id.strip()).execute()
    )
    if not response.data:
        raise ValueError(f"No invoice found with id '{invoice_id}'.")

    _log_action(
        agent_name="reconciliation_agent",
        action="flag_invoice_mismatch",
        invoice_id=invoice_id.strip(),
        details={"reason": reason.strip()},
    )
    return response.data[0]


@mcp.tool()
def get_overdue_invoices() -> list[dict[str, Any]]:
    """Retrieve all invoices that are overdue (past due_date, still 'pending')."""
    today_str = date.today().isoformat()
    response = (
        supabase.schema("ledgerops").table("invoices")
        .select("*").eq("status", "pending").lt("due_date", today_str).execute()
    )
    return response.data


@mcp.tool()
def create_invoice(
    vendor_name: str,
    amount: float,
    due_date: str,
    invoice_number: str = "",
) -> dict[str, Any]:
    """Create a new invoice record with status 'pending'.

    Used by intake_agent after parsing an uploaded invoice/receipt image.

    Args:
        vendor_name:    Name of the vendor/payee. Required, non-empty.
        amount:         Total amount due. Must be a positive number.
        due_date:       Payment due date in YYYY-MM-DD format.
        invoice_number: Invoice/receipt number, if visible. Optional.
    """
    if not vendor_name or not vendor_name.strip():
        raise ValueError("vendor_name must not be empty.")
    if len(vendor_name.strip()) > 200:
        raise ValueError("vendor_name must be 200 characters or fewer.")
    if amount <= 0:
        raise ValueError(f"amount must be a positive number (got {amount}).")
    if amount > 10_000_000:
        raise ValueError(f"amount exceeds maximum allowed value (got {amount}).")

    due_clean = _validate_date(due_date, "due_date")
    invoice_number_clean = invoice_number.strip() if invoice_number else None

    response = (
        supabase.schema("ledgerops").table("invoices")
        .insert({
            "vendor_name": vendor_name.strip(),
            "invoice_number": invoice_number_clean,
            "amount": amount,
            "due_date": due_clean,
            "status": "pending",
        })
        .execute()
    )
    if not response.data:
        raise ValueError("Failed to create invoice — no data returned from Supabase.")

    new_invoice = response.data[0]
    _log_action(
        agent_name="intake_agent",
        action="create_invoice",
        invoice_id=new_invoice["id"],
        details={
            "vendor_name": vendor_name.strip(),
            "amount": amount,
            "due_date": due_clean,
        },
    )
    return new_invoice


if __name__ == "__main__":
    print("Starting LedgerOps MCP Server...", file=sys.stderr)
    print(f"Connected to Supabase: {SUPABASE_URL}", file=sys.stderr)
    print("Schema: ledgerops", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    mcp.run()