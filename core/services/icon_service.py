"""Icon service — resolves and auto-downloads cryptocurrency icons.

Icons are stored in the user data directory (~/.ledger_app/icons/).
On first run, bundled icons from assets/icons/ are migrated there.
Missing icons are auto-downloaded from CoinGecko in the background.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────────
USER_DATA_DIR = Path.home() / ".ledger_app"
ICONS_DIR = USER_DATA_DIR / "icons"
VENUE_ICONS_DIR = USER_DATA_DIR / "venue_icons"
_BUNDLED_DIR = Path(__file__).parent.parent.parent / "assets" / "icons"
_BUNDLED_VENUE_DIR = Path(__file__).parent.parent.parent / "assets" / "venue_icons"

# ── Known CoinGecko IDs (skips search API call for common coins) ───────────────
_KNOWN_IDS: dict[str, str] = {
    "AAVE":  "aave",
    "ARB":   "arbitrum",
    "ATOM":  "cosmos",
    "AVAX":  "avalanche-2",
    "B3":    "b3-exchange",
    "BNB":   "binancecoin",
    "BTC":   "bitcoin",
    "DOGE":  "dogecoin",
    "DOT":   "polkadot",
    "DYDX":  "dydx",
    "ETH":   "ethereum",
    "FET":   "fetch-ai",
    "GRT":   "the-graph",
    "LINK":  "chainlink",
    "ONDO":  "ondo-finance",
    "PENGU": "pudgy-penguins",
    "RUNE":  "thorchain",
    "SOL":   "solana",
    "TAO":   "bittensor",
    "UNI":   "uniswap",
    "WOO":   "woo-network",
    "XRP":   "ripple",
}


def setup() -> None:
    """Create icon dirs and migrate bundled icons. Call once at startup."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    VENUE_ICONS_DIR.mkdir(parents=True, exist_ok=True)
    if _BUNDLED_DIR.exists():
        for src in _BUNDLED_DIR.glob("*.png"):
            dst = ICONS_DIR / src.name
            if not dst.exists():
                dst.write_bytes(src.read_bytes())
    if _BUNDLED_VENUE_DIR.exists():
        for src in _BUNDLED_VENUE_DIR.glob("*.png"):
            dst = VENUE_ICONS_DIR / src.name
            if not dst.exists():
                dst.write_bytes(src.read_bytes())


def has_venue_icon(venue: str) -> bool:
    return (VENUE_ICONS_DIR / f"{venue.lower().replace(' ', '_')}.png").exists()


def has_icon(asset: str) -> bool:
    return (ICONS_DIR / f"{asset.lower()}.png").exists()


def download_missing(assets: list[str]) -> None:
    """Download icons for any assets that don't have one yet. Silent on error."""
    missing = [a for a in assets if not has_icon(a)]
    for asset in missing:
        _download_one(asset)
        time.sleep(0.3)  # polite rate-limit


def _download_one(asset: str) -> bool:
    dest = ICONS_DIR / f"{asset.lower()}.png"
    if dest.exists():
        return True

    cg_id = _KNOWN_IDS.get(asset.upper()) or _search_coingecko_id(asset)
    if not cg_id:
        return False

    try:
        url = (
            f"https://api.coingecko.com/api/v3/coins/{cg_id}"
            "?localization=false&tickers=false&market_data=false"
            "&community_data=false&developer_data=false"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        img_url = data["image"]["large"]
        req2 = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=8) as r2:
            dest.write_bytes(r2.read())
        return True
    except Exception:
        return False


def _search_coingecko_id(asset: str) -> Optional[str]:
    try:
        url = f"https://api.coingecko.com/api/v3/search?query={asset}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        for coin in data.get("coins", [])[:5]:
            if coin.get("symbol", "").upper() == asset.upper():
                return coin["id"]
    except Exception:
        pass
    return None
