# Ledger App – v1

Investiční portfolio tracker nad `unified_format_raw`.
Desktop aplikace v Pythonu s Flet UI.

## Filozofie

Aplikace je nástroj pro čtení a vytváření tokového ledgeru,
nikoli pro jeho interpretaci; pravda je v datech, nikoli v kódu.

## Spuštění

```bash
# Desktop UI (výchozí / doporučeno)
python -m ledger_app

# CLI režim
python -m ledger_app --cli

# Alternativní (legacy shim)
python main.py
python main.py --cli
```

Po instalaci (`pip install -e .`) je k dispozici konzolový příkaz `ledgerapp`.

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

Konfigurační soubor je uložen ve složce uživatele — nezávisí na pracovním adresáři:

- **Windows:** `%USERPROFILE%\.ledger_app\ledger.ini`
- **macOS/Linux:** `~/.ledger_app/ledger.ini`

Při prvním spuštění se vytvoří automaticky s výchozím nastavením (SQLite na `~/.ledger_app/ledger.db`).

**Zpětná kompatibilita:** Pokud v kořeni projektu existuje `ledger.ini` a user-home config ještě neexistuje, použije se projektový soubor.
