"""Shared DTOs for time-series reports.

All reports return TimeSeriesReport so the UI can render any report
without understanding domain logic.

Layout:
    TimeSeriesReport
      ├── meta: ReportMeta   (bucket, fiat set used)
      ├── rows: list[TimeSeriesRow]   (sorted by date, then currency)
      └── totals: dict[currency, dict[metric, Decimal]] | None
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, FrozenSet, List, Literal, Optional

# Allowed time bucket granularities
Bucket = Literal["day", "week", "month"]


@dataclass
class ReportMeta:
    bucket: str           # "day", "week", or "month"
    fiat: FrozenSet[str]  # fiat asset set used (e.g. frozenset({"EUR","CZK"}))


@dataclass
class TimeSeriesRow:
    date: str             # bucket key: YYYY-MM-DD | YYYY-Www | YYYY-MM
    currency: str         # fiat asset (EUR, CZK, ...)
    values: Dict[str, Decimal]   # metric name → value


@dataclass
class TimeSeriesReport:
    meta: ReportMeta
    rows: List[TimeSeriesRow]
    totals: Optional[Dict[str, Dict[str, Decimal]]] = field(default=None)
