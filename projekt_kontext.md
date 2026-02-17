# LEDGER_APP – Projekt Kontext

## Proč to dělám

Chci mít jednoduchý a spolehlivý přehled o svých kryptoměnových investicích.
Nechci enterprise účetní systém.
Nechci účetní software.
Chci nástroj pro sebe, který funguje a dává mi klid.

---

## Co aplikace JE

LEDGER_APP je tokový (append-only) ledger nad SQLite databází.

- Každý řádek je atomický tok aktiva.
- Pravda je v datech (ledger), ne v odvozených výpočtech.
- Oprava = REVERSAL (žádné UPDATE/DELETE).
- Double-entry pro trade.
- Fingerprint deduplikace.

Aplikace umí:
- zapisovat transakce
- importovat CSV/XLSM
- počítat asset_balances
- počítat venue_balances
- zobrazit timeline
- exportovat RAW data

---

## Co aplikace ZATÍM NEDĚLÁ

Aplikace zatím není dashboard investic.

Neumí:
- průměrnou nákupní cenu
- P/L
- netto fiat invested
- přehled pozic jako “portfolio view”

Tohle je interpretační vrstva nad ledgerem,
ne samotný ledger.

---

## Architektonické zásady

- SQLite append-only databáze
- Žádné UPDATE / DELETE
- Oprava = REVERSAL
- Validator kontroluje syntaxi, ne ekonomiku
- Diagnostika varuje, nikdy neblokuje

Nezavádět zbytečnou složitost.

---

## Aktuální realita

Ledger vrstva je hotová a stabilní.
Chybí jednoduchá interpretační/reportovací vrstva,
která z ledgeru vytvoří přehled investic.

---

## Aktuální priorita

Přidat jednoduché reporty nad existujícím ledgerem:

1. Positions report:
   - aktuální balance per asset
   - průměrná nákupní cena
   - netto investovaný fiat

2. Jednoduchý cashflow report podle měny.

Cíl:
Mít přehled o investicích bez změny core architektury.
