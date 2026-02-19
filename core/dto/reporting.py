"""Shared DTOs for time-series and snapshot reports.

All time-series reports return TimeSeriesReport so the UI can render
any report without understanding domain logic.

Snapshot (table) reports return TableReport.

Layout:
    TimeSeriesReport
      ├── meta: ReportMeta   (bucket, fiat set used, kind)
      ├── rows: list[TimeSeriesRow]   (sorted by date, then currency)
      └── totals: dict[currency, dict[metric, Decimal]] | None

    TableReport
      ├── meta: ReportMeta   (bucket="snapshot", kind="snapshot")
      ├── rows: list[TableRow]   (sorted by key)
      └── totals: dict[str, Any] | None
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, FrozenSet, List, Literal, Optional

# Allowed time bucket granularities
Bucket = Literal["day", "week", "month"]


@dataclass
class ReportMeta:
    bucket: str           # "day", "week", "month", or "snapshot"
    fiat: FrozenSet[str]  # fiat asset set used (e.g. frozenset({"EUR","CZK"}))
    kind: str = "timeseries"  # "timeseries" | "snapshot"


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


# ── Snapshot / table reports ──────────────────────────────────────────────────

@dataclass
class TableRow:
    key: str                  # e.g. asset symbol "BTC"
    values: Dict[str, Any]    # metric_name → value (Decimal for numeric fields)


@dataclass
class TableReport:
    meta: ReportMeta
    rows: List[TableRow]
    totals: Optional[Dict[str, Any]] = field(default=None)
