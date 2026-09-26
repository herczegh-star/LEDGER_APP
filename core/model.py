"""Datový model: unified_format_raw řádek."""
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from typing import Optional
import hashlib
import uuid


VALID_TYPES = {
    "BUY", "SELL", "TRANSFER", "FEE", "REVERSAL", "STAKING",
    "CORRECTION", "BUY_COST_CORRECTION",
    "PORTFOLIO_CONTRIBUTION", "PORTFOLIO_WITHDRAWAL",
}


@dataclass
class RawRow:
    timestamp: datetime
    type: str
    asset: str
    amount: Decimal
    currency: str
    price: Optional[Decimal]
    venue: str
    note: Optional[str] = None
    id: Optional[str] = None
    account: Optional[str] = None
    # Audit-only creation time (maps to the ledger table's pre-existing
    # `imported_at` column — see core/ledger_store.py). Distinct from
    # `timestamp`, which is the accounting-EFFECTIVE time and drives WAC
    # ordering, fingerprint, and cash/position math throughout this codebase.
    # `imported_at` must NEVER be read by compute_positions(), fingerprint(),
    # or any dedup/accounting logic — display/audit only.
    imported_at: Optional[datetime] = None

    def __post_init__(self):
        if self.id is None:
            self.id = str(uuid.uuid4())
        if isinstance(self.amount, (int, float)):
            self.amount = Decimal(str(self.amount))
        if self.price is not None and isinstance(self.price, (int, float)):
            self.price = Decimal(str(self.price))
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)
        if isinstance(self.imported_at, str):
            self.imported_at = datetime.fromisoformat(self.imported_at)

    def fingerprint(self) -> str:
        # Fingerprint v2: timestamp|type|venue|asset|currency|amount
        # v1 chybělo currency, separátory a truncoval timestamp na sekundy.
        # Existující DB z v1 musí být reimportovány nebo zmigrovány.
        parts = "|".join([
            self.timestamp.isoformat(),
            self.type.upper(),
            self.venue.lower(),
            self.asset.upper(),
            self.currency.upper(),
            f"{self.amount:.8f}",
        ])
        return hashlib.sha256(parts.encode()).hexdigest()

    def fingerprint_v2(self) -> str:
        """Account-aware fingerprint. Informational only — NOT persisted,
        NOT used for uniqueness. Legacy fingerprint()/row_fp/idx_row_fp
        remain the sole dedup mechanism (Phase 0 decision: Varianta 1).

        Two rows identical in every legacy field but different `account`
        produce different fingerprint_v2() values. This does not change
        insert-time dedup behaviour — it exists for potential future
        diagnostics/tests only.
        """
        parts = "|".join([
            self.timestamp.isoformat(),
            self.type.upper(),
            self.venue.lower(),
            (self.account or "").strip(),
            self.asset.upper(),
            self.currency.upper(),
            f"{self.amount:.8f}",
        ])
        return hashlib.sha256(parts.encode()).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["amount"] = str(self.amount)
        d["price"] = str(self.price) if self.price is not None else None
        d["imported_at"] = self.imported_at.isoformat() if self.imported_at is not None else None
        return d
