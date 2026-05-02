"""Recursive Language Model harness for homelab project agents.

This package is a research spike. See docs/RLM_HARNESS.md for the contract.
"""

from .audit import AuditLog
from .harness import Harness, OrchestrationResult, RootProbeError, BudgetExhausted
from .sandbox import Handle, Sandbox
from .subcall import SubCallInvoker, SubCallResult, SubCallSchemaError

__all__ = [
    "AuditLog",
    "BudgetExhausted",
    "Handle",
    "Harness",
    "OrchestrationResult",
    "RootProbeError",
    "Sandbox",
    "SubCallInvoker",
    "SubCallResult",
    "SubCallSchemaError",
]
