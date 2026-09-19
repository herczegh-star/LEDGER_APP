"""Phase Model B — core/services/health_service.py: Check 11
(PORTFOLIO_CONTRIBUTION/WITHDRAWAL group validity)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.model import RawRow
from core.portfolio_boundary import CONTRIBUTION, build_portfolio_boundary_rows
from core.services.health_service import health_report

_TS = datetime(2026, 9, 19, 12, 0, 0)


def _kinds(report):
    return {row.values["kind"] for row in report.rows}


def test_valid_group_produces_no_boundary_issues():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    kinds = _kinds(health_report([outside, inside]))
    assert "invalid_portfolio_boundary_group" not in kinds


def test_missing_leg_flagged():
    outside, _inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    kinds = _kinds(health_report([outside]))
    assert "invalid_portfolio_boundary_group" in kinds


def test_nonzero_net_flagged():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    inside.amount = Decimal("40000")
    kinds = _kinds(health_report([outside, inside]))
    assert "invalid_portfolio_boundary_group" in kinds


def test_currency_mismatch_flagged():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("100"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    inside.asset = "EUR"
    inside.currency = "EUR"
    kinds = _kinds(health_report([outside, inside]))
    assert "invalid_portfolio_boundary_group" in kinds
