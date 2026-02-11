"""RAW Loader: načtení *_raw.xlsm / *_raw.csv → List[RawRow]. Žádná transformace významu."""
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List
from core.model import RawRow


def _parse_decimal(val) -> Decimal:
    if val is None or val == "":
        return Decimal("0")
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return Decimal("0")


def _parse_timestamp(val) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"]:
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    raise ValueError(f"Nelze parsovat timestamp: {val}")


def _normalize_row(row_dict: dict) -> RawRow:
    ts = _parse_timestamp(row_dict.get("timestamp"))
    amount = _parse_decimal(row_dict.get("amount"))
    price_raw = row_dict.get("price")
    price = _parse_decimal(price_raw) if price_raw is not None and str(price_raw).strip() != "" else None
    venue = str(row_dict.get("venue", "")).strip().lower()
    asset = str(row_dict.get("asset", "")).strip().upper()
    currency = str(row_dict.get("currency", "")).strip().upper()
    type_ = str(row_dict.get("type", "")).strip().upper()
    note = row_dict.get("note")
    if note is not None:
        note = str(note).strip()
        if note == "" or note.lower() == "nan" or note.lower() == "none":
            note = None
    row_id = row_dict.get("id")
    if row_id is not None:
        row_id = str(row_id).strip()
        if row_id == "" or row_id.lower() == "nan":
            row_id = None

    return RawRow(
        id=row_id,
        timestamp=ts,
        type=type_,
        asset=asset,
        amount=amount,
        currency=currency,
        price=price,
        venue=venue,
        note=note,
    )


def load_xlsm(filepath: str) -> List[RawRow]:
    import openpyxl
    wb = openpyxl.load_workbook(filepath, data_only=True)
    if "raw" in wb.sheetnames:
        ws = wb["raw"]
    else:
        ws = wb.active

    rows_data = list(ws.iter_rows(values_only=False))
    if not rows_data:
        return []

    headers = [str(c.value).strip().lower() if c.value else "" for c in rows_data[0]]
    result = []
    for row in rows_data[1:]:
        vals = [c.value for c in row]
        row_dict = dict(zip(headers, vals))
        if not row_dict.get("type") or not row_dict.get("asset"):
            continue
        if str(row_dict.get("type", "")).strip() == "":
            continue
        try:
            result.append(_normalize_row(row_dict))
        except (ValueError, KeyError) as e:
            print(f"  Přeskočen řádek (chyba): {e}")
    return result


def load_csv(filepath: str) -> List[RawRow]:
    result = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_dict in reader:
            if not row_dict.get("type") or not row_dict.get("asset"):
                continue
            try:
                result.append(_normalize_row(row_dict))
            except (ValueError, KeyError) as e:
                print(f"  Přeskočen řádek (chyba): {e}")
    return result


def load_raw(filepath: str) -> List[RawRow]:
    path = Path(filepath)
    if path.suffix in (".xlsm", ".xlsx"):
        return load_xlsm(filepath)
    elif path.suffix == ".csv":
        return load_csv(filepath)
    else:
        raise ValueError(f"Nepodporovaný formát: {path.suffix}")
