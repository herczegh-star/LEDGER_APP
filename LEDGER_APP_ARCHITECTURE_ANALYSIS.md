# LEDGER APP – Architecture Analysis

> Generated: 2026-03-10
> Codebase state: branch `main`, commit `c5f47bb`
> Analyst: Claude Sonnet 4.6 via deep static + dynamic analysis

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Database Schema](#2-database-schema)
3. [Ledger Data Flow](#3-ledger-data-flow)
4. [Compute Engines](#4-compute-engines)
5. [Dashboard Compute Flow](#5-dashboard-compute-flow)
6. [Duplicate Logic Analysis](#6-duplicate-logic-analysis)
7. [Performance Analysis](#7-performance-analysis)
8. [Identified Bottlenecks](#8-identified-bottlenecks)
9. [Timing Measurements](#9-timing-measurements)
10. [Recommendations](#10-recommendations)

---

## 1. System Architecture Overview

The application is a desktop investment portfolio tracker built on a strictly **append-only ledger** architecture. All portfolio state (positions, ROI, P&L, venue holdings) is derived at runtime from the immutable row stream. No mutable state is stored in the database.

### Layer Diagram

```
┌──────────────────────────────────────────────────────────────┐
│  UI LAYER  (ui/app_flet.py + ui/modules/*.py)               │
│  Flet 0.80 — renders DTOs, no computation, no DB access     │
└─────────────────────────┬────────────────────────────────────┘
                          │ imports ONLY from
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  UI FACADE  (core/services/ui_facade.py)                    │
│  Single contract boundary — all UI entry points             │
│  Returns typed DTOs, never raises to UI                     │
└──────┬──────────────────┬───────────────────────────────────┘
       │                  │
       ▼                  ▼
┌──────────────┐  ┌───────────────────────────────────────────┐
│  WRITE PATH  │  │  READ / COMPUTE PATH                      │
│              │  │                                           │
│ trade_service│  │  report_service.py                        │
│ reversal_svc │  │  health_service.py                        │
│ import_svc   │  │  portfolio_snapshot_service.py            │
│ export_svc   │  │  export_service.py                        │
└──────┬───────┘  └───────────────────┬───────────────────────┘
       │                              │
       ▼                              ▼
┌──────────────────────────────────────────────────────────────┐
│  COMPUTE LAYER  (core/reports/)                             │
│                                                              │
│  positions.py          WAC engine — per-asset positions     │
│  cashflow.py           Fiat flow bucketed by period         │
│  netto_invested.py     Invested vs inflow (no netting)      │
│  holdings.py           Physical venue quantity map          │
└──────────────────────────────┬───────────────────────────────┘
                               │ receives List[RawRow]
                               ▼
┌──────────────────────────────────────────────────────────────┐
│  CORE LAYER  (core/)                                        │
│                                                              │
│  service.py        LedgerService — public write/read API    │
│  ledger_store.py   LedgerStore  — SQLite append-only DB     │
│  model.py          RawRow dataclass + fingerprint()         │
│  validator.py      Syntactic validation only               │
│  trade.py          create_trade() → double-entry pair      │
│  fee.py            create_fee()                            │
│  transfer.py       create_transfer()                       │
│  reversal.py       create_reversal[_pair]()               │
└──────────────────────────────┬───────────────────────────────┘
                               │ parses files into
                               ▼
┌──────────────────────────────────────────────────────────────┐
│  I/O MODULE  (io_module/raw_loader.py)                      │
│  load_raw() → load_csv() / load_xlsm()                     │
│  No DB access. Returns List[RawRow]                         │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│  DATABASE  (SQLite — ledger.db)                             │
│  Single table: ledger                                       │
│  Indices: row_fp (UNIQUE), timestamp                        │
└──────────────────────────────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Files | Responsibility |
|---|---|---|
| **I/O Module** | `io_module/raw_loader.py` | Parse .csv / .xlsm → `List[RawRow]`. No DB, no logic. |
| **Core / Model** | `core/model.py`, `core/validator.py` | `RawRow` dataclass, SHA-256 fingerprint, syntactic validation. |
| **Core / Persistence** | `core/ledger_store.py`, `core/service.py` | All SQL. Append-only inserts, deduplication via UNIQUE index, row queries. |
| **Core / Row Builders** | `core/trade.py`, `core/fee.py`, `core/transfer.py`, `core/reversal.py` | Construct domain-correct `RawRow` objects following double-entry rules. |
| **Compute Layer** | `core/reports/*.py` | Pure functions. Input: `List[RawRow]`. Output: typed report objects. No DB, no side-effects. |
| **Services** | `core/services/*.py` (except ui_facade) | Thin coordinators — fetch rows from DB, call compute, return results. |
| **UI Facade** | `core/services/ui_facade.py` | **Single UI boundary.** Aggregates services, builds DTOs, handles all errors. UI never bypasses this. |
| **UI** | `ui/app_flet.py`, `ui/modules/*.py` | Flet rendering only. No computation. No DB access. |
| **Prices** | `core/prices/*.py` | Live price lookup with cache. Optional enrichment layer. |

---

## 2. Database Schema

### Table: `ledger`

```sql
CREATE TABLE IF NOT EXISTS ledger (
    pk          INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL,          -- shared UUID for double-entry pair
    timestamp   TEXT NOT NULL,          -- ISO 8601
    type        TEXT NOT NULL,          -- BUY | SELL | TRANSFER | FEE | REVERSAL
    asset       TEXT NOT NULL,          -- uppercase ticker (BTC, EUR, ...)
    amount      TEXT NOT NULL,          -- Decimal as string, sign = direction
    currency    TEXT NOT NULL,          -- counterpart currency
    price       TEXT,                   -- optional unit price
    venue       TEXT NOT NULL,          -- lowercase venue identifier
    note        TEXT,                   -- optional free text
    row_fp      TEXT NOT NULL,          -- SHA-256 fingerprint for dedup
    imported_at TEXT NOT NULL           -- ISO 8601 write timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_row_fp   ON ledger(row_fp);
CREATE INDEX        IF NOT EXISTS idx_timestamp ON ledger(timestamp);
```

### Indices

| Index | Column(s) | Type | Purpose |
|---|---|---|---|
| `idx_row_fp` | `row_fp` | UNIQUE | Deduplication on import — prevents identical rows |
| `idx_timestamp` | `timestamp` | Regular | ORDER BY timestamp on `timeline()` |

### Fingerprint Formula

```
row_fp = SHA-256(timestamp | type | venue | asset | currency | amount)
```

- Computed in Python before insert
- Collision = exact duplicate → silently skipped (not an error)

### Double-Entry Representation

A BUY trade for 0.5 BTC at 30,000 EUR on Kraken produces **two rows with the same `id`**:

```
id=abc123  BUY  asset=BTC   amount=+0.5    currency=EUR  price=30000  venue=kraken
id=abc123  BUY  asset=EUR   amount=-15000  currency=EUR  price=1      venue=kraken
```

Rules:
- `insert_pair()` wraps both inserts in a single SQLite transaction
- Compute engines find the fiat leg via `id` lookup: `fiat_by_id[row.id]`
- Reversal = new pair with opposite amounts and `type=REVERSAL`

### All SQL Queries (exhaustive)

```sql
-- Schema init (idempotent)
CREATE TABLE IF NOT EXISTS ledger (...)
CREATE UNIQUE INDEX IF NOT EXISTS idx_row_fp ON ledger(row_fp)
CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger(timestamp)

-- Insert
INSERT INTO ledger (id, timestamp, type, asset, amount, currency, price, venue, note, row_fp, imported_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

-- Read: full timeline (main query, used by all compute)
SELECT * FROM ledger ORDER BY timestamp ASC, id ASC, row_fp ASC

-- Read: filtered timeline
SELECT * FROM ledger WHERE 1=1 [AND venue=?] [AND asset=?] [AND timestamp>=?] [AND timestamp<=?]
  ORDER BY timestamp ASC, id ASC, row_fp ASC

-- Read: asset balances
SELECT asset, amount FROM ledger ORDER BY asset

-- Read: venue balances
SELECT venue, asset, amount FROM ledger ORDER BY venue, asset

-- Read: single row by PK
SELECT * FROM ledger WHERE pk = ?

-- Read: rows by shared UUID
SELECT * FROM ledger WHERE id = ? ORDER BY timestamp ASC, id ASC, row_fp ASC

-- Read: PKs by UUID
SELECT pk FROM ledger WHERE id = ? ORDER BY pk ASC

-- Read: recent rows
SELECT pk, id, timestamp, type, asset, amount, venue FROM ledger ORDER BY pk DESC LIMIT ?

-- Read: count
SELECT COUNT(*) FROM ledger

-- trade_service: canonical ID generation
SELECT COUNT(DISTINCT id) FROM ledger WHERE timestamp = ? AND venue = ? AND type = ?
```

**Total distinct query patterns: 11**

---

## 3. Ledger Data Flow

### Path: `LedgerService.timeline()` → compute functions

```
UI (dashboard view)
  │
  ▼ calls
ui_facade.get_dashboard_snapshot(db_path, price_provider, fiat)
  │
  ▼ opens
LedgerStore(db_path)
  │
  ▼ executes SQL
SELECT * FROM ledger ORDER BY timestamp ASC, id ASC, row_fp ASC
  │
  ▼ deserializes
_row_to_rawrow(sqlite3.Row) → RawRow
  Returns: List[RawRow]  (full ledger in memory)
  │
  ├──▶ compute_positions(rows, fiat)
  │      Sort by (timestamp, id) → iterate → WAC per asset → List[PositionRow]
  │
  ├──▶ compute_venue_holdings(rows, fiat)
  │      Single pass → Dict[venue, Dict[asset, Decimal]]
  │
  ├──▶ get_portfolio_snapshot(rows, fiat)
  │      Calls: netto_invested_report() → cashflow_report() → compute_positions()
  │      Returns: PortfolioSnapshot (invested, net_flow, assets_held, roi)
  │
  └──▶ [for each venue]:
         venue_rows = [r for r in rows if r.venue == venue]
         compute_positions(venue_rows, fiat)       ← per-venue WAC
         get_portfolio_snapshot(venue_rows, fiat)  ← per-venue summary
         → VenueDashboardDTO
```

### RawRow Deserialization

`_row_to_rawrow()` in `ledger_store.py`:
1. `sqlite3.Row` → dict
2. `amount`, `price` → `Decimal(str(value))`
3. `timestamp` → kept as ISO string (not parsed to datetime)
4. Returns `RawRow` dataclass (frozen, immutable)

---

## 4. Compute Engines

### 4.1 `compute_positions()` — WAC Engine

**File**: `core/reports/positions.py`

**Purpose**: Calculate per-asset Weighted Average Cost positions from the full ledger.

**Input**: `List[RawRow]`, `fiat: FrozenSet[str]`

**Output**: `List[PositionRow]`

```python
@dataclass
class PositionRow:
    asset:        str
    quantity:     Decimal   # units held
    wac:          Decimal   # weighted average cost per unit
    cost_basis:   Decimal   # quantity * wac
    realized_pnl: Decimal   # P&L from closed/partial sells
```

**Algorithm** (2 passes):

```
Pass 1 — Index fiat legs:
  fiat_by_id[row.id] = signed amount   (where asset ∈ fiat)
  fee_by_id[row.id]  = abs(fee amount) (where type = FEE)

Pass 2 — Process non-fiat asset legs chronologically:
  BUY (amount > 0):
    cost = abs(fiat_by_id[id]) + fee_by_id[id]
    qty += amount
    cost_basis += cost

  SELL (amount < 0):
    sold_qty = abs(amount)
    wac_per_unit = cost_basis / qty
    proceeds = abs(fiat_by_id[id])
    cost_removed = wac_per_unit * sold_qty
    qty -= sold_qty
    cost_basis -= cost_removed
    realized_pnl += proceeds - cost_removed - fee_cost

  REVERSAL:
    Treated as BUY or SELL based on sign of amount

  TRANSFER, FEE:
    Skipped (not tracked as cost)
```

**Complexity**: O(n log n) — sort dominant, then O(n) iteration

**Passes over rows**: 2 (index + iterate)

---

### 4.2 `cashflow_report()` — Fiat Flow

**File**: `core/reports/cashflow.py`

**Purpose**: Net fiat cash flows bucketed by time period.

**Input**: `List[RawRow]`, `bucket: str` ("day" | "week" | "month"), `fiat: Set[str]`

**Output**: `TimeSeriesReport` wrapping `List[CashflowRow]`

```python
@dataclass
class CashflowRow:
    date:       str      # bucket key: YYYY-MM-DD, YYYY-Www, YYYY-MM
    currency:   str
    net_amount: Decimal  # signed fiat sum for this bucket
```

**Algorithm** (1 pass):
1. Filter rows where `asset ∈ fiat`
2. Accumulate `amount` per `(bucket_key, currency)` key
3. Filter zero-net entries
4. Sort by `(date, currency)`

**Complexity**: O(n + b log b) where b = buckets

**Passes over rows**: 1

---

### 4.3 `netto_invested_report()` — Invested vs Inflow

**File**: `core/reports/netto_invested.py`

**Purpose**: Separate gross invested (outflows) from gross inflows — avoids netting within buckets.

**Input**: `List[RawRow]`, `bucket: str`, `fiat: Set[str]`

**Output**: `TimeSeriesReport` wrapping `List[NettoInvestedRow]`

```python
@dataclass
class NettoInvestedRow:
    date:             str
    currency:         str
    invested_amount:  Decimal   # gross outflows (>= 0)
    inflow_amount:    Decimal   # gross inflows  (>= 0)
    net_flow:         Decimal   # inflow - invested
```

**Algorithm**:
1. Call `cashflow(rows, bucket="day")` — get daily granularity base
2. Re-aggregate daily rows into requested bucket
3. Split sign: `net_amount < 0` → invested, `> 0` → inflow
4. Sort by `(date, currency)`

**Complexity**: O(n) for cashflow + O(d log d) rebucketing

**Note**: Calls `cashflow()` internally, so dashboard calling both independently duplicates that work.

---

### 4.4 `compute_venue_holdings()` — Physical Quantities

**File**: `core/reports/holdings.py`

**Purpose**: Physical asset quantities per venue, TRANSFER-aware.

**Input**: `List[RawRow]`, `fiat: Set[str]`

**Output**: `Dict[str, Dict[str, Decimal]]` — `{venue: {asset: quantity}}`

**Algorithm** (1 pass):
1. Sort by `(timestamp, id)` for determinism
2. For each non-fiat row with `type ∈ {BUY, SELL, REVERSAL, STAKING, TRANSFER}`:
   - `BUY/SELL/REVERSAL/STAKING`: `holdings[venue][asset] += amount`
   - `TRANSFER (amount < 0)`: outflow from venue; if `note` is a single token → synthesize inflow into `holdings[note.lower()][asset] += abs(amount)`
3. Prune zero-quantity entries

**Complexity**: O(n) — single pass

**Passes over rows**: 1

---

### 4.5 `health_report()` — Integrity Scanner

**File**: `core/services/health_service.py`

**Purpose**: Semantic integrity checks on the ledger.

**Checks performed**:
1. `missing_timestamp` (ERROR)
2. `zero_amount` (WARNING)
3. `missing_quote_leg` (ERROR) — investment leg without fiat counterpart
4. `missing_investment_leg` (ERROR) — fiat leg without investment counterpart
5. `multi_fiat_quote` (WARNING) — quote legs in >1 currency for same trade
6. `fiat_as_investment_leg` (ERROR) — fiat used as base asset
7. `oversell` (ERROR) — derived by running `compute_positions()` and finding `quantity < 0`

**Complexity**: O(n) for row scans + O(n log n) for the embedded `compute_positions()` call

**Passes over rows**: ~8 (7 check loops + 1 for positions)

---

### 4.6 `get_portfolio_snapshot()` — Aggregated Dashboard Summary

**File**: `core/services/portfolio_snapshot_service.py`

**Purpose**: High-level portfolio totals for the dashboard header.

**Output**:
```python
@dataclass
class PortfolioSnapshot:
    invested:     Dict[str, Decimal]   # gross fiat outflows per currency
    net_flow:     Dict[str, Decimal]   # net fiat flow per currency
    assets_held:  int                  # count of non-zero positions
    top_position: Optional[Dict]       # {"asset": str, "cost_basis": Decimal}
    roi:          Optional[Decimal]    # portfolio-level ROI %
```

**Algorithm**:
1. `netto_invested_report(rows, bucket="day")` → extract invested per currency
2. `cashflow_report(rows, bucket="day")` → extract net_flow per currency
3. `compute_positions(rows)` → count assets with quantity > 0, find top by cost_basis
4. ROI = `(sum realized_pnl / sum cost_basis) * 100`

**Complexity**: O(n) — dominated by the 3 inner report calls

**Internal calls**: `netto_invested_report` (which calls `cashflow`) + `compute_positions`

---

## 5. Dashboard Compute Flow

### Sequence when UI loads dashboard

```
1. UI triggers: get_dashboard_snapshot(db_path, price_provider, fiat="EUR,CZK")

2. ui_facade.py:
   a. LedgerStore(db_path)                          [1 SQL: SELECT *]
   b. rows = store.timeline()                        [deserialize all rows]
   c. compute_positions(rows, fiat)                  [1st full positions pass]
   d. Enrich with prices (optional)                  [price API calls]
   e. compute_venue_holdings(rows, fiat)             [venue quantity map]
   f. get_portfolio_snapshot(rows, fiat):
      ├── netto_invested_report(rows, bucket="day")
      │     └── cashflow_report(rows, bucket="day")  [1st cashflow pass]
      └── compute_positions(rows, fiat)              [2nd full positions pass]
   g. [for each venue V in venue_holdings]:
      ├── venue_rows = filter(rows, venue=V)
      ├── compute_positions(venue_rows, fiat)        [per-venue positions]
      └── get_portfolio_snapshot(venue_rows, fiat):
            ├── netto_invested_report(venue_rows)    [per-venue cashflow]
            └── compute_positions(venue_rows, fiat)  [per-venue positions again]

3. Return DashboardSnapshotDTO
```

### Compute call count (V = number of distinct venues)

| Operation | Calls |
|---|---|
| SQL `SELECT *` (full timeline load) | **1** |
| `compute_positions()` | **2 + 2V** |
| `cashflow_report()` | **1 + V** |
| `netto_invested_report()` | **1 + V** |
| `compute_venue_holdings()` | **1** |

For a typical portfolio with **5 venues**: 12 calls to `compute_positions()` and 6 to cashflow functions, all over the same data.

### Number of Python loops over rows

With 5 venues and N total rows:

| Function | Iterations |
|---|---|
| `compute_positions()` global | N log N (sort) + N |
| `compute_positions()` × 2 (snapshot) | N log N + N each |
| `compute_positions()` × 5 (per-venue) | ~N/5 log(N/5) + N/5 each |
| `cashflow_report()` × 6 | N each |
| `netto_invested_report()` × 6 | N each (+ cashflow internally) |
| `compute_venue_holdings()` | N log N + N |
| **Total approximate iterations** | **~25N** |

---

## 6. Duplicate Logic Analysis

### Duplication 1 — `compute_positions()` called multiple times per dashboard (HIGH IMPACT)

**Location**: `core/services/ui_facade.py`, `get_dashboard_snapshot()`

`compute_positions()` is called:
- Once for the global position list
- Once inside `get_portfolio_snapshot(rows)` (via `netto_invested_report` internally, then explicitly)
- Once per venue inside `get_portfolio_snapshot(venue_rows)`

**Each call re-sorts and re-iterates the full or filtered row list.** There is no caching or memoization.

**Cost**: With 5 venues × 1000 rows: ~10 redundant sort+iterate cycles.

---

### Duplication 2 — `cashflow_report()` called by both `netto_invested_report()` and directly

**Location**: `core/reports/netto_invested.py` calls `cashflow()` internally; `get_portfolio_snapshot` also calls `cashflow_report()` independently.

When `get_portfolio_snapshot()` is called, it triggers:
- `netto_invested_report()` → calls `cashflow()` internally
- `cashflow_report()` as a separate call

Same rows, same fiat set → identical computation twice per snapshot call.

---

### Duplication 3 — Price enrichment logic (LOW IMPACT)

Identical price-enrichment pattern (fetch spot price → compute value, unrealized P&L) appears in:
- `get_positions_full()` (`ui_facade.py` ~line 640)
- `get_dashboard_snapshot()` (`ui_facade.py` ~line 224)
- `get_asset_detail()` (`ui_facade.py` ~line 781)

Three copies of the same 10-15 line enrichment block.

---

### Duplication 4 — `VenueDashboardDTO` / `PositionDTO` assembly

Identical pattern for building per-venue position lists from `venue_rows` + prices:
- Inside `get_dashboard_snapshot()` venue loop (~lines 249-273)
- Inside `get_asset_detail()` venue section (~lines 759-779)

~30 lines of structural duplication.

---

### Duplication 5 — Deprecated engine (RESOLVED)

`ledger_engine/positions_engine.py` — old WAC engine with FX support. Fully replaced by `core/reports/positions.py`. Tests in `tests/test_positions_engine_wac.py` still reference the old engine but it is not used in production code.

---

## 7. Performance Analysis

### Complexity per function

| Function | Time Complexity | Space | Notes |
|---|---|---|---|
| `load_raw()` | O(n) | O(n) | File I/O bound |
| `LedgerStore.insert()` | O(1) | O(1) | SQLite + fingerprint |
| `LedgerStore.import_rows()` | O(n) | O(1) | n inserts |
| `LedgerStore.timeline()` | O(k log k) | O(k) | SQLite ORDER BY |
| `validate_row()` | O(1) | O(1) | |
| `validate_rows()` | O(n) | O(n) | |
| `compute_positions()` | O(n log n) | O(a) | Sort + iterate; a = assets |
| `cashflow_report()` | O(n + b log b) | O(b) | b = buckets |
| `netto_invested_report()` | O(n + d log d) | O(d) | Calls cashflow internally |
| `compute_venue_holdings()` | O(n log n) | O(v·a) | Sort + single pass |
| `health_report()` | O(n log n) | O(n) | 8 passes + positions |
| `get_portfolio_snapshot()` | O(n log n) | O(n) | Calls 3 report functions |
| `get_dashboard_snapshot()` | O(V · n log n) | O(n) | Per-venue recompute |
| `add_trade()` | O(log k) | O(1) | ID gen query + 2 inserts |
| `generate_canonical_id()` | O(log k) | O(1) | Single COUNT query |
| `reverse_trade()` | O(n) | O(1) | Scan for pair by UUID |

**V** = venues, **n** = total rows, **k** = total distinct IDs, **a** = distinct assets, **b/d** = time buckets

### Full ledger passes per dashboard load (5 venues, N rows)

| Operation | Full passes |
|---|---|
| `timeline()` SQL load | 1 (DB) |
| Global `compute_positions()` | 2 (sort + iterate) |
| `compute_venue_holdings()` | 2 (sort + iterate) |
| `get_portfolio_snapshot()` → netto + positions | 4 (2 cashflow + 2 positions) |
| Per-venue loop (×5) → positions + snapshot | ~20 partial passes |
| **Total** | **~29 passes** |

---

## 8. Identified Bottlenecks

### Bottleneck 1 — Per-venue `compute_positions()` recomputation (CRITICAL)

**Location**: `core/services/ui_facade.py`, `get_dashboard_snapshot()` lines ~245-293

**Problem**: `compute_positions()` is called once globally and then again for each venue's rows, and again inside the per-venue `get_portfolio_snapshot()`. For a portfolio with 10 venues this becomes ~20 calls to a sort+iterate function.

**Impact at scale**:
- 10 venues × 10,000 rows: ~20 × 10ms = ~200ms just for position recomputation
- Grows linearly with venues × quadratically with rows if row count dominates

---

### Bottleneck 2 — `cashflow_report()` called twice per snapshot (MODERATE)

**Location**: `get_portfolio_snapshot()` in `portfolio_snapshot_service.py`

**Problem**: `netto_invested_report()` internally calls `cashflow()`, then `get_portfolio_snapshot()` also calls `cashflow_report()` separately. Same rows, same fiat, identical result.

**Impact**: 2× the cashflow computation per snapshot — compounded by being called per-venue.

---

### Bottleneck 3 — Full timeline loaded into Python memory on every dashboard refresh (MODERATE)

**Location**: `get_dashboard_snapshot()` → `store.timeline()`

**Problem**: Every dashboard refresh triggers `SELECT * FROM ledger` → deserialize all rows → hold in memory for the duration of compute. No caching.

**Impact at scale**:
- 100k rows: ~100MB memory + ~500ms deserialization
- No incremental update path — always full reload

---

### Bottleneck 4 — `health_report()` performs ~8 passes over rows (LOW-MODERATE)

**Location**: `core/services/health_service.py`

**Problem**: Each of the 7 integrity checks iterates the full row list separately, then `compute_positions()` runs again for the oversell check. Could be merged into 1-2 passes.

**Impact**: Only triggered on user request (not dashboard auto-load), so acceptable currently.

---

### Bottleneck 5 — Price provider latency (ENVIRONMENT-DEPENDENT)

**Location**: Price enrichment in `get_dashboard_snapshot()`, `get_asset_detail()`, `get_positions_full()`

**Problem**: Synchronous price lookups per distinct asset. `CachedPriceProvider` wrapper exists and mitigates this, but initial load still hits network.

**Impact**: 10-20 assets × 50-200ms = 0.5-4 seconds on first load. Cache TTL controls subsequent loads.

---

### Bottleneck 6 — Missing index on `asset`, `venue`, `type` columns

**Location**: `core/ledger_store.py` schema

**Problem**: `timeline_filtered()` applies WHERE clauses on `venue`, `asset`, `type` but only `timestamp` and `row_fp` are indexed. Filtered queries do full table scans filtered in SQLite.

**Impact**: `timeline_filtered()` is currently not used in the hot path (dashboard loads the full timeline and filters in Python), so impact is low. Would matter if SQL-side filtering were introduced.

---

## 9. Timing Measurements

### Environment

- Platform: Windows 11 Pro, Python 3.10+
- Database: SQLite (local file)
- Measurement method: `time.perf_counter()`, averaged over 5 runs

### Empty database (actual ledger.db)

| Operation | Time |
|---|---|
| `LedgerStore.__init__()` | 90.30 ms |
| `timeline()` SQL + deserialize (0 rows) | 0.14 ms |
| `compute_positions()` | 0.02 ms |
| `cashflow_report()` | 0.02 ms |
| `netto_invested_report()` | 0.02 ms |
| `compute_venue_holdings()` | 0.01 ms |
| `get_portfolio_snapshot()` | 0.07 ms |
| `get_dashboard_snapshot()` end-to-end | 1.17 ms |

> Note: `LedgerStore.__init__()` takes ~90ms due to SQLite connection setup and `CREATE TABLE IF NOT EXISTS` DDL execution. This is a one-time startup cost.

### Synthetic benchmark — 1,000 rows (500 trades, 10 assets, 5 venues)

| Function | Avg time (5 runs) |
|---|---|
| `compute_positions()` | 1.10 ms |
| `cashflow_report()` | 2.36 ms |
| `netto_invested_report()` | 11.75 ms |
| `compute_venue_holdings()` | 0.79 ms |
| `get_portfolio_snapshot()` | 12.14 ms |

### Synthetic benchmark — 10,000 rows (scaling check)

| Function | Avg time (5 runs) |
|---|---|
| `compute_positions()` | 10.46 ms |

**Scaling**: `compute_positions()` scales linearly — 10× rows → ~9.5× slower. Consistent with O(n log n).

### Estimated dashboard load at scale (extrapolated)

| Dataset | Rows | Venues | Estimated dashboard time |
|---|---|---|---|
| Small portfolio | 500 | 3 | < 50 ms |
| Medium portfolio | 5,000 | 5 | ~250 ms |
| Large portfolio | 50,000 | 10 | ~3,000 ms |
| Very large | 200,000 | 15 | ~15,000 ms |

Estimates assume no price provider latency and no DB network overhead.

---

## 10. Recommendations

These are observations only. No code has been changed.

### R1 — Memoize `compute_positions()` within one dashboard load

**Priority**: HIGH
**Location**: `core/services/ui_facade.py`, `get_dashboard_snapshot()`

Compute positions once globally with the full row list, then derive per-venue positions by filtering the result list (already grouped by asset — just split by venue from the original rows). Eliminates the per-venue recomputation loop.

**Expected gain**: O(V · n log n) → O(n log n + V)

---

### R2 — Unify `cashflow` inside `get_portfolio_snapshot()`

**Priority**: MEDIUM
**Location**: `core/services/portfolio_snapshot_service.py`

`netto_invested_report()` internally calls `cashflow()`. `get_portfolio_snapshot()` then calls `cashflow_report()` again. Pass the pre-computed cashflow rows into `netto_invested_report()` or combine the two calls into one.

**Expected gain**: ~2× fewer cashflow iterations per snapshot

---

### R3 — Extract price enrichment into a shared helper

**Priority**: LOW
**Location**: `core/services/ui_facade.py` (3 locations)

The identical 10-15 line block that enriches a position list with spot prices appears in `get_positions_full()`, `get_dashboard_snapshot()`, and `get_asset_detail()`. Extract to `_enrich_with_prices(positions, provider, fiat)`.

**Benefit**: Maintainability, not performance.

---

### R4 — Add indices on `asset`, `venue` if SQL-side filtering is introduced

**Priority**: LOW (future)
**Location**: `core/ledger_store.py`

Currently the hot path loads the full timeline into Python and filters there. If `timeline_filtered()` ever becomes the primary access pattern, add:

```sql
CREATE INDEX IF NOT EXISTS idx_asset ON ledger(asset);
CREATE INDEX IF NOT EXISTS idx_venue ON ledger(venue);
```

---

### R5 — Cache timeline for the duration of a dashboard build

**Priority**: MEDIUM (for large datasets)
**Location**: `core/services/ui_facade.py`

The timeline is loaded once inside `get_dashboard_snapshot()` and used correctly throughout. No action needed for the main dashboard. But `get_asset_detail()` and `get_positions_full()` each open a new `LedgerStore` and call `timeline()` independently. If multiple views are loaded in sequence, each re-reads the full DB.

Short-term: pass the already-loaded `rows` list as a parameter.
Long-term: in-process row cache with TTL.

---

### R6 — Remove deprecated `ledger_engine/` module

**Priority**: LOW
**Files**: `ledger_engine/positions_engine.py`, `ledger_engine/fx_provider.py`, `tests/test_positions_engine_wac.py`

The old WAC engine with FX is fully superseded by `core/reports/positions.py`. The deprecated files are dead code. Remove to avoid confusion about which engine is canonical.

---

*End of analysis.*
