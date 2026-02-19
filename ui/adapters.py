"""Adapter layer: bridges LedgerService + positions engine → UI data model.

UI data model (per asset dict):
  asset       str
  amount      Decimal
  cost        Decimal        – cost_basis_czk
  avg_price   Decimal        – avg_buy_czk
  realized    Decimal        – realized_pnl_czk
  unrealized  Decimal|None   – unrealized_pnl_czk
  roi         Decimal|None
  spot_price  Decimal|None   – derived: value / qty
  value       Decimal|None   – derived: cost + unrealized
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from typing import Callable, List, Optional

from core.config import load_config
from core.service import LedgerService
from ledger_engine.fx_provider import FxProvider, ConfigFxProvider
from ledger_engine.positions_engine import compute_positions


DEFAULT_FX = ConfigFxProvider()


def load_positions_view(
    db_path: str,
    fx_provider: Optional[FxProvider] = None,
    price_provider: Optional[Callable[[str], Optional[Decimal]]] = None,
) -> List[dict]:
    """Načte ledger, spočítá WAC pozice a vrátí seznam dicts pro UI.

    Skips assets with qty == 0 (closed positions).
    Returns [] on any error (empty ledger, missing FX, etc.).
    """
    if fx_provider is None:
        fx_provider = DEFAULT_FX

    svc = LedgerService(db_path)
    try:
        rows = svc.timeline()
    finally:
        svc.close()

    if not rows:
        return []

    try:
        snapshots = compute_positions(rows, fx_provider, price_provider)
    except Exception:
        return []

    result = []
    for asset, snap in snapshots.items():
        if snap.qty == 0:
            continue  # closed position – skip

        # Derive value and spot_price from snapshot fields
        if snap.unrealized_pnl_czk is not None and snap.qty > 0:
            value = snap.cost_basis_czk + snap.unrealized_pnl_czk
            spot_price = value / snap.qty
        else:
            value = None
            spot_price = None

        result.append({
            "asset":      asset,
            "amount":     snap.qty,
            "cost":       snap.cost_basis_czk,
            "avg_price":  snap.avg_buy_czk,
            "realized":   snap.realized_pnl_czk,
            "unrealized": snap.unrealized_pnl_czk,
            "roi":        snap.roi,
            "spot_price": spot_price,
            "value":      value,
        })

    return result
