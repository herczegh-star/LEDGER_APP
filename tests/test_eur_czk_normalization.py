"""EUR → CZK normalization tests for ui_facade.add_trade().

Scenarios covered:
  1. EUR BUY — rate available for trade date
  2. EUR BUY — fallback to previous day (rate_date != trade_date)
  3. CZK BUY — no conversion, no note change
  4. EUR never stored in DB after EUR input
  5. Note format: without user note → only rate note
  6. Note format: with user note → user note | rate note
  7. Note duplicate prevention — rate not appended again if already present
  8. EUR fee converted together with quote amount
  9. CNB unavailable → add_trade fails cleanly, nothing written
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime
from decimal import Decimal

import pytest

import core.services.ui_facade as _facade
from core.ledger_store import LedgerStore
from core.services.ui_facade import AddTradeRequestDTO, add_trade

_TS = datetime(2026, 4, 2, 15, 0, 0)
_RATE = Decimal("25.315")
_RATE_DATE = date(2026, 4, 2)
_FALLBACK_DATE = date(2026, 4, 1)  # previous day (e.g. today's rate not yet published)


# ── Provider helpers ──────────────────────────────────────────────────────────

class _ExactDateProvider:
    """Returns rate only for _RATE_DATE; simulates rate available on trade day."""
    def get_eur_czk(self, d: date):
        if d == _RATE_DATE:
            return _RATE, _RATE_DATE
        raise RuntimeError("kurz nedostupný")


class _FallbackProvider:
    """Returns rate only for _FALLBACK_DATE; simulates morning fallback."""
    def get_eur_czk(self, d: date):
        for delta in range(7):
            lookup = d - __import__("datetime").timedelta(days=delta)
            if lookup == _FALLBACK_DATE:
                return _RATE, _FALLBACK_DATE
        raise RuntimeError("kurz nedostupný")


class _UnavailableProvider:
    """Always raises — simulates CNB downtime."""
    def get_eur_czk(self, d: date):
        raise RuntimeError("Kurz EUR/CZK nedostupný (ČNB API)")


# ── Fixtures & helpers ────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "eur_czk_test.db")


def _rows(db_path: str):
    store = LedgerStore(db_path)
    try:
        return store.timeline()
    finally:
        store.close()


def _req(**overrides) -> AddTradeRequestDTO:
    defaults = dict(
        type="BUY",
        timestamp=_TS,
        asset="BTC",
        amount=Decimal("1"),
        currency="EUR",
        price=Decimal("80000"),
        venue="kraken",
    )
    defaults.update(overrides)
    return AddTradeRequestDTO(**defaults)


# ── 1. EUR BUY — rate available for trade date ────────────────────────────────

def test_eur_buy_rate_available_succeeds(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    result = add_trade(_req(), db)
    assert result.success, result.error_message


def test_eur_buy_quote_amount_converted_to_czk(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(price=Decimal("80000")), db)
    rows = _rows(db)
    quote_row = next(r for r in rows if r.asset == "CZK")
    expected = (Decimal("80000") * _RATE).quantize(Decimal("0.01"))
    assert abs(quote_row.amount) == expected


def test_eur_buy_note_contains_rate_and_date(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(), db)
    notes = {r.note for r in _rows(db)}
    assert any("Kurz 25.315 2026-04-02" in (n or "") for n in notes)


# ── 2. EUR BUY — fallback to previous day ────────────────────────────────────

def test_eur_buy_fallback_rate_date_in_note(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _FallbackProvider())
    add_trade(_req(timestamp=datetime(2026, 4, 2, 8, 0, 0)), db)
    notes = {r.note for r in _rows(db)}
    # rate_date must be 2026-04-01, not the trade date 2026-04-02
    assert any("Kurz 25.315 2026-04-01" in (n or "") for n in notes)


def test_eur_buy_fallback_still_converts_correctly(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _FallbackProvider())
    add_trade(_req(price=Decimal("80000"), timestamp=datetime(2026, 4, 2, 8, 0, 0)), db)
    rows = _rows(db)
    quote_row = next(r for r in rows if r.asset == "CZK")
    expected = (Decimal("80000") * _RATE).quantize(Decimal("0.01"))
    assert abs(quote_row.amount) == expected


# ── 3. CZK BUY — no conversion ───────────────────────────────────────────────

def test_czk_buy_unchanged(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _UnavailableProvider())  # must not be called
    result = add_trade(_req(currency="CZK", price=Decimal("2500000")), db)
    assert result.success, result.error_message


def test_czk_buy_quote_amount_not_modified(db):
    add_trade(_req(currency="CZK", price=Decimal("2500000")), db)
    rows = _rows(db)
    quote_row = next(r for r in rows if r.asset == "CZK")
    assert abs(quote_row.amount) == Decimal("2500000")


def test_czk_buy_no_rate_note(db):
    add_trade(_req(currency="CZK", price=Decimal("2500000"), note=None), db)
    rows = _rows(db)
    assert all("Kurz " not in (r.note or "") for r in rows)


# ── 4. EUR never stored in DB ─────────────────────────────────────────────────

def test_eur_asset_not_in_db_after_eur_buy(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(), db)
    assets = {r.asset for r in _rows(db)}
    assert "EUR" not in assets


def test_eur_currency_not_in_db_after_eur_buy(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(), db)
    currencies = {r.currency for r in _rows(db)}
    assert "EUR" not in currencies
    assert "CZK" in currencies


# ── 5. Note format — no user note ─────────────────────────────────────────────

def test_note_format_without_user_note(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(note=None), db)
    notes = {r.note for r in _rows(db)}
    assert "Kurz 25.315 2026-04-02" in notes


def test_note_format_is_human_readable(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(note=None), db)
    base_row = next(r for r in _rows(db) if r.asset == "BTC")
    assert base_row.note == "Kurz 25.315 2026-04-02"


# ── 6. Note format — with user note ──────────────────────────────────────────

def test_note_user_note_preserved(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(note="Kraken nákup"), db)
    base_row = next(r for r in _rows(db) if r.asset == "BTC")
    assert base_row.note == "Kraken nákup | Kurz 25.315 2026-04-02"


def test_note_user_note_not_overwritten(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(note="moje poznamka"), db)
    base_row = next(r for r in _rows(db) if r.asset == "BTC")
    assert base_row.note.startswith("moje poznamka")


# ── 7. Note duplicate prevention ─────────────────────────────────────────────

def test_note_rate_not_duplicated_if_already_present(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    # Pre-populate note with the rate string (e.g. from a re-submission attempt)
    existing_note = "Kraken nákup | Kurz 25.315 2026-04-02"
    add_trade(_req(note=existing_note), db)
    base_row = next(r for r in _rows(db) if r.asset == "BTC")
    # "Kurz " appears exactly once
    assert base_row.note.count("Kurz ") == 1


# ── 8. EUR fee converted ──────────────────────────────────────────────────────

def test_eur_fee_converted_to_czk(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(fee_amount=Decimal("10"), fee_currency="EUR"), db)
    rows = _rows(db)
    fee_row = next((r for r in rows if r.type == "FEE"), None)
    assert fee_row is not None
    expected_fee = (Decimal("10") * _RATE).quantize(Decimal("0.01"))
    assert abs(fee_row.amount) == expected_fee
    assert fee_row.asset == "CZK"


def test_eur_fee_currency_stored_as_czk(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(fee_amount=Decimal("10"), fee_currency="EUR"), db)
    fee_row = next(r for r in _rows(db) if r.type == "FEE")
    assert fee_row.currency == "CZK"


# ── 9. EUR SELL ──────────────────────────────────────────────────────────────

def test_eur_sell_currencies_stored_as_czk(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(type="SELL"), db)
    currencies = {r.currency for r in _rows(db)}
    assert "EUR" not in currencies
    assert "CZK" in currencies


def test_eur_sell_quote_amount_positive_czk(db, monkeypatch):
    """SELL: quote leg is a CZK inflow — amount must be positive."""
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(type="SELL", price=Decimal("80000")), db)
    quote_row = next(r for r in _rows(db) if r.asset == "CZK")
    expected = (Decimal("80000") * _RATE).quantize(Decimal("0.01"))
    assert quote_row.amount == expected


# ── 10. EUR fee with implicit currency (fee_currency=None) ────────────────────

def test_eur_fee_implicit_currency_converted(db, monkeypatch):
    """fee_currency=None in EUR trade → fee is treated as EUR and converted."""
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(fee_amount=Decimal("5"), fee_currency=None), db)
    fee_row = next(r for r in _rows(db) if r.type == "FEE")
    expected = (Decimal("5") * _RATE).quantize(Decimal("0.01"))
    assert abs(fee_row.amount) == expected


def test_eur_fee_implicit_currency_asset_is_czk(db, monkeypatch):
    """fee_currency=None in EUR trade → fee asset must be CZK, not EUR."""
    monkeypatch.setattr(_facade, "_cnb", _ExactDateProvider())
    add_trade(_req(fee_amount=Decimal("5"), fee_currency=None), db)
    fee_row = next(r for r in _rows(db) if r.type == "FEE")
    assert fee_row.asset == "CZK"
    assert fee_row.currency == "CZK"


# ── 11. CNB unavailable → clean failure ──────────────────────────────────────

def test_cnb_unavailable_returns_failure(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _UnavailableProvider())
    result = add_trade(_req(currency="EUR"), db)
    assert not result.success
    assert result.error_message


def test_cnb_unavailable_nothing_written(db, monkeypatch):
    monkeypatch.setattr(_facade, "_cnb", _UnavailableProvider())
    add_trade(_req(currency="EUR"), db)
    store = LedgerStore(db)
    try:
        count = store.count()
    finally:
        store.close()
    assert count == 0
