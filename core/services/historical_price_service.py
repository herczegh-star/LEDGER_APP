"""Historical price backfill service.

backfill_historical_prices() is the ONLY supported way to populate
historical_prices. It is a one-shot, idempotent cache-fill that only ever
WRITES the (asset, date) pairs that were actually missing. An asset with
nothing missing is skipped entirely — zero HTTP calls — so re-running
this after a successful backfill costs nothing.

Chunking (added after the Etapa 1 validation found the anonymous
CoinGecko tier rejects a single request spanning >=365 days): the
requested [since, as_of] range is split into CHUNK_DAYS-wide (default
180) windows, each fetched with ONE
core.prices.coingecko.CoinGeckoPriceProvider.get_historical_prices_range()
call — never one request per day. A chunk with nothing missing in its
own date span is skipped (no HTTP call for it at all). Consecutive
chunks may overlap by a day at the boundary; this is harmless because
historical_prices' PRIMARY KEY (asset, date) deduplicates — the second
write for an overlapping date is simply ignored (see
core/historical_price_store.py's set_price()).

A chunk that fails (see core/prices/coingecko.py's bounded 429 retry,
or a hard failure like the anonymous tier's 365-day wall) does not abort
the asset or the run — the other chunks and other assets are still
attempted, and whatever a partially-successful asset DID receive is kept
(see assets_partial on BackfillResultDTO).

This module does NOT compute positions, WAC, or portfolio value — it only
fills the historical_prices cache that core/services/portfolio_history_service.py
later reads from. No accounting logic lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterator, List, Optional, Tuple

from core.historical_price_store import HistoricalPrice, HistoricalPriceStore
from core.ledger_store import LedgerStore

_FIAT_DEFAULT = frozenset({"EUR", "CZK"})
CHUNK_DAYS = 180  # per-request window size — comfortably under the 365-day wall found in validation


@dataclass
class BackfillResultDTO:
    """Outcome of one backfill_historical_prices() call. Never raises —
    per-asset/per-chunk failures are recorded here instead."""

    assets_considered: List[str] = field(default_factory=list)
    assets_already_complete: List[str] = field(default_factory=list)  # nothing missing, no HTTP call made
    assets_fetched: List[str] = field(default_factory=list)           # every missing date for this asset got stored
    assets_partial: List[str] = field(default_factory=list)           # SOME missing dates stored, at least one chunk failed
    assets_failed: List[str] = field(default_factory=list)            # no missing date could be stored at all
    dates_stored: int = 0


def _ledger_assets_and_earliest_date(db_path: str, fiat: frozenset) -> tuple:
    """Return (sorted list of non-fiat assets, earliest row date) from the
    ledger. (set(), None) when the ledger is empty."""
    store = LedgerStore(db_path)
    try:
        rows = store.timeline()
    finally:
        store.close()

    if not rows:
        return [], None

    assets = sorted({r.asset.upper() for r in rows if r.asset.upper() not in fiat})
    earliest = min(r.timestamp for r in rows).date()
    return assets, earliest


def _date_chunks(since: date, as_of: date, chunk_days: int = CHUNK_DAYS) -> Iterator[Tuple[date, date]]:
    """Yield (chunk_from, chunk_to) inclusive date pairs covering
    [since, as_of], each at most chunk_days days wide. The last chunk may
    be narrower. Consecutive chunks are contiguous (no gap, no overlap by
    construction) — see this module's docstring for why a boundary
    overlap would be harmless anyway if one were ever introduced."""
    chunk_start = since
    while chunk_start <= as_of:
        chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), as_of)
        yield chunk_start, chunk_end
        chunk_start = chunk_end + timedelta(days=1)


def backfill_historical_prices(
    db_path: str,
    provider,
    assets: Optional[List[str]] = None,
    fiat: str = "CZK",
    since: Optional[date] = None,
    as_of: Optional[date] = None,
) -> BackfillResultDTO:
    """Fill historical_prices for *assets* over [since, as_of], skipping
    whatever is already cached.

    Args:
        db_path:  Path to the SQLite ledger database (also holds
                  historical_prices — same file, separate table).
        provider: An object with a get_historical_prices_range(asset, fiat,
                  date_from, date_to) method (see
                  core.prices.coingecko.CoinGeckoPriceProvider).
                  Deliberately NOT typed as the shared PriceProvider
                  interface — see that module's docstring for why.
        assets:   Assets to backfill. Defaults to every non-fiat asset
                  that appears anywhere in the ledger.
        fiat:     Target fiat currency (default "CZK", matching the
                  historical_prices.price_czk column).
        since:    Start of the range. Defaults to the ledger's earliest
                  row date (nothing to backfill before any history
                  exists). Ignored (no-op) when the ledger is empty and
                  assets is also None.
        as_of:    End of the range (inclusive). Defaults to today.

    Returns:
        BackfillResultDTO — never raises. The range is split into
        CHUNK_DAYS-wide requests (see _date_chunks()); a chunk that fails
        for one asset (network error, rate limit exhausted, the
        anonymous-tier 365-day wall) does not abort that asset's other
        chunks or any other asset — see assets_partial/assets_failed.
    """
    fiat_set = frozenset(a.upper() for a in _FIAT_DEFAULT)
    result = BackfillResultDTO()

    ledger_assets, earliest_ledger_date = _ledger_assets_and_earliest_date(db_path, fiat_set)

    if assets is None:
        assets = ledger_assets
    if since is None:
        since = earliest_ledger_date
    if as_of is None:
        as_of = date.today()

    if not assets or since is None:
        return result  # nothing to backfill (e.g. empty ledger, no assets given)

    if since > as_of:
        return result

    wanted_dates = {since + timedelta(days=i) for i in range((as_of - since).days + 1)}
    chunks = list(_date_chunks(since, as_of))

    store = HistoricalPriceStore(db_path)
    try:
        now = datetime.now()
        for asset in assets:
            result.assets_considered.append(asset)
            existing_dates = store.get_existing_dates(asset)
            missing_dates = wanted_dates - existing_dates
            if not missing_dates:
                result.assets_already_complete.append(asset)
                continue

            dates_stored_for_asset = 0

            for chunk_from, chunk_to in chunks:
                chunk_dates = {
                    chunk_from + timedelta(days=i) for i in range((chunk_to - chunk_from).days + 1)
                }
                chunk_missing = chunk_dates & missing_dates
                if not chunk_missing:
                    continue  # nothing needed from this chunk — skip the HTTP call entirely

                fetched = provider.get_historical_prices_range(asset, fiat, chunk_from, chunk_to)
                if not fetched:
                    continue  # this chunk failed — try the remaining chunks anyway

                for price_date in chunk_missing:
                    price = fetched.get(price_date)
                    if price is None:
                        continue  # provider didn't cover this particular day — leave it missing, not 0
                    if not isinstance(price, Decimal):
                        continue  # defensive: never write a non-Decimal price
                    wrote = store.set_price(HistoricalPrice(
                        asset=asset, price_date=price_date, price_czk=price, fetched_at=now,
                    ))
                    if wrote:
                        dates_stored_for_asset += 1

            result.dates_stored += dates_stored_for_asset
            # Re-check against the store (not just this call's local bookkeeping) so
            # partial coverage WITHIN a single "successful" chunk — not just a whole
            # chunk failing outright — is still correctly reflected below.
            still_missing = missing_dates - store.get_existing_dates(asset)
            if not still_missing:
                result.assets_fetched.append(asset)
            elif dates_stored_for_asset == 0:
                result.assets_failed.append(asset)
            else:
                result.assets_partial.append(asset)
    finally:
        store.close()

    return result
