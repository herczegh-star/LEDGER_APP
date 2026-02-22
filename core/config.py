"""App configuration — load/save ledger.ini from a stable, OS-independent location.

Config resolution order (when no explicit path is given):
    1. ~/.ledger_app/ledger.ini   (user-home — preferred, always consistent)
    2. <cwd>/ledger.ini           (project root — backward-compat for existing installs)
    3. Create ~/.ledger_app/ledger.ini with defaults (first run)

Public API:
    get_default_config_path() -> Path   # ~/.ledger_app/ledger.ini
    get_resolved_config_path(config_path=None) -> Path
    load_config(config_path=None) -> dict
    set_db_path(new_db_path, config_path=None) -> None
"""
from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Optional, Union

DEFAULT_CONFIG = {
    "db_path": "ledger.db",
    "default_venue": "",
    "export_dir": "exports",
    # [prices] defaults
    "prices_provider":    "coingecko",
    "prices_fallback":    "coinbase",
    "prices_ttl_seconds": "60",
    "prices_fiat":        "CZK",
}

_LEDGER_KEYS = {"db_path", "default_venue", "export_dir"}
_PRICES_MAP = {
    "provider":    "prices_provider",
    "fallback":    "prices_fallback",
    "ttl_seconds": "prices_ttl_seconds",
    "fiat":        "prices_fiat",
}


# ── Path helpers ───────────────────────────────────────────────────────────────

def get_default_config_path() -> Path:
    """Return ~/.ledger_app/ledger.ini, creating the parent directory if needed."""
    home_cfg = Path.home() / ".ledger_app" / "ledger.ini"
    home_cfg.parent.mkdir(parents=True, exist_ok=True)
    return home_cfg


def get_resolved_config_path(
    config_path: Optional[Union[str, Path]] = None,
) -> Path:
    """Return the config Path to use, applying the home→project→create resolution.

    Args:
        config_path: Explicit path override.  When None, auto-resolution applies.

    Returns:
        Resolved Path object (file may or may not exist — callers handle that).
    """
    if config_path is not None:
        return Path(config_path)

    home_cfg = get_default_config_path()
    if home_cfg.exists():
        return home_cfg

    project_cfg = Path.cwd() / "ledger.ini"
    if project_cfg.exists():
        return project_cfg

    # Neither exists → first run: create home config with defaults.
    _write_defaults(home_cfg)
    return home_cfg


def _write_defaults(path: Path) -> None:
    """Write a minimal default ledger.ini to *path* (first-run helper)."""
    parser = configparser.ConfigParser()
    # Store DB next to the config file so the path is always absolute + stable.
    parser["ledger"] = {
        "db_path": str(path.parent / "ledger.db"),
    }
    parser["prices"] = {
        "fiat": DEFAULT_CONFIG["prices_fiat"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)


# ── Public config functions ────────────────────────────────────────────────────

def load_config(
    config_path: Optional[Union[str, Path]] = None,
) -> dict:
    """Load config from the resolved INI path and merge with defaults.

    Args:
        config_path: Explicit path override.  When None, auto-resolution applies.

    Returns:
        dict with all keys from DEFAULT_CONFIG, overridden by the INI values.
    """
    resolved = get_resolved_config_path(config_path)
    config = dict(DEFAULT_CONFIG)

    if resolved.exists():
        parser = configparser.ConfigParser()
        parser.read(resolved, encoding="utf-8")
        if "ledger" in parser:
            for key in _LEDGER_KEYS:
                if key in parser["ledger"]:
                    val = parser["ledger"][key].strip()
                    if val:
                        config[key] = val
        if "prices" in parser:
            for ini_key, cfg_key in _PRICES_MAP.items():
                if ini_key in parser["prices"]:
                    val = parser["prices"][ini_key].strip()
                    if val:
                        config[cfg_key] = val

    return config


def set_db_path(
    new_db_path: str,
    config_path: Optional[Union[str, Path]] = None,
) -> None:
    """Persist *new_db_path* as [ledger] db_path in the resolved INI file.

    Writes to the same path that load_config() would read from.
    """
    resolved = get_resolved_config_path(config_path)
    parser = configparser.ConfigParser()
    if resolved.exists():
        parser.read(resolved, encoding="utf-8")
    if "ledger" not in parser:
        parser["ledger"] = {}
    parser["ledger"]["db_path"] = new_db_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved, "w", encoding="utf-8") as f:
        parser.write(f)
