"""Netto invested report: gross fiat outflows and net flow per bucket.

Derived exclusively from cashflow(). The fiat leg rules and bucketing
logic live there; this module only aggregates the results further.

Strategy:
  1. Call cashflow(rows, bucket="day") to get per-day net amounts.
  2. Re-aggregate those daily rows into the requested bucket.
  3. Split into invested (outflows) and inflow (inflows) separately,
     so that BUY and SELL in the same requested bucket are not netted
     against each other before accounting.

  Example (bucket="month", Jan):
    Jan 10  EUR -10 000   → daily cashflow row (BUY outflow)
    Jan 15  EUR  +5 000   → daily cashflow row (SELL inflow)
    → invested_amount = 10 000
    → inflow_amount   =  5 000
    → net_flow        = -5 000
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Set

from core.reports.cashflow import cashflow as _cashflow, FIAT_DEFAULT


@dataclass
class NettoInvestedRow:
    date: str               # bucket key: YYYY-MM-DD | YYYY-Www | YYYY-MM
    currency: str           # fiat asset (EUR, CZK, ...)
    invested_amount: Decimal  # gross outflows, always >= 0
    inflow_amount: Decimal    # gross inflows,  always >= 0
    net_flow: Decimal         # inflow - invested (negative = net spent)


def _rebucket(date_str: str, bucket: str) -> str:
    """Convert YYYY-MM-DD date string to the requested bucket key."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if bucket == "day":
        return date_str
    if bucket == "week":
        return dt.strftime("%Y-W%V")
    if bucket == "month":
        return dt.strftime("%Y-%m")
    raise ValueError(f"Neznámý bucket: {bucket!r}. Povolené: 'day', 'week', 'month'.")


def netto_invested(
    rows: list,
    bucket: str = "day",
    fiat: Optional[Set[str]] = None,
) -> List[NettoInvestedRow]:
    """Vrátí hrubé fiat výdaje a příjmy agregované po časových bucketech.

    Args:
        rows:   Záznamy z ledgeru (ze svc.timeline()).
        bucket: Granularita výstupu: "day", "week", "month".
        fiat:   Množina fiat assetů (default: {"EUR", "CZK"}).

    Returns:
        Seřazený seznam NettoInvestedRow.
        Řádky s invested_amount == 0 a inflow_amount == 0 jsou vynechány.
    """
    if fiat is None:
        fiat = FIAT_DEFAULT

    # Daily cashflow as the atomic base; prevents BUY+SELL same-month netting
    daily = _cashflow(rows, bucket="day", fiat=fiat)

    invested: dict = defaultdict(Decimal)
    inflow:   dict = defaultdict(Decimal)

    for cf in daily:
        key = (_rebucket(cf.date, bucket), cf.currency)
        if cf.net_amount < 0:
            invested[key] += -cf.net_amount   # store as positive
        else:
            inflow[key] += cf.net_amount

    all_keys = set(invested.keys()) | set(inflow.keys())
    result = [
        NettoInvestedRow(
            date=k[0],
            currency=k[1],
            invested_amount=invested.get(k, Decimal("0")),
            inflow_amount=inflow.get(k, Decimal("0")),
            net_flow=inflow.get(k, Decimal("0")) - invested.get(k, Decimal("0")),
        )
        for k in all_keys
    ]
    result.sort(key=lambda r: (r.date, r.currency))
    return result
