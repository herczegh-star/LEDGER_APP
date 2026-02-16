"""Trade helper: double-entry vytvoření páru pro BUY/SELL."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Tuple, Optional
from core.model import RawRow


def create_trade(
    timestamp: datetime,
    type_: str,
    asset: str,
    amount: Decimal,
    currency: str,
    price: Decimal,
    venue: str,
    note: Optional[str] = None,
) -> Tuple[RawRow, RawRow]:
    """Vytvoří double-entry pár pro obchod.

    quote_amount = amount * price (vypočteno automaticky).

    BUY:  +amount aktiva, -quote_amount měny
    SELL: -amount aktiva, +quote_amount měny

    Oba řádky sdílejí stejné id (UUID).
    Invariant: currency = quote_asset pro oba řádky.
    """
    if type_ not in ("BUY", "SELL"):
        raise ValueError(f"Trade type musí být BUY nebo SELL, dostal: {type_}")

    shared_id = str(uuid.uuid4())
    quote_amount = amount * price

    if type_ == "BUY":
        asset_sign = amount
        currency_sign = -quote_amount
    else:
        asset_sign = -amount
        currency_sign = quote_amount

    # Invariant: currency = quote_asset pro oba řádky.
    trade_currency = currency

    row_asset = RawRow(
        id=shared_id,
        timestamp=timestamp,
        type=type_,
        asset=asset,
        amount=asset_sign,
        currency=trade_currency,
        price=price,
        venue=venue,
        note=note,
    )

    row_currency = RawRow(
        id=shared_id,
        timestamp=timestamp,
        type=type_,
        asset=trade_currency,
        amount=currency_sign,
        currency=trade_currency,
        price=Decimal("1"),
        venue=venue,
        note=note,
    )

    return (row_asset, row_currency)
