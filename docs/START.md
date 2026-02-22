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

## Config file location

The config file is stored in your user home directory — always the same regardless of where you launch the app from:

- **Windows:** `%USERPROFILE%\.ledger_app\ledger.ini`
- **macOS/Linux:** `~/.ledger_app/ledger.ini`

**Legacy fallback:** If a `ledger.ini` exists in the project folder and no user-home config exists yet, the project-root file is used (one-time migration). After that, the user-home config takes precedence.

## Architecture note

Core is the source of truth. UI is a thin shell using `core/services/ui_facade` exclusively.
All data persists in SQLite (path configured in the config file → `db_path`).
Offline behavior is guaranteed — live prices are optional and never required for core operations.

## First run

On first launch, `~/.ledger_app/ledger.ini` is created with default settings (SQLite at `~/.ledger_app/ledger.db`).
Import a `unified_format_raw` file via the Import button, or add trades manually via Add Trade.
