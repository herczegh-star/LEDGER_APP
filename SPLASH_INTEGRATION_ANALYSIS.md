# Splash Screen Integration — Architecture Analysis

> Date: 2026-03-10
> Flet version: 0.80.5
> Analysis only — no code changed

---

## 1. Current Startup Flow

```
python main.py
│
├─ [~1.3 s total Python cold start + Flutter engine]
│
└─ _main_view_impl(page) called by Flet
     │
     ├─ create_app_context()             ~240 ms  ← DB probe + price provider init
     ├─ build_reports_view()             ~1 ms
     ├─ build_positions_view()           ~1 ms
     ├─ build_health_view()              ~1 ms
     ├─ build_venue_view()               ~1 ms
     ├─ build_ledger_view()              ~1 ms
     ├─ dialog imports                   ~2 ms
     ├─ nav + header widget construction ~30 ms
     │
     ├─ page.add(_header, _body_row)     ← FIRST RENDER: skeleton only (content empty)
     │
     └─ set_view(0)
           └─ refresh()
                 └─ get_dashboard_snapshot()
                       ├─ timeline() SQL           ~5 ms
                       ├─ compute_positions()      ~1 ms
                       ├─ price_provider.get_prices(21 assets)  ← 7 274 ms BLOCK
                       ├─ per-venue loop           ~30 ms
                       └─ get_portfolio_snapshot() ~4 ms
                 └─ page.update()   ← FIRST FULL RENDER (dashboard with cards)
```

**Why the gray window:** After `page.add()` shows the skeleton, `set_view(0)` is called **synchronously on the same thread** and immediately blocks inside `price_provider.get_prices()`. `page.update()` is not called until the HTTP requests complete — 7+ seconds later.

---

## 2. Critical Finding — `ft.Video` Does Not Exist in Flet 0.80.5

**Verified against the installed Flet 0.80.5:**

```python
>>> import flet as ft
>>> ft.Video
AttributeError: module 'flet' has no attribute 'Video'. Did you mean: 'View'?
```

`ft.Video` was introduced in Flet ≥ 0.21.0 as part of the `flet-video` package, which requires a **separate pip install** (`pip install flet[video]` or `pip install flet-video`). It is **not bundled** with the base `flet>=0.25.0` dependency declared in `pyproject.toml`.

**Available media-adjacent controls in Flet 0.80.5:**
- `ft.Image` — static images (PNG, JPG) and animated GIFs
- `ft.ProgressBar` — determinate (0.0–1.0) and indeterminate (value=None)
- `ft.ProgressRing` — spinning ring, determinate or indeterminate

**Conclusion:** The splash screen cannot use `ft.Video` without adding a new dependency. There are two paths:
- **Path 1:** Add `flet[video]` / `flet-video` dependency → use `ft.Video` with `splash.mp4`
- **Path 2:** Convert `splash.mp4` to animated GIF → use `ft.Image` with no new dependency

Both paths are analyzed below.

---

## 3. Asset Loading Analysis

`ft.run()` signature (Flet 0.80.5):
```python
ft.run(main, ..., assets_dir: Optional[str] = 'assets', ...)
```

**Default value is already `'assets'`.** Current call in `run_ui()`:
```python
ft.run(main_view)   # assets_dir defaults to 'assets'
```

This means `assets/splash.mp4` (and any PNG/GIF placed there) is **already accessible** to Flet controls without any configuration change. No `assets_dir` argument needs to be added.

Reference path from Flet controls: just the filename, e.g. `"splash.mp4"` or `"splash.gif"`.

The `assets/` directory is relative to the **working directory** when the app is launched (`python main.py` from the project root). This is already how the project is used.

No packaging configuration changes are needed for development use. For a future packaged distribution, `assets/` would need to be included in the package data — `pyproject.toml` currently does not list it, but that is a packaging concern, not a dev concern.

---

## 4. Exact Blocking Analysis

The gray window is caused by a single chain:

```
_main_view_impl (line 123, ui/app_flet.py)
  └─ [all setup: ~290 ms]
  └─ page.add(_header, _body_row)          ← sends skeleton to Flutter
  └─ set_view(0)                           ← immediately, same thread
        └─ _content.content = _dashboard_view
        └─ refresh()                       ← no yield, no thread boundary
              └─ get_dashboard_snapshot()
                    └─ price_provider.get_prices(21 assets)
                          CoinGeckoPriceProvider.get_prices()
                            urllib.request.urlopen(timeout=5)
                                                    ← 7 274 ms here
              └─ page.update()             ← only after HTTP completes
```

**The `page.update()` inside `refresh()` is the ONLY `page.update()` call in the startup sequence that makes the dashboard visible.** It does not execute until `get_dashboard_snapshot()` returns.

The `page.add()` call internally does trigger a Flet update (skeleton becomes visible), but since the next line immediately starts a 7-second synchronous block, the user experiences:
- skeleton (header + empty nav + empty content area) for a brief flash
- no further updates for 7+ seconds
- then the full dashboard appears

---

## 5. Integration Point for Splash Screen

**The optimal insertion point is in `_main_view_impl()`, immediately after `page` properties are set, before any other work.**

Specifically, between lines 127–132 of `ui/app_flet.py`:

```python
def _main_view_impl(page: ft.Page) -> None:
    # page properties set here (bgcolor, theme_mode, etc.)
    ...

    # ← INSERT SPLASH HERE, call page.update()
    #   User sees splash within < 100ms of Flet calling main_view

    ctx = create_app_context()   # ← then heavy work starts
    ...
```

This location is chosen because:
1. `page` is fully initialized (bgcolor, theme_mode applied)
2. No Python work has started yet — `create_app_context()` hasn't been called
3. `page.update()` here renders the splash immediately
4. All subsequent loading happens **after** the user has visual feedback

---

## 6. Loading Status Integration Points

The loading toolbar needs a **mutable status text widget** that can be updated between phases. The update pattern is:

```python
status_text.value = "Loading ledger..."
page.update()
# ... do actual work ...
status_text.value = "Computing portfolio..."
page.update()
# ... etc.
```

**Phase breakdown** with measured timings:

| Phase | Status label | Duration |
|---|---|---|
| App bootstrap | `"Initializing..."` | shown during `create_app_context()` |
| DB probe + config | `"Loading ledger..."` | ~240 ms (inside create_app_context) |
| Widget construction | `"Building interface..."` | ~50 ms |
| DB timeline query | `"Reading transactions..."` | ~5 ms |
| Position compute | `"Computing portfolio..."` | ~1 ms |
| Price fetch | `"Fetching market prices..."` | **~7 000 ms** |
| Venue loop | `"Aggregating venues..."` | ~30 ms |
| Dashboard render | `"Building dashboard..."` | ~10 ms |

The **price fetch phase** dominates. This is where the visible toolbar provides the most value — the user needs to know the app is alive during those 7 seconds.

---

## 7. Option Comparison

### Option A — Splash + synchronous loading with status updates

**Description:**
Show splash + toolbar immediately. Keep all existing logic synchronous. Insert `status_text.value = "..."` + `page.update()` calls between each loading phase. At the end, replace splash with the full dashboard.

**How it works:**
```
main_view(page) called
  → build splash controls
  → page.add(splash_screen)
  → page.update()                     ← user sees splash < 100ms after window
  → set status "Loading ledger..."
  → page.update()
  → create_app_context()              (240ms — DB probe)
  → set status "Building interface..."
  → page.update()
  → build all view modules            (~50ms)
  → set status "Reading transactions..."
  → page.update()
  → call get_dashboard_snapshot() step by step:
      → timeline() + compute_positions()   (~6ms)
      → set status "Fetching market prices..."
      → page.update()                      ← user sees price phase label during 7s wait
      → price_provider.get_prices()        (7274ms — blocked, but labeled)
      → per-venue loop + snapshot
  → set status "Building dashboard..."
  → page.update()
  → build all view widgets (nav, header, cards)
  → page.controls = [full_app]
  → page.update()                     ← dashboard replaces splash
```

**Evaluation:**

| Criterion | Score | Notes |
|---|---|---|
| Complexity | LOW | No threads, no async, no architecture change |
| Risk | LOW | Purely additive — no existing logic changed |
| Architecture compatibility | HIGH | Fits perfectly with current synchronous model |
| User experience | GOOD | Splash + status visible, but 7s price wait still frozen per-phase |
| Code changes | MINIMAL | Only `ui/app_flet.py` + minor split in `ui_facade.py` |

**Limitation:** During `price_provider.get_prices()` the status text is set before the call and updated after — the user sees the correct status label, but there is no within-phase animation (the ProgressBar cannot animate its own indeterminate pulse while Python is blocked). The `ft.ProgressRing(value=None)` spinner will **freeze** during the HTTP call because Flutter's animation frame is not serviced while Python blocks.

---

### Option B — Splash + background thread for heavy initialization

**Description:**
Show splash immediately. Launch a `threading.Thread` for all heavy initialization (`create_app_context`, `get_dashboard_snapshot` with prices). The splash and progress toolbar remain animated. When the thread completes, call `page.update()` to replace splash with dashboard.

**How it works:**
```
main_view(page) called
  → build splash + progress controls
  → page.add(splash_screen)
  → page.update()                  ← user sees splash + spinning ring

  → threading.Thread(target=_init_worker).start()
      _init_worker():
          ctx = create_app_context()
          snap = get_dashboard_snapshot(...)   ← 7s here, background thread
          page.run_task(lambda: _finish(ctx, snap))
              → page.controls = [full_app]
              → page.update()

  (main thread returns to Flet event loop)
  (ProgressRing animates freely — Flutter renders normally)
```

**Evaluation:**

| Criterion | Score | Notes |
|---|---|---|
| Complexity | MEDIUM | Threading + `page.run_task()` cross-thread update required |
| Risk | MEDIUM | Thread safety: Flet controls must not be built on background thread |
| Architecture compatibility | MEDIUM | Requires splitting `_main_view_impl` — context and snapshot computed on thread, widget build on main thread |
| User experience | EXCELLENT | Spinner animates smoothly during all 7 seconds |
| Code changes | MODERATE | `ui/app_flet.py` refactor + thread wrapper |

**Key constraint:** In Flet 0.80.5, **all page/control mutations must happen on the Flet event loop thread**. A background thread can compute data, but `page.add()`, `page.update()`, and any control `.value = ...` must be dispatched via `page.run_task()` (async dispatch) or by calling a function that Flet schedules on its thread. This adds coordination complexity.

---

### Option C — Progressive dashboard build

**Description:**
Show splash, then progressively populate the dashboard shell while initialization proceeds. The content area fills in step by step as each piece becomes available.

**How it works:**
```
page.add(splash_screen)
page.update()

→ create_app_context()
→ build nav + header + empty dashboard shell
→ page.controls = [header, nav, empty_dashboard]
→ page.update()          ← nav visible, cards empty

→ timeline() + compute_positions()
→ populate cards with local data (no prices)
→ page.update()          ← cards visible without spot prices

→ price_provider.get_prices() in background thread
→ when done: enrich cards with prices
→ page.update()          ← cards update with live prices
```

**Evaluation:**

| Criterion | Score | Notes |
|---|---|---|
| Complexity | HIGH | Two-phase render: positions without prices, then enrich |
| Risk | MEDIUM-HIGH | Requires deeper refactor of `get_dashboard_snapshot()` and `refresh()` |
| Architecture compatibility | LOWER | `get_dashboard_snapshot()` tightly couples DB + compute + prices |
| User experience | EXCELLENT | User sees data immediately, prices appear later |
| Code changes | SIGNIFICANT | `ui_facade.py` + `app_flet.py` + possibly `refresh()` logic |

---

## 8. Recommended Solution

**Option A — Splash + synchronous loading with status updates**

**Rationale:**

1. **Lowest risk** — existing loading logic is not restructured. The only change is wrapping it in a splash container with status text updates between phases.

2. **Minimal code changes** — confined to `ui/app_flet.py` only, plus one minor helper to expose the step-by-step snapshot build.

3. **Solves the user's complaint** — the user currently sees a blank gray window with no feedback. After this change they will see a splash screen with visible status text during each phase. The "blocking" is intentional and labeled.

4. **ProgressRing limitation is acceptable** — The spinner will freeze during the HTTP call, but the status label `"Fetching market prices..."` is visible and static. A `ft.ProgressBar` in indeterminate mode (`value=None`) has the same limitation. This is a Flutter rendering constraint, not a code bug.

5. **Option B (background thread) is the right long-term fix** for the gray window itself, but it is a larger architectural change. Option A can be implemented now, and Option B applied as a follow-up without removing the splash.

---

## 9. Video Component Decision

Since `ft.Video` is not available in Flet 0.80.5:

| Approach | Dependency change | Effort | Recommendation |
|---|---|---|---|
| Add `flet[video]` pip dep | `pyproject.toml` + install | Low | Viable if video is important |
| Convert `splash.mp4` → `splash.gif` | None | Low (one-time conversion) | Simplest, no dep change |
| Styled static image (`ft.Image`) | None | None | Safest, no conversion needed |
| Skip video, use styled text + spinner | None | None | Cleanest, zero risk |

**Recommendation for lowest risk:** Convert `splash.mp4` to an animated GIF and use `ft.Image("splash.gif")`. No new dependency, no `pyproject.toml` change, works with `assets_dir='assets'` default.

If the video must play as video (not GIF): add `flet-video` to `pyproject.toml` dependencies and use `ft.Video(src="splash.mp4", autoplay=True, loop=True, muted=True)`.

---

## 10. Files to Modify

| File | Change needed | Scope |
|---|---|---|
| `ui/app_flet.py` | Add splash screen builder, insert before heavy work, add status updates between phases, remove splash at end | Primary change |
| `core/services/ui_facade.py` | Add a step-by-step variant of `get_dashboard_snapshot()` that accepts a `status_cb` callback — OR — split into `get_positions_only()` + `enrich_with_prices()` so status can be injected between steps | Optional — only needed if status granularity inside `get_dashboard_snapshot` is required |
| `pyproject.toml` | Add `flet-video` if MP4 playback is chosen | Only if ft.Video path is taken |

**No other files need to change.**

---

## 11. Exact Integration Points

### In `ui/app_flet.py`

```
_main_view_impl(page)
│
│   ← INSERTION POINT 1: build splash screen controls
│   ← INSERTION POINT 2: page.add(splash) + page.update()
│   ← now splash is visible
│
├─ set_status("Initializing...")  ← status callback
├─ create_app_context()
│
├─ set_status("Building interface...")
├─ [all build_*_view() calls]
├─ [nav + header construction]
│
├─ set_status("Reading transactions...")
├─ [timeline SQL inside get_dashboard_snapshot]
│
├─ set_status("Computing portfolio...")
├─ [compute_positions inside get_dashboard_snapshot]
│
├─ set_status("Fetching market prices...")
├─ page.update()                    ← critical: render status BEFORE price fetch
├─ price_provider.get_prices(...)   ← 7 second wait, label is visible
│
├─ set_status("Building dashboard...")
├─ [per-venue loop + portfolio snapshot]
│
│   ← INSERTION POINT 3: replace splash with full dashboard
└─ page.controls = [_header, _body_row]
   page.update()
```

### In `core/services/ui_facade.py` (optional, only for within-snapshot status)

The status callback needs to fire between the positions compute and the price fetch. Two options:

**Option A1 (simpler) — caller sets status before calling:**
```python
# In app_flet.py refresh():
set_status("Computing portfolio...")
page.update()
snap = get_dashboard_snapshot(db_path, price_provider, fiat)
# Status cannot change mid-snapshot — acceptable
```

**Option A2 (granular) — pass callback into snapshot:**
```python
# ui_facade.get_dashboard_snapshot() gains optional parameter:
def get_dashboard_snapshot(db_path, price_provider, fiat, status_cb=None):
    rows = svc.timeline()
    if status_cb: status_cb("Computing portfolio...")
    raw_positions = compute_positions(rows, _FIAT_DEFAULT)
    if status_cb: status_cb("Fetching market prices...")
    prices_map = price_provider.get_prices(assets, fiat_uc)
    ...
```

Option A1 changes no existing code. Option A2 requires one small parameter addition to `get_dashboard_snapshot()`.

---

## 12. Startup Pseudocode (adapted to actual project)

```python
def _main_view_impl(page: ft.Page) -> None:
    # ── Page base settings ────────────────────────────────────────────
    page.title = "LedgerApp 1.0.0"
    page.bgcolor = "#0b0f14"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.window.width = 1200
    page.window.height = 760

    # ── Build splash screen ───────────────────────────────────────────
    status_text = ft.Text("Starting...", size=13, color="#7b8799")
    progress_bar = ft.ProgressBar(value=None, color="#1d4ed8", bgcolor="#0f1621")

    splash_media = ft.Image(       # ft.Video(...) if flet-video installed
        src="splash.gif",          # or remove if no video asset
        fit=ft.ImageFit.CONTAIN,
        expand=True,
    )

    splash_screen = ft.Container(
        expand=True,
        bgcolor="#0b0f14",
        content=ft.Column(
            [
                splash_media,
                ft.Container(height=24),
                status_text,
                ft.Container(height=8),
                progress_bar,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            expand=True,
        ),
    )

    page.add(splash_screen)
    page.update()                   # ← first visible render, < 100ms from Flet call

    def set_status(msg: str) -> None:
        status_text.value = msg
        page.update()

    # ── Phase 1: app context (DB probe + config) ──────────────────────
    set_status("Loading configuration...")
    ctx = create_app_context()
    # error guard unchanged...

    db_path = ctx.db_path
    _price_provider = ctx.price_provider
    _price_fiat = ctx.fiat

    # ── Phase 2: build all view modules ──────────────────────────────
    set_status("Building interface...")
    from ui.modules.reports import build_reports_view as _build_rv
    _reports_view, _run_report = _build_rv(page, db_path)
    # ... all other build_*_view() calls ...
    # ... all dialog imports ...
    # ... nav, header, body widget construction ...

    # ── Phase 3: initial data load (local, fast) ──────────────────────
    set_status("Reading transactions...")
    page.update()                   # render status before DB call
    # timeline() + compute_positions() inside get_dashboard_snapshot
    # takes ~6ms total — no meaningful wait

    # ── Phase 4: price fetch (the 7-second phase) ────────────────────
    set_status("Fetching market prices...")
    page.update()                   # CRITICAL: must render label before blocking call
    snap = get_dashboard_snapshot(db_path, _price_provider, _price_fiat)
    snap_holder[0] = snap
    raw = snap.positions

    # ── Phase 5: build dashboard content ─────────────────────────────
    set_status("Building dashboard...")
    page.update()
    update_kpis()
    build_pills()
    build_cards()

    # ── Replace splash with dashboard ────────────────────────────────
    page.controls.clear()
    page.add(_header, _body_row)    # ← full app layout replaces splash
    page.update()                   # ← dashboard visible
```

---

## 13. Summary

| Question | Answer |
|---|---|
| Why is there a gray window? | `price_provider.get_prices(21 assets)` blocks the Flet thread for 7.3s, and `page.update()` is not called until it returns |
| Best integration point for splash | Inside `_main_view_impl()`, immediately after page properties are set, before `create_app_context()` |
| Best integration point for status toolbar | `status_text.value = "..."` + `page.update()` between each loading phase |
| Video playback available? | **No** — `ft.Video` not in Flet 0.80.5. Use animated GIF (`ft.Image`) or add `flet-video` dependency |
| Assets path already configured? | **Yes** — `ft.run()` defaults to `assets_dir='assets'`, no change needed |
| Recommended option | **Option A** — synchronous splash with status updates |
| Files to change | `ui/app_flet.py` (primary) + optionally `ui_facade.py` for within-snapshot status |
| Risk | LOW — all existing logic preserved, only wrapped in a splash |
| Threading required? | No — Option A is fully synchronous, matching the existing architecture |
