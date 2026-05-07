"""WAC-based positions engine (pure, no FX conversion).

Computes per-asset position snapshots using Weighted Average Cost.
All values stay in the native fiat currency of each trade — no FX conversion.

Key design:
  - Groups rows by trade_id (row.id) to find the matching fiat quote leg.
  - Cost of a BUY  = abs(fiat_quote_amount) + any fiat fee on the same trade.
  - Proceeds of SELL = abs(fiat_quote_amount).
  - Fiat fee on SELL reduces realized P&L.
  - Non-fiat fees are ignored (V1).
  - REVERSAL rows are processed by amount sign (same as BUY/SELL).

Usage:
    from core.reports.positions import compute_positions
    rows = svc.timeline()
    positions = compute_positions(rows)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional

from core.dto.reporting import ReportMeta, TableReport, TableRow
from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})
_ROI_PLACES = Decimal("0.01")

# Row types that carry investment position changes
_INVESTMENT_TYPES = frozenset({"BUY", "SELL", "REVERSAL", "STAKING"})


@dataclass
class PositionRow:
    """WAC position snapshot for a single non-fiat asset."""

    asset: str
    quantity: Decimal
    wac: Decimal           # weighted average cost per unit (in fiat)
    cost_basis: Decimal    # total cost basis (in fiat)
    realized_pnl: Decimal  # cumulative realised P&L (in fiat)


@dataclass
class _State:
    """Mutable per-asset accumulator (internal only)."""

    quantity: Decimal = field(default_factory=lambda: Decimal("0"))
    cost_basis: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))


def compute_positions(
    rows: List[RawRow],
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
    initial_cost_transfer: Optional[Dict[str, Decimal]] = None,
) -> List[PositionRow]:
    """Compute WAC per-asset positions from ledger rows.

    Args:
        rows: All ledger rows (sorted internally by (timestamp, id) for
              determinism; caller order does not matter).
        fiat: Set of fiat asset symbols treated as quote currencies.
              Rows whose asset is in this set are never counted as positions.

    Returns:
        List[PositionRow] sorted alphabetically by asset name.
        Includes assets with quantity == 0 (closed positions still carry
        realized_pnl).  Assets with zero total movement are omitted.
        Over-sells result in a negative quantity (diagnostic indicator).
    """
    fiat = frozenset(a.upper() for a in fiat)

    # ── Step 1: deterministic processing order ───────────────────────────────
    # Within the same (timestamp, trade_id), outgoing legs (amount < 0) are
    # sorted before incoming legs so cost basis is available for transfer in
    # asset→asset swaps (e.g. TAO→USDC).
    sorted_rows = sorted(rows, key=lambda r: (r.timestamp, r.id or "", 0 if r.amount < 0 else 1))

    # ── Step 2: index fiat legs by trade_id ──────────────────────────────────
    # fiat_by_id[trade_id] = signed fiat amount of the quote leg
    #   BUY  quote leg: amount < 0 (outflow – you paid fiat)
    #   SELL quote leg: amount > 0 (inflow  – you received fiat)
    fiat_by_id: Dict[str, Decimal] = {}
    # fee_by_id[trade_id] = total absolute fiat fee on the same trade
    fee_by_id: Dict[str, Decimal] = {}

    for row in sorted_rows:
        asset = row.asset.upper()
        if asset not in fiat:
            continue
        if row.type in _INVESTMENT_TYPES:
            # Quote leg of a BUY/SELL/REVERSAL trade
            fiat_by_id[row.id] = row.amount
        elif row.type == "FEE":
            # FEE amounts are stored negative (outflow); accumulate as positive
            fee_by_id[row.id] = (
                fee_by_id.get(row.id, Decimal("0")) + abs(row.amount)
            )

    # ── Step 3: process non-fiat investment rows in chronological order ───────
    states: Dict[str, _State] = {}

    # cost_transfer[trade_id] = CZK cost basis removed from the outgoing asset
    # in an asset→asset swap, to be applied to the incoming asset.
    # Realized P&L is deferred — it will be computed when the incoming asset is
    # eventually sold for fiat.
    # When initial_cost_transfer is supplied (venue-local mode), TRANSFER rows
    # are processed so cost basis flows between venues.
    cost_transfer: Dict[str, Decimal] = dict(initial_cost_transfer or {})
    venue_local = initial_cost_transfer is not None

    for row in sorted_rows:
        asset = row.asset.upper()
        if asset in fiat:
            continue                          # skip fiat quote legs
        if row.type not in _INVESTMENT_TYPES:
            if not (venue_local and row.type == "TRANSFER"):
                continue                      # skip FEE; skip TRANSFER in global mode

        if asset not in states:
            states[asset] = _State()
        state = states[asset]

        # TRANSFER carries cost basis between venues for venue-local WAC.
        if row.type == "TRANSFER":
            if row.amount > 0:               # inflow: inherit cost from source venue
                cost = cost_transfer.get(row.id, Decimal("0"))
                state.quantity   += row.amount
                state.cost_basis += cost
            else:                            # outflow: remove WAC × qty
                sold_qty = abs(row.amount)
                wac = (
                    state.cost_basis / state.quantity
                    if state.quantity > Decimal("0") else Decimal("0")
                )
                cost_removed = wac * sold_qty
                state.quantity   -= sold_qty
                state.cost_basis -= cost_removed
                cost_transfer[row.id] = cost_removed
            continue

        fiat_amount = fiat_by_id.get(row.id)  # None when no fiat leg exists

        if row.amount > 0:
            # ── BUY / STAKING / positive REVERSAL / asset→asset incoming ─────
            if fiat_amount is not None:
                # Normal fiat trade: cost = fiat paid + fiat fee
                base_cost = abs(fiat_amount)
                fee_cost  = fee_by_id.get(row.id, Decimal("0"))
                cost      = base_cost + fee_cost
            elif row.id in cost_transfer:
                # Asset→asset swap: inherit cost basis from the outgoing leg.
                # STAKING rewards have zero fiat cost → lowers blended WAC.
                cost = cost_transfer[row.id]
            else:
                # STAKING or no known cost (zero cost basis)
                cost = Decimal("0")

            state.quantity   += row.amount
            state.cost_basis += cost

        else:
            # ── SELL (or negative REVERSAL): decrease position, realise P&L ─
            # Over-sells (sold_qty > current quantity) are allowed — the position
            # goes negative, which acts as a diagnostic indicator in the UI.
            # Per project principle: diagnostics warn, never block.
            sold_qty = abs(row.amount)

            wac_per_unit = (
                state.cost_basis / state.quantity
                if state.quantity > 0
                else Decimal("0")
            )
            cost_removed = wac_per_unit * sold_qty

            state.quantity   -= sold_qty
            state.cost_basis -= cost_removed

            if fiat_amount is not None:
                # Normal fiat trade: realize P&L now
                proceeds = abs(fiat_amount)
                fee_cost = fee_by_id.get(row.id, Decimal("0"))
                state.realized_pnl += proceeds - cost_removed - fee_cost
            else:
                # Asset→asset swap: transfer cost basis to the incoming asset.
                # P&L is deferred until the incoming asset is sold for fiat.
                cost_transfer[row.id] = cost_removed

    # ── Step 4: build output ──────────────────────────────────────────────────
    result: List[PositionRow] = []
    for asset in sorted(states.keys()):
        state = states[asset]
        wac = (
            state.cost_basis / state.quantity
            if state.quantity > 0
            else Decimal("0")
        )
        result.append(PositionRow(
            asset=asset,
            quantity=state.quantity,
            wac=wac,
            cost_basis=state.cost_basis,
            realized_pnl=state.realized_pnl,
        ))

    return result


def positions_report(
    rows: List[RawRow],
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
) -> TableReport:
    """Wrap compute_positions() output in a TableReport DTO.

    Args:
        rows: All ledger rows (passed through to compute_positions).
        fiat: Set of fiat asset symbols (default: EUR, CZK).

    Returns:
        TableReport with one TableRow per non-fiat asset, sorted alphabetically.
        meta.kind == "snapshot" and meta.bucket == "snapshot".
    """
    fiat = frozenset(a.upper() for a in fiat)
    positions = compute_positions(rows, fiat)

    table_rows = []
    for pos in positions:  # already sorted alphabetically by compute_positions
        roi = (
            (pos.realized_pnl / pos.cost_basis * Decimal("100")).quantize(_ROI_PLACES)
            if pos.cost_basis != Decimal("0")
            else None
        )
        table_rows.append(TableRow(
            key=pos.asset,
            values={
                "quantity":     pos.quantity,
                "wac":          pos.wac,
                "cost_basis":   pos.cost_basis,
                "realized_pnl": pos.realized_pnl,
                "roi":          roi,
            },
        ))

    meta = ReportMeta(bucket="snapshot", fiat=fiat, kind="snapshot")
    return TableReport(meta=meta, rows=table_rows, totals=None)


def compute_transfer_costs(
    rows: List[RawRow],
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
) -> Dict[str, Decimal]:
    """Return {trade_id: cost_basis_removed} for every TRANSFER outflow.

    TRANSFER carries cost basis between venues for venue-local WAC.
    Run once over all_rows before venue-local compute_positions() calls.
    """
    fiat = frozenset(a.upper() for a in fiat)
    sorted_rows = sorted(rows, key=lambda r: (r.timestamp, r.id or "", 0 if r.amount < 0 else 1))

    fiat_by_id: Dict[str, Decimal] = {}
    fee_by_id: Dict[str, Decimal] = {}
    for row in sorted_rows:
        asset = row.asset.upper()
        if asset not in fiat:
            continue
        if row.type in _INVESTMENT_TYPES:
            fiat_by_id[row.id] = row.amount
        elif row.type == "FEE":
            fee_by_id[row.id] = fee_by_id.get(row.id, Decimal("0")) + abs(row.amount)

    states: Dict[str, _State] = {}
    cost_transfer: Dict[str, Decimal] = {}

    for row in sorted_rows:
        asset = row.asset.upper()
        if asset in fiat:
            continue
        if row.type not in _INVESTMENT_TYPES and row.type != "TRANSFER":
            continue  # skip FEE

        if asset not in states:
            states[asset] = _State()
        state = states[asset]

        # TRANSFER carries cost basis between venues for venue-local WAC.
        if row.type == "TRANSFER":
            if row.amount < 0:              # outflow: record WAC × qty leaving
                sold_qty = abs(row.amount)
                wac = (
                    state.cost_basis / state.quantity
                    if state.quantity > Decimal("0") else Decimal("0")
                )
                cost_removed = wac * sold_qty
                state.quantity   -= sold_qty
                state.cost_basis -= cost_removed
                cost_transfer[row.id] = cost_removed
            else:                           # inflow: inherit cost basis
                cost = cost_transfer.get(row.id, Decimal("0"))
                state.quantity   += row.amount
                state.cost_basis += cost
            continue

        fiat_amount = fiat_by_id.get(row.id)
        if row.amount > 0:
            if fiat_amount is not None:
                cost = abs(fiat_amount) + fee_by_id.get(row.id, Decimal("0"))
            elif row.id in cost_transfer:
                cost = cost_transfer[row.id]
            else:
                cost = Decimal("0")
            state.quantity   += row.amount
            state.cost_basis += cost
        else:
            sold_qty = abs(row.amount)
            wac = (
                state.cost_basis / state.quantity
                if state.quantity > Decimal("0") else Decimal("0")
            )
            cost_removed = wac * sold_qty
            state.quantity   -= sold_qty
            state.cost_basis -= cost_removed
            if fiat_amount is None:
                cost_transfer[row.id] = cost_removed

    return cost_transfer
