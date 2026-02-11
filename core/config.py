"""App configuration: načítá z ledger.ini, fallback na defaults."""
import configparser
import os

DEFAULT_CONFIG = {
    "db_path": "ledger.db",
    "default_venue": "",
    "export_dir": "exports",
}


def load_config(config_path: str = "ledger.ini") -> dict:
    """Načte konfiguraci z INI souboru. Chybějící hodnoty = defaults."""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(config_path):
        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")
        if "ledger" in parser:
            for key in DEFAULT_CONFIG:
                if key in parser["ledger"]:
                    val = parser["ledger"][key].strip()
                    if val:
                        config[key] = val
    return config
