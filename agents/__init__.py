"""
LedgerOps — agents package.

This __init__.py exposes `root_agent` at the package level so that the
ADK dev server (`adk web`) can auto-discover it.

How `adk web` discovery works:
  1. ADK looks for a Python package (directory with __init__.py) in the
     current working directory.
  2. Inside that package it looks for a module-level variable named
     `root_agent`.
  3. It registers that agent (and all its sub_agents, recursively) in the
     web UI.

By re-exporting `root_agent` here, running `adk web` from the project root
(the parent of this `agents/` directory) will find and register all 4 agents:
  • ledger_orchestrator  (root)
  • intake_agent
  • reconciliation_agent
  • collections_agent
"""

# Re-export root_agent from the orchestrator module
from agents.agent import root_agent

# This list controls what `from agents import *` exposes — good practice,
# but `adk web` only needs `root_agent` to exist at this scope.
__all__ = ["root_agent"]
