"""App configuration: načítá z ledger.ini, fallback na defaults."""
import configparser
import os

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


def load_config(config_path: str = "ledger.ini") -> dict:
    """Načte konfiguraci z INI souboru. Chybějící hodnoty = defaults."""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(config_path):
        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")
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


def set_db_path(new_db_path: str, config_path: str = "ledger.ini") -> None:
    """Persist *new_db_path* as [ledger] db_path in the INI file."""
    parser = configparser.ConfigParser()
    if os.path.exists(config_path):
        parser.read(config_path, encoding="utf-8")
    if "ledger" not in parser:
        parser["ledger"] = {}
    parser["ledger"]["db_path"] = new_db_path
    with open(config_path, "w", encoding="utf-8") as f:
        parser.write(f)
