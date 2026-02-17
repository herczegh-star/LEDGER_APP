"""Fee helper: vytvoření FEE řádku."""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from core.model import RawRow


def create_fee(
    timestamp: datetime,
    venue: str,
    asset: str,
    amount_abs: Decimal,
    note: Optional[str] = None,
) -> RawRow:
    """Vytvoří FEE řádek.

    amount_abs musí být kladné – uloží se jako záporné (odchozí tok).
    currency = asset, price = 1 (identita).
    """
    if amount_abs <= 0:
        raise ValueError(f"amount_abs musí být kladné, dostal: {amount_abs}")
    if not asset:
        raise ValueError("asset nesmí být prázdný")
    if not venue:
        raise ValueError("venue nesmí být prázdné")

    return RawRow(
        timestamp=timestamp,
        type="FEE",
        asset=asset.upper(),
        amount=-abs(amount_abs),
        currency=asset.upper(),
        price=Decimal("1"),
        venue=venue.lower(),
        note=note,
    )
