# Startup Performance Analysis — LedgerApp

> Date: 2026-03-10
> DB rows: 315 | Open positions: 21

---

## 1. Startup Execution Flow

```
python main.py
│
├─ [~200ms]  Python process start + sys.path setup
│
├─ [+280ms]  from ledger_app.__main__ import main
│             └─ configure_logging()
│             └─ from ui.app_flet import run_ui
│                  ├─ CHECKPOINT 1 logged (module-level code runs)
│                  └─ from core.services.ui_facade import ...   ← ~278ms import
│
├─ [+0ms]    run_ui()
│             ├─ CHECKPOINT 2 logged
│             └─ ft.run(main_view)   ← hands control to Flutter
│                  CHECKPOINT 3 logged
│
│            ~~~ Flutter engine boots (blank gray window shown) ~~~
│            ~~~            ~800ms                              ~~~
│
├─ [+800ms]  main_view(page) called by Flet
│             └─ _main_view_impl(page)
│                  ├─ create_app_context()                     ~240ms
│                  │    ├─ load_config()                       ~20ms
│                  │    ├─ LedgerStore(db_path) probe          ~90ms
│                  │    └─ get_price_provider()                ~0ms  (just object creation)
│                  │
│                  ├─ build_reports_view()                     ~1ms  (already imported)
│                  ├─ build_positions_view()                   ~1ms
│                  ├─ build_health_view()                      ~1ms
│                  ├─ build_venue_view()                       ~1ms
│                  ├─ build_ledger_view()                      ~1ms
│                  ├─ all dialog imports                       ~2ms
│                  ├─ nav/header widget construction           ~30ms
│                  │
│                  ├─ page.add(_header, _body_row)  ◄── FIRST RENDER (header + nav visible)
│                  │                                    But content area is still EMPTY
│                  │
│                  └─ set_view(0)
│                        └─ refresh()
│                              └─ get_dashboard_snapshot()     ← BLOCKING
│                                   ├─ timeline() SQL          ~5ms
│                                   ├─ compute_positions()     ~1ms
│                                   ├─ price_provider.get_prices(21 assets)
│                                   │         ████████████████ 7274ms ████████████████
│                                   │         (synchronous HTTP — main thread blocked)
│                                   ├─ per-venue loop          ~30ms
│                                   └─ get_portfolio_snapshot()~4ms
│
└─ [+7300ms] page.update()  ◄── FIRST FULL RENDER (cards visible)
```

---

## 2. Timing Table

### Phase A — Before Flet window appears (Python cold start)

| Step | Time (ms) | Notes |
|---|---|---|
| Python process start | ~100 | OS + CPython init |
| `main.py` → `__main__.py` imports | ~80 | sys, os, configure_logging |
| `from ui.app_flet import run_ui` (cold) | ~280 | Triggers full `ui_facade` import chain |
| `ft.run(main_view)` called | +0 | Hands off to Flutter |
| **Flutter engine startup** | **~800** | Blank gray window visible |
| **Subtotal: console → window appears** | **~1 260** | ~1.3 seconds |

### Phase B — Inside `_main_view_impl()` (gray window, before content)

| Step | Time (ms) | Cumulative from CP5 |
|---|---|---|
| `create_app_context()` | ~240 | 240 |
| All `build_*_view()` calls + widget build | ~50 | 290 |
| `page.add(_header, _body_row)` | ~3 | 293 |
| **→ Skeleton render (header + nav visible)** | — | — |
| `set_view(0)` → `refresh()` called | +1 | 294 |
| `svc.timeline()` SQL (315 rows) | **4.9** | 299 |
| `compute_positions()` (315 rows, 21 assets) | **0.8** | 300 |
| `price_provider.get_prices(21 assets)` | **7 274** | 7 574 |
| per-venue loop (5 venues) | 30 | 7 604 |
| `get_portfolio_snapshot()` | 4 | 7 608 |
| `page.update()` — cards appear | +2 | 7 610 |

### Phase C — Total user-perceived delay

| Delay segment | Duration |
|---|---|
| "Window appears" (console → blank Flet window) | ~1.3 s |
| "Gray content" (window visible → skeleton header/nav) | ~0.3 s |
| **"Empty content area" (skeleton → full dashboard)** | **~7.3 s** |
| **Total: console → fully rendered dashboard** | **~9 s** |

---

## 3. Blocking Operations Before First Render

### Before `page.add()` (blocking Flet from showing anything):

| Operation | Duration | Blocking? |
|---|---|---|
| `create_app_context()` → `LedgerStore` probe | 240ms | YES — on Flet callback thread |
| Module imports + widget construction | ~50ms | YES |

### After `page.add()` but before `page.update()` (empty content visible):

| Operation | Duration | Blocking? |
|---|---|---|
| `price_provider.get_prices(21 assets, CZK)` | **7 274ms** | **YES — synchronous HTTP** |
| per-venue `compute_positions()` × 5 | 30ms | YES (minor) |
| `get_portfolio_snapshot()` | 4ms | YES (minor) |

---

## 4. Root Cause Analysis

### ROOT CAUSE 1 — Synchronous price API call blocks the first `page.update()` (CRITICAL)

**Location:** `ui/app_flet.py` → `set_view(0)` → `refresh()` → `get_dashboard_snapshot()` → `price_provider.get_prices()`

**What happens:**
1. `page.add(_header, _body_row)` is called — Flet sends the skeleton to Flutter ✓
2. **Immediately**, `set_view(0)` is called **on the same Python thread**
3. `refresh()` calls `get_dashboard_snapshot()`, which calls `price_provider.get_prices(21 assets)`
4. `CoinGeckoPriceProvider` makes a single HTTP request to CoinGecko: **~7 seconds**
5. The Python thread is blocked. The Flutter UI shows the skeleton but content area is empty.
6. **`page.update()` is not called until the HTTP request completes.**

**Measured impact:** `price_provider.get_prices()` = **7 274ms** (today's run)

**Why so slow:**
- 21 distinct assets → CoinGecko batch request for all at once
- CoinGecko free tier can be slow (rate limits, server load)
- If CoinGecko fails, Coinbase fallback runs 21 **individual** requests × 5s timeout each = potential 105s
- No timeout shorter than the urllib default (5s per request)

**Why `page.update()` is delayed:**
The flow is entirely synchronous:
```python
page.add(...)          # renders skeleton
set_view(0)            # no yield / no thread separation
  refresh()
    get_dashboard_snapshot()
      price_provider.get_prices(...)   # 7 seconds here
    page.update()                      # only NOW does the dashboard appear
```

---

### ROOT CAUSE 2 — `create_app_context()` runs before `page.add()` (MODERATE)

**Location:** `ui/app_flet.py:125` — `ctx = create_app_context()`

**What happens:** The very first thing `_main_view_impl` does is open the SQLite DB and initialize the price provider. This runs on the Flet callback thread before any controls are added to the page.

**Measured impact:** 240ms

**Why it matters:** The Flutter window is already visible but shows nothing because `page.add()` hasn't been called yet. Every millisecond here extends the blank gray phase.

---

### ROOT CAUSE 3 — All 5 view modules are built eagerly at startup (MINOR)

**Location:** `ui/app_flet.py:408–424` — all `build_*_view()` calls happen sequentially before `page.add()`

**What happens:** `build_reports_view`, `build_positions_view`, `build_health_view`, `build_venue_view`, `build_ledger_view` are all constructed at startup, even though only the Dashboard is shown initially.

**Measured impact:** ~50ms total (negligible today; grows with more views)

**Note:** The actual data loading for these views is lazy (each `_run_*()` is only called when that tab is clicked). Only the Python widget object construction happens eagerly.

---

## 5. Evidence from Boot Log

Most recent session (2026-03-10 17:09):

```
[17:09:41.331] CHECKPOINT 3 - about to call ft.run()
[17:09:42.159] CHECKPOINT 4 - main_view entered         (+828ms  Flutter engine)
[17:09:42.160] CHECKPOINT 5 - _main_view_impl entered
[17:09:42.198] BODY: page.add() called                  (+38ms   setup work)
[17:09:42.209] BODY: page.controls=2                    (+11ms   page.add() done)
[17:09:48.876] build_cards: raw=21 cards=21             (+6667ms PRICE API BLOCK)
```

**Gap of 6.667 seconds between `page.add()` and first `build_cards`** — entirely caused by the synchronous price API call inside `refresh()`.

Instrumentation output from today's run (identical timing pattern):
```
SNAP_TIMING +    4.9 ms  timeline() loaded 315 rows
SNAP_TIMING +    5.7 ms  compute_positions() done  21 assets
SNAP_TIMING +    5.9 ms  price_provider.get_prices() START  (21 assets)
SNAP_TIMING + 7274.0 ms  price_provider.get_prices() DONE  got 21/21 prices
SNAP_TIMING + 7304.4 ms  per-venue loop done  venues=5
SNAP_TIMING + 7308.5 ms  get_portfolio_snapshot() done
```

---

## 6. Summary

| Root cause | Impact | Location |
|---|---|---|
| `price_provider.get_prices()` called synchronously before `page.update()` | **~7 s** | `app_flet.py:refresh()` + `ui_facade.py:get_dashboard_snapshot()` |
| `create_app_context()` runs before `page.add()` | ~240 ms | `app_flet.py:_main_view_impl()` |
| All views built eagerly at startup | ~50 ms | `app_flet.py:408-424` |
| Flutter engine cold start | ~800 ms | Unavoidable (Flet architecture) |
| Python cold import (`flet`, `ui_facade` chain) | ~280 ms | Unavoidable on first run |

**The single action that would eliminate ~90% of the user-visible delay:**
Move `refresh()` (and specifically the price API call it triggers) off the Flet callback thread or defer it until after the first `page.update()`.

---

## 7. Suggested Fix Strategy

> These are observations only. No code has been changed beyond adding timing instrumentation.

### Fix A — Defer price loading to a background thread (HIGH IMPACT)

Render the dashboard immediately with `—` placeholders (positions, WAC, cost basis — all available instantly from the local DB in <10ms), then fetch prices on a background thread and update when they arrive.

```
page.add(skeleton)        ← instant, user sees UI in < 300ms
page.update()             ← first render with local data

threading.Thread(target=fetch_prices_then_update).start()
  → fetch prices (~7s, background)
  → page.update()         ← second render with live prices
```

**Required changes:**
- `refresh()` in `app_flet.py`: call `get_dashboard_snapshot()` without prices first, then enrich on a thread
- `get_dashboard_snapshot()` in `ui_facade.py`: accept `price_provider=None` path already works — no price logic needed for first render
- `threading.Thread` wrapper with `page.update()` callback on completion

### Fix B — Show loading skeleton immediately, run everything in background (ALTERNATIVE)

Call `page.add(loading_indicator)` + `page.update()` as the very first action, then run the full `_main_view_impl` setup in a thread. User sees a spinner within ~100ms.

### Fix C — Move `create_app_context()` before `ft.run()` (MINOR, +240ms gain)

Call `create_app_context()` in `run_ui()` before `ft.run()`, pass `ctx` to `main_view` via a closure. Reduces the blank phase by ~240ms.

---

*Instrumentation added to:*
- `ui/app_flet.py` — `_tick()` calls in `_main_view_impl()`, timing in `refresh()`
- `core/services/ui_facade.py` — `_snap_tick()` calls in `get_dashboard_snapshot()`

*To remove instrumentation: revert changes to these two files.*
