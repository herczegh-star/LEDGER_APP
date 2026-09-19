"""Validator: syntaktická kontrola RAW řádku. Žádná sémantika."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Tuple
from core.model import RawRow, VALID_TYPES


def validate_row(row: RawRow) -> Tuple[bool, List[str]]:
    errors = []

    if not row.id:
        errors.append("Chybí id.")

    if not isinstance(row.timestamp, datetime):
        errors.append(f"timestamp není datetime: {row.timestamp}")

    if row.type not in VALID_TYPES:
        errors.append(f"Neplatný type: '{row.type}'. Povolené: {VALID_TYPES}")

    if not row.asset or not isinstance(row.asset, str):
        errors.append("Chybí nebo neplatný asset.")

    try:
        amt = Decimal(str(row.amount))
        # CORRECTION and BUY_COST_CORRECTION are the sole exceptions: their
        # non-fiat POSITION MARKER leg is required to be amount=0 (no
        # physical quantity ever moves for a correction — see
        # core/correction.py, core/buy_cost_correction.py). All other types
        # keep the existing rule: amount=0 has no economic meaning and stays
        # rejected. Group-level correctness (exactly one marker + one fiat
        # delta leg, marker asset not fiat, delta != 0, matching
        # venue/account/currency) is NOT checked here — validate_row() is
        # single-row/syntactic only; see validate_correction_group() /
        # validate_buy_cost_correction_group() for that.
        if amt == 0 and row.type not in ("CORRECTION", "BUY_COST_CORRECTION"):
            errors.append("amount je 0.")
    except (InvalidOperation, TypeError):
        errors.append(f"amount není platné číslo: {row.amount}")

    if not row.currency or not isinstance(row.currency, str):
        errors.append("Chybí nebo neplatný currency.")

    if row.price is not None:
        try:
            Decimal(str(row.price))
        except (InvalidOperation, TypeError):
            errors.append(f"price není platné číslo: {row.price}")

    if not row.venue or not isinstance(row.venue, str):
        errors.append("Chybí nebo neplatný venue.")

    return (len(errors) == 0, errors)


def validate_rows(rows: List[RawRow]) -> Tuple[List[RawRow], List[Tuple[int, List[str]]]]:
    valid = []
    invalid = []
    for i, row in enumerate(rows):
        ok, errs = validate_row(row)
        if ok:
            valid.append(row)
        else:
            invalid.append((i, errs))
    return valid, invalid
