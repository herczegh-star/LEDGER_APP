# How to Run Ledger App

## Desktop UI (default)

    python main.py

## CLI mode

    python main.py --cli

## Architecture note

Core is the source of truth. UI is a thin shell using `core/services/ui_facade` exclusively.
All data persists in SQLite (path configured in `ledger.ini` → `db_path`).
Offline behavior is guaranteed — live prices are optional and never required for core operations.

## First run

On first launch, `ledger.ini` is created with default settings (SQLite at `ledger.db`).
Import a `unified_format_raw` file via the Import button, or add trades manually via Add Trade.
