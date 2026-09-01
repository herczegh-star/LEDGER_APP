"""Health guard: a REVERSAL that exactly cancels a same-id FEE of the same asset
(fee-currency correction) must not raise missing_quote_leg.  A lone orphan
REVERSAL with no matching FEE is still flagged."""
from datetime import datetime
from decimal import Decimal

from core.model import RawRow
from core.services.health_service import health_report


def _r(type_, asset, amount, venue, id_, currency=None, ts="2026-03-25T17:00:35"):
    return RawRow(
        id=id_,
        timestamp=datetime.fromisoformat(ts),
        type=type_,
        asset=asset,
        amount=Decimal(str(amount)),
        currency=currency or asset,
        price=Decimal("1"),
        venue=venue,
    )


def _kinds(rows):
    return [row.values["kind"] for row in health_report(rows).rows]


TID = "20260325_170034_KRAKEN_TRANSFER_001"


def test_fee_currency_correction_raises_nothing():
    rows = [
        # prior USDC position so the corrected USDC fee has a balance to draw from
        _r("BUY", "USDC", "100", "kraken", "buy1", currency="CZK", ts="2026-03-01T00:00:00"),
        _r("BUY", "CZK", "-2400", "kraken", "buy1", currency="CZK", ts="2026-03-01T00:00:00"),
        _r("FEE", "USDT", "-0.5902", "kraken", TID, ts="2026-03-25T17:00:34"),
        _r("REVERSAL", "USDT", "0.5902", "kraken", TID),
        _r("FEE", "USDC", "-0.5902", "kraken", TID),
    ]
    kinds = _kinds(rows)
    assert "missing_quote_leg" not in kinds
    assert "oversell" not in kinds


def test_lone_orphan_reversal_still_flagged():
    rows = [_r("REVERSAL", "WOO", "-5", "anycoin", "ORPHAN_1")]
    assert "missing_quote_leg" in _kinds(rows)


def test_fee_correction_amount_mismatch_still_flagged():
    rows = [
        _r("FEE", "USDT", "-0.5902", "kraken", TID, ts="2026-03-25T17:00:34"),
        _r("REVERSAL", "USDT", "0.60", "kraken", TID),   # wrong magnitude
    ]
    assert "missing_quote_leg" in _kinds(rows)


def test_fee_correction_asset_mismatch_still_flagged():
    rows = [
        _r("FEE", "USDT", "-0.5902", "kraken", TID, ts="2026-03-25T17:00:34"),
        _r("REVERSAL", "USDC", "0.5902", "kraken", TID),  # different asset
    ]
    assert "missing_quote_leg" in _kinds(rows)


def test_fee_correction_only_consumes_one_matching_fee():
    # two identical corrective reversals but only one matching FEE -> the second
    # reversal is still an orphan and must be flagged.
    rows = [
        _r("FEE", "USDT", "-0.5902", "kraken", TID, ts="2026-03-25T17:00:34"),
        _r("REVERSAL", "USDT", "0.5902", "kraken", TID),
        _r("REVERSAL", "USDT", "0.5902", "kraken", TID),
    ]
    assert "missing_quote_leg" in _kinds(rows)
