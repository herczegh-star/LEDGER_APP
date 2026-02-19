"""Cashflow report: net fiat flows aggregated by time bucket.

Only rows where asset is in the fiat set are included.
All types are handled uniformly — the amount sign is the source of truth:
  BUY  fiat leg:  amount < 0  (outflow – you paid fiat)
  SELL fiat leg:  amount > 0  (inflow  – you received fiat)
  FEE  fiat:      amount < 0  (outflow – you paid fee in fiat)
  TRANSFER fiat:  sign preserved (+inflow, -outflow)
  REVERSAL:       opposite sign, naturally cancels the original

Buckets: "day" (YYYY-MM-DD), "week" (YYYY-Www), "month" (YYYY-MM).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import FrozenSet, List, Optional, Set

from core.model import RawRow
from core.dto.reporting import ReportMeta, TimeSeriesRow, TimeSeriesReport

FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})


@dataclass
class CashflowRow:
    date: str       # bucket key: YYYY-MM-DD | YYYY-Www | YYYY-MM
    currency: str   # fiat asset (EUR, CZK, ...)
    net_amount: Decimal


def _bucket_key(ts, bucket: str) -> str:
    if bucket == "day":
        return ts.strftime("%Y-%m-%d")
    if bucket == "week":
        return ts.strftime("%Y-W%V")
    if bucket == "month":
        return ts.strftime("%Y-%m")
    raise ValueError(f"Neznámý bucket: {bucket!r}. Povolené: 'day', 'week', 'month'.")


def cashflow(
    rows: List[RawRow],
    bucket: str = "day",
    fiat: Optional[Set[str]] = None,
) -> List[CashflowRow]:
    """Vrátí čistý fiat tok agregovaný po časových bucketech.

    Args:
        rows:   Záznamy z ledgeru (např. ze svc.timeline()).
        bucket: Granularita: "day", "week", "month".
        fiat:   Množina fiat assetů (default: {"EUR", "CZK"}).

    Returns:
        Seřazený seznam CashflowRow (datum, měna, net_amount).
        Buckety s net_amount == 0 jsou vynechány.
    """
    if fiat is None:
        fiat = FIAT_DEFAULT

    totals: dict = defaultdict(Decimal)
    for row in rows:
        if row.asset not in fiat:
            continue
        key = (_bucket_key(row.timestamp, bucket), row.asset)
        totals[key] += row.amount

    result = [
        CashflowRow(date=k[0], currency=k[1], net_amount=v)
        for k, v in totals.items()
        if v != 0
    ]
    result.sort(key=lambda r: (r.date, r.currency))
    return result


def cashflow_report(
    rows: List[RawRow],
    bucket: str = "day",
    fiat: Optional[Set[str]] = None,
) -> TimeSeriesReport:
    """Same as cashflow(), but returns a TimeSeriesReport DTO.

    Each TimeSeriesRow.values = {"net_amount": Decimal}.
    totals = {currency: {"net_amount": total}} per currency.
    """
    if fiat is None:
        fiat = FIAT_DEFAULT

    cf_rows = cashflow(rows, bucket=bucket, fiat=fiat)

    ts_rows = [
        TimeSeriesRow(date=r.date, currency=r.currency, values={"net_amount": r.net_amount})
        for r in cf_rows
    ]

    totals: dict = {}
    for r in ts_rows:
        cur = r.currency
        if cur not in totals:
            totals[cur] = {"net_amount": Decimal("0")}
        totals[cur]["net_amount"] += r.values["net_amount"]

    return TimeSeriesReport(
        meta=ReportMeta(bucket=bucket, fiat=frozenset(fiat)),
        rows=ts_rows,
        totals=totals if totals else None,
    )
