"""Asset display name overrides — purely cosmetic, never used by accounting.

Some assets rebrand on CoinGecko after this ledger's internal symbol was
already established (e.g. TON -> the project CoinGecko now lists as
"Gram (prev. Toncoin)", id=the-open-network — see core/prices/coingecko.py's
COINGECKO_IDS mapping note). The ledger, SQLite rows, every DTO field, and
every compute_positions()-based calculation ALWAYS use the ledger's own
internal symbol ("TON") — nothing here ever changes that. This module only
maps that internal symbol to a friendlier user-facing label, for UI
rendering ONLY, applied at the last possible moment before a Text control
is built.

Do NOT import this into core/ledger_store.py, core/reports/, core/model.py,
or any accounting/export path — display_asset() must never leak into a
RawRow, a DB write, a CSV export, or any computed DTO field. It exists
solely for ui/* modules to call right where they already build a
ft.Text(asset, ...) control.
"""
from __future__ import annotations

from typing import Dict

# Internal ledger symbol -> user-facing display label. The internal symbol
# is always kept visible in parentheses so a displayed label can still be
# traced back to the ledger/export symbol it corresponds to.
_DISPLAY_OVERRIDES: Dict[str, str] = {
    "TON": "GRAM (TON)",
}


def display_asset(asset: str) -> str:
    """Return the user-facing display string for *asset*.

    Returns the ledger's own symbol unchanged unless a rebrand override
    exists above (case-insensitive match on the internal symbol, which is
    always uppercase per this project's convention). Every other ticker
    passes through completely unchanged.
    """
    return _DISPLAY_OVERRIDES.get(asset.upper(), asset)
