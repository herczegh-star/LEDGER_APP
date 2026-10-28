"""Portfolio Pulse — portfolio-weighted 24h change.

Public API:
    compute_portfolio_24h_change(positions, price_provider, fiat="CZK")
        -> Portfolio24hChangeDTO

The other three Portfolio Pulse cards need no new service code:
Portfolio Value reuses core.services.ui_facade.compute_headline_value();
Best/Worst Contributor are a plain max()/min() pick over the SAME
get_portfolio_contributors() result the Contributors section already
fetched for the shared period (see ui/modules/analysis_view.py) — no
new computation, just a selection, exactly like that section's own
existing Top-5/Worst-5 picking.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from core.services.ui_facade import PositionDTO, compute_headline_positions

_ZERO = Decimal("0")
_COVERAGE_THRESHOLD = Decimal("0.90")


@dataclass
class Portfolio24hChangeDTO:
    """Portfolio-weighted 24h change, weighted by each asset's CURRENT
    value share of total_value — NOT a sum or plain average of
    percentages.

    change_pct is a percentage NUMBER (e.g. 2.8 means +2.8%), matching
    core.services.relative_strength_service.RelativeStrengthRowDTO.
    change_24h_pct's convention (itself CoinGecko's own convention) —
    NOT a fraction. It is None whenever coverage is below
    _COVERAGE_THRESHOLD or total_value is 0/unknown: a partial-coverage
    number is judged more misleading than a withheld one, never shown
    with an asterisk instead.

    covered_value / total_value are the CZK sums behind coverage.
    coverage is covered_value / total_value (a fraction), populated
    whenever total_value > 0 even when change_pct ends up None — callers
    may use it for a disclosure note, though the default UI need not
    show it.
    """

    change_pct: Optional[Decimal]
    covered_value: Decimal
    total_value: Decimal
    coverage: Optional[Decimal]


def compute_portfolio_24h_change(
    positions: List[PositionDTO],
    price_provider,
    fiat: str = "CZK",
) -> Portfolio24hChangeDTO:
    """Portfolio-weighted 24h change over the SAME headline scope as
    Allocation/Dashboard KPIs: compute_headline_positions(positions)
    (excludes PRESALE_ASSETS) + quantity > 0. Unlike Relative Strength's
    table, BTC and stablecoins are NOT excluded here — they are real
    parts of total portfolio value and must contribute their (small or
    ~0%) share like anything else.

        weight_i = current_value_i / Σ current_value_j   (over every
                   in-scope, valued position — the denominator is total
                   headline value, NOT reduced by missing-24h assets)
        portfolio_24h_change = Σ(weight_i × asset_24h_change_i)
                               renormalized over only the assets that
                               HAVE a live 24h figure (see coverage)

    An asset missing a live 24h figure (price_provider.
    get_prices_with_24h_change() didn't return it, or returned it with
    change_24h_pct=None) is dropped from BOTH the weighted numerator and
    the renormalization denominator — never substituted with 0.

    coverage = covered_value / total_value. Below _COVERAGE_THRESHOLD
    (0.90), or when total_value is 0, change_pct is None.

    Never raises: a price_provider failure (missing method, exception,
    empty result) yields change_pct=None — never crashes the rest of
    Analysis.
    """
    headline = compute_headline_positions(positions)
    candidates = [p for p in headline if p.quantity > _ZERO and p.value is not None]

    total_value = sum((p.value for p in candidates), _ZERO)
    if total_value <= _ZERO:
        return Portfolio24hChangeDTO(change_pct=None, covered_value=_ZERO, total_value=_ZERO, coverage=None)

    change_map = {}
    if price_provider is not None:
        try:
            change_map = price_provider.get_prices_with_24h_change([p.asset for p in candidates], fiat)
        except Exception:
            change_map = {}

    covered_value = _ZERO
    weighted_sum = _ZERO
    for p in candidates:
        entry = change_map.get(p.asset)
        if entry is None or entry.change_24h_pct is None:
            continue
        covered_value += p.value
        weighted_sum += p.value * entry.change_24h_pct

    coverage = covered_value / total_value

    if covered_value <= _ZERO or coverage < _COVERAGE_THRESHOLD:
        return Portfolio24hChangeDTO(
            change_pct=None, covered_value=covered_value, total_value=total_value, coverage=coverage,
        )

    change_pct = weighted_sum / covered_value  # renormalized: weights among covered assets sum to 1
    return Portfolio24hChangeDTO(
        change_pct=change_pct, covered_value=covered_value, total_value=total_value, coverage=coverage,
    )
