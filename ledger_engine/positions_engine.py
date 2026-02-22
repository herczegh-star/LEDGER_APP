"""Positions engine: WAC výpočet per-asset nad unified_format_raw ledgerem.

DEPRECATED (production): Tento engine je nahrazen core/reports/positions.py,
který je fiat-native (bez FX konverze) a vrací TableReport DTO.
Tento soubor zůstává kvůli existujícím testům (tests/test_positions_engine_wac.py).
Nepoužívat v novém kódu.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from core.model import RawRow
from ledger_engine.fx_provider import FxProvider, FIAT_ASSETS


# Typy toků ignorované při výpočtu investičních pozic.
_SKIP_TYPES = frozenset({"FEE", "TRANSFER"})


@dataclass
class AssetSnapshot:
    """Snapshot WAC pozice jednoho crypto assetu."""

    qty: Decimal
    cost_basis_czk: Decimal
    avg_buy_czk: Decimal
    realized_pnl_czk: Decimal
    unrealized_pnl_czk: Optional[Decimal] = None
    roi: Optional[Decimal] = None


@dataclass
class _WacState:
    """Vnitřní WAC stav pro jeden asset – mutuje se průchodem ledgerem."""

    qty: Decimal = field(default_factory=lambda: Decimal("0"))
    cost_basis_czk: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_pnl_czk: Decimal = field(default_factory=lambda: Decimal("0"))


def _to_czk(row: RawRow, fx_provider: FxProvider) -> Decimal:
    """Převede hodnotu řádku na CZK.

    trade_value = abs(amount) * price
    CZK → beze změny
    EUR → * fx_provider.get_eur_to_czk(date)

    Raises:
        ValueError: Chybějící price, nepodporovaná currency nebo chybějící FX kurz.
    """
    if row.price is None:
        raise ValueError(
            f"Řádek id={row.id!r} nemá price – nelze spočítat hodnotu v CZK."
        )

    trade_value = abs(row.amount) * row.price

    if row.currency == "CZK":
        return trade_value
    elif row.currency == "EUR":
        date = row.timestamp.strftime("%Y-%m-%d")
        rate = fx_provider.get_eur_to_czk(date)
        return trade_value * rate
    else:
        raise ValueError(
            f"Nepodporovaná currency: {row.currency!r}. Podporováno: CZK, EUR."
        )


def compute_positions(
    rows: List[RawRow],
    fx_provider: FxProvider,
    price_provider: Optional[Callable[[str], Decimal]] = None,
) -> Dict[str, AssetSnapshot]:
    """Vypočítá WAC pozice ze seznamu RawRow.

    Seřadí ledger podle (timestamp, id) pro determinismus.
    Ignoruje fiat assety (EUR, CZK) a typy FEE, TRANSFER.
    Aplikuje BUY/SELL logiku podle znaménka amount.
    REVERSAL se chová jako standardní řádek s opačným znaménkem.

    Args:
        rows: Ledger řádky (typicky z LedgerService.timeline()).
        fx_provider: Zdroj historických kurzů EUR→CZK.
        price_provider: Volitelný callback asset → aktuální cena v CZK.
                        Pokud předán, dopočítá unrealized_pnl_czk a roi.

    Returns:
        Dict {asset: AssetSnapshot} – jen crypto assety s nenulovým pohybem.

    Raises:
        ValueError: Při prodeji více než je aktuální pozice, chybějícím FX
                    kurzu nebo chybějící price u zpracovávaného řádku.
    """
    # Deterministické řazení: (timestamp, id)
    sorted_rows = sorted(rows, key=lambda r: (r.timestamp, r.id or ""))

    states: Dict[str, _WacState] = {}

    for row in sorted_rows:
        # Přeskočit fiat (jsou protistrana, ne investiční aktiva)
        if row.asset.upper() in FIAT_ASSETS:
            continue

        # Přeskočit typy bez investičního efektu
        if row.type in _SKIP_TYPES:
            continue

        asset = row.asset.upper()
        if asset not in states:
            states[asset] = _WacState()
        state = states[asset]

        value_czk = _to_czk(row, fx_provider)

        if row.amount > 0:
            # BUY: přidání do pozice
            state.qty += row.amount
            state.cost_basis_czk += value_czk
        else:
            # SELL / REVERSAL (záporný amount): odebrání z pozice s P&L
            qty_out = abs(row.amount)
            if qty_out > state.qty:
                raise ValueError(
                    f"Nelze prodat {qty_out} {asset}: aktuální pozice je {state.qty}."
                )
            avg_cost = state.cost_basis_czk / state.qty
            cost_removed = avg_cost * qty_out
            proceeds = value_czk

            state.qty -= qty_out
            state.cost_basis_czk -= cost_removed
            state.realized_pnl_czk += proceeds - cost_removed

    # Sestavení výstupních snapshotů
    result: Dict[str, AssetSnapshot] = {}
    for asset, state in states.items():
        avg_buy = state.cost_basis_czk / state.qty if state.qty > 0 else Decimal("0")

        snapshot = AssetSnapshot(
            qty=state.qty,
            cost_basis_czk=state.cost_basis_czk,
            avg_buy_czk=avg_buy,
            realized_pnl_czk=state.realized_pnl_czk,
        )

        if price_provider is not None and state.qty > 0:
            last_price_czk = price_provider(asset)
            if last_price_czk is None:
                raise ValueError(f"Missing price for asset {asset!r}.")
            unrealized = state.qty * last_price_czk - state.cost_basis_czk
            snapshot.unrealized_pnl_czk = unrealized
            if state.cost_basis_czk > 0:
                snapshot.roi = unrealized / state.cost_basis_czk

        result[asset] = snapshot

    return result
