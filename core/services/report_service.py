"""Thin service layer for report generation.

UI calls get_report() or get_positions_report() only.
No computations happen in UI.
"""
from enum import Enum
from typing import Optional, Set

from core.dto.reporting import TableReport, TimeSeriesReport
from core.reports.cashflow import cashflow_report
from core.reports.netto_invested import netto_invested_report
from core.reports.positions import positions_report

_FIAT_DEFAULT = frozenset({"EUR", "CZK"})


class ReportKind(str, Enum):
    CASHFLOW       = "cashflow"
    NETTO_INVESTED = "netto_invested"
    POSITIONS      = "positions"


def get_report(
    rows: list,
    kind: ReportKind,
    bucket: str = "month",
    fiat: Optional[Set[str]] = None,
) -> TimeSeriesReport:
    """Dispatch to the appropriate report function.

    Args:
        rows:   RawRow list from svc.timeline()
        kind:   ReportKind.CASHFLOW or ReportKind.NETTO_INVESTED
        bucket: "day", "week", or "month"
        fiat:   Fiat asset set (default: {"EUR", "CZK"})

    Returns:
        TimeSeriesReport DTO
    """
    if fiat is None:
        fiat = set(_FIAT_DEFAULT)
    if kind == ReportKind.CASHFLOW:
        return cashflow_report(rows, bucket=bucket, fiat=fiat)
    if kind == ReportKind.NETTO_INVESTED:
        return netto_invested_report(rows, bucket=bucket, fiat=fiat)
    raise ValueError(f"Unknown ReportKind: {kind!r}")


def get_positions_report(
    rows: list,
    fiat: Optional[Set[str]] = None,
) -> TableReport:
    """Return a TableReport snapshot of WAC positions.

    Args:
        rows: RawRow list from svc.timeline()
        fiat: Fiat asset set (default: {"EUR", "CZK"})

    Returns:
        TableReport DTO with one TableRow per non-fiat asset.
    """
    _fiat = frozenset(a.upper() for a in (fiat or _FIAT_DEFAULT))
    return positions_report(rows, fiat=_fiat)
