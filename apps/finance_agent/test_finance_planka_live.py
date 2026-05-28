"""Optional live Planka smoke for finance defer cards (Alienware / configured host)."""

from __future__ import annotations

import os

import pytest

from apps.finance_agent.categorize.planka import planka_defer_configured
from apps.finance_agent.categorize.planka_smoke import live_smoke_finance_planka


@pytest.mark.live
def test_live_finance_planka_defer_roundtrip() -> None:
    """Create + delete a smoke card on the real finance board."""
    if os.environ.get("FINANCE_PLANKA_LIVE") != "1":
        pytest.skip("set FINANCE_PLANKA_LIVE=1 with agent-finance.env sourced")
    if not planka_defer_configured():
        pytest.skip("Planka defer env not configured")

    result = live_smoke_finance_planka(correlation_id="pytest.live.finance-planka")
    assert result["card_id"]
    assert result["deleted"] is True, result.get("delete_error")
