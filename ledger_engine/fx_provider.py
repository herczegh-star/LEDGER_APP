"""FX rate provider: EUR → CZK historické kurzy."""
from abc import ABC, abstractmethod
from decimal import Decimal


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
