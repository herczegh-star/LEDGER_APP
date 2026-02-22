# How to Run Ledger App

## Desktop UI (recommended)

    python -m ledger_app

## CLI mode

    python -m ledger_app --cli

## Help

    python -m ledger_app --help

## Legacy shim (backward compatible)

    python main.py
    python main.py --cli

## Installed console script

After `pip install -e .`:

    ledgerapp
    ledgerapp --cli

## Architecture note

Core is the source of truth. UI is a thin shell using `core/services/ui_facade` exclusively.
All data persists in SQLite (path configured in `ledger.ini` → `db_path`).
Offline behavior is guaranteed — live prices are optional and never required for core operations.

## First run

On first launch, `ledger.ini` is created with default settings (SQLite at `ledger.db`).
Import a `unified_format_raw` file via the Import button, or add trades manually via Add Trade.
