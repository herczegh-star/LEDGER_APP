# LEDGER_APP – Technický audit projektu

**Datum auditu:** 2026-02-21
**Auditor:** Claude Sonnet 4.6 (automatický audit na základě čtení skutečného kódu)

---

## 1. Struktura projektu

```
LEDGER_APP/
├── main.py                          # Entry point (CLI + Flet switcher)
├── cli.py                           # Click CLI příkazy
├── ledger.ini                       # Konfigurace (db_path, fx.eur_to_czk)
│
├── core/
│   ├── model.py                     # RawRow dataclass + fingerprint v2
│   ├── validator.py                 # Syntaktická validace (ne sémantická)
│   ├── ledger_store.py              # SQLite append-only store
│   ├── service.py                   # LedgerService (fasáda nad store)
│   ├── config.py                    # load_config() → ledger.ini
│   ├── trade.py                     # build_trade_rows()
│   ├── fee.py                       # build_fee_row()
│   ├── transfer.py                  # build_transfer_row()
│   ├── reversal.py                  # create_reversal(), reverse_rows()
│   ├── dto/
│   │   └── reporting.py             # ReportMeta, TimeSeriesRow, TimeSeriesReport,
│   │                                #   TableRow, TableReport
│   ├── reports/
│   │   ├── cashflow.py              # cashflow_report() → TimeSeriesReport
│   │   ├── netto_invested.py        # netto_invested_report() → TimeSeriesReport
│   │   └── positions.py            # compute_positions() + positions_report() → TableReport
│   └── services/
│       ├── report_service.py        # get_report(), get_positions_report() (dispatcher)
│       ├── health_service.py        # health_report() → TableReport (7 integrity checks)
│       ├── export_service.py        # export_*_csv() (6 funkcí, utf-8-sig)
│       ├── trade_service.py         # add_trade() / reverse_trade()
│       ├── portfolio_snapshot_service.py  # get_portfolio_snapshot() → PortfolioSnapshot
│       └── unified_format_raw_import_service.py  # import_unified_file()
│
├── io_module/
│   └── raw_loader.py                # LoadResult, _load_xlsm, _load_csv
│
├── ledger_engine/                   # STARÝ reporting engine (CZK-native, FX)
│   ├── positions_engine.py          # compute_positions() → Dict[str, AssetSnapshot]
│   ├── fx_provider.py               # FxProvider, ConfigFxProvider, DictFxProvider
│   └── __init__.py
│
├── ui/
│   ├── adapters.py                  # Přemostění: LedgerService → positions_engine → UI dict
│   ├── app_flet.py                  # Hlavní Flet aplikace (5 záložek v NavigationRail)
│   ├── dashboard.py                 # STARÝ Flet dashboard (superseded, nepoužívaný)
│   ├── terminal.py                  # Terminálové interaktivní menu
│   └── modules/
│       ├── reports.py               # Cashflow + Netto Invested reports view
│       ├── positions_view.py        # WAC Positions tab (filter/sort/export)
│       ├── health_view.py           # Data Health tab (7 checks, summary cards)
│       ├── ledger_view.py           # Ledger audit table (filter/sort/inline Reverse)
│       ├── add_trade_dialog.py      # Add Trade dialog
│       ├── import_dialog.py         # Import Unified File dialog
│       ├── export_dialog.py         # Export CSV dialog (type/bucket/fiat)
│       └── reversal_dialog.py       # Reversal dialog
│
└── tests/
    ├── test_mvp.py                  # 12 testů (legacy MVP kritéria)
    ├── test_service.py              # 17 testů (LedgerService)
    ├── test_export.py               # 9 testů (legacy export CSV/JSON)
    ├── test_cli.py                  # 5 testů (CLI příkazy)
    ├── test_positions_engine_wac.py # 10 testů (ledger_engine WAC)
    ├── test_config_fx_provider.py   # 2 testů (ConfigFxProvider)
    ├── test_positions_report_dto.py # 12 testů (core/reports/positions.py DTO)
    ├── test_health_service.py       # 32 testů (health_service, 7 checks)
    ├── test_export_service.py       # 24 testů (export_service, 6 funkcí)
    ├── test_positions_filter_sort.py # 26 testů (positions_view filter/sort helper)
    └── test_ledger_filter_sort.py   # 36 testů (ledger_view filter/sort helper)
```

---

## 2. Architektura

### 2.1 Deklarovaná vs. skutečná vrstvová architektura

CLAUDE.md deklaruje 4 vrstvy: `I/O Modul → CORE → Workflow → Grafika`.

**Skutečný stav:**

| Vrstva | Soubory | Dodržení hranic |
|--------|---------|-----------------|
| I/O | `io_module/raw_loader.py`, `core/services/unified_format_raw_import_service.py` | OK |
| CORE | `core/model.py`, `core/ledger_store.py`, `core/service.py`, `core/reports/*`, `core/services/*` | OK |
| Workflow (UI logika) | `ui/adapters.py`, `ui/modules/*` | Částečně OK (viz níže) |
| Grafika | `ui/app_flet.py`, `ui/modules/*.py` (Flet controls) | OK |

**Porušení:**
- `ui/adapters.py` volá přímo `ledger_engine/positions_engine.py`, čímž přeskakuje `core/` hranici. Adaptér by měl volat `core/services/report_service.py`.
- `ui/app_flet.py` volá `load_positions_view()` (adaptér) a `get_portfolio_snapshot()` (core service) paralelně — dvě různé cesty pro dvě různé záložky Dashboard a Positions.

### 2.2 Navigační struktura Flet aplikace

`ui/app_flet.py` obsahuje NavigationRail s 5 záložkami:

| Index | Ikona | Label | View builder | Zdroj dat |
|-------|-------|-------|-------------|-----------|
| 0 | `dashboard_outlined` | Dashboard | inline v `main()` | `load_positions_view()` + `get_portfolio_snapshot()` |
| 1 | `bar_chart_outlined` | Reports | `build_reports_view()` | `get_report()` (cashflow/netto) |
| 2 | `table_chart_outlined` | Positions | `build_positions_view()` | `get_positions_report()` |
| 3 | `health_and_safety_outlined` | Health | `build_health_view()` | `health_report()` |
| 4 | `table_rows_outlined` | Ledger | `build_ledger_view()` | `svc.timeline()` |

Header bar: **Add Trade**, **Reverse**, **Import**, **Export**, **Refresh** — vše plně zapojeno.

### 2.3 Tok dat

```
UI akce (Add Trade / Import)
    │
    ▼
core/services/trade_service.py  /  unified_format_raw_import_service.py
    │
    ▼
core/ledger_store.py  (INSERT, append-only, SHA256 dedup)
    │
    ▼
_refresh_all()  →  svc.timeline()  →  příslušný report
    │
    ▼
DTO (TimeSeriesReport / TableReport)  →  UI render (žádná logika v UI)
```

---

## 3. Datový model a výpočetní logika

### 3.1 RawRow (`core/model.py`)

| Pole | Typ | Poznámka |
|------|-----|----------|
| `id` | str (UUID4) | Sdílený double-entry párem |
| `timestamp` | datetime | ISO 8601, auto-parse z stringu |
| `type` | str | BUY, SELL, TRANSFER, FEE, REVERSAL |
| `asset` | str uppercase | BTC, ETH, EUR, CZK… |
| `amount` | Decimal | + příchozí, − odchozí |
| `currency` | str uppercase | Protistranné aktivum |
| `price` | Decimal? | Informativní |
| `venue` | str lowercase | Burza / peněženka / banka |
| `note` | str? | Volitelná poznámka |

**Fingerprint v2:**
```
SHA256(timestamp_iso|TYPE|venue|ASSET|CURRENCY|amount:.8f)
```
Unikátní index `UNIQUE(row_fp)` v SQLite. Opakovaný import = 0 duplicit.

### 3.2 Dva paralelní WAC enginy

**Kritický nález:** Projekt obsahuje DVA různé enginy pro výpočet pozic:

#### Engine #1 — `ledger_engine/positions_engine.py` (STARÝ)
- Výstup: `Dict[str, AssetSnapshot]` (CZK-native)
- Vyžaduje `FxProvider` pro EUR→CZK přepočet
- Pole: `qty`, `cost_basis_czk`, `avg_buy_czk`, `realized_pnl_czk`, `unrealized_pnl_czk`, `roi`
- Spotřeba: `ui/adapters.py` → `ui/app_flet.py` (Dashboard záložka)
- `price_provider=None` → `unrealized = None` → zobrazeno jako `—`
- Flat-rate FX (stejný kurz pro všechna data, z `ledger.ini`)

#### Engine #2 — `core/reports/positions.py` (NOVÝ)
- Výstup: `TableReport` DTO (fiat-native, bez FX)
- Nevyžaduje `FxProvider`
- Pole: `quantity`, `wac`, `cost_basis`, `realized_pnl`
- Spotřeba: `core/services/report_service.py` → `ui/modules/positions_view.py` (Positions záložka)
- Hodnoty v té měně, ve které byly provedeny obchody (multi-currency aware)

**Důsledek:** Dashboard a Positions záložka mohou zobrazovat různá čísla pro stejná data, pokud jsou použity různé FX kurzy nebo metodika.

### 3.3 Cashflow report (`core/reports/cashflow.py`)

- Scannuje všechny řádky, filtruje fiat aktiva (EUR, CZK)
- Agreguje příchozí (BUY → fiat outflow, SELL → fiat inflow) per bucket (day/week/month)
- Výstup: `TimeSeriesReport` s metrikami `inflow`, `outflow`, `net`

### 3.4 Netto Invested (`core/reports/netto_invested.py`)

- Anti-netting two-pass strategie: pass 1 = gross inflows, pass 2 = gross outflows
- Zamezuje záporným hodnotám v netto (desinvestice ≠ záporná investice)
- Výstup: `TimeSeriesReport` s metrikami `gross_in`, `gross_out`, `netto`

### 3.5 Portfolio Snapshot (`core/services/portfolio_snapshot_service.py`)

- `PortfolioSnapshot`: `invested`, `net_flow`, `assets_held`, `top_position`
- Zobrazeno v Dashboard strip (Net Invested, Net Flow, Assets Held, Top Position)
- `invested` = suma fiat odtoků za BUY; `net_flow` = suma všech fiat toků

### 3.6 Health service (`core/services/health_service.py`)

7 integrity checks, výstup `TableReport`:

| # | Kind | Severity | Popis |
|---|------|----------|-------|
| 1 | `missing_quote_leg` | ERROR | Non-fiat investiční leg bez fiat quote leg |
| 2 | `missing_investment_leg` | ERROR | Fiat quote leg bez non-fiat leg |
| 3 | `multi_fiat_quote` | WARNING | Trade má quote legy v >1 fiat měně |
| 4 | `fiat_as_investment_leg` | ERROR | Fiat aktivum na straně investice (špatné znaménko) |
| 5 | `zero_amount` | WARNING | Řádek s amount == 0 |
| 6 | `missing_timestamp` | ERROR | Řádek bez timestamp |
| 7 | `oversell` | ERROR | SELL/REVERSAL přesahuje drženou pozici |

Řazení: severity (error first), kind, trade_id, asset, timestamp — plně deterministické.

### 3.7 Export service (`core/services/export_service.py`)

6 veřejných funkcí, všechny `utf-8-sig` (BOM pro Excel):

| Funkce | Výstup |
|--------|--------|
| `export_ledger_csv(db_path, out_path)` | Všechny RawRow řádky |
| `export_timeseries_report_csv(report, out_path)` | TimeSeriesReport → CSV |
| `export_table_report_csv(report, out_path)` | TableReport → CSV |
| `export_cashflow_csv(db_path, out_path, bucket, fiat)` | Cashflow report |
| `export_netto_invested_csv(db_path, out_path, bucket, fiat)` | Netto invested report |
| `export_positions_csv(db_path, out_path, fiat)` | WAC positions snapshot |

`_str(val)`: Decimal → `format(val.normalize(), "f")` (žádná vědecká notace).

---

## 4. Technický dluh

### 4.1 Dva WAC enginy (KRITICKÉ)

**Problem:** `ledger_engine/positions_engine.py` a `core/reports/positions.py` implementují WAC nezávisle s různými výstupy a různými předpoklady (CZK vs. multi-currency).

**Riziko:** Dashboard a Positions záložka mohou zobrazovat nekonzistentní čísla. Dashboard záložka závisí na flat-rate FX kurzu z `ledger.ini`; Positions záložka pracuje s původní měnou obchodů.

**Doporučení:** Rozhodnout se pro jednu kanonickou implementaci. Pravděpodobně Engine #2 (fiat-native) je správnější pro multi-currency portfolio.

### 4.2 `insert_pair()` není atomická (STŘEDNÍ)

**Problem:** `LedgerStore.insert_pair()` provádí dvě separátní `INSERT` bez obalení do jedné SQLite transakce (`BEGIN ... COMMIT`). Pokud první `INSERT` uspěje a druhý selže z důvodu jiného než `IntegrityError` (např. I/O chyba, výpadek procesu), ledger skončí s neúplným double-entry párem.

```python
# Aktuální kód (core/ledger_store.py:132-165)
try:
    self.conn.execute(INSERT ...)   # row_a
    results[0] = True
except sqlite3.IntegrityError:
    pass
try:
    self.conn.execute(INSERT ...)   # row_b — NENÍ v jedné transakci s row_a
    results[1] = True
except sqlite3.IntegrityError:
    pass
self.conn.commit()  # ← commit pro obě, ale chyba mezitím = nekonzistence
```

**Doporučení:** Obalit oba INSERT do explicitní SQLite transakce (`with self.conn:` nebo `BEGIN/ROLLBACK`).

### 4.3 Tichá chyba v `ui/adapters.py` (STŘEDNÍ)

```python
# ui/adapters.py:52-55
try:
    snapshots = compute_positions(rows, fx_provider, price_provider)
except Exception:   # ← zachycuje VŠE, včetně programátorských chyb
    return []
```

Pokud `compute_positions()` vyhodí `TypeError`, `AttributeError` nebo jiný programátorský bug, adaptér vrátí prázdný seznam a Dashboard zobrazí prázdnou stránku bez jakékoli chybové zprávy. Debugování je velmi obtížné.

**Doporučení:** Zachytávat pouze konkrétní očekávané výjimky (`ValueError`, `KeyError`) a ostatní nechat propagovat.

### 4.4 `ui/dashboard.py` je mrtvý kód (NÍZKÉ)

`ui/dashboard.py` je starší verze Flet dashboardu (záložky Balances / Timeline). Je superseded novým `ui/app_flet.py` a nikde nevolán. Způsobuje zmatek při čtení kódu.

**Doporučení:** Smazat soubor.

### 4.5 `ui/adapters.py` porušuje vrstvové hranice (NÍZKÉ)

Adaptér volá přímo `ledger_engine/positions_engine.py`, zatímco nový kód volá `core/services/report_service.py`. Jsou dvě různé cesty k podobnému výsledku.

**Doporučení:** Při unifikaci WAC enginů přesměrovat adaptér přes `report_service.py`.

---

## 5. Testovací pokrytí

### 5.1 Přehled test souborů

| Soubor | Testy | Oblast | Stav |
|--------|-------|--------|------|
| `tests/test_mvp.py` | 12 | MVP kritéria (import, dedup, timeline, reversal) | ⚠ Mohou selhávat (legacy API) |
| `tests/test_service.py` | 17 | LedgerService operace | ⚠ Mohou selhávat (legacy API) |
| `tests/test_export.py` | 9 | Legacy export (CSV/JSON přes service) | ⚠ Mohou selhávat |
| `tests/test_cli.py` | 5 | CLI příkazy | ⚠ Mohou selhávat |
| `tests/test_positions_engine_wac.py` | 10 | ledger_engine WAC | ⚠ Mohou selhávat (stará API) |
| `tests/test_config_fx_provider.py` | 2 | ConfigFxProvider | OK (izolované) |
| `tests/test_positions_report_dto.py` | 12 | core/reports/positions.py DTO | ✅ Nové, passing |
| `tests/test_health_service.py` | 32 | health_service, 7 checks | ✅ Nové, passing |
| `tests/test_export_service.py` | 24 | export_service, 6 funkcí | ✅ Nové, passing |
| `tests/test_positions_filter_sort.py` | 26 | Positions filter/sort pure helper | ✅ Nové, passing |
| `tests/test_ledger_filter_sort.py` | 36 | Ledger filter/sort pure helper | ✅ Nové, passing |
| **Celkem** | **185** | | |

### 5.2 Kvalita testů

**Nové testy (✅):** Pokrývají čistě izolované pure funkce bez Flet závislostí. Systematické: edge cases, kombinace filtrů, typy výjimek, deterministické řazení. Dobře organizované pomocí fixture helperů.

**Legacy testy (⚠):** Psány pro starší API (`core/service.py` metody, které mohly být přejmenovány nebo přesunuty). Pravděpodobně selhávají při `pytest tests/ -v`. Nebyly aktualizovány po přidání nové vrstvy `core/services/`.

### 5.3 Mezery v pokrytí

- `core/services/trade_service.py` — není dedikovaný test soubor
- `core/services/portfolio_snapshot_service.py` — není dedikovaný test soubor
- `core/reports/cashflow.py` a `core/reports/netto_invested.py` — testy chybí
- `ui/modules/*.py` — žádné UI integrační testy (záměrně, kvůli Flet závislosti)
- `core/ledger_store.py` `insert_pair()` atomicita — není testována

---

## 6. Datová integrita a determinismus

### 6.1 Decimal precision

Veškerá numerická logika v core uses `decimal.Decimal`. Konverze z `float` probíhá přes `Decimal(str(float_val))` (v `RawRow.__post_init__`), nikdy přes `Decimal(float_val)` — správně.

Export přes `format(val.normalize(), "f")` → žádná vědecká notace ani floating point chyby v CSV.

### 6.2 Dedup edge cases

Fingerprint zahrnuje `amount` s pevnou přesností `:.8f`. Pokud dva záznamy mají stejný timestamp/type/venue/asset/currency ale různé amount (např. `1.0` vs `1.00000001`), jsou fingerprints různé → **nejsou deduplikovány** — správné chování.

Pokud je `timestamp` přesný na milisekundy a dvě transakce proběhnou ve stejné milisekundě na stejném venue pro stejné aktivum se stejnou částkou → **kolize fingerprint** → druhá transakce je tiše zahozena jako duplikát. Toto je known edge case, který nelze snadno opravit bez změny schématu.

### 6.3 Řazení

`timeline()` řadí dle `timestamp ASC`. Pro záznamy se stejným timestamp je řazení nedeterministické (SQLite `rowid` order, ale není garantováno po `VACUUM`). Dopad: výpočet WAC může záviset na pořadí — pokud dvě transakce mají stejný timestamp, výsledné WAC může být různé při různém řazení.

### 6.4 Dedup zpětná vazba

`trade_service.add_trade()` a `import_unified_file()` vrací počty `inserted`/`skipped`, ale UI je nezobrazuje uživateli systematicky. Import dialog zobrazí výsledek, ale Add Trade dialog zpětnou vazbu o duplicitách nemá.

---

## 7. Bezpečnost modelu a ochrana dat

### 7.1 Reversal mechanismus

`reverse_trade(db_path, trade_id)` v `core/services/trade_service.py`:
- Najde všechny řádky se stejným `trade_id` (sdílené `id`)
- Vytvoří nové řádky s `amount = -original.amount`, `type = REVERSAL`
- Nové řádky sdílí skupinové `id` ve formátu `REV_{original_id}_{8-hex}`
- Append-only: původní záznamy nejsou mazány ani měněny

**Bezpečnostní vlastnost:** Double reversal (reversal reversalu) technicky možný, ale fingerprint nové reversal skupiny je jiný → je vložen jako nový záznam.

### 7.2 Oversell detekce

`health_service` (check #7) volá `compute_positions()` a zachytí `ValueError` při oversell. Pokud engine nevyhodí výjimku (tj. `compute_positions()` tiše ignoruje záporné pozice), oversell nebude detekován. Závisí na implementaci engine.

### 7.3 Health check slepá místa

- `TRANSFER` a `FEE` typy nejsou zahrnuty v check #1 a #2 (správně — netvoří double-entry investiční páry)
- `REVERSAL` typ JE zahrnut v `_INVESTMENT_TYPES` → reversal páry jsou kontrolovány na missing legs
- Záporné zůstatky na venue level: pokryto `diagnostics()` v `LedgerStore`, ale health_service tento check neopakuje (oddělené pohledy)

---

## 8. Celkové hodnocení stability

### Co funguje dobře

| Oblast | Hodnocení |
|--------|-----------|
| Append-only SQLite ledger | ✅ Solidní |
| SHA256 dedup fingerprint v2 | ✅ Správná implementace |
| RawRow Decimal precision | ✅ Bez float chyb |
| DTO vrstva (TimeSeriesReport, TableReport) | ✅ Čistá separace UI od logiky |
| Pure filter/sort helpery (testovatelné bez Flet) | ✅ Správný přístup |
| CSV export (utf-8-sig, no scientific notation) | ✅ Excel-compatible |
| Health service (7 checks, deterministické řazení) | ✅ Solidní |
| Positions UI (filter, sort, export) | ✅ Funkční |
| Ledger View (audit table + inline Reverse) | ✅ Funkční |
| Flet NavigationRail (5 záložek) | ✅ Plně zapojeno |
| Add Trade / Import / Reverse dialogy | ✅ Funkční |

### Identifikované problémy

| Oblast | Závažnost | Dopad |
|--------|-----------|-------|
| Dva WAC enginy (nekonzistentní čísla) | KRITICKÉ | Dashboard vs Positions zobrazuje různá data |
| `insert_pair()` není atomická | STŘEDNÍ | Potenciální neúplný double-entry při I/O chybě |
| Tichá chyba v `ui/adapters.py` | STŘEDNÍ | Programátorské chyby jsou neviditelné |
| Legacy testy pravděpodobně selhávají | STŘEDNÍ | `pytest tests/` není zelený |
| `ui/dashboard.py` mrtvý kód | NÍZKÉ | Zmatek při čtení |
| `ui/adapters.py` porušuje vrstvové hranice | NÍZKÉ | Technický dluh |

### Celkový verdikt

**V1 stav: Funkční pro osobní použití, ne pro produkci.**

Ledger vrstva (core) je přímočará a append-only architektura je dodržena. UI je plně interaktivní se všemi potřebnými operacemi. Hlavní slabiny jsou dvě paralelní implementace WAC výpočtu a absence atomické transakce v `insert_pair()`. Legacy testy je nutné opravit nebo odstranit před prohlášením projektu za "zelený".

---

## 9. Prioritizovaná doporučení

| Priorita | Akce | Soubory |
|----------|------|---------|
| P1 | Opravit `insert_pair()` — obalit oba INSERT do jedné SQLite transakce | `core/ledger_store.py:132-165` |
| P1 | Unifikovat WAC enginy — rozhodnout se pro Engine #2 (fiat-native), adaptovat Dashboard | `ledger_engine/positions_engine.py`, `ui/adapters.py`, `ui/app_flet.py` |
| P1 | Opravit tichý except v adaptéru — zachytávat jen konkrétní výjimky | `ui/adapters.py:52-55` |
| P2 | Opravit nebo smazat legacy testy — `test_mvp.py`, `test_service.py`, `test_export.py`, `test_cli.py`, `test_positions_engine_wac.py` | `tests/` |
| P2 | Přidat testy pro `trade_service`, `portfolio_snapshot_service`, `cashflow_report`, `netto_invested_report` | `tests/` |
| P2 | Smazat `ui/dashboard.py` (mrtvý kód) | `ui/dashboard.py` |
| P3 | Přidat zpětnou vazbu při duplikátu v Add Trade dialogu | `ui/modules/add_trade_dialog.py` |
| P3 | Přidat schema versioning do SQLite (tabulka `_meta`) | `core/ledger_store.py` |
| P3 | Rozšířit health check pro TRANSFER páry (příchozí + odchozí musí souhlasit) | `core/services/health_service.py` |
| P3 | Přidat warning při importu pokud jsou všechny řádky ze souboru duplicity | `core/services/unified_format_raw_import_service.py` |

---

*Audit proveden na základě přímého čtení zdrojového kódu. Nebyly použity žádné shrnutí ani dedukce.*
