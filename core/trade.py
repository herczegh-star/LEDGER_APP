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
    asset_amount: Decimal,
    currency: str,
    currency_amount: Decimal,
    venue: str,
    price: Optional[Decimal] = None,
    note: Optional[str] = None,
) -> Tuple[RawRow, RawRow]:
    """Vytvoří double-entry pár pro obchod.

    BUY: +asset_amount aktiva, -currency_amount měny
    SELL: -asset_amount aktiva, +currency_amount měny

    Oba řádky sdílejí stejné id (UUID).
    """
    if type_ not in ("BUY", "SELL"):
        raise ValueError(f"Trade type musí být BUY nebo SELL, dostal: {type_}")

    shared_id = str(uuid.uuid4())

    if type_ == "BUY":
        asset_sign = asset_amount
        currency_sign = -currency_amount
    else:
        asset_sign = -asset_amount
        currency_sign = currency_amount

    row_asset = RawRow(
        id=shared_id,
        timestamp=timestamp,
        type=type_,
        asset=asset,
        amount=asset_sign,
        currency=currency,
        price=price,
        venue=venue,
        note=note,
    )

    row_currency = RawRow(
        id=shared_id,
        timestamp=timestamp,
        type=type_,
        asset=currency,
        amount=currency_sign,
        currency=asset,
        price=price,
        venue=venue,
        note=note,
    )

    return (row_asset, row_currency)
