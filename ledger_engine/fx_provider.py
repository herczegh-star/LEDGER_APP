"""FX rate provider: EUR → CZK historické kurzy."""
import configparser
import logging
import os
import urllib.request
from abc import ABC, abstractmethod
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Optional, Tuple

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


class CnbFxProvider:
    """Stahuje denní kurz EUR/CZK z ČNB s fallbackem na předchozí pracovní dny.

    Cache: slovník {date: Decimal}, žije po dobu běhu procesu.
    Pokud kurz pro požadovaný den není dostupný (víkend, svátek, ČNB ještě
    nevyhlásilo), zkusí postupně až 6 předchozích dní.
    Raises RuntimeError pokud žádný den nemá kurz.
    """

    _CNB_URL = (
        "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/"
        "kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/"
        "denni_kurz.txt?date={}"
    )

    def __init__(self) -> None:
        self._cache: Dict[date, Decimal] = {}

    def get_eur_czk(self, d: date) -> Tuple[Decimal, date]:
        """Vrátí (kurz, datum kurzu) pro EUR/CZK.

        Zkouší d, d-1, ..., d-6.
        Raises RuntimeError pokud žádný den nemá kurz.
        """
        for delta in range(7):
            lookup = d - timedelta(days=delta)
            if lookup in self._cache:
                return self._cache[lookup], lookup
            rate = self._fetch(lookup)
            if rate is not None:
                self._cache[lookup] = rate
                return rate, lookup
        raise RuntimeError(
            f"Kurz EUR/CZK nedostupný pro {d} ani předchozích 6 dní (ČNB API)."
        )

    def _fetch(self, d: date) -> Optional[Decimal]:
        """Stáhne kurz z ČNB pro konkrétní datum. Vrátí None při jakékoli chybě."""
        url = self._CNB_URL.format(d.strftime("%d.%m.%Y"))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LedgerApp/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                text = resp.read().decode("windows-1250")
        except Exception:
            return None
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) >= 5 and parts[3].strip().upper() == "EUR":
                try:
                    return Decimal(parts[4].strip().replace(",", "."))
                except Exception:
                    return None
        return None
