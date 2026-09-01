"""Data Health Check service: ledger integrity scanner.

Scans canonical RawRow ledger rows and reports structural problems as a
TableReport DTO.  No computations are left to the UI — the UI only renders
the returned report.

V1 checks:
  1. missing_quote_leg      – investment leg without a fiat quote leg   (ERROR)
  2. missing_investment_leg – fiat quote leg without a non-fiat leg    (ERROR)
  3. multi_fiat_quote        – trade has quote legs in >1 fiat currency (WARNING)
  4. fiat_as_investment_leg  – fiat asset appears on the investment side (ERROR)
  5. zero_amount             – any row with amount == 0                 (WARNING)
  6. missing_timestamp       – row without a valid timestamp            (ERROR)
  7. oversell                – SELL/REVERSAL exceeds held position      (ERROR)

Output ordering:
  severity (error first), kind, trade_id, asset, timestamp  — fully deterministic.

Usage:
    from core.services.health_service import health_report
    report = health_report(svc.timeline())
    # report is a TableReport; each row is one issue
    # TableRow.key  = "ISSUE_0001", "ISSUE_0002", …
    # TableRow.values keys: severity, kind, trade_id, asset, timestamp, message, hint
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, FrozenSet, List, Optional, Set

from core.dto.reporting import ReportMeta, TableReport, TableRow
from core.model import RawRow
from core.reports.positions import compute_positions

# Severity labels
ERROR   = "error"
WARNING = "warning"

# Severity sort order (lower = earlier)
_SEV_ORDER: Dict[str, int] = {ERROR: 0, WARNING: 1}

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})
_INVESTMENT_TYPES = frozenset({"BUY", "SELL", "REVERSAL"})

# Stablecoins that were historically misclassified as fiat in trade_service.
# Used by check 8 (stablecoin_quote_legacy) — a WARNING-only diagnostic.
_STABLECOIN_ASSETS: FrozenSet[str] = frozenset({"USDC", "USDT"})


def _ts_str(row: RawRow) -> str:
    """Return ISO timestamp string or empty string if unavailable."""
    ts = getattr(row, "timestamp", None)
    if ts is None:
        return ""
    return ts.isoformat()


def _issue(
    severity: str,
    kind: str,
    trade_id: str = "",
    asset: str = "",
    timestamp: str = "",
    message: str = "",
    hint: str = "",
) -> Dict[str, Any]:
    return {
        "severity":  severity,
        "kind":      kind,
        "trade_id":  trade_id,
        "asset":     asset,
        "timestamp": timestamp,
        "message":   message,
        "hint":      hint,
    }


def health_report(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
) -> TableReport:
    """Scan ledger rows and return a TableReport of integrity issues.

    Args:
        rows: All ledger rows (typically from svc.timeline()).
        fiat: Fiat asset set (default: {"EUR", "CZK"}).

    Returns:
        TableReport where each TableRow represents one issue.
        TableRow.key  : "ISSUE_0001", "ISSUE_0002", …
        TableRow.values: dict with keys:
            severity  – "error" | "warning"
            kind      – machine-readable issue type
            trade_id  – relevant trade id or ""
            asset     – relevant asset or ""
            timestamp – ISO timestamp string or ""
            message   – human-readable description
            hint      – suggested fix
        meta.kind == "snapshot", meta.bucket == "snapshot"
    """
    fiat_set: FrozenSet[str] = frozenset(
        a.upper() for a in (fiat or _FIAT_DEFAULT)
    )

    issues: List[Dict[str, Any]] = []

    # ── Check 6: Missing timestamp ────────────────────────────────────────────
    for row in rows:
        if getattr(row, "timestamp", None) is None:
            issues.append(_issue(
                severity=ERROR,
                kind="missing_timestamp",
                trade_id=row.id or "",
                asset=row.asset or "",
                timestamp="",
                message=f"Row for asset={row.asset!r} in trade {row.id!r} has no timestamp.",
                hint="Set a valid ISO 8601 timestamp on this row.",
            ))

    # ── Check 5: Zero amount ──────────────────────────────────────────────────
    for row in rows:
        if row.amount == Decimal("0"):
            issues.append(_issue(
                severity=WARNING,
                kind="zero_amount",
                trade_id=row.id or "",
                asset=row.asset or "",
                timestamp=_ts_str(row),
                message=(
                    f"Row for asset={row.asset!r} in trade {row.id!r} "
                    f"has amount=0 (type={row.type!r})."
                ),
                hint="Zero-amount rows have no effect; verify this is intentional.",
            ))

    # ── Group rows by trade_id (only investment-type rows) ────────────────────
    groups: Dict[str, List[RawRow]] = defaultdict(list)
    for row in rows:
        if row.type in _INVESTMENT_TYPES:
            groups[row.id or ""].append(row)

    # FEE rows indexed by trade id — used to recognise a fee-currency correction:
    # a REVERSAL leg that exactly cancels a same-id FEE of the same asset.
    _fees_by_trade: Dict[str, List[RawRow]] = defaultdict(list)
    for row in rows:
        if row.type == "FEE":
            _fees_by_trade[row.id or ""].append(row)

    def _is_valid_swap(non_fiat_rows: list) -> bool:
        """Return True iff the rows form a valid 1:1 crypto↔crypto swap.

        Conditions (all must hold):
          - at least one valid outflow leg: SELL amount<0 or REVERSAL amount<0
          - at least one valid inflow leg:  BUY  amount>0 or REVERSAL amount>0
          - exactly 1 unique outflow asset
          - exactly 1 unique inflow asset
          - outflow asset != inflow asset
        FEE rows are never present here (excluded by _INVESTMENT_TYPES grouping).
        Multi-leg / routing patterns (>1 asset on either side) intentionally fail.
        """
        outflow_legs = [
            r for r in non_fiat_rows
            if (r.type == "SELL"     and r.amount < 0)
            or (r.type == "REVERSAL" and r.amount < 0)
        ]
        inflow_legs = [
            r for r in non_fiat_rows
            if (r.type == "BUY"      and r.amount > 0)
            or (r.type == "REVERSAL" and r.amount > 0)
        ]

        if not outflow_legs or not inflow_legs:
            return False

        outflow_assets = {r.asset.upper() for r in outflow_legs}
        inflow_assets  = {r.asset.upper() for r in inflow_legs}

        if len(outflow_assets) != 1 or len(inflow_assets) != 1:
            return False

        return outflow_assets.isdisjoint(inflow_assets)

    for trade_id, trade_rows in sorted(groups.items()):
        # ── Fully-reversed trade: every asset in this trade group nets exactly
        #    to Decimal("0") (BUY + matching REVERSAL).  Such a trade has no
        #    economic substance, so the structural quote-leg checks 1–4 below
        #    do not apply.  Other checks (5–8) run over all rows regardless.
        _net: Dict[str, Decimal] = defaultdict(Decimal)
        for _r in trade_rows:
            _net[_r.asset.upper()] += _r.amount
        if trade_rows and all(v == Decimal("0") for v in _net.values()):
            continue

        # ── Fee-currency correction: a REVERSAL leg that exactly cancels a
        #    same-id FEE of the same asset (equal magnitude, opposite sign) is a
        #    correction of a mis-booked fee, not an investment leg.  Drop such
        #    legs before the structural checks so they raise no missing_quote_leg.
        #    A lone REVERSAL with no matching same-id FEE is left untouched.
        _fee_avail = [
            (f.asset.upper(), f.amount) for f in _fees_by_trade.get(trade_id, [])
        ]
        _corrective_idx = set()
        for _i, _r in enumerate(trade_rows):
            if _r.type == "REVERSAL" and (_r.asset.upper(), -_r.amount) in _fee_avail:
                _fee_avail.remove((_r.asset.upper(), -_r.amount))
                _corrective_idx.add(_i)
        effective_rows = [
            r for i, r in enumerate(trade_rows) if i not in _corrective_idx
        ]

        fiat_rows     = [r for r in effective_rows if r.asset.upper() in fiat_set]
        non_fiat_rows = [r for r in effective_rows if r.asset.upper() not in fiat_set]

        # ── Check 1: Missing quote leg ────────────────────────────────────────
        if non_fiat_rows and not fiat_rows and not _is_valid_swap(non_fiat_rows):
            first = non_fiat_rows[0]
            issues.append(_issue(
                severity=ERROR,
                kind="missing_quote_leg",
                trade_id=trade_id,
                asset=first.asset,
                timestamp=_ts_str(first),
                message=(
                    f"Trade {trade_id!r} has a non-fiat investment leg "
                    f"({first.asset}, type={first.type!r}) but no fiat quote leg."
                ),
                hint=(
                    f"Add a fiat quote row ({'/'.join(sorted(fiat_set))}) "
                    f"with trade_id={trade_id!r}."
                ),
            ))

        # ── Check 2: Missing investment leg ───────────────────────────────────
        if fiat_rows and not non_fiat_rows:
            first = fiat_rows[0]
            issues.append(_issue(
                severity=ERROR,
                kind="missing_investment_leg",
                trade_id=trade_id,
                asset=first.asset,
                timestamp=_ts_str(first),
                message=(
                    f"Trade {trade_id!r} has a fiat quote leg ({first.asset}) "
                    f"but no non-fiat investment leg."
                ),
                hint=(
                    f"Add the non-fiat asset row (e.g. BTC) "
                    f"with trade_id={trade_id!r}."
                ),
            ))

        # ── Check 3: Multiple fiat currencies in same trade ───────────────────
        fiat_currencies = {r.asset.upper() for r in fiat_rows}
        if len(fiat_currencies) > 1:
            first = fiat_rows[0]
            listed = ", ".join(sorted(fiat_currencies))
            issues.append(_issue(
                severity=WARNING,
                kind="multi_fiat_quote",
                trade_id=trade_id,
                asset=listed,
                timestamp=_ts_str(first),
                message=(
                    f"Trade {trade_id!r} has quote legs in multiple fiat "
                    f"currencies: {listed}."
                ),
                hint="A trade should use exactly one quote currency.",
            ))

        # ── Check 4: Fiat asset used as investment leg ────────────────────────
        # BUY: fiat quote should have amount < 0 (outflow).
        #      If fiat has amount > 0 in a BUY, fiat is the thing being bought.
        # SELL: fiat quote should have amount > 0 (inflow / proceeds).
        #       If fiat has amount < 0 in a SELL, fiat is the thing being sold.
        for row in fiat_rows:
            is_wrong = (
                (row.type == "BUY"  and row.amount > 0) or
                (row.type == "SELL" and row.amount < 0)
            )
            if is_wrong:
                issues.append(_issue(
                    severity=ERROR,
                    kind="fiat_as_investment_leg",
                    trade_id=trade_id,
                    asset=row.asset,
                    timestamp=_ts_str(row),
                    message=(
                        f"Fiat asset {row.asset!r} appears as the investment leg "
                        f"(amount={row.amount}) in {row.type!r} trade {trade_id!r}."
                    ),
                    hint=(
                        "Fiat assets should only be quote legs: "
                        "negative for BUY (outflow), positive for SELL (proceeds)."
                    ),
                ))

    # ── Check 7: Oversell — any position with negative quantity ──────────────
    positions = compute_positions(rows, fiat_set)
    for pos in positions:
        if pos.quantity < Decimal("0"):
            issues.append(_issue(
                severity=ERROR,
                kind="oversell",
                trade_id="",
                asset=pos.asset,
                timestamp="",
                message=(
                    f"Asset {pos.asset!r} has a net quantity of {pos.quantity}: "
                    f"sells exceed buys."
                ),
                hint="Check that no SELL or negative-REVERSAL row exceeds the held position.",
            ))

    # ── Check 8: Stablecoin used as quote currency (legacy rows) ─────────────
    # BUY/SELL rows where row.currency is USDC/USDT indicate the stablecoin
    # was used as fiat quote in a historical trade.  positions.py never treated
    # these as fiat, so the WAC cost basis for such trades is likely zero.
    # WARNING only — no auto-fix.  Repair: REVERSAL + re-entry as SWAP.
    sc_trade_ids: Set[str] = set()
    for row in rows:
        if (
            row.type in ("BUY", "SELL")
            and (row.currency or "").upper() in _STABLECOIN_ASSETS
            and (row.asset or "").upper() not in _STABLECOIN_ASSETS
        ):
            sc_trade_ids.add(row.id or "")

    for sc_id in sorted(sc_trade_ids):
        sc_rows = [r for r in rows if r.id == sc_id]
        first = sc_rows[0] if sc_rows else rows[0]
        non_sc_assets = {
            r.asset.upper() for r in sc_rows
            if r.asset and r.asset.upper() not in _STABLECOIN_ASSETS
            and r.asset.upper() not in fiat_set
        }
        asset_str = ", ".join(sorted(non_sc_assets)) if non_sc_assets else "?"
        sc_used = {
            (r.currency or "").upper() for r in sc_rows
            if (r.currency or "").upper() in _STABLECOIN_ASSETS
        }
        sc_name = "/".join(sorted(sc_used)) if sc_used else "USDC/USDT"
        issues.append(_issue(
            severity=WARNING,
            kind="stablecoin_quote_legacy",
            trade_id=sc_id,
            asset=asset_str,
            timestamp=_ts_str(first),
            message=(
                f"Trade {sc_id!r} used {sc_name} as quote currency "
                f"(asset {asset_str!r}). WAC cost basis may be zero."
            ),
            hint=(
                f"Stablecoins ({sc_name}) are assets, not fiat. "
                "Repair: REVERSAL of this trade + re-entry as SWAP."
            ),
        ))

    # ── Sort: severity, kind, trade_id, asset, timestamp ─────────────────────
    issues.sort(key=lambda i: (
        _SEV_ORDER.get(i["severity"], 99),
        i["kind"],
        i["trade_id"],
        i["asset"],
        i["timestamp"],
    ))

    # ── Build TableReport ─────────────────────────────────────────────────────
    table_rows = [
        TableRow(key=f"ISSUE_{idx:04d}", values=issue)
        for idx, issue in enumerate(issues, 1)
    ]

    meta = ReportMeta(bucket="snapshot", fiat=fiat_set, kind="snapshot")
    return TableReport(meta=meta, rows=table_rows, totals=None)
