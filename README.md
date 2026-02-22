# Ledger App – v1

Investiční portfolio tracker nad `unified_format_raw`.
Desktop aplikace v Pythonu s Flet UI.

## Filozofie

Aplikace je nástroj pro čtení a vytváření tokového ledgeru,
nikoli pro jeho interpretaci; pravda je v datech, nikoli v kódu.

## Spuštění

```bash
# Desktop UI (výchozí)
python main.py

# CLI režim
python main.py --cli
```

Jediný podporovaný vstupní bod je `main.py`.

## Architektura

```
I/O Modul  →  CORE (doménová logika)  →  ui_facade  →  Flet UI
```

- Core je jediný zdroj pravdy.
- UI importuje výhradně z `core.services.ui_facade` a `core.constants`.
- Ledger je append-only SQLite; opravy jdou přes REVERSAL.
- Offline provoz je plně podporován (live ceny jsou volitelné).

## Závislosti

- Python 3.10+
- Flet (desktop UI)
- openpyxl (čtení .xlsm)
- SQLite (součást Pythonu)

## Konfigurace

Nastavení DB a cen je v `ledger.ini` (vytvoří se automaticky při prvním spuštění).
