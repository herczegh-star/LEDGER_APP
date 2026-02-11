"""Reversal helper: vytvoření REVERSAL řádku z existujícího záznamu."""
from datetime import datetime
from typing import List, Optional
from core.model import RawRow


def create_reversal(original: RawRow, note_prefix: str = "reversal of") -> RawRow:
    """Vytvoří REVERSAL řádek negující původní tok.

    - Nové auto-generované id
    - type = REVERSAL
    - Timestamp = nyní
    - Stejný asset, currency, venue
    - Opačný amount
    - price zkopírována (informativní)
    - note odkazuje na původní id
    """
    return RawRow(
        id=None,
        timestamp=datetime.now().replace(microsecond=0),
        type="REVERSAL",
        asset=original.asset,
        amount=-original.amount,
        currency=original.currency,
        price=original.price,
        venue=original.venue,
        note=f"{note_prefix} {original.id}",
    )


def create_reversal_pair(originals: List[RawRow], note_prefix: str = "reversal of") -> List[RawRow]:
    """Vytvoří REVERSAL řádky pro celý double-entry pár."""
    return [create_reversal(row, note_prefix) for row in originals]
