# LEDGER_APP – Projekt Kontext

## Proč to dělám

Chci mít jednoduchý a spolehlivý přehled o svých kryptoměnových investicích.
Nechci enterprise účetní systém.
Nechci účetní software.
Chci nástroj pro sebe, který funguje a dává mi klid.

---

## Co aplikace JE

LEDGER_APP je tokový (append-only) ledger nad SQLite databází s Flet desktop UI.

- Každý řádek je atomický tok aktiva.
- Pravda je v datech (ledger), ne v odvozených výpočtech.
- Oprava = REVERSAL (žádné UPDATE/DELETE).
- Double-entry pro trade.
- Fingerprint deduplikace (SHA-256).

---

## Aktuální stav – co funguje ✓

### Core / datová vrstva
- SQLite append-only databáze
- Import CSV / XLSM (unified_format_raw)
- Deduplikace při importu
- Validace syntaxe (ne ekonomiky)
- Diagnostika záporných zůstatků (warning, ne blokace)
- REVERSAL opravné toky

### Flet UI (desktop aplikace)
- Spouštění: `python main.py`
- Levé menu: Dashboard, Reports, Positions, Health, Ledger
- Header s akcemi: Add Trade, Reverse, Import, Export, Refresh

### Dashboard
- Portfolio Value (celková hodnota v CZK)
- Unrealized PnL (nerealizovaný zisk/ztráta)
- ROI (celkový výnos v %)
- Asset karty: množství, průměrná nákupní cena (WAC), cost basis,
  spot cena, aktuální hodnota, ROI (realized)
- Sort pillky: ROI, PnL, Value, A–Z

### Ledger view
- Tabulka všech transakcí (plná šířka okna)
- Filtrování: vyhledávání, typ, venue, řazení
- Reverse akce přímo z řádku

### Health view
- Diagnostická tabulka stavů portfolia

### Positions view
- WAC přehled pozic s cenou

### Reports view
- Přehledy nad ledgerem

---

## Co aplikace ZATÍM NEDĚLÁ ✗

- Parsery pro burzy (Anycoin, Bybit, Kraken, Revolut) – manuální import
- Daňová legislativa
- Automatické opravy
- Detail view na jednotlivé assety (TODO placeholder)
- Live ceny jsou aproximace (žádný API klíč pro burzy)

---

## Architektonické zásady

- SQLite append-only databáze
- Žádné UPDATE / DELETE
- Oprava = REVERSAL
- Validator kontroluje syntaxi, ne ekonomiku
- Diagnostika varuje, nikdy neblokuje
- 4 vrstvy: I/O → Core → Workflow → Flet UI

Nezavádět zbytečnou složitost.

---

## Technologie

- Python 3.10+
- Flet 0.80.x (desktop UI)
- SQLite (append-only ledger store)
- openpyxl (čtení .xlsm)

### Klíčové Flet 0.80 poznatky
- `ft.run(fn)` místo `ft.app(target=fn)`
- `ft.Icons.XXX` (velké I) pro ikony
- `ft.NavigationRail` nefunguje → custom Column nav
- `ft.FilePicker` vyžaduje `page.overlay.append()`
- `ft.SingleChildScrollView` neexistuje → `ft.Row(scroll=AUTO)`
- `ft.DataTable` neroztahuje se přes `expand` → `column_spacing=58`

---

## Další kroky (backlog)

1. **Parsery pro burzy** – I/O modul: Anycoin, Bybit, Kraken, Revolut
2. **Detail view** – kliknutí na asset → historia transakcí, P/L chart
3. **Export** – CSV / RAW výstupy (cashflow, P/L, daňový podklad)
4. **Live ceny** – integrace cenového API (CoinGecko nebo burzy)
