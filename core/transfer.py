"""Transfer helper: vytvoření TRANSFER řádku."""
from datetime import datetime
from decimal import Decimal
from core.model import RawRow


def create_transfer(
    timestamp: datetime,
    venue: str,
    asset: str,
    amount: Decimal,
    note: str,
) -> RawRow:
    """Vytvoří TRANSFER řádek.

    amount se zachovává se znaménkem (+ příchozí, - odchozí).
    currency = asset, price = 1 (identita).
    note je povinná (kam/odkud).
    """
    if amount == 0:
        raise ValueError("amount nesmí být 0")
    if not asset:
        raise ValueError("asset nesmí být prázdný")
    if not venue:
        raise ValueError("venue nesmí být prázdné")
    if not note:
        raise ValueError("note je povinná pro TRANSFER")

    return RawRow(
        timestamp=timestamp,
        type="TRANSFER",
        asset=asset.upper(),
        amount=amount,
        currency=asset.upper(),
        price=Decimal("1"),
        venue=venue.lower(),
        note=note,
    )
