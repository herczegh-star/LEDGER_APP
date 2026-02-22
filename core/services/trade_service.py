"""Trade service: build and append double-entry trade rows to the ledger.

UI calls add_trade() only. No computations happen in UI.

Key difference from LedgerService.add_trade():
  - Input is quote_amount (total fiat exchanged), not unit price.
  - Optional bundled fee row (same trade_id, type=FEE).
  - Returns TradeResult with inserted/skipped dedup counts.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import FrozenSet, List, Optional

from core.ledger_store import LedgerStore
from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})


@dataclass
class TradeResult:
    """Return value of add_trade() — generated rows plus dedup counters."""

    rows: List[RawRow]
    inserted: int
    skipped: int


@dataclass
class AddTradeInput:
    """User-facing trade input.  All amounts are positive; service assigns signs."""

    type: str                          # "BUY" | "SELL"
    timestamp: datetime
    base_asset: str                    # e.g. "BTC" — must NOT be in fiat
    base_amount: Decimal               # positive quantity of base asset
    quote_currency: str                # e.g. "EUR" — must be in fiat
    quote_amount: Decimal              # positive total fiat amount paid / received
    venue: str
    fee_amount: Optional[Decimal] = field(default=None)    # positive if present
    fee_currency: Optional[str] = field(default=None)      # defaults to quote_currency
    note: Optional[str] = field(default=None)


def _validate(inp: AddTradeInput, fiat: FrozenSet[str]) -> None:
    """Raise ValueError if inp is invalid."""
    if inp.type not in ("BUY", "SELL"):
        raise ValueError(f"type must be BUY or SELL, got: {inp.type!r}")
    if inp.base_amount <= 0:
        raise ValueError(f"base_amount must be > 0, got: {inp.base_amount}")
    if inp.quote_amount <= 0:
        raise ValueError(f"quote_amount must be > 0, got: {inp.quote_amount}")
    if inp.fee_amount is not None and inp.fee_amount <= 0:
        raise ValueError(f"fee_amount must be > 0 when provided, got: {inp.fee_amount}")
    if not inp.venue:
        raise ValueError("venue must not be empty")
    q = inp.quote_currency.upper()
    if q not in fiat:
        raise ValueError(f"quote_currency {q!r} is not in allowed fiat set {sorted(fiat)}")
    b = inp.base_asset.upper()
    if b in fiat:
        raise ValueError(f"base_asset {b!r} must not be a fiat currency")


def build_trade_rows(
    inp: AddTradeInput,
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
) -> List[RawRow]:
    """Pure function — no side-effects.

    Returns 2 rows (base + quote) or 3 rows (base + quote + fee).
    All rows share the same trade_id so they form one logical transaction.

    Signs:
        BUY:  base +amount, quote -amount, fee -amount
        SELL: base -amount, quote +amount, fee -amount
    """
    _validate(inp, fiat)

    trade_id = str(uuid.uuid4())
    base = inp.base_asset.upper()
    quote = inp.quote_currency.upper()
    price = inp.quote_amount / inp.base_amount   # informational unit price

    if inp.type == "BUY":
        base_sign = inp.base_amount
        quote_sign = -inp.quote_amount
    else:
        base_sign = -inp.base_amount
        quote_sign = inp.quote_amount

    row_base = RawRow(
        id=trade_id,
        timestamp=inp.timestamp,
        type=inp.type,
        asset=base,
        amount=base_sign,
        currency=quote,
        price=price,
        venue=inp.venue.lower(),
        note=inp.note,
    )

    row_quote = RawRow(
        id=trade_id,
        timestamp=inp.timestamp,
        type=inp.type,
        asset=quote,
        amount=quote_sign,
        currency=quote,
        price=Decimal("1"),
        venue=inp.venue.lower(),
        note=inp.note,
    )

    rows: List[RawRow] = [row_base, row_quote]

    if inp.fee_amount is not None:
        fee_asset = (inp.fee_currency or inp.quote_currency).upper()
        row_fee = RawRow(
            id=trade_id,
            timestamp=inp.timestamp,
            type="FEE",
            asset=fee_asset,
            amount=-inp.fee_amount,   # always outflow
            currency=fee_asset,       # matches create_fee() convention
            price=Decimal("1"),
            venue=inp.venue.lower(),
            note=inp.note,
        )
        rows.append(row_fee)

    return rows


def add_trade(
    db_path: str,
    inp: AddTradeInput,
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
) -> TradeResult:
    """Build rows and append to the ledger.

    Raises ValueError on invalid input (before touching DB).
    Returns TradeResult with the generated rows and inserted/skipped counts.
    """
    rows = build_trade_rows(inp, fiat)
    store = LedgerStore(db_path)
    try:
        counts = store.import_rows(rows)
    finally:
        store.close()
    return TradeResult(rows=rows, inserted=counts["inserted"], skipped=counts["skipped"])
