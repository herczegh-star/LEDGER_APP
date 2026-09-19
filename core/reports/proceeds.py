"""Per-SELL proceeds report: neutral fiat breakdown of historical SELL trades.

Pure read-only projection over ledger rows. Does NOT compute cost basis, WAC,
or realized PnL — those remain the sole responsibility of
core/reports/positions.py (compute_positions()), which this module does not
call and does not duplicate.

Field naming is deliberately NEUTRAL: "quote_amount", "fee", "cash_impact".
We do NOT label quote_amount as "gross" or "net" — the ledger's own
trade_service semantics define quote_amount as the fiat total recorded for
the trade's quote leg, and fee as a separately recorded deduction. Whether
that recorded quote_amount corresponds to what a venue statement calls
"gross" or "net" is a source-data question (see Phase 1 HYPE fixture) that
this module does not decide.

CORRECTION-aware (bugfix): a SELL that was later adjusted by one or more
CORRECTION groups (core/correction.py) referencing it via correction_of
must display the CORRECTED economic result, not the stale originally-
recorded numbers — otherwise Asset Detail shows cash figures that
contradict the asset's own (already-correct) realized_pnl total. This is a
pure read of already-existing CORRECTION rows' amounts (the exact delta
that core/reports/positions.py's CORRECTION branch adds to realized_pnl,
via `states[asset].realized_pnl += delta`) — it never recomputes WAC or
cost basis, and never touches the original SELL/FEE rows. Both the
recorded (audit) and corrected (displayed) figures are always returned, so
nothing is hidden.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional, Set

from core.correction import parse_correction_note
from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})


@dataclass
class ProceedsRow:
    """One historical SELL trade's fiat-side breakdown.

    quote_amount / cash_impact reflect the CORRECTED economic result
    (recorded value + correction_delta) — this is what should be displayed
    as "the" quote amount / cash impact. recorded_quote_amount /
    recorded_cash_impact preserve the original, uncorrected ledger facts
    for audit — when correction_delta == 0 (no correction), corrected and
    recorded values are identical, byte-for-byte, and existing callers see
    no behaviour change.
    """

    trade_id: str
    date: str                     # ISO timestamp of the SELL leg
    asset: str                    # asset sold (e.g. "HYPE")
    quantity_sold: Decimal        # positive
    quote_currency: str
    quote_amount: Decimal         # CORRECTED — recorded_quote_amount + correction_delta
    fee: Optional[Decimal]        # positive, or None if no fiat fee on this trade — corrections never change fee
    cash_impact: Decimal          # CORRECTED — recorded_cash_impact + correction_delta
    cash_account: Optional[str]   # ORIGINAL quote leg's account (None = legacy/unassigned) —
                                   # a correction's OWN cash leg may carry a different account;
                                   # see correction_accounts. Account assignment is a separate
                                   # problem from cash-figure correctness (this module never
                                   # invents/merges an account here).
    recorded_quote_amount: Decimal    # ORIGINAL, as-recorded ledger fact (audit)
    recorded_cash_impact: Decimal     # ORIGINAL, as-recorded ledger fact (audit)
    correction_delta: Decimal = Decimal("0")   # sum of all CORRECTION fiat deltas referencing this trade
    has_correction: bool = False
    correction_accounts: List[str] = field(default_factory=list)  # accounts the correction delta(s) landed in


def get_asset_proceeds(
    rows: List[RawRow],
    asset: str,
    fiat: Optional[Set[str]] = None,
) -> List[ProceedsRow]:
    """Build one ProceedsRow per historical SELL trade of *asset*.

    Groups rows by trade_id (row.id). For each group containing a SELL leg
    of *asset*, finds the sibling fiat quote leg (same id, asset in fiat,
    amount > 0) and any sibling fiat FEE leg in the SAME currency as the
    quote leg (amount < 0). REVERSAL-cancelled trades are not filtered here
    — callers wanting only "live" history should cross-reference with
    compute_positions()/health_report() if needed; this is a raw ledger
    projection, not a reconciled view.

    Any CORRECTION group whose note's correction_of matches this trade_id
    (direct reference only — a correction-of-a-correction chain is not
    walked) has its fiat delta leg summed into correction_delta and folded
    into quote_amount/cash_impact. Multiple valid corrections on the same
    trade sum deterministically (in ledger order).

    Returns rows sorted by date, oldest first.
    """
    if fiat is None:
        fiat = _FIAT_DEFAULT
    fiat_set = frozenset(a.upper() for a in fiat)
    asset_uc = asset.upper()

    groups: Dict[str, List[RawRow]] = defaultdict(list)
    for row in rows:
        if row.id:
            groups[row.id].append(row)

    # Pre-pass: CORRECTION fiat legs, indexed by the trade_id they correct.
    # Only the fiat delta leg carries an amount (the non-fiat marker leg is
    # always 0 — see core/correction.py) and a parseable note with
    # correction_of; a malformed/unparseable note is silently skipped here
    # (health_service.py flags those separately — this module never raises
    # on malformed data, matching its existing read-only-projection contract).
    corrections_by_trade: Dict[str, List[RawRow]] = defaultdict(list)
    for row in rows:
        if row.type != "CORRECTION" or row.asset.upper() not in fiat_set:
            continue
        parsed = parse_correction_note(row.note)
        if not parsed or not parsed.get("correction_of"):
            continue
        corrections_by_trade[parsed["correction_of"]].append(row)

    result: List[ProceedsRow] = []
    for trade_id, trade_rows in groups.items():
        sell_legs = [
            r for r in trade_rows
            if r.type == "SELL" and r.asset.upper() == asset_uc and r.amount < 0
        ]
        if not sell_legs:
            continue
        sell_leg = sell_legs[0]

        quote_leg = next(
            (r for r in trade_rows
             if r.asset.upper() in fiat_set and r.type == "SELL" and r.amount > 0),
            None,
        )
        if quote_leg is None:
            continue  # no fiat quote leg on this trade — nothing to report

        fee_leg = next(
            (r for r in trade_rows
             if r.type == "FEE" and r.asset.upper() == quote_leg.asset.upper()
             and r.amount < 0),
            None,
        )
        fee_amount = abs(fee_leg.amount) if fee_leg is not None else None
        recorded_cash_impact = quote_leg.amount - (fee_amount or Decimal("0"))

        correction_legs = [
            r for r in corrections_by_trade.get(trade_id, [])
            if r.asset.upper() == quote_leg.asset.upper()  # same currency as the quote leg
        ]
        correction_delta = sum((r.amount for r in correction_legs), Decimal("0"))
        correction_accounts = sorted({r.account for r in correction_legs if r.account is not None})

        result.append(ProceedsRow(
            trade_id=trade_id,
            date=sell_leg.timestamp.isoformat(),
            asset=asset_uc,
            quantity_sold=abs(sell_leg.amount),
            quote_currency=quote_leg.asset.upper(),
            quote_amount=quote_leg.amount + correction_delta,
            fee=fee_amount,
            cash_impact=recorded_cash_impact + correction_delta,
            cash_account=quote_leg.account,
            recorded_quote_amount=quote_leg.amount,
            recorded_cash_impact=recorded_cash_impact,
            correction_delta=correction_delta,
            has_correction=bool(correction_legs),
            correction_accounts=correction_accounts,
        ))

    result.sort(key=lambda r: r.date)
    return result
