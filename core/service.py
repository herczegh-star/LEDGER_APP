"""LedgerService: jediné veřejné API jádra. UI volá jen tuto vrstvu."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from core.model import RawRow
from core.ledger_store import LedgerStore
from core.validator import validate_row, validate_rows
from core.trade import create_trade
from core.reversal import create_reversal, create_reversal_pair
from io_module.raw_loader import load_raw


@dataclass
class ImportResult:
    """Výsledek importu souboru do ledgeru."""
    inserted: int = 0
    skipped: int = 0
    parse_errors: List[dict] = field(default_factory=list)
    validation_errors: List[tuple] = field(default_factory=list)
    diagnostics: List[dict] = field(default_factory=list)


@dataclass
class OperationResult:
    """Výsledek jedné write operace (add row, trade, reversal)."""
    success: bool = False
    rows_inserted: int = 0
    diagnostics: List[dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class LedgerService:
    """Jediný vstupní bod mezi UI a core. Všechny operace procházejí zde."""

    def __init__(self, db_path: str = "ledger.db"):
        self._store = LedgerStore(db_path)

    # ── WRITE OPERACE ─────────────────────────────

    def import_file(self, filepath: str) -> ImportResult:
        """Pipeline: load file → validate → insert → diagnostics."""
        result = ImportResult()

        load_result = load_raw(filepath)
        result.parse_errors = load_result.errors

        valid, invalid = validate_rows(load_result.rows)
        result.validation_errors = invalid

        if valid:
            insert_result = self._store.import_rows(valid)
            result.inserted = insert_result["inserted"]
            result.skipped = insert_result["skipped"]

        result.diagnostics = self._store.diagnostics()
        return result

    def add_row(
        self,
        timestamp: datetime,
        type_: str,
        asset: str,
        amount: Decimal,
        currency: str,
        venue: str,
        price: Optional[Decimal] = None,
        note: Optional[str] = None,
    ) -> OperationResult:
        """Vytvoří a vloží jeden manuální řádek."""
        row = RawRow(
            timestamp=timestamp, type=type_, asset=asset, amount=amount,
            currency=currency, price=price, venue=venue, note=note,
        )

        ok, errs = validate_row(row)
        if not ok:
            return OperationResult(success=False, errors=errs)

        inserted = self._store.insert(row)
        if not inserted:
            return OperationResult(success=False, errors=["Duplicitní řádek (row_fp již existuje)."])

        return OperationResult(
            success=True,
            rows_inserted=1,
            diagnostics=self._store.diagnostics(),
        )

    def add_trade(
        self,
        timestamp: datetime,
        type_: str,
        asset: str,
        asset_amount: Decimal,
        currency: str,
        currency_amount: Decimal,
        venue: str,
        price: Optional[Decimal] = None,
        note: Optional[str] = None,
    ) -> OperationResult:
        """Vytvoří a vloží double-entry obchodní pár."""
        try:
            row_a, row_c = create_trade(
                timestamp=timestamp, type_=type_, asset=asset,
                asset_amount=asset_amount, currency=currency,
                currency_amount=currency_amount, venue=venue,
                price=price, note=note,
            )
        except ValueError as e:
            return OperationResult(success=False, errors=[str(e)])

        ok_a, errs_a = validate_row(row_a)
        ok_c, errs_c = validate_row(row_c)
        if not ok_a or not ok_c:
            all_errs = []
            if errs_a:
                all_errs.append(f"Asset řádek: {', '.join(errs_a)}")
            if errs_c:
                all_errs.append(f"Currency řádek: {', '.join(errs_c)}")
            return OperationResult(success=False, errors=all_errs)

        ins_a, ins_c = self._store.insert_pair(row_a, row_c)
        rows_inserted = sum([ins_a, ins_c])
        if rows_inserted == 0:
            return OperationResult(success=False, errors=["Oba řádky jsou duplikáty."])

        return OperationResult(
            success=True,
            rows_inserted=rows_inserted,
            diagnostics=self._store.diagnostics(),
        )

    def add_reversal(self, pk: int, reverse_pair: bool = True) -> OperationResult:
        """Vytvoří a vloží reversal pro řádek identifikovaný pk."""
        original = self._store.get_row_by_pk(pk)
        if original is None:
            return OperationResult(success=False, errors=[f"Řádek pk={pk} nenalezen."])

        pair = self._store.get_rows_by_id(original.id)
        if len(pair) > 1 and reverse_pair:
            reversals = create_reversal_pair(pair)
        else:
            reversals = [create_reversal(original)]

        all_errors = []
        for rev in reversals:
            ok, errs = validate_row(rev)
            if not ok:
                all_errors.extend(errs)
        if all_errors:
            return OperationResult(success=False, errors=all_errors)

        inserted_count = 0
        for rev in reversals:
            if self._store.insert(rev):
                inserted_count += 1

        if inserted_count == 0:
            return OperationResult(success=False, errors=["Všechny reversaly jsou duplikátní."])

        return OperationResult(
            success=True,
            rows_inserted=inserted_count,
            diagnostics=self._store.diagnostics(),
        )

    # ── READ / QUERY OPERACE ─────────────────────

    def timeline(self) -> List[RawRow]:
        return self._store.timeline()

    def asset_balances(self) -> dict:
        return self._store.asset_balances()

    def venue_balances(self) -> dict:
        return self._store.venue_balances()

    def diagnostics(self) -> List[dict]:
        return self._store.diagnostics()

    def recent_rows(self, limit: int = 20) -> List[dict]:
        return self._store.recent_rows(limit)

    def count(self) -> int:
        return self._store.count()

    def get_row_by_pk(self, pk: int) -> Optional[RawRow]:
        return self._store.get_row_by_pk(pk)

    def get_rows_by_id(self, row_id: str) -> List[RawRow]:
        return self._store.get_rows_by_id(row_id)

    # ── LIFECYCLE ─────────────────────────────────

    def close(self):
        self._store.close()
