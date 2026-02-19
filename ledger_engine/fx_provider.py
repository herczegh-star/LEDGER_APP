"""FX rate provider: EUR → CZK historické kurzy."""
import configparser
import logging
import os
from abc import ABC, abstractmethod
from decimal import Decimal

logger = logging.getLogger(__name__)
_DEFAULT_EUR_TO_CZK = Decimal("25")


# Assety, které jsou protistrana obchodu, ne investiční pozice.
FIAT_ASSETS = frozenset({"EUR", "CZK"})


class FxProvider(ABC):
    """Abstraktní interface pro historické FX kurzy."""

    @abstractmethod
    def get_eur_to_czk(self, date: str) -> Decimal:
        """Vrátí denní kurz EUR/CZK pro dané datum (YYYY-MM-DD).

        Raises:
            ValueError: Pokud kurz pro dané datum není k dispozici.
        """
        ...


class DictFxProvider(FxProvider):
    """FX provider nad statickým slovníkem – pro testy a offline scénáře.

    Příklad:
        provider = DictFxProvider({"2026-01-01": Decimal("25.00")})
    """

    def __init__(self, rates: dict) -> None:
        self._rates = {k: Decimal(str(v)) for k, v in rates.items()}

    def get_eur_to_czk(self, date: str) -> Decimal:
        if date not in self._rates:
            raise ValueError(f"FX kurz EUR/CZK pro {date!r} nenalezen.")
        return self._rates[date]


class ConfigFxProvider(FxProvider):
    """FX provider čtoucí fixní kurz EUR/CZK z ledger.ini.

    Sekce [fx], klíč eur_to_czk.
    Pokud klíč chybí, použije default 25 CZK/EUR a zaloguje varování.
    Vrací stejný kurz pro všechna data (flat rate).
    """

    def __init__(self, config_path: str = "ledger.ini") -> None:
        self._rate = _DEFAULT_EUR_TO_CZK
        parser = configparser.ConfigParser()
        if os.path.exists(config_path):
            parser.read(config_path, encoding="utf-8")
            if "fx" in parser and "eur_to_czk" in parser["fx"]:
                val = parser["fx"]["eur_to_czk"].strip()
                if val:
                    self._rate = Decimal(val)
                    return
        logger.warning(
            "ConfigFxProvider: [fx] eur_to_czk nenalezen v %r, použit default %s",
            config_path,
            _DEFAULT_EUR_TO_CZK,
        )

    def get_eur_to_czk(self, date: str) -> Decimal:
        return self._rate
